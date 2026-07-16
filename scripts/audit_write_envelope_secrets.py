#!/usr/bin/env python3
"""Read-only Package 4 secret-pattern audit (write-safety-integrity-repair-
spec.md B6, I7, I8, Section 11 Package 4 migration deliverable).

Opens one or more journals **read-only** and scans every client-controlled
text field a rejected write would otherwise have gone through Band C
(brain/write_policy.py::looks_like_secret) for secret-shaped content that
predates Package 4 or was written outside the writer:

  - memory_records.payload (parsed JSON: dict keys and values, any nesting)
  - memory_records.raw_input (parsed JSON: dict keys and values, any nesting
    -- this is the one column that can carry a raw, unhashed client
    idempotency_key)
  - memory_records.source_excerpt / source_ref / session_ref (provenance
    scalar columns)
  - record_state.change_reason
  - artifact_links.artifact_uri
  - memory_events.data (parsed JSON: dict keys and values, any nesting --
    covers change_reason/possible_duplicate_of on supersede/duplicate events)

This is a detection pass only: it never writes to the journal and it never
prints a matched value. Findings report record_id, event_id (where
applicable), and a dotted field path only.

Usage:
    scripts/audit_write_envelope_secrets.py --journal personal=/path/to/personal/journal.db \
                                             --journal work=/path/to/work/journal.db \
                                             [--journal legacy=/path/to/spike.db]

A label of "legacy" (or containing "legacy"/"spike") is treated as
informational only: its findings are reported but never affect the exit
code, matching the spec's "legacy/spike files stay exempt" posture used
elsewhere (B2, Package 1).

Exit codes:
    0  no secret-shaped content found in any non-legacy journal
    1  at least one secret-shaped match found in a personal/work journal
    2  a given journal path does not exist or could not be opened read-only
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brain.write_policy import looks_like_secret


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


def _is_legacy(label):
    return "legacy" in label.lower() or "spike" in label.lower()


def _walk(value, path, hits):
    """Depth-first walk mirroring write_policy.collect_client_strings, but
    path-tracking instead of value-collecting: dict keys and values, list/
    tuple elements, at any nesting depth.

    Field-path labels never contain any substring of a value or key that
    matched looks_like_secret: a dict key IS the secret in the B6 attack
    shape, so the redacted placeholder is a fixed literal with no key
    content, and it also replaces the key in the path used to recurse into
    that key's value (a secret key's own text must not leak into any
    descendant path label either)."""
    if isinstance(value, str):
        if looks_like_secret(value):
            hits.append(path)
    elif isinstance(value, dict):
        for key, item in value.items():
            if looks_like_secret(key):
                hits.append(f"{path}.<dict-key:REDACTED>")
                _walk(item, f"{path}.<dict-key:REDACTED>.<value>", hits)
            else:
                label = key if len(key) <= 40 else key[:40] + "…"
                _walk(item, f"{path}.{label}", hits)
    elif isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _walk(item, f"{path}[{i}]", hits)


def _scan_json_column(raw_text, path, hits):
    try:
        parsed = json.loads(raw_text) if raw_text is not None else None
    except (TypeError, ValueError):
        # Not valid JSON -- scan the raw text itself rather than skip it.
        if raw_text and looks_like_secret(raw_text):
            hits.append(path)
        return
    _walk(parsed, path, hits)


def audit_journal(path, label):
    """Return a list of finding dicts: {journal, record_id, event_id, field_path}."""
    con = open_readonly(path)
    try:
        findings = []
        for row in con.execute(
            "SELECT record_id, payload, raw_input, source_excerpt, source_ref, session_ref FROM memory_records"
        ):
            rid = row["record_id"]
            hits = []
            _scan_json_column(row["payload"], "payload", hits)
            _scan_json_column(row["raw_input"], "raw_input", hits)
            for h in hits: findings.append({"journal": label, "record_id": rid, "event_id": None, "field_path": h})
            for col in ("source_excerpt", "source_ref", "session_ref"):
                value = row[col]
                if value and looks_like_secret(value):
                    findings.append({"journal": label, "record_id": rid, "event_id": None, "field_path": col})

        for row in con.execute("SELECT record_id, change_reason FROM record_state WHERE change_reason IS NOT NULL"):
            if looks_like_secret(row["change_reason"]):
                findings.append({"journal": label, "record_id": row["record_id"], "event_id": None, "field_path": "change_reason"})

        for row in con.execute("SELECT record_id, artifact_uri FROM artifact_links"):
            if looks_like_secret(row["artifact_uri"]):
                findings.append({"journal": label, "record_id": row["record_id"], "event_id": None, "field_path": "artifact_links.artifact_uri"})

        for row in con.execute("SELECT record_id, event_id, data FROM memory_events"):
            hits = []
            _scan_json_column(row["data"], "memory_events.data", hits)
            for h in hits: findings.append({"journal": label, "record_id": row["record_id"], "event_id": row["event_id"], "field_path": h})

        return findings
    finally:
        con.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--journal", action="append", required=True, metavar="LABEL=PATH",
                         help="repeatable; e.g. --journal personal=/path/journal.db. "
                              "Package 4 acceptance is personal+work; a legacy/spike journal is an optional, "
                              "informational-only extra input.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON instead of a text summary")
    args = parser.parse_args(argv)

    all_findings = []
    scanned = []
    for spec in args.journal:
        if "=" not in spec:
            parser.error(f"--journal must be LABEL=PATH, got: {spec!r}")
        label, path = spec.split("=", 1)
        if not Path(path).is_file():
            print(f"audit_write_envelope_secrets: journal not found: {label}={path}", file=sys.stderr)
            return 2
        try:
            findings = audit_journal(path, label)
        except sqlite3.Error as exc:
            print(f"audit_write_envelope_secrets: could not open {label}={path} read-only: {exc}", file=sys.stderr)
            return 2
        all_findings.extend(findings)
        scanned.append((label, path))

    blocking = [f for f in all_findings if not _is_legacy(f["journal"])]
    informational = [f for f in all_findings if _is_legacy(f["journal"])]

    if args.json:
        print(json.dumps({"scanned": [{"label": l, "path": p} for l, p in scanned],
                          "findings": all_findings, "blocking_count": len(blocking),
                          "informational_count": len(informational)}, indent=2))
    else:
        print(f"audit_write_envelope_secrets: scanned {len(scanned)} journal(s): "
              f"{', '.join(f'{l}={p}' for l, p in scanned)}")
        if not all_findings:
            print("no secret-shaped content found in payload/raw_input/provenance/artifact-uri/"
                  "change_reason/memory_events fields")
        for f in all_findings:
            role = "informational" if _is_legacy(f["journal"]) else "blocking"
            evt = f" event={f['event_id']}" if f["event_id"] else ""
            print(f"[{f['journal']}] ({role}) record={f['record_id']}{evt} field={f['field_path']}")
        print(f"\n{len(blocking)} blocking finding(s), {len(informational)} informational-only finding(s)")

    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
