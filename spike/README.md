# Pavol-Brain Graphiti/FalkorDB retrieval-index spike

> **CLOSED — NO.** This is not a production component. Its artifacts remain reproducible technical evidence; the active project direction is a canonical SQLite journal with FTS5 + embeddings/vector retrieval. See `DECISION.md`.

This is an intentionally small Python experiment inside `pavol-brain`, not a production Pavol-Brain server. SQLite is the append-only journal; Graphiti/FalkorDB is disposable and may only receive accepted or historical-superseded records. Candidates, rejected records, and forgotten records are excluded.

## Historical diagnostic reproduction

```sh
uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -r requirements.txt
docker compose up -d
.venv/bin/python scripts/ingest.py
.venv/bin/python scripts/verify_state.py
.venv/bin/python scripts/model_probe.py
.venv/bin/python scripts/project.py --build-id build-a --reset --limit 1
.venv/bin/python scripts/project.py --build-id build-a --confirm-full
.venv/bin/python scripts/query.py --build-id build-a
.venv/bin/python scripts/evaluate.py --build-id build-a
.venv/bin/python scripts/rebuild.py --build-a build-a --build-b build-b
.venv/bin/python scripts/evaluate.py --build-id build-b --compare-builds build-a build-b
```

`checkpoint.py` uses the installed graphiti-core API directly: `FalkorDriver`, `Graphiti.add_triplet`, `EntityEdge.get_by_uuid`, `EntityEdge.save`, and `Graphiti.search(group_ids=[...])`. It is the mandatory gate; do not run a benchmark unless both N1 and N2 pass. Results are written under timestamped `results/` directories and contain no secrets.

## Local model profile

The only supported profile is `GRAPHITI_PROFILE=local`. The adapter explicitly constructs Graphiti's `OpenAIGenericClient`, `OpenAIEmbedder`, and `OpenAIRerankerClient`; it never relies on Graphiti's implicit OpenAI client. Safe local defaults target `http://localhost:11434/v1`, `qwen3.6:35b-mlx`, and `nomic-embed-text:latest` with dimension `768`. Override them with the `GRAPHITI_*` variables in `.env.example`. A blank or missing model setting raises `model_configuration_missing` before connecting. Checkpoint output records the profile, base URL, model names, and embedding dimension—never the API key.

The dataset has 56 JSONL inputs (55 unique persisted records plus one exact idempotency retry), including SK/EN/DE-shaped topics, candidates, rejected records, a supersede case, cross-workspace names, artifact links, and synthetic `sap-work` content only. Repository validation is read-only.

The historical commands below are diagnostic material. Do not resume a Graphiti full projection, benchmark, or rebuild under this closed spike.

`project.py` projects only accepted and superseded records. It creates Graphiti episodes for decisions, outcomes, and facts, plus a deterministic `ASSERTS` triplet for every eligible record. Per-build mappings, deterministic edge UUIDs, projection events, failures, and latency are persisted in the SQLite journal. `rebuild.py` uses distinct `spike_<build>` FalkorDB graphs and resets only build B's graph.

For FalkorDB, the spike uses a sequential driver wrapper because graphiti-core 0.29.2 schedules index creation in the stock Falkor driver constructor. The wrapper schedules nothing; `Adapter.initialize()` awaits index creation, health check, and a create/read/delete smoke node before any episode or triplet operation. Every workspace has its own explicit Graphiti client and physical graph `spike_<build>__<workspace>`; episode ingest omits `group_id` to prevent Graphiti's mutable internal driver clone. Multi-workspace queries fan out across these clients and merge results.

Do not use a full projection until `model_probe.py` and `project.py --limit 1` pass. Full projection requires the explicit `--confirm-full` guard.

Run `scripts/driver_probe.py` as the final N5/N6 gate. It creates exactly one `probe` workspace client over `spike_probe_final__probe`, calls `add_episode()` without `uuid` and `group_id`, checks generated episode UUID/read-back, name-based recovery, explicit-edge invalidation, stable driver identity, and zero pending index tasks.
