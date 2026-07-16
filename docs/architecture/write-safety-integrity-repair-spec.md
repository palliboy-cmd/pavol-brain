# Pavol-Brain Write Safety & Integrity Repair Specification

- **Status:** Proposed — analysis and specification only, no production code changed by this document.
- **Date:** 2026-07-15 (revision 2 — adds four runtime-probe-confirmed blockers missed by revision 1 and corrects claims the probes disproved)
- **Baseline:** commit `e92566a` on `main`; full test suite passing (124 tests + 5 subtests).
- **Audience:** the implementing agent (Sonnet). This document is the authority for security and integrity rules; the implementer must not invent, relax, or "improve" any rule stated here.
- **Scope:** the M1 write path (`brain/writer.py`, `brain/write_policy.py`, `brain/control.py`, `brain/control_center.py`, `brain/mcp_server.py`, `brain/api.py`, `brain/projector/*`, `scripts/bootstrap_brain_instances.py`) and the schema/tests that back it.
- **Out of scope:** Graphiti/graph projection, learning engine, consolidations, Obsidian sync, proactive notifications, `brain_context()`, candidate-review UI, any new user-facing feature. These remain M2/M3 backlog per [m2-roadmap.md](m2-roadmap.md). The SQLite journal stays the single source of truth; retrieval (and any future Markdown/graph output) stays a derived, rebuildable projection. The lifecycle `add → supersede → retract → confirm` and the chain `problem → analysis → decision → outcome` must remain expressible without another schema break.

## 0. How to read this document

Each factual claim is tagged:

- **[confirmed]** — reproduced by reading the current source/schema/tests cited next to it.
- **[probe]** — reproduced by *executing* the described scenario against a temporary journal built with `tests/journal_fixture.py` on 2026-07-15. Probe-tagged blockers are not hypotheses.
- **[confirmed, narrower]** — the underlying risk is real, but the mechanism differs from how it was originally described.
- **[not confirmed]** — checked against current code and not found; the code already satisfies the invariant. Kept so nobody "fixes" something that isn't broken.

---

## 1. Current-state assessment

### 1.1 Correctly implemented (leave alone)

| Capability | Evidence | Note |
|---|---|---|
| Append-only journal, single fold (`record_state`), supersede-as-new-row | `spike/schema/journal.sql`, `brain/writer.py:205-212` | `UPDATE record_state` only ever marks the *old* row `superseded`; the new row is always a fresh `INSERT`. No `UPDATE` ever touches `payload`/`content_hash`. |
| Transactional write: `BEGIN IMMEDIATE`, single commit, rollback on any error, connection closed in `finally` | `brain/writer.py:121-221` | [confirmed] |
| Policy bands A/B/C, deterministic, no LLM judgment in the hot path | `brain/write_policy.py` | `agent_inference` is always candidate; `verified_tool_result` outcomes need ≥1 server-verified artifact; accepted-decision statement dedup per workspace (`writer.py:129-136`). |
| Idempotency is agent- and instance-namespaced | `brain/writer.py:114-120`; tested by `test_idempotency_is_agent_namespaced_and_semantic_duplicates_are_candidates` | Stored key = `"m1:"+sha256({instance_id, agent_id, key})`; `request_hash` covers write metadata. **Contradicts the original blocker claim "fingerprint does not distinguish agents" — see §1.5.** One real gap remains at the edges: B8 in §1.4. |
| Cross-agent same-content writes demoted to Band B with `possible_duplicate_of` | `brain/writer.py:138-144` | [confirmed] |
| Supersede validation: target exists, same workspace + type, `accepted` only; `change_reason` required; candidates cannot supersede; replayed supersede is idempotent (the `_existing` return precedes the supersede branch) | `brain/writer.py:106-109,124-127,146-156` | [confirmed] |
| Cross-workspace **typed** record links rejected at write time | `brain/writer.py:158-165` (`BRAIN_CROSS_WORKSPACE_LINK_DENIED`) | Only for `metadata.links[]` — **not** for `record://` URIs in evidence/artifacts, see B3. |
| Sensitivity floor | `brain/writer.py:92-93`, `brain/mcp_server.py:55-57` | WORK instance / floor workspaces forced `sensitive`; client can raise, never lower. |
| Instance workspace binding at write time | `brain/writer.py:84-89` | `legacy` permanently read-only; `personal`/`work` reject foreign workspaces (`BRAIN_INSTANCE_DENIED`). File-binding side is B2. |
| MCP authorization: registry or env policy; write tools gated by `write_enabled`, tool grants, workspace grants, sensitive grants, launcher identity, instance binding | `brain/control.py` (`RegistryPolicy`), `brain/mcp_server.py`; tested end-to-end by `test_closed_memory_loop_two_profiles_and_two_instances` | `imported_curated` is deliberately absent from the MCP tool signatures — keep it that way. |
| Deterministic artifact verification for `repo://` and `git://` | `brain/artifact_verifier.py` (`git ls-files`, `git cat-file`) | Client cannot self-assert "verified"; only `verify_all()` results feed Band A (`write_policy.py:77-80`). |
| Read-side hygiene | `brain/api.py:76-85` | `get_record` hides candidate/rejected/forgotten and answers out-of-scope with `BRAIN_RECORD_NOT_FOUND` (indistinguishable from absence — preserve this property). |
| Projector per-record postcondition before cursor advance | `brain/projector/projector.py:130-143` (`_assert_projected`), called at `:190` before `set_cursor` at `:195`, same transaction | Document hash + embedding hash + embedding contract all asserted. **Contradicts the original blocker claim "projector lacks a postcondition" — see §1.5.** Two small extensions remain (§9.3). |
| Projector rebuild gating, crash safety, determinism | `brain/projector/validation.py`, `tests/test_brain_projector.py` (failure injection at 5 points, byte-identical journal, baseline-hash equality) | [confirmed] |
| Legacy→Personal/WORK journal split is all-or-nothing at the file level | `scripts/bootstrap_brain_instances.py` (`build` staging + rollback, `publish_pair` rollback, gates on integrity/FK/counts/cross-partition refs, snapshot-bound rec-056 exclusion) | Tested. Narrower than "bootstrap" as a lifecycle — B1; and the recovery path has a destructive window — also B1. |

### 1.2 Partially implemented

