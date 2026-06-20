import asyncio
import os
import datetime
import threading
import time
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import torch
import numpy as np

from transcriber import IndicConformerTranscriber
from llm import generate_soap_note, add_to_hotlist, verify_grounding, scan_for_missed_drugs, get_shortlist_suggestions, update_shortlist
from drug_db import CSVDrugDatabase

app = FastAPI()

# Mount static files for the frontend
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Preload transcriber
transcriber_engine = IndicConformerTranscriber()


# Preload VAD
print("Loading Silero VAD model...")
vad_model, vad_utils = torch.hub.load(
    repo_or_dir='snakers4/silero-vad',
    model='silero_vad',
    force_reload=False,
    trust_repo=True
)
print("VAD model loaded.")

print("Loading Drug Database...")
drug_db = CSVDrugDatabase("data/indian_medicine_data.csv")
print("Drug Database loaded.")

os.makedirs("transcripts", exist_ok=True)
os.makedirs("sessions", exist_ok=True)

@app.get("/")
async def get():
    with open("static/index.html", "r") as f:
        return HTMLResponse(f.read())

import re

def preserve_dosage_unit_py(dosage_val: str, db_strength: str) -> str:
    if not dosage_val:
        return ""
    val_str = str(dosage_val).strip()
    if val_str.lower() in ["not specified", "none identified", "none", ""]:
        return val_str
    
    # Check if it's a pure number (e.g., "20", "500", "0.5")
    is_pure_number = re.match(r'^\d+(?:\.\d+)?$', val_str)
    if is_pure_number:
        if db_strength:
            unit_match = re.search(r'(mg|ml|mcg|g|iu|%)', db_strength, re.IGNORECASE)
            if unit_match:
                return val_str + unit_match.group(1).lower()
        return val_str + "mg"
    return val_str

def is_valid_dosage_strength(val: str) -> bool:
    if not val:
        return False
    val_lower = str(val).lower().strip()
    if val_lower in ["not specified", "none identified", "none", "", "null", "undefined"]:
        return False
    quantity_words = {
        "one", "two", "three", "four", "five", "six", "single", "a", "an",
        "tablet", "tablets", "capsule", "capsules", "pill", "pills", "half",
        "each", "once", "twice", "1", "2", "3", "4", "5"
    }
    if val_lower in quantity_words:
        return False
    return True

class TranscriptRequest(BaseModel):
    transcript: str

