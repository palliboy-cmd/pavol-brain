# Acceptance Matrix — §10 Adversarial Acceptance Matrix Traceability

Maps every row (and sub-row) of `docs/architecture/write-safety-integrity-repair-spec.md`
§10 to the real, individually-runnable pytest test(s) that prove it, the
package/commit that closed the underlying blocker, and what the test
actually demonstrates. Built during **Package 8 — Adversarial and recovery
suite consolidation** (§11).

No row below is marked COVERED on the strength of "the full suite passes."
Each COVERED row names the exact assertions that prove every column of its
§10 row (Expected result / Expected persistent state / Expected
audit-error behavior). Rows that are only **PARTIAL** say precisely which
column is unproven and why it was left that way — see "Known limitations"
at the end. No PARTIAL row was silently upgraded to COVERED.

## How to run this suite

```
scripts/run_brain_acceptance.py
# equivalent to:
pytest tests/ -m acceptance -q
```

This selects exactly the modules carrying `pytestmark = pytest.mark.acceptance`:
`test_brain_instance_bootstrap.py`, `test_brain_write.py`,
`test_brain_scope_integrity.py`, `test_artifact_validation.py`,
`test_brain_projector.py`, `test_brain_control.py`. It is a thin wrapper
over the same `pytest tests/ -q` used for the authoritative full suite —
same interpreter, same collection rules, no live journal/retrieval writes
(every test below constructs its own `tmp_path` journal via
`journal_fixture()`/`monkeypatch`; the runner additionally strips
`BRAIN_*` live-instance environment variables as a safety net). Legacy
"spike" suites live under `spike/tests/` and `sqlite-spike/tests/`, both
outside `tests/`, so `pytest tests/` never collects them.

## Legend

- **Type**: `unit` (single function/module, no journal), `integration`
  (real journal/Brain/projector round-trip via `tmp_path`), `mutation`
  (constructs a specific corrupted state and asserts the safety net
  catches it), `property` (asserts an invariant over a generated/looped
  set of inputs), `manual/live` (operator-run script against a real
  journal, not part of `pytest tests/`).