| Capability | Status | Evidence |
|---|---|---|
| Band C (secret/transcript) filter | Covers every client string **value and dict key** reachable from payload + metadata + provenance, at any nesting depth, including `idempotency_key`, `change_reason`, nested `verification` values *and keys*, and `alternatives[].evidence` (tested by `test_band_c_filters_all_persisted_client_text`, and by Package 4's dict-key/nesting tests). `request_id` is shape-constrained (not Band-C scanned) and rejected before any journal or audit write — B6/B7 closed in Package 4. | `brain/write_policy.py::collect_client_strings` (walks dict keys and values, list/tuple elements, recursively); `brain/write_policy.py::validate_request_id` |
| Cross-workspace leakage filtered at read time | `_scope_related` filters incoming links and `record://` artifact targets (tested by `test_search_filters_corrupt_cross_workspace_related_record_ids`), but **not** `supersedes`/`superseded_by` rows — see B4 [probe]. | `brain/api.py:94-103` |
| Idempotency conflict detection | `request_hash` compared only when the stored `record_created` event carries one; rows without it silently match — see B8 [probe]. | `brain/writer.py:67` |
| Bootstrap retry-safety | The split is atomic and the marker recovery handles the tested crash windows, but: rerun after full success is a hard error, and the crash window between `publish_pair` and the manifest write triggers **unverified deletion of both published journals** — see B1 [confirmed]. The full "stand up a working instance" lifecycle (journal + Control Center profile + projector LaunchAgent) is additionally not one retry-safe unit. | `scripts/bootstrap_brain_instances.py:270-282,296-302` |
| Artifact trust vocabulary | Internal 3-state fold (`unknown`/`verified_active`/`verified_inactive`) exists and only the server writes it, but it is not surfaced in responses, carries no digest/verifier metadata, and `evidence[]` URIs are never run through `verify_all` (always `origin="derived"`, `writer.py:191`) — see B9. | `brain/artifact_validation.py`, `brain/models.py` |

### 1.3 Declared in documentation only

| Item | Where declared | Actual code state |
|---|---|---|
| "Cross-instance references are impossible" | `brain-direction-reassessment.md`, ADR-001 | True for `links[]` and `supersedes`; **false** for `record://` URIs in `evidence`/`artifacts` (B3 [probe]) and unverified on the read side for supersede pointers (B4 [probe]). |
| "Instances are isolated by construction" | ditto | Isolation is launcher-env convention (`BRAIN_INSTANCE` + DB paths + hardcoded workspace sets). No file carries its own instance identity (B2). |
| Candidate review lifecycle events (`record_approved`/`record_rejected`/`record_forgotten`) | anticipated in `projector.py:29`, `journal_reader.py:91` | No write path emits them; correctly M2. Constrains §7/§9: adding retract/confirm later must need no schema break (already true: `record_state` columns and event vocabulary exist). |
| Graph tables `graph_edges`, `projection_map` | `spike/schema/journal.sql` | Unused Graphiti-spike remnants; not part of the write path; not touched by this spec. |

### 1.4 Active blockers (all verified against current code; [probe] items reproduced by execution)

**B1 — Bootstrap is not retry-safe at the edges, and recovery has a destructive window [confirmed].**
**Status: implemented in Package 1 (2026-07-15) — see the Changelog for evidence.** The description below is kept as an accurate record of the pre-Package-1 baseline this document was written against.
Three distinct defects in `scripts/bootstrap_brain_instances.py`:
1. *Rerun after success is an error, not an idempotent no-op.* After a completed run the marker is gone, so `recover_interrupted_publish` never fires, and `main()` hits `targets must be distinct and must not exist` (`:301-302`) — exit as failure instead of "already bootstrapped".
2. *Crash between `publish_pair` and the manifest write destroys published data on retry.* In that window the marker exists and the manifest does not yet say `published: true`, so the next run takes the `"cleaned"` branch (`:280-282`) and **deletes both published journals without verifying what they are**. Any record written into them between publish and retry (projector enabled, agent write) is destroyed. `test_retry_keeps_a_completed_pair_if_crash_happened_after_manifest` covers only the *post*-manifest crash.
3. *Recovery deletes by path, not by proof.* The marker records target paths but no digests; `recover_interrupted_publish` will delete whatever file sits at those paths (e.g. a restored backup), with no check that it is the file this bootstrap staged.
Above the script, the instance lifecycle is three uncoordinated artifacts: journal split → Control Center profile (`brain/control_center.py` `POST /integrations/add` → `ControlStore.save`, which never checks that a journal for `profile.brain_instance` exists) → projector LaunchAgent. A write-enabled profile can be created for an instance whose journal was never bootstrapped.

**B2 — Instance ↔ physical journal binding is convention, not an enforced invariant [confirmed].**
**Status: implemented in Package 1 (2026-07-15) — see the Changelog for evidence. The one-time backfill for pre-existing live journals (`scripts/stamp_brain_instance.py`) still needs to be run by the operator on the host where those journals actually live — this document's own implementation environment has none.** The description below is kept as an accurate record of the pre-Package-1 baseline this document was written against.
Nothing binds `BrainConfig.instance_id` to the file it opens. `instance_paths()` (`brain/config.py:6-14`), `scripts/install_brain_launchagent.sh`, and `scripts/run_brain_mcp_ssh.sh` are three independent derivations of "instance → file"; `BRAIN_JOURNAL_DB` (default `spike/spike.db`) bypasses all of them. `JournalWriter.connect()` checks only the schema shape. A mis-wired launcher applies `personal` workspace rules to the WORK file (or vice versa) with no error, silently destroying the split invariant. `grep instance spike/schema/*.sql` → no table carries an instance identity.

**B3 — `record://` URIs in `evidence`/`artifacts` bypass all link validation [probe].**
**Status: implemented in Package 2 (2026-07-16) — see the Changelog for evidence.** VARIANT A was chosen: the `record` scheme is banned from `URI_RE`, so these fields can no longer carry a `record://` value at all, resolvable or not; record-to-record relations remain expressible only through typed `metadata.links[]`, whose existing write-time validation is unchanged. The description below is kept as an accurate record of the pre-Package-2 baseline this document was written against.
`URI_RE` (`brain/write_policy.py:29`) accepts the `record://` scheme and `validate_evidence_uris` checks syntax only; `JournalWriter.record` then inserts every evidence/artifact URI into `artifact_links` (`writer.py:188-194`) without resolving `record://` targets. Reproduced: `evidence=["record://rec-does-not-exist"]` (dangling) and `evidence=["record://rec-001"]` (a foreign-workspace record) were both **accepted into an accepted Band-A record** and persisted as `artifact_links` rows. This defeats `BRAIN_CROSS_WORKSPACE_LINK_DENIED` and creates exactly the cross-workspace/cross-instance references the bootstrap audit exists to prevent.

**B4 — `supersedes`/`superseded_by` rows bypass the read-side scope filter [probe].**
`Repository.related()` (`brain/repository.py:54-57`) appends `{"relation": "supersedes"|"superseded_by", "record_id": …}` rows. `Brain._scope_related` (`api.py:94-103`) extracts a target only for `direction=="incoming"` rows and `record://` artifact URIs — supersede rows carry neither, so they pass unfiltered. Reproduced: with a (corrupt/legacy) cross-workspace supersede pointer, `get_related` scoped to `["personal"]` returned a `sap-work` record id. The write path prevents *new* cross-workspace supersedes, but the read side must not assume a clean journal — that is the whole point of defense in depth. The same rows flow into `search(include_artifacts=True)` responses.

**B5 — `Brain` read methods are fail-open by default [confirmed].**
`get_record`, `get_related`, and `search`'s use of `_scope_related` all treat `allowed_workspaces=None` (the default) as "no restriction". Today every MCP tool happens to pass scope from `policy.resolve_scope()` — one call site per tool, one layer up, no floor beneath it. Any future caller that omits the kwarg silently becomes a full cross-workspace/cross-sensitivity leak, and `tests/test_brain_contract.py` exercises the no-scope path as *accepted* behavior.

**B6 — Secret filter does not scan dict keys [probe].**
**Status: implemented in Package 4 (2026-07-16) — see the Changelog for evidence.** The description below is kept as an accurate record of the pre-Package-4 baseline this document was written against.
`enforce_band_c.collect()` recurses into dict *values* only (`write_policy.py:53-58`). `OutcomeRequest.verification: dict[str,str]` has client-controlled keys persisted into `payload` and `raw_input`. Reproduced: `verification={"api_key=sk-live-…": "ok"}` was **accepted** and persisted; the same secret in a value is rejected.

**B7 — `request_id` bypasses Band C and is persisted to the audit log [confirmed].**
**Status: implemented in Package 4 (2026-07-16) — see the Changelog for evidence.** The description below is kept as an accurate record of the pre-Package-4 baseline this document was written against.
A free-form client string on every tool (`mcp_server.py`), never scanned, not in the audit blocklist (`brain/audit.py:23`), written verbatim to the on-disk JSONL audit log by every operation (`api.py:128-136`). The one remaining client-controlled free-text path into local persistent storage outside the Band C gate.

**B8 — A stored `record_created` event without `request_hash` disables conflict detection [probe].**
**Status: implemented in Package 3 (2026-07-16) — see the Changelog for evidence.** The description below is kept as an accurate record of the pre-Package-3 baseline this document was written against.
`_existing` compares `request_hash` only `if created.get("request_hash")` (`writer.py:67`). Reproduced: after blanking the stored hash (simulating any pre-repair row), a replay with the same payload but different `session_ref` **silently returned the earlier success as `idempotent=True`** — the caller believes its new metadata was recorded. Violates "an explicit key with a different request must never silently return the earlier success."

**B9 — Artifact trust model has no persisted vocabulary or verifier metadata [confirmed, narrower than "too weak/syntactic"].**
Syntax is correctly *not* treated as proof (`classify()` requires a real `verify_all()` hit for Band A). The gaps: only 2 of 7 URI schemes are ever verifiable; `verified_active` is a point-in-time existence observation with no recorded digest/verifier metadata (TOCTOU: the file can vanish a minute later and the stored state implies more than was established); `evidence[]` URIs never run through `verify_all`; no response-visible `unverified_reference`/`verified` vocabulary exists for consumers.

**B10 — Missing adversarial/failure-recovery coverage [confirmed, precisely scoped].**
No test exists for: B1's destructive recovery window and rerun-after-success; B2 instance/file mismatch; B3 `record://` evidence smuggling; B4 supersede-pointer leak; B5 no-scope regression gate; B6 dict-key secrets; B7 `request_id` canary; B8 missing-request-hash replay; per-field secret matrix incl. byte-level absence proof.

### 1.5 Blockers claimed in the original brief but not confirmed in current code

Recorded so the historical claim is not silently dropped; implementers must **not** re-fix these.

- **"Idempotency fingerprint does not distinguish agents."** [not confirmed] — namespaced by `agent_id` + `instance_id` since `brain/writer.py:114-120`, fully tested. The real residual gaps are B8 and the explicit-key/workspace semantics defined in §6.
- **"Projector lacks a per-record postcondition before cursor commit."** [not confirmed] — `_assert_projected` is exactly this (commit `db948bb`), wired and tested (`test_v2_outcome_cannot_advance_cursor_without_projection`). §9 preserves and slightly extends it.
- **"`include_artifacts` can bypass scope boundaries."** [confirmed, narrower] — the flag itself only toggles expansion; the actual bypasses are B4 (supersede rows) and B5 (no scope floor), both fixed underneath the flag so that `include_artifacts` can never be the only thing standing between a caller and unauthorized data.

---

## 2. System invariants

Each invariant is written to be directly testable (DB rows, response payloads, file digests, or process exit codes).

| # | Invariant | Testable as |
|---|---|---|
| I1 | **Bootstrap atomicity.** After any single bootstrap invocation, either both instance journals exist, pass `integrity_check`/`foreign_key_check`/count reconciliation/reference audit and the manifest says `published: true` — or neither target exists and no staging file remains. No third state is observable. | Failure injection after every step (§10 rows 1–4). |
| I2 | **Bootstrap retry safety.** Re-running with identical inputs from *any* reachable state either completes the original operation, reports "already bootstrapped" with exit 0 and zero writes, or refuses with a distinct error and **zero deletions**. Recovery never deletes a file whose content it has not verified against digests it previously recorded. | §10 rows 5–8, incl. the post-publish-write survival test. |
| I3 | **Instance isolation.** A record with workspace `w` exists only in the journal that owns `w`; journal and retrieval DB each carry a persisted instance identity; every writer/reader/projector refuses on mismatch before any query. | Open instance A's file with `instance_id="B"`; assert refusal. |
| I4 | **Workspace and agent scope isolation.** Every `Brain` read method requires an explicit scope; no code path treats an omitted scope as "unrestricted". No MCP response contains a record id, payload fragment, artifact link, or provenance pointer whose workspace is outside the caller's grants, or sensitive without a sensitive grant — on every expansion path (search results, `include_artifacts`, related incoming/outgoing, supersede pointers). | §10 rows 9–15. |
| I5 | **Referential integrity.** Every persisted record-to-record reference — typed `links[]`, `supersedes`, and any `record://` URI in any write field — resolves at write time to an existing, same-workspace, non-rejected/forgotten record in the same journal. No write can create a dangling or cross-workspace `record://` reference. | §10 rows 9–10. |
| I6 | **Idempotency identity.** Identity is the function defined in §6 over `{instance_id, agent_id, workspace, record_type, canonical payload, explicit key, write metadata}`. Identical replay → original result with `idempotent: true`. Any divergence under the same stored key → loud `BRAIN_IDEMPOTENCY_CONFLICT`; never a silent earlier-success return — including for rows whose stored event lacks `request_hash`. | §6.2 matrix, §10 rows 16–19. |
| I7 | **Canonical write-envelope filtering.** Every client-controlled scalar that will be persisted anywhere (values **and dict keys**, URIs, reasons, refs) passes through `enforce_band_c` before any SQL runs; every field on every request model is classified in §7.1; unknown fields are rejected (`extra="forbid"` stays mandatory). | Field-classification test + §10 rows 20–22. |
| I8 | **Secret non-persistence.** After a rejected write, the secret bytes exist in no journal table (including `raw_input`), no retrieval table, no audit log line, and no error `details`. `request_id` is shape-constrained so it cannot carry free text into the audit log. | Canary + byte-grep of DB file, audit log, and error JSON (§10 rows 20–22). |
| I9 | **Artifact trust.** Stored artifact-relation state is exactly one of `unknown` (read as `unverified_reference`), `verified_active`, `verified_inactive`; only server-side code produces `verified_*`; a syntactically valid URI alone never does; downstream logic (bands, projector eligibility) never treats an unverified reference as evidence. | §10 rows 23–24 + schema test that no request model has a state-like field. |
| I10 | **Retrieval expansion never exceeds direct access.** For any object returned to identity X via any expansion, X could also obtain it via a direct, individually authorized call. | Property test: replay direct fetch for every transitively returned id (§10 row 13). |
| I11 | **Projector cursor safety.** The cursor advances only in a transaction in which every touched record passed its per-record postcondition (§9.2): projected records have a hash-consistent document + embedding (+ FTS row + exact link set); removed records are verifiably absent; skips carry a closed-enum reason. Crash ⇒ cursor unmoved, retrieval consistent with it. | Existing injection tests + §10 rows 25–26. |
| I12 | **Deterministic rebuildability.** Rebuild from an empty retrieval DB reproduces identical `projection_hash` values and row sets as the incremental path — including for live-written records, not only migrated ones. The journal is byte-identical before/after any projector run. | §10 row 27. |

---

## 3. Blocker matrix

| Blocker | Invariant | Root cause in code | Failure/attack scenario | Enforcement point | Recommended fix | Required tests | Recovery/migration impact |
|---|---|---|---|---|---|---|---|
| B1 bootstrap retry + destructive recovery | I1, I2 | `main()` refuses when targets exist (`:301-302`); `recover_interrupted_publish` deletes targets whenever marker exists and manifest lacks `published` (`:270-282`), digest-blind; `ControlStore.save` has no bootstrap-state check | (a) rerun after success fails; (b) crash after publish, before manifest → next run deletes two valid journals incl. any post-publish writes; (c) foreign file at target path deleted unverified; (d) write-enabled profile for a never-bootstrapped instance | `scripts/bootstrap_brain_instances.py`; `ControlStore.save` (`brain/control.py`) | §4 state machine: marker carries staged digests; recovery classifies state and only deletes digest-verified own output; completed state short-circuits to exit 0; `ControlStore.save` refuses `write_enabled=True` unless the instance journal exists and carries a valid identity marker | §10 rows 1–8, 28 | Marker/manifest format extended (additive); no journal data affected |
| B2 instance↔file binding | I3 | No in-file identity; three duplicated path derivations; `BRAIN_JOURNAL_DB` fallback | Mis-wired launcher writes `personal` records into the WORK file, silently | `JournalWriter.connect()`, `Repository._readonly`, `JournalReader.connect`, projector `validate()` | New one-row `brain_instance_identity(singleton CHECK(singleton=1), instance_id, created_at, source_digest)` table stamped by bootstrap into staged journals; all connect paths assert it against config (`BRAIN_INSTANCE_MISMATCH`); projector stamps/checks `retrieval_embedding_meta['instance_id']`; missing marker on `personal`/`work` ⇒ `BRAIN_INSTANCE_MARKER_MISSING`; `legacy`/spike files stay exempt | §10 rows 9 (marker mismatch), 15 | One-time, backup-first, digest-verified backfill for the two live instance journals (same pattern as `brain/migrations.py::migrate_m1`) |
| B3 `record://` evidence/artifacts | I5, I4 | `URI_RE` admits `record://`; `validate_evidence_uris` is syntax-only; `writer.py:188-194` inserts unresolved URIs into `artifact_links` | WORK agent writes `evidence=["record://<personal-id>"]` → cross-instance reference persisted; dangling refs corrupt the graph the bootstrap audit protects | `JournalWriter.record`, inside the transaction, next to the existing `links[]` validation | Every `record://` URI in `evidence`, `artifacts`, `commit`, `alternatives[].evidence` is resolved exactly like a typed link (exists, same workspace, not rejected/forgotten) or the write fails with the existing codes. Alternative (acceptable only if a read-only audit of both live journals shows zero existing `record://` evidence rows): drop `record` from `URI_RE` for these fields and require typed `links[]`. Record the choice in this file's changelog. | §10 rows 10a–10b | Read-only audit script (reuse `brain/record_references.journal_references`) over both live journals attached to the PR; findings handled by lifecycle events, never by rewriting history |
| B4 supersede rows bypass `_scope_related` | I4, I10 | `api.py:97` extracts a target only for `direction=="incoming"` or `record://` URIs | Corrupt/legacy cross-workspace supersede pointer leaks a foreign record id through `get_related` and `search include_artifacts` (reproduced) | `Brain._scope_related` — one filter for all expansion rows | Uniform rule: any row naming another record (incoming, `record://` target, relation ∈ {`supersedes`,`superseded_by`}) is resolved and dropped unless target workspace ∈ scope and sensitivity covered; unresolvable target ⇒ dropped. Apply the same rule to the `Provenance.supersedes/superseded_by` fields in search results (out-of-scope ⇒ `null`). | §10 rows 11–12 | None (read-side) |
| B5 `Brain` reads fail open | I4, I10 | `allowed_workspaces=None` default = unrestricted (`api.py:80,88`) | Any future call site omitting scope = full leak; single point of failure one layer up | `Brain.get_record`, `get_related`, `search` | Scope becomes a required keyword-only argument (or an explicit `UNRESTRICTED` sentinel reserved for local operator tooling, greppable and test-asserted to never appear in `mcp_server.py`); all MCP call sites already pass scope, so this is signature tightening | §10 row 13; update `test_brain_contract.py` no-scope call | None |
| B6 dict keys unscanned | I7, I8 | `collect()` walks `value.values()` only | Secret in a `verification` key persisted into `payload` + `raw_input` (reproduced) | `enforce_band_c` | `collect()` also collects every dict key recursively; additionally constrain `verification` keys via pydantic (`^[A-Za-z0-9 _./:-]{1,100}$`) so prose cannot live in keys at all (schema change → re-export schemas) | §10 rows 20–21 (key positions) | Read-only secret-pattern audit over existing `payload`/`raw_input` in both live journals; report-only |
| B7 `request_id` unscanned | I8 | Free string on every tool; audit blocklist omits it | Credential smuggled into the on-disk audit JSONL, read by Control Center views/log shipping | MCP boundary (`mcp_server.py`) + request models | Constrain to `^[A-Za-z0-9._:-]{1,128}$` in all request models / tool params; reject otherwise with `BRAIN_INVALID_REQUEST` before any journal work (schema change → re-export) | §10 row 22 | None |
| B8 missing `request_hash` ⇒ silent match | I6 | `writer.py:67` guard | Replay with same content, different metadata (supersedes/links/valid_at/provenance) silently "succeeds" without recording anything (reproduced) | `_existing` | If the stored created event lacks `request_hash`, raise `BRAIN_IDEMPOTENCY_CONFLICT` with `details={"reason":"legacy_record_without_request_hash"}`. Do **not** mutate `memory_events` (append-only); do not attempt fuzzy metadata reconstruction. | §10 row 17c | None; affected agents must mint a fresh idempotency key — document in the MCP integration guide |
| B9 artifact trust vocabulary | I9 | Fold is journal-internal; no digest/verifier metadata; `evidence` never verified | Consumers cannot distinguish "checked" from "nobody looked"; `verified_active` overstates what was established | `brain/writer.py` validation-event assembly; `brain/models.py` responses; `brain/artifact_verifier.py` | §8: run `evidence` URIs through `verify_all` alongside `artifacts`; record method/digest/verifier metadata in the validation event's `evidence` JSON; surface a read-only `artifact_trust` object in responses; document `unknown` ≡ `unverified_reference` | §10 rows 23–24 | Additive only (JSON column content + new response field; re-export schemas) |
| B10 test gaps | all | absence | regressions ship undetected | test suite | §10 in full, with T-numbers traceable to test names | — | None |

---

## 4. Bootstrap state machine

Binds the abstract lifecycle to `scripts/bootstrap_brain_instances.py`, the Control DB, and the projector installer. The unit of bootstrap is the **pair** of instance journals derived from one legacy-source snapshot. There are no seed records beyond the migrated partition (historical decisions are backfilled later through the normal write path). Retrieval DBs are not bootstrap outputs; the projector rebuilds them from an empty cursor after publish. Grants live in the Control DB and remain operator CRUD — bootstrap only gates them (step "workspace and grants" below).

### 4.1 States

```
NOT_BOOTSTRAPPED
   │ preflight (dry run, no --apply)
   ▼
PREFLIGHT_REVIEWED    manifest written, blocked=false, nothing published
   │ --apply
   ▼
STAGING               *.staging files only; instance identity marker stamped into each
   │ integrity/FK/count/reference/marker gates pass
   ▼
PUBLISH_PENDING       marker file written atomically; extended payload:
                      {targets, source_digest,
                       staged: {personal: {sha256, logical_digest},
                                work:     {sha256, logical_digest}}, started_at}
   │ os.replace × 2 (publish_pair)
   ▼
PUBLISHED             both files at final paths; manifest written with published:true
                      and result_journal_digests; marker removed
   │ operator: Control Center profile(s); ControlStore.save verifies journal + marker
   ▼
PROFILE_REGISTERED
   │ operator: projector LaunchAgent installed; first run_once() advances the cursor
   ▼
BOOTSTRAPPED          instance eligible for write-enabled profiles
```

Failure before PUBLISH_PENDING: staging files deleted, targets never touched (exists). Failure between PUBLISH_PENDING and the manifest write: resolved by the recovery classifier below — never by unconditional deletion.

### 4.2 Step definitions bound to existing code

| Step | What happens | Existing code | Gate before advancing |
|---|---|---|---|
| Preflight | snapshot source read-only, logical digest, reference audit, expected partition counts | `snapshot_source`, `inspect_snapshot`, `reference_audit`, `partition_counts` | `blocked` flag: overlap, unassigned, unknown workspaces, unapproved cross-partition refs, exclusion-manifest error, count mismatch, integrity/FK failure. **New:** requested partition must equal `PERSONAL_WORKSPACES`/`WORK_WORKSPACES` from `brain/control.py` (one source of truth for the partition, closing the CLI-vs-constants drift). |
| Staging | build both targets as `.staging` | `build()` | per-instance integrity, FK, zero cross-partition refs (exists) |
| Instance creation | **new:** stamp `brain_instance_identity` into each staged file (after `av.apply_migration`) | new, inside `build()` | marker row count == 1; `instance_id` matches the side being built |
| Workspace and grants | Control Center profile creation (separate DB, operator action) | `control_center.py` → `ControlStore.save` | **new gate:** `save` with `write_enabled=True` requires the instance journal to exist at `instance_paths(profile.brain_instance)` and carry a matching identity marker |
| Seed records | none in M1 | — | N/A |
| Link validation | `reference_audit` classifies every canonical reference | exists | zero blocking rows beyond the digest-bound rec-056 exclusion |
| Invariant validation | post-build re-check incl. source-unchanged digest | exists (`:379-391`) | any failure raises before `os.replace` |
| Publish/commit | marker → `publish_pair` → manifest → marker removal | exists (`:392-397`) | marker now carries staged digests (above) |
| Rollback/cleanup | staging removed on exception; marker removed on failed publish | exists | no un-suffixed file except a fully published pair |
| Retry after interruption | **replaced:** the classifier in §4.3 | `recover_interrupted_publish` rewritten | see table |
| Historical partial state | journal exists without an identity marker (predates this spec) | **new behavior** | `personal`/`work` runtimes refuse writes with `BRAIN_INSTANCE_MARKER_MISSING`; repair is the one-time audited backfill migration (B2), never auto-repair |

### 4.3 Recovery/state classification (normative)

Inputs: marker `M` (exists?, payload incl. staged digests), manifest `F` (exists?, `published`?, `result_journal_digests`), targets `P`,`W` (exist?, logical digests), current source digest.

| # | Observation | Classification | Action |
|---|---|---|---|
| 1 | no M, no P, no W | FRESH | full run |
| 2 | no M; F.published; P,W exist; digests == `F.result_journal_digests` | **ALREADY_BOOTSTRAPPED** | print manifest, exit 0, write nothing |
| 3 | no M; F.published; targets exist; digests differ | LIVE (post-bootstrap writes — normal life) | refuse: "instances already live; bootstrap is not a reset tool"; exit 3; delete nothing |
| 4 | no M; targets exist without a published manifest, or exactly one target exists | INCOMPATIBLE_EXISTING_STATE | refuse, exit 3, delete nothing, name the offending paths |
| 5 | M; F.published; targets match `F.result_journal_digests` | COMPLETED_CRASH_AFTER_MANIFEST | remove marker, exit 0 (exists today) |
| 6 | M; no F.published; targets exist; digests == `M.staged` | CRASH_AFTER_PUBLISH_BEFORE_MANIFEST | **complete forward:** write manifest from marker + re-derived report, remove marker, exit 0. Never delete. |
| 7 | M; no F.published; a file at a target path matches `M.staged` digest, or only staging remains | RECOVERABLE_PARTIAL | delete **only** digest-verified own output + staging + marker; proceed as FRESH |
| 8 | M; a file at a target path matches neither `M.staged` nor `F` | FOREIGN/CORRUPTED | refuse, exit 4, delete nothing, demand operator inspection |
| 9 | M.targets ≠ requested targets | INCOMPATIBLE RETRY | refuse, exit 3 (exists as RuntimeError; keep, with exit code) |
| 10 | any target fails `PRAGMA integrity_check`, or a marker/instance mismatch inside a target | CORRUPTED | refuse, exit 4 |

Task-term mapping: *successful bootstrap* = rows 2/5 evidence; *already completely bootstrapped* = row 2; *compatible retry* = rows 6–7; *incompatible existing state* = rows 3, 4, 9; *recoverable partial state* = rows 6 (forward) and 7 (backward); *corrupted state* = rows 8, 10.

---

## 5. Referential and scope integrity

### 5.1 Rule for every reference kind

Write-time enforcement lives in `JournalWriter.record`, inside the `BEGIN IMMEDIATE` transaction — the writer is the *integrity* gate; MCP `resolve_scope`/`authorize` is the *authorization* gate; both must hold independently.

| Reference kind | Where stored | Current enforcement | Gap | Required rule |
|---|---|---|---|---|
| Direct record link (`links[]`) | `artifact_links` (`record://<id>`) | write-time: exists, same workspace, not rejected/forgotten | none | preserve |
| Reverse link (incoming) | same table, queried by `artifact_uri='record://<id>'` | `_scope_related` filters by target workspace/sensitivity | only when scope is passed (B5) | scope mandatory (§3 B5) |
| `supersedes`/`superseded_by` | `record_state` | write-time: same workspace + type + accepted | read-side rows unfiltered (B4) | uniform target resolution in `_scope_related`; `Provenance` fields nulled when out of scope |
| `record://` in `evidence`/`artifacts`/`commit`/`alternatives[].evidence` | `artifact_links` | **syntax only** | **B3** | resolved like a typed link, or scheme banned from these fields (audit-gated choice) |
| Provenance strings (`source_ref`, `session_ref`, `source_excerpt`, `source_assertion`) | `memory_records` columns | Band-C scanned; never dereferenced | none | keep opaque: a `record://` inside `source_ref` is *not* an edge — document this in the MCP guide |
| Record URI format | everywhere | `URI_RE` | none | `record://<id>` must additionally match the journal's id shape; anything else in a record position ⇒ `BRAIN_INVALID_ARTIFACT_URI` |
| Non-record artifact URIs | `artifact_links`, validation tables | not workspace-scoped (not records) — correct | trust visibility (B9) | §8 |
| Bootstrap seed links | legacy payloads | digest-bound single-record exclusion mechanism | none | preserve as a closed, one-shot mechanism; do not generalize |
| Migration/import paths | any future script | `brain/migrations.py` is backup-first + digest-verified | none | any importer must reuse `brain/record_references.py` as the only reference parser and re-run the bootstrap audit gates before publishing |

### 5.2 The governing rule, made concrete

> No retrieval expansion may return an object that the calling integration identity could not also obtain via a direct, individually authorized query for that exact object.

After B4 + B5: `_scope_related` is the **single shared function** through which every traversal result passes, with mandatory scope inputs, and it resolves **every** row that names a record (incoming, `record://` target, supersede pointers) against the same workspace + sensitivity checks used by `get_record`. `include_artifacts=False` remains a way to ask for *less*; it is never the only barrier. Enforcement points, exhaustively: `Repository.candidates` SQL filter (workspace/sensitivity/status) → `policy.resolve_scope`/`authorize` (MCP) → `get_record` scope check → `_scope_related` for all expansion rows → `Provenance` field nulling. A property test (I10) replays a direct authorized fetch for every id returned transitively.

Library callers (`allowed_workspaces` sentinel) are a documented trusted mode for local operator tooling only; a test asserts `mcp_server.py` never uses it.

---

## 6. Idempotency contract

Formalizes what `brain/writer.py` already does, and pins the edge semantics. The identity algorithm itself is **kept** (no key-derivation change, no migration): workspace and operation type participate through `content_hash`, and divergence under a reused key is answered with a loud conflict rather than a silent fork.

### 6.1 Canonical fingerprint

```
content_hash     = sha256(canonical({type, workspace, payload}))          # semantic identity
client_key       = metadata.idempotency_key  OR  content_hash             # explicit or content-derived
stored_key       = "m1:" + sha256(canonical({instance_id, agent_id, key: client_key}))   # UNIQUE column
request_hash     = sha256(canonical({instance_id, agent_id, record_type, workspace, content_hash, sensitivity,
                                     source_assertion, source_excerpt, source_ref, session_ref,
                                     valid_at, supersedes, change_reason, links}))
```

- `instance_id`, `agent_id` — server-owned (config/launcher), never client-supplied. `agent_id` is bound to the integration by `RegistryPolicy`'s identity check, so integration identity is covered transitively; no separate integration field is added.
- `record_type`, `workspace` — carried explicitly in `request_hash` (in addition to being covered transitively through `content_hash`), so the stored event is independently auditable without re-deriving `content_hash`; a mismatch under the same `stored_key` is a **conflict**, not a fork (see matrix).
- `canonical()` = `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` over the **post-validation, normalized** pydantic dump — normalized content, not raw input. String contents are not whitespace-normalized (strings are content); field order and defaults are canonicalized.

### 6.2 Required behavior matrix (normative)

| Scenario | Required behavior | Status |
|---|---|---|
| Same agent, identical request replay | `idempotent=true`, original `record_id`/`event_id`, no new rows | ✅ implemented |
| Different agents, same content (any keys) | independent record; later one Band B `candidate` + `possible_duplicate_of`; never merged | ✅ implemented |
| Same agent, same explicit key, different payload | `BRAIN_IDEMPOTENCY_CONFLICT`, nothing persisted | ✅ implemented |
| Same agent, same explicit key, same payload, different metadata (`supersedes`/`links`/`valid_at`/provenance) | `BRAIN_IDEMPOTENCY_CONFLICT` (request_hash mismatch) | ✅ implemented |
| …when the stored event lacks `request_hash` | `BRAIN_IDEMPOTENCY_CONFLICT` with `details.reason="legacy_record_without_request_hash"` — **never** a silent idempotent return | ✅ implemented in Package 3 |
| Same agent, same explicit key, **different workspace or type** | `BRAIN_IDEMPOTENCY_CONFLICT` (content_hash mismatch under the same stored key). Rationale: an explicit key names one logical write; reusing it across workspaces/types is an agent bug and must fail loud. This is current behavior [probe] — now normative, pinning test added in Package 3. | ✅ implemented; pinned |
| Same content, different workspaces, **no explicit key** | independent records (content-derived keys differ), independently classified; duplicate demotion applies per-workspace only | ✅ implemented |
| Retry after network failure (response lost) | identical replay → idempotent return; the only silent-success path | ✅ implemented |
| Supersede replay | idempotent return of the original supersede result; target superseded exactly once (`_existing` precedes the supersede branch) | ✅ implemented; pinned in Package 3 |
| Retract/confirm | no write path yet (M2); schema already supports the events; this spec must not foreclose them | ✅ nothing to do |
| Normalized vs raw input | identity over normalized payload; `raw_input` is audit-only, never identity | ✅ implemented |

### 6.3 Required changes

1. B8 fix in `_existing` (strict conflict when the stored event has no `request_hash`). ✅ implemented in Package 3.
2. Add `record_type` and `workspace` to the `request_hash` input set (self-containedness of the stored event; no behavior change since `content_hash` already covers them — but the stored event becomes independently auditable). This changes hashes only for *future* events; comparison is per-record and never mixes eras, so no migration. ✅ implemented in Package 3 (`brain/writer.py:120-121`).
3. Pinning tests for the two previously untested rows above. ✅ implemented in Package 3.

---

## 7. Canonical write envelope and filtering

### 7.1 Field inventory (write paths: `record_outcome`, `record_decision` via MCP; `record_problem`, `record_analysis` library-only)

| Field | Classification | Current handling | Verdict |
|---|---|---|---|
| `record_id`, `event_id`, `created_at`, `confidence`, `content_hash`, `request_hash`, stored key, `raw_input`, `schema_version`, `record_state.*`, `artifact_links.origin/verified_at/confidence`, all validation-event fields | server-generated | writer only; `actor='server-artifact-validator'` on the write path | correct |
| `agent_id`, `instance_id`, integration binding, grants | trusted integration metadata | launcher env + control DB + `RegistryPolicy` identity check; never request fields | correct (file-binding side = B2) |
| `workspace` | agent-controlled, narrowed | `resolve_scope` (may only narrow) → writer instance check | correct |
| `sensitivity` | agent-controlled, raise-only | floor forced server-side | correct |
| payload scalars: `summary`, `statement`, `rationale`, `impact`, `reason`, `reopen_when`, `verdict`, `changes[]`, `findings[]`, `open_questions[]`, `alternatives[].{option,verdict,reason,reopen_when}` | agent-controlled | pydantic types/limits → Band C scan → persist | correct |
| `verification` dict **values** | agent-controlled | Band C scanned | correct |
| `verification` dict **keys** | agent-controlled | scanned by Band C (`collect_client_strings` walks keys); shape-constrained (`^[A-Za-z0-9 _./:-]{1,100}$`, `brain/models.py::VerificationKey`) | correct — B6 closed in Package 4 |
| `evidence[]`, `alternatives[].evidence[]` | agent-controlled | syntax-validated; never verified; `record://` unresolved | **B3 (resolution) + B9 (verify alongside artifacts)** |
| `artifacts[]`, `commit` | agent-controlled, security-sensitive (drive Band A) | syntax + `verify_all` server-side | correct except `record://` case (B3) |
| `source_assertion` | **security-sensitive control field** (selects the band) | `Literal` enum; `authoritative_document` requires `source_ref`; `imported_curated` absent from MCP signatures | correct — keep `imported_curated` MCP-unreachable |
| `source_excerpt` (≤500), `source_ref`, `session_ref` | agent-controlled | Band C scanned; never dereferenced | correct |
| `valid_at` | agent-controlled control field (becomes the superseded target's `invalid_at`) | tz-aware ISO-8601 → UTC; naive rejected | correct; document the control-field role |
| `idempotency_key` | agent-controlled control field | length 1–200, Band C scanned, hashed; raw key persisted only inside `raw_input` | correct |
| `supersedes`, `change_reason`, `links[]` | control fields | §5/§6 rules; `change_reason` scanned | correct |
| `request_id` | control-adjacent | shape-constrained (`^[A-Za-z0-9._:-]{1,128}$`, `brain/write_policy.py::validate_request_id`); rejected with `BRAIN_INVALID_REQUEST` before any policy call, journal write, or audit write; rejection always carries `request_id=""` and empty `details` | correct — B7 closed in Package 4 |
| external IDs | not present | — | if ever added, must be classified here first |

**Classification lock-in:** a checked-in list (mirroring the `brain/schemas.py::check_exported` pattern) enumerates every field of every request model with its bucket; a test fails when a model field is missing from the list. Adding an unclassified field becomes impossible silently.

### 7.2 Single enforcement point

`JournalWriter.record()` remains the one gate. Normative order (current order, made binding): type/workspace/instance/agent checks → URI syntax → **Band C scan over values and keys** (`enforce_band_c` stays the only scanner implementation) → server artifact verification → classification → identity computation → transaction (idempotency lookup, duplicate demotion, supersede/link/`record://` target resolution, inserts, validation events, commit). Nothing is inserted before the scan passes; error paths never copy scanned values into `details` (URIs failing *syntax* may be echoed — they were rejected precisely because they are inert; anything that passed syntax but failed the secret scan is reported only by error code).

Dispositions: **rejected** = unknown fields, bad enums, oversize strings, secret/deny-text hits (values or keys), invalid URIs, unresolvable `record://` targets, malformed `request_id`; **normalized** = timestamps to UTC, canonical JSON; **hashed** = the three §6 hashes; **redacted** = nothing (rejection instead of redaction — a partially cleaned record must not exist); **never stored** = secrets (rejected pre-storage), `request_id` (response/audit only, never journal); **server-generated only** = first row of §7.1.

---

## 8. Artifact trust model

Four distinct concepts; conflating them was the original defect:

| Concept | Definition | Current representation |
|---|---|---|
| Syntactically valid reference | matches `URI_RE` | gates acceptance, proves nothing — and must never produce a `verified_*` state by itself |
| Existing artifact | server observed existence at verification time | `verified_active` via `git ls-files`/`git cat-file`, `repo://`+`git://` only |
| Scope-authorized artifact | repo alias ∈ `config.artifact_repo_roots` | unknown aliases → `unknown` (`repo_unavailable`), never an error revealing paths |
| Server-verified artifact | deterministic server check with recorded evidence | the target state of this section |

**Temporary safe model (normative until a real content verifier exists):**

- Client-supplied URIs the server cannot deterministically check are persisted with state `unknown`, which downstream MUST read as **`unverified_reference`**. Document the equivalence in `docs/integrations/brain-mcp.md`.
- **No client-reachable field can set or influence any validation state** — true today (the only write-path validation author is `server-artifact-validator`); pin it with a schema test that no request model carries a state-like field, and keep `extra="forbid"` everywhere.
- Downstream logic never treats `unknown` as evidence. Both current consumers already comply — `classify()` (Band A needs `valid=True`) and the projector (`unknown` relations on `artifact_link` records ⇒ `REBUILD_REQUIRED`) — pin each with a test.
- `evidence[]` URIs go through the same `verify_all` as `artifacts[]` (they can only gain honesty: a verifiable `repo://` evidence URI becomes `verified_active`/`origin="deterministic"` instead of blanket `derived`; unverifiable ones stay `unknown`). This must not change band classification inputs (`classify` continues to look at `artifacts`+`commit` only).
- Each write-path validation event records, inside its existing `evidence` JSON column: `{"method": "git_ls_files"|"git_cat_file"|"not_deterministically_verifiable", "repo_alias": …, "object_digest": <git blob/commit sha when cheaply available>, "verifier": "server-artifact-validator", "verifier_instance": config.instance_id, "verified_at": <ISO-8601 UTC>}`. This is the minimum a future verifier needs to re-check or upgrade a claim without a schema migration; `digest` stays server-populated, never client input.
- Responses gain a read-only `artifact_trust` sub-object per artifact link (`state` — with `unknown` surfaced as `unverified_reference` — `method`, `verifier`, `verified_at`, `digest|null`); additive schema change, re-export schemas.
- Semantics of `verified_active` are documented as "existence verified by the server at `verified_at`" — an observation, not content endorsement, and not a durability promise (TOCTOU is inherent until a digest verifier exists; the recorded `object_digest` is what makes later drift detectable).

---

## 9. Projector safety

### 9.1 What already exists (preserve, do not rebuild)

Per-record postcondition before cursor commit is implemented and tested: for every projected record, `_assert_projected` (`projector.py:130-143`) verifies — inside the same `BEGIN IMMEDIATE` transaction that will move the cursor — that the retrieval document exists with the expected `projection_hash`, and the embedding exists with the same hash and the configured model fingerprint + dimension. Any failure raises, the whole transaction rolls back, `set_cursor` (`:195`) is never reached. Skips carry a closed-enum reason (`_skip_reason`) in `report.details.record_outcomes`; ineligible-but-present records are removed; unknown event types / missing snapshots / `unknown` artifact validation stop with `REBUILD_REQUIRED` and an unmoved cursor; `validate()` detects drift, cursor-ahead, orphans, docs-without-embeddings, forbidden statuses.

### 9.2 Per-record postcondition (normative; mostly existing)

Before the cursor may be set, for **every** record coalesced from the batch:

- **Projected:** document row with `projection_hash == sha256(canonical_text)`; embedding row with the same hash, configured fingerprint, configured dimension; **FTS row present for the doc_id** (extend `_assert_projected` — currently unasserted); **link rows exactly equal to the desired set** (extend — currently unasserted). Every projected record requires an embedding; there is no record type without one in this design.
- **Removed:** document, embedding, FTS row, and links all verifiably absent (new `_assert_removed` — `_remove` currently deletes without verifying).
- **Skipped:** reason from the closed enum `{status_candidate, status_rejected, status_forgotten, artifact_no_verified_active_relations, not_eligible}`; keep it closed, never free text.
- **Cursor:** written only after all of the above, same transaction (exists).

### 9.3 Failure, retry, rebuild, observability

- **Retry:** rollback leaves the cursor at its last value; the next `run_once()` re-reads the same batch (exists, tested at 5 injection points).
- **Unprojectable record:** blocks the whole batch (`REBUILD_REQUIRED`/`ProjectorError`), never skipped past. Remediation is a lifecycle event in the journal or a code fix — never a manual retrieval-DB edit. Preserve the "stall, don't guess" posture; add no auto-repair heuristics.
- **Rebuild from source journal:** delete the retrieval DB, run to `NO_CHANGES`; I12 requires identical hashes. Document as the only supported repair for `REBUILD_REQUIRED` in `docs/operations/brain-runtime.md`.
- **Instance binding (B2):** projector stamps `retrieval_embedding_meta['instance_id']` and `validate()` checks it against journal marker + config.
- **Observability:** `record_outcomes` already carries `{record_id, result, action/reason, projection_hash}` per record; extend each entry with `source_event_id`. Extend I12 coverage to live-written records (today's baseline-hash tests cover migrated fixtures plus a few write-path records).

---

## 10. Adversarial acceptance matrix

Every row is one test Sonnet implements (or maps to an existing test by exact name in a checked-in T-number → test-name table). ⛁ = assert file-level SHA-256 unchanged. Canary = a fixed fake secret matching `SECRET_PATTERNS` (e.g. `sk-live-` + 24 chars).

| # | Test | Setup | Action | Expected result | Expected persistent state | Expected audit/error behavior |
|---|---|---|---|---|---|---|
| 1 | failure injection per bootstrap step | fixture legacy source + rec-056 manifest; monkeypatch each of: snapshot, build×2, marker write, first `os.replace`, second `os.replace`, manifest write | run `--apply` | nonzero exit (pre-publish steps) or recoverable state (post-marker) | no targets unless both published; no staging leftovers; source ⛁ | error names the failed gate |
| 2 | rollback without partial published state | injected failure in the second build | run | error | neither target exists | exit ≠ 0 |
| 3 | staging validation gates | injected FK violation / count mismatch / `av.verify_state` mismatch (existing tests) | run | error | nothing published | — |
| 4 | never exactly one target | repeat 1–3 | inspect | — | both targets or neither, never one | — |
| 5 | retry after each failure type | after each row-1 crash | rerun identical args | classified per §4.3; completes or FRESH-rebuilds | valid published pair; digests reconciled | exit 0 |
| 6 | duplicate bootstrap after success | completed run (marker gone) | rerun identical args | ALREADY_BOOTSTRAPPED | targets ⛁ | exit 0, manifest reprinted |
| 7 | crash after publish, before manifest | simulate: `publish_pair` done, marker present, manifest without `published` | rerun | forward-completion (§4.3 row 6) | targets ⛁; manifest written; marker gone | exit 0 |
| 8 | post-publish writes survive recovery | row-7 setup + one record appended to the personal journal | rerun | forward-completion, **no deletion** | appended record still present | exit 0 |
| 8b | incompatible/foreign state | (a) delete one target after success; (b) place a foreign file at a target path with marker present | rerun | refusal with classification | nothing deleted ⛁ | exit 3/4, offending path named |
| 9 | instance-marker mismatch | stamped WORK journal | `JournalWriter` with `instance_id="personal"` pointed at it; same for `Repository`/projector | refusal before any query | journal ⛁ | `BRAIN_INSTANCE_MISMATCH` |
| 9b | missing marker | pre-spec `personal` journal without marker | write attempt | refusal | ⛁ | `BRAIN_INSTANCE_MARKER_MISSING` |
| 10a | cross-instance/workspace direct link | record in workspace A | write in workspace B with `links=[A-record]` | `BRAIN_CROSS_WORKSPACE_LINK_DENIED` (exists) | no rows | error audited |
| 10b | `record://` in evidence/artifacts/commit/alternatives | same | write with dangling and with foreign-workspace `record://` in each field | rejected per B3 | no rows | stable error code |
| 11 | cross-instance/workspace reverse link | corrupt `artifact_links` row targeting a foreign-workspace record (existing fixture pattern) | `get_related`; `search include_artifacts=True` | foreign id absent everywhere | — | — |
| 12 | cross-workspace provenance/supersede pointers | corrupt `record_state.supersedes`/`superseded_by` → foreign workspace | `get_related`; `search` | rows dropped; `Provenance` fields `null` | — | — |
| 13 | no-scope regression gate + I10 property | any fixture | call `get_record`/`get_related`/`search` scoping without scope; then, for a scoped search with expansion, replay direct fetch for every returned id | no-scope ⇒ `BrainError`; property: every expanded id individually fetchable | — | — |
| 14 | `include_artifacts` scope escalation | in-scope record linked (all four row kinds) to out-of-scope records | `search(include_artifacts=True)` | no out-of-scope id in any response field | — | — |
| 15 | invalid grant / workspace mismatch | profile bound to wrong instance; workspace outside instance set; sensitive without grant | tool calls + `ControlStore.save` | `BRAIN_INSTANCE_DENIED` / `BRAIN_WORKSPACE_DENIED` / `BRAIN_SENSITIVE_SCOPE_DENIED` / `ValueError` at save | nothing persisted | policy denial audited |
| 16 | idempotency collision between agents | agents A, B; same content, same explicit key | both write | B gets distinct record, `candidate`, `possible_duplicate_of=A's` | two records | — |
| 17a | explicit-key payload conflict | A writes key K | replay K + different payload | `BRAIN_IDEMPOTENCY_CONFLICT` | one record | conflict audited |
| 17b | explicit-key metadata conflict | A writes key K | replay K + same payload, different `links`/`supersedes`/`valid_at`/provenance | `BRAIN_IDEMPOTENCY_CONFLICT` | one record | — |
| 17c | legacy row without `request_hash` | blank the stored event's `request_hash` | replay K + different metadata | `BRAIN_IDEMPOTENCY_CONFLICT` with `reason=legacy_record_without_request_hash` | one record | — |
| 17d | explicit key across workspace/type | A writes key K in workspace X | replay K in workspace Y (and as a different record type) | `BRAIN_IDEMPOTENCY_CONFLICT` (pinned semantics §6.2) | one record | — |
| 18 | same payload, different workspaces, no explicit key | two granted workspaces | same content to both | two independent records, independently classified | two rows | — |
| 19 | supersede replay | successful supersede | replay identical supersede request | idempotent return; target superseded exactly once | one supersede event | — |
| 20 | secret in every write field | parametrize: `summary`, `changes[]`, `verification` **key**, `verification` value, `open_questions[]`, `statement`, `rationale`, `alternatives[].reason`, `alternatives[].evidence[]`, `evidence[]`, `artifacts[]`, `commit`, `source_excerpt`, `source_ref`, `session_ref`, `change_reason`, `idempotency_key`, `request_id` | write with canary in that field | rejected (`BRAIN_WRITE_SECRET_REJECTED` or the field's own code) | canary bytes absent from the journal **file** (byte grep) | canary absent from audit log bytes and from returned error JSON |
| 21 | secret in nested and list values | canary two levels deep in dict values, inside list elements, in a dict key nested inside a list | write | rejected | as row 20 | as row 20 |
| 22 | `request_id` shape | overlong / whitespace / canary-bearing `request_id` | any tool call | `BRAIN_INVALID_REQUEST` before journal work | no rows | canary absent from audit log |
| 23 | falsely verified artifact | (a) request tries to pass a verification/state field; (b) direct DB insert of a forged `verified_active` state row diverging from events | (a) write; (b) `av.verify_state` | (a) rejected as unknown field; (b) mismatch reported, `rebuild_state` repairs | fold reproducible | verifier names the link id |
| 24 | artifact scope escalation / unverifiable schemes | `artifacts=["doc://…"]` or unknown repo alias, with `verified_tool_result` | `record_outcome` | Band B candidate; state `unknown` (`unverified_reference`) | no `verified_*` persisted | — |
| 25 | projector postcondition failure | silent-skip subclass (exists) **plus** mutations: delete the FTS row / a link row / the embedding before the assert | `run_once()` | raise; cursor unmoved | retrieval consistent with old cursor | error names the record |
| 26 | cursor retry | after row 25 | rerun clean | HEALTHY; cursor advances exactly once past the failed batch | postconditions hold | `record_outcomes` complete, incl. `source_event_id` |
| 27 | deterministic full rebuild | a journal that lived through this whole suite (live-written records included) | delete retrieval DB; rebuild to `NO_CHANGES` | hashes + row sets equal the incremental result | journal ⛁ | — |
| 28 | write-grant before bootstrap | Control DB only, no instance journal | `ControlStore.save` with `write_enabled=True` | refused | no profile persisted | error names the missing journal/marker |

---

## 11. Sonnet implementation sequence

Order follows the mandated sequence. Every package: full suite green; exported JSON schemas unchanged unless the package says otherwise (then regenerate via `brain/schemas.py::export()` and include the diff in review); touch nothing outside the listed files plus tests.

### Package 1 — Bootstrap atomicity, recovery, and instance-marker binding (closes B1 + B2)
- **Files:** `scripts/bootstrap_brain_instances.py`; `spike/schema/journal.sql` (+`brain_instance_identity`); `brain/writer.py`, `brain/repository.py`, `brain/projector/journal_reader.py`, `brain/projector/projector.py`+`validation.py` (marker checks/stamp); `brain/control.py` (`ControlStore.save` write-grant gate); new `scripts/stamp_brain_instance.py` (backup-first, digest-verified one-time backfill, `brain/migrations.py` pattern); `tests/test_brain_instance_bootstrap.py`.
- **Goal:** §4 state machine incl. digest-carrying marker and the §4.3 classifier; no connect path operates on a mismatched or unmarked instance file; write grants require a marked journal.
- **Tests:** §10 rows 1–9b, 15 (save-gate part), 28.
- **Migration:** run the stamp script once per live instance (operator step; document in `docs/operations/brain-runtime.md`).
- **Exit criteria:** rerun-after-success exits 0 idempotently; row-8 proves post-publish writes survive recovery; cross-wired writer/reader/projector all refuse; live-state fixture classifies as row 2/3, never as an error to "clean up".

### Package 2 — Cross-instance referential integrity (closes B3)
- **Files:** `brain/writer.py`, `brain/write_policy.py`, small read-only audit script reusing `brain/record_references.py`; `tests/test_brain_write.py`.
- **Goal:** `record://` in `evidence`/`artifacts`/`commit`/`alternatives[].evidence` resolved like typed links (or scheme banned there if the live-journal audit shows zero existing rows — record the decision in the changelog).
- **Tests:** §10 rows 10a–10b.
- **Migration:** audit report over both live journals attached to the PR; findings handled by lifecycle events only.
- **Exit criteria:** the §1.4 B3 probes are rejected.

### Package 3 — Idempotency hardening (closes B8; pins §6)
- **Files:** `brain/writer.py` (`_existing`, `request_hash` input set); `tests/test_brain_write.py`.
- **Goal:** strict conflict for legacy rows; `record_type`+`workspace` added to `request_hash`; pinning tests for cross-workspace/type explicit keys and supersede replay.
- **Tests:** §10 rows 16–19 incl. 17c/17d.
- **Exit criteria:** §6.2 matrix fully green; the B8 probe now conflicts.

### Package 4 — Canonical write-envelope filtering (closes B6 + B7; locks §7)
- **Files:** `brain/write_policy.py` (`collect` walks keys); `brain/models.py` (`verification` key pattern, `request_id` constraint — schema change, re-export); `brain/mcp_server.py`/`brain/api.py` (reject bad `request_id` early); field-classification list + test; `tests/test_brain_write.py`.
- **Tests:** §10 rows 20–22 + classification lock-in test.
- **Migration:** report-only secret audit over live journals.
- **Exit criteria:** dict-key probe rejected; byte-grep assertions pass; adding an unclassified model field fails a test.

### Package 5 — Scope-safe retrieval expansion (closes B4 + B5; depends on nothing above)
- **Files:** `brain/api.py` (`_scope_related` uniform target resolution, mandatory scope signatures, `Provenance` nulling); `tests/test_brain_contract.py` (update the intentional no-scope call), new `tests/test_brain_scope_integrity.py`.
- **Tests:** §10 rows 11–14 incl. the I10 property test.
- **Exit criteria:** the B4 probe returns nothing out of scope; no-scope calls raise; `mcp_server.py` asserted never to use the trusted sentinel.

### Package 6 — Artifact trust semantics (closes B9)
- **Files:** `brain/artifact_verifier.py` (evidence metadata), `brain/writer.py` (verify `evidence[]`, pass metadata into validation events), `brain/models.py` (+`artifact_trust` response object — schema change, re-export), `docs/integrations/brain-mcp.md`; `tests/test_brain_write.py`, `tests/test_artifact_validation.py`.
- **Constraint:** must not change which records classify Band A today (`classify` inputs unchanged); before/after diff of `artifact_validation_state` over the fixture set proves it.
- **Tests:** §10 rows 23–24; evidence-URI verification test.
- **Exit criteria:** `check_exported()` green with the new field; a verifiable `repo://` evidence URI surfaces `verified_active`; unknown schemes surface `unverified_reference`.

### Package 7 — Projector postconditions (extends §9)
- **Files:** `brain/projector/projector.py` (`_assert_projected` +FTS+links, new `_assert_removed`, `record_outcomes.source_event_id`); `tests/test_brain_projector.py`.
- **Tests:** §10 rows 25–26; retrieval instance-stamp mismatch (with Package 1).
- **Exit criteria:** mutation tests (deleted FTS/link/embedding row) fail the batch with an unmoved cursor.

### Package 8 — Adversarial and recovery suite consolidation
- **Files:** tests only; a checked-in T-number → test-name map (`tests/ACCEPTANCE_MATRIX.md` or module docstrings).
- **Goal:** every §10 row implemented or mapped to an existing test by exact name; row 27 rebuild-equivalence over the post-suite journal.
- **Exit criteria:** all 28 rows traceable and green; each *new* invariant test demonstrated red at least once before its fix landed (capture in the PR).

### Package 9 — Documentation and migrations
- **Files:** this document (changelog: blockers → closing commits), `docs/operations/brain-runtime.md` (rebuild-as-repair, stamp script, bootstrap exit codes and state table), `docs/integrations/brain-mcp.md` (envelope table, idempotency matrix, `unverified_reference`, `request_id` shape).
- **Exit criteria:** zero drift between documented and merged behavior; every §3 blocker has a closing commit reference or an explicit dated deferral note in `m2-roadmap.md`.

---

## 12. Final Fable acceptance checklist

Fable verifies **evidence, not assertions** — commands it runs itself on the reviewed checkout. Verdict is exactly one of `READY FOR CONTROLLED WRITE ROLLOUT` · `READY WITH EXPLICIT LIMITATIONS` · `NOT READY`.

| # | Check | Required evidence |
|---|---|---|
| 1 | Full suite green, count strictly above the 124-test baseline, zero skips in the new adversarial modules | captured `pytest -q` output |
| 2 | §10 traceability: all 28 rows resolve to real tests; spot-execute at least rows 7, 8, 10b, 12, 17c, 20, 25 individually with `-k` | per-row test names + outputs |
| 3 | **Probe re-execution:** rerun the four §1.4 probes (dangling/cross-workspace `record://` evidence; dict-key secret; missing-`request_hash` replay; corrupt supersede-pointer leak) against a temp journal | all four rejected/filtered — the regression proof that the original blockers are closed |
| 4 | Byte-level secret proof | one manual canary write; `grep` the journal file, retrieval file, and audit log bytes; zero hits |
| 5 | Bootstrap evidence | execute on fixtures: full run → rerun (exit 0) → crash-after-publish sim → rerun (forward-completed) → post-publish-write survival (row 8) verified by query |
| 6 | Instance markers on real files | `SELECT * FROM brain_instance_identity` against all live journals matches their directories; a deliberately cross-wired `BrainConfig` write fails with `BRAIN_INSTANCE_MISMATCH` |
| 7 | Determinism | delete a fixture retrieval DB, rebuild, diff `record_id → projection_hash` sets vs incremental (row 27); journal byte-compare |
| 8 | Schema discipline | `check_exported()` true; exported-schema diff vs baseline reviewed and limited to §7/§8 changes |
| 9 | No scope creep | `git diff --stat` vs baseline limited to §11 files + tests/docs; MCP tool list unchanged; no Graphiti/learning/sync/notification code; artifact Band A classification unchanged on the fixture set (Package 6 constraint) |
| 10 | Live-journal audits | Package 2 reference audit + Package 4 secret audit reports over both live journals attached; zero findings or operator-acknowledged findings |
| 11 | Lifecycle compatibility | confirm no migration in this work set forecloses `retract`/`confirm` (schema unchanged where they will land) |
| 12 | Red-test evidence | at least one demonstrated failing run per new invariant test, from before its fix (Package 8 exit criterion) |

**Verdict rules:** all 12 with zero unacknowledged findings → `READY FOR CONTROLLED WRITE ROLLOUT`. Items 1–8 pass but 9–12 carry documented, bounded exceptions (each naming its follow-up and residual risk) → `READY WITH EXPLICIT LIMITATIONS`. Any failure in items 1–8, or scope creep in item 9 → `NOT READY`.

---

## Appendix A — probe protocol (for §12 item 3)

Each probe runs against a temporary journal built by `tests/journal_fixture.py` with `instance_id="personal"`, `client_identity="probe"`, a no-op embedding transport, and default config otherwise:

1. **record:// evidence:** `record_problem(evidence=["record://rec-does-not-exist"], …)` and `record_problem(evidence=["record://rec-001"], workspace="personal", …)` (rec-001 lives in `ai-pos`). Baseline result: both **accepted**, rows in `artifact_links`. Required post-fix: both rejected.
2. **dict-key secret:** `record_outcome(verification={"api_key=sk-live-<24 chars>": "ok"}, …)`. Baseline: **accepted**. Required: `BRAIN_WRITE_SECRET_REJECTED`.
3. **missing request_hash:** write with explicit key; blank `request_hash` inside the stored `record_created` event data; replay with a different `session_ref`. Baseline: silent `idempotent=True`. Required: `BRAIN_IDEMPOTENCY_CONFLICT`.
4. **supersede-pointer leak:** set `record_state.supersedes='rec-001'` on a `personal` record; `get_related(record_id, allowed_workspaces=["personal"])`. Baseline: returns `{"relation":"supersedes","record_id":"rec-001"}`. Required: row absent.

## Appendix B — validation performed before finalizing this document

- `git status` in `pavol-brain` confirmed a clean tracked tree; this document is the only file added/changed by this task (it is untracked; no commit or push was made).
- Full test suite executed at baseline: 124 passed, 5 subtests passed.
- All four Appendix A probes executed against temporary journals; results as stated.
- Every `file:line` citation re-checked against `e92566a`.

### Changelog

- 2026-07-16 — **Package 4 implemented (closes B6 + B7; locks §7).** Two probe-confirmed gaps in the canonical write envelope:
  - **B6 (dict keys unscanned):** `enforce_band_c`'s `collect()` walked dict *values* only. Fix: `brain/write_policy.py::collect_client_strings` is now the single recursive walk (dict keys **and** values, list/tuple elements, at any nesting depth) and is the only implementation `enforce_band_c` calls — no parallel scanner was added. `OutcomeRequest.verification` keys additionally gained a pydantic shape constraint (`brain/models.py::VerificationKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9 _./:-]{1,100}$")]`, min/max length 1–100 via the pattern itself): both gates apply independently — a key can have a valid shape and still be secret-shaped (caught by the Band C dict-key scan), or an invalid shape regardless of content (caught by pydantic). Schema change: `brain/schemas/v1/OutcomeRequest.json` regenerated (`verification`'s `additionalProperties` became `patternProperties`); no other exported schema changed.
  - **B7 (`request_id` bypasses Band C and the audit blocklist):** `brain/write_policy.py::validate_request_id` constrains every `request_id` to `^[A-Za-z0-9._:-]{1,128}$` and raises `BRAIN_INVALID_REQUEST` with `request_id=""` and empty `details` — never the client's invalid value. This check runs at the top of every MCP tool (`brain_search`, `brain_get_record`, `brain_get_related`, `brain_record_outcome`, `brain_record_decision` in `brain/mcp_server.py`) **before** any `policy.resolve_scope`/`policy.authorize` call, and again at the top of every `Brain` method that accepts `request_id` (`brain/api.py::_request`, `get_record`, `get_related`, `_record`) for library-only callers (`record_problem`, `record_analysis`) and defense in depth. The MCP-boundary placement matters specifically because `RegistryPolicy._deny` (`brain/control.py`) writes a `policy_denial` audit line carrying `request_id` on any authorization failure — a workspace-denial that fires before `Brain.search`/`Brain.record_*` is ever called would otherwise still leak an invalid `request_id` into the audit log even though those methods are themselves guarded (`tests/test_brain_mcp.py::test_b7_probe_rerun_invalid_request_id_rejected_before_policy_denial_audit` demonstrates this exact path). `request_id` is deliberately shape-constrained only, not Band-C content-scanned (per §7.1): it is a control-adjacent correlation token, not free-form content, and the tight charset is the accepted mitigation the spec calls for — a compact alnum/dash/dot/colon token that happens to resemble a real credential format is a known, accepted residual (not a gap this package closes; scanning `request_id` for secret *content* would be new behavior beyond B7's stated fix).
  - **Fixed as a direct consequence of the above (not a new blocker):** pydantic's default `ValidationError` rendering embeds the raw offending value (for dict-key errors, even in the error's `loc` tuple, not just `input`) — adding the `verification`-key shape constraint means a secret-shaped, invalid-shape key would otherwise leak through `brain/api.py::_record`'s existing `{"validation": str(exc)}` error-details construction. Fixed by gating those details through `looks_like_secret` (renamed from `_looks_like_secret`, now a public `brain/write_policy.py` export reused here, not a new scanner): if the rendered validation-error text matches a secret pattern, `details` is emptied instead of populated. Also added `raise error from None` at that same site so the original (secret-bearing) `ValidationError` is not chained onto the returned `BrainError` via `__context__`. Considered and rejected: mirroring the `pattern=` constraint onto the raw MCP tool parameter type hints (`verification`, `request_id`) in `brain/mcp_server.py` — `mcp.server.fastmcp` validates tool arguments against the function signature *before* the tool body runs, and wraps a failing pydantic `ValidationError` into a `ToolError(f"...{e}")` whose message embeds the raw value; that path is outside this codebase's control and outside the audit-safe error construction above, so the tool signatures are left exactly as they were (untyped `dict[str,str]`/`str | None`) and all enforcement happens inside the function bodies, which this package fully controls.
  - **Field-classification lock-in (§7.1):** new `brain/write_envelope.py` — `FIELD_CLASSIFICATION` maps every field of every request model (`SearchRequest`, `OutcomeRequest`, `DecisionRequest`, `ProblemRequest`, `AnalysisRequest`, `DecisionAlternative`, `RecordLink`) to one of `server_generated` / `trusted_integration_metadata` / `user_controlled_content` / `security_sensitive_control`; `OUT_OF_BAND_FIELDS` covers `request_id` (deliberately not a pydantic field on any write-path model, per B7). `tests/test_brain_write.py::test_write_envelope_field_classification_lock_in` asserts `set(model.model_fields) == set(FIELD_CLASSIFICATION[name])` for every model — adding a field to any request model without classifying it here fails this test immediately.
  - B6 probe re-run: `verification={"api_key=sk-live-<canary>": "ok"}` (the literal baseline probe, key containing `=`) is now rejected with `BRAIN_INVALID_REQUEST` (shape gate fires first) and the canary is absent from the returned error's `str()`/`details` despite pydantic's default value-echoing behavior; a shape-safe variant (`verification={"sk-live-<canary>": "ok"}`, no `=`) is separately rejected with `BRAIN_WRITE_SECRET_REJECTED`, proving the dict-key Band C scan itself (not just the shape gate) closes the gap. Zero rows change in either case.
  - B7 probe re-run: a `request_id` containing the same canary is rejected with `BRAIN_INVALID_REQUEST` before any journal work, `request_id=""` in the response, and the canary is absent from the audit log bytes — both at the `Brain` level (`tests/test_brain_write.py::test_b7_probe_rerun_request_id_canary_is_rejected_before_any_write`) and at the MCP boundary through a `RegistryPolicy`-backed server where the same call would otherwise also trigger a `BRAIN_WORKSPACE_DENIED` policy-denial audit write (`tests/test_brain_mcp.py::test_b7_probe_rerun_invalid_request_id_rejected_before_policy_denial_audit`).
  - Secret non-persistence matrix (§10 rows 20–21): parametrized canary injection across `summary`, `changes[]`, `verification` key and value, `open_questions[]`, `statement`, `rationale`, `alternatives[].reason`, `alternatives[].evidence[]`, `evidence[]`, `artifacts[]`, `commit`, `source_excerpt`, `source_ref`, `session_ref`, `change_reason`, `idempotency_key`, `request_id` — every case rejected, zero persistent-row deltas, canary bytes absent from the journal DB file, the audit log file, and every returned error (`tests/test_brain_write.py::test_secret_non_persistence_matrix`). Nested-position coverage (a dict nested inside a list, and a dict used as a mapping — its own keys — nested inside a list) is proven directly against the canonical scanner (`tests/test_brain_write.py::test_collect_client_strings_walks_keys_values_and_nesting`, `test_b6_dict_key_and_nested_secrets_are_rejected_by_band_c`), since no current request-model field accepts free-form nesting deep enough to reach those shapes end-to-end through the public write API.
  - Full suite: 184 passed + 5 subtests (up from the pre-Package-4 174 + 5), zero regressions. `check_exported()` green (only `OutcomeRequest.json` changed, as expected). No live journal or retrieval DB read or written; no push.
  - Diff scope: `brain/write_policy.py`, `brain/models.py`, `brain/api.py`, `brain/mcp_server.py`, new `brain/write_envelope.py`, `brain/schemas/v1/OutcomeRequest.json`, `tests/test_brain_write.py`, `tests/test_brain_mcp.py`, this changelog entry, `docs/integrations/brain-mcp.md`. Nothing else.
- 2026-07-16 — **Package 3 implemented (closes B8; §6.2 matrix fully green).** `_existing` (`brain/writer.py`) previously returned a silent `idempotent=true` whenever a stored `record_created` event carried no `request_hash` at all — any pre-repair or corrupted row disabled conflict detection outright, so a replay with the same explicit key but different metadata (`session_ref`/`source_ref`/`valid_at`/`links`/`supersedes`) would silently report the earlier success instead of the caller's new request. Fix:
  - `brain/writer.py::_existing`: a missing stored `request_hash` on the matched `record_created` event now always raises `BRAIN_IDEMPOTENCY_CONFLICT` with `details={"reason": "legacy_record_without_request_hash"}` — never a silent idempotent return, regardless of whether the replayed metadata happens to be identical (no fuzzy reconstruction attempted, matching the spec's explicit prohibition). The `memory_events` row itself is never touched (append-only) and no backfill is performed.
  - `brain/writer.py::record`: `request_hash`'s input set gained `record_type` and `workspace` for self-containedness of the stored event, matching §6.3 item 2. Both were already covered transitively via `content_hash`, so this only changes hashes for *future* writes; comparison stays per-record (`created["request_hash"]!=request_hash` against the same record's own stored value), so no migration and no behavior change for any existing row.
  - No key-derivation change, no schema change, no MCP tool surface change.
  - B8 probe re-run: baseline (pre-fix) — blanking a stored event's `request_hash` and replaying the same explicit key with a different `session_ref` silently returned `idempotent=true`. After the fix — the same replay raises `BRAIN_IDEMPOTENCY_CONFLICT` with `details.reason="legacy_record_without_request_hash"` and zero new rows across `memory_records`/`memory_events`/`record_state`/`artifact_links`. Reproduced against a disposable fixture journal only; no live journal was read or written.
  - Tests added to `tests/test_brain_write.py`: `test_idempotent_replay_returns_original_record_and_writes_no_new_rows` (identical replay, zero new rows); `test_idempotency_explicit_key_metadata_conflict_matrix` (§10 row 17b — same key/payload, `session_ref`/`source_ref`/`valid_at`/`links`/`supersedes` each varied independently, all conflict, original event byte-for-byte unchanged); `test_idempotency_legacy_row_without_request_hash_forces_conflict` (§10 row 17c — the B8 probe, asserts the exact `reason` and zero new rows); `test_idempotency_explicit_key_across_workspace_and_type_conflicts` (§10 row 17d — same key reused across workspace and across record type, both conflict); `test_idempotency_no_explicit_key_cross_workspace_produces_independent_records` (§10 row 18); `test_idempotency_supersede_replay_supersedes_target_exactly_once` (§10 row 19 — replay is idempotent, target superseded exactly once, no second `record_superseded` event). §10 row 16 (idempotency collision between agents) and row 17a (explicit-key payload conflict) were already pinned by the pre-existing `test_idempotency_is_agent_namespaced_and_semantic_duplicates_are_candidates` and `test_policy_bands_secret_filter_idempotency_and_provenance`; not duplicated.
  - Full suite: 174 passed + 5 subtests (up from the pre-Package-3 168 + 5), zero regressions. `check_exported()` green (no schema change).
  - Diff scope: `brain/writer.py`, `tests/test_brain_write.py`, this changelog entry, `docs/integrations/brain-mcp.md`. Nothing else. No live journal read or written; no push.
- 2026-07-16 — **Package 2 implemented (closes B3; I5 satisfied for `record://` in evidence/artifact fields).** Preceded by a read-only audit (`docs/reviews/package-2-record-reference-audit.md`) of both live M1 journals (personal, work) plus the legacy `spike.db` for context: zero existing `record://` references in `evidence`/`artifacts`/`commit`/`alternatives[].evidence` in any of the three; the sole `record://` reference found anywhere (personal journal) was a legitimate typed link. Per the spec's own §11 Package 2 alternative clause ("acceptable only if a read-only audit ... shows zero existing `record://` evidence rows: drop `record` from `URI_RE` for these fields and require typed `links[]`"), **VARIANT A** was implemented — the scheme is banned, not resolved:
  - `brain/write_policy.py`: removed `record` from `URI_RE`'s scheme alternation (was `^(?:repo|git|adr|route|doc|workspace|record)://[^\s]+$`, now `^(?:repo|git|adr|route|doc|workspace)://[^\s]+$`). `validate_evidence_uris` — called on `evidence` (which already folds in every `alternatives[].evidence`) and on `artifacts` (which already folds in `commit`), both before any DB connection is opened (`writer.py:97-103`, ahead of `enforce_band_c`, `verify_all`, `classify`, and `BEGIN IMMEDIATE`) — now rejects any `record://` value in those fields with the existing `BRAIN_INVALID_ARTIFACT_URI` code. No new error code, no new request field, no schema/`check_exported()` change (`URI_RE` is not part of any exported request/response schema).
  - `brain/writer.py`: no functional change. Reviewed and confirmed: the two `validate_evidence_uris` calls still run before `self.connect()`/`BEGIN IMMEDIATE`, so a rejected `record://` write never reaches `memory_records`, `memory_events`, `record_state`, or `artifact_links` — confirmed by test, not just by reading. Typed `metadata.links[]` validation (target exists, same workspace, not `rejected`/`forgotten`, `BRAIN_LINK_TARGET_NOT_FOUND`/`BRAIN_CROSS_WORKSPACE_LINK_DENIED`) is untouched and still runs inside the transaction — it never went through `URI_RE` in the first place (the `"record://"+target_record_id` string is a storage convention applied only after the target is already validated).
  - `scripts/audit_record_references.py` (new): read-only, reuses `brain/record_references.py::journal_references` as the only reference parser, classifies every `record://` `artifact_links` row as `typed_link` or `evidence_or_artifact_field`, flags dangling/cross-workspace/rejected-or-forgotten-target/forbidden-origin rows, reports record IDs/workspaces/statuses/relations only (no payload text). Exit 0 = clean or typed-links-only; exit 1 = any flagged row; exit 2 = journal missing/unreadable. Takes explicit `--journal LABEL=PATH` (repeatable); a legacy/spike journal is an optional extra input, Package 2 acceptance is personal+work.
  - Tests: `tests/test_brain_write.py` gained `test_record_uri_is_rejected_in_evidence_artifacts_commit_and_alternatives_evidence` (parametrized over dangling / same-workspace / foreign-workspace targets, across all four fields, asserting zero rows in all four tables after each rejection), `test_record_scheme_removed_from_uri_policy_does_not_affect_typed_links` (typed links still write/read/supersede correctly; dangling and cross-workspace typed links still fail with their existing codes), and `test_b3_probe_rerun_record_uri_evidence_is_rejected` (re-executes Appendix A probe 1 verbatim: both the dangling and the foreign-workspace `record://` evidence writes, previously accepted at baseline, now raise `BRAIN_INVALID_ARTIFACT_URI` with zero `artifact_links` rows persisted). `tests/test_audit_record_references.py` (new) covers the audit script's classification logic and CLI exit codes, including that it never writes to the journal it reads. Full suite: 168 passed + 5 subtests (up from the pre-Package-2 160 + 5), zero regressions — `doc://`/`repo://`/`git://` evidence, artifact verification/Band A-B classification, idempotency, and projector/retrieval tests all pass unmodified.
  - Live journals: not touched. This entry closes B3 in the write path; the Package 1 instance-marker backfill (`scripts/stamp_brain_instance.py`) on the real `personal`/`work` journals remains a separate, still-open operator step, unaffected by this package.
  - Diff scope: `brain/write_policy.py`, `tests/test_brain_write.py`, plus new files `scripts/audit_record_references.py`, `tests/test_audit_record_references.py`, `docs/reviews/package-2-record-reference-audit.md` (Implementation verification section), `docs/integrations/brain-mcp.md`, this changelog entry. Nothing else.
- 2026-07-15 — **Package 1 repair pass (closes review findings B-1, H-1, H-2, M-1, M-3).** An independent adversarial review (`docs/reviews/package-1-bootstrap-instance-binding-review.md`, verdict `REVISE PACKAGE 1`) of commit `2ec71d8` found, and reproduced by execution, one Blocking and two High findings inside the Package 1 implementation described below, plus two Medium findings. This entry records the fix; the review document's own findings and verdict text are left unedited, with a "Repair verification" section (§10) appended there instead. Fixes:
  - **B-1 (Blocking):** a half-published pair (crash between the two `os.replace` calls in `publish_pair`) that then receives a legitimate write could be silently overwritten by a naive retry falling through to a fresh build. `main()` now re-verifies both targets are actually absent after `cleanup_recoverable_partial` (refuses, exit 4, if either survived) and again immediately before `publish_pair` (closing the classification→publish TOCTOU window, which can span the full build duration). Neither guard deletes or overwrites anything it cannot already prove is safe to touch by the existing digest/identity rules — they only add a second look immediately before the two points that mutate target paths.
  - **H-1 (High):** a plain preflight dry run over an already-published system was overwriting the manifest with a non-published report, destroying `result_journal_digests` and degrading the next `--apply` from an idempotent no-op into a refusal. The manifest write now checks whether the on-disk manifest already says `published: true`; if so, the current run's report goes to a sibling `<manifest>.preflight.json` and the published manifest is left untouched.
  - **H-2 (High):** `scripts/build_brain_m1_indexes.sh` — the documented index-build step — never passed `--instance-id` to `run_brain_projector.py`, so following the runbook literally built a retrieval index under the `legacy` exemption (unmarked), which every marked reader then correctly refused. Fixed with one added flag.
  - **M-1 (Medium):** forward-completion (`crash_after_publish_before_manifest`) now re-runs `PRAGMA foreign_key_check` and a workspace-partition subset check on both targets before completing; a failure downgrades the classification to `corrupted` (refuse, nothing deleted) instead of blessing a broken pair. The written manifest gained a `recovered_observation` block (current sha256, logical digest, integrity, FK-check result, marker) per target; `result_journal_digests` is unchanged (still the staged-at-publish digests, which is what makes later `live` detection work).
  - **M-3 (Medium):** `scripts/smoke_brain_m1_write.py` — the documented write-smoke step — stopped working once `ControlStore.save()` gained its write-grant marker gate, because the gate resolves the *real* instance journal path, not the smoke's disposable one. The script now stamps its disposable journal and temporarily redirects the relevant `BRAIN_{INSTANCE}_JOURNAL_DB` env var to it for the duration of the `ControlStore.save()` call.
  - Not fixed in this pass, by explicit scope: M-2 (the identity marker is a copyable label under local-FS trust — accepted, documented, not hardened) and L-1–L-3 (RuntimeInspector marker check, stamp-script backup ergonomics, corrupt-marker-JSON handling) — all deferred exactly as the review's own §8 recommended.
  - Both of the review's own probe scripts (Probe 1: crash-between-replaces + post-publish write + retry; Probe 2: dry-run over a completed system) were re-run byte-identical against the fix and now show the corrected behavior (Probe 1: exit 4, write survives; Probe 2: manifest unchanged, rerun idempotent).
  - 9 new regression tests added to `tests/test_brain_instance_bootstrap.py`; full suite 160 passed + 5 subtests (0 failed), up from the pre-repair 151+5.
  - Diff scope: `scripts/bootstrap_brain_instances.py`, `scripts/build_brain_m1_indexes.sh`, `scripts/smoke_brain_m1_write.py`, `tests/test_brain_instance_bootstrap.py`, this changelog entry, and the review document's own repair-verification section — nothing else.
- 2026-07-15 — **Package 1 implemented (closes B1 and B2; I1, I2, I3 satisfied).** Committed as `2ec71d8`. Evidence:
  - Full suite green at 151 passed + 5 subtests (baseline before Package 1: 124 passed + 5 subtests) — 27 new tests, zero regressions, zero skips.
  - `brain/instance_identity.py` (new): the single module owning the `brain_instance_identity` journal table and the `retrieval_embedding_meta['instance_id']` retrieval-side marker — read/stamp/enforce/diagnose for both, plus `marked_instance_at_path` for the Control DB gate. `legacy` stays exempt everywhere.
  - Enforcement wired at every point §3 named: `JournalWriter.connect()`, `Repository.journal()`/`Repository.retrieval()`, `JournalReader.connect()` (optional `instance_id`, defaults to unset for standalone/diagnostic callers), `ProjectionProjector._write()` (stamps a genuinely empty retrieval DB, enforces otherwise), `brain/projector/validation.py::validate()` (soft `retrieval_instance_marker_missing`/`_mismatch` issues for `status()`/`plan()`), and `ControlStore.save()` (refuses `write_enabled=True` for `personal`/`work` without an existing, correctly marked journal at `instance_paths(brain_instance)`).
  - `scripts/bootstrap_brain_instances.py` rewritten: `build()` stamps the marker inside the same transaction as the data copy and validates it before publish; `classify_recovery()` implements the full §4.3 ten-row table; recovery deletion (`cleanup_recoverable_partial`) requires an exact content-digest match against what was staged, while forward-completing the manifest (`crash_after_publish_before_manifest`) requires only that each target's own immutable identity-marker row (`instance_id` + `source_digest`) prove it is this run's published output — **a deliberate deviation from §4.3 row 6's literal "digest == M.staged" wording**, made because that literal wording is unsatisfiable together with row 8's required outcome (a legitimate write landing between publish and manifest-completion changes the file's content digest by construction; requiring exact match would make forward-completion impossible exactly when it matters most, and B1 finding #2 — the original bug — is precisely about a write in that gap being destroyed). Preflight now also requires `--personal-workspaces`/`--work-workspaces` to equal `brain/control.py`'s `PERSONAL_WORKSPACES`/`WORK_WORKSPACES` exactly.
  - `scripts/stamp_brain_instance.py` (new): backup-first, digest-verified, idempotent one-time backfill for journals that predate this marker; refuses on workspace-partition violation, marker conflict, or integrity failure; **has not been run against any real journal** — none exist on the machine this was implemented on (the real Personal/WORK instances run on mini-core); this is a required, not-yet-completed operator step before deploying Package 1 there.
  - Test fixtures updated to stay meaningful under enforcement: `tests/journal_fixture.py` gained an optional `instance_id` param (stamps only when given; default fixture content intentionally still spans both Personal and WORK workspaces, matching the real pre-split legacy shape); `test_brain_write.py`, `test_brain_m1_acceptance.py`, `test_brain_control.py`, `test_brain_control_center.py` updated their helpers/env-vars accordingly. Two pre-Package-1 tests in `test_brain_instance_bootstrap.py` that asserted the *old, unconditional* marker-present-therefore-delete recovery behavior (exactly what B1 finding #3 flagged) were replaced — not silently dropped — with `classify_recovery`/`cleanup_recoverable_partial` tests asserting the corrected behavior; see that file's inline note at the replacement site.
  - New/expanded coverage maps directly to §10: rows 1–2 (existing failure-injection + new manifest-write injection), 3 (`test_live_instance_is_never_treated_as_a_reset_target`), 4 (`test_incompatible_existing_state_refuses_without_deleting`, `test_exactly_one_target_present_is_incompatible_existing_state`), 5 (retry-after-failure, existing + new), 6 (`test_rerun_after_success_is_idempotent_noop`), 7 (`test_crash_after_publish_before_manifest_forward_completes_without_touching_targets`), 8 (`test_post_publish_write_survives_recovery` — the critical regression test for B1 finding #2), 8b (`test_classify_recovery_never_deletes_a_foreign_file_it_cannot_verify`), 9/9b (`test_journal_writer_refuses_on_instance_mismatch`, `test_journal_writer_refuses_on_missing_marker`, `test_repository_journal_and_retrieval_refuse_on_instance_mismatch`, `test_repository_retrieval_refuses_on_instance_mismatch`, `test_projector_journal_reader_refuses_on_instance_mismatch`, `test_projector_stamps_retrieval_marker_on_first_write_and_enforces_it_thereafter`), 15/28 (`test_brain_control.py::test_write_grant_requires_an_existing_marked_journal`), plus `test_partition_must_match_control_constants` (new preflight gate, not a numbered §10 row).
  - Not attempted in this pass: Packages 2–9 (B3–B10) remain open, exactly as scoped.
- 2026-07-15 rev 2 — merged with the same-day rev 1: added probe-confirmed blockers B3 (record:// evidence), B4 (supersede-row scope bypass), B6 (dict-key secrets), B8 (legacy request_hash) which rev 1 missed or asserted as covered; corrected rev 1's claims that Band C "walks the entire tree", that the live path has "no record-URI gap", and that bootstrap recovery is fully retry-safe; replaced the recovery flow with the digest-verified classifier (§4.3); pinned explicit-key cross-workspace semantics (§6.2) instead of changing the key derivation. Retained rev 1's B1 lifecycle framing, B2 instance marker, B5 fail-open scope floor, `request_id` finding, §7 field inventory, and package discipline.
- 2026-07-15 rev 1 — initial version.
