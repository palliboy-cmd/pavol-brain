#!/usr/bin/env python3
"""Operator migration: add the additive artifact-validation tables to the canonical journal.

Dry-run by default. ``--apply`` requires ``--backup`` and refuses to run without a
verified SQLite-consistent backup. Existing canonical rows are never updated or
deleted; a content digest over the pre-existing canonical tables proves it.
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain import artifact_validation as av


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_journal_identity(con):
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in av.CANONICAL_TABLES if t not in tables]
    if missing:
        raise SystemExit(f"refusing: journal identity mismatch, missing canonical tables {missing}")
    return {
        "memory_records": con.execute("SELECT count(*) FROM memory_records").fetchone()[0],
        "memory_events": con.execute("SELECT count(*) FROM memory_events").fetchone()[0],
        "record_state": con.execute("SELECT count(*) FROM record_state").fetchone()[0],
        "artifact_links": con.execute("SELECT count(*) FROM artifact_links").fetchone()[0],
    }


def make_backup(journal, backup_path, expected_counts):
    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        raise SystemExit(f"refusing: backup target already exists: {backup_path}")
    source = sqlite3.connect(journal)
    target = sqlite3.connect(backup_path)
    with target:
        source.backup(target)
    source.close()
    check = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
    integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
    counts = verify_journal_identity(check)
    check.close()
    if integrity != "ok" or counts != expected_counts:
        raise SystemExit(f"refusing: backup verification failed integrity={integrity} counts={counts}")
    return {"path": str(backup_path), "integrity_check": integrity, "table_counts": counts, "sha256": sha(backup_path)}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--journal-db", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--backup", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()

    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "journal_path": str(a.journal_db), "mode": "apply" if a.apply else "dry_run", "journal_sha256_before": sha(a.journal_db)}
    ro = sqlite3.connect(f"file:{a.journal_db.resolve()}?mode=ro", uri=True)
    report["table_counts_before"] = verify_journal_identity(ro)
    report["validation_tables_before"] = av.tables_present(ro)
    report["canonical_table_digest_before"] = av.canonical_table_digest(ro)
    ro.close()

    if not a.apply:
        report["planned"] = {"ddl": av.SCHEMA_PATH.name, "creates": list(av.TABLES), "additive_only": True}
        report["journal_sha256_after"] = sha(a.journal_db)
        report["journal_unchanged"] = True
    else:
        if not a.backup:
            raise SystemExit("refusing: --apply requires --backup <path>")
        report["backup"] = make_backup(a.journal_db, a.backup, report["table_counts_before"])
        con = sqlite3.connect(a.journal_db)
        try:
            report["migration"] = av.apply_migration(con)
            report["state_rows_after_fold"] = av.rebuild_state(con)
            report["fold_mismatches"] = av.verify_state(con)
            con.commit()
        except Exception:
            con.rollback()
            raise
        report["canonical_table_digest_after"] = av.canonical_table_digest(con)
        report["canonical_tables_unchanged"] = report["canonical_table_digest_after"] == report["canonical_table_digest_before"]
        report["validation_tables_after"] = av.tables_present(con)
        con.close()
        report["journal_sha256_after"] = sha(a.journal_db)
        if not report["canonical_tables_unchanged"] or report["fold_mismatches"]:
            raise SystemExit(f"migration verification failed: {report}")

    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.output:
        a.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
