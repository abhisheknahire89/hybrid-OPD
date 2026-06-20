import asyncio
from llm import generate_soap_note
from drug_db import CSVDrugDatabase

async def main():
    print("Loading DB...")
    db = CSVDrugDatabase("data/indian_medicine_data.csv")
    
    results = []
    
    # Helper to test direct matcher
    def test_direct(input_str):
        print(f"Testing direct: {input_str}")
        matches = db.find_matches(input_str)
        top = matches[0] if matches else None
        
        # Format for table
        matcher_input = input_str
        brand = top['brand'] if top else 'N/A'
        generic = top['generic'] if top else 'N/A'
        confidence = top['confidence'] if top else 'N/A'
        
        results.append(f"| {input_str} (direct) | {matcher_input} | {brand} | {generic} | {confidence} |")
        
    # Helper to test pipeline
    async def test_pipeline(input_str):
        print(f"Testing pipeline: {input_str}")
        transcript = f"Doctor: daily {input_str} lijiye."
        note = await generate_soap_note(transcript)
        meds = note.get("medications", [])
        if not meds:
            matcher_input = "NONE"
            matches = []
        else:
            matcher_input = meds[0].get("name", "Unknown")
            matches = db.find_matches(matcher_input)
            
        top = matches[0] if matches else None
        
        brand = top['brand'] if top else 'N/A'
        generic = top['generic'] if top else 'N/A'
        confidence = top['confidence'] if top else 'N/A'
        
        results.append(f"| {input_str} (pipeline) | {matcher_input} | {brand} | {generic} | {confidence} |")

    await test_pipeline("मेटामॉर्फिन / Metamophin")
    test_direct("Metformin")
    test_direct("dolo")
    test_direct("telma am")
    test_direct("Metamophin")
    test_direct("blarghax")
    
    print("\n### Part 4: Test Results Table\n")
    print("| Input | Matcher Input Received | Top Match | Generic | Confidence |")
    print("|---|---|---|---|---|")
    for r in results:
        print(r)

if __name__ == "__main__":
    asyncio.run(main())
