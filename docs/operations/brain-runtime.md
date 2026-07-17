# Pavol-Brain runtime operations

Each canonical SQLite journal is append-only truth and each retrieval database is a disposable derived index. M1 runs separate Personal and WORK journals/indexes with no cross-instance retrieval; the former mixed database is a read-only `legacy` rollback source. The runtime has no public listener: agents use local or SSH stdio MCP, the governed writer appends to a journal, and only the operator projector writes a derived database.

## Projector

`scripts/run_brain_projector_locked.py` performs one bounded iteration, uses a non-blocking file lock, times out after 240 seconds by default, exits non-zero on failure, and is safe to retry. The LaunchAgent template runs it every 300 seconds with explicit Python, database, endpoint, model, and dimension arguments. After bootstrap, build both indexes and review their manifests before preparing or activating LaunchAgents:

```sh
BRAIN_BOOTSTRAP_MANIFEST=/path/to/reviewed-split-manifest.json BRAIN_PYTHON="$PWD/.venv/bin/python" scripts/build_brain_m1_indexes.sh
BRAIN_BOOTSTRAP_MANIFEST=/path/to/reviewed-split-manifest.json BRAIN_M1_APPROVED=yes scripts/install_brain_m1_projectors.sh
BRAIN_BOOTSTRAP_MANIFEST=/path/to/reviewed-split-manifest.json BRAIN_M1_APPROVED=yes BRAIN_ACTIVATE_PROJECTORS=yes scripts/install_brain_m1_projectors.sh
launchctl print "gui/$(id -u)/com.pavol.brain-projector-personal"
launchctl print "gui/$(id -u)/com.pavol.brain-projector-work"
```

Logs and the lock live under `~/Library/Logs/Pavol-Brain` and `~/Library/Application Support/Pavol-Brain`, outside Git. Uninstall with:

```sh
launchctl bootout "gui/$(id -u)/com.pavol.brain-projector-personal"
launchctl bootout "gui/$(id -u)/com.pavol.brain-projector-work"
rm "$HOME/Library/LaunchAgents/com.pavol.brain-projector-personal.plist" "$HOME/Library/LaunchAgents/com.pavol.brain-projector-work.plist"
```

Before replacing an active index, use SQLite `.backup`, verify `PRAGMA integrity_check`, run plan/build/validate on a disposable database, run the 24-query parity gate, retain the old active file, and switch using a same-directory rename. Roll back by stopping the LaunchAgent and renaming the retained `retrieval.db.pre-*` file to `retrieval.db`.

`scripts/build_brain_m1_indexes.sh` passes `--instance-id "$instance"` to `run_brain_projector.py` for each of `personal`/`work`, so the resulting retrieval database is stamped with the matching marker on first write (Package 1 repair, closed finding H-2 — before the fix, following this step literally silently built an unmarked, `legacy`-exempt index that every marked reader then refused).

