#!/bin/sh
set -eu
host="${BRAIN_MCP_SSH_HOST:-pavol@192.168.50.109}"
root="${BRAIN_MCP_REMOTE_ROOT:-/Users/pavol/Documents/Personal/Projects/pavol-brain}"
integration_id="${BRAIN_INTEGRATION_ID:-}"
test -n "$integration_id" || { echo "BRAIN_INTEGRATION_ID is required" >&2; exit 64; }
case "$integration_id$root" in *"'"*) echo "single quotes are not supported in MCP wrapper configuration" >&2; exit 64;; esac
exec ssh -T -o BatchMode=yes "$host" "cd '$root' && BRAIN_JOURNAL_DB=spike/spike.db BRAIN_RETRIEVAL_DB=sqlite-spike/retrieval.db BRAIN_CONTROL_DB=\"\$HOME/Library/Application Support/Pavol-Brain/brain-control.db\" BRAIN_INTEGRATION_ID='$integration_id' BRAIN_CLIENT_IDENTITY='$integration_id' BRAIN_AUDIT_LOG=\"\$HOME/Library/Logs/Pavol-Brain/audit.jsonl\" exec .venv/bin/python scripts/run_brain_mcp.py"
