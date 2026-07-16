# Package 2 — Cross-Instance Referential Integrity: Read-Only Audit

- **Status:** Audit complete; **VARIANT A implemented and verified (2026-07-16)** — see §8 Implementation verification below. Original audit findings and decision (§1–§7) are unmodified.
- **Date:** 2026-07-16
- **Reviewed HEAD:** `958f9c3` on `main` (matches the Package 1 completion commit; working tree clean before and after this audit).
- **Authority:** [write-safety-integrity-repair-spec.md](../architecture/write-safety-integrity-repair-spec.md) — blocker **B3**, invariant **I5**, §5 (Referential and scope integrity), §10 rows 10a–10b, §11 Package 2.
- **Audience:** the implementing agent (Sonnet) for Package 2, and the human reviewer deciding which variant to authorize.

---

## 1. Repo findings

### 1.1 Where `record://` URIs arise

| Path | Code | Behavior |
|---|---|---|
| Typed links (`metadata.links[]`) | `brain/writer.py:194-195` | For each `{target_record_id, relation}` in `metadata.links[]`, the writer inserts `artifact_links(record_id, "record://"+target_record_id, relation=<RecordRelation>, origin="deterministic", ...)`. The target is validated **before** this insert (`writer.py:160-167`): must exist, must not be `rejected`/`forgotten`, must be same-workspace (`BRAIN_LINK_TARGET_NOT_FOUND` / `BRAIN_CROSS_WORKSPACE_LINK_DENIED`). This path never calls `validate_evidence_uris` or `URI_RE` — the `"record://"` prefix is just a storage convention for an already-validated internal id. |
| Evidence / artifacts / commit / `alternatives[].evidence` | `brain/writer.py:97-103` folds `evidence` (incl. every `alternatives[].evidence`) and `artifacts`+`commit` into two lists, then calls `validate_evidence_uris(evidence, ...)` and `validate_evidence_uris(artifacts, ...)` (`brain/write_policy.py:65-68`). | `validate_evidence_uris` checks only `URI_RE.fullmatch(value)` — **syntax only**. `URI_RE` (`write_policy.py:29`) is `^(?:repo|git|adr|route|doc|workspace|record)://[^\s]+$` — it explicitly admits the `record` scheme. Nothing resolves the target. `writer.py:190-196` then inserts **every** evidence/artifact URI (`record://` included) into `artifact_links` with relation `"evidence"` or `"touches"`, `origin="deterministic"` if `verify_all` happened to recognize the scheme (it doesn't, for `record://`) else `"derived"`. |

Confirms the spec's B3 description exactly: one enforced path (typed links), one syntax-only path (evidence/artifacts/commit/alternatives) that shares the same URI scheme.

### 1.2 Where validation happens

- **Syntax only:** `validate_evidence_uris` (`brain/write_policy.py:65-68`), called twice per write (`writer.py:102-103`).
- **Resolution (exists / same-workspace / not rejected-forgotten):** only for `metadata.links[]` (`writer.py:160-167`) and for `metadata.supersedes` (`writer.py:150-158`). Nothing plays this role for `record://` inside evidence/artifacts.
- **Server artifact verification (`verify_all`)** (`brain/artifact_verifier.py`) only understands `repo://` and `git://`; a `record://` URI is never dereferenced by it and always ends up `origin="derived"`.

### 1.3 Where reference parsing/canonicalization lives

`brain/record_references.py` is the single canonical-reference parser, already built for exactly this purpose and already reused by bootstrap (`scripts/bootstrap_brain_instances.py:200-211,17` imports `journal_references` for `reference_audit`). It recognizes:
- structured legacy fields (`source_record`, `target_record_id`, `old_record`, `new_record`, `supersedes`, `superseded_by`) — pre-M1 legacy record types (`correction`, `artifact_link`, etc.), not part of the M1 write path.
- any string starting with `record://` anywhere in `payload` or `memory_events.data` (walked recursively).
- `artifact_links` rows where `artifact_uri LIKE 'record://%'` (this is where both typed-link rows and evidence/artifact-field rows land — the table does not distinguish them; only `relation` does, informally, since typed links always carry a `RecordRelation` value while evidence/artifact rows carry `"evidence"`/`"touches"`).
- `record_state.supersedes` / `record_state.superseded_by`.

This module already gives Package 2 everything it needs; no new parser is required, only a driver script that classifies the rows it returns.

### 1.4 Import / migration / bootstrap paths

- `scripts/bootstrap_brain_instances.py` — read-only audit only (`reference_audit`, `inspect_snapshot`). It **classifies** existing references (`ok`/`blocking` by workspace partition) and gates publish on zero unapproved cross-partition rows; it does not construct new `record://` references. The one exception, `CURATED_EXCLUSION` (`bootstrap_brain_instances.py:23-30`, `record_id="rec-056"`), is a single, digest-bound, operator-approved exclusion for a **legacy** `payload.source_record` field (`field_path="payload.source_record"`, relation `payload_source`) — not a `record://` URI, and not part of the M1 write path Package 2 is scoped to.
- `scripts/migrate_brain_m1.py`, `scripts/stamp_brain_instance.py`, `scripts/backfill_artifact_validation.py`, `scripts/apply_artifact_validation_migration.py` — none construct or accept `record://` URIs; grepped for `record://` across `scripts/` and found no hits outside `bootstrap_brain_instances.py`'s pre-existing audit/exclusion code described above.
- No importer other than the bootstrap split exists today. Per §5.1, any future importer would need to reuse `brain/record_references.py` and re-run the bootstrap audit gates — already the stated rule; Package 2 does not need to add anything here.

### 1.5 Test fixtures

`grep -rn "record://" tests/` (excluding this audit) shows exactly two uses, both already aligned with the intended design, neither exercising the B3 gap through the public write API:
- `tests/test_brain_write.py:140-141` — asserts a typed link (`links=[{"target_record_id":problem.record_id,"relation":"addresses"}]`) produced the expected `artifact_links` row. Goes through `writer.record()` normally.
- `tests/test_brain_write.py:179` — a **direct raw SQL insert** of a corrupt cross-workspace `artifact_links` row (`origin="corrupt-fixture"`), used to test the **read-side** scope filter (`_scope_related`, B4), not the write path. It deliberately bypasses `writer.record()` to simulate data that predates or evades write-time enforcement.

No test in the current suite writes `evidence=["record://…"]` or `artifacts=["record://…"]` through the public API and expects acceptance. All production-style fixtures use `doc://`, `repo://`, `git://` for evidence/artifacts and `links=[...]` for record-to-record relations. This means banning `record://` from evidence/artifacts/commit/alternatives[].evidence breaks no existing test.

### 1.6 Does typed `links[]` already cover the needed record-to-record relations?

Yes, for the M1 scope (`problem → analysis → decision → outcome`, `add → supersede → retract → confirm`). `RecordRelation` (`brain/models.py:86`) already has `addresses, analyzes, decides, implements, results_in, caused_by` — enough to express every edge in the documented lifecycle chain. `supersedes` has its own dedicated, already-validated field. No new relation type is needed for Package 2.

---

## 2. Live-journal audit

### 2.1 Method

- Live journals are reachable only on `mini-core`, over the existing SSH launcher host alias (`mini`) already used by `scripts/run_brain_mcp_ssh.sh` — no new access path was created.
- **Read-only copies only.** Each journal file (`personal/journal.db`, `work/journal.db`, and the legacy `spike/spike.db` from the separate checkout that lives on `mini-core`) was fetched with `scp` (a read of the remote file) into this session's scratch directory. The `-wal` files on the remote host were 0 bytes at copy time for `personal` and `work` (fully checkpointed), so the copied `.db` files are complete and consistent.
- All queries against the copies used `sqlite3` opened as `file:...?mode=ro&immutable=1` plus `PRAGMA query_only=ON` — belt-and-suspenders read-only. **Zero write statements were issued against the live files on `mini-core` or against the copies.**
- The audit script (ad hoc, not committed — see §5 for the recommended permanent version) reused the same reference shape as `brain/record_references.py` (`artifact_links` rows where `artifact_uri LIKE 'record://%'`), then classified each row by: origin (typed-link relation vs. evidence/artifact-style relation), dangling target, same/cross-workspace, same/cross-instance (via `PERSONAL_WORKSPACES`/`WORK_WORKSPACES` from `brain/control.py`), and target status. It also grepped `memory_records.payload`, `memory_records.raw_input`, and `memory_events.data` for the literal substring `record://` to catch any reference that might exist outside `artifact_links` (e.g., surviving in payload text without ever having been resolved into a relation row).

### 2.2 Per-journal summary

| Journal | Instance marker (`brain_instance_identity`) | Record count | `artifact_links` rows with `record://` URI | `supersedes`/`superseded_by` rows | Literal `record://` in payload/raw_input/events outside `artifact_links` |
|---|---|---|---|---|---|
| personal | table exists (schema is Package-1-current) but **no row** — the Package 1 one-time stamp backfill has not yet been run on this journal | 54 | 1 | 1 | 0 |
| work | table exists, **no row** (same reason) | 4 | 0 | 0 | 0 |
| legacy (`spike.db`, pre-M1 schema, `user_version=0`) | table does not exist (pre-Package-1 schema; legacy is permanently exempt) | 55 | 0 | 1 | 0 |

This confirms, independently of the repo-code reading, the Package 1 note that the live personal/WORK journals still need the one-time `stamp_brain_instance.py` backfill — an open Package 1 operator step, not something Package 2 should touch (flagged again in §6).

### 2.3 The one `record://` reference found

| # | Journal | Source record | Target record | Relation | Origin | Source ws | Target ws | Dangling | Cross-workspace | Cross-instance | Target status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | personal | `rec_8fea572184ba46b882ac3d66e598d42a` | `rec_7dcd90a052244180a66260ab8df94361` | `results_in` | **typed link** (`metadata.links[]`, written through `writer.record()`) | personal | personal | no | no | no | `accepted` |

`results_in` is a member of `RecordRelation`, never a value the evidence/artifact insertion path uses (`writer.py:193` only ever inserts `"evidence"`/`"touches"`), so this row is conclusively a typed link, not a B3-style leak. **Zero rows anywhere in either live journal, or in the legacy journal, originate from an unresolved `record://` URI in `evidence`, `artifacts`, `commit`, or `alternatives[].evidence`.**

### 2.4 Dangling / cross-workspace / cross-instance / rejected-or-forgotten findings

**None found.** Across all three journals: 0 dangling `record://` targets, 0 cross-workspace `record://` references, 0 cross-instance `record://` references, 0 references whose target is `rejected` or `forgotten`. The only `record://` row that exists at all is the same-workspace, same-instance, `accepted`-target typed link in §2.3.

### 2.5 What remains to verify on mini-core

Nothing outstanding for Package 2 itself — both live M1 journals (`personal`, `work`) were audited directly, which is the full live scope §11 Package 2 asks for ("both live journals attached to the PR"). Two adjacent, pre-existing items are **not** part of Package 2 but are worth carrying forward:
- The Package 1 instance-marker backfill (`scripts/stamp_brain_instance.py`) still has not been run against the real `personal`/`work` journals on `mini-core` (§2.2). This audit re-confirms that gap; it is Package 1's open item, not Package 2's.
- The legacy `spike.db` checkout on `mini-core` is a separate, out-of-scope M1 predecessor (pre-M1 schema, permanently read-only instance). It was audited here for completeness (and came back clean), but the spec's Package 2 scope is the M1 write path, which the legacy instance is explicitly excluded from (`writer.py:86-87`, `BRAIN_WRITE_DISABLED`).

---

## 3. Decision

### Recommended: **VARIANT A — ban `record://` in evidence/artifact fields**

Justification, per the spec's own condition for choosing A ("if the live journals do not contain legitimate such references"):

- Zero legitimate (or illegitimate) `record://` references exist in `evidence`, `artifacts`, `commit`, or `alternatives[].evidence` in either live M1 journal, or in the legacy journal.
- The one `record://` reference that does exist in the personal journal is a typed link, entirely unaffected by banning the scheme from the other four fields (typed links never pass through `validate_evidence_uris`/`URI_RE`).
- The M1 write path's own `RecordRelation` vocabulary already expresses every record-to-record edge the documented lifecycle needs (§1.6).
- No test in the current suite exercises `record://` in evidence/artifacts through the public API expecting acceptance (§1.5) — a ban introduces no known regression.
- Variant B (resolve-and-validate) would add write-time resolution logic, a rollback-safe transaction extension, and a full negative-test matrix to guard a capability that has zero live usage and a fully equivalent, already-enforced typed-link alternative. That is strictly more surface for zero behavioral gain today.

---

## 4. Implementation scope for Sonnet (Package 2, VARIANT A)

Matches §11 Package 2's stated file list; no file outside this list should be touched.

1. **`brain/write_policy.py`**
   - Remove `record` from `URI_RE`'s scheme alternation (`write_policy.py:29`): `^(?:repo|git|adr|route|doc|workspace)://[^\s]+$`.
   - Effect: `validate_evidence_uris` (called on `evidence` and `artifacts`, which already includes `commit` and every `alternatives[].evidence`, per `writer.py:97-103`) now rejects **any** `record://` value in those fields with the existing `BRAIN_INVALID_ARTIFACT_URI` code — no new error code needed. This happens **before** `enforce_band_c` and before any DB write, so nothing is ever inserted.
   - Do **not** touch `_looks_like_secret`'s use of `URI_RE` (`write_policy.py:42`) beyond what the scheme removal already changes — a `record://…` string no longer being URI-shaped there just means it falls back to whole-string entropy scanning, which is strictly more scrutiny, not less, and is not part of Package 2's job to tune.
2. **`brain/writer.py`**
   - No functional change expected. Verify (and note in the PR) that `validate_evidence_uris(evidence, ...)` and `validate_evidence_uris(artifacts, ...)` at `writer.py:102-103` still run **before** any `con.execute`/`BEGIN IMMEDIATE` — they already do (`writer.py:78-125`), so the rejection remains a pre-transaction, pre-side-effect refusal. Nothing to change here beyond confirming this order still holds after the `write_policy.py` edit.
   - Leave the typed-link insertion (`writer.py:194-195`) and its existing validation (`writer.py:160-167`) untouched — that is the preserved, correct path per §5.1's "preserve" row.
3. **Small read-only audit script**, reusing `brain/record_references.py::journal_references`, added under `scripts/` (e.g. `scripts/audit_record_references.py`). It should:
   - open journal(s) read-only (`mode=ro`, `PRAGMA query_only=ON`),
   - call `journal_references(con)` and additionally query `artifact_links` directly (as this audit did) to classify each `record://` row by origin (typed-link relation vs. `evidence`/`touches`), dangling/same-workspace/cross-workspace/cross-instance, and target status,
   - be safe to run against `personal` and `work` on `mini-core` for the exit-criteria evidence in §11 Package 2 ("Migration: audit report over both live journals attached to the PR").
   - This formalizes the ad hoc script used for §2 of this audit; that ad hoc script is not part of the repo and should not be treated as the deliverable — write a clean version.
4. **`tests/test_brain_write.py`** — new negative tests (see §5).

No schema change, no migration, no re-export of JSON schemas (`URI_RE` is not part of any exported request/response schema).

---

## 5. Required negative tests (§10 row 10b)

Parametrize across all four fields, for both a **dangling** target and a **foreign-workspace, otherwise-valid** target (both must be rejected — the ban is unconditional on the scheme, not on resolvability):

| Field | Example write | Expected |
|---|---|---|
| `evidence` | `record_problem(evidence=["record://rec-does-not-exist"], ...)` | `BRAIN_INVALID_ARTIFACT_URI`, no rows persisted |
| `evidence` | `record_problem(evidence=["record://<real-record-in-another-workspace>"], ...)` | same |
| `artifacts` | `record_outcome(artifacts=["record://rec-does-not-exist"], ...)` | same |
| `commit` | `record_outcome(commit="record://rec-does-not-exist", ...)` | same (folds into `artifacts`, `writer.py:101`) |
| `alternatives[].evidence` | `record_decision(alternatives=[{..., "evidence":["record://rec-does-not-exist"]}], ...)` | same (folds into `evidence`, `writer.py:99`) |

Plus:
- **Regression guard:** existing `test_decision_payload_record_links_and_supersede_are_append_only` (typed `links[]`) and `test_problem_analysis_project_and_old_baseline_hashes_stay_stable` (evidence/artifacts using `doc://`/`repo://`/`git://`) must continue to pass unmodified — proves the ban doesn't touch the preserved paths.
- **Stable error code check:** assert the error code is identical (`BRAIN_INVALID_ARTIFACT_URI`) whether the `record://` target would have been dangling or resolvable, so client-visible behavior does not depend on server-side data (no information leak about foreign-workspace record existence via error-code branching).
- **Appendix A probe re-run:** re-execute probe 1 from the spec's Appendix A (`evidence=["record://rec-does-not-exist"]` and `evidence=["record://rec-001"]` against `personal`) and confirm both now raise, closing the loop on the exact probes that motivated B3.

---

## 6. Migration / cleanup consequences

**None.** The live-journal audit (§2) found zero existing `record://` rows in `evidence`/`artifacts`/`commit`/`alternatives[].evidence` in either live M1 journal, so there is nothing to migrate, backfill, or clean up. The one existing `record://` row (personal journal, §2.3) is a typed link and is written through a code path this change does not touch. This audit report itself is the "read-only audit report over both live journals attached to the PR" the spec's Package 2 migration line calls for.

---

## 7. Open questions

1. **Package 1 marker backfill still pending on `mini-core`.** Both live M1 journals have the `brain_instance_identity` table but no row (§2.2) — `scripts/stamp_brain_instance.py` has not been run there yet. This blocks nothing in Package 2, but it means Package 2's own audit script (§4.3) cannot rely on a persisted instance marker and must keep using the workspace-set fallback (`PERSONAL_WORKSPACES`/`WORK_WORKSPACES`) the way this ad hoc audit did. Worth an explicit note to whoever runs the Package 1 backfill so it isn't forgotten.
2. **Legacy `spike.db` inclusion.** The spec's Package 2 migration line says "both live journals" (personal + WORK). This audit additionally covered the legacy `spike.db` checkout on `mini-core` for completeness (came back clean) even though the legacy instance is outside the M1 write path's scope. Confirm whether the PR's attached audit report should keep including legacy for defense-in-depth visibility, or stay strictly to personal+WORK per the spec's literal wording.
3. **Error-code granularity.** VARIANT A reuses `BRAIN_INVALID_ARTIFACT_URI` for the new rejection (a `record://` URI is, after the scheme change, simply an invalid URI shape again). If the user/reviewer wants a distinguishable code for "syntactically well-formed but scheme banned in this field" versus "malformed URI", that would be a small, additive change to `write_policy.py` — not required by the spec, flagged only as an option.

---

## 8. Implementation verification (2026-07-16, post-decision)

VARIANT A (§3) was implemented against `958f9c3` exactly as scoped in §4, with no scope creep. This section records what was actually done and tested; §1–§7 above are left as the pre-implementation audit and are not retroactively edited.

### 8.1 URI policy change

`brain/write_policy.py`, one line:

```
-URI_RE = re.compile(r"^(?:repo|git|adr|route|doc|workspace|record)://[^\s]+$")
+URI_RE = re.compile(r"^(?:repo|git|adr|route|doc|workspace)://[^\s]+$")
```

`validate_evidence_uris` (called on `evidence`, which already folds in every `alternatives[].evidence`, and on `artifacts`, which already folds in `commit` — `writer.py:97-103`) now rejects any `record://` value in those fields with the existing `BRAIN_INVALID_ARTIFACT_URI` code. No new error code, no new request field, no change to `metadata.links[]` or its validation.

### 8.2 Writer review outcome

Confirmed by reading and by test:
- Both `validate_evidence_uris` calls (`writer.py:102-103`) run before `self.connect()`/`BEGIN IMMEDIATE` (`writer.py:123-125`) — a rejected `record://` write never opens a journal transaction, let alone leaves a row.
- `test_record_uri_is_rejected_in_evidence_artifacts_commit_and_alternatives_evidence` writes a `record://` URI into each of the four fields (dangling / same-workspace-existing / foreign-workspace-existing targets) and asserts `memory_records`, `memory_events`, `record_state`, and `artifact_links` row counts are identical before and after every rejection — no partial write in any case.
- Typed `metadata.links[]` validation (`writer.py:160-167`, inside the transaction) is untouched: it never used `URI_RE`. `test_record_scheme_removed_from_uri_policy_does_not_affect_typed_links` writes a same-workspace typed link (succeeds, `artifact_links` row `record://<target>` with the correct relation, appears in `get_related` as `direction=incoming`), then confirms a dangling typed link still fails with `BRAIN_LINK_TARGET_NOT_FOUND` and a cross-workspace typed link still fails with `BRAIN_CROSS_WORKSPACE_LINK_DENIED`.

### 8.3 B3 probe re-run

`test_b3_probe_rerun_record_uri_evidence_is_rejected` re-executes Appendix A probe 1 verbatim:

| Probe | Baseline (pre-Package-2) | After Package 2 |
|---|---|---|
| `evidence=["record://rec-does-not-exist"]` | accepted, `artifact_links` row persisted | `BRAIN_INVALID_ARTIFACT_URI`, no row |
| `evidence=["record://<foreign-workspace-record>"]` | accepted, `artifact_links` row persisted | `BRAIN_INVALID_ARTIFACT_URI`, no row |

`artifact_links` row count with `artifact_uri LIKE 'record://%'` is asserted unchanged across both probe attempts.

### 8.4 Negative test results (§5 of this document)

All pass: 3 fields × 3 target kinds (dangling / same-workspace / foreign-workspace) × 4 call sites (`evidence`, `artifacts`, `commit`, `alternatives[].evidence`) all raise `BRAIN_INVALID_ARTIFACT_URI` with zero rows in any of the four journal tables. Error code is identical regardless of target resolvability, so no foreign-workspace-existence signal leaks through error-code branching.

### 8.5 Regression results

All pass, unmodified: same-workspace typed `links[]` write, dangling/cross-workspace typed link rejection, `doc://`/`repo://`/`git://` evidence and artifacts, artifact verification and Band A/B classification (`test_artifact_validation_controls_band_a_and_writes_audit_events`), idempotency (`test_idempotency_is_agent_namespaced_and_semantic_duplicates_are_candidates`, `test_policy_bands_secret_filter_idempotency_and_provenance`), and projector/retrieval behavior (`test_problem_analysis_project_and_old_baseline_hashes_stay_stable`, `test_search_filters_corrupt_cross_workspace_related_record_ids`).

### 8.6 Audit script

`scripts/audit_record_references.py` (new) — reuses `brain/record_references.py::journal_references` as its only reference parser; opens journals with `mode=ro` (falling back to `mode=ro&immutable=1` for a static copy without live `-wal`/`-shm` companions) plus `PRAGMA query_only=ON`; issues no write statement (`tests/test_audit_record_references.py::test_audit_never_writes_to_the_journal_it_reads` asserts the journal's bytes are identical before and after both a direct call and a full CLI invocation). Reports only `source_record`, `source_workspace`, `target_record`, `target_workspace`, `target_status`, `relation`, `origin` (`typed_link`/`evidence_or_artifact_field`), and boolean flags (`dangling`, `cross_workspace`, `target_rejected_or_forgotten`, `forbidden_origin`) — no payload text. Exit 0 = clean or typed-links-only; exit 1 = any flagged row; exit 2 = a given journal path is missing or unreadable. Takes `--journal LABEL=PATH` (repeatable) so `personal`/`work` (Package 2's acceptance scope) and an optional `legacy` journal can all be passed explicitly.

### 8.7 Full suite result

```
168 passed, 5 subtests passed
```
(up from the pre-Package-2 baseline of 160 passed + 5 subtests — 8 new tests: 3 in `tests/test_brain_write.py`, 5 in the new `tests/test_audit_record_references.py`; zero regressions, zero skips.)

`brain/schemas.py::check_exported()` returns `True` — no exported JSON schema changed (`URI_RE` is internal to `write_policy.py`, not part of any request/response model).

### 8.8 Files changed

- `brain/write_policy.py` (1-line `URI_RE` edit)
- `tests/test_brain_write.py` (+3 tests)
- `scripts/audit_record_references.py` (new)
- `tests/test_audit_record_references.py` (new)
- `docs/reviews/package-2-record-reference-audit.md` (this section)
- `docs/integrations/brain-mcp.md` (new "Record-to-record references" section)
- `docs/architecture/write-safety-integrity-repair-spec.md` (B3 status line + changelog entry)

No file outside this list was touched. No live journal on `mini-core` was written (verified: `stat` mtimes/sizes for `personal/journal.db`, `work/journal.db`, and the legacy `spike/spike.db` unchanged from the values recorded during the original audit in §2). No commit was made until this verification passed.

---

## Validation performed before finalizing this document

- `git status` in `pavol-brain`: clean before this audit began and clean after — this document is the only file added.
- No journal file on `mini-core` was modified: all remote access was `scp` (read) of the live files into local scratch space; all queries against the copies used `mode=ro&immutable=1` + `PRAGMA query_only=ON`. No `INSERT`/`UPDATE`/`DELETE`/`PRAGMA ... = ...` write statement was issued anywhere in this audit.
- No commit, no push made in `pavol-brain`.
- This report contains only record IDs, workspace names, table/column names, relation names, statuses, and counts — no payload text, no secrets, and no absolute local filesystem paths.
