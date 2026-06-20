import os
import json
import asyncio
import re
import time
import fcntl
import jellyfish
from rapidfuzz import fuzz, process as fuzz_process, utils as fuzz_utils
from google import genai
from google.genai import types

# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a precise medical-data EXTRACTOR for Indian clinical transcripts. Extract ONLY what is present. Do NOT analyze, elaborate, or invent. Output strict JSON only.

## CRITICAL ANTI-FABRICATION & SAFETY RULES (Read first)
1. You MUST NEVER invent, infer, assume, or guess any medication.
- Only include a drug if the DOCTOR EXPLICITLY said it in the transcript.
- If you are even slightly unsure whether a drug was actually prescribed (vs. mentioned in history, or not at all), DO NOT include it.
- It is ALWAYS safer to omit a drug than to invent one. A fabricated drug on a prescription can harm or kill a patient.
- For each medication, record the EXACT short phrase from the transcript where the doctor prescribed it in the "transcript_phrase" field. If you cannot find a real phrase, you must not include the drug.
2. DOCTOR REJECTIONS: If the doctor mentions a drug but then explicitly REJECTS, cancels, or decides against it (e.g., "nahi nahi", "nako", "won't work", "instead give", "never mind", "do not give", "abhi nako", "nahi dena hai"), you MUST NOT extract or prescribe it.
3. VITALS SANITY: Never invent, guess, or estimate vital signs (temperature, blood pressure, pulse, SpO2). If a vital sign is not clearly and explicitly stated by the doctor or patient in the transcript, leave it blank/omit. Flag/blank physiologically impossible values (e.g. temp >110F, BP without two numbers like "120" instead of "120/80").

## THIRD-SPEAKER ATTRIBUTION (CRITICAL)
- Transcripts may contain inputs from multiple speakers (e.g., daughter, relative, companion).
- You MUST distinguish between symptoms/requests of the PATIENT and those of the COMPANION.
- Only extract clinical findings, history, diagnosis, and prescriptions for the PATIENT.
- Do NOT prescribe or list medications for the companion. For example, if a companion mentions their own symptom (e.g. "mala pan acidity hote" or "I also have acidity") or requests a drug for themselves, you MUST NOT list that symptom or drug in the patient's record or prescription.

## LANGUAGE HANDLING & CODE-SWITCHING (Critical)
The transcript may be in Hindi, Marathi, Gujarati, Hinglish (mixed Hindi+English), or English.
- Normalize ALL drug names to standard English brand/generic names.
  Examples: 'डोलो' → 'Dolo 650', 'ऑगमेंटिन' → 'Augmentin 625', 'सेटिरिज़ीन' → 'Cetirizine', 'ज़ाइलोमेटाज़ोलिन' → 'Xylometazoline Nasal Spray'
- Normalize Hinglish numbers to digits: 'do sau' → 200, 'paanch sau' → 500, 'ek' → 1, 'do' → 2, 'teen' → 3, 'paanch' → 5
- Normalize Hinglish durations: 'saat din' → 7 days, 'paanch din' → 5 days, 'teen din' → 3 days, 'ek hafte' → 1 week

## FREQUENCY VOCABULARY (Standardized abbreviations mapping)
Use the following standard frequency codes based on the spoken words:
- OD = once daily (e.g., "ek baar", "sakali", "once a day", "daily", "daily ek")
- BD = twice daily (e.g., "do baar", "subah shaam", "twice daily", "twice a day", "bds", "jevnanantar" where twice daily)
- TDS = three times daily (e.g., "teen baar", "din me teen baar", "thrice a day", "thrice daily", "tid")
- QID = four times daily (e.g., "char baar", "four times a day")
- HS = at bedtime (e.g., "raat ko", "raatri", "sote samay", "night", "qhs")
- PRN = as needed / as required (e.g., "as needed", "when required")
- SOS = if needed (e.g., "jarurat padne par", "when dizzy", "only if needed", "sos")

