# Proposal 005: SQLite FTS5 + Embeddings Retrieval Baseline

- **Status:** Completed — GO WITH CONDITIONS (vector-only)
- **Date:** 2026-07-10
- **Follows:** [Proposal 002](002-pavol-brain-shared-memory-and-knowledge-graph.md), [Proposal 003](003-graphiti-spike-design.md), [Proposal 004](004-graphiti-spike-architecture-review.md)
- **Decision input:** `spike/DECISION.md` — Graphiti NO; canonical journal retained

---

## 1. Decision to validate

Test whether a small, deterministic retrieval layer built from **SQLite FTS5 + local embeddings** is sufficient for Pavol-Brain over the existing canonical journal. The baseline reuses the Graphiti spike's records and 24 benchmark query IDs so results remain traceable, while removing LLM extraction and graph lifecycle from the retrieval path.

The journal remains the only source of truth. FTS and vectors are disposable, rebuildable indexes. Typed artifact links and supersede chains remain explicit journal data; they are not inferred by a model.

> **Final result (2026-07-11):** The FTS-only route missed S1 (top-3 70.83%). Local `nomic-embed-text:latest` exact-cosine retrieval achieved 91.67% top-1 and 100% top-3 with 51/51 embedding coverage and zero workspace, sensitive, or forbidden-status leaks. Hybrid RRF achieved 91.67% top-1 and 95.83% top-3, so it is not selected. Fresh build A/B equivalence and active-build switching passed. S4 noise rate is 10.53% (6/57 top-3 results), narrowly above the ≤10% threshold. The selected MVP direction is **vector-only**, with the bounded conditions recorded in `sqlite-spike/DECISION.md`.

## 2. Goals

1. Build a complete retrieval index from `memory_records`, folded `record_state`, and `artifact_links`.
2. Combine lexical and semantic retrieval with deterministic workspace, sensitivity, type, status, and time filters.
3. Return current and historical truth predictably, with record-level provenance.
4. Measure retrieval quality, isolation, latency, index size, idempotency, and rebuild equivalence on the existing spike corpus.
5. Produce a hard GO / GO WITH CONDITIONS / NO decision without changing the future `brain.*` API.

## 3. Non-goals

- production MCP/REST server, authentication, dashboard, or domain integrations;
- knowledge-graph traversal, entity extraction, community detection, or LLM-generated links;
- conversation ingestion or chain-of-thought storage;
- approximate-nearest-neighbour infrastructure for a corpus of tens or hundreds of records;
- cloud model comparison in the first baseline;
- mutation of canonical `memory_records` to support retrieval;
- physical privacy redaction beyond the existing journal decision.

## 4. Existing inputs and benchmark preflight

Inputs are reused, not regenerated:

- `spike/schema/journal.sql`;
- `spike/dataset/records.jsonl` (56 submissions; expected 55 persisted after the exact idempotency retry);
- `spike/dataset/queries.json` (24 query IDs).

The query manifest is not currently score-ready: **20 of 24 queries expect a record whose workspace is outside the query's declared scope**. A scoped retrieval system must not return those records. Before measuring the backend, an automated preflight must emit a report and a versioned manifest derived from the same 24 query IDs. The original file remains immutable historical evidence.

Permitted benchmark-manifest corrections are limited to `scope`, explicit query tags, filters, and expected records. Query IDs and the underlying record corpus remain stable. Every correction must be listed in `benchmark-manifest-diff.json`. A backend verdict is forbidden while any query is internally inconsistent or references an ineligible record.

Each score-ready query must contain:

```json
{
  "id": "Q01",
  "query": "...",
  "scope": ["ai-pos"],
  "filters": {"types": ["decision"], "mode": "current"},
  "expected_top": ["rec-001"],
  "allowed_alternatives": [],
  "tags": ["lexical", "multilingual"],
  "failure_condition": "workspace leak or ineligible status"
}
```

## 5. Canonical text projection

Each eligible record produces one deterministic retrieval document. JSON is parsed and rendered by type, never embedded as arbitrary key order:

