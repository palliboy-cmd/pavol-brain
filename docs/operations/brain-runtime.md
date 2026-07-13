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

M1 rollout order is fixed: backup legacy/control state; bootstrap dry-run; operator resolution of every reference finding; staged dual-journal build; digest/partition gate; explicit publish approval; one-shot dual-index build; parity/manifest review; disabled profile creation; read-only smoke; `RegistryPolicy` write smoke against disposable staging journals; plist preparation; final dual activation. On any failure, keep legacy read-only, revoke the new profiles, boot out both M1 labels and remove only unpublished/staging outputs. Published M1 files are retained for forensics until the operator explicitly removes them.

## Health interpretation

`index_behind` means the projector cursor differs from journal head. `stale_index` becomes true only while behind and the oldest unprojected event exceeds `BRAIN_STALE_AFTER_SECONDS` (default 3600), or the optional `BRAIN_STALE_GAP_EVENTS` threshold is exceeded. A short scheduling delay is healthy-but-behind; stale, endpoint-down, cursor-ahead, or rebuild-required state is degraded. A missing journal or retrieval DB is unavailable.

Endpoint probes are loopback-only, bounded by `BRAIN_ENDPOINT_PROBE_TIMEOUT`, and cached for `BRAIN_ENDPOINT_PROBE_TTL`. Audit logs are metadata-only rotating JSONL when `BRAIN_AUDIT_LOG` is explicitly configured; query text and record bodies are excluded. Debug query logging is intentionally unsupported in the MVP. Retain operational logs according to local machine policy and never commit them.

For stale state, inspect `brain_health`, run a projector plan and validate, then one locked bounded run. For `rebuild_required`, build a fresh disposable index and repeat the backup/parity/atomic-switch process. Projector failures leave the transactional cursor unchanged and the next schedule retries.

## Four-week usage checkpoint

After four weeks, summarize the metadata audit JSONL: successful calls by operation, manual interventions, stale/unavailable incidents, whether returned record IDs/provenance resolved the task, and operator maintenance time. A documented one-off query is sufficient; no dashboard is required.
