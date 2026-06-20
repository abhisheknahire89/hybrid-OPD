import os
import json
import asyncio
import re
import time
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

SYSTEM_PROMPT = """You are a fast medical-data EXTRACTOR. Do not analyze, elaborate, or add commentary. Extract only what is present and output JSON. Be terse.

RULES:
1. Normalize drug names to standard English (e.g., 'डोलो' -> 'Dolo', 'मेटामॉर्फिन' -> 'Metformin'). Never invent drugs. If garbled, set confidence="low".
2. Do NOT append or guess standard strengths or generics in the drug name or dosage fields unless the doctor explicitly spoke them.
3. Only use transcript info. Missing data = "Not specified" / "None identified" / null.
4. Doses/Freq/Route/Duration: If explicitly spoken by the doctor, set "explicitly_stated": true. If inferred/guessed, set false. If not spoken or mentioned, set value to null.
5. Output strict JSON matching the schema. No markdown formatting. Output compact JSON without formatting spaces or newlines.

FIELD STYLES:
- chief_complaint, clinical_findings, advice: Short phrases only (max 1-2 lines, no paragraphs).
- history: Concise bullet-style facts (e.g., "- Symptom A for X days\n- History of Y").
- unstructured_notes: Capture any clinical context or extra details from the transcript that didn't fit into the other fields. Do not lose any details.

SCHEMA:
{
  "chief_complaint": "string",
  "history": "string",
  "clinical_findings": "string",
  "diagnosis": "string or 'None identified'",
  "medications": [
    {
      "name": "string",
      "dosage": { "value": "string or null", "explicitly_stated": boolean },
      "frequency": { "value": "string or null", "explicitly_stated": boolean },
      "route": { "value": "string or null", "explicitly_stated": boolean },
      "duration": { "value": "string or null", "explicitly_stated": boolean },
      "confidence": "high" | "low"
    }
  ],
  "advice": "string",
  "unstructured_notes": "string"
}"""

EMPTY_FALLBACK = {
    "chief_complaint": "AI summary unavailable — please fill manually.",
    "history": "AI summary unavailable — please fill manually.",
    "clinical_findings": "AI summary unavailable — please fill manually.",
    "diagnosis": "None identified",
    "medications": [],
    "advice": "AI summary unavailable — please fill manually.",
    "unstructured_notes": ""
}

MODELS_TO_TRY = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]

async def _call_gemini_with_retry(transcript: str) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY environment variable is missing!")
        raise ValueError("GEMINI_API_KEY environment variable is missing.")
    
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1)
        )
    )
    last_exception = None
    
    for model_name in MODELS_TO_TRY:
        print(f"[INFO] Attempting Gemini API call with model: {model_name}...")
        try:
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=transcript,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    max_output_tokens=1024,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=0
                    )
                )
            )
            text = response.text.strip()
            print(f"[INFO] Raw response from Gemini ({model_name}):")
            print(text)
            if response.usage_metadata:
                p_tok = response.usage_metadata.prompt_token_count
                c_tok = response.usage_metadata.candidates_token_count
                print(f"[GEMINI USAGE] Model: {model_name} | Input Tokens: {p_tok} | Output Tokens: {c_tok} | Total: {p_tok + c_tok}", flush=True)
            
            return _parse_json_response(text)
        except Exception as e:
            last_exception = e
            err_msg = str(e)
            print(f"[ERROR] Model {model_name} failed: {err_msg}")
            # Immediately try next model in next iteration without sleeping or retrying the failed endpoint
            print(f"[INFO] Fail-over to next model.")
    
    raise last_exception or RuntimeError("All Gemini models failed to generate content.")

def _parse_json_response(text: str) -> dict:
    try:
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Find the matching closing brace for the first opening brace to ignore trailing commentary
        cleaned_text = text
        start = text.find('{')
        if start != -1:
            brace_count = 0
            in_string = False
            escape = False
            for i in range(start, len(text)):
                char = text[i]
                if escape:
                    escape = False
                    continue
                if char == '\\':
                    escape = True
                    continue
                if char == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            cleaned_text = text[start:i+1]
                            break

        parsed_json = json.loads(cleaned_text)
        print("[INFO] Successfully parsed Gemini response as JSON.")
        return parsed_json
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse JSON. Raw text was: {text}")
        print(f"[ERROR] Parse error: {e}")
        raise ValueError("Failed to parse JSON response.") from e



# --- Drug Name Hotlist Pre-Scan ---
HOTLIST_PATH = os.path.join(os.path.dirname(__file__), "data", "doctor_drug_hotlist.json")
_drug_hotlist = {}

