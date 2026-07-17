# Final Write-Rollout Acceptance Review — Pavol-Brain Write Safety & Integrity Repair Program

- **Reviewed commit:** `598a7a0` ("docs(brain): finalize rollout and migration readiness") on `main`
- **Baseline:** merge-base with `origin/main` = `10349b0` (i.e., only the final Package 9 docs commit is unpushed; Packages 1–8 are already on `origin/main`)
- **Reviewer:** Claude (Fable 5), independent final acceptance pass, 2026-07-17
- **Authority:** [write-safety-integrity-repair-spec.md](../architecture/write-safety-integrity-repair-spec.md) (§2 invariants I1–I12, §10 acceptance matrix, §11 closing commit map, §12 final acceptance checklist, §13 residual register)
- **Method:** complete final-state review. Full suite and acceptance runner executed; 65 matrix-mapped tests re-run individually across every package; all four Appendix A probes re-executed by this reviewer against a temporary fixture journal; the real `bootstrap_brain_instances.py` and `stamp_brain_instance.py` scripts executed as subprocesses on fixtures with measured exit codes; every closing commit hash resolved; every review document's verdict trail checked for historical preservation; runbook commands spot-checked against script argparse definitions; repository hygiene scanned (tracked files, secrets, absolute paths, dependencies). Closed implementation findings were **not** re-litigated; they were checked only for contradiction by the final documentation and evidence — none found.
- **Constraints honored:** no commit, no push, no live journal or retrieval DB read or written, no SSH to `mini-core`, no instance-marker backfill. Every executed probe/script ran against disposable fixtures in a session scratch directory.

---

## 1. Threat model

Unchanged from the spec and the Package 1 review, re-confirmed appropriate for this verdict:

