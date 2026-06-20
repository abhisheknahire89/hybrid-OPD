import pandas as pd
import re

def process_data():
    df = pd.read_csv('data/indian_medicine_data.csv', encoding='utf-8')
    initial_rows = len(df)
    print(f"Columns: {df.columns.tolist()}")
    
    # Check if 'Is_discontinued' exists, otherwise default to False
    if 'Is_discontinued' in df.columns:
        df['Is_discontinued'] = df['Is_discontinued'].fillna(False).astype(bool)
        df = df[~df['Is_discontinued']]
    
    # Required columns
    required = ['name', 'short_composition1', 'short_composition2']
    for col in required:
        if col not in df.columns:
            df[col] = ''
            
    df['name'] = df['name'].fillna('').astype(str).str.strip()
    df['short_composition1'] = df['short_composition1'].fillna('').astype(str).str.strip()
    df['short_composition2'] = df['short_composition2'].fillna('').astype(str).str.strip()
    
    # Drop rows without a name
    df = df[df['name'] != '']
    
    # Combine compositions
    df['generic'] = df.apply(lambda row: ' + '.join([c for c in [row['short_composition1'], row['short_composition2']] if c]), axis=1)
    
    # Extract strength using regex (e.g. "10mg", "500 mg", "100ml", "1% w/v")
    def extract_strength(text):
        # find anything looking like a dosage/strength
        matches = re.findall(r'(\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|iu|%|w/v|w/w))', text, re.IGNORECASE)
        return ' + '.join(matches) if matches else ''
    
    df['strength'] = df['generic'].apply(extract_strength)
    
    # Deduplicate by name + generic
    df['key'] = df['name'].str.lower() + '|' + df['generic'].str.lower()
    df = df.drop_duplicates(subset=['key'])
    
    final_rows = len(df)
    
    print(f"Initial Rows: {initial_rows}")
    print(f"Final Rows: {final_rows}")
    print(df[['name', 'generic', 'strength']].head(10))

if __name__ == '__main__':
    process_data()
