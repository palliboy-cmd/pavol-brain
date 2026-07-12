# Pavol-Brain MCP integration

The Slice 4 adapter uses official Python MCP SDK 1.x (`mcp>=1.27,<2`, resolved as 1.28.1) over stdio. It exposes exactly `brain_search`, `brain_get_record`, `brain_get_related`, `brain_health`, and `brain_rebuild_status`. It has no mutation, filesystem, shell, projector, migration, or build-switch tool.

Run locally on mini-core with explicit configuration:

```sh
BRAIN_JOURNAL_DB=spike/spike.db BRAIN_RETRIEVAL_DB=sqlite-spike/retrieval.db \
BRAIN_ALLOWED_WORKSPACES=ai-pos,personal .venv/bin/python scripts/run_brain_mcp.py
```

MBP clients launch `scripts/run_brain_mcp_ssh.sh`; it uses SSH key authentication and stdio, with no forwarding or TCP listener. The default profile grants only `ai-pos` and `personal`. Requests must name workspaces explicitly. A caller-provided `sensitive_allowed=true` cannot create a grant: sensitive access requires an explicit server-side `BRAIN_SENSITIVE_GRANTS` entry as well as request scope. The default has no sensitive grants.

Hermes is configured under `mcp_servers.pavol-brain` in `~/.hermes/config.yaml`; remove with `hermes mcp remove pavol-brain`. Codex is configured under `mcp_servers.pavol-brain` in `~/.codex/config.toml`; remove with `codex mcp remove pavol-brain`. Claude Desktop uses `mcpServers.pavol-brain` in `~/Library/Application Support/Claude/claude_desktop_config.json`; remove only that JSON member and restart Claude. Restore the timestamped backups if targeted rollback is needed.

Agents should always pass explicit workspaces, preserve `record_id`, `source_event_id`, projection hash, and retrieval build ID, and treat results as read-only evidence. Runtime logs stay outside Git. ChatGPT remains outside this MVP.

Live status: Hermes 0.18.2 passed a real agent retrieval and preserved record/event provenance. Codex CLI 0.144.0 configured and its MCP definition is present, but the real agent smoke failed first on a missing `codex-code-mode-host` and then client-cancelled the direct call, so it is not an acceptance pass. Claude Desktop 1.20186.1 is configured but has no installed scriptable CLI for a real-agent smoke, so execution is not evaluated.
