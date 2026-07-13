# Pavol-Brain MCP integration

The adapter uses official Python MCP SDK 1.x (`mcp>=1.27,<2`, resolved as 1.28.1) over stdio. M1 exposes the five read tools plus the narrow `brain_record_outcome` and `brain_record_decision` tools. It has no generic remember/delete, filesystem, shell, review, projector, migration, or build-switch tool. Write tools require both an allowed-tool entry and the separate profile write grant, which defaults off.

Run locally on mini-core with explicit configuration:

```sh
BRAIN_JOURNAL_DB=spike/spike.db BRAIN_RETRIEVAL_DB=sqlite-spike/retrieval.db \
BRAIN_ALLOWED_WORKSPACES=ai-pos,personal .venv/bin/python scripts/run_brain_mcp.py
```

MBP clients launch `scripts/run_brain_mcp_ssh.sh`; it uses SSH key authentication and stdio, with no forwarding or TCP listener. Read scope defaults to the profile and a request may only narrow it. A write may omit workspace only for a one-workspace profile. A caller-provided `sensitive_allowed=true` cannot create a grant: sensitive access requires an explicit server-side grant as well as request scope.

The wrapper defaults to the SSH alias `mini-core` and derives the repository root from its own checkout. Override portability-sensitive values with `BRAIN_MCP_SSH_HOST`, `BRAIN_MCP_REMOTE_ROOT`, or `BRAIN_CLIENT_ROOT`; do not commit host credentials, keys, tokens, or generated machine-specific client configs.

Hermes is configured under `mcp_servers.pavol-brain` in `~/.hermes/config.yaml`; remove with `hermes mcp remove pavol-brain`. Codex is configured under `mcp_servers.pavol-brain` in `~/.codex/config.toml`; remove with `codex mcp remove pavol-brain`. Claude Desktop uses `mcpServers.pavol-brain` in `~/Library/Application Support/Claude/claude_desktop_config.json`; remove only that JSON member and restart Claude. Restore the timestamped backups if targeted rollback is needed.

Agents preserve `record_id`, `source_event_id`, projection hash, and retrieval build ID. Profiles are bound to `personal`, `work`, or the read-only migration fallback `legacy`; a request cannot choose the instance. Runtime logs stay outside Git. ChatGPT remains outside this MVP.

Live status: Hermes 0.18.2 passed a real agent retrieval and preserved record/event provenance. Codex CLI 0.144.0 configured and its MCP definition is present, but the real agent smoke failed first on a missing `codex-code-mode-host` and then client-cancelled the direct call, so it is not an acceptance pass. Claude Desktop 1.20186.1 is configured but has no installed scriptable CLI for a real-agent smoke, so execution is not evaluated.
