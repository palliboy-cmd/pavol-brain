# Brain Control Center operations

The Control Center is a server-rendered Python application bound only to mini-core loopback `127.0.0.1:8765`. Open it from MBP through an SSH local forward:

```sh
ssh -N -L 8765:127.0.0.1:8765 pavol@192.168.50.109
open http://127.0.0.1:8765/
```

It has three separated planes: the MCP data plane; `~/Library/Application Support/Pavol-Brain/brain-control.db` as the operational control plane; and metadata-only health/audit aggregation as the observability plane. The control DB contains profiles and append-only policy events, never memory records or query text.

Install or update with `BRAIN_PYTHON="$PWD/.venv/bin/python" scripts/install_brain_control_center.sh`. The installer backs up an existing control DB and plist. Inspect with `launchctl print gui/$(id -u)/com.pavol.brain-control-center`. Stop with `launchctl bootout gui/$(id -u)/com.pavol.brain-control-center`; restore the timestamped DB/plist backup and bootstrap it to roll back.

All mutations use POST, an unpredictable same-origin CSRF token, explicit confirmation, fixed model fields, and additive SQLite transactions. The application rejects non-loopback binds and offers no shell, arbitrary path, config editor, journal, retrieval, embedding, ranking, or projector mutation endpoint. Logs remain under `~/Library/Logs/Pavol-Brain` outside Git.

The trusted-process identity is supplied by a fixed per-agent launcher through `BRAIN_INTEGRATION_ID`; requests cannot override it. M1 also binds every profile to one Brain instance and adds a separate `write_enabled` grant. Personal profiles may select only Personal workspaces; WORK profiles may select only `sap-work` and must carry its sensitive grant; legacy is read-only. Existing migrated profiles default to `legacy` and write-disabled; a write tool without the explicit write flag is rejected. This isolates cooperative local clients but is not protection against a malicious process running as Pavol, which can read the local operational files and environment. The authentication field is future-compatible, but no public remote authentication or endpoint exists.

Sensitive activity remains default-deny. A profile needs both an allowed workspace and a sensitive grant; `sensitive_allowed=true` alone does nothing. WORK and configured sensitive workspaces impose a server-owned `sensitive` floor: a client may raise sensitivity but cannot lower it with `sensitivity=normal`. Control-plane event history records actor, before/after policy hashes, fields, timestamp and reason. Activity displays metadata and record IDs only.