## ROUTE DEFAULTS
Set these default routes based on the drug form if not explicitly stated by the doctor:
- Oral: for all tablets, capsules, or oral syrups (unless doctor says 'oral' or 'swallow', set explicitly_stated: false with value "Oral")
- Intranasal: for nasal sprays and nasal drops
- Topical: for creams, gels, and ointments
- Ophthalmic: for eye drops
- Otic: for ear drops
- IM / IV: for injections as spoken
Set explicitly_stated: true ONLY if the doctor explicitly said the route. Otherwise false.

## FIELD RULES
1. diagnosis: State the specific clinical diagnosis if the doctor indicates it. E.g., 'Acute Bacterial Sinusitis', not just 'sinusitis', or 'Diabetes Mellitus' or 'Diabetic Neuropathy'. If unconfirmed, write 'Suspected [X]'. Never output 'None identified' if a clear diagnosis is discussed.
2. clinical_findings: List all positive findings mentioned. Bullet-style, one per line.
3. advice: Bullet-style list of ALL advice/instructions mentioned. Up to 8 items. One item per line starting with '- '.
4. history: Concise bullet-style facts (e.g., '- Patient has diabetic history'). One fact per line.
5. unstructured_notes: Capture any clinical context.
6. Doses/Freq/Route/Duration: explicitly_stated: true ONLY if the doctor spoke that value. If inferred from standard practice or default route, set false. If not mentioned at all, set value to null.
7. Output compact JSON — no markdown, no code fences, no explanatory text.