- **In scope:** accidental cross-instance/cross-workspace data flow (Personal ↔ WORK), agent bugs (key reuse, malformed input, secret-bearing payloads), operator error during bootstrap/migration/rollback, crash windows mid-publish, corrupt or legacy journal rows (defense in depth on the read side), client attempts to self-assert trust or smuggle unscanned text into persistence, option-injection through client-controlled URI components.
- **Out of scope (accepted, documented):** a malicious actor with local filesystem write access (the instance marker is an operational label, not a cryptographic proof — R1/M-2, stated in the runbook's threat-model note); network adversaries (no public listener; local/SSH stdio MCP only).
- The single-operator local-machine assumption is stated where it matters (marker backfill runbook) and does not weaken any backup/verify/rollback discipline.

## 2. Invariant compliance I1–I12

No invariant is marked satisfied on "full suite passes" alone; each is tied to named, individually re-executed tests and/or probes.

| # | Invariant | Status | Evidence tie |
|---|---|---|---|
| I1 | Bootstrap atomicity | **SATISFIED** | T01–T04 (all 6 spec-named injection points + 3 gates + the T04 both-or-neither aggregate, 10 parametrized cases) re-run green; real-script full run on fixture: exit 0, both targets published, manifest `published: true` |
| I2 | Bootstrap retry safety | **SATISFIED WITH NON-BLOCKING RESIDUAL** | T05–T08b re-run green (incl. post-publish-write survival and forward-completion); real-script rerun after success: exit 0, both targets byte-identical (shasum-verified by this review). Residual: retry after count-gate/FK-gate failures not independently exercised (matrix limitation #1); exit codes mostly asserted in-process (R10) — the real-script runs here measured exit 0 paths directly |
| I3 | Instance isolation | **SATISFIED** (repo side) | T09/T09b re-run green (`BRAIN_INSTANCE_MISMATCH`/`BRAIN_INSTANCE_MARKER_MISSING` with journal byte-identity ⛁); stamp-script dry-run on a wrong-instance journal blocks with `marker_mismatch` (executed). **Live journals are not yet stamped — a pending operator prerequisite, not a repo defect** |
| I4 | Workspace/agent scope isolation | **SATISFIED WITH NON-BLOCKING RESIDUAL** | T11–T15 re-run green incl. the I10 property test; Appendix A probe 4 re-executed by this reviewer: corrupt cross-workspace supersede pointer FILTERED. Residual: R12 (`TypeError` not `BrainError` for omitted scope — fails closed either way), R7 (grant-surface shape asymmetry, doc-only) |
| I5 | Referential integrity | **SATISFIED** | T10a/T10b re-run green; Appendix A probes 1a/1b re-executed: dangling and foreign-workspace `record://` evidence both rejected (`BRAIN_INVALID_ARTIFACT_URI`, VARIANT A scheme ban) |
| I6 | Idempotency identity | **SATISFIED** | T16–T19 re-run green; Appendix A probe 3 re-executed: replay against a stored event stripped of `request_hash` → `BRAIN_IDEMPOTENCY_CONFLICT` (was silent success at baseline) |
| I7 | Canonical write-envelope filtering | **SATISFIED** | T20–T22 re-run green incl. the 17-field secret matrix and the field-classification lock-in; Appendix A probe 2 re-executed: dict-key secret rejected before any persistence |
| I8 | Secret non-persistence | **SATISFIED** | T20/T22 byte-grep assertions re-run green; this reviewer's independent canary write against a fixture journal: 0 canary hits in journal file bytes and 0 in the audit log after rejection |
| I9 | Artifact trust | **SATISFIED WITH NON-BLOCKING RESIDUAL** | T23a/T23b re-run green (self-assertion rejected at schema level; tampered state row detected and repaired by `rebuild_state`); Package 6 F1 (git option injection) closed with argument isolation + terminators, `test_f1_*` closure probes green. Residual: T24 proof-completeness gaps (matrix limitation #9, derived-view-only assertion) |
| I10 | Expansion never exceeds direct access | **SATISFIED** | `test_i10_every_expanded_id_is_directly_fetchable_with_same_scope` re-run green (mixed corrupt fixture: cross-workspace supersede, corrupt incoming link, dangling outgoing link) |
| I11 | Projector cursor safety | **SATISFIED WITH NON-BLOCKING RESIDUAL** | T25/T26 re-run green (FTS/embedding/link mutation each blocks the cursor, clean retry advances exactly once); skip reasons closed-enum with a `python -O`-safe runtime guard. Residual: R13 (error message does not interpolate the record id — diagnostics only) |
| I12 | Deterministic rebuildability | **SATISFIED** | T27 re-run green: live-written record appended, retrieval DB deleted, rebuild to `NO_CHANGES`, 13-column document row sets + embedding/FTS/link row sets + cursor final state equal, journal SHA-256 byte-identical |

## 3. Blocker reconciliation (§11 closing commit map)

All 20 commit hashes cited across the map, changelog, and review documents resolve in this repository with subjects matching their claimed roles (`e92566a`, `2ec71d8`, `59f97c4`, `958f9c3`, `3c60d02`, `82b45e0`, `7a55c24`, `fcbc2ee`, `9bef695`, `dac4b56`, `e8d1605`, `2700ba9`, `fa2e3b8`, `e42bc43`, `0e28eda`, `6e83fd7`, `249c146`, `10349b0`, `598a7a0`, plus historical `db948bb`). File stats spot-checked for `2ec71d8`, `fa2e3b8`, `0e28eda`, `10349b0`, `598a7a0` — each touches exactly the claimed areas.

**Verdict-trail preservation, verified in git history:**

| Package | Trail | Preserved? |
|---|---|---|
| 1 | `REVISE PACKAGE 1` → delta re-review `APPROVED FOR PACKAGE 2` | Yes — the `59f97c4→958f9c3` diff of the review doc is append-only (§11 delta added; §1–§9 untouched) |
| 2 | Read-only audit + §8 implementation verification, no blocking-findings cycle | Yes — original findings/decision §1–§7 marked unmodified |
| 3 | `APPROVED FOR PACKAGE 4` | Yes |
| 4 | `APPROVED WITH REQUIRED FOLLOW-UP` → F1/F2/F2b + live secret audit closed; F3/F5 (Low) explicitly still open | Yes — "original verdict above is preserved unchanged" |
| 5 | `APPROVED WITH REQUIRED FOLLOW-UP` → F1/F2 closed, F3 implemented; F4/F5 (Info) open | Yes |
| 6 | `REVISE PACKAGE 6` → F1 (HIGH)/F2 closed → delta `APPROVED FOR PACKAGE 7` | Yes — original verdict kept as historical record |
| 7 | `APPROVED FOR PACKAGE 8` | Yes |
| 8 | `APPROVED WITH REQUIRED FOLLOW-UP` → both required items closed in `10349b0`; "Verdict (unchanged)" | Yes |

No historical REVISE verdict was overwritten. **No blocker is falsely marked live-complete:** the B1/B2 row correctly carries **ROLLOUT PENDING** for the live marker backfill, and the runtime doc's manual/live evidence table marks the backfill, the controlled deploy, and the §12 checklist as **Pending**. Rollout-impact statements match the evidence in every row.

## 4. Executed commands and results (this review, 2026-07-17)

| Command | Result |
|---|---|
| `python scripts/run_brain_acceptance.py` | **245 passed + 21 subtests, 0 failed/errors/skipped, 68 deselected, exit 0** — matches the spec's claimed counts exactly |
| `pytest tests/ -q` (full suite) | **313 passed + 21 subtests, 0 failed, exit 0** — matches the claimed 313+21 at `10349b0` |
| 65 matrix-mapped tests re-run individually via `-k` (all packages, incl. §12 spot rows 7, 8, 10b, 12, 17c, 20, 25) | **65 passed** (18 + 17 + 30 across three selections) |
| `check_exported()` | **True** |
| `pytest tests/test_brain_mcp.py -k test_exact_tool_list_and_search_schema_parity` | **1 passed** — tool list pinned, unchanged |
| `git diff --check` | clean |
| `git status` | clean tracked tree; zero untracked files (before this review document) |
| Tracked-file hygiene | no live DB/backup/log/runtime-config files tracked (`.gitignore` excludes `*.db`/WAL/SHM; only `spike/dataset/records.jsonl` — synthetic fixture data); no secret-pattern hits outside test canaries/docs; no user-absolute paths in `brain/`, `scripts/`, `tests/`, `docs/{operations,architecture,reviews,integrations}` (pre-existing hits confined to historical `spike/`/`sqlite-spike/results/` evidence and `docs/design-system.md` — outside the rollout-critical path) |
| Graphiti in rollout-critical path | **absent** — `pyproject.toml` dependencies are `pydantic` + `mcp` only; the sole textual mention is a natural-language query string in `scripts/smoke_brain_consumer.py` |
| **Appendix A probes (re-executed, fixture journal)** | 1a dangling `record://` evidence → `BRAIN_INVALID_ARTIFACT_URI`; 1b foreign-workspace `record://` evidence → `BRAIN_INVALID_ARTIFACT_URI`; 2 dict-key secret → `BRAIN_INVALID_REQUEST` (shape constraint fires before Band C — see finding INFO-1); 3 missing-`request_hash` replay → `BRAIN_IDEMPOTENCY_CONFLICT`; 4 corrupt supersede pointer → **FILTERED** from scoped `get_related` |
| Canary byte-grep (independent, after probe-2 rejection) | 0 hits in journal file bytes, 0 hits in audit log bytes |
| `bootstrap_brain_instances.py --apply` on fixture (real subprocess) | Without exclusion manifest: **exit 2** (preflight blocked on the known rec-056 cross-partition reference — correct fail-closed). With digest-bound exclusion manifest: **exit 0**, both journals published, manifest `published: true`, both instance markers stamped. **Rerun after success: exit 0, both targets byte-identical (shasum-verified)** |
| `stamp_brain_instance.py` dry-runs (real subprocess) | Marked journal, matching instance: `already_stamped: true`, `blocked: null`, **exit 0**; wrong instance: `blocked: "marker_mismatch"` (stop condition) — both exactly as the runbook documents |

## 5. Acceptance matrix assessment

`tests/ACCEPTANCE_MATRIX.md` is **honest and accurate**:

- All 28 §10 rows and every sub-row (8b, 9b, 10a/10b, 17a–d) are represented with exact `module::test:line` mappings.
- No COVERED row rests on "full suite passes"; the traceability header count (52 mapping rows / 35 T-numbers / 65 test functions) was verified by direct count in the Package 8 review and is consistent with this review's sampling.
- PARTIAL/ADJACENT labels are honest — every PARTIAL names the exact unproven column; the one `assert True` stub (T28 pointer) is labeled ADJACENT with the real proof named beside it.
- The 11 Known limitations match the real code and tests (spot-verified: T13's `TypeError` deviation, T25's non-interpolated record id, T28's stub, T21's flat-schema reachability argument).
- The manual/live evidence section correctly separates operator-reported deliverables (Package 2/4 live audits) from automated coverage and correctly states the marker backfill and live deploy are **not executed**.
- This review's real-subprocess bootstrap runs partially close the R10 "inferred exit code" gap for the success and rerun paths (measured exit 0 twice, exit 2 for blocked preflight).

## 6. Migration and runbook assessment

**Instance-marker backfill runbook** (`docs/operations/brain-runtime.md`): covers all required elements — read-only preflight (Step 1), external checksum capture before mutation (Step 2), dry-run with explicit `--instance-id` (Step 3), backup-first apply with automatic digest-verified backup (Step 4), automatic post-stamp verification incl. canonical-table logical-digest comparison with restore-on-failure (Step 5), independent operator SQL verification (Step 6), logical-preservation spot-check (Step 7), idempotent rerun, manual rollback with forensic-copy-first, and five explicit stop conditions.

**Spot-check against `scripts/stamp_brain_instance.py`:** every documented flag exists (`--journal-db`, `--instance-id {personal,work}`, `--source-digest`, `--backup-path`, `--apply`); the default backup filename, the `blocked`/`already_stamped` report fields, the auto-restore-on-failure behavior, and the canonical-table digest check are all implemented as documented. **No unsupported command or flag is documented.** The dry-run and wrong-instance behaviors were reproduced by execution (§4).

**Deployment sequencing (adversarial evaluation):** the 11-step table is correctly ordered and each ordering constraint is justified:

- Write-capable agents and the projector stop at step 2, **before** code deploy (3) and migration (4); nothing write-capable restarts until step 9.
- Mixed-version runtime is prevented by construction (stop-all → atomic checkout → restart), with an explicit stop condition if any process cannot be confirmed stopped.
- No write-enabled process starts before marker verification (5) and retrieval validation (6); MCP write clients restart only after the projector is confirmed healthy (8→9).
- Personal/WORK crossing is guarded three ways: the marker itself, the documented cross-wiring verification (open each journal with the other instance's config → `BRAIN_INSTANCE_MISMATCH`), and the stamp script's workspace-partition preflight (stamping the wrong journal blocks on `workspace_partition_violation`/`marker_mismatch` — reproduced in §4).
- Retrieval DBs are validated after journal markers and repaired only by delete-and-rebuild.
- Smoke checks (step 7) run the acceptance suite, which uses only `tmp_path` fixtures and a runner that strips live-instance `BRAIN_*` env vars — safe on the deployed host.

**No step was found where following the runbook literally corrupts, overwrites, or exposes live state.** Two documentation nits were found (LOW-1, LOW-2 below); neither is unsafe — one fails loudly at argparse, the other is mitigated by the runbook's own forensic-copy-first instruction.

## 7. Rollback assessment

| Path | Viable? | Notes |
|---|---|---|
| Code rollback | Yes | `git checkout <previous SHA>`; safe at any point — old code ignores the additive marker table |
| Journal restoration | Yes | From the explicit `<journal>.pre-instance-stamp-backup.db` created by apply; manual path preserves the current journal as `.rollback-investigate.db` **first**, so no state is destroyed. See LOW-1 for a recommended clarification about post-deploy writes |
| Retrieval DB | Yes | Delete-and-rebuild is always safe and is the only supported repair (I12); explicitly never used to restore journal data |
| Control DB/profile | Yes | Operator CRUD (`write_enabled=False`/delete), independent of journal/retrieval artifacts |
| LaunchAgent | Yes | `bootout` + reinstall previous plists, or `bootout`+`bootstrap` cycle when only code changed |

The runbook explicitly states the journal is always source of truth, is never restored from or reconciled against the retrieval DB, and that hand-editing journal rows is never permitted. The rollback-vs-continue decision point is concrete and actionable (four named immediate-rollback triggers; one named continue case). Rollback remains possible throughout the defined window (backups from step 1 + pre-deploy SHA retained per the checklist's final box).

## 8. Residual findings register (R1–R13) — classification by this review

| # | Register entry | Classification |
|---|---|---|
| R1 | Marker is a copyable label (M-2) | **Safe to carry** — accepted threat model, documented in the runbook |
| R2 | `RuntimeInspector` no marker check | **Safe to carry** — health metadata only |
| R3 | Stamp backup `FileExistsError` ergonomics | **Safe to carry** — safe refusal (verified in `backup()`); manual resolution documented |
| R4 | Corrupt marker file crashes `classify_recovery` | **Safe to carry** — fail-closed crash, near-impossible via `write_json_atomic` |
| R5 | No `request_id` floor at audit sink | **Safe to carry** — every MCP entry point validates first; no other caller exists |
| R6 | Manual field-classification inventory | **Safe to carry** — lock-in test fails loudly on unclassified fields |
| R7 | Sensitivity-grant surface asymmetry (Info) | **Safe to carry** — no leak either direction (I10 test) |
| R8 | `_target_visible` N+1 connections (perf) | **Safe to carry** at current volumes |
| R9 | Vacuous-pass robustness note (Info) | **Safe to carry** — sibling assertion guards |
| R10 | Indirect exit-code assertions | **Safe to carry** — partially closed by this review's real-subprocess runs (exit 0 success/rerun, exit 2 blocked); remaining sub-cases proven by file-state assertions |
| R11 | Named test-completeness gaps | **Safe to carry** — each row's core property proven elsewhere |
| R12 | `TypeError` vs `BrainError` no-scope deviation | **Safe to carry** — documented, fails closed before any read |
| R13 | Projector error lacks record id | **Safe to carry** — diagnostics only; safety property fully proven |

**None must be completed before enabling writes; none is stale; no documentation correction is required within the register itself.** The register accurately reflects the open findings in the cited review documents (F3/F5 of Package 4 → R5/R6; F4/F5 of Package 5 → R7/R8; Package 1 L-1–L-3/M-2 → R2/R3/R4/R1).

## 9. Findings of this review, by severity

No Blocking, High, or Medium findings.

- **LOW-1 (documentation, recommended):** the "Rollback plan" § journal-marker-rollback paragraph does not state that restoring a journal from `.pre-instance-stamp-backup.db` *after live writes have landed* (post step 9) would remove those writes from the active journal (they survive only in the `.rollback-investigate.db` forensic copy the detailed runbook mandates). Since old code ignores the additive marker table, a late-window rollback should normally be code-only + profile-disable, without journal restoration. Recommend one clarifying sentence. Not blocking: the forensic-copy-first instruction prevents actual data destruction, and every rollback trigger in the decision point precedes meaningful write traffic.
- **LOW-2 (documentation):** the rollout checklist ("Retrieval" section) and deployment step 6 abbreviate the validate command as `run_brain_projector.py --instance-id <id> --validate`, omitting the required `--journal-db`/`--retrieval-db` arguments. The command as written fails loudly at argparse (no unsafe behavior); the full form appears in "Acceptance and diagnostic commands". Recommend expanding the abbreviated forms.
- **INFO-1:** Appendix A probe 2's "Required: `BRAIN_WRITE_SECRET_REJECTED`" does not match the current layered behavior for that exact probe: `api_key=sk-live-…` as a `verification` key is shape-invalid (`VerificationKey` pattern bans `=`) and is rejected as `BRAIN_INVALID_REQUEST` before Band C runs — the exact behavior the Package 4 review documented, probed, and accepted (with `__context__ is None` and zero leakage, re-confirmed here by byte-grep). A shape-*valid* secret-bearing key is proven to reject as `BRAIN_WRITE_SECRET_REJECTED` by T20. Fail-closed either way; wording-only mismatch.
- **INFO-2:** pre-existing `/Users/pavol` absolute paths in `spike/`, `sqlite-spike/results/` (historical spike evidence) and `docs/design-system.md` — outside the rollout-critical path; no action needed for this rollout.
- **INFO-3:** `main` is 1 commit ahead of `origin/main` (`598a7a0` unpushed). The rollout checklist's own first pre-deploy box requires a synced `main`, so a push is a prerequisite operator action before deploy.
- **INFO-4:** the acceptance runner strips a named list of 8 live-instance `BRAIN_*` variables, not every `BRAIN_*` variable (e.g. `BRAIN_INSTANCE` passes through). Defense-in-depth only — every acceptance test constructs its own explicit `tmp_path` config; no action required.

## 10. Pending live prerequisites (operator actions — expected rollout steps, not engineering blockers)

1. Push `598a7a0` (or the reviewed final SHA) to `origin/main` and deploy that exact checkout to `mini-core` per the controlled-deployment table.
2. Live Personal instance-marker backfill (`stamp_brain_instance.py --instance-id personal`, dry-run then `--apply`) on `mini-core`.
3. Live WORK instance-marker backfill (same, `--instance-id work`).
4. Live retrieval-DB marker validation for both instances; delete-and-rebuild any DB reporting a missing/mismatched marker.
5. Controlled deploy steps 5–9 (marker SQL verification, deployed-host acceptance smoke, projector restart, write-client re-enable) and the step 10 monitoring window.
6. Post-deploy smoke per the "Runtime"/"Post-deploy" checklist sections (cross-wiring verification, scoped-read smoke, idempotency replay smoke, artifact trust smoke), and §12 items 5–6 live evidence (markers on real files, cross-wired refusal against real files, read-only).

All six are documented, sequenced, and gated in the runbook/checklist. None indicates unfinished engineering.

## 11. Final verdict

**READY FOR CONTROLLED WRITE ROLLOUT**

The repository — code, tests, acceptance evidence, migration tooling, runbook, and rollback plan — is ready for the operator-controlled migration and deployment to `mini-core`. This verdict does **not** mean the live migration is complete: the live marker backfills, controlled deploy, and post-deploy smoke (§10 above) remain pending operator actions and are correctly documented as such throughout the repository. LOW-1/LOW-2 are recommended documentation touch-ups, not gates.

### Stop/go conditions for the operator

**GO** when all of the following hold, in order:
1. `main` pushed/synced; deployed checkout equals the reviewed SHA; `git status` clean on `mini-core`.
2. Step 1 backups (both journals + both retrieval DBs, `shasum -a 256` + copies) exist outside the deploy path.
3. All write-capable agents and both projector LaunchAgents confirmed stopped (hard stop condition if any process cannot be confirmed stopped).
4. Both backfill dry-runs report `blocked: null` and `already_stamped: false`.
5. Both applies report `stamped: true`; marker SQL verification shows exactly one row per journal with the directory-matching `instance_id`; Step 7 row counts unchanged; both `.pre-instance-stamp-backup.db` files retained.
6. Deployed-host acceptance runner and full suite green (exit 0).
7. First projector cycle per instance returns `HEALTHY`/`NO_CHANGES`.

**STOP (do not proceed; follow the documented rollback, never improvise):**
- Any backfill preflight/dry-run stop condition: `integrity_check` ≠ `ok`, non-null `blocked`, or a marker already present for the *wrong* instance (never force a re-stamp).
- The stamp script's automatic verification raises (it auto-restores; investigate before any retry).
- Any Step 7 canonical-table count deviates from the pre-backfill baseline.
- Marker SQL verification fails after apply.
- Red acceptance/full suite on the deployed host.
- Projector first run reports `REBUILD_REQUIRED`/`FAILED` after one rebuild-and-retry.
- Any `BRAIN_INSTANCE_MISMATCH`/`BRAIN_INSTANCE_MARKER_MISSING` from a live client during the monitoring window.
- Any state the recovery classifier calls `foreign_corrupted`/`corrupted` (exit 4) anywhere in the process.
- An isolated, explained `BRAIN_IDEMPOTENCY_CONFLICT` from a client replay is **not** a stop condition (§6 working as designed).

---

## Post-review documentation corrections

LOW-1 and LOW-2 were closed as documentation-only edits in the same commit that records this review:

- **LOW-1:** the "Rollback plan" § journal-marker-rollback in `docs/operations/brain-runtime.md` now carries an explicit warning that the pre-stamp journal backup is a safe rollback point only until new live write events land; that restoring it afterwards removes those events from the active journal; that a forensic copy of the current post-deploy journal must be made first; that the operator must explicitly decide between full rollback and preserving/reconciling the newer events; and that the retrieval DB is never a recovery source.
- **LOW-2:** the abbreviated `--validate` commands in `docs/operations/brain-controlled-rollout-checklist.md` ("Retrieval" section) and `docs/operations/brain-runtime.md` deployment step 6 now spell out the full argparse-required form (`--journal-db`/`--retrieval-db`/`--instance-id`) using the runbook's environment placeholders. The checklist's two stamp dry-run shorthands were expanded the same way (adding the required `--journal-db`), so every documented stamp/validate command is syntactically runnable as written.

No production or test code changed, and the rollout verdict above is unchanged.

*This review performed no commit, no push, no live journal/retrieval read or write, no SSH deployment, and no instance-marker backfill. All executed evidence ran against disposable fixture journals in a session scratch directory.*