- `decision`: statement, rationale, alternatives, artifact URIs;
- `outcome`: summary, changes, verification, open questions, artifact URIs and commit;
- `fact`: subject, predicate, object, evidence;
- `artifact_link`: source record, relation, normalized artifact URI;
- `correction`: reason and referenced old/new record IDs; no standalone current truth unless accepted by policy;
- `preference`: statement/value and declared scope.

The projection stores a short `title`, a full `body`, normalized `artifacts_text`, and `canonical_text = title + body + artifacts`. Its hash includes projection schema version, record type, workspace, and normalized text. The embedding is recomputed only when that hash or the model fingerprint changes.

## 6. Derived SQLite schema

The baseline may use a separate `retrieval.db` or namespaced tables in a disposable copy. It must not add triggers that make the canonical journal dependent on retrieval.

```sql
CREATE TABLE retrieval_builds (
  build_id           TEXT PRIMARY KEY,
  projection_version INTEGER NOT NULL,
  embedding_model    TEXT NOT NULL,
  embedding_dim      INTEGER NOT NULL,
  status             TEXT NOT NULL CHECK (status IN ('building','ready','failed','retired')),
  source_event_id    TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  completed_at       TEXT
);

CREATE TABLE retrieval_documents (
  doc_id          INTEGER PRIMARY KEY,
  build_id        TEXT NOT NULL REFERENCES retrieval_builds(build_id),
  record_id       TEXT NOT NULL,
  workspace       TEXT NOT NULL,
  type            TEXT NOT NULL,
  sensitivity     TEXT NOT NULL CHECK (sensitivity IN ('normal','sensitive')),
  status          TEXT NOT NULL,
  review          TEXT NOT NULL,
  valid_at        TEXT NOT NULL,
  invalid_at      TEXT,
  supersedes      TEXT,
  superseded_by   TEXT,
  is_current      INTEGER NOT NULL CHECK (is_current IN (0,1)),
  title           TEXT NOT NULL,
  body            TEXT NOT NULL,
  artifacts_text  TEXT NOT NULL DEFAULT '',
  canonical_text  TEXT NOT NULL,
  projection_hash TEXT NOT NULL,
  UNIQUE (build_id, record_id)
);

CREATE INDEX idx_retrieval_filter
  ON retrieval_documents(build_id, workspace, sensitivity, status, type, valid_at, invalid_at);

CREATE VIRTUAL TABLE retrieval_fts USING fts5(
  title,
  body,
  artifacts_text,
  content='retrieval_documents',
  content_rowid='doc_id',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE retrieval_embeddings (
  build_id        TEXT NOT NULL,
  record_id       TEXT NOT NULL,
  model           TEXT NOT NULL,
  dimensions      INTEGER NOT NULL,
  vector_format   TEXT NOT NULL CHECK (vector_format = 'float32-le'),
  vector           BLOB NOT NULL,
  vector_norm      REAL NOT NULL,
  projection_hash TEXT NOT NULL,
  created_at       TEXT NOT NULL,
  PRIMARY KEY (build_id, record_id),
  FOREIGN KEY (build_id, record_id)
    REFERENCES retrieval_documents(build_id, record_id)
);

CREATE TABLE retrieval_active_build (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  build_id  TEXT NOT NULL REFERENCES retrieval_builds(build_id)
);
```

FTS rows are inserted/deleted explicitly in the rebuild transaction. No journal trigger writes to FTS. For this corpus, semantic search is exact cosine over float32 vectors loaded from `retrieval_embeddings`; an ANN extension is deliberately deferred. This gives a deterministic reference implementation against which a future `sqlite-vec` path can be checked.

## 7. Eligibility and filters

Filtering happens **before final ranking** and is never delegated to prompt text.

### 7.1 Projection eligibility

- project `accepted` current records;
- retain `superseded` records only for explicit historical mode;
- never project `candidate`, `rejected`, or `forgotten` records;
- exact idempotency duplicates produce one document;
- artifact links are included only when active and policy-eligible.

### 7.2 Query requirements

