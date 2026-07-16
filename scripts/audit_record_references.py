#!/usr/bin/env python3
"""Read-only Package 2 record:// reference audit (write-safety-integrity-repair-spec.md B3, I5, Section 11 Package 2).

Opens one or more journals **read-only** and reports every record-to-record
reference stored as a ``record://`` ``artifact_links`` row, classified by
origin (typed link via metadata.links[] vs. an evidence/artifact-field
reference — the latter is rejected at write time as of Package 2, but this
script also catches any that predate the fix or were written outside the
writer). Reuses brain/record_references.py as the single canonical reference
parser; issues no write statement.

Never prints payload text or source excerpts: only record IDs, workspaces,
statuses, relation names, and derived flags.

Usage:
    scripts/audit_record_references.py --journal personal=/path/to/personal/journal.db \
                                        --journal work=/path/to/work/journal.db \
                                        [--journal legacy=/path/to/spike.db]

Exit codes:
    0  no record:// references found, or every one found is a legitimate
       typed link (target exists, same workspace as source, not
       rejected/forgotten)
    1  at least one dangling, cross-workspace, rejected/forgotten-target, or
       evidence/artifact-field-origin record:// reference was found
    2  a given journal path does not exist or could not be opened read-only
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brain.record_references import journal_references

TYPED_LINK_RELATIONS = {"addresses", "analyzes", "decides", "implements", "results_in", "caused_by"}


def open_readonly(path):
    """Open path read-only. Falls back to immutable=1 for a static copy
    that lacks its live -wal/-shm companions (sqlite otherwise cannot open
    a WAL-mode file for reading without write access to create them)."""
    resolved = Path(path).resolve()
    uri = resolved.as_uri()
    try:
        con = sqlite3.connect(uri + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return con
    except sqlite3.OperationalError:
        con = sqlite3.connect(uri + "?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        return con


def _record_index(con):
    index = {}
    for row in con.execute(
        "SELECT r.record_id, r.workspace, s.status FROM memory_records r JOIN record_state s USING(record_id)"
    ):
        index[row["record_id"]] = {"workspace": row["workspace"], "status": row["status"]}
    return index


def audit_journal(path, label):
    """Return a list of finding dicts for every record:// artifact_links row in this journal."""
    con = open_readonly(path)
    try:
        index = _record_index(con)
        findings = []
        for ref in journal_references(con):
            if ref["field_path"] != "artifact_links.artifact_uri":
                continue
            source_id = ref["source_record"]
            target_id = ref["target_record"]
            relation = ref["relation"]
            source_meta = index.get(source_id)
            target_meta = index.get(target_id)
            dangling = target_meta is None
            source_workspace = source_meta["workspace"] if source_meta else None
            target_workspace = target_meta["workspace"] if target_meta else None
            cross_workspace = (not dangling) and source_workspace != target_workspace
            target_status = target_meta["status"] if target_meta else None
            target_rejected_or_forgotten = target_status in {"rejected", "forgotten"}
            origin = "typed_link" if relation in TYPED_LINK_RELATIONS else "evidence_or_artifact_field"
            findings.append({
                "journal": label,
                "source_record": source_id,
                "source_workspace": source_workspace,
                "target_record": target_id,
                "target_workspace": target_workspace,
                "target_status": target_status,
                "relation": relation,
                "origin": origin,
                "dangling": dangling,
                "cross_workspace": cross_workspace,
                "target_rejected_or_forgotten": target_rejected_or_forgotten,
                "forbidden_origin": origin == "evidence_or_artifact_field",
            })
        return findings
    finally:
        con.close()


def _is_bad(finding):
    return (finding["dangling"] or finding["cross_workspace"]
            or finding["forbidden_origin"] or finding["target_rejected_or_forgotten"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--journal", action="append", required=True, metavar="LABEL=PATH",
                         help="repeatable; e.g. --journal personal=/path/journal.db. "
                              "Package 2 acceptance is personal+work; a legacy/spike journal is an optional extra input.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a text table")
    args = parser.parse_args(argv)

    findings = []
    for spec in args.journal:
        if "=" not in spec:
            parser.error(f"--journal must be LABEL=PATH, got: {spec!r}")
        label, path = spec.split("=", 1)
        if not Path(path).is_file():
            print(f"audit_record_references: journal not found: {label}={path}", file=sys.stderr)
            return 2
        try:
            findings.extend(audit_journal(path, label))
        except sqlite3.Error as exc:
            print(f"audit_record_references: could not open {label}={path} read-only: {exc}", file=sys.stderr)
            return 2

    bad = [f for f in findings if _is_bad(f)]

    if args.json:
        print(json.dumps(findings, indent=2))
    else:
        if not findings:
            print("audit_record_references: no record:// references found in any audited journal")
        for f in findings:
            flags = ",".join(k for k in ("dangling", "cross_workspace", "forbidden_origin", "target_rejected_or_forgotten") if f[k]) or "ok"
            print(f"[{f['journal']}] {f['source_record']} ({f['source_workspace']}) --{f['relation']}--> "
                  f"{f['target_record']} ({f['target_workspace']}) status={f['target_status']} "
                  f"origin={f['origin']} flags={flags}")
        print(f"\n{len(findings)} record:// reference(s) found, {len(bad)} flagged")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
