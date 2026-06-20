import asyncio
from llm import generate_soap_note

test_transcript = """
Doctor: Hello, what brings you in today?
Mother: He has had a fever for the last two days. It goes up at night.
Doctor: I see. Does he have a cough?
Child: My throat hurts a lot when I swallow.
Mother: He hasn't eaten much since yesterday. And I've had a bit of a headache too, actually.
Doctor: Ok, I will give him Azithromycin 250mg for 3 days, once daily.
Mother: So, Azithromycin twice daily?
Doctor: No, just once daily for him. Also give him Dolo 250 for the fever.
"""

async def run_test():
    result = await generate_soap_note(test_transcript)
    import json
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    asyncio.run(run_test())
