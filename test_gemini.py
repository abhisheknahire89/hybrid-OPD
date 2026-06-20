import os
import google.generativeai as genai

def test_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GEMINI_API_KEY environment variable is not set!")
        return

    print(f"[INFO] API Key loaded. Length: {len(api_key)}")
    genai.configure(api_key=api_key)

    model_name = "gemini-2.5-flash"
    print(f"[INFO] Attempting to connect using model: {model_name}")

    try:
        model = genai.GenerativeModel(model_name)
        print("[INFO] Sending 'Say hello' to Gemini...")
        response = model.generate_content("Say hello")
        print("\n[SUCCESS] Response received:")
        print(response.text)
    except Exception as e:
        print("\n[ERROR] Gemini API Call Failed:")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_gemini()
