# Package 7 Review — Projector Postconditions

- **Commit under review:** `0e28eda` — `fix(brain): strengthen projector postconditions`
- **Baseline:** `e42bc43`
- **Scope:** invariant I11, invariant I12, §9 Projector safety, §10 rows 25–27, Package 7 diff only. Packages 1–6 not re-reviewed.
- **Reviewer:** Claude (Fable 5), 2026-07-17
- **Verdict:** **APPROVED FOR PACKAGE 8**

---

## 1. `_assert_projected` — verified

Read against `brain/projector/projector.py` at `0e28eda`:

- **Document hash:** the join fetches `d.projection_hash` and raises `accepted_record_document_hash_mismatch` on drift; `accepted_record_missing_document` if the row is absent. ✔
- **Embedding hash / fingerprint / dimension:** `LEFT JOIN retrieval_embeddings USING(record_id)` means a missing embedding surfaces as `NULL != projection_hash` → `accepted_record_missing_or_mismatched_embedding`; fingerprint and dimension are compared against config → `accepted_record_embedding_contract_mismatch`. ✔
- **Exact FTS row for the correct doc_id:** `doc_id` is taken from the just-fetched `retrieval_documents` row (not recomputed), then `retrieval_fts WHERE rowid=?` is fetched and `(title, body, artifacts_text)` compared tuple-exact against the projected document. Missing row → `accepted_record_missing_fts_row`; content drift → `accepted_record_fts_row_stale`. Since FTS5 `rowid` is unique, "exactly one matching row" holds by construction. ✔
- **Exact order-independent link set:** stored and desired links are compared as sets of full `(artifact_uri, relation, confidence, origin, verified_at)` tuples, so missing keys, extra/stale keys, **and value drift on non-key fields** (confidence/origin/verified_at) all raise `accepted_record_link_set_mismatch`. Float `confidence` round-trips exactly through SQLite REAL (IEEE-754 double both sides). ✔
- **Stale/missing/value-drifted rows fail the batch:** `_assert_projected` runs inside the `BEGIN IMMEDIATE` transaction, per record, before `set_cursor`; any raise propagates to `run_once`'s `except`, which rolls back and re-raises as `ProjectorError`. Verified by 4 mutation tests (deleted FTS row, deleted embedding, deleted link, injected stale extra link), each asserting the named error, unmoved cursor, clean retry, single cursor advance, then `NO_CHANGES`. ✔

Note: `_assert_projected` also runs on the `unchanged` (hash-noop) path, so a previously-corrupted FTS row or link set is caught even when the document itself is not rewritten — stronger than the minimum §9.2 requires.

## 2. `_assert_removed` — verified

- Independently re-queries all four tables (document by `record_id`, embedding by `record_id`, FTS by the `doc_id` captured **before** the delete, links by `record_id`) and raises a distinct `removed_record_*_still_present` error for each. It does not infer success from the DELETE statements not raising. ✔
- **No bypassing delete path:** the only statements that delete rows from any retrieval table in the whole codebase live in `projector.py` (checked via `git grep` at the commit; `sqlite-spike/scripts/fts_baseline.py` writes only during the separate, documented disposable-index build, and `cursor.py` touches only the cursor row). Within the projector: `_remove`'s four step-deletes are always followed by `_assert_removed` before returning; `_upsert`'s FTS delete-then-reinsert and link-diff deletes are always followed by `_assert_projected` on the same record. Both run before `set_cursor` in the same transaction. Deleting the whole retrieval DB file (rebuild) is the documented repair path and re-establishes all postconditions from scratch. ✔
- **Residual gap (F1, LOW, non-blocking):** the `already_absent` early return in `_remove` performs no deletes and asserts nothing. See §8.

## 3. Cursor safety (I11) — verified

