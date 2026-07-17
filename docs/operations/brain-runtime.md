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

### One-time backfill for pre-Package-1 journals

A journal built before Package 1 has no marker and will refuse every write and read. Stamp it once, per instance, with `scripts/stamp_brain_instance.py`:

```sh
# Dry run first — never mutates anything.
.venv/bin/python scripts/stamp_brain_instance.py --journal-db "$HOME/Library/Application Support/Pavol-Brain/personal/journal.db" --instance-id personal
.venv/bin/python scripts/stamp_brain_instance.py --journal-db "$HOME/Library/Application Support/Pavol-Brain/work/journal.db" --instance-id work

# Apply — backs up the journal first (`<journal>.pre-instance-stamp-backup.db`), verifies the backup's
# content digest matches, stamps inside its own transaction, then re-verifies the canonical tables
# (memory_records/memory_events/record_state/artifact_links/artifact_validation_events) are byte-for-byte
# unchanged before declaring success. Any failure restores the journal from the backup.
.venv/bin/python scripts/stamp_brain_instance.py --journal-db "$HOME/Library/Application Support/Pavol-Brain/personal/journal.db" --instance-id personal --apply
.venv/bin/python scripts/stamp_brain_instance.py --journal-db "$HOME/Library/Application Support/Pavol-Brain/work/journal.db" --instance-id work --apply
```

The tool refuses (exit 2, nothing mutated) rather than guessing when: the journal is missing or fails `PRAGMA integrity_check`; it already carries a marker for a *different* instance; or its `memory_records.workspace` values are not a subset of that instance's workspace partition (`PERSONAL_WORKSPACES`/`WORK_WORKSPACES` in `brain/control.py`). Re-running against an already-stamped journal is a no-op (`already_stamped: true`, exit 0). By default the marker's `source_digest` is the journal's own current logical digest (self-referential — a backfill has no legacy snapshot to bind to); pass `--source-digest` explicitly to record the original bootstrap manifest's digest instead, for continuity with a manifest that predates this marker.

**This backfill has not been run against any live instance journal.** It must be run once per Personal/WORK journal, on the host where they actually live, before this Package 1 code is deployed there — otherwise every write and read against those journals will start failing with `BRAIN_INSTANCE_MARKER_MISSING` the moment the new code is deployed.

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

## Health interpretation

`index_behind` means the projector cursor differs from journal head. `stale_index` becomes true only while behind and the oldest unprojected event exceeds `BRAIN_STALE_AFTER_SECONDS` (default 3600), or the optional `BRAIN_STALE_GAP_EVENTS` threshold is exceeded. A short scheduling delay is healthy-but-behind; stale, endpoint-down, cursor-ahead, or rebuild-required state is degraded. A missing journal or retrieval DB is unavailable.

Endpoint probes are loopback-only, bounded by `BRAIN_ENDPOINT_PROBE_TIMEOUT`, and cached for `BRAIN_ENDPOINT_PROBE_TTL`. Audit logs are metadata-only rotating JSONL when `BRAIN_AUDIT_LOG` is explicitly configured; query text and record bodies are excluded. Debug query logging is intentionally unsupported in the MVP. Retain operational logs according to local machine policy and never commit them.

For stale state, inspect `brain_health`, run a projector plan and validate, then one locked bounded run. For `rebuild_required`, build a fresh disposable index and repeat the backup/parity/atomic-switch process — this is also the repair for a retrieval database whose instance marker is missing-with-existing-documents or mismatched (`validate()` surfaces `retrieval_instance_marker_missing`/`retrieval_instance_marker_mismatch` as ordinary rebuild-required issues); a fresh, empty retrieval database is always re-stamped by the next projector run, since it is fully derived and rebuildable from the journal (deterministic rebuildability, I12). Rebuilding the retrieval database is the *only* supported repair for `REBUILD_REQUIRED` — never hand-edit `retrieval_documents`/`retrieval_embeddings`/`retrieval_fts`/`retrieval_document_links` rows or the cursor directly. Projector failures leave the transactional cursor unchanged and the next schedule retries: the cursor only advances once every touched record has passed its full per-record postcondition (document + embedding + FTS row + exact link set for a projected record; verified absence of all four for a removed record), so a partial write can never be observed downstream.

## Four-week usage checkpoint

After four weeks, summarize the metadata audit JSONL: successful calls by operation, manual interventions, stale/unavailable incidents, whether returned record IDs/provenance resolved the task, and operator maintenance time. A documented one-off query is sufficient; no dashboard is required.
