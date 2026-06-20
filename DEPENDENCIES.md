# Project Dependencies Explanation

This document explains the key dependencies specified in `requirements.txt` and their functional roles in the Medical Scribe prototype.

---

## 1. Web & API Framework

*   **`fastapi` (>=0.100.0, <1.0.0)**
    *   *Role*: The core ASGI web framework used to expose backend HTTP API endpoints (like SOAP note generation, correction logging, and session saving) and WebSocket connections.
*   **`uvicorn` (>=0.20.0, <1.0.0)**
    *   *Role*: High-performance ASGI server used to run and host the FastAPI application.

---

## 2. Real-Time Audio Streaming & Voice Activity Detection (VAD)

*   **`websockets` (>=11.0, <13.0)**
    *   *Role*: Handles bidirectional WebSocket communication between the client's browser and the server to stream live 16-bit PCM audio chunks and receive transcribed text segments in real time.
*   **`torch` (>=2.0.0, <3.0.0) & `torchaudio` (>=2.0.0, <3.0.0)**
    *   *Role*: PyTorch is the machine learning framework running the local deep learning models. `torchaudio` is used to load, slice, and process audio data.
*   **Silero VAD (`snakers4/silero-vad`)** *(Loaded dynamically via `torch.hub`)*
    *   *Role*: Voice Activity Detection model that evaluates incoming float32 tensors to detect speech and filter out silent or noisy audio chunks before running transcription.

---

## 3. Local Transcription Engine

*   **`transformers`**
    *   *Role*: Hugging Face's library used to preload and run the gated `ai4bharat/indic-conformer-600m-multilingual` conformer speech model.
*   **`huggingface_hub` (>=0.17.0, <1.0.0)**
    *   *Role*: Facilitates access, token-based authentication, downloading, and caching of Hugging Face models.
*   **`onnx` (>=1.19.0) & `onnxruntime` (>=1.19.0)**
    *   *Role*: Provides open-standard formats and execution environments to optimize model inference speeds.

---

## 4. LLM & SOAP Note Generation

*   **`google-generativeai` (>=0.5.0)**
    *   *Role*: Google's official SDK for interacting with Gemini models. It sends the pre-scanned transcript to Gemini (e.g., `gemini-2.5-flash`) for clinical data extraction, SOAP formatting, and structured JSON output.
*   **`tenacity` (>=8.2.0)**
    *   *Role*: Utility library providing robust retry logic and exponential backoff strategies in the event of API transient failures or rate limit caps.

---

## 5. Fuzzy Matcher & Drug Database

*   **`rapidfuzz` (>=3.0.0)**
    *   *Role*: Fast string-matching library used to compute fuzzy token-sort similarity scores when comparing spoken medication names against the 240k+ records in `indian_medicine_data.csv`.
*   **`jellyfish` (>=1.0.0)**
    *   *Role*: Provides phonetic string matching (Metaphone, Jaro-Winkler similarity) used in three places: (a) the DB drug matcher's phonetic boost, (b) the per-doctor shortlist suggestion engine, and (c) the anti-hallucination grounding verifier that checks each extracted drug name against the transcript.

---

## 6. Safety & Learning Systems (New)

*   **Anti-Hallucination Grounding (`llm.verify_grounding`)**
    *   *Role*: After every Gemini extraction, each medication is verified against the raw transcript using exact substring, word-token, and phonetic matching. Drugs with no transcript evidence are flagged `hallucination_risk=true` and displayed with a red badge — they cannot auto-confirm and require explicit doctor review.
    *   *Data file*: `data/hallucination_log.jsonl` — persistent log of flagged drugs for audit.

*   **Missed Drug Scanner (`llm.scan_for_missed_drugs`)**
    *   *Role*: After extraction, scans the transcript for drug-indicator words (`mg`, `tablet`, `TDS`, etc.) near un-extracted candidate terms. Returns up to 5 possible missed drug names logged and surfaced in the API response as `possible_missed_medications`.

*   **Per-Doctor Drug Shortlist (`data/doctor_drug_shortlist.json`)**
    *   *Role*: Learns from every confirmed prescription. When the doctor confirms a drug, `update_shortlist()` increments its count. Future garbled drug names (e.g., "Ajithral") are matched against the shortlist using fuzzy+phonetic scoring — shortlist suggestions (marked ⭐) appear as one-tap Accept buttons on unmatched drug cards.
    *   *Concurrency*: All shortlist and hotlist writes use `fcntl.LOCK_EX` (POSIX file lock) to prevent race conditions on concurrent API calls.
    *   *Data file*: `data/doctor_drug_shortlist.json` — JSON map of `{drug_name_lower: {canonical, count}}`.
