#!/bin/sh
set -eu
host="${BRAIN_MCP_SSH_HOST:-mini-core}"
local_root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
root="${BRAIN_MCP_REMOTE_ROOT:-$local_root}"
integration_id="${BRAIN_INTEGRATION_ID:-}"
instance="${BRAIN_INSTANCE:-legacy}"
test -n "$integration_id" || { echo "BRAIN_INTEGRATION_ID is required" >&2; exit 64; }
case "$instance" in
  personal) journal='\$HOME/Library/Application Support/Pavol-Brain/personal/journal.db'; retrieval='\$HOME/Library/Application Support/Pavol-Brain/personal/retrieval.db';;
  work) journal='\$HOME/Library/Application Support/Pavol-Brain/work/journal.db'; retrieval='\$HOME/Library/Application Support/Pavol-Brain/work/retrieval.db';;
  legacy) journal='spike/spike.db'; retrieval='sqlite-spike/retrieval.db';;
  *) echo "BRAIN_INSTANCE must be personal, work, or legacy" >&2; exit 64;;
esac
case "$integration_id$root$instance" in *"'"*) echo "single quotes are not supported in MCP wrapper configuration" >&2; exit 64;; esac
exec ssh -T -o BatchMode=yes "$host" "cd '$root' && BRAIN_INSTANCE='$instance' BRAIN_JOURNAL_DB=\"$journal\" BRAIN_RETRIEVAL_DB=\"$retrieval\" BRAIN_CONTROL_DB=\"\$HOME/Library/Application Support/Pavol-Brain/brain-control.db\" BRAIN_INTEGRATION_ID='$integration_id' BRAIN_CLIENT_IDENTITY='$integration_id' BRAIN_AUDIT_LOG=\"\$HOME/Library/Logs/Pavol-Brain/audit.jsonl\" exec .venv/bin/python scripts/run_brain_mcp.py"
