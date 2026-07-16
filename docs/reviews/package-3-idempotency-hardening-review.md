# Package 3 review — idempotency hardening (commit 82b45e0)

Reviewed 2026-07-16 against `docs/architecture/write-safety-integrity-repair-spec.md`
(B8, I6, §6, §10 rows 16–19, §11 Package 3). Scope: commit `82b45e0` only.

## Verdict

**APPROVED FOR PACKAGE 4**

## B8 status

Closed. `_existing` (`brain/writer.py`) now raises `BRAIN_IDEMPOTENCY_CONFLICT` with
`details={"reason": "legacy_record_without_request_hash"}` whenever the matched
`record_created` event carries no `request_hash` (the `not stored_request_hash` guard
also covers an empty-string value). The raise happens before the idempotent-return
path is constructed, so `idempotent=true` is unreachable for legacy rows — including
for a byte-identical replay of the original request. `_existing` only SELECTs; the
stored event is never mutated and no backfill occurs. The raise propagates through
`record()`'s `except Exception: rollback` / `finally: close`, and no INSERT precedes
the `_existing` call, so a conflict can never leave partial rows.

## request_hash input set

`record()` now includes `record_type` and `workspace` explicitly in the
`request_hash` input (§6.3 item 2). Verified unchanged: `content_hash`,
`client_key`/stored-key derivation (`"m1:" + sha256({instance_id, agent_id, key})`),
agent/instance namespacing, and cross-agent duplicate behavior (existing pinning
test still passes). Comparison remains per-record against the same event's own
stored value, so no migration is needed and no existing row changes behavior.

## B8 probe re-run (independent, disposable fixture journal)

Created a record with explicit key `b8-probe-key`, blanked the stored event's
`request_hash` via direct SQL in a temp fixture journal, replayed with a different
`session_ref`:

- raised `BRAIN_IDEMPOTENCY_CONFLICT`, `details.reason == "legacy_record_without_request_hash"`
- row counts identical before/after across `memory_records`, `memory_events`,
  `record_state`, `artifact_links` (56, 56, 56, 1)
- stored event JSON byte-identical after the conflict
- an *identical* replay (same `session_ref`) also conflicts — never `idempotent=true`

No live journal was read or written.

## Tests

- Package 3 tests all present and passing: identical replay (zero new rows),
  metadata conflict matrix (17b: `session_ref`/`source_ref`/`valid_at`/`links`/
  `supersedes` varied independently, original event unchanged), legacy
  missing-request-hash conflict (17c), cross-workspace/type explicit-key conflict
  (17d), no-explicit-key cross-workspace independence (18), supersede replay exactly
  once (19). Rows 16/17a covered by pre-existing tests as the changelog states.
- `pytest tests/test_brain_write.py -k "idempoten or supersede"`: 9 passed.
- Full suite `pytest tests/ -q`: **174 passed + 5 subtests, 0 failures** — matches
  the changelog claim.
- `check_exported()`: green (no schema change).

Persistence invariants are asserted inside the new tests via `row_counts()` over all
four tables and byte-comparison of the original `record_created` event.

## Diff hygiene

Commit touches exactly `brain/writer.py`, `tests/test_brain_write.py`, and the spec
(status/matrix/changelog updates) — matching §11 Package 3's file list. No schema
change, no MCP tool-surface change (`mcp_server.py`, `models.py` untouched), no
journal data, no secrets, no user-specific absolute paths, no feature creep.

## Findings (non-blocking, documentation only)

1. **Changelog diff-scope claim is inaccurate.** The Package 3 changelog entry lists
   `docs/integrations/brain-mcp.md` in its diff scope, but the commit does not touch
   that file. Consequently the B8 remediation note required by the §5 blocker table
   ("affected agents must mint a fresh idempotency key — document in the MCP
   integration guide") is not yet documented there. Follow-up: docs-only change —
   add the note to `docs/integrations/brain-mcp.md` and correct the changelog line.
2. **§6.1 canonical fingerprint block is stale.** It still lists the `request_hash`
   inputs without `record_type`/`workspace`, while §6.3 item 2 and the code now
   include them. Follow-up: update the §6.1 block to match the implementation.

Neither finding affects runtime behavior or the Package 3 exit criteria (§6.2 matrix
fully green; B8 probe conflicts), so Package 4 is not blocked.

## Documentation follow-up

Both findings above are resolved in this repository's `docs(brain): align Package 3
idempotency contract` commit (docs-only, no production code or test changes):

1. `docs/integrations/brain-mcp.md` now documents the idempotency contract,
   including the legacy-`request_hash` conflict, the append-only/no-backfill
   guarantee, and the explicit-key/workspace/record-type/metadata rule. **CLOSED.**
2. `docs/architecture/write-safety-integrity-repair-spec.md` §6.1's canonical
   fingerprint block now lists `record_type` and `workspace` explicitly in
   `request_hash`, matching `brain/writer.py:120-121`. **CLOSED.**
