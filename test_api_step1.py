import requests
import json
import time

url = "http://localhost:8000/api/generate_note"
data = {
    "transcript": "Doctor: Patient presents with fever and cough for 2 days. Prescribed Dolo 650 twice daily. Patient: Yes doctor, I started feeling weak yesterday."
}

print("Sending request to:", url)
start = time.time()
response = requests.post(url, json=data)
end = time.time()

print(f"\nRequest completed in {end - start:.2f} seconds.")
print(f"Status Code: {response.status_code}")
print("\nRaw JSON response received by frontend:")
try:
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print("Failed to parse JSON response:", e)
    print("Raw text:", response.text)