M1 rollout order is fixed: backup legacy/control state; bootstrap dry-run; operator resolution of every reference finding; staged dual-journal build; digest/partition gate; explicit publish approval; one-shot dual-index build; parity/manifest review; disabled profile creation; read-only smoke; `RegistryPolicy` write smoke against disposable staging journals (`scripts/smoke_brain_m1_write.py` — stamps its own disposable journal and temporarily redirects the matching `BRAIN_{INSTANCE}_JOURNAL_DB` env var so the Control DB's write-grant gate checks the disposable journal, never a real one); plist preparation; final dual activation. On any failure, keep legacy read-only, revoke the new profiles, boot out both M1 labels and remove only unpublished/staging outputs. Published M1 files are retained for forensics until the operator explicitly removes them.

## Instance identity marker and bootstrap recovery (Package 1)

Every Personal and WORK journal carries a persisted `brain_instance_identity` singleton row (`instance_id`, `created_at`, `source_digest`), stamped once by `scripts/bootstrap_brain_instances.py`'s `build()` inside the same transaction as the initial data copy. `JournalWriter.connect()`, `Repository.journal()`/`Repository.retrieval()`, and the projector's `JournalReader.connect()`/`ProjectionProjector._write()` all refuse before running any query when a `personal`/`work`-configured connection's marker disagrees with what it opened: `BRAIN_INSTANCE_MISMATCH` for a wrong instance, `BRAIN_INSTANCE_MARKER_MISSING` for a journal that predates this marker. `legacy`/spike configurations are exempt by design. Retrieval databases are fully derived and disposable, so the projector is additionally allowed to *stamp* a genuinely empty (never-projected) retrieval database's `retrieval_embedding_meta['instance_id']` key itself, the first time it writes to it — a journal never gets this self-stamping behavior, because it is the source of truth, not a derived index.

`brain/control.py`'s `ControlStore.save()` refuses to persist a `write_enabled=True` profile for `brain_instance` `personal`/`work` unless `instance_paths(brain_instance)` already resolves to an existing, correctly-marked journal — a write grant can no longer be created for an instance that was never bootstrapped.

### Instance-marker backfill runbook (operator, mini-core)

A journal built before Package 1 has no `brain_instance_identity` marker and will refuse every write and read once Package 1 code is deployed. This is a **one-time, per-instance, backup-first, digest-verified** operation using the existing `scripts/stamp_brain_instance.py` — no new tooling is needed or should be invented for this. **Not yet run against any live journal.**

Set these once per shell session (placeholders — do not hardcode a specific operator's home directory in any committed doc or script):

```sh
BRAIN_HOME="${BRAIN_HOME:-$HOME/Library/Application Support/Pavol-Brain}"
PERSONAL_JOURNAL="$BRAIN_HOME/personal/journal.db"
WORK_JOURNAL="$BRAIN_HOME/work/journal.db"
PERSONAL_RETRIEVAL_DB="$BRAIN_HOME/personal/retrieval.db"
WORK_RETRIEVAL_DB="$BRAIN_HOME/work/retrieval.db"
PY="$PWD/.venv/bin/python"   # repo-relative venv; adjust only if the operator's checkout differs
```

**Step 1 — Preflight, read-only, both journals.** Confirms the journal exists, passes `PRAGMA integrity_check`, is not already marked for the *other* instance, and its `memory_records.workspace` values are a subset of that instance's partition (`PERSONAL_WORKSPACES`/`WORK_WORKSPACES` in `brain/control.py`). Mutates nothing — this is the same code path the dry run below uses, run here explicitly first so the operator sees the pre-stamp state before anything is written.

```sh
sqlite3 "$PERSONAL_JOURNAL" "PRAGMA integrity_check; SELECT count(*) FROM brain_instance_identity;"
sqlite3 "$WORK_JOURNAL" "PRAGMA integrity_check; SELECT count(*) FROM brain_instance_identity;"
```

Both `integrity_check` results must read `ok`. `brain_instance_identity` existing with `count=0` means unstamped-but-schema-current (expected pre-backfill state); a query error means the journal predates even that table and needs the schema migration reviewed before this runbook proceeds — **stop condition**, do not continue.

**Step 2 — Digest/checksum capture (evidence, before any mutation).** Record the pre-backfill state independently of the script's own internal check, so the operator has an external before/after comparison:

```sh
shasum -a 256 "$PERSONAL_JOURNAL" "$WORK_JOURNAL"
```

Keep this output; compare it against Step 6's post-stamp `shasum` if any doubt arises about whether canonical bytes moved (they must not — see Step 6).

**Step 3 — Dry run (explicit `--instance-id`, no `--apply`) — never mutates anything.**

```sh
"$PY" scripts/stamp_brain_instance.py --journal-db "$PERSONAL_JOURNAL" --instance-id personal
"$PY" scripts/stamp_brain_instance.py --journal-db "$WORK_JOURNAL" --instance-id work
```

Read the printed JSON report: `blocked` must be `null` and `already_stamped` must be `false` (a `true` here means this journal is already stamped for this instance — skip straight to Step 6's verification, nothing to apply). Any non-null `blocked` value (`journal_missing`, `integrity_check_failed`, `marker_mismatch`, `workspace_partition_violation`) is a **stop condition** — do not pass `--apply` until the operator has investigated and resolved the specific reason named.

**Step 4 — Backup-first apply.** `--apply` backs up the journal to `<journal>.pre-instance-stamp-backup.db` *before* any write, verifies the backup's logical digest matches the original, then stamps the marker inside its own transaction:

```sh
"$PY" scripts/stamp_brain_instance.py --journal-db "$PERSONAL_JOURNAL" --instance-id personal --apply
"$PY" scripts/stamp_brain_instance.py --journal-db "$WORK_JOURNAL" --instance-id work --apply
```

By default the marker's `source_digest` is the journal's own current logical digest (self-referential — a backfill has no legacy bootstrap snapshot to bind to). Pass `--source-digest <digest>` explicitly only if continuity with a specific original bootstrap manifest's digest is required.

**Step 5 — Automatic post-stamp verification (built into the script, no separate operator step).** After stamping, the script itself: re-reads the journal, asserts `PRAGMA integrity_check` still reads `ok`, and asserts the marker row now reads back the exact `instance_id`/`source_digest` just written. It also re-computes the logical digest of the five canonical tables (`memory_records`, `memory_events`, `record_state`, `artifact_links`, `artifact_validation_events`) and compares it against the value captured *before* stamping — any mismatch raises, and the exception handler restores the journal from the Step 4 backup before re-raising. A successful run's JSON report has `"stamped": true` and echoes the verified `marker`.

**Step 6 — Manual post-stamp SQL verification (operator, independent of the script's own checks).**

```sh
sqlite3 "$PERSONAL_JOURNAL" "SELECT * FROM brain_instance_identity;"
sqlite3 "$WORK_JOURNAL" "SELECT * FROM brain_instance_identity;"
shasum -a 256 "$PERSONAL_JOURNAL" "$WORK_JOURNAL"
```

Confirm: exactly one row per journal, `instance_id` matches the journal's own directory (`personal`/`work`), `singleton=1`. The canonical-table byte content is unaffected by the stamp (it lives in a new table, not touched columns), but the journal *file's* own SHA-256 legitimately changes (the marker table itself is new data) — do not expect Step 2's file hash to still match; do expect the *logical digest of the five canonical tables* (what the script itself checks) to be unchanged, which Step 5 already proved automatically.

**Step 7 — Byte/logical-content preservation check (independent record-count spot-check).**

```sh
sqlite3 "$PERSONAL_JOURNAL" "SELECT count(*) FROM memory_records; SELECT count(*) FROM memory_events;"
```

Compare against a count taken before Step 4 (or against the Package 1/2 live-audit figures already on file, if this is the first time these counts are captured) — they must be identical; the backfill adds one marker row to a new table and touches nothing else.

**Rollback from backup.** The script auto-restores from `<journal>.pre-instance-stamp-backup.db` on any failure during apply (Step 4/5) — no operator action needed in that case. If a problem is discovered *after* a reported success (e.g., Step 6/7 disagrees with expectation), restore manually:

```sh
cp "$PERSONAL_JOURNAL" "$PERSONAL_JOURNAL.rollback-investigate.db"   # preserve the post-stamp state for forensics first
cp "$PERSONAL_JOURNAL.pre-instance-stamp-backup.db" "$PERSONAL_JOURNAL"
```

Never hand-edit journal rows to "fix" a bad stamp — always restore from the backup file and re-run the dry run to diagnose.

**Idempotent rerun.** Running the script again (with or without `--apply`) against an already-correctly-stamped journal returns `already_stamped: true`, exit 0, zero writes — safe to re-run as many times as needed, including accidentally.

**Stop conditions (do not proceed past these without operator resolution):**
- Step 1's `PRAGMA integrity_check` does not read `ok` on either journal.
- Step 3's dry run reports any non-null `blocked` value.
- Step 3's dry run reports `already_stamped: true` for the *wrong* instance (i.e., the WORK journal already carries a `personal` marker or vice versa) — this indicates the journal/directory pairing itself is wrong; fix the pairing before stamping anything, never force a re-stamp over a mismatched marker.
- Step 5's automatic verification raises (the script already restores from backup in this case — treat this as a hard stop, investigate before retrying, do not immediately retry with `--apply` again without understanding why).
- Any canonical-table count in Step 7 disagrees with the pre-backfill baseline.

**Threat-model note (Package 1 review, finding M-2):** the identity marker `(instance_id, source_digest)` is an operational label under local-filesystem trust, not a cryptographic authenticity mechanism — anyone with filesystem write access to the journal could forge it. This is accepted for the declared single-operator local-machine threat model (§1 of `docs/reviews/package-1-bootstrap-instance-binding-review.md`) and does not weaken this runbook's backup/verify/rollback discipline, which protects against *accidental* corruption and operator error, not a malicious actor with existing filesystem access.

**This backfill must be run once per Personal/WORK journal, on the host where they actually live (`mini-core`), before Package 1+ code is deployed there** — otherwise every write and read against those journals will start failing with `BRAIN_INSTANCE_MARKER_MISSING` the moment the new code is deployed. See "Controlled deployment and rollout" below for how this fits into the full rollout sequence.

### Bootstrap state machine and exit codes

`scripts/bootstrap_brain_instances.py --apply` classifies current on-disk state (marker file, manifest, and the two target journals) before doing anything, per this table (`classify_recovery()`):

| Classification | Meaning | `--apply` action | Exit |
|---|---|---|---|
| `fresh` | nothing exists yet | full bootstrap run | 0 (or 2 if preflight audit blocks it) |
| `already_bootstrapped` | a prior run completed and nothing has diverged since | print the existing manifest, write nothing | 0 |
| `live` | bootstrap already completed and the instance(s) have since been used normally (new writes exist) | refuse — "bootstrap is not a reset tool" | 3 |
| `incompatible_existing_state` | a target exists with no marker and no published manifest (foreign file, or exactly one target present) | refuse | 3 |
| `completed_crash_after_manifest` | crash after the manifest was already written; only the marker cleanup was missed | remove the stale marker, print the manifest | 0 |
| `crash_after_publish_before_manifest` | both `os.replace` calls succeeded but the process died before the manifest write | re-verify `PRAGMA foreign_key_check` and workspace-partition membership on both targets; if both pass, write the manifest from the marker's own record of what was published plus a `recovered_observation` per target (current sha256, logical digest, integrity, FK-check, marker), and remove the marker — **never re-verify content digest or touch the published files**, so a legitimate write that landed in the gap is preserved. If either target fails the FK/partition check, downgrade to `corrupted` instead (below) rather than completing over a broken pair. | 0, or 4 if the FK/partition check fails |
| `recoverable_partial` | at most a digest-matching remnant of an incomplete publish remains (e.g. only one `os.replace` succeeded before a crash) | delete only the file(s) whose content digest matches what this bootstrap staged, plus `.staging` leftovers; **then re-verify both targets are actually gone** — if either survived cleanup (its content diverged from the staged digest, so cleanup correctly refused to touch it — e.g. a legitimate write landed there), refuse (`recoverable_partial_cleanup_incomplete`) rather than falling through to a fresh build/publish over it, **and keep the marker** (it is the forensic record of what this bootstrap staged, and lets the next retry re-derive the same precise classification instead of degrading to a generic `incompatible_existing_state`); only remove the marker and proceed to a fresh run once both targets are confirmed absent | 0 (fresh run) or 4 (a target survived cleanup; marker preserved) |
| `foreign_corrupted` | a target exists but disagrees with everything this or a prior bootstrap ever recorded | refuse, demand operator inspection | 4 |
| `incompatible_retry` | the marker's own recorded targets don't match the targets requested this time | refuse | 3 |
| `corrupted` | a target fails `PRAGMA integrity_check`, a published manifest's recorded digests don't match reality, or a would-be `crash_after_publish_before_manifest` pair fails its FK/partition re-check | refuse | 4 |

Two distinct proof requirements back this table: completing the *manifest write* for `crash_after_publish_before_manifest` only requires that each target's own stamped `brain_instance_identity` marker (`instance_id` + `source_digest`) prove it is this bootstrap's output — content may have changed since (a legitimate write), and that is fine, because nothing is deleted in this branch. *Deleting* a file (`recoverable_partial`) requires an exact content-digest match against what was staged — the higher bar for the higher-risk, irreversible action. Recovery never deletes a file it cannot verify by one of these two proofs, and never falls through to a build/publish step without re-confirming immediately beforehand that neither target exists (closing the window between classification/staging and the actual `os.replace` calls, which can span the full time it takes to build two journal copies). Preflight now also requires the requested `--personal-workspaces`/`--work-workspaces` to equal `brain/control.py`'s `PERSONAL_WORKSPACES`/`WORK_WORKSPACES` exactly (exit 2 via the existing preflight-audit path otherwise) — the workspace partition has one source of truth.

A plain preflight dry run (no `--apply`) never writes to a manifest that is already published on disk — its report goes to a sibling `<manifest>.preflight.json` instead, and the real manifest (the recovery classifier's own input) is left byte-for-byte untouched. `--apply` never reaches this path with an already-published on-disk manifest present, since `already_bootstrapped`/`live`/`completed_crash_after_manifest` all resolve earlier.

Preflight-audit blocking (missing/overlapping/unknown workspaces, unresolved cross-partition references, count mismatches, integrity/FK failures, partition-constant mismatch) remains a separate `blocked` path with exit code 2, unchanged from before Package 1.

## Controlled deployment and rollout

This is the operator sequence for deploying Package 1–9's code to `mini-core` and completing the instance-marker backfill above. **Not yet executed** — recorded here so the sequence is reviewed before anyone runs it. Every step is written to be idempotent or safely re-runnable; none of them may be reordered without re-reading the "must not run before" column.

| # | Step | Must stop / must not run before this | Rollback if this step fails |
|---|---|---|---|
| 1 | **Backup live journals.** `shasum -a 256` + file copy of `$PERSONAL_JOURNAL`, `$WORK_JOURNAL`, and the retrieval DBs to a timestamped location outside the deploy path. | Nothing prior required; this is the first step. | N/A — nothing has changed yet. |
| 2 | **Stop write-capable agents and the projector.** `launchctl bootout` both `com.pavol.brain-projector-{personal,work}` LaunchAgents (see "Projector" above); ensure no MCP client with a `write_enabled` profile is mid-session (check `brain_health`/active SSH sessions). **No write-capable process may run again until step 9.** | Step 1 must be complete (backups exist before anything stops, so a failed stop can be diagnosed against a known-good backup). | Restart the LaunchAgents from their existing (pre-deploy) plists — no code changed yet. |
| 3 | **Deploy code.** Pull/checkout the reviewed commit on `mini-core`; do not `git pull` into a mixed state — the checkout must move atomically (e.g., a fresh clone or `git checkout <sha>` with a clean tree) so no process ever sees half-old/half-new source. **Verify no projector or MCP process is running (step 2) before this step** — a running process holding old code while the working tree changes underneath it is exactly the mixed-version hazard this order prevents. | Step 2 (all write-capable/projector processes stopped). | `git checkout` back to the previous deployed SHA; nothing else has changed. |
| 4 | **Run the marker backfill.** Follow "Instance-marker backfill runbook" above, in full, for both `personal` and `work`. **This must run before any Package-1-or-later code opens either journal** — every connect path (`JournalWriter`, `Repository`, `JournalReader`, the projector) refuses an unmarked journal with `BRAIN_INSTANCE_MARKER_MISSING` the instant new code touches it, and a half-completed backfill (one instance stamped, the other not) leaves the two instances in different verification states, so complete both before proceeding. | Steps 2 and 3 (nothing reads/writes the journal with new code while unmarked; code is already the reviewed version so the marker check is actually active). | Follow the backfill runbook's own "Rollback from backup" section — restore each journal independently from its own `.pre-instance-stamp-backup.db`; this does not require re-deploying code. |
| 5 | **Verify journal markers.** `sqlite3 "$PERSONAL_JOURNAL" "SELECT * FROM brain_instance_identity;"` and the same for WORK — confirm exactly one row each, `instance_id` matching the journal's own directory. This is the same check as backfill Step 6, repeated here as the deployment gate. | Step 4 complete for both instances. | If a marker is wrong or missing, do not proceed to step 6 — return to step 4's rollback. |
| 6 | **Rebuild or validate retrieval DB markers.** Run `"$PY" scripts/run_brain_projector.py --journal-db "$PERSONAL_JOURNAL" --retrieval-db "$PERSONAL_RETRIEVAL_DB" --instance-id personal --validate` and the WORK equivalent (`--journal-db "$WORK_JOURNAL" --retrieval-db "$WORK_RETRIEVAL_DB" --instance-id work --validate`) — read-only. If it reports `retrieval_instance_marker_missing`/`retrieval_instance_marker_mismatch` (via `REBUILD_REQUIRED`), delete that retrieval DB and let step 8's first projector run rebuild and re-stamp it from empty — retrieval is fully derived and disposable (I12); never hand-edit its rows. | Step 5 (journal markers verified correct — a retrieval rebuild reads the journal, so the journal must already be correctly marked). | Deleting a retrieval DB is itself the rollback for a bad retrieval marker — it is always safe, since it is rebuilt from the journal on the next run. |
| 7 | **Run acceptance smoke.** From the deployed checkout: `python scripts/run_brain_acceptance.py` and `pytest tests/ -q` (see "Acceptance and diagnostic commands" below) against the deployed code — not against a developer machine — to confirm the deployed environment (Python version, dependencies) actually reproduces the reviewed green suite. | Steps 3–6 (code deployed, markers correct on both journal and retrieval sides). | A red suite here means stop the rollout — do not proceed to step 8; return to step 3's rollback (revert code) and re-diagnose before retrying. |
| 8 | **Restart the projector.** Re-`launchctl bootstrap`/reinstall both `com.pavol.brain-projector-{personal,work}` LaunchAgents (`scripts/install_brain_m1_projectors.sh`, passing `--instance-id` per the H-2 fix); confirm one bounded run completes with `HEALTHY` or `NO_CHANGES`, not `REBUILD_REQUIRED`/`FAILED`. | Step 7 green. | `launchctl bootout` again; retrieval DB deletion (step 6's rollback) is still safe at this point if the first run surfaces a problem. |
| 9 | **Restart MCP/write clients.** Only now re-enable write-capable profiles/sessions (this is the first point since step 2 that a write-enabled profile may safely operate against the newly-marked journals). Verify `ControlStore` write profiles are still accepted (`ControlStore.save` requires a marked journal — this now exists) before any agent actually writes. | Step 8 (projector confirmed healthy) — writing before the projector is confirmed healthy risks a backlog the projector then has to catch up on blind; not unsafe by construction, but avoid it operationally. | Disable the write-enabled profile(s) again (`ControlStore`) if a problem surfaces; this does not require touching the journal or code. |
| 10 | **Monitor audit/errors.** Watch the audit JSONL (`BRAIN_AUDIT_LOG`) and projector logs for the rollback window (see "Rollback plan" below for the exact duration and criteria) for `BRAIN_INSTANCE_*` errors, unexpected `BRAIN_IDEMPOTENCY_*` conflicts, or `REBUILD_REQUIRED`. | Step 9. | See "Rollback plan" below for the decision point and full rollback procedure. |
| 11 | **Rollback criteria.** See "Rollback plan" below — do not improvise a rollback path outside what is documented there. | — | — |

**Personal vs WORK wiring verification (do this explicitly, not just by inspection of config):** attempt to open the `personal` journal with a `BrainConfig(instance_id="work")` and confirm `BRAIN_INSTANCE_MISMATCH` (and the reverse) — this is exactly what `tests/test_brain_instance_bootstrap.py::test_journal_writer_refuses_on_instance_mismatch` proves in fixtures; running the equivalent by hand against the real files (read-only — do not actually write) is the deployment-time confirmation that the launcher's env-derived paths are wired to the correct physical files, not just conventionally believed to be.

**Write-enabled profile verification:** query `ControlStore` (`brain_control_center` or a direct read) for each profile's `write_enabled`/`brain_instance`/workspace grants; confirm every write-enabled profile's `brain_instance` resolves (via `instance_paths`) to a journal that now carries a matching marker (step 5) — `ControlStore.save` already refuses to create such a profile without one, but an operator inspecting *existing* profiles after a code deploy should confirm none were created before this gate existed under a since-invalidated assumption.

**Preventing mixed-version runtime:** the ordering above (stop everything in step 2, deploy in step 3, only restart in steps 8–9) is the whole mechanism — no write-capable or projector process may hold old code in memory while the on-disk journal/retrieval schema or marker state changes underneath it. If any step 2 process cannot be confirmed stopped (e.g., an SSH session outside operator control), treat that as a stop condition for the entire rollout, not a step to skip.

## Rollback plan

**Code rollback:** `git checkout <previous-deployed-sha>` on `mini-core`; safe at any point, since no step above makes an irreversible code-side change — the journal/retrieval side is what needs its own, separate rollback below.

**Journal marker rollback:** restore each journal from its own `<journal>.pre-instance-stamp-backup.db` (created by the backfill runbook's Step 4) — **the journal is the source of truth and is never restored from, or reconciled against, the retrieval DB.** Never hand-edit journal rows to undo a stamp or anything else; a restore-from-backup is the only supported path. If the backup itself is suspect, stop — do not improvise; the situation needs operator judgment, not a scripted recovery.

**Warning — journal restore after live writes have resumed:** the pre-stamp backup is a safe rollback point only while no new live write events have landed since it was taken. Once step 9 re-enables write clients, restoring that backup removes every event written after it from the active journal. Before any such restore: (1) make a forensic copy of the current post-deploy journal first (`cp "$JOURNAL" "$JOURNAL.rollback-investigate.db"`) — never skip this; (2) the operator must explicitly decide whether to roll the whole journal back or to preserve/reconcile the newer events before restoring — this is a judgment call, not a scripted step; (3) the retrieval DB is never a recovery source for those events. Because old code ignores the additive marker table, a late-window rollback should normally be code-only plus profile-disable, without any journal restore.

**Retrieval DB rollback/rebuild:** the retrieval DB is always safe to delete outright — it is a fully derived, disposable projection (I12) and the next projector run rebuilds it from the journal from an empty cursor. There is no "restore the retrieval DB from backup" path because there is never a need for one; deletion-and-rebuild is strictly simpler and always correct. Use the existing backup/parity/atomic-switch procedure in "Projector" above when replacing an *active* index rather than an empty one.

**Control DB/profile rollback:** `ControlStore` profiles are operator CRUD, separate from the journal/retrieval artifacts — disable (`write_enabled=False`) or delete a profile directly via `control_center.py`/`ControlStore`; this never touches journal or retrieval files and can be done independently of any other rollback step.

**LaunchAgent restart rollback:** `launchctl bootout` both `com.pavol.brain-projector-{personal,work}` labels and reinstall the previous plist versions if the LaunchAgent template itself changed; if only the underlying code changed (not the plist), a `bootout` + `bootstrap` cycle against the rolled-back code is sufficient.

**Decision point — rollback vs. continue:** roll back immediately (do not attempt to "fix forward" mid-rollout) if any of: step 5's marker verification fails after the backfill runbook's own stop conditions were already satisfied; step 7's acceptance smoke is red on the deployed checkout; step 8's first projector run reports `REBUILD_REQUIRED` or `FAILED` (rebuild-and-retry once per "Health interpretation" above is acceptable before calling this a rollback trigger — only escalate to rollback if a fresh rebuild also fails); or step 10's monitoring window surfaces a `BRAIN_INSTANCE_MISMATCH`/`BRAIN_INSTANCE_MARKER_MISSING` error from a live client (this means the wiring verification above was not actually correct, and continuing risks a genuine misroute). Continue (do not roll back) for an isolated, explained `BRAIN_IDEMPOTENCY_CONFLICT` from a client replaying with different metadata — that is the system working as designed (§6), not a rollout defect.

## Acceptance and diagnostic commands

Run from the repo root, against the target checkout (repo-relative paths; `$PY` as defined above):

```sh
# Acceptance runner (§10-mapped modules only)
"$PY" scripts/run_brain_acceptance.py

# Full suite (authoritative)
"$PY" -m pytest tests/ -q

# Schema export parity
"$PY" -c "from brain.schemas import check_exported; print(check_exported())"

# MCP tool-list / search-schema parity
"$PY" -m pytest tests/test_brain_mcp.py -k test_exact_tool_list_and_search_schema_parity -q

# Bootstrap dry-run / status diagnostics (read-only; omit --apply)
"$PY" scripts/bootstrap_brain_instances.py --source "$LEGACY_SOURCE" \
  --personal-journal "$PERSONAL_JOURNAL" --work-journal "$WORK_JOURNAL" \
  --personal-workspaces "$PERSONAL_WORKSPACES_CSV" --work-workspaces "$WORK_WORKSPACES_CSV" \
  --manifest /tmp/bootstrap-status-preflight.json

# Instance-marker query (read-only)
sqlite3 "$PERSONAL_JOURNAL" "SELECT * FROM brain_instance_identity;"
sqlite3 "$WORK_JOURNAL" "SELECT * FROM brain_instance_identity;"

# Projector plan/status (read-only; never calls the embedding endpoint)
"$PY" scripts/run_brain_projector.py --journal-db "$PERSONAL_JOURNAL" --retrieval-db "$PERSONAL_RETRIEVAL_DB" --instance-id personal --plan
"$PY" scripts/run_brain_projector.py --journal-db "$WORK_JOURNAL" --retrieval-db "$WORK_RETRIEVAL_DB" --instance-id work --plan

# Read-only reference/secret audits (both live journals)
"$PY" scripts/audit_record_references.py --journal personal="$PERSONAL_JOURNAL" --journal work="$WORK_JOURNAL"
"$PY" scripts/audit_write_envelope_secrets.py --journal personal="$PERSONAL_JOURNAL" --journal work="$WORK_JOURNAL"

# git hygiene
git diff --check
```

## Manual and live evidence

| Evidence item | Status | Date | Source document | Rerun required before rollout? |
|---|---|---|---|---|
| Package 2 record-reference audit (both live journals) | Completed | 2026-07-16 | `docs/reviews/package-2-record-reference-audit.md` §2 | No — zero findings recorded; `scripts/audit_record_references.py` is available to re-run at any time as a cheap confirmation, not required |
| Package 4 write-envelope secret audit (both live journals) | Completed | 2026-07-16 | `docs/architecture/write-safety-integrity-repair-spec.md` §11 Package 4 changelog; `docs/reviews/package-4-write-envelope-filtering-review.md` "Required follow-up verification" | No — 0 blocking/informational findings recorded; re-run only if new write paths are added before rollout |
| Package 1 live instance-marker backfill (`scripts/stamp_brain_instance.py` against the real `personal`/`work` journals) | **Pending** | — | `docs/reviews/package-1-bootstrap-instance-binding-review.md` §5; this document's "Instance-marker backfill runbook" above | **Yes — required, not yet run** |
| Live controlled deploy (Packages 1–9 to `mini-core`) | **Pending** | — | This document's "Controlled deployment and rollout" section | **Yes — required, not yet run** |
| §12 Final Fable acceptance checklist | **Pending** | — | `docs/architecture/write-safety-integrity-repair-spec.md` §12 | **Yes — required before `READY FOR CONTROLLED WRITE ROLLOUT`/`READY WITH EXPLICIT LIMITATIONS` verdict** |

Do not read the table above as "live rollout is done" — only the first two rows are. The bottom three gate the actual rollout and are unaffected by any docs/tests-only package (Package 9 included).

## Health interpretation

`index_behind` means the projector cursor differs from journal head. `stale_index` becomes true only while behind and the oldest unprojected event exceeds `BRAIN_STALE_AFTER_SECONDS` (default 3600), or the optional `BRAIN_STALE_GAP_EVENTS` threshold is exceeded. A short scheduling delay is healthy-but-behind; stale, endpoint-down, cursor-ahead, or rebuild-required state is degraded. A missing journal or retrieval DB is unavailable.

Endpoint probes are loopback-only, bounded by `BRAIN_ENDPOINT_PROBE_TIMEOUT`, and cached for `BRAIN_ENDPOINT_PROBE_TTL`. Audit logs are metadata-only rotating JSONL when `BRAIN_AUDIT_LOG` is explicitly configured; query text and record bodies are excluded. Debug query logging is intentionally unsupported in the MVP. Retain operational logs according to local machine policy and never commit them.

For stale state, inspect `brain_health`, run a projector plan and validate, then one locked bounded run. For `rebuild_required`, build a fresh disposable index and repeat the backup/parity/atomic-switch process — this is also the repair for a retrieval database whose instance marker is missing-with-existing-documents or mismatched (`validate()` surfaces `retrieval_instance_marker_missing`/`retrieval_instance_marker_mismatch` as ordinary rebuild-required issues); a fresh, empty retrieval database is always re-stamped by the next projector run, since it is fully derived and rebuildable from the journal (deterministic rebuildability, I12). Rebuilding the retrieval database is the *only* supported repair for `REBUILD_REQUIRED` — never hand-edit `retrieval_documents`/`retrieval_embeddings`/`retrieval_fts`/`retrieval_document_links` rows or the cursor directly. Projector failures leave the transactional cursor unchanged and the next schedule retries: the cursor only advances once every touched record has passed its full per-record postcondition (document + embedding + FTS row + exact link set for a projected record; verified absence of all four for a removed record), so a partial write can never be observed downstream.

## Four-week usage checkpoint

After four weeks, summarize the metadata audit JSONL: successful calls by operation, manual interventions, stale/unavailable incidents, whether returned record IDs/provenance resolved the task, and operator maintenance time. A documented one-off query is sufficient; no dashboard is required.