- at least one explicit workspace is required;
- related workspaces are expanded by policy before retrieval;
- a sensitive workspace is included only when explicitly requested and `sensitive_allowed=true`;
- `types` is an allow-list over record types;
- current mode requires `is_current=1` and `status='accepted'`;
- historical mode may include `superseded`, but the result must expose status, `invalid_at`, and supersede pointers;
- `as_of=T` requires `valid_at <= T` and `(invalid_at IS NULL OR invalid_at > T)`;
- forgotten/rejected/candidate rows are excluded even if an old index accidentally contains them.

Every result carries `record_id`, workspace, type, sensitivity, state, validity interval, rank components, projection hash, and artifact provenance.

## 8. Candidate generation and hybrid ranking

For each query and already-authorized scope:

1. FTS5 retrieves up to `K_fts=30` eligible candidates using `MATCH`; lower `bm25()` is better.
2. The query is embedded once. Exact cosine retrieves up to `K_vec=30` eligible candidates.
3. Candidate sets are unioned by `record_id`.
4. Deterministic Reciprocal Rank Fusion computes:

```text
rrf = w_fts / (60 + rank_fts) + w_vec / (60 + rank_vec)
```

Default weights are `w_fts=0.5`, `w_vec=0.5`. A missing route contributes zero. Ties resolve by: current before historical, higher confidence, newer `valid_at`, then lexical `record_id`. No LLM reranker is allowed in the baseline.

Weights are chosen on a declared calibration subset or fixed before the scored run. They must not be tuned against all 24 expected answers. Report FTS-only, vector-only, and hybrid results so the value of embeddings is measurable.

## 9. Embedding contract

- baseline model: the available local `nomic-embed-text` endpoint;
- record exact model identifier, dimensions, endpoint profile, and normalization behavior in the build manifest;
- reject a response with unexpected dimensions, NaN/Inf, empty vector, or near-zero norm;
- use the same documented query/document prefixes if required by the selected model deployment;
- never store API credentials in SQLite or results;
- batching and caching may improve rebuild time but must not change vector bytes for the same model fingerprint and projection hash.

## 10. Rebuild and recovery

1. Verify `record_state` by folding all journal events; abort on mismatch.
2. Create a fresh `build_id` with status `building`.
3. Read only the journal snapshot ending at `source_event_id`.
4. Project eligible documents deterministically and populate FTS.
5. Generate or reuse embeddings only on an exact projection-hash + model-fingerprint match.
6. Validate exact counts, embedding coverage, filter invariants, and zero forbidden statuses.
7. Mark the build `ready` and atomically switch `retrieval_active_build` in one transaction.
8. Keep the previous ready build until post-switch queries pass; then it may be retired/deleted.

Replaying the same snapshot with a new build ID must produce exact equality for document IDs by record, projection hashes, FTS source text, embedding bytes, and scored query pass/fail outcomes. Rowids may differ and are not compared. A failed build never becomes active.

## 11. Benchmark execution

The validated 24-query manifest is run against three routes on the same build:

- FTS-only;
- vector-only;
- hybrid RRF.

Store per query: query ID/text, resolved workspace scope, filters, expected and returned IDs, per-route ranks/scores, final rank, state/current flag, latency, and each failure condition. Run one warm-up plus five measured repetitions; quality is calculated once per deterministic ranking, latency across repetitions.

Automatic metrics:

- top-1 and top-3 hit rate;
- multilingual top-3 by explicit query tag;
- noise rate in top-3 using declared relevant/allowed sets;
- workspace and sensitive leaks;
- candidate/rejected/forgotten leaks;
- current truth and historical/as-of correctness;
- exact duplicate documents and embedding coverage;
- rebuild equivalence;
- p50/p95 query latency, projection latency, and total rebuild time;
- database size and bytes per indexed record.

If no query has a required tag (for example multilingual or historical), that metric is `NOT EVALUATED`, never PASS. Manual relevance judgments are stored separately and do not silently alter automatic results.

## 12. GO / NO criteria

### GO gates