def _load_hotlist():
    """Load the drug alias hotlist from disk."""
    global _drug_hotlist
    try:
        with open(HOTLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            _drug_hotlist = data.get("aliases", {})
            print(f"[INFO] Loaded drug hotlist with {len(_drug_hotlist)} aliases.")
    except FileNotFoundError:
        print("[WARN] Drug hotlist not found. Skipping pre-scan.")
        _drug_hotlist = {}
    except Exception as e:
        print(f"[WARN] Failed to load drug hotlist: {e}")
        _drug_hotlist = {}

# Load on module import
_load_hotlist()

def _prescan_drug_names(transcript: str) -> str:
    """
    Scan the transcript for known drug aliases (including Devanagari transliterations)
    and replace them with canonical English brand names before sending to Gemini.
    Uses longest-match-first to avoid partial replacements.
    """
    if not _drug_hotlist:
        return transcript
    
    # Sort aliases by length (longest first) to avoid partial matches
    sorted_aliases = sorted(_drug_hotlist.keys(), key=len, reverse=True)
    
    normalized = transcript
    replacements_made = []
    
    for alias in sorted_aliases:
        # Case-insensitive search for Latin script, exact match for Devanagari
        if any(ord(c) > 127 for c in alias):
            # Non-ASCII (Devanagari etc.) — exact match
            if alias in normalized:
                canonical = _drug_hotlist[alias]
                normalized = normalized.replace(alias, canonical)
                replacements_made.append(f"'{alias}' -> '{canonical}'")
        else:
            # Latin script — case-insensitive word boundary match
            pattern = re.compile(re.escape(alias), re.IGNORECASE)
            if pattern.search(normalized):
                canonical = _drug_hotlist[alias]
                normalized = pattern.sub(canonical, normalized)
                replacements_made.append(f"'{alias}' -> '{canonical}'")
    
    if replacements_made:
        print(f"[HOTLIST] Pre-scan normalized {len(replacements_made)} drug names: {', '.join(replacements_made[:5])}")
    
    return normalized

def add_to_hotlist(original: str, corrected: str):
    """Auto-learn: when a doctor corrects a drug name, add the mapping to the hotlist."""
    global _drug_hotlist
    original_lower = original.strip().lower()
    if original_lower and corrected.strip() and original_lower != corrected.strip().lower():
        _drug_hotlist[original_lower] = corrected.strip()
        # Persist to disk
        try:
            with open(HOTLIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["aliases"][original_lower] = corrected.strip()
            with open(HOTLIST_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            print(f"[HOTLIST] Auto-learned: '{original}' -> '{corrected}'")
        except Exception as e:
            print(f"[WARN] Failed to persist hotlist update: {e}")


async def generate_soap_note(transcript: str) -> dict:
    print(f"\n[INFO] === Starting generate_soap_note ===")
    print(f"[INFO] Transcript length: {len(transcript)} characters")
    
    if not transcript.strip() or transcript.strip() == 'Select language and click Start Recording...':
        print("[INFO] Empty transcript provided. Returning fallback directly.")
        return EMPTY_FALLBACK

    # Pre-scan: normalize drug names using the hotlist BEFORE sending to Gemini
    normalized_transcript = _prescan_drug_names(transcript)
    if normalized_transcript != transcript:
        print(f"[INFO] Transcript was normalized. New length: {len(normalized_transcript)} characters")

    start_time = time.time()
    try:
        # Wrap the retried function with an overall timeout of 25 seconds
        result = await asyncio.wait_for(_call_gemini_with_retry(normalized_transcript), timeout=25.0)
        duration = time.time() - start_time
        print(f"[INFO] === generate_soap_note COMPLETED SUCCESSFULLY in {duration:.2f}s ===\n")
        return result
    except asyncio.TimeoutError:
        duration = time.time() - start_time
        print(f"[ERROR] Gemini call timed out after {duration:.2f}s (Safety margin is 25s).")
        return EMPTY_FALLBACK
    except Exception as e:
        duration = time.time() - start_time
        print(f"[ERROR] Gemini call ultimately failed after {duration:.2f}s: {e}")
        return EMPTY_FALLBACK


async def generate_soap_note_stream(transcript: str):
    print(f"\n[INFO] === Starting generate_soap_note_stream ===")
    print(f"[INFO] Transcript length: {len(transcript)} characters")
    
    if not transcript.strip() or transcript.strip() == 'Select language and click Start Recording...':
        print("[INFO] Empty transcript provided. Returning fallback directly.")
        yield json.dumps(EMPTY_FALLBACK)
        return

    # Pre-scan: normalize drug names using the hotlist BEFORE sending to Gemini
    normalized_transcript = _prescan_drug_names(transcript)
    if normalized_transcript != transcript:
        print(f"[INFO] Transcript was normalized. New length: {len(normalized_transcript)} characters")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY environment variable is missing!")
        yield json.dumps(EMPTY_FALLBACK)
        return

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1)
        )
    )

    model_name = MODELS_TO_TRY[0]
    print(f"[INFO] Attempting Gemini API stream call with model: {model_name}...")
    try:
        response_stream = await client.aio.models.generate_content_stream(
            model=model_name,
            contents=normalized_transcript,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                max_output_tokens=1024,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=0
                )
            )
        )
        async for chunk in response_stream:
            if chunk.text:
                yield chunk.text
    except Exception as e:
        print(f"[ERROR] Streaming failed: {e}. Falling back to generate_soap_note.")
        # Fallback to non-stream note generation
        res = await generate_soap_note(transcript)
        yield json.dumps(res)
