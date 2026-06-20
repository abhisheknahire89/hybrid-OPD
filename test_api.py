import asyncio
import json
from llm import generate_soap_note

async def test():
    res = await generate_soap_note("Doctor: I am prescribing Dolo 650 mg twice daily. Patient: Okay.")
    print(json.dumps(res, indent=2))

asyncio.run(test())
