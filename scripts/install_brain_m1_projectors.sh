#!/bin/sh
set -eu
root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
state="${BRAIN_STATE_DIR:-$HOME/Library/Application Support/Pavol-Brain}"
test "${BRAIN_M1_APPROVED:-}" = "yes" || { echo "BRAIN_M1_APPROVED=yes is required after manifest/parity review" >&2; exit 64; }
manifest="${BRAIN_BOOTSTRAP_MANIFEST:?BRAIN_BOOTSTRAP_MANIFEST is required}"
python="${BRAIN_PYTHON:-$root/.venv/bin/python}"
"$python" -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("published") and not d.get("cross_partition_references") and d.get("source_sha256_before")==d.get("source_sha256_after")' "$manifest"
for instance in personal work; do
  test -f "$state/$instance/journal.db"
  test -f "$state/$instance/retrieval.db"
  BRAIN_INSTANCE="$instance" BRAIN_INSTALL_ONLY=true "$root/scripts/install_brain_launchagent.sh"
done
if test "${BRAIN_ACTIVATE_PROJECTORS:-no}" != "yes"; then
  echo "Both plists prepared and linted; set BRAIN_ACTIVATE_PROJECTORS=yes for the separately approved activation step"
  exit 0
fi
domain="gui/$(id -u)"
personal="$HOME/Library/LaunchAgents/com.pavol.brain-projector-personal.plist"
work="$HOME/Library/LaunchAgents/com.pavol.brain-projector-work.plist"
launchctl bootout "$domain/com.pavol.brain-projector-personal" 2>/dev/null || true
launchctl bootout "$domain/com.pavol.brain-projector-work" 2>/dev/null || true
launchctl bootstrap "$domain" "$personal"
if ! launchctl bootstrap "$domain" "$work"; then
  launchctl bootout "$domain/com.pavol.brain-projector-personal" 2>/dev/null || true
  exit 1
fi
launchctl kickstart "$domain/com.pavol.brain-projector-personal"
launchctl kickstart "$domain/com.pavol.brain-projector-work"
