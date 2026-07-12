#!/bin/sh
set -eu
host="${BRAIN_MCP_SSH_HOST:-pavol@192.168.50.109}"
root="${BRAIN_MCP_REMOTE_ROOT:-/Users/pavol/Documents/Personal/Projects/pavol-brain}"
exec ssh -T -o BatchMode=yes "$host" "cd '$root' && exec .venv/bin/python scripts/run_brain_mcp.py"
