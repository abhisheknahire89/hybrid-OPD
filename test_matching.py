from drug_db import CSVDrugDatabase

def main():
    print("Loading database...")
    db = CSVDrugDatabase("data/indian_medicine_data.csv")
    print("Database loaded.")

    queries = [
        "Metamophin",
        "dolo",
        "telma am"
    ]

    for q in queries:
        print(f"\n--- Matches for '{q}' ---")
        matches = db.find_matches(q)
        if not matches:
            print("No matches found.")
        for m in matches:
            print(f"[{m['match_type']} - Score: {m['score']}] Brand: {m['brand']} | Generic: {m['generic']} | Class: {m['therapeutic_class']} | Confidence: {m['confidence']}")

if __name__ == "__main__":
    main()
