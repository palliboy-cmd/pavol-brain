from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'spike.db'
DATASET = ROOT / 'dataset' / 'records.jsonl'
RESULTS = ROOT / 'results'
