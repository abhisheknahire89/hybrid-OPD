# -*- coding: utf-8 -*-
import requests
import json
import time
import os

def test_unmatched_drugs():
    url = "http://localhost:8000/api/generate_note"
    # Transcript containing a made-up brand ("Zyximax 200") and a real-but-rare brand ("Rarimed 10")
    data = {
        "transcript": "पेशेंट: पेट में बहुत दर्द है डॉक्टर साहब। डॉक्टर: ठीक है, मैं आपको पेट दर्द के लिए एक नयी दवा लिख रहा हूँ, ज़ायक्सीमैक्स 200 (Zyximax 200) एक गोली रोज़ सुबह खाने के बाद 5 दिन के लिए। और कमज़ोरी के लिए रारीमेड 10 (Rarimed 10) एक गोली रात को सोने से पहले लें।"
    }
    
    # Clean previous log if exists to ensure clean verification
    log_path = "data/unmatched_drugs_log.jsonl"
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
            print(f"Cleared previous log at {log_path}")
        except Exception as e:
            print(f"Could not clear log: {e}")
            
    print("=== TESTING UNMATCHED DRUGS FALLBACK ===")
    print("Sending request to generate_note...")
    t0 = time.time()
    response = requests.post(url, json=data)
    duration = time.time() - t0
    
    print(f"Status Code: {response.status_code}")
    print(f"Call Latency: {duration:.2f} seconds")
    
    if response.status_code != 200:
        print(f"Error: {response.text}")
        return False
        
    res = response.json()
    print("\nParsed Medications:")
    meds = res.get("medications", [])
    print(json.dumps(meds, indent=2, ensure_ascii=False))
    
    # Assertions
    assert len(meds) >= 2, f"Expected at least 2 medications, got {len(meds)}"
    
    # Find Zyximax and Rarimed in extracted list
    zyximax_found = False
    rarimed_found = False
    
    for med in meds:
        name_lower = med.get("name", "").lower()
        if "zyximax" in name_lower:
            zyximax_found = True
            assert med.get("is_unverified") is True, f"Zyximax should be marked is_unverified = True"
            # Verify top match is unverified
            top_match = med.get("matches", [{}])[0]
            assert top_match.get("score", 0) < 60 or top_match.get("brand") == "No reliable match — enter manually", "Zyximax should have score < 60 or enter manually fallback"
            # Verify no suggestions (due to floor threshold >= 80)
            other_matches = med.get("matches", [])[1:]
            assert len(other_matches) == 0, f"Zyximax 200 should have NO suggestions, but got: {other_matches}"
            print("✓ Zyximax 200 verified as unmatched fallback successfully.")
            
        elif "rarimed" in name_lower:
            rarimed_found = True
            assert med.get("is_unverified") is True, f"Rarimed should be marked is_unverified = True"
            top_match = med.get("matches", [{}])[0]
            assert top_match.get("score", 0) < 60 or top_match.get("brand") == "No reliable match — enter manually", "Rarimed should have score < 60 or enter manually fallback"
            print("✓ Rarimed 10 verified as unmatched fallback successfully.")
            
    assert zyximax_found, "Could not find Zyximax in extracted medications"
    assert rarimed_found, "Could not find Rarimed in extracted medications"
    
    # Verify the log file contents
    assert os.path.exists(log_path), f"Log file not found at {log_path}"
    
    logged_names = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                logged_names.append(entry.get("drug_name"))
                
    print(f"\nLogged drugs in {log_path}: {logged_names}")
    
    # Verify both are logged
    assert any("zyximax" in name.lower() for name in logged_names if name), "Zyximax not found in log"
    assert any("rarimed" in name.lower() for name in logged_names if name), "Rarimed not found in log"
    print("✓ Logging verification successful.")
    print("\nALL TESTS PASSED SUCCESSFULLY!")
    return True

if __name__ == "__main__":
    test_unmatched_drugs()
