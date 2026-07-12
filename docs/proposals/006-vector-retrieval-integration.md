# Proposal 006: Vector-Only Retrieval Integration

- **Status:** Draft — design only, nothing implemented
- **Date:** 2026-07-12
- **Follows:** [Proposal 002](002-pavol-brain-shared-memory-and-knowledge-graph.md), [Proposal 003](003-graphiti-spike-design.md), [Proposal 004](004-graphiti-spike-architecture-review.md), [Proposal 005](005-sqlite-fts5-embeddings-retrieval-baseline.md)
- **Decision inputs:** `spike/DECISION.md` (**NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated.**), `sqlite-spike/DECISION.md` (GO WITH CONDITIONS — vector-only)

---

## 1. Executive summary

**NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated.** The SQLite retrieval baseline ended **GO WITH CONDITIONS — vector-only**: FTS-only missed the top-3 gate (70.83%), hybrid RRF passed but scored below vector-only (95.83% vs 100% top-3) and is not used. Vector-only achieved 91.67% top-1, 100% top-3, zero workspace/sensitive/status leaks, p95 under 31 ms, and 51/51 embedding coverage. A/B rebuild equivalence and the active-build switch both passed; S4 noise failed at 10.53% (threshold ≤10%), which is why the verdict carries conditions rather than a plain GO.

> **Erratum (2026-07-12 — ranking compatibility):** `sqlite-spike/results/vector-baseline.json` is immutable historical retrieval-quality evidence. Its explicit route sort was `score DESC, accepted-first, confidence DESC, valid_at ASC, record_id ASC`, yielding top-1 91.67%, top-3 100%, and S4 noise 10.53%. It is not the normative integration order. `sqlite-spike/results/vector-contract-baseline-v1.json` uses the same frozen queries, embeddings, build, candidates, and filters with the contract order `score DESC, valid_at DESC, record_id ASC`; it yields top-1 91.67%, top-3 95.83%, S4 noise 12.28%, and zero safety leaks. Slice 1 reports historical differences rather than redefining historical evidence. The verdict remains **GO WITH CONDITIONS — vector-only**; contract S4 remains FAIL and production conditions stay open.

This proposal defines the integration layer on top of that result:

- **Backend:** SQLite + local embeddings + exact cosine over the full eligible candidate set. `nomic-embed-text:latest` is the provisional spike baseline, not the final model decision.
- **Canonical truth** remains the append-only SQLite journal (`memory_records`, `record_state`, `artifact_links`). The retrieval index is a derived, disposable, rebuildable projection and never becomes authoritative.
- **Vector-only is the primary and only ranked route.** FTS5 remains a diagnostic/explicitly-requested fallback route and never silently substitutes for vector search.
- **Hybrid RRF is not used** in the MVP API. Existing hybrid code remains benchmark-only and is re-evaluated in Slice 5 on a new dataset; no RRF weights may be tuned on the existing 24 queries, and re-measurement does not automatically enable hybrid.
- Hermes, Codex, and Claude consume retrieval through a single small transport-neutral contract — `brain.search`, `brain.get_record`, `brain.get_related`, `brain.health`, `brain.rebuild_status` — with no knowledge of the SQLite schema, the embedding endpoint, or the build lifecycle. ChatGPT is not an MVP consumer.

Production use stays gated behind the carried-over conditions in §22: expanded benchmark, re-measured noise rate on an independent set, no tuning against the current 24 queries, and a strictly read-only retrieval API.

## 2. Goals

1. One **read-only retrieval API** shared by every agent; a single seam that hides backend details.
2. **Workspace / sensitivity / status / type / time filtering** applied as metadata predicates before ranking, identical to the baseline semantics.
3. **Provenance-rich results:** every hit is traceable to its canonical journal record; no free-floating text.
4. **Deterministic ranking:** same corpus + same query + same build ⇒ byte-identical result order.
5. **Incremental projection** from the journal so new accepted records become searchable without a full rebuild.
6. **Rebuild fallback:** a full fresh build with atomic active-build switch remains the recovery path for any incremental doubt.
7. **Stable integration seam:** the `brain.*` contract survives future backend changes (ANN, different embedding model, different store) without agent-side changes.

## 3. Non-goals