| ID | Metric | Threshold |
|---|---|---:|
| S1 | Hybrid expected record in top-3 | ≥ 80% |
| S2 | Hybrid expected record top-1 | ≥ 60% |
| S3 | Multilingual top-3 | ≥ 70%, if scoreable |
| S4 | Noise rate in top-3 | ≤ 10% |
| S5 | Workspace leaks | 0 |
| S6 | Sensitive leaks | 0 |
| S7 | Candidate/rejected/forgotten leaks | 0 |
| S8 | Current truth after supersede | 100% |
| S9 | Historical/as-of truth | 100%, if scoreable |
| S10 | Duplicate documents | 0 |
| S11 | Eligible embedding coverage | 100% |
| S12 | Rebuild deterministic equivalence | PASS |
| S13 | Hybrid search latency p95, warm local corpus | < 500 ms |
| S14 | Index projection latency p95 excluding embedding network time | < 100 ms/record |

GO requires all applicable gates and a valid 24-query manifest. **GO WITH CONDITIONS** is allowed only for a non-safety soft gate with a concrete, bounded mitigation—for example multilingual S3 below threshold while all isolation/current-truth gates pass. FTS-only outperforming hybrid is not failure: the selected MVP may use FTS-only if it meets every applicable gate.

Immediate NO conditions:

- any workspace or sensitive leak;
- any candidate/rejected/forgotten retrieval leak;
- inability to reconstruct current/historical truth from the journal;
- rebuild mismatch in projection hashes, eligibility, embeddings, or query outcomes;
- embeddings require non-deterministic LLM parsing or a server component disproportionate to this corpus;
- the minimal implementation grows beyond the bounded plan below without improving a measured gate.

An invalid benchmark manifest produces **PENDING — benchmark correction required**, not backend NO.

## 13. Minimal implementation plan

No framework and no server. Add a new isolated experiment directory; do not reopen or mutate the closed Graphiti spike evidence.

```text
sqlite-spike/
├── README.md
├── requirements.txt
├── schema.sql
├── dataset/
│   ├── queries.json              # validated derivative of the same 24 IDs
│   └── benchmark-manifest-diff.json
├── src/
│   ├── projection.py             # typed canonical text + eligibility
│   ├── embeddings.py             # local endpoint + float32 encoding
│   ├── index.py                  # FTS and embedding persistence/rebuild
│   ├── search.py                 # filters, FTS, cosine, RRF
│   └── evaluation.py             # pure metric functions
├── scripts/
│   ├── validate_benchmark.py
│   ├── rebuild.py
│   ├── query.py
│   └── evaluate.py
├── tests/
└── results/
```

Implementation order:

1. Benchmark preflight: validate records, folded states, scopes, expected IDs, tags, and failure conditions; produce the reviewed derivative manifest.
2. Projection: implement pure functions per record type plus eligibility/filter unit tests.
3. FTS baseline: schema, rebuild, FTS-only query and provenance output.
4. Embeddings: local batch client, dimension/normalization validation, exact cosine reference search.
5. Hybrid: RRF, deterministic ties, current/historical and sensitive policy tests.
6. Evaluation: run all three routes, latency repetitions, automatic metrics and `NOT EVALUATED` handling.
7. Rebuild A/B: clean rebuild twice, byte/hash/count/query equivalence, active-build switch test.
8. Decision: write measured results and select FTS-only, hybrid, GO WITH CONDITIONS, or NO.

Minimum tests cover canonical text determinism, event-fold eligibility, all forbidden statuses, workspace/sensitivity/as-of filters, FTS tokenization, vector dimension validation, cosine ordering, RRF/tie-breaking, duplicate prevention, manifest validation, and rebuild equivalence.

## 14. Expected outcome

The likely MVP is SQLite FTS5 plus exact local-vector reranking over a small eligible candidate set. The point of the baseline is to prove whether embeddings materially improve the same benchmark without sacrificing determinism, privacy, rebuildability, or maintenance cost. If FTS5 alone clears all gates, prefer it; complexity must earn its place through measured retrieval quality.
