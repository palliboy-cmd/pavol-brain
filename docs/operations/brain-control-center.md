# Brain Control Center operations

The Control Center is a server-rendered Python application bound only to mini-core loopback `127.0.0.1:8765`. Open it from MBP through an SSH local forward:

```sh
ssh -N -L 8765:127.0.0.1:8765 pavol@192.168.50.109
open http://127.0.0.1:8765/
```

It has three separated planes: the MCP data plane; `~/Library/Application Support/Pavol-Brain/brain-control.db` as the operational control plane; and metadata-only health/audit aggregation as the observability plane. The control DB contains profiles and append-only policy events, never memory records or query text.

Install or update with `BRAIN_PYTHON="$PWD/.venv/bin/python" scripts/install_brain_control_center.sh`. The installer backs up an existing control DB and plist. Inspect with `launchctl print gui/$(id -u)/com.pavol.brain-control-center`. Stop with `launchctl bootout gui/$(id -u)/com.pavol.brain-control-center`; restore the timestamped DB/plist backup and bootstrap it to roll back.

The Control Center runs from the standard project `.venv`, currently **Python 3.14.6** (uv-managed) on mini-core, migrated from Python 3.11.15 — see [local-development.md](local-development.md#migration-history) for the validating commits and what was checked. Python 3.11.15 remains the supported rollback runtime if ever needed. The mini-core checkout that this LaunchAgent runs from is kept in parity with this repository by file-content sync, not `git pull` — it has no `.git` directory.

### macOS TCC warning when restarting the LaunchAgent

Restarting this LaunchAgent (`launchctl bootout` followed by `bootstrap` + `kickstart`) can trigger macOS's Documents-folder privacy consent gate (TCC, `kTCCServiceSystemPolicyDocumentsFolder`), because both the interpreter and this checkout live under `~/Documents`. When that gate fires for a freshly-spawned background LaunchAgent process, Python's own interpreter startup hangs indefinitely inside `_PyConfig_InitPathConfig` waiting on a consent decision nothing can answer over SSH — the process shows as running but never binds its port and never responds to `curl`. This is unrelated to Python version; it can happen on any interpreter path that hasn't already cleared the gate for a fresh process instance, and was observed independently on both Python 3.11.15 and 3.14.6 during the 3.14 migration.

**Safe recovery steps, in order:**
1. If a restart hangs (process running per `launchctl list`/`ps`, but `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/` returns `000` after several seconds), **do not repeatedly retry the restart over SSH** — each attempt just re-triggers the same unanswerable prompt.
2. Stop the hung instance with `launchctl bootout gui/$(id -u)/com.pavol.brain-control-center` (this also prevents `KeepAlive` from looping a respawn).
3. Verify interactively at the physical machine (or via screen sharing): run the exact same command by hand in Terminal.app —
   `~/Documents/Personal/Projects/pavol-brain/.venv/bin/python ~/Documents/Personal/Projects/pavol-brain/scripts/run_brain_control_center.py --host 127.0.0.1 --port 8765`
   — a real, interactive GUI session can display and answer the consent dialog. Grant access (or add the interpreter binary to System Settings → Privacy & Security → Full Disk Access directly), confirm the server logs a successful request, then `Ctrl+C` to stop the temporary interactive instance.
4. Confirm `curl http://127.0.0.1:8765/` returns `200` from that temporary run before proceeding.
5. Only then restore the LaunchAgent: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.pavol.brain-control-center.plist && launchctl kickstart -k gui/$(id -u)/com.pavol.brain-control-center`, and re-verify `curl` returns `200`.

### Post-migration projector and FD-health verification

After any interpreter change, check for the file-descriptor-exhaustion class of failure specifically (see the connection-lifecycle fix referenced above):
- `lsof -p <control-center-pid> | wc -l` — should be a small, stable number; watch it across several minutes rather than checking once.
- Projector LaunchAgents (`com.pavol.brain-projector-personal`, `-work`) log their JSON result to `~/Library/Logs/Pavol-Brain/projector-{personal,work}.stdout.log` on every scheduled cycle; confirm the most recent entry's `run`/`validation` status is `HEALTHY` or `NO_CHANGES` (not an error) and that `journal_unchanged` is `true` unless a real write happened.
- A safe, read-only check that never mutates production data: `.venv/bin/python scripts/run_brain_projector.py --journal-db <path> --retrieval-db <path> --plan --validate`.
- Run `PRAGMA quick_check` and `PRAGMA foreign_key_check` (read-only, via `sqlite3 <path> '<pragma>'`) on all five production databases (personal/work journal + retrieval, plus `brain-control.db`) after any interpreter change.

All mutations use POST, an unpredictable same-origin CSRF token, explicit confirmation, fixed model fields, and additive SQLite transactions. The application rejects non-loopback binds and offers no shell, arbitrary path, config editor, journal, retrieval, embedding, ranking, or projector mutation endpoint. Logs remain under `~/Library/Logs/Pavol-Brain` outside Git.

The trusted-process identity is supplied by a fixed per-agent launcher through `BRAIN_INTEGRATION_ID`; requests cannot override it. M1 also binds every profile to one Brain instance and adds a separate `write_enabled` grant. Personal profiles may select only Personal workspaces; WORK profiles may select only `sap-work` and must carry its sensitive grant; legacy is read-only. Existing migrated profiles default to `legacy` and write-disabled; a write tool without the explicit write flag is rejected. This isolates cooperative local clients but is not protection against a malicious process running as Pavol, which can read the local operational files and environment. The authentication field is future-compatible, but no public remote authentication or endpoint exists.

Sensitive activity remains default-deny. A profile needs both an allowed workspace and a sensitive grant; `sensitive_allowed=true` alone does nothing. WORK and configured sensitive workspaces impose a server-owned `sensitive` floor: a client may raise sensitivity but cannot lower it with `sensitivity=normal`. Control-plane event history records actor, before/after policy hashes, fields, timestamp and reason. Activity displays metadata and record IDs only.