- No LLM entity extraction anywhere in the retrieval path (Proposal 004 closed this).
- No graph traversal; `get_related` follows explicit typed journal links only, one hop, no transitive walks.
- No ANN index in the MVP — the corpus is tens of records; exact cosine is measured at p95 < 31 ms.
- No agent-specific backend access: no agent opens `retrieval.db` or the journal directly.
- No mutations through the retrieval API — no write, no "log this as a fact", no side effects on the journal.
- No production server framework (FastAPI/uvicorn/etc.) in this phase; the interface-form decision in §14 governs when a network adapter appears.

## 4. Public internal API

The contract is five read-only operations. Names are stable; transports (library call, MCP tool, CLI) map onto them 1:1.

```python
brain.search(
    query: str,
    workspaces: list[str],          # REQUIRED, explicit scope, no default
    types: list[str] | None = None,
    mode: str = "current",          # "current" | "historical"
    as_of: str | None = None,       # ISO-8601, only with mode="historical"
    sensitive_allowed: bool = False,
    limit: int = 10,
    include_artifacts: bool = True,
    min_score: float | None = None,
    request_id: str | None = None,
) -> SearchResponse

brain.get_record(record_id: str) -> RecordEnvelope
brain.get_related(record_id: str, relation_types: list[str] | None = None) -> RelatedResponse
brain.health() -> HealthReport
brain.rebuild_status() -> RebuildStatus
```

Contract rules:

- `brain.search` **requires an explicit, non-empty `workspaces` list**. There is no "all workspaces" wildcard; omitting the argument is a validation error, not an implicit broadening.
- `get_record` returns the full canonical record envelope (journal fields + folded state), independent of the retrieval index, but respects sensitivity policy (§8).
- `get_related` returns only rows that exist in `artifact_links` / supersede chains — explicit typed links written by the journal, never inferred edges.
- `health` and `rebuild_status` never take or return record content.
- Every operation is idempotent and side-effect-free apart from audit logging (§17).

## 5. Search request schema

| Field | Type | Default | Validation |
|---|---|---|---|
| `query` | string | — required | non-empty after trim; max 2 000 chars; rejected if empty (`BRAIN_EMPTY_QUERY`) |
| `workspaces` | string[] | — required | non-empty; each value must exist in the active build's workspace list (`BRAIN_UNKNOWN_WORKSPACE` otherwise); duplicates deduped |
| `types` | string[] | `null` = all eligible types | each value in the journal type enum (`decision`, `outcome`, `fact`, `preference`, `artifact_link`, `correction`); unknown type → validation error |
| `mode` | enum | `"current"` | `current` or `historical` only |
| `as_of` | ISO-8601 timestamp | `null` | allowed only with `mode="historical"`; `mode="historical"` without `as_of` means "include superseded records, evaluated at now" |
| `sensitive_allowed` | bool | `false` | must be `true` to include any sensitive-workspace scope; `false` + sensitive workspace in `workspaces` → validation error, not silent filtering |
| `limit` | int | `10` | 1–50; values above 50 rejected, not clamped, so callers notice |
| `include_artifacts` | bool | `true` | when `false`, `artifact_links` arrays are omitted from results (cheaper payload) |
| `min_score` | float | `null` | Reserved for a future schema. **Before Slice 5, every non-null value is rejected with structured error `BRAIN_FEATURE_NOT_ENABLED`; no threshold is applied.** This technically prevents tuning against the existing 24 queries. |
| `request_id` | string | auto-generated UUIDv7 | caller-supplied trace ID propagated to audit log and response |

Validation is fail-fast and total: an invalid request returns a structured error and touches neither the embedding endpoint nor the index.

## 5.1 Transport-neutral contract schemas

Slice 1 fixes both the Python models and transport-neutral JSON Schemas for `SearchRequest`, `SearchResponse`, `RecordEnvelope`, `RelatedResponse`, `HealthReport`, `RebuildStatus`, and structured errors (including `code`, `message`, and field-level validation details). They are **one contract**, generated from or checked against the same source; tests must fail on any divergence. Slice 4's MCP server maps these schemas 1:1 and must not change field semantics, defaults, or validation rules.

## 6. Search response schema

