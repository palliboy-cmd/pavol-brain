# Package 1 Adversarial Review — Bootstrap Atomicity, Recovery, and Instance-Marker Binding

- **Reviewed commit:** `2ec71d8` ("feat(brain): bootstrap atomicity, recovery, and instance-marker binding (Package 1)")
- **Baseline:** `e92566a` (spec baseline; 124 tests + 5 subtests)
- **Specification:** [write-safety-integrity-repair-spec.md](../architecture/write-safety-integrity-repair-spec.md) — blockers B1/B2, invariants I1/I2/I3, §4 state machine, §10 rows 1–9b/15/28
- **Reviewer:** Claude (Fable 5), independent adversarial pass, 2026-07-15
- **Method:** full spec re-read; full diff `e92566a..2ec71d8` read file-by-file; full and targeted test runs re-executed by the reviewer; seven adversarial probes executed against temporary journals (none against live data — no live instance journals exist on this host); every claim below that says **[executed]** was reproduced by running code, not by reading it.

**Verdict: `REVISE PACKAGE 1`** — one Blocking finding (B-1, an executed data-loss reproduction that violates I2 inside the package's own core deliverable) and two High findings. All three have small, contained fixes; nothing in the design needs rework.

---

## 1. Threat model

In scope: **accidental** hazards on a single-operator local machine — crashes at any instruction boundary (power loss, kill), operator retries and dry-runs in any order, mis-wired launcher environments, restored backups or unrelated files landing at target paths, concurrent legitimate writers (MCP agents, the 300-second LaunchAgent projector) during recovery windows, and operational scripts run exactly as documented.

Out of scope: a **malicious local actor with filesystem write access**. Such an actor can forge the marker file, the manifest, or the journals themselves with equal ease — no artifact in this design carries authenticity stronger than local FS trust, and the spec does not claim otherwise. Findings involving forgeability are therefore graded for their *documentation* value, not as privilege escalations.

## 2. Tests and probes executed by the reviewer

| Run | Result |
|---|---|
| Full suite (`pytest tests -q`) | **151 passed + 5 subtests, 0 failed, 0 skipped** (baseline 124+5; +27 tests) |
| Package 1 files (`test_brain_instance_bootstrap.py`, `test_stamp_brain_instance.py`, `test_brain_control.py`) | 48 passed |
| Targeted `-k` selection: instance mismatch, missing marker, retrieval marker, write grant, projector stamping | 11 passed |
| **Probe 1 [executed]:** hard crash (BaseException) between the two `os.replace` calls → post-publish write lands → retry | **write destroyed, retry exits 0** → finding B-1 |
| **Probe 2 [executed]:** completed bootstrap → plain dry run (no `--apply`) → `--apply` rerun | manifest `published` erased; classification degraded `already_bootstrapped` → `incompatible_existing_state`; formerly idempotent rerun now exits 3 → finding H-1 |
| **Probe 3a [executed]:** fully-formed forged journal pair carrying a copied `(instance_id, source_digest)` | classified `crash_after_publish_before_manifest` — the identity label is copyable → finding M-2 |
| **Probe 3b [executed]:** pair where one side does not identity-match | `foreign_corrupted`, nothing deleted — pair consistency holds |
| **Probe 4 [executed]:** cross-wired `personal` config against a WORK-marked journal via `JournalWriter`, `Repository.journal()`, and `ProjectionProjector` | all three refused with `BRAIN_INSTANCE_MISMATCH` |
| **Probe 5 [executed]:** unmarked journal, `personal` config | `BRAIN_INSTANCE_MARKER_MISSING` |
| **Probe 6 [executed]:** index built the way `build_brain_m1_indexes.sh` invokes the projector (no `--instance-id`, no `BRAIN_INSTANCE`) → read by `personal` runtime and by the `personal`-configured projector | build silently succeeds under `legacy` exemption, retrieval marker is `None`, then **both the runtime read and the properly-configured projector refuse with `BRAIN_INSTANCE_MARKER_MISSING`** → finding H-2 |
| **Probe 7 [executed]:** `scripts/smoke_brain_m1_write.py` against fresh disposable paths (documented runbook step) | exits 1 at the new `ControlStore.save` gate → finding M-3 |
| Stamp-script suite (9 tests: dry-run purity, apply+backup, idempotent rerun, wrong-instance refusal, mixed-workspace refusal, corrupt refusal, explicit digest, backup-restore-on-failure) | all passed within the suite runs above |

Foreign-file-never-deleted and one-genuine-one-foreign scenarios are additionally covered by committed tests (`test_classify_recovery_never_deletes_a_foreign_file_it_cannot_verify`, `test_incompatible_existing_state_refuses_without_deleting`, `test_exactly_one_target_present_is_incompatible_existing_state`) which the reviewer re-ran.

## 3. Findings

### BLOCKING

**B-1 — A half-published journal that has received a post-publish write is silently clobbered by the retry (I2 violation). [executed]**

Reproduction (Probe 1): hard crash between `os.replace` #1 and #2 in `publish_pair` (personal published, work absent, marker present, manifest absent) → a legitimate write commits into the published personal journal (its marker is valid, so `JournalWriter`/the LaunchAgent projector accept it — nothing consults manifests) → operator retries `--apply`. Result: the retry **completes with exit 0 and the committed write is gone**.

Mechanism:
- `classify_recovery` classifies the half-published state as `recoverable_partial` ([bootstrap_brain_instances.py:404-415](../../scripts/bootstrap_brain_instances.py)): personal is identity-ours (line 402) so it is not "unexpected" (line 411), work is absent so it is not "unexpected" (line 412), and the both-exist condition for forward-completion (line 404) fails → falls to line 415.
- `cleanup_recoverable_partial` ([:418-427](../../scripts/bootstrap_brain_instances.py)) then **correctly refuses to delete** the personal journal (its digest diverged from the staged digest — the exact-match rule works as designed).
- But `main()` ([:483-487](../../scripts/bootstrap_brain_instances.py)) unlinks the marker and **falls through to a full FRESH build regardless of whether cleanup actually cleared the targets**. The pre-Package-1 "targets must not exist" guard was removed from line 461 (necessarily, for `already_bootstrapped`), and nothing between line 487 and `publish_pair` (line 590) re-checks target absence. `os.replace` overwrites silently.

This is equivalent-by-effect to the unverified deletion I2 forbids ("recovery never deletes a file whose content it has not verified") — the file is not unlinked, it is overwritten, and the run reports success. The committed test `test_cleanup_recoverable_partial_deletes_only_the_digest_matching_target` verifies the deletion rule but never the fall-through, so the suite is green over this hole. Probability is low (crash must land between two adjacent syscalls, then a write must land before retry — the LaunchAgent makes the second half routine), but the consequence is silent destruction of committed canonical data inside the exact code path Package 1 exists to make safe.

Also note: the same missing pre-publish check is the answer to review question 10 (TOCTOU) — a target appearing between classification and `publish_pair` (a window of several seconds during build) is clobbered identically.

**Required fix (small):** after `cleanup_recoverable_partial`, re-inspect both targets; if either still exists, print the classification report and exit 4 — never proceed. Belt-and-braces: immediately before `publish_pair`, assert both target paths are absent and refuse otherwise (closes the classification→publish TOCTOU for every path into the build). Add Probe 1's scenario as a regression test.

### HIGH

**H-1 — A plain dry run destroys the published manifest, corrupting the recovery state machine's own input. [executed]**

`main()` ends with `if a.manifest and not report.get("published"): write_json_atomic(a.manifest, report)` ([:597-599](../../scripts/bootstrap_brain_instances.py)). A dry run (no `--apply`) never short-circuits on classification — all recovery actions are gated behind `if a.apply:` ([:467](../../scripts/bootstrap_brain_instances.py)) — so it proceeds to preflight and **overwrites the real manifest with a report that lacks `published: true` and `result_journal_digests`**. Probe 2: after one innocent dry run over a completed system, classification degrades from `already_bootstrapped` to `incompatible_existing_state` and the formerly idempotent `--apply` rerun exits 3. No data is lost (everything refuses — fail-safe direction), but the publish-authority audit record is destroyed and I2's "reports already bootstrapped with exit 0" is permanently broken for that deployment. The overwrite is pre-existing behavior, but Package 1 promoted the manifest from a report into a **load-bearing state-machine input**, which turns the old cosmetic quirk into state corruption.

**Required fix (small):** never overwrite a manifest whose stored copy says `published: true` from a non-publishing run — write dry-run output to a sibling path (e.g. `<manifest>.preflight.json`) or refuse with a message. Regression test = Probe 2.

**H-2 — The documented index-build flow produces a poisoned retrieval index. [executed]**

`scripts/build_brain_m1_indexes.sh` (untouched by the commit) invokes `run_brain_projector.py` without `--instance-id` and without exporting `BRAIN_INSTANCE` ([build_brain_m1_indexes.sh:13-14](../../scripts/build_brain_m1_indexes.sh)); the flag defaults to `legacy` ([run_brain_projector.py:24-25](../../scripts/run_brain_projector.py)). Probe 6: the build **silently succeeds** (legacy is exempt, so it neither stamps nor detects the cross-instance read of a personal-marked journal), producing a non-empty retrieval DB with no marker. The `personal` runtime (`Repository.retrieval()`) and the properly-configured LaunchAgent projector then both refuse it with `BRAIN_INSTANCE_MARKER_MISSING` — by design, a non-empty marker-less index is never adopted ([instance_identity.py:153-155](../../brain/instance_identity.py)). The commit changed the projector's contract but not the documented script that drives it: following the runbook as written now yields a broken deployment (repair exists — delete + rebuild with the flag — but the flow should not self-break).

**Required fix (one line):** add `--instance-id "$instance"` to the projector invocation in `build_brain_m1_indexes.sh`; update the runbook example if needed.

### MEDIUM

**M-1 — Forward-completion writes a thin manifest and skips the spec's re-derived report; FK and workspace-partition are not re-verified.**

`forward_complete_from_marker` ([:430-446](../../scripts/bootstrap_brain_instances.py)) records only marker data plus `recovered_from`/`recovered_at`. Verified against the review checklist: **integrity is checked** (implicitly — `inspect_target` only reads the identity marker when `PRAGMA integrity_check` is `ok`, [:317-320](../../scripts/bootstrap_brain_instances.py)); **marker singleton** is schema-enforced (`PRIMARY KEY CHECK(singleton=1)`); **FK check and workspace-partition re-verification are not performed**; and the manifest does **not** record any current-state observation (current sha256/digest/integrity result at recovery time). Recording the *staged* digests as `result_journal_digests` is a defensible, even load-bearing choice (it is what makes later `live` detection work), but §4.3 row 6 says "write manifest from marker **+ re-derived report**" and that half is missing. Not data-endangering (this branch never modifies targets), but the recovered manifest is materially weaker evidence than a normal one.

**Recommended fix:** add a `recovered_observation` block (per-target current sha256, integrity result, `PRAGMA foreign_key_check` outcome, marker row) to the forward-completed manifest.

**M-2 — `(instance_id, source_digest)` is a copyable label, not proof of ownership. [executed]**

Probe 3a: a fully-formed forged journal pair (all canonical tables present, marker row copied from the plaintext `.publish-pending` file) classifies as `crash_after_publish_before_manifest`, and forward-completion would bless it with a `published: true` manifest. Within the declared threat model this is acceptable — an actor who can plant those files can forge the manifest directly — and the blast radius is bounded: nothing is deleted, and the blessed manifest's staged digests won't match the forged content, so every subsequent run classifies `live`/`corrupted` and refuses. Two footnotes worth keeping in mind: (a) an *accidental* near-collision is plausible only via a restored backup of this same instance, which is semantically "ours" anyway; (b) the first forgery probe failed for an accidental reason — the forged file lacked `artifact_validation_events`, so `logical_digest` raised and the marker was never read ([:318-322](../../scripts/bootstrap_brain_instances.py)) — i.e., some of the current rejection power is incidental table-shape coupling, not deliberate verification.

**Recommended (documentation, not code):** state in the runbook that the identity marker is an operational label under local-FS trust, not an authenticity mechanism. Optional cheap hardening for a future package: a per-run random nonce recorded in both the marker file and the stamped rows.

**M-3 — `scripts/smoke_brain_m1_write.py`, a documented rollout step, is broken by the new gate. [executed]**

The runbook's rollout order includes "`RegistryPolicy` write smoke against disposable staging journals". Probe 7: the script now dies at `ControlStore.save` because the gate checks `instance_paths(brain_instance)` — the *real* instance journal, not the smoke's disposable one ([control.py:96-108](../../brain/control.py), [smoke_brain_m1_write.py:23-27](../../scripts/smoke_brain_m1_write.py)); even with env redirection it would fail again at the writer, since the disposable journal is created unstamped. The gate itself is correct; the smoke tool was not updated alongside it.

**Required fix (small):** stamp the disposable journal in the smoke script and point `BRAIN_PERSONAL_JOURNAL_DB`/`BRAIN_WORK_JOURNAL_DB` at it for the duration of the run.

### LOW

**L-1 — `RuntimeInspector` reads journal and retrieval DBs with no marker check** ([runtime.py:14-20](../../brain/runtime.py)). Health/metadata only, no record content; but a mis-wired health endpoint will happily report a foreign instance's statistics, slightly *reducing* the detectability of the exact mis-wiring B2 is about. Consider a `diagnose`-style field in the health report rather than a hard refusal.

**L-2 — Stamp script: interruption between backup and stamp leaves a stale backup that blocks retry** ([stamp_brain_instance.py:77-79](../../scripts/stamp_brain_instance.py) raises `FileExistsError` — safe refusal, but an unguided traceback the operator must resolve by hand). Additionally `backup()`'s digest comparison ([:85-88](../../scripts/stamp_brain_instance.py)) is `!=` over possibly-`None` values — `None == None` would pass; unreachable in practice because preflight already proved the digest non-`None`, but it lacks the strictness `_digest_matches` established. Cosmetic hardening.

**L-3 — A corrupt/truncated marker file crashes classification with a raw `json.JSONDecodeError`** ([bootstrap_brain_instances.py:366](../../scripts/bootstrap_brain_instances.py)) instead of a clean `corrupted` classification. `write_json_atomic` makes this near-impossible to produce; still, the classifier should not except on its own inputs.

## 4. The digest-vs-marker deviation, assessed

The implementation's split — **exact content digest for deletion, immutable identity marker for forward-completion** — is the right resolution of the spec's internal contradiction, and this review confirms the contradiction is real: §4.3 row 6's literal "digests == `M.staged`" cannot hold simultaneously with row 8's "post-publish write survives", because any legitimate write changes the content digest by construction. Answers to the ten mandated questions:

1. **Sufficient proof of ownership?** Against accident: yes in practice (garbage and foreign files fail integrity or lack the row; a colliding file would have to be a valid journal deliberately stamped with this instance and this exact source digest). Against forgery: no (Q2). The asymmetry is correct: the weaker proof gates only the non-destructive action.
2. **Copyable/forgeable?** Yes — demonstrated [executed], Probe 3a. Bounded by the threat model (§1); finding M-2.
3. **Pair verified as a consistent pair?** Yes — forward-completion requires *both* targets to exist and identity-match ([:404](../../scripts/bootstrap_brain_instances.py)); anything less is `foreign_corrupted` or `recoverable_partial`, never a blessed pair.
4. **One genuine + one foreign accepted?** No — `foreign_corrupted`, exit 4, zero deletions [executed], Probe 3b + committed tests.
5. **Integrity/FK/partition/singleton before forward-completion?** Integrity yes (implicit), singleton yes (schema), FK no, partition no — finding M-1.
6. **Manifest records current state and digests?** No — marker data only; staged digests as `result_journal_digests` (defensible), no current-state observation (finding M-1).
7. **Never deletes on identity without exact digest?** True as written ([:425](../../scripts/bootstrap_brain_instances.py)) and re-verified *at action time* (no delete-side TOCTOU) — but finding B-1 shows an overwrite-by-publish that is equivalent to deletion in effect.
8. **Can a post-publish write really occur before the manifest?** Yes — the published journal is valid and marked; no writer or projector consults manifests; the LaunchAgent fires every 300 s. Probe 1 executed the exact sequence.
9. **Do the ControlStore gate and runtime enforcement bound the window?** Only for newly created profiles. Existing write-enabled profiles and the projector operate freely in the window — which is precisely why row 6/8 semantics exist. No further mitigation needed beyond fixing B-1.
10. **TOCTOU between classification and action?** Deletion: no (re-inspects). Forward-completion: no writes to targets. FRESH-proceed: **yes** — nothing re-checks targets before `publish_pair`; this is the enabling half of B-1 and is fixed by the same pre-publish guard.

## 5. Backfill tool (`scripts/stamp_brain_instance.py`)

Verified against every checklist item: backup-first with digest-verified backup before any mutation ✓; idempotent rerun (`already_stamped`, exit 0, byte-identical journal — test) ✓; mixed/foreign workspace refusal without guessing (`workspace_partition_violation`, exit 2, journal untouched — test + fixture's own mixed shape) ✓; instance is a required explicit argument, never inferred ✓; canonical tables proven byte-unchanged by digest comparison after stamping (test) ✓; existing different marker → `marker_mismatch` refusal, marker untouched (test) ✓; interruption between backup and stamp → safe-but-stuck refusal (L-2) ✓; restore-from-backup on mid-apply failure (test; note that `sqlite3.Connection.backup` is a logical copy, so restoration is verified by canonical-content digest, not raw bytes — correctly documented in the test). SQLite logical comparison is sufficient for the property that matters (canonical memory content preservation); byte-level identity is neither guaranteed nor relied upon. **Not executed against any live journal** (none exist on this host); the runbook correctly flags the backfill as a required, not-yet-performed operator step.

## 6. Enforcement-path inventory

Every `sqlite3.connect` site in `brain/` was enumerated and classified:

| Site | Status |
|---|---|
| [writer.py:43→50](../../brain/writer.py) | enforced (`enforce_journal` before return) — the **only** code path that inserts into `memory_records` (repo-wide grep) |
| [repository.py:21-31](../../brain/repository.py) | both `journal()` and `retrieval()` wrap `_readonly` with enforcement before yield; all repository queries route through them |
| [projector/journal_reader.py:20-45](../../brain/projector/journal_reader.py) | enforced when `instance_id` given; `ProjectionProjector` always passes it ([projector.py:36](../../brain/projector/projector.py)); the `None` default is used only by standalone diagnostics and tests |
| [projector/projector.py:41 (`_write`)](../../brain/projector/projector.py) | `enforce_retrieval(allow_stamp=True)` — stamps only a genuinely empty DB, immediate commit; refuses non-empty missing/mismatched |
| [projector/projector.py:66,80 (`status`/`plan`)](../../brain/projector/projector.py) | direct read-only connects, but `validate()` surfaces `retrieval_instance_marker_missing`/`_mismatch` as REBUILD_REQUIRED issues ([validation.py:6-16](../../brain/projector/validation.py)) |
| [control.py:96-108](../../brain/control.py) | write-grant gate via `marked_instance_at_path` (fail-closed on unreadable/foreign files — verified by reading its error handling) |
| [runtime.py:14-20](../../brain/runtime.py) | **unenforced** — L-1 (health metadata only) |
| [migrations.py](../../brain/migrations.py), [instance_identity.py:105](../../brain/instance_identity.py) | operator tooling / the checker itself — acceptable |

No alternative connect or write path bypasses the marker on any record-content-bearing operation.

## 7. Diff and scope review

- Diff limited to the 22 Package 1 files; no changes outside the package's declared file set.
- MCP tool surface unchanged (`test_exact_tool_list_and_search_schema_parity` passes; exported schemas untouched, `check_exported()` true).
- No Graphiti/learning/sync/notification code (diff grep clean).
- Schema change is additive (`CREATE TABLE IF NOT EXISTS brain_instance_identity`); the marker table is deliberately excluded from `logical_digest`'s `TABLES`, keeping content digests and identity orthogonal — consistent throughout bootstrap, stamp tool, and tests.
- No secrets, tokens, user-specific absolute paths, or live journal/runtime data in the commit (greps clean; the only `.db` references are schema/docs text).
- Documentation: the runtime-doc state table and exit codes match `BLOCKING_CLASSIFICATIONS` and `classify_recovery`; the spec changelog describes the digest-vs-marker deviation accurately and at the right level of detail. Two doc-vs-reality gaps are consequences of H-2/M-3 (the runbook's index-build and write-smoke steps no longer work as written), not overclaims about the new code.
- Two pre-existing tests asserting the old destructive recovery were replaced with an inline explanation rather than silently dropped — verified present in the diff.

## 8. Recommended fixes for Sonnet (in order)

1. **(B-1)** After `cleanup_recoverable_partial`, re-inspect both targets and exit 4 if either survives; add a target-absence assertion immediately before `publish_pair`. Regression test = Probe 1 (half-publish crash → post-publish write → retry → write must survive or the run must refuse; it must never exit 0 having destroyed it).
2. **(H-1)** Protect a `published: true` manifest from non-publishing overwrites; route dry-run reports to a sibling file. Regression test = Probe 2.
3. **(H-2)** `build_brain_m1_indexes.sh`: pass `--instance-id "$instance"`.
4. **(M-3)** Fix `smoke_brain_m1_write.py` (stamp the disposable journal; scope the gate env vars to it).
5. **(M-1)** Enrich the forward-completed manifest with a current-state observation block (+ FK check).
6. **(M-2)** Runbook note on the marker's trust level. **(L-1..L-3)** optional hardening, may ride along with any later package.

Fixes 1–4 are all localized to `scripts/` plus one guard in `main()`; none touch the enforcement design, which this review found sound.

## 9. Verdict

**`REVISE PACKAGE 1`**

The instance-marker binding (B2) is correctly designed and correctly enforced at every content-bearing boundary — all cross-wired, missing-marker, retrieval-marker, and write-grant probes refused exactly as specified, and the digest-vs-marker deviation is the right call, correctly reasoned and honestly documented. The package fails on one point, but it is the package's own headline: recovery can still silently destroy a committed journal write (B-1, executed reproduction, exit 0), which is a direct I2 violation, and two of the documented operational flows (index build, write smoke) no longer work against the new contract (H-2, M-3), with the dry-run manifest overwrite (H-1) corrupting the new state machine's input. Because the fixes are small and the architecture needs no change, this is a revise, not a redesign; after fixes 1–4 land with their regression tests, re-review can be limited to those paths.

This verdict, and every finding above, is left exactly as originally written. §10 below records what changed since.

---

## 10. Repair verification (2026-07-15, same-day follow-up)

A narrowly-scoped repair pass (Claude Sonnet 5) closed B-1, H-1, H-2, M-1, and M-3 as recommended in §8, items 1–5. M-2 and L-1–L-3 were intentionally left open, per this review's own §8 note that they may ride along with a later package. Repair commit: see the top of this document / repo history for the hash following `2ec71d8`.

### What changed, mapped to each finding

- **B-1 (Blocking):** `main()` now re-inspects both targets immediately after `cleanup_recoverable_partial` and refuses (exit 4, nothing further touched) if either survived, instead of falling through to a fresh build. A second guard immediately before `publish_pair` re-checks both targets are absent and raises rather than overwriting if either has appeared since staging began — closing the classification→publish TOCTOU this review's Q10 identified as the same underlying gap.
- **H-1 (High):** the final manifest-write step now checks whether the *existing on-disk* manifest already says `published: true`; if so, the current run's report is written to a sibling `<manifest>.preflight.json` instead, and the published manifest is left byte-for-byte untouched.
- **H-2 (High):** `scripts/build_brain_m1_indexes.sh` now passes `--instance-id "$instance"` to `run_brain_projector.py`.
- **M-1 (Medium):** `classify_recovery`'s forward-completion branch now re-runs `PRAGMA foreign_key_check` and a workspace-partition subset check on both targets before returning `crash_after_publish_before_manifest`; a violation on either side downgrades the classification to `corrupted` (refuse, exit 4, nothing deleted) instead of completing. The written manifest gained a `recovered_observation` block per target (current sha256, logical digest, integrity result, FK-check result, marker row) alongside the pre-existing, still load-bearing `result_journal_digests` (staged-at-publish-time digests — unchanged, since those are what makes later `live` detection work, exactly as this review's §4 analysis recommended keeping).
- **M-3 (Medium):** `scripts/smoke_brain_m1_write.py` now stamps the disposable journal's instance marker at creation and temporarily points the relevant `BRAIN_{INSTANCE}_JOURNAL_DB` environment variable at that disposable path for the duration of the `ControlStore.save()` call, restoring the previous value (or unsetting it) in a `finally` block afterward.

### Reviewer probes re-executed against the repaired code

Both probes were re-run with the *exact same scripts* used in the original review (byte-identical `review_probe1_half_publish.py` / `review_probe2_dryrun_manifest.py`), not rewritten versions.

- **Probe 1** (crash between the two `os.replace` calls → post-publish write lands → retry): classification is still `recoverable_partial` (unchanged, correct); the retry now **exits 4** instead of 0, and the post-publish write **survives** (`post-publish write survived retry: True`). This was the Blocking finding; it is closed.
- **Probe 2** (completed bootstrap → plain dry run → `--apply` rerun): the manifest's `published` field and `result_journal_digests` now **survive the dry run unchanged**; classification stays `already_bootstrapped` both before and after the dry run; the following `--apply` rerun returns normally (idempotent), instead of degrading to `incompatible_existing_state` / exit 3.

### New regression tests (in `tests/test_brain_instance_bootstrap.py` unless noted)

| Test | Covers |
|---|---|
| `test_B1_half_published_target_with_post_publish_write_is_never_clobbered_by_retry` | Probe 1's exact scenario; asserts exit 4 (never 0) and write survival |
| `test_B1_target_appearing_between_build_and_publish_is_refused_toctou` | the pre-publish TOCTOU guard, review Q10 |
| `test_H1_dry_run_never_overwrites_a_published_manifest` | Probe 2's exact scenario; manifest byte-identity + sibling preflight file + idempotent rerun |
| `test_H2_build_brain_m1_indexes_script_passes_instance_id_to_projector` | textual regression guard on the shell script |
| `test_H2_run_brain_projector_instance_id_flag_enforces_marker_via_plan` | the real CLI, via subprocess with `--plan` (no embedding server needed) |
| `test_M1_forward_completion_records_recovered_observation` | the new evidence block's presence and correctness |
| `test_M1_forward_completion_refuses_when_foreign_key_check_fails` | validation gate, FK side |
| `test_M1_forward_completion_refuses_when_workspace_partition_is_violated` | validation gate, partition side |
| `test_M3_smoke_brain_m1_write_end_to_end_against_disposable_paths` | (subprocess) the documented smoke flow end-to-end, plus no env leakage into the parent process |

### Full suite

`pytest tests -q`: **160 passed, 5 subtests passed, 0 failed** (up from this review's baseline of 151+5; +9 tests from the repair pass). Re-run by the repair author, not merely reported.

### What remains open (unchanged from §8)

M-2 (marker forgeability under local-FS trust — documentation-only recommendation, not implemented) and L-1–L-3 (RuntimeInspector marker check, stamp-script backup ergonomics, corrupt-marker-JSON handling) are exactly as this review left them. They were explicitly out of scope for this repair pass and require no action before a subsequent package addresses them, per §8's own framing.

### Re-review scope

Per this review's closing sentence, re-review can be limited to the five changed paths above (`scripts/bootstrap_brain_instances.py`'s `main()`/`classify_recovery`/`forward_complete_from_marker`, `scripts/build_brain_m1_indexes.sh`, `scripts/smoke_brain_m1_write.py`) plus their new tests; nothing else in the Package 1 diff was touched by this repair pass.
