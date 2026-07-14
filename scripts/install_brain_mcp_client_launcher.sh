#!/bin/sh
# Install the location-independent SSH stdio launcher for desktop clients.
set -eu

source_root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
source="$source_root/scripts/run_brain_mcp_ssh.sh"
destination="${BRAIN_MCP_CLIENT_LAUNCHER:-$HOME/bin/run_brain_mcp_ssh.sh}"

test -f "$source" || { echo "Pavol-Brain MCP installer: source launcher missing: $source" >&2; exit 66; }
mkdir -p "$(dirname "$destination")"
if [ -e "$destination" ] || [ -L "$destination" ]; then
  if [ -L "$destination" ] || [ ! -f "$destination" ]; then
    echo "Pavol-Brain MCP installer: refusing non-regular destination: $destination" >&2
    exit 66
  fi
  if cmp -s "$source" "$destination"; then
    printf '%s\n' "$destination"
    exit 0
  fi
  backup="$destination.backup.$(date -u +%Y%m%dT%H%M%SZ).$$"
  cp -p "$destination" "$backup"
  printf 'Pavol-Brain MCP installer: backed up existing launcher to %s\n' "$backup" >&2
fi
install -m 755 "$source" "$destination"
printf '%s\n' "$destination"