```jsonc
{
  "request_id": "0197f3a2-…",
  "retrieval_build_id": "build-2026-07-11T21:04:12Z-a3f9",
  "embedding_model": "model-name@sha256:…",   // required model fingerprint, not a fixed model name
  "mode": "current",
  "degraded": false,          // true only for the explicit FTS fallback route (§19)
  "results": [
    {
      "record_id": "rec-031",
      "score": 0.8231,          // raw exact cosine
      "rank": 1,
      "workspace": "ai-pos",
      "type": "decision",
      "sensitivity": "normal",
      "status": "accepted",
      "valid_at": "2026-06-02T10:11:00Z",
      "invalid_at": null,
      "is_current": true,
      "title": "Adopt commitment classification model",
      "snippet": "…deterministic excerpt from canonical_text…",
      "provenance": {
        "journal_record_id": "rec-031",
        "source_event_id": 412,
        "projection_hash": "ph-9c41…",
        "superseded_by": null,
        "supersedes": ["rec-017"]
      },
      "artifact_links": [
        {"relation": "implements", "uri": "git://ai-pos@8c2f6e3", "link_record_id": "rec-032"}
      ],
      "projection_hash": "ph-9c41…",
      "embedding_model": "model-name@sha256:…",
      "retrieval_build_id": "build-2026-07-11T21:04:12Z-a3f9"
    }
  ]
}
```

Hard rule: **a result without provenance is invalid.** The layer must refuse to emit any hit whose `record_id`, `projection_hash`, or `source_event_id` cannot be resolved — that is treated as index corruption (§19), not as a degraded answer. `snippet` is generated deterministically from the stored canonical projection (fixed offset/length rules), never by a model.

## 7. Ranking semantics

1. **Filter, then rank.** Workspace, sensitivity, type, status, and time predicates run as SQL metadata filters first; only eligible documents are scored. A record outside scope never has a score computed — scoring an ineligible record and discarding it is forbidden, since it would leak via timing/limit interactions.
2. **Exact cosine** between the query embedding and every eligible document embedding. No ANN, no quantization in MVP.
3. **Deterministic tie-break:** order by `score DESC, valid_at DESC, record_id ASC`. `record_id ASC` is the final total-order guarantee.
4. **No LLM reranking, no hidden heuristics.** No recency boosts, type weights, or popularity signals. Any future ranking feature requires a new benchmarked proposal.
5. **Score semantics:** raw cosine similarity in `[-1, 1]` (in practice `[0, 1]` for this model), reported to 4 decimals, comparable only within a single `(embedding_model, retrieval_build_id)` pair. Scores must not be compared across builds or models and must not be presented as calibrated confidence.
6. **Historical mode** ranks over the corpus that was valid per `as_of` / supersede evaluation (§9); ranking math is otherwise identical. Current mode never returns superseded, forgotten, rejected, or candidate records regardless of score.
7. `limit` truncates after the total order is established, so pagination-free repeated calls are stable.

## 8. Workspace and sensitivity policy

- **Explicit scope is mandatory.** Every search names its workspaces; the library has no ambient default workspace.
- **No implicit cross-workspace search.** Cross-workspace results occur only when the caller explicitly lists multiple workspaces in the same request.
- **Sensitive workspaces** are excluded from resolution unless `sensitive_allowed=true`. Requesting a sensitive workspace with `sensitive_allowed=false` is a loud validation error (the caller must know it asked for something it did not unlock), while `sensitive_allowed=true` grants access only to the sensitive workspaces actually listed — it is not a global unlock.
- **Zero leak is a hard invariant**, carried from the baseline (0 leaks across all runs). Any result outside the resolved scope is a release-blocking defect, never a tolerable noise rate.
- **Audit:** every request logs both the *requested* scope and the *resolved* scope (after sensitivity policy) with the `request_id`, so leak audits can be replayed from logs alone (§17).
- `get_record` and `get_related` apply the same policy: fetching a sensitive record by ID still requires `sensitive_allowed=true`.

## 9. Current and historical truth

- **Current mode** (default): only records whose folded state is `accepted`, not superseded, not forgotten, with `invalid_at` null or in the future. This is "what the brain believes now."
- **Historical mode:** includes superseded records. With `as_of`, eligibility is evaluated at that instant: a record counts if `valid_at <= as_of` and (`invalid_at` is null or `invalid_at > as_of`).
- **Supersede chains** come from explicit journal links. Every historical result carries `superseded_by` / `supersedes` so a caller can walk to the current successor via `get_related`.
- **Always excluded, both modes:** `forgotten` (hard exclusion — forgotten records are also removed from the index, §11), `rejected`, and `candidate` records. Historical mode widens time, not trust.
- **Obsolete record behavior:** when a query's best match is superseded, current mode returns the successor if it matches (or nothing — it does not "helpfully" surface the obsolete one); historical mode returns the obsolete record with `is_current: false` and a populated `superseded_by`, never presenting it as unmarked truth.