- **Status**: `COVERED` (every §10 column proven), `PARTIAL` (named gap,
  see "Known limitations"), `ADJACENT` (real coverage that supports the
  row's invariant but isn't the row's own literal scenario).

---

## Rows 1–9b — Bootstrap atomicity, retry, recovery, instance markers (I1, I2, I3)

Closed by **Package 1**: `2ec71d8` (feat, initial), `59f97c4` (fix, recovery/rollout gaps), `958f9c3` (fix, marker preserved on recovery refusal).

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T01 | 1 — failure injection per bootstrap step | `test_brain_instance_bootstrap.py::test_second_build_failure_cleans_staging_and_retry_succeeds:140` | integration | 2nd `build()` call fails → staging cleaned, no targets, retry succeeds | PARTIAL |
| T01 | 1 (cont.) | `::test_count_gate_failure_publishes_nothing:154` | integration | count-mismatch gate → nothing published | PARTIAL |
| T01 | 1 (cont.) | `::test_integrity_or_fk_gate_failure_publishes_nothing:166` | integration | `av.verify_state` mismatch gate → nothing published | PARTIAL |
| T01 | 1 (cont.) | `::test_reported_fk_failure_publishes_nothing:174` | integration | reported FK gate → nothing published | PARTIAL |
| T01 | 1 (cont.) | `::test_second_publish_failure_rolls_back_first_target:186` | integration | 2nd `os.replace` fails → 1st target rolled back | PARTIAL |
| T01 | 1 (cont.) | `::test_crash_after_publish_before_manifest_forward_completes_without_touching_targets:281` | integration | manifest-write failure after both publishes → forward-completion | PARTIAL |
| T02 | 2 — rollback without partial published state | `::test_second_build_failure_cleans_staging_and_retry_succeeds:140` | integration | neither target exists after injected 2nd-build failure | PARTIAL |
| T03 | 3 — staging validation gates | `::test_count_gate_failure_publishes_nothing:154`, `::test_integrity_or_fk_gate_failure_publishes_nothing:166`, `::test_reported_fk_failure_publishes_nothing:174` | integration | each gate independently blocks publish | COVERED |
| T04 | 4 — never exactly one target | *(no dedicated test — see limitations)* | — | — | PARTIAL |
| T05 | 5 — retry after each failure type | `::test_second_build_failure_cleans_staging_and_retry_succeeds:140` | integration | retry after 2nd-build failure succeeds | PARTIAL |
| T06 | 6 — duplicate bootstrap after success | `::test_rerun_after_success_is_idempotent_noop:271` | integration | rerun after success: `personal`/`work`/`manifest` bytes unchanged, `main()` returns normally | PARTIAL |
| T07 | 7 — crash after publish, before manifest | `::test_crash_after_publish_before_manifest_forward_completes_without_touching_targets:281` | integration | manifest-write failure after both `os.replace` calls → rerun forward-completes: targets unchanged (⛁), manifest written, marker gone | COVERED |
| T08 | 8 — post-publish writes survive recovery | `::test_post_publish_write_survives_recovery:301` | integration | row-7 setup + a record appended to `personal` before rerun → record still present, forward-completion, no deletion | COVERED |
| T08b | 8b(a) — target deleted after success, rerun | *(no test — see limitations)* | — | — | PARTIAL |
| T08b | 8b(b) — foreign file at target path with marker present | `::test_classify_recovery_never_deletes_a_foreign_file_it_cannot_verify:223` | unit | `classify_recovery()`/`cleanup_recoverable_partial()` classify as `foreign_corrupted`; foreign file bytes unchanged | PARTIAL |
| T08b | 8b(b) via CLI | `::test_incompatible_existing_state_refuses_without_deleting:347`, `::test_exactly_one_target_present_is_incompatible_existing_state:358` | integration | `main()` refuses incompatible/single-target state without deleting | COVERED |
| T09 | 9 — instance-marker mismatch (writer/repository/projector) | `::test_journal_writer_refuses_on_instance_mismatch:384`, `::test_repository_journal_and_retrieval_refuse_on_instance_mismatch:412`, `::test_repository_retrieval_refuses_on_instance_mismatch:423`, `::test_projector_journal_reader_refuses_on_instance_mismatch:440` | integration | `BRAIN_INSTANCE_MISMATCH` before any query; journal/retrieval bytes unchanged (⛁, added in Package 8) | COVERED |
| T09b | 9b — missing marker | `::test_journal_writer_refuses_on_missing_marker:394` | integration | `BRAIN_INSTANCE_MARKER_MISSING`; journal bytes unchanged (⛁, added in Package 8) | COVERED |

## Rows 10a–10b — Referential integrity (I5)

Closed by **Package 2**: `3c60d02` (fix: enforce typed record references).

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T10a | 10a — cross-instance/workspace direct link | `test_brain_write.py::test_record_scheme_removed_from_uri_policy_does_not_affect_typed_links:381` (inline case, lines ~410–420) | integration | `BRAIN_CROSS_WORKSPACE_LINK_DENIED`; no rows persisted (delta check added Package 8); error audited (added Package 8) | COVERED |
| T10b | 10b — `record://` in evidence/artifacts/commit/alternatives | `::test_record_uri_is_rejected_in_evidence_artifacts_commit_and_alternatives_evidence:326` | integration | dangling / same-workspace / foreign-workspace `record://` in all 4 fields → `BRAIN_INVALID_ARTIFACT_URI`, no rows persisted, error audited exactly 12 times (added Package 8) | COVERED |
| T10b | 10b (probe replay) | `::test_b3_probe_rerun_record_uri_evidence_is_rejected:424` | integration | Appendix A probe 1 replay: dangling + foreign-workspace evidence rejected, no rows | COVERED |

## Rows 11–14 — Scope-safe retrieval expansion (I4, I10)

Closed by **Package 5**: `dac4b56` (fix: enforce scope-safe retrieval expansion), `e8d1605` (fix: reject malformed record relation URIs).

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T11 | 11 — cross-instance/workspace reverse link (`get_related`) | `test_brain_scope_integrity.py::test_corrupt_incoming_link_from_foreign_workspace_excluded:219`, `::test_corrupt_outgoing_link_to_foreign_workspace_excluded:226` | mutation | foreign id absent from `get_related` for both link directions | COVERED (get_related) |
| T11 | 11 — same, `search(include_artifacts=True)` | `::test_b4_probe_include_artifacts_and_provenance_sanitized_in_search:195`, `::test_i10_every_expanded_id_is_directly_fetchable_with_same_scope:438` | mutation/property | link-based leak also excluded from `search`, combined with other corruptions | PARTIAL |
| T12 | 12 — cross-workspace `supersedes`/`superseded_by` | `::test_b4_probe_corrupt_cross_workspace_supersedes_excluded_from_get_related:152`, `::test_b4_probe_corrupt_supersedes_nulled_in_get_record_provenance:159`, `::test_b4_probe_corrupt_superseded_by_excluded_and_nulled:170` (get_related/get_record, both directions), `::test_b4_probe_include_artifacts_and_provenance_sanitized_in_search:195` (`supersedes` via search), `::test_b4_probe_corrupt_superseded_by_nulled_in_search_provenance:179` (`superseded_by` via search — added Package 8) | mutation | both pointer directions dropped from `get_related`, nulled in `get_record`/`search` `Provenance` | COVERED |
| T13 | 13 — no-scope regression gate | `::test_get_record_without_scope_fails_closed_before_reading_data:113`, `::test_get_related_without_scope_fails_closed_before_reading_data:119` (raise `TypeError`, not `BrainError`), `::test_search_without_scope_fails_closed_before_embedding:130` (raises `BrainError`/`BRAIN_UNKNOWN_WORKSPACE`) | integration | omitted scope always fails closed before reading data, for all 3 read methods | PARTIAL |
| T13 | 13 — I10 property | `::test_i10_every_expanded_id_is_directly_fetchable_with_same_scope:438` | property | every id returned via `get_related`/`search(include_artifacts=True)` expansion is independently fetchable via direct `get_record` with the same scope | COVERED |
| T14 | 14 — `include_artifacts` scope escalation | `::test_i10_every_expanded_id_is_directly_fetchable_with_same_scope:438` (incoming/outgoing link, supersedes, dangling, malformed URI), `::test_b4_probe_include_artifacts_and_provenance_sanitized_in_search:195` (supersedes + outgoing link) | mutation | no out-of-scope id leaks through `search(include_artifacts=True)` across the tested corruption shapes | PARTIAL |

## Row 15 — Invalid grant / workspace mismatch (I4)

Closed by **Package 5** (denial paths, `dac4b56`) and **Package 1** (save-gate part, `2ec71d8`/`59f97c4`).

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T15 | 15 — `BRAIN_INSTANCE_DENIED` | `test_brain_control.py::test_write_grant_defaults_off_and_instance_is_bound:55` | unit | wrong-instance profile denied at `authorize()` | COVERED |
| T15 | 15 — `BRAIN_WORKSPACE_DENIED` / `BRAIN_SENSITIVE_SCOPE_DENIED` | `::test_unknown_disabled_tool_workspace_sensitive_denials:26` | unit | both denials raised at `authorize()` | COVERED |
| T15 | 15 — `ValueError` at `ControlStore.save` | `::test_write_grant_requires_an_existing_marked_journal:43` (missing/wrong-instance journal, now also asserts `s.get(...) is None` — added Package 8), `::test_sensitive_must_be_subset_and_generic_requires_no_source_change:37`, `::test_instance_workspace_mapping_and_work_sensitive_floor_are_enforced:68` | unit | 3 distinct `ValueError` messages naming the violated constraint; nothing persisted (added Package 8, first two `save()` cases) | PARTIAL |

## Rows 16–19 — Idempotency contract (I6)

Closed by **Package 3**: `82b45e0` (fix: harden idempotency conflict detection), `7a55c24` (docs align).

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T16 | 16 — idempotency collision between agents | `test_brain_write.py::test_idempotency_is_agent_namespaced_and_semantic_duplicates_are_candidates:108` | integration | agent B gets a distinct record, `status="candidate"`, `possible_duplicate_of=A's id` | COVERED |
| T17a | 17a — explicit-key payload conflict | `::test_idempotency_is_agent_namespaced_and_semantic_duplicates_are_candidates:108` (lines ~111–112) | integration | `BRAIN_IDEMPOTENCY_CONFLICT` on payload replay | PARTIAL |
| T17b | 17b — explicit-key metadata conflict | `::test_idempotency_explicit_key_metadata_conflict_matrix:131` | integration | 5 metadata-variant replays (session_ref/source_ref/valid_at/links/supersedes+change_reason), each `BRAIN_IDEMPOTENCY_CONFLICT`, journal row counts and the original event byte-identical | COVERED |
| T17c | 17c — legacy row without `request_hash` | `::test_idempotency_legacy_row_without_request_hash_forces_conflict:153` | integration | stored event stripped of `request_hash`, replay with different metadata → `BRAIN_IDEMPOTENCY_CONFLICT` with `reason=legacy_record_without_request_hash`, one record, corrupted event row untouched | COVERED |
| T17d | 17d — explicit key across workspace/type | `::test_idempotency_explicit_key_across_workspace_and_type_conflicts:174` | integration | same key reused in a different workspace and as a different record type → `BRAIN_IDEMPOTENCY_CONFLICT` both times, one record | COVERED |
| T18 | 18 — same payload, different workspaces, no explicit key | `::test_idempotency_no_explicit_key_cross_workspace_produces_independent_records:189` | integration | two independent `accepted` records, row-count delta of +2 across all 4 journal tables | COVERED |
| T19 | 19 — supersede replay | `::test_idempotency_supersede_replay_supersedes_target_exactly_once:199` | integration | identical replay is idempotent, no new rows, exactly one `record_superseded` event, target superseded once | COVERED |

## Rows 20–22 — Canonical write-envelope filtering / secret non-persistence (I7, I8)

Closed by **Package 4**: `fcbc2ee` (fix: enforce canonical write-envelope filtering), `9bef695` (fix: close Package 4 secret leak paths).

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T20 | 20 — secret in every write field (17 fields) | `test_brain_write.py::test_secret_non_persistence_matrix:575` | integration | all 17 spec-named fields rejected with `BRAIN_WRITE_SECRET_REJECTED` (code assertion added Package 8), canary absent from raw journal file bytes and raw audit log bytes (genuine byte-level `read_bytes()` grep), no rows persisted per case | COVERED |
| T21 | 21 — secret in nested and list values | `::test_b6_dict_key_and_nested_secrets_are_rejected_by_band_c:505` (dict key inside a list, dict value inside a list, top-level dict key — direct `enforce_band_c()` unit calls, no journal/audit log touched), `::test_collect_client_strings_walks_keys_values_and_nesting:493` (scanner reaches all nesting shapes) | unit | Band C's scanner (`collect_client_strings`) is proven to walk keys/values at arbitrary depth and nesting; every *reachable* request-model field is a flat structure (`verification: dict[str,str]`, `evidence: list[str]`, etc.) so "two levels deep in a dict value" has no real request path to exercise end-to-end — this is a scanner-implementation property test, not reachable via any public write call | PARTIAL |
| T22 | 22 — `request_id` shape | `::test_request_id_shape_contract:548` (overlong/whitespace/canary via `record_outcome`), `::test_b7_probe_rerun_request_id_canary_is_rejected_before_any_write:564` (canary, with real audit-log byte grep) | integration | `BRAIN_INVALID_REQUEST` before journal work, `request_id` field on the error sanitized to `""`, canary never echoed, no rows, canary absent from audit log bytes | COVERED |

## Rows 23–24 — Artifact trust model (I9)

Closed by **Package 6**: `2700ba9` (fix: expose server-owned artifact trust semantics), `fa2e3b8` (fix: harden artifact verifier argument isolation).

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T23a | 23(a) — request self-asserts a verification/state field | `test_brain_write.py::test_client_cannot_self_assert_trust_fields_on_any_request_model:842`, `::test_request_models_reject_trust_fields_at_the_schema_level:852` | integration/unit | 6 forbidden fields rejected as unknown (`extra="forbid"`), no rows | COVERED |
| T23b | 23(b) — forged `verified_active` state row diverging from events | `test_artifact_validation.py::test_tampered_state_table_is_detected:66` | unit | direct DB tamper of a correct `verified_active` row detected by `verify_state`, mismatch names the link id; `rebuild_state` re-run then repairs it and `verify_state` returns clean (added Package 8) | COVERED |
| T24 | 24 — artifact scope escalation / unverifiable schemes | `test_brain_write.py::test_artifacts_only_band_a_gate_unaffected_by_package_6:829` (Band B candidate via `verified_tool_result` + `doc://`), `::test_doc_scheme_artifact_is_unverified_reference:756`, `::test_unknown_repo_alias_is_unverified_reference_without_path_leak:766` (`unverified_reference` state, both via `explicit_user_confirmation` rather than `verified_tool_result`) | integration | Band B classification and `unverified_reference` state each independently proven; no single test combines an unknown-repo-alias artifact with `source_assertion="verified_tool_result"` in one call, and none directly queries `artifact_validation_state`/`events` to confirm no `verified_*` row was written (only the derived trust view is checked) | PARTIAL |

## Rows 25–27 — Projector postconditions (I11, I12)

Closed by **Package 7**: `0e28eda` (fix: strengthen projector postconditions). Package 8 additions noted inline.

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T25 | 25 — projector postcondition failure (silent-skip + FTS/link/embedding mutation) | `test_brain_projector.py::test_v2_outcome_cannot_advance_cursor_without_projection:99` (silent-skip subclass), `::test_mutation_deleted_fts_row_blocks_cursor_then_retry_advances_once:265`, `::test_mutation_deleted_embedding_row_blocks_cursor_then_retry_advances_once:278`, `::test_mutation_deleted_required_link_blocks_cursor_then_retry_advances_once:291`, `::test_mutation_stale_extra_link_blocks_cursor_then_retry_advances_once:304` | mutation | each corruption raises a named `ProjectorError` reason string, cursor unmoved, rollback verified | PARTIAL |
| T26 | 26 — cursor retry | same 4 mutation tests as T25 + `::test_source_event_id_present_for_every_outcome:247` | mutation | clean retry after each T25 failure returns `HEALTHY`, cursor advances exactly once to the failed batch's `source_event_id`, next run is `NO_CHANGES`; every outcome carries `source_event_id` (checked generically, not re-verified inside each T25 retry body) | COVERED |
| T27 | 27 — deterministic full rebuild | `::test_deterministic_full_rebuild_with_live_written_records:504` | integration | live-written record appended and projected; retrieval DB deleted and rebuilt to `NO_CHANGES`; hashes, document rows (13 columns incl. sensitivity/valid_at/invalid_at/confidence/source_event_id/supersedes/superseded_by — widened Package 8), embedding rows, FTS rows, link rows, and cursor final state (added Package 8) all equal; journal SHA-256 byte-identical before/after | COVERED |

## Row 28 — Write-grant before bootstrap (I2)

Closed by **Package 1**: `2ec71d8`/`59f97c4`.

| T# | §10 row | Test module::name | Type | Proves | Status |
|---|---|---|---|---|---|
| T28 | 28 — write-grant before bootstrap | `test_brain_control.py::test_write_grant_requires_an_existing_marked_journal:43` | unit | `ControlStore.save(write_enabled=True)` with no instance journal → `ValueError` naming the missing/mismatched journal path; no profile persisted (`s.get(...) is None`, added Package 8) | COVERED |
| T28 | 28 (stub pointer) | `test_brain_instance_bootstrap.py::test_control_store_write_grant_gate_is_the_row_28_scenario:464` | — | docstring-only pointer to the real test above (`assert True` body) — not independent verification | ADJACENT |

---

## Package 7 review F1 follow-up — orphan FTS/link detection (this package)

`brain/projector/validation.py::validate()` gained two new checks:
`orphan_fts` (an FTS row whose `rowid` has no matching `retrieval_documents.doc_id`)
and `orphan_link` (a `retrieval_document_links` row whose `record_id` has no
matching `retrieval_documents.record_id`).

| Test | Type | Proves |
|---|---|---|
| `test_brain_projector.py::test_orphan_fts_row_without_document_requires_rebuild_no_auto_repair:407` | mutation | orphan FTS row (document/embedding/links deleted, FK off) → `run_once()` returns `REBUILD_REQUIRED` with `orphan_fts` in `issues`; FTS row count and content unchanged after the call (no auto-repair) |
| `::test_orphan_link_row_without_document_requires_rebuild_no_auto_repair:425` | mutation | orphan link row (document/embedding/FTS deleted, FK off) → `REBUILD_REQUIRED` with `orphan_link`; link row count and content unchanged (no auto-repair) |
| `::test_remove_already_absent_branch_catches_leftover_embedding_row:443` | mutation | `_remove()`'s `already_absent` branch now calls `_assert_removed(con, record_id, None)`, which still catches a leftover embedding row (keyed by `record_id`, doc_id not required) |
| `::test_remove_already_absent_branch_catches_leftover_link_row:454` | mutation | same branch also catches a leftover link row |
| `::test_remove_already_absent_branch_returns_cleanly_when_truly_clean:466` | mutation | when genuinely clean, the branch still returns `"already_absent"` and increments `report.noops` |

The FTS check on the `already_absent` path is necessarily out of scope for
`_assert_removed` itself (the former `doc_id` is not recoverable once the
document row is already gone) — it is covered instead by the new
`validate()` `orphan_fts` check, which runs at the start of every
`run_once()`/`status()` call and would catch it on the very next run. This
is consistent with the Package 7 review's own framing: I11 binds records
touched by the current batch, and an already-absent record was not touched
by it.

## Skip-reason runtime guard (this package)

`ProjectionProjector._skip_reason` no longer uses a bare `assert` for its
closed-enum check (Package 7 review F2) — it now does an explicit
`if reason not in CLOSED_SKIP_REASONS: raise ProjectorError(...)`, which
survives `python -O`.

| Test | Type | Proves |
|---|---|---|
| `test_brain_projector.py::test_skip_reason_runtime_guard_rejects_reason_outside_closed_set:478` | unit | monkeypatches `CLOSED_SKIP_REASONS` to a smaller set and confirms the guard raises `ProjectorError` for a reason it would normally accept |
| `::test_skip_reason_guard_is_not_implemented_via_bare_assert:489` | unit | AST-inspects the method body: no `ast.Assert` node, a reachable `ast.Raise` node — proves the guard cannot be stripped by `python -O` |

Manually confirmed outside the test suite: `python -O -c "..."` calling
`ProjectionProjector._skip_reason` with a patched `CLOSED_SKIP_REASONS`
still raises (bare `assert` would have been compiled out and silently
returned the closed-but-now-invalid reason instead).

---

## Manual / live evidence (not part of `pytest tests/`)

These are the operator-run, read-only or one-time deliverables the spec's
Package migrations required. None of them run as part of the automated
suite; none write to a live journal.

| Deliverable | Status | Evidence |
|---|---|---|
| Package 2 live record-reference audit (both live journals, `scripts/audit_record_references.py`) | Reported done in Package 2 changelog | `tests/test_audit_record_references.py` proves the *tool* is read-only and correctly classifies clean/dangling/cross-workspace/forbidden-origin rows (`test_audit_never_writes_to_the_journal_it_reads:125`, `test_audit_flags_dangling_cross_workspace_and_forbidden_origin_rows:50`); the actual run against `mini-core`'s live journals is an operator action outside this repo's test suite — this package did not re-run it |
| Package 4 live read-only secret audit (`scripts/audit_write_envelope_secrets.py`) | Reported done in §11 Package 4 changelog: 0 findings, both journals clean | Same posture as above — the audit *tool* is exercised by tests; the live run against `mini-core` copies is an operator record from the Package 4 follow-up session, not reproduced here |
| Package 1 instance-marker live backfill (`scripts/stamp_brain_instance.py` against the real `personal`/`work` journals on `mini-core`) | **Not executed by this package.** Status per §11 Package 1: "operator step; document in docs/operations/brain-runtime.md" | `tests/test_stamp_brain_instance.py` proves the backfill script's behavior on fixture journals (dry-run, apply-with-backup, rerun idempotence, wrong-instance refusal, corrupt-journal refusal, backup-restore-on-failure) — it does not assert the live backfill happened |
| §12 Final Fable acceptance checklist / live deploy or acceptance run | **Not executed.** This package is test/doc consolidation only, per its own scope ("Nerob Package 9 rollout alebo live deployment") | N/A — explicitly out of scope for Package 8 |

Do not read the above as "live rollout is done." Only the checklist items
this package could prove from the repo's own automated tests are marked
COVERED anywhere in this document; every live/operator action is called
out here as such, separate from automated coverage.

---

## Red-test evidence

For every *new* invariant test introduced across Packages 1–7 (not
reconstructing full experiment history, per instruction — using existing
review documents and commit messages as the audit trail):

| Test area | Failure/blocker it reproduced | Closing commit | Review probe (before/after) |
|---|---|---|---|
| Bootstrap atomicity/recovery (T01–T09b) | B1 (destructive recovery, no digest verification), B2 (no instance-marker binding) | `2ec71d8`, `59f97c4`, `958f9c3` | No standalone Package 1 review doc found in `docs/reviews/`; changelog in §11 records exit criteria met |
| Referential integrity (T10a–T10b) | B3 (`record://` accepted unresolved into `evidence`/`artifacts`) | `3c60d02` | `test_b3_probe_rerun_record_uri_evidence_is_rejected` explicitly re-runs the pre-fix baseline probe and asserts the old-baseline behavior (accept + persist) no longer occurs |
| Idempotency (T16–T19) | B8 (missing `request_hash` silently matched divergent metadata) | `82b45e0` | `test_idempotency_legacy_row_without_request_hash_forces_conflict` docstring/assertions directly encode the reproduced B8 scenario and its new conflict outcome |
| Secret filtering (T20–T22) | B6 (dict keys unscanned), B7 (`request_id` unscanned) | `fcbc2ee`, `9bef695` | `docs/reviews/` Package 4 review; `test_b6_probe_rerun_verification_key_with_equals_is_rejected_without_leaking` and `test_b7_probe_rerun_request_id_canary_is_rejected_before_any_write` are literal probe re-runs; F1/F2/F2b findings and their closure are in the Package 4 §11 changelog entry |
| Scope expansion (T11–T14) | B4 (supersede rows bypassed `_scope_related`), B5 (fail-open no-scope reads) | `dac4b56`, `e8d1605` | `test_b4_probe_*` tests are named after and directly reproduce the B4 probes; `docs/reviews/package-5-scope-safe-retrieval-review.md` F1 finding and its `e8d1605` closure are cross-referenced in `test_f1_malformed_record_like_uri_dropped_from_get_related_and_search`'s docstring |
| Artifact trust (T23a–T24) | B9 (unverified fold, no verifier metadata) | `2700ba9`, `fa2e3b8` | `docs/reviews/` Package 6 review recorded a `REVISE PACKAGE 6` verdict for F1 (HIGH, git argument injection) and F2 (MEDIUM, `JSONDecodeError` crash); both closed in `fa2e3b8`, and the `test_f1_*`/`test_f2_*` tests in `test_brain_write.py`/`test_artifact_validation.py` are the direct closure probes named after those findings |
| Projector postconditions (T25–T27) | silent-skip cursor advance without full postcondition | `0e28eda` | `docs/reviews/package-7-projector-postconditions-review.md` verdict `APPROVED FOR PACKAGE 8`, §9 test evidence: 46 passed (14 new), full suite 291 passed |
| Package 8: orphan FTS/link, skip-reason guard, rebuild widening | Package 7 review F1 (orphan detection gap) and F2 (bare-assert guard) | this package's commit | This document's "Package 7 review F1 follow-up" and "Skip-reason runtime guard" sections above; verified failing-before/passing-after by running the new tests against the pre-Package-8 `validate()`/`_skip_reason` (orphan checks absent → `orphan_fts`/`orphan_link` never appear in `issues`; bare `assert` present → AST test fails) before applying the fix in this session |

No red/green re-run was performed for Packages 1–6 in this session (per
the task's own instruction not to reconstruct full experiment history);
those packages' red-test evidence is taken from the cited review documents
and commit messages, not re-verified here.

---

## Known limitations (honest gaps, not silently closed)

These are documented rather than force-closed, to avoid overstating
coverage. None of them represents a confirmed security regression — each
is a proof-strength gap in the *test*, or (T25) in what the *error
message* surfaces, not a missing safety mechanism.

1. **T01/T04/T05 — bootstrap failure injection is not exhaustive.** The
   spec names 6 injection points (snapshot, build×2, marker write, first
   `os.replace`, second `os.replace`, manifest write); tests exist for the
   *second* `build`/`os.replace` call, the count/FK/integrity gates, and
   the manifest write, but not for a `snapshot_source` failure, the
   *first* `build` call, marker-write itself, or the *first*
   `os.replace` call. T04 ("never exactly one target") has no standalone
   aggregate test — it is only implied by the individual "neither target"
   assertions in T01–T03's covered cases. T05 (retry after *each* failure
   type) is demonstrated for one failure type (2nd build) but not
   independently for the count-gate/FK-gate/publish-rollback paths.
2. **T02/T06/T07 — exit-code assertions are indirect.** Several bootstrap
   tests call `bootstrap.main()` in-process and assert a raised exception
   or unchanged files, rather than running the script via `subprocess` and
   checking the actual process exit code. The behavior these tests prove
   (refusal, no partial state) is real; "exit ≠ 0" / "exit 0" specifically
   is inferred, not measured, in those cases.
3. **T08b(a) — no test for "delete one target after success, then
   rerun."** Only the "foreign file at a target path" half of row 8b is
   exercised through `main()`'s CLI-level refusal path.
4. **T11/T14 — no single test exercises all four relation kinds
   (incoming link, outgoing link, `supersedes`, `superseded_by`)
   simultaneously through `search(include_artifacts=True)`.** Each kind is
   proven individually or in pairs; `superseded_by` in particular is only
   combined with the others via `get_related`, not `search`.
5. **T13 — `get_record`/`get_related` raise `TypeError`, not `BrainError`,
   for an omitted scope.** The §10 row's literal expected result is
   `BrainError`; only `search()` actually raises one. This is a real,
   pre-existing deviation between the row's expected shape and
   `brain/api.py`'s actual signature (a required keyword-only parameter
   raises `TypeError` by Python's own mechanism). Package 8's scope is
   tests/docs only — changing `api.py`'s exception type is a behavior
   change outside this package's mandate, so it is documented here rather
   than "fixed."
6. **T15 — no audit-log assertion for the `ControlStore.save` `ValueError`
   paths**, and no audit check at all for the `authorize()`-time denials
   (`brain/control.py` has no audit-log call on either path — this is a
   feature gap in the source, not a test gap; documented, not changed,
   since Package 8 does not touch `control.py`).
7. **T17a — no audit-log assertion.** The row's own audit column says
   "conflict audited" but no test checks the audit log content for this
   specific case (T17b/17c/17d don't require it — their audit columns are
   "—").
8. **T21 — "two levels deep in a dict value" has no real request-model
   field to reach it end-to-end.** Every reachable write field is flatly
   typed (`verification: dict[str,str]`, `evidence: list[str]`, etc.), so
   this shape is proven only at the `enforce_band_c()` unit level. This is
   arguably a strength of the schema (the shape can't occur in a real
   request), not a weakness of the test — but it is not what row 21's
   Setup column describes end-to-end.
9. **T24 — no single test combines an unknown-repo-alias artifact with
   `source_assertion="verified_tool_result"`, and no test directly queries
   `artifact_validation_state`/`artifact_validation_events` to confirm no
   `verified_*` row was written** (existing tests check the derived trust
   view only).
10. **T25 — the projector's error does not literally "name the record."**
    `ProjectorError` messages are fixed reason strings
    (`accepted_record_missing_fts_row`, etc.) with no `record_id`
    interpolated anywhere in `brain/projector/projector.py`, confirmed by
    reading `_assert_projected`/`_assert_removed`/`run_once`'s exception
    re-wrap. Fixing this would mean editing `projector.py` outside the
    `already_absent`/runtime-guard scope this package was given, so it is
    documented rather than changed. The safety property itself (raise,
    cursor unmoved, rollback) is fully proven; only the diagnostic text is
    short of the row's literal wording.
11. **T28 (stub pointer) — `test_control_store_write_grant_gate_is_the_row_28_scenario`
    is a docstring-only `assert True` stub**, not independent
    verification; the real proof is `test_write_grant_requires_an_existing_marked_journal`
    in `tests/test_brain_control.py`.

None of these gaps block Package 8's own exit criteria (§11: "all 28 rows
traceable and green") — every row has at least one real, passing test that
proves its core safety property; the gaps above are about *proof
completeness* against the row's full Setup/Action/Expected-columns
wording, not about an unproven safety mechanism.
