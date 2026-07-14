# Pavol-Brain MCP integration

The adapter uses official Python MCP SDK 1.x (`mcp>=1.27,<2`, resolved as 1.28.1) over stdio. M1 exposes the five read tools plus the narrow `brain_record_outcome` and `brain_record_decision` tools. It has no generic remember/delete, filesystem, shell, review, projector, migration, or build-switch tool. Write tools require both an allowed-tool entry and the separate profile write grant, which defaults off.

Run locally on mini-core with explicit configuration:

```sh
BRAIN_JOURNAL_DB=spike/spike.db BRAIN_RETRIEVAL_DB=sqlite-spike/retrieval.db \
BRAIN_ALLOWED_WORKSPACES=ai-pos,personal .venv/bin/python scripts/run_brain_mcp.py
```

Desktop clients launch a copied `~/bin/run_brain_mcp_ssh.sh` through SSH key authentication and stdio, with no forwarding or TCP listener. Install it from a checkout with `scripts/install_brain_mcp_client_launcher.sh`; Claude Desktop must not execute a launcher from `~/Documents`. The installer leaves a matching copy untouched and backs up a different regular file before replacing it. Read scope defaults to the profile and a request may only narrow it. A write may omit workspace only for a one-workspace profile. A caller-provided `sensitive_allowed=true` cannot create a grant: sensitive access requires an explicit server-side grant as well as request scope.

Every generated desktop configuration sets `BRAIN_MCP_SSH_HOST` and `BRAIN_MCP_REMOTE_ROOT` explicitly. The standard host is `mini`; the remote root is the Pavol-Brain checkout on mini-core. A copied launcher refuses to start without an explicit remote root unless it is itself colocated with a verified checkout. It checks the remote root, `.venv/bin/python`, and `scripts/run_brain_mcp.py` before starting MCP and prints an exact diagnostic for a missing prerequisite. `BRAIN_CLIENT_ROOT` is only a compatibility fallback for a colocated launcher. Do not commit host credentials, keys, tokens, or generated machine-specific client configs.

Hermes is configured under `mcp_servers.pavol-brain` in `~/.hermes/config.yaml`; remove with `hermes mcp remove pavol-brain`. Codex is configured under `mcp_servers.pavol-brain` in `~/.codex/config.toml`; remove with `codex mcp remove pavol-brain`. Claude Desktop uses `mcpServers.pavol-brain` in `~/Library/Application Support/Claude/claude_desktop_config.json`; remove only that JSON member and restart Claude. Restore the timestamped backups if targeted rollback is needed.

Agents preserve `record_id`, `source_event_id`, projection hash, and retrieval build ID. Profiles are bound to `personal`, `work`, or the read-only migration fallback `legacy`; a request cannot choose the instance. Runtime logs stay outside Git. ChatGPT remains outside this MVP.

Live status: Hermes 0.18.2 passed a real agent retrieval and preserved record/event provenance. Codex CLI 0.144.0 configured and its MCP definition is present, but the real agent smoke failed first on a missing `codex-code-mode-host` and then client-cancelled the direct call, so it is not an acceptance pass. Claude Desktop 1.20186.1 is configured but has no installed scriptable CLI for a real-agent smoke, so execution is not evaluated.
