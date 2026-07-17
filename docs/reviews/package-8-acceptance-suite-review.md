# Package 8 Adversarial Review — Acceptance Suite Consolidation

- **Reviewed commit:** `249c146` ("test(brain): consolidate adversarial acceptance suite") — the only commit in scope; Packages 1–7 implementation was not re-reviewed except where a claimed test mapping required verification.
- **Baseline:** `6e83fd7` (Package 7 approval recorded)
- **Specification:** [write-safety-integrity-repair-spec.md](../architecture/write-safety-integrity-repair-spec.md) — §2 invariants I1–I12, §10 acceptance matrix, §11 Package 8, §12 final acceptance checklist
- **Primary artifact:** [tests/ACCEPTANCE_MATRIX.md](../../tests/ACCEPTANCE_MATRIX.md)
- **Reviewer:** Claude (Fable 5), independent adversarial pass, 2026-07-17
- **Method:** every test name cited by the matrix resolved against the checked-in tree; test bodies of every mapped test read and compared to the §10 row's Expected result / persistent state / audit columns; suites re-executed; new tests re-run against the pre-Package-8 projector code to independently confirm the red-before claim; every "Known limitation" independently re-derived from source rather than accepted from the matrix. Every claim below marked **[executed]** was reproduced by running code.

**Verdict: `APPROVED WITH REQUIRED FOLLOW-UP`**

The traceability core is sound: every test the matrix names exists at the claimed module (all 68 distinct test functions cited across 48 mapping rows / 35 distinct T-numbers), every spot-executed test runs and passes individually, no COVERED row rests on "full suite passes", PARTIAL/ADJACENT labels are honest, and the manual/live evidence table is truthful. Two factual errors inside the matrix document itself, plus the spec-enumerated bootstrap failure-injection gap, must be closed as follow-up. None of the eleven Known limitations is a rollout blocker — I verified this independently for each (details in §4), including reading the bootstrap code paths behind the four missing injection points.

---

## 1. Test execution results **[executed]**

