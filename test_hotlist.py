import requests
import json
import time

url = "http://localhost:8000/api/generate_note"

# Simulate a Devanagari-mangled transcript (what IndicConformer produces)
# This is the kind of transcript that previously failed — drug names in Hindi script
data = {
    "transcript": """डॉक्टर: आओ रमेशभाई बैठो। क्या तकलीफ है?
पेशेंट: डॉक्टर साहब चक्कर आवे छे तीन-चार दिन से। शुगर भी हाई रहवे छे। कल सुबह 250 आवता। पैर में भी बहुत दुखे छे रात ना सुई नथी शकतो।
डॉक्टर: अच्छा BP चेक कराई छे? पहले टेल्मा AM चलता हतू ने?
पेशेंट: हा टेल्मा AM 40 चलता हतू but लास्ट वीक बंद कर दिधु फार्मसी में नहोतु
डॉक्टर: अरे ये तो continue करवानु होय। मैं लिखु छु। टेल्मा AM 40 एक टैबलेट रोज सवार मा खाली पेट। शुगर माटे ग्लाइकोमेट SR 500 दो बार सवार अने रात जमन पछी। चक्कर माटे वर्टिन 8 mg twice daily पांच दिवस। पैर माटे गैबापिन 100 रात ना एक सुता पहले।"""
}

print("=== DEVANAGARI TRANSCRIPT TEST (Previously failed) ===")
print(f"Transcript length: {len(data['transcript'])} chars\n")

start = time.time()
response = requests.post(url, json=data)
end = time.time()

print(f"Time: {end - start:.2f}s | Status: {response.status_code}\n")

result = response.json()
meds = result.get('medications', [])
print(f"Medications found: {len(meds)}")
for i, med in enumerate(meds):
    matches = med.get('matches', [])
    top_match = matches[0]['brand'] if matches else 'NO MATCH'
    print(f"  {i+1}. {med['name']} -> {top_match} (confidence: {med.get('confidence', '?')})")

print(f"\nChief Complaint: {result.get('chief_complaint', 'MISSING')}")
print(f"Advice: {result.get('advice', 'MISSING')[:100]}...")
