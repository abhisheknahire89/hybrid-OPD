import asyncio
from llm import generate_soap_note

test_transcript = """
Doctor: नमस्ते, tell me what is the issue?
Patient: Sir, मुझे दो दिन से severe headache हो रहा है। And sometimes चक्कर भी आता है।
Doctor: Have you checked your ब्लड प्रेशर?
Patient: Yes, it was normal. 120/80.
Doctor: Ok. I will give you a टैबलेट for the pain. Take पैरासिटामोल 650mg, दिन में दो बार.
Patient: And what about the चक्कर?
Doctor: For that, I'm giving you वर्टिगो टैबलेट. Take it if you feel dizzy. Also, avoid looking at screens at night.
"""

async def run_test():
    result = await generate_soap_note(test_transcript)
    import json
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(run_test())
