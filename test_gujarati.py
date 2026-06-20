import requests
import json
import time

url = "http://localhost:8000/api/generate_note"

# Real mixed-language Gujarati/Hindi/English clinical transcript
data = {
    "transcript": """Doctor: Aavo Rameshbhai, baeso. Su thayu chhe? Kya taklif hai?

Patient: Doctor saheb, chakkar aave chhe... three-four days thi. Ane sugar bhi high rehve chhe — yesterday morning ma 250 aavtu'tu. Pug ma pan bahu dukhe chhe, raat na sui nathi shakto.

Doctor: Achha. BP check karaavi chhe? Tame pehla Telma chaltu hatu ne — Telma-AM?

Patient: Ha, Telma-AM 40 chaltu hatu, but last week thi band kari didhu... pharmacy ma nહોtu, wasn't available.

Doctor: Arre, e to continue karvanu hoy, band na karay. Okay listen, main likhu chhu. Telma-AM 40, ek tablet roj sawar ma, khaali pet. Sugar mate Glycomet SR 500, do baar — sawar ane raat, jaman pachi. Ane chakkar mate Vertin 8 mg, twice daily, paanch divas.

Patient: Ane a pug no dukhavo, doctor? Koi malam?

Doctor: Pug mate Gabapin 100 lakhi aapu chhu — raat na ek, sutaa pehle. Ane joao, paani vadhare pivo, sugar daily check karo, ane jaman ma salt kam karo. Next week pacha aavjo with fasting sugar report.

Patient: Thik chhe doctor saheb. Thank you."""
}

print("=== MIXED-LANGUAGE GUJARATI TEST ===")
print(f"Transcript length: {len(data['transcript'])} characters\n")
print("Sending request to:", url)

start = time.time()
response = requests.post(url, json=data)
end = time.time()

print(f"\n=== RESULTS ===")
print(f"Total request time: {end - start:.2f} seconds")
print(f"Status Code: {response.status_code}")
print(f"\nRaw JSON response:")

result = response.json()
print(json.dumps(result, indent=2, ensure_ascii=False))

# Summary
print(f"\n=== QUICK SUMMARY ===")
print(f"Chief Complaint: {result.get('chief_complaint', 'MISSING')}")
print(f"Diagnosis: {result.get('diagnosis', 'MISSING')}")
meds = result.get('medications', [])
print(f"Medications found: {len(meds)}")
for i, med in enumerate(meds):
    matches = med.get('matches', [])
    top_match = matches[0]['brand'] if matches else 'NO MATCH'
    print(f"  {i+1}. {med['name']} -> {top_match} (confidence: {med.get('confidence', '?')})")
