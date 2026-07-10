# Graphiti spike decision

- **Decision date:** 2026-07-10
- **Verdict:** **NO — SQLite FTS5 + embeddings fallback**

## Tested stack

- Python 3.11.15
- graphiti-core 0.29.2
- FalkorDB 4.14.9
- qwen3.6:35b-mlx via a local OpenAI-compatible endpoint
- nomic-embed-text

## Evidence

Passed:

- canonical SQLite journal, dataset ingest (55 records; 1 idempotent retry) and event-state fold;
- N1 explicit edge invalidation;
- N2 workspace isolation and retry deduplication;
- low-level Graphiti/FalkorDB CRUD;
- per-workspace SequentialFalkorDriver and Graphiti client;
- `add_episode` without caller-provided `uuid` or `group_id`, including generated episode UUID and name-based recovery in the final driver/episode probe.

Failed:

- reliable structured output during the real Graphiti ingest workflow. For Graphiti's complex `SummarizedEntities` Pydantic schema, the local server's `json_object` mode can echo the injected JSON Schema instead of returning the required `summaries` instance. Its `json_schema` mode returned plain text rather than JSON.

The local wrapper only removes an outer Markdown fence and validates JSON; it does not create the schema echo. Correcting this would need prompt repair/retry, response filtering, schema conversion, or a different model/server. Those changes exceed N5 frozen patch budget; N6 therefore stops the experiment.

## Not evaluated

- full Graphiti ingest;
- 24-query retrieval benchmark;
- rebuild A/B equivalence.

These are **NOT EVALUATED**, not failed benchmarks.

## Selected direction

Keep the canonical SQLite journal and implement retrieval with SQLite FTS5, embeddings/vector index, metadata/workspace/time filtering, deterministic merge/ranking, explicit typed links, and journal supersede chains. Graphiti may be reconsidered only for a significant new version or a demonstrably reliable local structured-output stack, without extending this spike's patch budget.
