# SQLite Retrieval Spike

This spike is closed: **GO WITH CONDITIONS — vector-only**. The canonical journal is authoritative; FTS and vector indexes are disposable derived data.

## Selected route

Vector-only exact cosine over local `nomic-embed-text:latest` is the MVP route. It measured 91.67% top-1, 100% top-3, 51/51 embedding coverage, zero safety leaks, and p95 under 31 ms on mini-core. Every result carries journal-derived provenance.

Hybrid RRF measured 91.67% top-1 and 95.83% top-3; it did not improve the baseline and regressed Q21, so it is not selected. FTS-only measured 66.67% top-1 and 70.83% top-3 and is retained as a diagnostic route only.

## Limitations and conditions

The top-3 manual noise rate is 10.53% (6/57), narrowly above the ≤10% S4 threshold. Retrieval must not mutate state. Before production use, validate an expanded independent semantic benchmark and re-measure noise; do not tune against the current 24 queries.

## Reproduction

Run on a host with an OpenAI-compatible local embeddings endpoint:

```bash
export EMBEDDING_BASE_URL=http://localhost:11434/v1
export EMBEDDING_MODEL=nomic-embed-text:latest
export EMBEDDING_DIMENSION=768
python sqlite-spike/scripts/run_fts_baseline.py
python sqlite-spike/scripts/run_vector_hybrid.py
python sqlite-spike/scripts/review_noise.py --report sqlite-spike/results/vector-baseline.json
python sqlite-spike/scripts/rebuild_ab.py
```

Runtime `.db`, WAL, and SHM files are ignored. Commit the JSON evidence, not generated databases.