- The pre-existing 5-point failure-injection test (`test_failure_points_roll_back_and_retry`: `after_batch_read`, `after_documents`, `after_embeddings`, `before_cursor_update`, `before_commit`) passes unchanged: every injected failure leaves `last_source_event_id` NULL/unchanged and a clean rerun succeeds.
- All 7 new Package 7 mutation tests follow the full cycle: injected corruption → named `ProjectorError` (or `IntegrityError` for the FK-backstopped case) → cursor equals its pre-run value → clean retry returns `HEALTHY` → cursor equals exactly the injected event's `source_event_id` (advanced exactly once) → next `run_once()` returns `NO_CHANGES`. ✔
- `set_cursor` is reached only after every record in the coalesced batch passed its postcondition; `con.rollback()` in the single `except` covers every raise inside the transaction. ✔

**I11: SATISFIED.**

## 4. Skip reasons — verified

- `_skip_reason` has three branches producing exactly the five `CLOSED_SKIP_REASONS` members (`FORBIDDEN = {candidate, rejected, forgotten}` → the three `status_*` values; `artifact_link` → `artifact_no_verified_active_relations`; residual → `not_eligible`), plus a membership assert. The pin test (`test_closed_skip_reasons_pin`) drives the full fixture and asserts observed reasons are a non-empty subset of the closed five. ✔
- **Unknown/unprojectable records never silently skip:** unknown event types, missing snapshots, and unresolved artifact validation each return `REBUILD_REQUIRED` with an unmoved cursor **before** the write transaction begins — re-read at the commit, unchanged by Package 7. ✔
- **Note (F2, LOW):** the guard is a bare `assert`, stripped under `python -O`. Closure still holds by construction, so this is defense-in-depth only; a `ProjectorError` would survive optimization.

## 5. `source_event_id` — verified

- Present on all three outcome shapes (projected / removed-via-skip / already_absent-via-skip) — the skip branch attaches it to the same dict that carries `action`. ✔
- Value is `journal.source_event_id(event)` for the record's coalescing event (`occurred_at + "\x1f" + event_id`); two updated exact-equality tests pin the literal value against the actual fixture event (`…\x1fevt-v2`, `…\x1fevt-audit-skip`), and `test_source_event_id_present_for_every_outcome` checks presence + shape across the whole fixture run. ✔
- No payload, title/body, or any other sensitive field was added to `record_outcomes`. ✔

## 6. Deterministic rebuild (I12) — verified

`test_deterministic_full_rebuild_with_live_written_records`:

- Appends a genuinely live-written record (`rec-p7-live`, direct journal insert with record/state/event rows) after the fixture projection, projects it incrementally, snapshots, deletes the retrieval DB, rebuilds with a fresh projector to `NO_CHANGES`, and asserts snapshot equality plus **journal byte-identity** (`sha256` before/after). ✔
- Snapshot covers per-record `projection_hash` map, document row set, embedding row set (hash/fingerprint/dimensions), full FTS content set, and full link tuple set — the incremental result and the rebuild result match. ✔
- **Limitation (F4, INFO):** the document row-set comparison covers `(record_id, projection_hash, workspace, type, status, is_current)`; `sensitivity`, `valid_at`, `invalid_at`, `confidence`, `source_event_id`, `supersedes`, `superseded_by` are not directly compared (title/body/artifacts_text are pinned via the FTS set and `canonical_text` via the hash). All excluded columns are deterministic functions of the snapshot, so this weakens the test, not the property. Worth widening in Package 8's consolidation.

**I12: SATISFIED** (with the test-strength note above).

## 7. FK/cascade special cases — verified legitimate

Schema facts confirmed at the commit: `retrieval_embeddings.record_id REFERENCES retrieval_documents(record_id)` (no cascade action, `sqlite-spike/schema.sql`); `retrieval_document_links … ON DELETE CASCADE` (projector `MIGRATION` DDL); `retrieval_fts` is FTS5, no true FK. `_write()` sets `PRAGMA foreign_keys=ON` on every connection before the transaction.

