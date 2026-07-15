#!/bin/sh
set -eu
root="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
python="${BRAIN_PYTHON:-$root/.venv/bin/python}"
state="${BRAIN_STATE_DIR:-$HOME/Library/Application Support/Pavol-Brain}"
manifest="${BRAIN_BOOTSTRAP_MANIFEST:?BRAIN_BOOTSTRAP_MANIFEST is required}"
"$python" -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("published") and not d.get("cross_partition_references") and d.get("source_sha256_before")==d.get("source_sha256_after")' "$manifest"
for instance in personal work; do
  journal="$state/$instance/journal.db"
  retrieval="$state/$instance/retrieval.db"
  test -f "$journal"
  test ! -e "$retrieval" || { echo "$retrieval already exists; build requires a fresh target" >&2; exit 64; }
  "$python" "$root/scripts/run_brain_projector.py" --journal-db "$journal" --retrieval-db "$retrieval" \
    --instance-id "$instance" --run-once --validate --batch-size 1000000 --output "$state/$instance/projector-build-manifest.json"
done
