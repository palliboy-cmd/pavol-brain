#!/bin/sh
# SSH-stdio MCP launcher. Safe to copy outside the repository (for example ~/bin).
set -eu

host="${BRAIN_MCP_SSH_HOST:-mini}"
integration_id="${BRAIN_INTEGRATION_ID:-}"
instance="${BRAIN_INSTANCE:-legacy}"

fail() { echo "Pavol-Brain MCP launcher: $*" >&2; exit 64; }
quote() { printf "'%s'" "$1"; }

test -n "$integration_id" || fail "BRAIN_INTEGRATION_ID is required"
case "$instance" in personal|work|legacy) ;; *) fail "BRAIN_INSTANCE must be personal, work, or legacy";; esac

# A copied launcher cannot infer the remote checkout from its own path. A
# colocated checkout is a compatibility fallback only; every generated client
# configuration supplies BRAIN_MCP_REMOTE_ROOT explicitly.
root="${BRAIN_MCP_REMOTE_ROOT:-}"
if [ -z "$root" ]; then
  client_root="${BRAIN_CLIENT_ROOT:-$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)}"
  if [ -x "$client_root/.venv/bin/python" ] && [ -f "$client_root/scripts/run_brain_mcp.py" ]; then
    root="$client_root"
  else
    fail "BRAIN_MCP_REMOTE_ROOT is required when the launcher is outside a Pavol-Brain checkout"
  fi
fi
case "$integration_id$root$instance" in *"'"*) fail "single quotes are not supported in MCP wrapper configuration";; esac

qroot=$(quote "$root")
qinstance=$(quote "$instance")
qintegration=$(quote "$integration_id")
remote_command="
root=$qroot
python=\"\$root/.venv/bin/python\"
entry=\"\$root/scripts/run_brain_mcp.py\"
if [ ! -d \"\$root\" ]; then echo \"Pavol-Brain MCP launcher: remote root missing: \$root\" >&2; exit 66; fi
if [ ! -x \"\$python\" ]; then echo \"Pavol-Brain MCP launcher: remote Python missing or not executable: \$python\" >&2; exit 66; fi
if [ ! -f \"\$entry\" ]; then echo \"Pavol-Brain MCP launcher: remote MCP entry point missing: \$entry\" >&2; exit 66; fi
cd \"\$root\" || { echo \"Pavol-Brain MCP launcher: cannot cd to remote root: \$root\" >&2; exit 66; }
case $qinstance in
  personal) journal=\"\$HOME/Library/Application Support/Pavol-Brain/personal/journal.db\"; retrieval=\"\$HOME/Library/Application Support/Pavol-Brain/personal/retrieval.db\" ;;
  work) journal=\"\$HOME/Library/Application Support/Pavol-Brain/work/journal.db\"; retrieval=\"\$HOME/Library/Application Support/Pavol-Brain/work/retrieval.db\" ;;
  legacy) journal=\"\$root/spike/spike.db\"; retrieval=\"\$root/sqlite-spike/retrieval.db\" ;;
esac
exec env BRAIN_INSTANCE=$qinstance BRAIN_JOURNAL_DB=\"\$journal\" BRAIN_RETRIEVAL_DB=\"\$retrieval\" BRAIN_CONTROL_DB=\"\$HOME/Library/Application Support/Pavol-Brain/brain-control.db\" BRAIN_INTEGRATION_ID=$qintegration BRAIN_CLIENT_IDENTITY=$qintegration BRAIN_AUDIT_LOG=\"\$HOME/Library/Logs/Pavol-Brain/audit.jsonl\" \"\$python\" \"\$entry\"
"

exec ssh -T -o BatchMode=yes "$host" "$remote_command"