- **Embedding leftover as `IntegrityError`:** with the embedding delete skipped, `DELETE FROM retrieval_documents` itself raises `FOREIGN KEY constraint failed` (immediate FK) before `_assert_removed` runs. Testing this as an `IntegrityError` through the full `run_once()` rollback/retry cycle is the honest shape — the FK fires first and is the stronger guarantee. Correctly done in `test_mutation_embedding_left_after_remove_via_fk_bypass_blocks_document_delete`. ✔
- **Direct `_assert_removed` unit tests are not masking a normal-path hole:** through the normal transactional path with FKs on, a leftover **link** row after a genuine document delete is unconstructible (the cascade removes it even if `_delete_links` were skipped), and a leftover **embedding** never reaches `_assert_removed` (FK raises earlier). The two `PRAGMA foreign_keys=OFF` unit tests are therefore the *only* way to exercise those two assertion branches, and they model exactly the states the assertions exist for: out-of-band corruption or a future schema change relaxing the FKs. Meanwhile the document and FTS branches — the two with no FK backstop — **are** exercised through the full transactional path (`_SkipRemovalStep("document")`, `_SkipRemovalStep("fts")`). Correct division. ✔

## 8. Findings

| # | Severity | Finding |
|---|---|---|
| F1 | LOW (residual risk, non-blocking) | `_remove`'s `already_absent` early return skips `_assert_removed`. An orphan **embedding** in that state is still caught (as `orphan_embedding`) by `validate()` at the start of every `run_once`, but an orphan **FTS row** or orphan **link row** (constructible only via out-of-band/FK-off corruption — the same class the Package 7 unit tests defend against) is neither asserted on this path nor detected by `validate()`, whose checks cover only `orphan_embedding` / `document_without_embedding` / `forbidden_document` / `hash_mismatch`. An orphan FTS row can't leak through search (results join to `retrieval_documents`), but it persists silently. Suggested for Package 8/9: add orphan-FTS-rowid and orphan-link checks to `validate()`. Not an I11 violation — I11 binds records touched by the batch, and this record was not touched. |
| F2 | LOW | `_skip_reason`'s closed-enum guard is a bare `assert`, stripped under `python -O`. The enum stays closed by construction (branch analysis above), so purely defense-in-depth; consider `ProjectorError` instead. |
| F3 | INFO (doc) | §11 Package 7 bullet claims "21 new tests in `tests/test_brain_projector.py`"; the actual count is **14** new test methods (46 at `0e28eda` vs 32 at `e42bc43`), which matches the changelog's own "delta is 14 new Package 7 tests". Fix the "21" on the next doc touch. |
| F4 | INFO (test strength) | Rebuild-test row-set comparison omits several deterministic document columns (see §6); `test_source_event_id_present_for_every_outcome` would pass vacuously on an empty fixture (the fixture is non-empty, and the pin test's `assertTrue(seen)` guards the sibling). |

No finding blocks Package 8. F1 is the most serious and is explicitly a corruption-detection breadth issue outside the batch invariant, consistent with the package's own documented defense-in-depth framing.

## 9. Test evidence (run 2026-07-17, repo venv)

- `pytest tests/test_brain_projector.py -q` → **46 passed, 5 subtests passed** (includes all 14 new Package 7 tests).
- Existing failure-injection test `test_failure_points_roll_back_and_retry` (5 injection points) → **passed**.
- Full suite `pytest tests/ -q` → **291 passed, 21 subtests passed** — matches the changelog's claimed count exactly.
- `brain.schemas.check_exported()` → **True**.
- MCP tool-list parity (`tests/test_brain_mcp.py::test_exact_tool_list_and_search_schema_parity`, exact 7-tool pin + search-schema parity + forbidden-verb scan) → **passed** (full module: 6 passed).

## 10. Process confirmation

- Review performed read-only against commit `0e28eda` on `main`; working tree was clean before review.
- No source, test, or doc file other than this review document was created or modified.
- **No commit was created. No push was performed.**

## Verdict

**APPROVED FOR PACKAGE 8**
