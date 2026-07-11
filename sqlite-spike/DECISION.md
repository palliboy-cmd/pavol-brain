# SQLite Retrieval Spike Decision

## GO WITH CONDITIONS — vector-only

Date: 2026-07-11

The canonical journal remains the source of truth. The selected MVP retrieval route is exact-cosine **vector-only** search over local `nomic-embed-text:latest` embeddings; FTS5 remains a diagnostic lexical route and hybrid RRF is not selected.

## Measured result

| Route | Top-1 | Top-3 | Selection |
|---|---:|---:|---|
| FTS-only | 66.67% | 70.83% | Below S1; not selected |
| Vector-only | 91.67% | 100% | Preferred MVP route |
| Hybrid RRF | 91.67% | 95.83% | Passes retrieval gates but not preferred; Q21 regressed |

- Embedding coverage: 51/51.
- Workspace, sensitive, and forbidden-status leaks: 0.
- Search p95: under 31 ms on mini-core.
- Rebuild A/B: projection hashes, document counts, embedding bytes, vector rankings/outcomes, and hybrid rankings/outcomes all equal.
- Active-build switch: PASS; failed build rejection: PASS.
- S4 noise: **FAIL**, 6/57 top-3 results = 10.53% (threshold ≤10%).

## Conditions

1. Use vector-only as the MVP retrieval route; do not use hybrid RRF.
2. Include provenance with every retrieval result.
3. A retrieval result must never execute a mutation itself.
4. Before production use, expand the semantic benchmark beyond these 24 queries and re-measure noise rate on that independent set.
5. Do not tune ranking, thresholds, or allow-lists against the current 24 scored queries.

Reproducible evidence is in `results/fts-baseline.json`, `results/vector-baseline.json`, `results/hybrid-baseline.json`, `results/noise-review.json`, and `results/rebuild-ab.json`.