| Check | Result |
|---|---|
| `python scripts/run_brain_acceptance.py` | **231 passed, 21 subtests passed, 68 deselected, exit 0** — exactly matches the changelog's claimed 231/68 split |
| `pytest tests/ -q` (full suite) | **299 passed + 21 subtests, 0 failed/skipped** — exactly matches the claimed 299+21 (8 net new over Package 7's 291) |
| `check_exported()` | **True** — no schema drift |
| MCP tool-list parity (`tests/test_brain_mcp.py::test_exact_tool_list_and_search_schema_parity`, full module) | **passed** (module: 6 passed) — 7-tool pin unchanged |
| 14 matrix-mapped tests spot-run individually by node id (incl. §12 spot rows 7, 8, 10b, 12, 17c, 20, 25 equivalents and all 7 new Package 8 projector tests) | **14 passed** — individually runnable as claimed |
| Runner exit-code propagation | verified by execution: a deliberately failing acceptance test → **exit 1**; empty selection → **exit 5**; both pass straight through `subprocess`/`SystemExit`. The summary line is parsed from pytest output but the exit code does not depend on the parse, so a parse miss cannot mask a failure |
| Red-before evidence for the Package 8 projector changes | independently reproduced: with `brain/projector/{projector,validation}.py` reverted to `6e83fd7`, **6 of the 7 new tests fail** (both orphan tests, both already-absent leftover tests, both skip-guard tests); the seventh (`..._returns_cleanly_when_truly_clean`) correctly passes on both versions as a behavior pin. Files restored; tree clean |
| Skip-guard under `python -O` | reproduced the matrix's manual claim: `_skip_reason` with a patched `CLOSED_SKIP_REASONS` raises `ProjectorError("skip_reason_not_closed:not_eligible")` under `-O` |

## 2. Acceptance matrix accuracy

**Mappings.** All 68 cited test functions exist in the claimed modules; cited line numbers match the actual `def` lines. Assertions were read for every mapped row and support the row's claim in each case — notable strong points, verified in the test bodies: T07/T08 (forward-completion with byte-identical targets and post-publish-write survival), T09/9b (byte-identity across all five mismatch tests, added this package), T10b (3 cases × 4 fields with per-case row-count deltas and an audit-log byte count of exactly 12 `BRAIN_INVALID_ARTIFACT_URI` lines), T17b–d (row counts plus byte-identical stored event), T20 (17 fields, per-field error-code + no-rows + closing byte-grep of journal and audit log), T26 (cursor advances to the exact `source_event_id`, then `NO_CHANGES`), T27 (13-column document row set + embedding/FTS/link row sets + 5-field cursor final state + journal SHA-256; the only excluded document columns are `doc_id` (rowid, legitimately nondeterministic) and content columns already pinned via the FTS set and `projection_hash = sha256(canonical_text)` — the "all documented deterministic columns" claim holds).

**No mapping relies on "full suite passes".** Confirmed row by row. The one `assert True` stub (T28 pointer) is labeled ADJACENT, not COVERED, and the real proof exists and was spot-executed.

**Two factual errors in the matrix document (required follow-up, documentation-only):**

1. **Red-test table, bootstrap row:** claims "No standalone Package 1 review doc found in `docs/reviews/`". False — [package-1-bootstrap-instance-binding-review.md](package-1-bootstrap-instance-binding-review.md) exists (verdict `REVISE PACKAGE 1`, with executed probes and a recorded repair pass at `59f97c4`/`958f9c3`). The matrix *understates* the available red-test evidence for T01–T09b; the correct citation should replace the changelog-only one.
2. **Known limitation 6:** claims "`brain/control.py` has no audit-log call on either path — this is a feature gap in the source". False for the `authorize()` path: `RegistryPolicy._deny` ([control.py:160-163](../../brain/control.py)) writes a `policy_denial` audit event with the error code for **every** denial (`BRAIN_WORKSPACE_DENIED`, `BRAIN_SENSITIVE_SCOPE_DENIED`, `BRAIN_INSTANCE_DENIED`, …) whenever an audit logger is wired — and `mcp_server.py:65` wires `brain.audit`. The §10 row 15 "policy denial audited" column is *implemented*; what is missing is only a test assertion (the unit tests construct `RegistryPolicy` without an audit logger). Only the `ControlStore.save` `ValueError` path genuinely has no audit, which is a local-operator action. The matrix mislabels a test gap as a source feature gap.

**Mapping-count note:** the matrix contains 48 T-numbered mapping rows over 35 distinct T-numbers (plus 7 unnumbered rows for the Package 7 follow-ups); all were verified. No natural count yields exactly 37; nothing is missing against §10's 28 rows + sub-rows.

## 3. Acceptance runner and marker

- The `acceptance` marker is applied as module-level `pytestmark` in exactly the 6 modules the matrix names; no other file in the repo carries the marker **[executed: repo-wide grep]**.
- All 68 §10-mapped test functions live inside those 6 modules — nothing §10-mapped is outside the selection.
- **Over-inclusion, not under-inclusion:** module-granularity marking sweeps in every test in those modules (231 selected vs ~68 mapped), so the acceptance gate is a strict superset of the §10 suite — it can only fail more, never fewer. Acceptable; worth a one-line note in the matrix.
- Legacy spike suites (`spike/tests/`, `sqlite-spike/tests/`) sit outside `tests/`; the runner invokes `pytest tests/` explicitly; there is no `conftest.py`, `testpaths`, or `addopts` anywhere in the repo that could widen collection **[executed: find/grep]**. 0 skips in the full run confirms no optional-dependency experiments are being silently skipped inside `tests/`.
- The runner's `BRAIN_*` env-strip covers every path-bearing config variable (`BRAIN_STATE_DIR`, all four journal/retrieval DB vars, `BRAIN_JOURNAL_DB`/`BRAIN_RETRIEVAL_DB`, `BRAIN_AUDIT_LOG`). Informational: `BRAIN_REPO_ROOTS_JSON` (read-only verifier roots) is not stripped; every acceptance test constructs its own `tmp_path` config, so this is defense-in-depth breadth only.

## 4. The eleven Known limitations — independent classification

Per instruction, none of the matrix's "none is blocking" claims was accepted without checking. Classifications: **RB** = rollout blocker, **AB** = acceptance blocker (§12), **PF** = proof-strength follow-up, **DOC** = documentation-only.

| # | Limitation | Class | Independent verification |
|---|---|---|---|
| 1 | 4 of 6 bootstrap injection points untested (snapshot, first build, marker write, first `os.replace`); no aggregate T04 test; T05 retry proven for one failure type | **PF — required** | Not RB: I read `main()`'s apply block — staging files are removed in a shared `finally` regardless of failure point; a marker-write failure leaves no marker (atomic write) and no targets; a first-`os.replace` failure triggers `marker.unlink` in the `except` and publishes nothing; snapshot/first-build failures precede any target-path mutation. All four untested points are safe by construction, and the *tested* points (2nd build, gates, 2nd replace, manifest write) are the genuinely stateful ones. But §10 row 1's Setup column and I1's "Failure injection after every step" explicitly enumerate all six, so row 1 cannot honestly be called implemented until they exist. T04's aggregate check is implied by the per-test "neither target exists" assertions and is trivial to add. Tests-only, required before §12. |
| 2 | Exit-code assertions indirect (in-process `main()`) | **PF — optional** | Where §10 names a specific code the tests do assert it (`SystemExit` code 2/3/4); "exit 0" is inferred from a normal return, which is faithful to the script's `raise SystemExit(main(...))`-free structure. §12 item 5 executes the scripts live anyway. |
| 3 | T08b(a) "delete one target after success, rerun" untested | **PF — optional, cheap** | Code-verified: marker gone + `published: true` manifest + one missing target → `classify_recovery` returns `live` (digest match fails) → `BLOCKING_CLASSIFICATIONS` → exit 3, refusal, no deletion, offending state in the printed report. The `live` refusal path itself is tested (`test_live_instance_is_never_treated_as_a_reset_target`). One cheap test closes it. |
| 4 | No single test with all four relation kinds simultaneously via `search` | **PF — optional** | All four kinds are individually proven through `search` (incl. the new `superseded_by` search test); the filter (`_scope_related`) is a uniform per-row rule, so combinatorial interaction risk is low; the I10 property test already combines three kinds plus malformed URIs in one search. |
| 5 | T13 `TypeError` vs `BrainError` for omitted scope | **DOC** | This is a spec-internal inconsistency, not an implementation defect: §3 B5's own recommended fix is "Scope becomes a required keyword-only argument", which by Python's mechanism raises `TypeError` — the shipped behavior. Row 13's "BrainError" wording contradicts B5. Fails closed before any read either way (verified: `search` raises `BRAIN_UNKNOWN_WORKSPACE` with zero embedding calls). Needs a Package 9 changelog note reconciling row 13's wording; changing `api.py` was correctly out of Package 8's scope. |
| 6 | T15 audit gaps | **DOC — matrix text is wrong (required correction) + PF optional** | See §2 error 2: `authorize()` denials ARE audited in source; only the test assertion (and `save()`-path audit, a local operator action) is missing. |
| 7 | T17a conflict-audit not asserted | **PF — optional** | `brain/api.py:191` audits `error_code` on every failed `record_*` call — the exact mechanism whose on-disk bytes T10a/T10b now assert. The conflict is therefore audited by code shared with byte-proven paths; only a specific assertion is missing. |
| 8 | T21 nested shapes unreachable end-to-end | **DOC** | Verified: every request field is flatly typed, so "two levels deep in a dict value" cannot arrive via any public write; the scanner walk is unit-proven (`collect_client_strings` + three `enforce_band_c` shapes). Schema strength, honestly described. |
| 9 | T24 no unknown-alias × `verified_tool_result` combination; trust checked only via derived view | **PF — optional, cheap** | `classify()` requires a real `verify_all` hit for Band A and an unknown alias yields `repo_unavailable`; adjacent tests cover missing-repo × `verified_tool_result` (→ candidate/B) and unknown-alias × user-confirmation (→ `unverified_reference`). The combination and a direct `artifact_validation_state`/`events` no-`verified_*` query are cheap adds. |
| 10 | T25 error does not name the record | **DOC** | Verified in `projector.py`: `ProjectorError` reasons are fixed strings. The safety property (raise, cursor unmoved, rollback, retry-once) is fully proven; only the diagnostic falls short of row 25's audit-column wording. Fixing it is a `projector.py` behavior change correctly left out of this package; candidate for Package 9. |
| 11 | T28 `assert True` stub | **DOC** | Honestly labeled ADJACENT; harmless. Optional cleanup: delete it or make it assert the referenced test function exists. |

**Rollout blockers still open from this list: none.** **Acceptance blockers (§12 as written): none** — §12's spot-execute rows (7, 8, 10b, 12, 17c, 20, 25) and item 5's bootstrap scenarios are all covered today. Item #1 above is required to make the §10 row-1 traceability claim honest before §12 runs.

## 5. Package 7 follow-ups **[executed]**

- **Orphan FTS / orphan link detection:** both `validate()` checks present; both mutation tests construct the corruption FK-off, assert `REBUILD_REQUIRED` with the named issue, and assert row count *and content* unchanged (no auto-repair). Red-verified against `6e83fd7` (checks absent → tests fail).
- **`_remove` already-absent path:** now calls `_assert_removed(con, record_id, None)`; leftover embedding and link rows are caught (both red-verified); the FTS check is correctly skipped when `doc_id is None` (unrecoverable) and is covered by `orphan_fts` on the next `run_once()`/`status()` — consistent with the Package 7 review's I11 framing.
- **Skip-reason guard:** explicit `raise ProjectorError`, AST-pinned (no `ast.Assert`, reachable `ast.Raise`), functionally tested, and re-confirmed under `python -O` by this review.
- **Rebuild comparison:** widened to 13 document columns + 5-field cursor final state; verified complete against the actual `retrieval_documents` DDL (spike schema + 4 migration columns = 18 columns; the 5 not compared directly are `doc_id` and the four content columns pinned via FTS set + hash).
- All four items match what the Package 7 review explicitly deferred ("Suggested for Package 8/9", "consider `ProjectorError`", F3's "fix the 21", F4's "worth widening in Package 8").

## 6. Manual/live evidence truthfulness

Verified against the underlying documents — all four representations are truthful:

- **Package 2 live record-reference audit: genuinely completed** ([package-2-record-reference-audit.md](package-2-record-reference-audit.md) §2 records the `scp` copies of both live `mini-core` journals plus legacy, read-only query discipline, and zero B3-style rows).
- **Package 4 live secret audit: genuinely completed** (spec §11 Package 4 status: operator ran the tool against `scp`'d copies, 0 findings).
- **Package 1 instance-marker live backfill: correctly reported as NOT executed** (independently corroborated by the Package 2 audit's §2.2/§6, which found `brain_instance_identity` present but empty on both live journals).
- **Live deploy / §12 final acceptance: correctly reported as not executed** and out of Package 8's scope.

The matrix's closing warning ("Do not read the above as live rollout is done") is accurate and appropriately prominent.

## 7. Red-test evidence

Grounded, with one correction: Packages 2–7 rows cite real probe re-run tests (`test_b3_*`, `test_b4_*`, `test_b6_*`, `test_b7_*`, the B8 legacy-hash test) and real review documents whose verdicts and findings I confirmed on disk. Package 8's own red claim was **independently reproduced by this review** (§1). No invented before/after execution evidence found. The one defect is §2 error 1 — the bootstrap row wrongly claims no Package 1 review doc exists; the citation should be corrected, which *strengthens* the evidence rather than weakening it.

## 8. Diff hygiene **[executed: full diff read]**

- Production changes confined to `brain/projector/validation.py` (two `validate()` checks) and `brain/projector/projector.py` (`already_absent` → `_assert_removed(None)` + `doc_id is not None` guard, `_skip_reason` raise) — exactly the two Package 7 Low follow-ups, nothing more.
- No writer/API/MCP change (none of `brain/writer.py`, `brain/api.py`, `brain/mcp_server.py`, `brain/models.py` touched); MCP tool list pinned green.
- No schema change (`check_exported()` True; no journal/retrieval DDL touched; `pyproject.toml` gains only the pytest marker registration).
- No live data: all tests use `tmp_path`; the runner additionally strips live env paths; no live journal path appears in the diff.
- No feature creep: remaining files are tests, the matrix, the runner, and spec §11/changelog/B10-status doc updates. The spec edit also corrects Package 7's "21 new tests" → "14 new test methods" per the Package 7 review's F3, without touching any review document's findings — confirmed.

## 9. Required follow-up (before Package 9 / §12)

1. **Bootstrap injection completion (tests only):** add failure-injection tests for `snapshot_source`, the first `build()`, the marker write, and the first `os.replace`; an aggregate T04 "both targets or neither" assertion over the row 1–3 scenarios; and retry-after-failure coverage for the gate/publish failure types (T05). Update matrix rows T01/T02/T04/T05 accordingly.
2. **Matrix corrections (doc only):** fix the red-test table's Package 1 citation (the review doc exists) and rewrite Known limitation 6 to state that `authorize()` denials are audited in source (`RegistryPolicy._deny`) and only the test assertion (plus `save()`-path audit) is missing.

Optional (non-blocking, cheap): limitations 3, 7, 9; a matrix note on the module-granularity marker over-selection.

## 10. Scope discipline of this review

No commit or push was created. The only file added is this review document. The temporary red-check (`git checkout 6e83fd7 -- brain/projector/...`) and the exit-code probe test file were fully reverted/removed; `git status` shows a clean tracked tree plus this untracked file.

## 11. Required follow-up verification (closes §9's items)

Both §9 follow-up items were closed in a tests/docs-only follow-up pass on top of this commit (`249c146`). No writer/API/MCP/schema change; no Package 9 work; no live write.

**1. Bootstrap injection completion.** All four previously-missing §10 row 1 injection points now have a dedicated test in `tests/test_brain_instance_bootstrap.py`, each asserting source-byte-identity, no unverified target overwrite/delete, phase-correct staging cleanup, correct marker/state-machine behavior, and a successful clean retry:

- `test_snapshot_failure_before_staging_leaves_no_targets_and_retry_succeeds` — `snapshot_source` failure
- `test_first_build_failure_cleans_staging_and_retry_succeeds` — first (`personal`) `build()` failure, as distinct from the pre-existing second-build test
- `test_marker_write_failure_leaves_no_marker_and_no_targets_retry_succeeds` — marker-write failure; asserts `classify_recovery` returns `fresh` afterward, not a recovery state
- `test_first_publish_replace_failure_removes_marker_and_staging_retry_succeeds` — first (`personal`) `os.replace` failure, as distinct from the pre-existing `publish_pair`-level second-replace test

`tests/ACCEPTANCE_MATRIX.md` row T01 now cites all 6 spec-named injection points (previously 2 of 6 plus the 3 gate tests); it stays **PARTIAL**, not COVERED — the remaining, honestly-documented gap is the pre-existing indirect/inferred-exit-code caveat (Known limitation #2), not missing coverage. T05 (retry after each failure type) is now independently demonstrated for snapshot/first-build/second-build/marker-write/first-replace and stays **PARTIAL** only for the count-gate/FK-gate retry path, which still isn't independently exercised.

**T04 aggregate result.** New parametrized test `test_T04_every_bootstrap_failure_injection_never_leaves_exactly_one_target_unless_recoverable_partial` (10 cases) runs every row 1–3 failure-injection scenario (snapshot, first/second build, count-mismatch gate, FK gate, marker write, first/second `os.replace`, manifest write) and asserts **both target journals exist or neither does** in 9 of the 10 cases. The 10th case — a hard crash (`KeyboardInterrupt`, bypassing `publish_pair`'s own `except Exception` rollback exactly as an uncatchable process kill would) landing between the two `os.replace` calls — is the one scenario the §4.3 state machine deliberately classifies as `recoverable_partial` (exactly one target published). For that case the test seeds a legitimate post-publish write into the surviving target (mirroring `test_B1_half_published_target_with_post_publish_write_is_never_clobbered_by_retry`) and asserts the retry refuses (`SystemExit` code 4), touches neither target, and preserves the marker — fail-closed, never a silent overwrite. Row T04 is now **COVERED**.

**2. Matrix corrections.**
- The red-test evidence table's bootstrap row no longer claims no Package 1 review doc exists; it now cites `docs/reviews/package-1-bootstrap-instance-binding-review.md` by path (verdict `REVISE PACKAGE 1`, executed before/after probes, repair pass at `59f97c4`/`958f9c3`).
- Known limitation 6 no longer claims `brain/control.py` has no audit call on either path. It now states that `RegistryPolicy._deny` (`brain/control.py:160-163`) writes a `policy_denial` audit event for every `authorize()`-time denial whenever an audit logger is wired (`mcp_server.py:65` wires it), and that only the *test assertion* for this is missing — plus that `ControlStore.save`'s `ValueError` path (a local-operator action) genuinely has no audit call in source.
- Added an explicit traceability-count note to `tests/ACCEPTANCE_MATRIX.md`'s header, verified by direct count rather than estimated: **52 mapping rows** over **35 distinct T-numbers**, citing **65 distinct test functions** (up from 48/35/60 before this follow-up, reflecting the 4 new individual tests + 1 new T04 aggregate test). No natural count of any of these three quantities is 37.

**Verdict (unchanged):** `APPROVED WITH REQUIRED FOLLOW-UP` — both required items are now closed; this section records their closure rather than revising the verdict itself.
