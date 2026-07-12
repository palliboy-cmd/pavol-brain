#!/bin/sh
set -eu
root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)";python="${BRAIN_PYTHON:-$root/.venv/bin/python}"
state="${BRAIN_STATE_DIR:-$HOME/Library/Application Support/Pavol-Brain}";logs="${BRAIN_LOG_DIR:-$HOME/Library/Logs/Pavol-Brain}";target="$HOME/Library/LaunchAgents/com.pavol.brain-control-center.plist"
mkdir -p "$state" "$logs" "$(dirname "$target")";test -x "$python"
if test -f "$state/brain-control.db";then sqlite3 "$state/brain-control.db" ".backup '$state/brain-control.db.backup.$(date -u +%Y%m%dT%H%M%SZ)'";fi
if test -f "$target";then cp -p "$target" "$target.backup.$(date -u +%Y%m%dT%H%M%SZ)";fi
sed -e "s|__PYTHON__|$python|g" -e "s|__ROOT__|$root|g" -e "s|__STATE__|$state|g" -e "s|__LOGS__|$logs|g" operations/com.pavol.brain-control-center.plist.template > "$target"
plutil -lint "$target";launchctl bootout "gui/$(id -u)/com.pavol.brain-control-center" 2>/dev/null||true;launchctl bootstrap "gui/$(id -u)" "$target";launchctl kickstart -k "gui/$(id -u)/com.pavol.brain-control-center"
