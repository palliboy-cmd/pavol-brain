#!/bin/sh
set -eu
root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
python="${BRAIN_PYTHON:-$root/.venv/bin/python}"
state="${BRAIN_STATE_DIR:-$HOME/Library/Application Support/Pavol-Brain}"
logs="${BRAIN_LOG_DIR:-$HOME/Library/Logs/Pavol-Brain}"
instance="${BRAIN_INSTANCE:-legacy}"
case "$instance" in
  legacy) label="com.pavol.brain-projector"; journal="$root/spike/spike.db"; retrieval="$root/sqlite-spike/retrieval.db";;
  personal|work) label="com.pavol.brain-projector-$instance"; journal="$state/$instance/journal.db"; retrieval="$state/$instance/retrieval.db";;
  *) echo "BRAIN_INSTANCE must be personal, work, or legacy" >&2; exit 64;;
esac
target="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$state" "$logs" "$(dirname "$target")"
test -x "$python"
if test -f "$target"; then cp "$target" "$target.backup.$(date -u +%Y%m%dT%H%M%SZ)"; fi
sed -e "s|__PYTHON__|$python|g" -e "s|__ROOT__|$root|g" -e "s|__STATE__|$state|g" -e "s|__LOGS__|$logs|g" \
    -e "s|__LABEL__|$label|g" -e "s|__INSTANCE__|$instance|g" -e "s|__JOURNAL__|$journal|g" -e "s|__RETRIEVAL__|$retrieval|g" \
    "$root/operations/com.pavol.brain-projector.plist.template" > "$target"
plutil -lint "$target"
if test "${BRAIN_INSTALL_ONLY:-false}" = "true"; then
  echo "Prepared $target without activation"
  exit 0
fi
launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$target"
launchctl kickstart "gui/$(id -u)/$label"
