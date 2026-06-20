import csv
import time
import jellyfish
from rapidfuzz import fuzz, process, utils
from typing import List, Dict, Optional
from abc import ABC, abstractmethod

class DrugDatabase(ABC):
    @abstractmethod
    def find_matches(self, query: str) -> List[Dict]:
        """Return a ranked list of top 3 matches for the query."""
        pass

    @abstractmethod
    def get_loaded_count(self) -> int:
        pass

class CSVDrugDatabase(DrugDatabase):
    def __init__(self, filepath: str):
        self.drugs = []
        self._load_and_clean(filepath)

    def _map_generic_to_class(self, generic_str: str) -> str:
        if not generic_str:
            return "Unknown"
        import re
        # 1. Clean the generic string: split by ' + '
        parts = [p.strip() for p in generic_str.split('+') if p.strip()]
        classes = []
        
        # Hardcoded/Synonyms dictionary for common database variations
        synonyms = {
            'amoxycillin': 'amoxicillin',
            'clavulanic acid': 'clavulanate',
            'methylcobalamin': 'methylcobalamin',
            'mecobalamin': 'methylcobalamin',
            'fexofenadine': 'antihistamine',
            'vitamin d3': 'cholecalciferol',
            'calcium carbonate': 'calcium',
            'chlorpheniramine maleate': 'chlorpheniramine',
            'dextromethorphan hydrobromide': 'dextromethorphan',
            'levocloperastine': 'antitussive',
        }
        
        for part in parts:
            # Strip anything in parentheses, e.g., "Amoxycillin  (500mg) " -> "Amoxycillin"
            clean_part = re.sub(r'\s*\([^)]*\)', '', part).strip()
            clean_part_lower = clean_part.lower()
            
            mapped_class = self.generic_to_class_dict.get(clean_part_lower)
            if not mapped_class:
                # Check synonym map
                syn_key = synonyms.get(clean_part_lower)
                if syn_key:
                    mapped_class = self.generic_to_class_dict.get(syn_key)
            
            # If still not found, try substring matching
            if not mapped_class:
                for g, cls in self.generic_to_class_dict.items():
                    if g in clean_part_lower or clean_part_lower in g:
                        mapped_class = cls
                        break
            
            if mapped_class:
                classes.append(mapped_class)
            else:
                classes.append("Unknown")
        
        # Merge duplicates and filter out "Unknown" if we have at least one known class
        unique_classes = []
        for c in classes:
            if c != "Unknown" and c not in unique_classes:
                unique_classes.append(c)
                
        if not unique_classes:
            return "Unknown"
            
        return " + ".join(unique_classes)

    def _load_and_clean(self, filepath: str):
        self._cache = {} # Basic cache for find_matches
        import pandas as pd
        import re
        
        # First load the generic_to_class mapping
        self.generic_to_class_dict = {}
        try:
            import os
            class_csv_path = "data/generic_to_class.csv"
            if os.path.exists(class_csv_path):
                with open(class_csv_path, mode='r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    header = next(reader, None) # skip header
                    for row in reader:
                        if len(row) >= 2:
                            gen_name = row[0].strip().lower()
                            therap_class = row[1].strip()
                            if gen_name:
                                self.generic_to_class_dict[gen_name] = therap_class
                print(f"[INFO] Loaded generic-to-class mapping with {len(self.generic_to_class_dict)} entries.")
            else:
                print(f"[WARNING] Generic-to-class file not found at {class_csv_path}")
        except Exception as e:
            print(f"[ERROR] Failed to load generic-to-class mapping: {e}")

        try:
            df = pd.read_csv(filepath, encoding='utf-8')
        except Exception as e:
            print(f"[ERROR] Failed to load {filepath}: {e}")
            return
            
        initial_rows = len(df)
        
        if 'Is_discontinued' in df.columns:
            df['Is_discontinued'] = df['Is_discontinued'].fillna(False).astype(bool)
            df = df[~df['Is_discontinued']]
            
        required = ['name', 'short_composition1', 'short_composition2']
        for col in required:
            if col not in df.columns:
                df[col] = ''
                
        df['name'] = df['name'].fillna('').astype(str).str.strip()
        df['short_composition1'] = df['short_composition1'].fillna('').astype(str).str.strip()
        df['short_composition2'] = df['short_composition2'].fillna('').astype(str).str.strip()
        
        df = df[df['name'] != '']
        
        def combine_comp(row):
            comps = [c for c in [row['short_composition1'], row['short_composition2']] if c]
            return ' + '.join(comps)
            
        df['generic'] = df.apply(combine_comp, axis=1)
        
        def extract_strength(text):
            matches = re.findall(r'(\d+(?:\.\d+)?\s*(?:mg|ml|mcg|g|iu|%|w/v|w/w))', text, re.IGNORECASE)
            return ' + '.join(matches) if matches else ''
            
        df['strength'] = df['generic'].apply(extract_strength)
        
        df['key'] = df['name'].str.lower() + '|' + df['generic'].str.lower()
        df = df.drop_duplicates(subset=['key'])
        
        self.brand_choices = {}
        self.generic_choices = {}
        
        seen = set()
        loaded = 0
        
        for i, row in df.iterrows():
            brand = row['name']
            generic = row['generic']
            strength = row['strength']
            
            # Map therapeutic class
            t_class = self._map_generic_to_class(generic)
            
            self.drugs.append({
                "brand": brand,
                "generic": generic,
                "strength": strength,
                "therapeutic_class": t_class,
                "brand_lower": brand.lower(),
                "generic_lower": generic.lower(),
                "brand_phonetic": jellyfish.metaphone(brand),
                "generic_phonetic": jellyfish.metaphone(generic)
            })
            
            # Populate choices for rapidfuzz
            self.brand_choices[loaded] = brand.lower()
            self.generic_choices[loaded] = generic.lower()
            
            loaded += 1
            
        covered = sum(1 for d in self.drugs if d["therapeutic_class"] != "Unknown")
        coverage_pct = (covered / loaded * 100) if loaded > 0 else 0
        print(f"[INFO] CSVDrugDatabase loaded {loaded} unique drugs from {filepath}. (Initial: {initial_rows})")
        print(f"[INFO] Therapeutic Class Coverage: {coverage_pct:.2f}% ({covered}/{loaded} drugs covered)")

    def get_loaded_count(self) -> int:
        return len(self.drugs)

    def find_matches(self, query: str) -> List[Dict]:
        start_time = time.time()
        query = query.strip()
        if not query:
            return []

        query_lower = query.lower()
        if query_lower in self._cache:
            return self._cache[query_lower]
        
        t0_fuzzy = time.time()
        # 1. Fuzzy match against brands and generics in C++ using RapidFuzz
        brand_matches = process.extract(
            query_lower, self.brand_choices, 
            scorer=fuzz.token_set_ratio, processor=utils.default_process, 
            limit=150, score_cutoff=60
        )
        generic_matches = process.extract(
            query_lower, self.generic_choices, 
            scorer=fuzz.token_set_ratio, processor=utils.default_process, 
            limit=150, score_cutoff=60
        )
        t1_fuzzy = time.time()
        
        # Combine matches and keep the highest score per drug_id
        candidate_scores = {}
        max_fuzzy_score = 0
        
        for _, score, idx in brand_matches:
            if idx not in candidate_scores or score > candidate_scores[idx]['score']:
                candidate_scores[idx] = {'score': score, 'type': 'Fuzzy'}
            if score > max_fuzzy_score: max_fuzzy_score = score
                
        for _, score, idx in generic_matches:
            if idx not in candidate_scores or score > candidate_scores[idx]['score']:
                candidate_scores[idx] = {'score': score, 'type': 'Fuzzy (Generic)'}
            if score > max_fuzzy_score: max_fuzzy_score = score
                
        t0_phonetic = time.time()
        scored_candidates = []
        query_phonetic = jellyfish.metaphone(query_lower)
        
        for idx, match_info in candidate_scores.items():
            drug = self.drugs[idx]
            score = match_info['score']
            match_type = match_info['type']
            
            if query_lower == drug["brand_lower"] or query_lower == drug["generic_lower"]:
                score = 100
                match_type = "Exact"
            else:
                # 2a. Penalise extra unrequested ingredients to prevent superset matches
                # If target has more active ingredients than the query implies,
                # and the query is not a close sort-match for the brand (like 'telma am' vs 'telma-am tablet')
                ingredient_count = drug["generic"].count("+") + 1
                query_ingredient_count = query_lower.count("+") + 1
                
                if ingredient_count > query_ingredient_count:
                    sort_ratio = fuzz.token_sort_ratio(query_lower, drug["brand_lower"], processor=utils.default_process)
                    if sort_ratio < 60:
                        score -= 20  # Apply penalty to downgrade below 95 confidence
                        
                phonetic_boost = 0
                # 2b. Phonetic loop - Skip if we already have a strong fuzzy match globally
                if max_fuzzy_score < 90:
                    try:
                        if query_phonetic == drug["brand_phonetic"] or \
                           query_phonetic == drug["generic_phonetic"]:
                            phonetic_boost = 15
                        # If not exact metaphone match, fallback to match_rating_codex comparison if needed, 
                        # but simple metaphone equality is fast and robust for misspellings
                    except:
                        pass
                        
                score += phonetic_boost
                if score > 100:
                    score = 100
                if phonetic_boost > 0 and match_info['score'] < 80:
                    match_type = "Phonetic"

            confidence = "high" if score >= 95 else "uncertain — verify"
            
            scored_candidates.append({
                "brand": drug["brand"],
                "generic": drug["generic"],
                "strength": drug["strength"],
                "therapeutic_class": drug.get("therapeutic_class", "Unknown"),
                "score": score,
                "match_type": match_type,
                "confidence": confidence
            })
        t1_phonetic = time.time()

        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        top_candidates = scored_candidates[:3]
        
        # Log unmapped therapeutic class if the top candidate is Unknown
        if top_candidates and top_candidates[0]['score'] >= 60:
            top_cand = top_candidates[0]
            if top_cand.get("therapeutic_class") == "Unknown":
                print(f"[THERAPEUTIC CLASS LOG] Matched drug '{top_cand['brand']}' has Unknown therapeutic class for composition '{top_cand['generic']}'.", flush=True)
        
        # 3. Handle < 90 or phonetic floor edge case (unreliable database match)
        if not top_candidates or top_candidates[0]['score'] < 90 or top_candidates[0].get('match_type') == 'Phonetic':
            # Save suggestions with score >= 80 (excluding fallback)
            low_conf_suggestions = [c for c in top_candidates if c['score'] >= 80 and c.get('brand') != "No reliable match — enter manually"]
            
            fallback_match = {
                "brand": "No reliable match — enter manually",
                "generic": "",
                "strength": "",
                "therapeutic_class": "",
                "score": 0,
                "match_type": "None",
                "confidence": "uncertain — verify"
            }
            top_candidates = [fallback_match] + low_conf_suggestions
            
        self._cache[query_lower] = top_candidates
        
        duration = time.time() - start_time
        print(f"[PERF] find_matches('{query}') | Fuzzy: {(t1_fuzzy - t0_fuzzy):.4f}s | Phonetic: {(t1_phonetic - t0_phonetic):.4f}s | Total: {duration:.4f}s")
        return top_candidates