## 10. Provenance and artifact links

- Every result resolves back to exactly one canonical journal record: `record_id` + `source_event_id` + `projection_hash` reproduce the indexed document from the journal deterministically.
- **Artifact links are explicit typed links** from `artifact_links` (relation + normalized URI + the link's own record ID). The retrieval layer transports them; it never creates, infers, or ranks them.
- **No model-invented relation edges** — **NO for the tested local Graphiti 0.29.2 + structured-output stack; graph retrieval quality was not evaluated.** Relatedness here exists only where the journal wrote it.
- Recommended representation (as in §6): artifact links nested inside each result under `artifact_links`, and provenance as a dedicated `provenance` object rather than flattened top-level fields, so agents can pass the provenance block through to citations verbatim.

## 11. Incremental indexing

The projector is a single-writer process advancing a cursor over the journal:

- **Journal cursor:** the index stores `last_source_event_id` (monotonic journal event ID). Incremental runs process only events beyond the cursor, in journal order.
- **Projection hash:** per document, `hash(projection_schema_version, type, workspace, canonical_text)` exactly as in the baseline. Unchanged hash ⇒ no re-embed, no re-write.
- **Embedding cache:** keyed by `(projection_hash, embedding_model_fingerprint)`. Model change invalidates everything; text change invalidates one document.
- **New accepted record:** project → embed (cache miss only) → insert document + vector → advance cursor, in one transaction per event batch.
- **Supersede:** the superseded document's `is_current` flips and `invalid_at` is set; it stays indexed for historical mode. **Forget:** the document and its vector are deleted from the index entirely — forgotten content must not be retrievable in any mode. **Reject:** never indexed; if a previously accepted record is corrected to a non-eligible status, it is removed like a forget.
- **Idempotency:** replaying the same event range is a no-op (hash + cursor make projection idempotent), matching the journal's own idempotent-submission semantics.
- **Failure recovery:** the cursor advances only after a fully committed batch. On crash, the projector resumes from the last committed cursor; on any inconsistency (hash mismatch, orphan vector, cursor ahead of journal), the answer is not repair-in-place but a fresh rebuild (§12). Incremental is an optimization, never a second source of truth.

## 12. Rebuild and active-build switch

Carried directly from the baseline, where A/B equivalence and switching passed:

1. **Fresh build:** project and embed the entire eligible journal into a new build directory/DB (`build_id` = timestamp + short hash), independent of the active build.
2. **Validation:** document count vs. eligible journal records, embedding coverage 100%, dimension check against the model fingerprint, spot A/B ranking equivalence against the outgoing build on a fixed probe set.
3. **Ready state:** a validated build is marked `ready`; only `ready` builds are eligible for activation.
4. **Atomic switch:** a single pointer (one row / one symlink) names the active build; readers resolve it per request. The switch is atomic and in-flight queries finish on the build they started with.
5. **Rollback:** the previous build is retained; rollback is repointing to it — no rebuild required.
6. **Failed build never active:** a build failing any validation stays in `failed` state and cannot be pointed to (the baseline verified this rejection path).
7. **Cleanup policy:** keep the active build plus the most recent previous `ready` build; older builds and all `failed` builds are deleted by the builder after a successful switch (with `failed` build logs retained).

## 13. Runtime topology

MVP topology, deliberately boring:

- **mini-core is the primary retrieval host.** The projector, the retrieval library, and the active `retrieval.db` all live there.
- **Embeddings** come from a local Ollama/MLX-compatible endpoint on mini-core, loopback only. The runtime contract is model-name agnostic and always records a mandatory model fingerprint; `nomic-embed-text:latest` is only the provisional spike baseline.
- **`retrieval.db` is local disk on mini-core** — never on a network share; SQLite over NFS/SMB is a corruption risk.
- **NAS is backup only:** periodic snapshots of the canonical journal (the thing that matters) and optionally the active build (a convenience — builds are rebuildable).
- **MBP** is a future dev/offline candidate: it would run its own rebuild from a journal copy, never sync `retrieval.db` files, and never write back.
- **No multi-master.** Exactly one host owns the journal and one projector owns the index. Offline replicas are read-only and derived.

## 14. Interface form

| Option | Pros | Cons |
|---|---|---|
| **Python library** | Zero deployment; contract = typed function signatures; trivially testable; no auth surface; matches "thin layer, low maintenance" | Only in-process Python callers; each consumer process needs the package and local DB access |
| **Local CLI** | Universal (anything that can exec); scriptable; trivial wrapper over the library | Per-call process + model-handshake overhead; JSON-over-stdout contract is easy to drift; weak typing |
| **MCP server** | Native for Hermes/Codex/Claude-style agents; tools map 1:1 onto `brain.*`; read-only tools are a natural fit | A running process to manage; MCP spec churn; still needs the library underneath |
| **Small HTTP service** | Universal transport; language-agnostic | Pulls in the server framework, auth, and hardening this phase explicitly excludes (§3); most maintenance for least MVP benefit |

**Recommendation: Python library first (`brain` package) as the canonical contract, with a thin MCP server as the single agent-facing adapter in Slice 4.**

Rationale: the library *is* the contract — every other form is a serializer around it, so building it first means the CLI/MCP/HTTP question never changes the semantics. It carries zero operational surface while the GO conditions are still open. MCP covers the MVP consumers Hermes, Codex, and Claude; HTTP is deferred until a concrete non-MCP consumer exists.

## 15. Agent integration

All agents call the same contract, differing only in transport:

- **Hermes** (mini-core resident): imports the `brain` library directly in-process — the lowest-latency path — or uses the local MCP server once Slice 4 lands, whichever matches its runtime.
- **Codex:** MCP client → `brain_search` / `brain_get_record` / `brain_health` tools exposed by the Slice-4 MCP server.
- **Claude (Claude Code / agents):** same MCP server, registered as a local stdio or loopback server.
- **ChatGPT:** not an MVP consumer. Any future integration requires a separate security/topology decision; this proposal designs no public endpoint, tunnel, remote authentication, or side channel.

Every transport maps to the identical request/response schemas of §5–§6; the MCP tool descriptions state that `workspaces` is mandatory and results carry provenance that must be cited, not paraphrased away.

Agents must **not**:

- open `retrieval.db` or the journal SQLite files directly (enforced by filesystem permissions: only the brain service user reads them);
- know or configure the embedding model/endpoint — the model fingerprint in responses is informational provenance, not a knob;
- trigger, switch, or delete retrieval builds — build lifecycle belongs to the projector/operator only;
- write anything through the search API — there is no mutating tool to call; journal writes remain a separate, already-governed path.

## 16. Health and diagnostics

`brain.health()` returns:

- `active_build_id` and its creation timestamp;
- `journal_cursor` (`last_source_event_id`) of the active build **and** the journal head event ID — their gap is the staleness measure;
- `indexed_document_count` (total and `is_current` counts);
- `embedding_coverage` (embedded/eligible; must be 1.0 for a `ready` build);
- `embedding_model` fingerprint (name + digest + dimension);
- `last_successful_rebuild` timestamp and build ID; last failed build ID if any;
- `embedding_endpoint` status (probe result + latency);
- `stale_index` boolean: true when cursor gap exceeds a configured event-count or age threshold;
- `per_workspace_counts` of indexed current documents (workspace names and counts only — no content, and the sensitive flag governs whether sensitive workspace names appear for callers without `sensitive_allowed`).

`brain.rebuild_status()` reports the builder state machine: `idle | building | validating | ready | switching | failed`, with the in-progress build ID, started-at, per-phase progress, and the last error for `failed`.

## 17. Observability and audit

Per request, the audit log records: `request_id`, timestamp, operation, requested scope, resolved scope, filter summary (types/mode/as_of/limit/sensitive_allowed), active build ID, latency (embed + search split), result count, and **returned record IDs**. Record IDs are enough to replay leak audits without storing content.

- **No raw query text is logged by default** — query text is the most sensitive field in the system (it reveals what Pavol is thinking about), so default logs are metadata-only.
- **Debug mode** (explicit, per-invocation or short-lived config flag) additionally logs query text and scores, and is itself recorded in the log line so its use is auditable.
- **Retention:** audit logs rotate; default retention 90 days for metadata logs, 7 days for debug logs, local disk only, included in NAS backup only if explicitly configured.
- Latency histograms (p50/p95) and error counters per operation are derivable from the audit log; no separate metrics stack in MVP.

## 18. Security and privacy

- **Local-only by default:** the library touches only local files and the loopback embedding endpoint. The MCP adapter binds to `127.0.0.1` (or stdio); no listening on external interfaces.
- **If ever network-exposed** (not in this phase): static bearer token minimum, per-agent tokens preferred, TLS termination required — but the default answer to remote access is "don't; run the agent where the brain is."
- **Sensitive handling:** sensitive results are never cached by the adapter layer, never written to debug logs unless debug mode *and* `sensitive_allowed` were both set, and sensitive query text follows the same rule.
- **No secrets in the DB or results:** journal policy already forbids storing credentials as records; the retrieval layer additionally must not embed configuration secrets (tokens, paths acting as capabilities) into responses or logs.
- **No conversation or chain-of-thought storage:** the retrieval layer stores journal projections only. Agent conversations, prompts, and reasoning never enter the index or the audit log (queries excepted, under §17 rules).

## 19. Failure modes

| Failure | Behavior |
|---|---|
| Embedding endpoint down | `brain.search` fails fast with `BRAIN_EMBEDDING_UNAVAILABLE`. **Default is fail-closed, not silent FTS.** A caller may explicitly pass a degraded-mode flag to get FTS diagnostic results; the response then carries `degraded: true` and agents must surface that. FTS failed the top-3 gate — it is a labeled emergency route, never a transparent substitute. |
| `retrieval.db` missing/corrupt | Health reports it; search returns `BRAIN_INDEX_UNAVAILABLE`. Recovery = fresh rebuild from the journal (§12); the journal is never at risk from index loss. |
| Stale build (cursor gap over threshold) | Queries still answer (old truth beats no truth) but every response and `health()` carries `stale_index: true`; the projector is expected to catch up or a rebuild is triggered. |
| Embedding dimension mismatch (model changed under us) | Build validation fails → build never activates. At query time, a fingerprint mismatch between endpoint and active build aborts with `BRAIN_MODEL_MISMATCH` rather than comparing incomparable vectors. |
| Empty query | Validation error `BRAIN_EMPTY_QUERY`, nothing executed. |
| Unknown workspace | Validation error `BRAIN_UNKNOWN_WORKSPACE`, listing the offending names — never silently dropped from scope. |
| No results | Empty `results` array with full envelope (build ID, scope echo). An empty list is a valid answer, not an error, and must not trigger automatic scope widening. |
| Active build failed to switch | Pointer still names the previous `ready` build; queries continue uninterrupted; `rebuild_status()` shows `failed` with the error. |
| Recovery, generally | Order of preference: resume incremental from committed cursor → rollback to previous build → fresh rebuild. Never repair index rows in place. |

## 20. MVP implementation slices

1. **Slice 1 — library contract + read-only search:** `brain` Python package implementing §4–§10 over the existing baseline build; validation, ranking, provenance, and the leak invariant under test. No projector changes.
2. **Slice 2 — incremental projection:** journal cursor, projection-hash reuse, embedding cache, supersede/forget invalidation, idempotent replay (§11).
3. **Slice 3 — health/status:** `brain.health()` and `brain.rebuild_status()` (§16), staleness detection, fingerprint checks.
4. **Slice 4 — MCP adapter:** thin MCP server exposing the fixed Slice-1 schemas 1:1 as read-only loopback/stdio tools for Hermes/Codex/Claude. It must not alter fields, semantics, defaults, or validation.
5. **Slice 5 — extended benchmark and model bake-off:** at least 100 new semantic queries; predeclared quotas for Slovak, English, multilingual/paraphrase, technical jargon, identifiers/artifact paths, historical/current, and sensitive/cross-workspace policy cases; and roughly 10× distractor corpus relative to the original corpus. Gates require a minimum sample size and confidence-interval reporting; a boundary result from one sample is not reliable evidence that a gate is crossed. No tuning against the old set. Candidate list (proposal, not commitment): nomic-embed-text v1 baseline, Nomic Embed V2, BGE-M3, and a suitable local Qwen embedding variant. Before implementation, verify availability, license, RAM/disk needs, Ollama/MLX compatibility, multilingual performance, and dimensions.
6. **Slice 6 — production readiness review:** conditions checklist, security pass on the adapter, retention/backup verification, and the GO/NO decision to drop "WITH CONDITIONS."

Each slice is independently shippable and reviewable; Slices 5–6 gate the production label, not the earlier slices' merging.

## 21. Acceptance criteria

- **Candidate-and-score parity:** the library matches the historical `results/vector-baseline.json` candidates and raw cosine scores for all frozen queries on the same build.
- **Contract-order parity:** the library matches `results/vector-contract-baseline-v1.json` for all 24 query orders under `score DESC, valid_at DESC, record_id ASC`.
- **Historical-order visibility:** any difference from the historical vector route order is reported explicitly; historical order parity is not a Slice 1 failure and the historical baseline is never silently redefined.
- **Zero leaks:** no workspace, sensitivity, or forbidden-status leak in any test, including adversarial scope combinations — hard invariant, not a rate.
- **Determinism:** repeated identical queries against the same build return byte-identical rankings.
- **Provenance complete:** every result in every test resolves to a journal record with valid `projection_hash` and `source_event_id`; a synthetic orphan is refused, not returned.
- **Reproducible rebuild:** fresh build A/B matches the incremental-maintained index (hashes, counts, rankings), re-running the baseline's equivalence check.
- **Staleness detected:** a journal write without projection flips `stale_index` within the configured threshold.
- **No mutation path:** the API surface contains no write operation; tests confirm journal byte-equality after arbitrary API use.
- **Tests:** unit tests for validation/ranking/tie-breaks and integration tests for search/health/rebuild flows run in CI.
- **Latency:** search p95 ≤ 50 ms on mini-core at current corpus size (baseline measured < 31 ms; budget allows library overhead, and any regression beyond it fails the slice).

## 22. GO WITH CONDITIONS carry-over

Explicitly restated from `sqlite-spike/DECISION.md`; this proposal does not weaken any of them:

1. **Vector-only is the selected route.** Exact cosine over local embeddings; the concrete model remains provisional and the fingerprint is mandatory.
2. **Hybrid RRF is disabled** and absent from the API; existing code is benchmark-only, is re-measured in Slice 5, and is not automatically enabled by that measurement.
3. **The benchmark must be expanded** beyond the 24 queries before any production label (Slice 5).
4. **Noise rate must be re-verified on an independent set** — S4 failed at 10.53% against the ≤10% gate; the retest happens on new queries, not a re-scored old set.
5. **No tuning** of ranking, thresholds, or allow-lists against the existing 24 scored queries. `min_score` is technically disabled pre-Slice-5: any non-null request value returns `BRAIN_FEATURE_NOT_ENABLED`.
6. **Provenance with every result** and **no mutations through retrieval** are structural properties of this design (§6, §4), not optional behaviors.
7. **The "production" label** is granted only at Slice 6, after conditions 3–4 pass.

## 23. Decision points

Decisions this proposal makes (defaults that stand unless overturned in review):

1. **Primary interface form:** Python library as the contract core; MCP server as the sole agent-facing adapter (Slice 4). No HTTP service, no CLI as an integration target.
2. **SQLite DB placement:** local disk on mini-core; NAS holds backups only; no network-filesystem hosting of any SQLite file.
3. **Process topology:** single retrieval host (mini-core), single projector process, readers via library/MCP on the same host; no multi-master, no replicas in MVP.
4. **Active build ownership:** the projector/builder process exclusively owns build creation, validation, switching, rollback, and cleanup; the query path only dereferences the active pointer.
5. **Query audit level:** metadata + returned record IDs by default; raw query text only in explicit, logged, short-retention debug mode.
6. **Embedding-outage fallback:** fail closed by default; FTS available only as an explicit, `degraded: true`-labeled emergency route.

## 24. Open questions

Only those that block implementation:

1. **Hermes transport:** does Hermes import the Python library in-process, or must even the local resident go through MCP for uniformity? Decides whether Slice 1 or Slice 4 unblocks Hermes.
2. **Sensitive-workspace grant model for MCP:** `sensitive_allowed` is a request flag — but which agents are *permitted* to set it, and is that enforced per-tool-configuration or per-token? Blocks Slice 4's tool definitions.
3. **Staleness threshold:** the concrete cursor-gap/age values that flip `stale_index` (needed for Slice 3's tests; a placeholder default of "any gap older than 1 h" can be adopted at review).

## 25. Recommended next step

Implement **Slice 1 only**: the `brain` Python package with `search` / `get_record` / `get_related` / `health` (static fields) over the existing baseline build — request validation, filter-then-rank exact cosine, deterministic tie-breaks, provenance-complete responses, and the acceptance tests for baseline parity, zero leaks, and determinism (§21).

Not implemented yet — this document is design only.
