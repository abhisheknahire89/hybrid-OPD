import asyncio
import time
import requests
import json
import os
import sys

# Ensure current directory is on sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import llm
from google.genai import types

test_transcript = """
Doctor: Namaste, kya problem hai?
Daughter: Doctor, these are my father's reports. Unka diabetes mellitus check-up karna tha. He is elderly.
Doctor: Achha, and normal glucose level kya hai?
Daughter: Glycosylated hemoglobin is 8.5%. Aur unke pairon me tingling and numbness (neuropathy) hoti hai.
Doctor: I see. Diabetic neuropathy developed ho rahi hai. Current medications kya le rahe hain?
Daughter: Abhi to Glycomet chal rahi hai.
Doctor: Ok, I will change/add medications.
Pehle to write down Glycomet SR 500mg, din me do baar khane ke baad (after food). Make sure it's Glycomet SR 500.
Daughter: Do baar means BD na doctor?
Doctor: Haan, BD. Aur subah khali pet ya breakfast ke samay, write Glimepiride 1mg. Subah me ek tablet lena hai.
Daughter: Glimepiride 1mg once daily in morning. Ok.
Doctor: Pairon me tingling numbness ke liye, Gabapin 100mg ek tablet raat ko sote samay lijiye. 100mg at night.
Daughter: So Gabapin 100 at night. Freq HS?
Doctor: Yes, HS. Aur daily supplement ke liye, Mecobalamin once daily lijiye, OD.
Daughter: And what about the vertigo/giddiness? Unhe chakkaer bhi aate hain.
Doctor: Vertigo ke liye Vertin 8mg de raha hoon, but only SOS. SOS only, take it when he feels dizzy, not daily.
Daughter: Ok, Vertin 8mg SOS. What about Januvia? Doctor in Pune suggested Januvia 100mg.
Doctor: Pune doctor suggested Januvia? Nahi nahi, abhi nako. We will manage with these for now. Januvia cancel karo.
Daughter: Accha, cancel Januvia. Aur doctor, my own stomach burns. Mala pan acidity hote, can you prescribe something for me?
Doctor: No, for you we will check later. But for Uncle, write Pantoprazole 40mg subah khali pet daily for 5 days only. Pantoprazole 40 subah khali pet, paanch din ke liye bas.
Daughter: Pantoprazole 40mg for 5 days only, empty stomach.
Doctor: Yes, that is all. Make sure to do steam inhalation if he gets congested, and check blood sugar weekly. Keep him hydrated.
"""

async def measure_runs():
    url = "http://localhost:8000/api/generate_note"
    data = {"transcript": test_transcript}
    
    # 1. Run with live server (which currently has thinking_budget=512)
    print("Sending request to live server (thinking_budget=512)...")
    t0 = time.time()
    response = requests.post(url, json=data)
    t1 = time.time()
    latency_with_budget = t1 - t0
    print(f"Latency with thinking_budget=512: {latency_with_budget:.2f} seconds")
    
    if response.status_code != 200:
        print(f"Error from server: {response.text}")
        return
        
    res_json = response.json()
    print("\nLive Server Response JSON:")
    print(json.dumps(res_json, indent=2, ensure_ascii=False))
    
    # 2. To measure without thinking_budget (thinking_budget=0), we call the local function directly
    # and temporarily patch GenerateContentConfig to use thinking_budget=0.
    import main
    
    # Mock / patch llm.py calls
    original_call = llm._call_gemini_with_retry
    
    async def mocked_call(transcript: str) -> dict:
        api_key = os.environ.get("GEMINI_API_KEY")
        from google import genai
        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=1)
            )
        )
        model_name = llm.MODELS_TO_TRY[0]
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=transcript,
            config=types.GenerateContentConfig(
                system_instruction=llm.SYSTEM_PROMPT,
                response_mime_type="application/json",
                max_output_tokens=2048,
                thinking_config=types.ThinkingConfig(
                    thinking_budget=0
                )
            )
        )
        return llm._parse_json_response(response.text.strip())
        
    llm._call_gemini_with_retry = mocked_call
    
    print("\nSleeping 7 seconds to avoid rate limiting...")
    await asyncio.sleep(7.0)
    
    print("\nCalling local pipeline directly with thinking_budget=0...")
    t0 = time.time()
    note_json = await llm.generate_soap_note(test_transcript)
    main._process_medications(note_json)
    t1 = time.time()
    latency_without_budget = t1 - t0
    print(f"Latency with thinking_budget=0: {latency_without_budget:.2f} seconds")
    
    # Restore original call
    llm._call_gemini_with_retry = original_call
    
    # Validation checks on live server response (res_json)
    meds = res_json.get("medications", [])
    med_names = [m.get("name", "") for m in meds]
    print(f"\nExtracted Medications: {med_names}")
    
    # Pass/Fail checklist
    checklist = {
        "Exactly 6 drugs": len(meds) == 6,
        "Januvia ABSENT": not any("januvia" in n.lower() for n in med_names),
        "Daughter acidity drug ABSENT": not any("acidity" in n.lower() or "pantocid" in n.lower() or "pan-d" in n.lower() for n in med_names if n != "Pantoprazole 40"),
        "Vertin marked SOS/as-needed, NOT daily": False,
        "Pantoprazole duration = 5 days ONLY": False,
        "Diagnosis captured (diabetes/neuropathy)": False,
        "All frequencies + routes populated": True,
    }
    
    # Check meds detail
    for m in meds:
        m_name = m.get("name", "").lower()
        if "vertin" in m_name:
            freq = m.get("frequency", {}).get("value", "")
            if freq in ["SOS", "PRN", "as needed", "if needed"]:
                checklist["Vertin marked SOS/as-needed, NOT daily"] = True
        if "pantoprazole" in m_name:
            dur = m.get("duration", {}).get("value", "")
            if "5 days" in dur.lower():
                checklist["Pantoprazole duration = 5 days ONLY"] = True
                
    diagnosis = res_json.get("diagnosis", "").lower()
    if "diab" in diagnosis or "neuropathy" in diagnosis:
        checklist["Diagnosis captured (diabetes/neuropathy)"] = True
        
    for m in meds:
        freq = m.get("frequency", {}).get("value")
        route = m.get("route", {}).get("value")
        # Check that value is not None, 'null', 'undefined'
        if freq is None or str(freq).lower() in ["null", "undefined", ""] or route is None or str(route).lower() in ["null", "undefined", ""]:
            checklist["All frequencies + routes populated"] = False
            
    print("\n=== VERIFICATION RESULTS ===")
    all_pass = True
    for item, status in checklist.items():
        symbol = "[x]" if status else "[ ]"
        print(f"{symbol} {item}")
        if not status:
            all_pass = False
            
    print(f"\nStop->Note Latency with budget: {latency_with_budget:.2f}s")
    print(f"Stop->Note Latency without budget: {latency_without_budget:.2f}s")
    
    if all_pass:
        print("\nALL VERIFICATION CHECKS PASSED!")
    else:
        print("\nSOME VERIFICATION CHECKS FAILED.")

if __name__ == "__main__":
    if "GEMINI_API_KEY" not in os.environ:
        print("Error: GEMINI_API_KEY environment variable is not set!")
        sys.exit(1)
    asyncio.run(measure_runs())