def _process_medications(note_json: dict, original_transcript: str = "") -> dict:
    """Shared drug-matching, dosage-resolution, grounding-check, and shortlist-suggestion logic.
    Modifies note_json in-place and returns it."""
    if "medications" not in note_json or not isinstance(note_json["medications"], list):
        return note_json

    # Run grounding verification if original transcript is provided
    if original_transcript:
        note_json["medications"] = verify_grounding(note_json["medications"], original_transcript)

    extracted_names = []

    for med in note_json["medications"]:
        med_name = med.get("name", "")
        extracted_names.append(med_name)

        # --- DB Matching ---
        matches = drug_db.find_matches(med_name)
        med["matches"] = matches

        is_unverified = False
        if (not matches or
                matches[0]["score"] < 95 or
                matches[0].get("match_type") == "Phonetic" or
                matches[0]["brand"] == "No reliable match — enter manually"):
            is_unverified = True

        med["is_unverified"] = is_unverified

        # --- Shortlist suggestions (for unverified / garbled drugs) ---
        shortlist_hints = get_shortlist_suggestions(med_name, limit=2)
        # Merge with DB suggestions >= 80 (excluding the fallback)
        db_hints = [
            {"brand": m["brand"], "score": m["score"], "source": "db"}
            for m in matches
            if m["score"] >= 80 and m["brand"] != "No reliable match — enter manually"
        ]
        # Shortlist first, then DB hints (dedup)
        all_hints = shortlist_hints[:]
        seen_brands = {h["brand"].lower() for h in all_hints}
        for h in db_hints:
            if h["brand"].lower() not in seen_brands:
                all_hints.append(h)
                seen_brands.add(h["brand"].lower())
        med["suggestions"] = all_hints[:2]  # max 2 suggestions

        # --- Log unmatched ---
        if is_unverified and med_name:
            try:
                os.makedirs("data", exist_ok=True)
                log_entry = {"drug_name": med_name, "timestamp": datetime.datetime.now().isoformat()}
                with open("data/unmatched_drugs_log.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(log_entry) + "\n")
                print(f"[UNMATCHED DRUG LOG] {med_name}", flush=True)
            except Exception as ex:
                print(f"[ERROR] Failed to log unmatched drug {med_name}: {ex}", flush=True)

        top_match = matches[0] if (matches and matches[0]["score"] > 0) else None

        # --- Dosage Resolution ---
        gemini_dosage = med.get("dosage", {})
        raw_doc_dosage_val = None
        if isinstance(gemini_dosage, dict):
            raw_doc_dosage_val = gemini_dosage.get("value")

        displayed_dosage = "Not specified"
        db_supplied = "None"
        dosage_explicit = False

        if raw_doc_dosage_val and is_valid_dosage_strength(raw_doc_dosage_val):
            db_strength = top_match["strength"] if top_match else ""
            displayed_dosage = preserve_dosage_unit_py(str(raw_doc_dosage_val), db_strength)
            dosage_explicit = True
            db_supplied = "None (doctor spoke)"
        elif top_match and top_match.get("strength"):
            displayed_dosage = top_match["strength"]
            dosage_explicit = False
            db_supplied = top_match["strength"]
        else:
            displayed_dosage = "Not specified"
            dosage_explicit = False
            db_supplied = "None (empty)"

        med["dosage"] = {"value": displayed_dosage, "explicitly_stated": dosage_explicit}

        print(f"[DOSAGE AUDIT] Med: {med_name} | Raw: {raw_doc_dosage_val} | DB: {db_supplied} | Displayed: {displayed_dosage}", flush=True)

        if matches:
            top_item = matches[0]
            print(f"[MATCH] {med_name} -> {top_item['brand']} | Score: {top_item['score']} | Hallucination: {med.get('hallucination_risk', False)}", flush=True)
        else:
            print(f"[MATCH] {med_name} -> None | Score: 0", flush=True)

    # Layer 1: scan for possibly missed drugs
    if original_transcript:
        note_json["possible_missed_medications"] = scan_for_missed_drugs(
            original_transcript, extracted_names
        )
        print(f"[CONSULT LOG] Extracted: {len(extracted_names)} drugs | Possible missed: {note_json.get('possible_missed_medications', [])}", flush=True)

    return note_json


@app.post("/api/generate_note")
async def generate_note(req: TranscriptRequest):
    t0 = time.time()
    note_json = await generate_soap_note(req.transcript)
    # generate_soap_note already runs verify_grounding + scan_for_missed_drugs internally
    # Here we run the DB matching + dosage resolution
    _process_medications(note_json, original_transcript="")  # grounding already done inside llm.py
    t1 = time.time()
    print(f"[PERF] End-to-End latency: {t1 - t0:.2f}s", flush=True)
    return note_json


from fastapi.responses import StreamingResponse
import llm

@app.post("/api/generate_note_stream")
async def generate_note_stream(req: TranscriptRequest):
    async def stream_generator():
        accumulated = []
        normalized_transcript_captured = ""
        try:
            async for text_chunk in llm.generate_soap_note_stream(req.transcript):
                # Intercept the normalized-transcript sentinel emitted at the end
                if text_chunk.startswith("\n__NORMALIZED_TRANSCRIPT__:"):
                    try:
                        normalized_transcript_captured = json.loads(
                            text_chunk.split("\n__NORMALIZED_TRANSCRIPT__:", 1)[1]
                        )
                    except Exception:
                        normalized_transcript_captured = req.transcript
                    continue  # Don't forward this internal sentinel to the client
                accumulated.append(text_chunk)
                yield json.dumps({"type": "chunk", "text": text_chunk}) + "\n"
        except Exception as e:
            print(f"[ERROR] stream_generator: {e}", flush=True)

        full_text = "".join(accumulated)
        try:
            note_json = llm._parse_json_response(full_text)
        except Exception as e:
            print(f"[ERROR] Failed parsing streamed JSON: {e}", flush=True)
            note_json = llm.EMPTY_FALLBACK

        # Safety + drug-matching pass (identical logic to generate_note)
        _process_medications(note_json, original_transcript=normalized_transcript_captured or req.transcript)

        yield json.dumps({"type": "final", "data": note_json}) + "\n"

    return StreamingResponse(stream_generator(), media_type="application/x-ndjson")

class CorrectionRequest(BaseModel):
    original_value: str
    corrected_value: str
    field_type: str
    timestamp: str

@app.post("/api/log_correction")
async def log_correction(req: CorrectionRequest):
    record = req.dict()
    with open("data/corrections_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

    # Auto-learn corrections for medications (hotlist alias)
    if req.field_type == "medication":
        add_to_hotlist(req.original_value, req.corrected_value)

    return {"status": "ok"}


class MedicationConfirmRequest(BaseModel):
    drug_name: str

@app.post("/api/confirm_medication")
async def confirm_medication(req: MedicationConfirmRequest):
    """Called when a doctor confirms a medication. Updates the per-doctor shortlist."""
    if req.drug_name and req.drug_name.strip():
        update_shortlist(req.drug_name.strip())
    return {"status": "ok"}


@app.get("/api/shortlist")
async def get_shortlist():
    """Returns the doctor's frequently-prescribed drugs, sorted by frequency."""
    from llm import get_shortlist_drugs
    return {"drugs": get_shortlist_drugs()}

class SessionRequest(BaseModel):
    patient_info: dict
    soap_note: dict
    medications: list
    timestamp: str
    consent_timestamp: str

@app.post("/api/save_session")
async def save_session(req: SessionRequest):
    session_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    filepath = f"sessions/session-{session_id}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(req.dict(), f, indent=2)
    return {"status": "ok", "file": filepath}

@app.get("/api/metrics")
async def get_metrics():
    import glob
    sessions_count = len(glob.glob("sessions/*.json"))
    
    corrections_count = 0
    field_counts = {}
    
    if os.path.exists("data/corrections_log.jsonl"):
        with open("data/corrections_log.jsonl", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    corrections_count += 1
                    try:
                        record = json.loads(line)
                        field = record.get("field_type", "unknown")
                        field_counts[field] = field_counts.get(field, 0) + 1
                    except json.JSONDecodeError:
                        pass
                        
    most_corrected = "None"
    if field_counts:
        most_corrected = max(field_counts, key=field_counts.get)
        
    avg_edits = (corrections_count / sessions_count) if sessions_count > 0 else 0
    
    return {
        "total_consults": sessions_count,
        "average_edits_per_prescription": round(avg_edits, 2),
        "most_corrected_field": most_corrected
    }

@app.websocket("/ws/audio")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # Receive language code
    data = await websocket.receive_text()
    language_code = data
    
    transcriber_engine.start_session(language_code)
    
    # Setup transcript file
    session_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    transcript_file = f"transcripts/session-{session_id}.txt"
    
    # Thread safety for file writes
    file_lock = threading.Lock()
    
    await websocket.send_json({"type": "info", "message": f"Saving to {transcript_file}"})
    
    # A buffer to hold transcripts to send back
    transcript_queue = []
    
    def sync_on_partial(text):
        if text.strip():
            with file_lock:
                with open(transcript_file, "a", encoding="utf-8") as f:
                    f.write(text + " ")
                    f.flush()
                    os.fsync(f.fileno())
            transcript_queue.append(text)
            
    transcriber_engine.on_partial(sync_on_partial)
    
    audio_queue = asyncio.Queue()
    expected_seq = 0
    
    async def consumer_task():
        nonlocal expected_seq
        while True:
            seq, chunk = await audio_queue.get()
            if chunk is None:  # Sentinel to stop
                audio_queue.task_done()
                break
                
            if seq != expected_seq:
                print(f"[ERROR] Ordering violated! Expected {expected_seq}, got {seq}", flush=True)
                
            qsize = audio_queue.qsize()
            
            # Calculate length of audio chunk
            # 16-bit PCM = 2 bytes per sample, 16000 Hz
            samples = len(chunk) // 2
            duration = samples / 16000.0
            
            t0 = time.time()
            # Run inference in threadpool so event loop is NEVER blocked
            await asyncio.to_thread(transcriber_engine.feed_audio, chunk)
            t1 = time.time()
            
            print(f"[DIAGNOSTIC] seq: {seq} | qsize: {qsize} | audio_duration: {duration:.3f}s | inference_time: {t1 - t0:.3f}s | ratio (inf/dur): {(t1 - t0) / duration:.2f}x", flush=True)
            
            expected_seq += 1
            audio_queue.task_done()

    consumer = asyncio.create_task(consumer_task())
    seq_num = 0
    
    try:
        while True:
            # We receive 16-bit PCM binary from the client
            audio_bytes = await websocket.receive_bytes()
            
            # Simple VAD logic: convert bytes to float32 tensor
            audio_array = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            
            chunk_size = 512
            has_speech = False
            for i in range(0, len(audio_array), chunk_size):
                chunk = audio_array[i:i+chunk_size]
                if len(chunk) == chunk_size:
                    tensor = torch.from_numpy(chunk)
                    speech_prob = vad_model(tensor, 16000).item()
                    if speech_prob > 0.5:
                        has_speech = True
                        break
            
            # If there's speech in this payload, enqueue it
            if has_speech:
                qsize = audio_queue.qsize()
                if qsize > 5:
                    print(f"[WARNING] Transcription lagging! Queue size: {qsize}", flush=True)
                
                await audio_queue.put((seq_num, audio_bytes))
                seq_num += 1
                
            # Send generated text back
            while transcript_queue:
                text = transcript_queue.pop(0)
                await websocket.send_json({"type": "transcript", "text": text})
                
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await audio_queue.put((-1, None)) # Stop consumer
        await consumer # wait for processing to finish
        transcriber_engine.stop_session()
        while transcript_queue:
            text = transcript_queue.pop(0)
            try:
                await websocket.send_json({"type": "transcript", "text": text})
            except:
                pass
