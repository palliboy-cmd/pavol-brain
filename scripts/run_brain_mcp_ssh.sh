#!/bin/sh
set -eu
host="${BRAIN_MCP_SSH_HOST:-pavol@192.168.50.109}"
root="${BRAIN_MCP_REMOTE_ROOT:-/Users/pavol/Documents/Personal/Projects/pavol-brain}"
allowed="${BRAIN_ALLOWED_WORKSPACES:-ai-pos,personal}"
profile="${BRAIN_CLIENT_PROFILE:-default-nonsensitive}"
case "$allowed$profile$root" in *"'"*) echo "single quotes are not supported in MCP wrapper configuration" >&2; exit 64;; esac
exec ssh -T -o BatchMode=yes "$host" "cd '$root' && BRAIN_JOURNAL_DB=spike/spike.db BRAIN_RETRIEVAL_DB=sqlite-spike/retrieval.db BRAIN_ALLOWED_WORKSPACES='$allowed' BRAIN_CLIENT_PROFILE='$profile' BRAIN_CLIENT_IDENTITY='$profile' BRAIN_AUDIT_LOG=\"\$HOME/Library/Logs/Pavol-Brain/audit.jsonl\" exec .venv/bin/python scripts/run_brain_mcp.py"