SCHEMA:
{"chief_complaint":"string","history":"string","clinical_findings":"string","diagnosis":"string","medications":[{"name":"string","transcript_phrase":"string — exact phrase from transcript where doctor prescribed this drug","dosage":{"value":"string or null","explicitly_stated":boolean},"frequency":{"value":"string or null","explicitly_stated":boolean},"route":{"value":"string or null","explicitly_stated":boolean},"duration":{"value":"string or null","explicitly_stated":boolean},"confidence":"high"|"low"}],"advice":"string","unstructured_notes":"string"}"""

EMPTY_FALLBACK = {
    "chief_complaint": "AI summary unavailable — please fill manually.",
    "history": "AI summary unavailable — please fill manually.",
    "clinical_findings": "AI summary unavailable — please fill manually.",
    "diagnosis": "None identified",
    "medications": [],
    "advice": "AI summary unavailable — please fill manually.",
    "unstructured_notes": ""
}

MODELS_TO_TRY = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]

# ─────────────────────────────────────────────────────────────
# GEMINI API CALLS
# ─────────────────────────────────────────────────────────────
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
                    max_output_tokens=2048,
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=512
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
            print(f"[ERROR] Model {model_name} failed: {e}")
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

        # Find the matching closing brace for the first opening brace
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


# ─────────────────────────────────────────────────────────────
# ANTI-HALLUCINATION: GROUNDING VERIFIER
# ─────────────────────────────────────────────────────────────
# Drug-indicator words that signal a medication mention in transcript
_DRUG_INDICATORS = {
    "tablet", "tablets", "tab", "capsule", "capsules", "cap", "syrup", "drops",
    "spray", "cream", "gel", "ointment", "injection", "inj", "sachet", "powder",
    "mg", "ml", "mcg", "g", "gm", "iu", "%", "od", "bd", "tds", "qid", "hs",
    "prn", "sos", "bds", "tid", "twice", "thrice", "once", "daily", "night",
    "morning", "evening", "noon", "week", "days", "din", "baar"
}


def _name_in_transcript(drug_name: str, transcript: str) -> bool:
    """
    Check whether a drug name (or its significant tokens) actually appear in the transcript.
    Uses: exact substring, word token overlap, and phonetic similarity.
    """
    t_lower = transcript.lower()
    d_lower = drug_name.lower()

    # 1. Exact substring match (most reliable)
    if d_lower in t_lower:
        return True

    # 2. Word-token overlap: ≥1 significant token (>3 chars, not a dose/route word) found
    stop = {"and", "the", "for", "with", "mg", "ml", "tab", "cap", "spray", "oral", "dose"}
    tokens = [t for t in re.split(r'\W+', d_lower) if len(t) > 3 and t not in stop]
    if tokens and any(tok in t_lower for tok in tokens):
        return True

    # 3. Phonetic check: metaphone of primary token vs transcript words
    if tokens:
        primary = tokens[0]
        primary_meta = jellyfish.metaphone(primary)
        transcript_words = re.split(r'\W+', t_lower)
        for word in transcript_words:
            if len(word) > 3:
                try:
                    if jellyfish.metaphone(word) == primary_meta:
                        return True
                except Exception:
                    pass

    return False


def _is_drug_rejected(med: dict, transcript: str) -> bool:
    """
    Check if a drug is explicitly rejected by the doctor in the transcript.
    Looks for rejection keywords (e.g. nako, nahi nahi, won't work) in a window after the drug name or its primary word.
    """
    t_lower = transcript.lower()
    phrase = med.get("transcript_phrase", "").lower().strip()
    name = med.get("name", "").lower().strip()

    search_terms = []
    if phrase:
        search_terms.append(phrase)
    if name:
        search_terms.append(name)
        # also add first word of name
        first_word = name.split()[0]
        if len(first_word) > 3:
            search_terms.append(first_word)

    # De-duplicate search terms
    search_terms = list(dict.fromkeys(search_terms))

    rejections = [
        "nahi nahi", "nako", "won't work", "instead give", "no no", 
        "abhi nako", "avoid that", "nahi dena", "do not give", "don't give",
        "nahi de rahe", "cancel that", "drop that"
    ]

    for term in search_terms:
        idx = 0
        while True:
            pos = t_lower.find(term, idx)
            if pos == -1:
                break
            # check the window of 60 characters following the matched term
            window = t_lower[pos + len(term) : pos + len(term) + 60]
            for rej in rejections:
                if rej in window:
                    return True
            idx = pos + 1
    return False


def verify_grounding(medications: list, transcript: str) -> list:
    """
    For each extracted medication, verify it's grounded in the transcript text.
    Returns the medications list with `hallucination_risk` flag added.
    Medications explicitly rejected by the doctor are silently dropped.
    Medications with no grounding are flagged.
    """
    verified = []
    for med in medications:
        name = med.get("name", "")
        
        # Check if drug was rejected
        if _is_drug_rejected(med, transcript):
            print(f"[SAFETY] Dropping explicitly rejected drug: '{name}'", flush=True)
            continue
            
        phrase = med.get("transcript_phrase", "")

        # Check phrase grounding first (most direct evidence)
        phrase_grounded = bool(phrase and phrase.strip() and _name_in_transcript(phrase, transcript))
        name_grounded = _name_in_transcript(name, transcript)

        grounded = phrase_grounded or name_grounded

        if not grounded:
            print(f"[HALLUCINATION RISK] Drug '{name}' not found in transcript. Phrase: '{phrase}'. Flagging.", flush=True)
            # Log it
            try:
                os.makedirs("data", exist_ok=True)
                with open("data/hallucination_log.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "drug_name": name,
                        "transcript_phrase": phrase,
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
                    }) + "\n")
            except Exception as ex:
                print(f"[ERROR] Failed to write hallucination log: {ex}")

        med["hallucination_risk"] = not grounded
        verified.append(med)

    hallucinated = [m["name"] for m in verified if m.get("hallucination_risk")]
    if hallucinated:
        print(f"[SAFETY] {len(hallucinated)} potentially hallucinated drug(s): {hallucinated}", flush=True)
    else:
        print(f"[SAFETY] All {len(verified)} drug(s) grounded in transcript. ✓", flush=True)

    return verified


# ─────────────────────────────────────────────────────────────
# LAYER 1: MISSED DRUG SCANNER
# ─────────────────────────────────────────────────────────────
def scan_for_missed_drugs(transcript: str, extracted_names: list) -> list:
    """
    Scan transcript for drug-indicator words near un-extracted candidate terms.
    Returns list of candidate missed drug phrases.
    """
    missed = []
    t_lower = transcript.lower()

    # Find all words within a ±4-word window of a drug indicator
    tokens = re.split(r'\s+', t_lower)
    indicator_positions = [i for i, t in enumerate(tokens) if t.strip('.,;:()') in _DRUG_INDICATORS]

    candidate_words = set()
    for pos in indicator_positions:
        window = tokens[max(0, pos - 4):pos + 5]
        for w in window:
            w_clean = re.sub(r'[^a-z]', '', w)
            # A plausible drug-name word: ≥4 chars, not a common word, not a number
            if len(w_clean) >= 4 and not w_clean.isdigit() and w_clean not in _DRUG_INDICATORS:
                candidate_words.add(w_clean)

    # Check if any candidate is NOT covered by extracted drugs
    extracted_lower = [n.lower() for n in extracted_names]
    for cand in candidate_words:
        already_covered = any(
            cand in ex or ex in cand or
            (len(cand) > 4 and jellyfish.metaphone(cand) == jellyfish.metaphone(ex.split()[0]))
            for ex in extracted_lower
        )
        if not already_covered:
            missed.append(cand)

    if missed:
        print(f"[MISSED DRUG SCAN] Possible missed drugs in transcript: {missed}", flush=True)

    return missed[:5]  # cap at 5 to avoid noise


# ─────────────────────────────────────────────────────────────
# DRUG NAME HOTLIST PRE-SCAN
# ─────────────────────────────────────────────────────────────
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
        print(f"[WARN] Failed to load hotlist: {e}")
        _drug_hotlist = {}


# Load on module import
_load_hotlist()


def _prescan_drug_names(transcript: str) -> str:
    """
    Single-pass replacement (longest alias first) to prevent cascade double-substitution.
    Devanagari aliases replaced first (exact), then Latin aliases (combined regex, one pass).
    """
    if not _drug_hotlist:
        return transcript

    devanagari_aliases = {k: v for k, v in _drug_hotlist.items() if any(ord(c) > 127 for c in k)}
    latin_aliases = {k: v for k, v in _drug_hotlist.items() if not any(ord(c) > 127 for c in k)}

    replacements_made = []
    normalized = transcript

    # Pass 1: Devanagari — exact match is safe (no Latin overlap)
    for alias, canonical in sorted(devanagari_aliases.items(), key=lambda x: len(x[0]), reverse=True):
        if alias in normalized:
            normalized = normalized.replace(alias, canonical)
            replacements_made.append(f"'{alias}' -> '{canonical}'")

    # Pass 2: Latin — single combined regex so each position matched ONCE
    if latin_aliases:
        sorted_latin = sorted(latin_aliases.keys(), key=len, reverse=True)
        pattern = re.compile(
            r'\b(' + '|'.join(re.escape(a) for a in sorted_latin) + r')\b',
            re.IGNORECASE
        )

        def _replacer(m):
            matched = m.group(0)
            canonical = latin_aliases.get(matched.lower())
            if canonical:
                replacements_made.append(f"'{matched}' -> '{canonical}'")
                return canonical
            return matched

        normalized = pattern.sub(_replacer, normalized)

    if replacements_made:
        print(f"[HOTLIST] Pre-scan normalized {len(replacements_made)} drug names: {', '.join(replacements_made[:5])}")

    return normalized


def add_to_hotlist(original: str, corrected: str):
    """Auto-learn: when a doctor corrects a drug name, add the mapping to the hotlist.
    Uses file locking to prevent race conditions on concurrent saves."""
    global _drug_hotlist
    original_lower = original.strip().lower()
    if not (original_lower and corrected.strip() and original_lower != corrected.strip().lower()):
        return

    _drug_hotlist[original_lower] = corrected.strip()

    try:
        os.makedirs("data", exist_ok=True)
        with open(HOTLIST_PATH, "r+", encoding="utf-8") as f:
            # Exclusive lock — wait up to 2s
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                data = json.load(f)
                data.setdefault("aliases", {})[original_lower] = corrected.strip()
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=4, ensure_ascii=False)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        print(f"[HOTLIST] Auto-learned: '{original}' -> '{corrected}'")
    except FileNotFoundError:
        # File doesn't exist yet — create it
        with open(HOTLIST_PATH, "w", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump({"aliases": {original_lower: corrected.strip()}}, f, indent=4, ensure_ascii=False)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        print(f"[WARN] Failed to persist hotlist update: {e}")


# ─────────────────────────────────────────────────────────────
# PER-DOCTOR DRUG SHORTLIST (Layer 2)
# ─────────────────────────────────────────────────────────────
SHORTLIST_PATH = os.path.join(os.path.dirname(__file__), "data", "doctor_drug_shortlist.json")
_drug_shortlist: dict = {}   # {drug_name_lower: {"canonical": str, "count": int}}


def _load_shortlist():
    """Load the per-doctor frequently-prescribed drugs shortlist."""
    global _drug_shortlist
    try:
        with open(SHORTLIST_PATH, "r", encoding="utf-8") as f:
            _drug_shortlist = json.load(f)
        print(f"[INFO] Loaded doctor shortlist with {len(_drug_shortlist)} drugs.")
    except FileNotFoundError:
        _drug_shortlist = {}
    except Exception as e:
        print(f"[WARN] Failed to load shortlist: {e}")
        _drug_shortlist = {}


_load_shortlist()


def update_shortlist(drug_name: str):
    """
    Increment confirm count for a drug on the doctor's shortlist.
    Called every time the doctor confirms a medication.
    File-locked for concurrent safety.
    """
    global _drug_shortlist
    key = drug_name.strip().lower()
    if not key:
        return

    canonical = drug_name.strip()
    if key in _drug_shortlist:
        _drug_shortlist[key]["count"] += 1
        _drug_shortlist[key]["canonical"] = canonical  # update casing
    else:
        _drug_shortlist[key] = {"canonical": canonical, "count": 1}

    try:
        os.makedirs("data", exist_ok=True)
        # Atomic write with lock
        with open(SHORTLIST_PATH, "r+" if os.path.exists(SHORTLIST_PATH) else "w+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                try:
                    existing = json.load(f)
                except (json.JSONDecodeError, ValueError):
                    existing = {}
                existing[key] = _drug_shortlist[key]
                f.seek(0)
                f.truncate()
                json.dump(existing, f, indent=2, ensure_ascii=False)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        print(f"[SHORTLIST] Updated '{canonical}' → count={_drug_shortlist[key]['count']}")
    except Exception as e:
        print(f"[WARN] Failed to persist shortlist update: {e}")


def get_shortlist_suggestions(query: str, limit: int = 2) -> list:
    """
    Fuzzy + phonetic match of query against doctor's confirmed drug shortlist.
    Returns up to `limit` suggestions sorted by (score DESC, count DESC).
    Only returns suggestions with effective score >= 75.
    """
    if not _drug_shortlist or not query:
        return []

    query_lower = query.strip().lower()
    # Extract base query (strip trailing numbers/strengths) for partial matching
    query_base = re.sub(r'\s*\d+\s*(mg|ml|mcg|iu|%|g|gm)?\s*$', '', query_lower).strip()
    try:
        query_meta = jellyfish.metaphone(query_lower.split()[0])
    except Exception:
        query_meta = ""

    results = []
    for key, info in _drug_shortlist.items():
        canonical = info["canonical"]
        count = info.get("count", 1)

        # Strip strength from shortlist key for fairer comparison
        key_base = re.sub(r'\s*\d+\s*(mg|ml|mcg|iu|%|g|gm|duo|forte|retard|sr|er|xl|od)?\s*$', '', key).strip()

        # Multiple similarity measures — take max
        scores = [
            fuzz.token_set_ratio(query_lower, key, processor=fuzz_utils.default_process),
            fuzz.token_set_ratio(query_base, key_base, processor=fuzz_utils.default_process),
            fuzz.partial_ratio(query_base, key_base, processor=fuzz_utils.default_process),
            int(jellyfish.jaro_winkler_similarity(query_lower, key) * 100),
            int(jellyfish.jaro_winkler_similarity(query_base, key_base) * 100),
        ]
        best_score = max(scores)

        # Phonetic boost: +10 if metaphone of primary token matches
        phonetic_match = False
        if query_meta:
            try:
                key_primary_meta = jellyfish.metaphone(key.split()[0])
                if key_primary_meta == query_meta:
                    phonetic_match = True
            except Exception:
                pass

        score = min(best_score + (10 if phonetic_match else 0), 100)

        if score >= 75:
            results.append({
                "brand": canonical,
                "score": score,
                "count": count,
                "source": "shortlist"
            })

    # Sort: score descending, then frequency descending
    results.sort(key=lambda x: (-x["score"], -x["count"]))
    return results[:limit]



def get_shortlist_drugs() -> list:
    """Return all drugs in the shortlist, sorted by frequency (most prescribed first)."""
    drugs = [{"name": v["canonical"], "count": v["count"]}
             for v in _drug_shortlist.values()]
    drugs.sort(key=lambda x: -x["count"])
    return drugs


# ─────────────────────────────────────────────────────────────
# PUBLIC ENTRY POINTS
# ─────────────────────────────────────────────────────────────
async def generate_soap_note(transcript: str) -> dict:
    print(f"\n[INFO] === Starting generate_soap_note ===")
    print(f"[INFO] Transcript length: {len(transcript)} characters")

    if not transcript.strip() or transcript.strip() == 'Select language and click Start Recording...':
        print("[INFO] Empty transcript provided. Returning fallback directly.")
        return EMPTY_FALLBACK

    normalized_transcript = _prescan_drug_names(transcript)
    if normalized_transcript != transcript:
        print(f"[INFO] Transcript was normalized. New length: {len(normalized_transcript)} characters")

    start_time = time.time()
    try:
        result = await asyncio.wait_for(_call_gemini_with_retry(normalized_transcript), timeout=25.0)
        duration = time.time() - start_time
        print(f"[INFO] === generate_soap_note COMPLETED in {duration:.2f}s ===\n")

        # Safety pass: ground each medication in transcript text
        if "medications" in result and isinstance(result["medications"], list):
            extracted_count = len(result["medications"])
            result["medications"] = verify_grounding(result["medications"], normalized_transcript)
            # Layer 1: scan for potentially missed drugs
            extracted_names = [m.get("name", "") for m in result["medications"]]
            result["possible_missed_medications"] = scan_for_missed_drugs(
                normalized_transcript, extracted_names
            )
            print(f"[CONSULT LOG] Drugs extracted: {extracted_count} | Transcript length: {len(transcript)}", flush=True)

        return result
    except asyncio.TimeoutError:
        print(f"[ERROR] Gemini call timed out after {time.time() - start_time:.2f}s.")
        return EMPTY_FALLBACK
    except Exception as e:
        print(f"[ERROR] Gemini call failed: {e}")
        return EMPTY_FALLBACK


async def generate_soap_note_stream(transcript: str):
    print(f"\n[INFO] === Starting generate_soap_note_stream ===")
    print(f"[INFO] Transcript length: {len(transcript)} characters")

    if not transcript.strip() or transcript.strip() == 'Select language and click Start Recording...':
        print("[INFO] Empty transcript. Returning fallback.")
        yield json.dumps(EMPTY_FALLBACK)
        return

    normalized_transcript = _prescan_drug_names(transcript)
    if normalized_transcript != transcript:
        print(f"[INFO] Transcript normalized. New length: {len(normalized_transcript)}")

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY missing!")
        yield json.dumps(EMPTY_FALLBACK)
        return

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1)
        )
    )

    model_name = MODELS_TO_TRY[0]
    print(f"[INFO] Attempting stream call with model: {model_name}...")
    try:
        response_stream = await client.aio.models.generate_content_stream(
            model=model_name,
            contents=normalized_transcript,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                max_output_tokens=2048,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=512
                )
            )
        )
        async for chunk in response_stream:
            if chunk.text:
                yield chunk.text
        # Store normalized transcript for the grounding pass done in main.py
        yield f"\n__NORMALIZED_TRANSCRIPT__:{json.dumps(normalized_transcript)}"
    except Exception as e:
        print(f"[ERROR] Streaming failed: {e}. Falling back.")
        res = await generate_soap_note(transcript)
        yield json.dumps(res)
