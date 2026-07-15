#!/usr/bin/env python3
"""One-time backfill: stamp the persisted ``brain_instance_identity`` marker
(write-safety-integrity-repair-spec.md §3 B2) into a live Personal or WORK
journal that predates Package 1.

Backup-first and digest-verified, mirroring ``brain/migrations.py``'s
``migrate_m1`` pattern: the journal is backed up before any write, and the
backup's content digest is checked against the original before the stamp is
applied. Idempotent: re-running against an already-stamped journal is a
no-op success. Refuses — never silently repairs — when:

  * the journal already carries a marker for a *different* instance,
  * the journal's own workspace content is not a subset of the requested
    instance's workspace partition (``PERSONAL_WORKSPACES``/``WORK_WORKSPACES``
    from ``brain/control.py`` — the same single source of truth bootstrap's
    preflight now checks against),
  * integrity or foreign-key checks fail.

No path is guessed or defaulted from environment/user directories: the
journal path and instance id are both required, explicit arguments.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain import instance_identity
from brain.control import PERSONAL_WORKSPACES, WORK_WORKSPACES
from scripts.bootstrap_brain_instances import inspect_target, logical_digest, readonly, sha  # noqa: E402

PARTITIONS = {"personal": PERSONAL_WORKSPACES, "work": WORK_WORKSPACES}


def preflight(journal_path, instance_id):
    """Read-only checks. Returns a report dict; never mutates anything."""
    info = inspect_target(journal_path)
    report = {"journal": str(journal_path), "instance_id": instance_id, "exists": info is not None}
    if info is None:
        report["blocked"] = "journal_missing"
        return report
    report.update({"sha256": info["sha256"], "logical_digest": info["logical_digest"],
                   "integrity_check": info["integrity_check"], "existing_marker": info["marker"]})
    if info["integrity_check"] != "ok" or info["logical_digest"] is None:
        report["blocked"] = "integrity_check_failed"
        return report
    if info["marker"] is not None:
        if info["marker"]["instance_id"] == instance_id:
            report["blocked"] = None
            report["already_stamped"] = True
            return report
        report["blocked"] = "marker_mismatch"
        return report
    con = readonly(journal_path)
    try:
        workspaces = {row[0] for row in con.execute("SELECT DISTINCT workspace FROM memory_records")}
    finally:
        con.close()
    foreign = workspaces - PARTITIONS[instance_id]
    report["workspaces"] = sorted(workspaces)
    report["foreign_workspaces"] = sorted(foreign)
    if foreign:
        report["blocked"] = "workspace_partition_violation"
        return report
    report["blocked"] = None
    report["already_stamped"] = False
    return report


def backup(journal_path, backup_path):
    if backup_path.exists():
        raise FileExistsError(f"backup target already exists: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(journal_path)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    before = inspect_target(journal_path)
    after = inspect_target(backup_path)
    if before["logical_digest"] != after["logical_digest"]:
        backup_path.unlink(missing_ok=True)
        raise RuntimeError("backup verification failed: digest mismatch")
    return after


CANONICAL_TABLES = ("memory_records", "memory_events", "record_state", "artifact_links", "artifact_validation_events")


def apply_stamp(journal_path, instance_id, source_digest):
    """Stamp the marker in its own transaction, then verify the stamp is
    exactly what landed: correct instance/digest, integrity intact. Callers
    are responsible for verifying the canonical tables are otherwise
    untouched (they can't be, by construction — the marker is a new table —
    but that is proven independently in ``main()`` since it is the property
    that actually matters here, not an implementation detail of this
    function)."""
    con = sqlite3.connect(journal_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        instance_identity.stamp_journal_marker(con, instance_id, source_digest)
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise
    else:
        con.close()
    after = inspect_target(journal_path)
    if after["integrity_check"] != "ok":
        raise RuntimeError("post-stamp integrity_check failed")
    marker = after["marker"]
    if not marker or marker["instance_id"] != instance_id or marker["source_digest"] != source_digest:
        raise RuntimeError("post-stamp marker verification failed")
    return after, marker


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal-db", type=Path, required=True)
    p.add_argument("--instance-id", choices=("personal", "work"), required=True)
    p.add_argument("--source-digest",
                    help="digest to record in the marker; defaults to the journal's own current logical_digest "
                         "(self-referential — this is a backfill, not a fresh bootstrap split, so there is no "
                         "legacy snapshot digest to bind to unless one is explicitly supplied, e.g. from the "
                         "original bootstrap manifest for continuity)")
    p.add_argument("--backup-path", type=Path,
                    help="defaults to <journal-db>.pre-instance-stamp-backup.db next to the journal")
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()

    report = preflight(a.journal_db, a.instance_id)
    if report.get("already_stamped"):
        print_report(report)
        return 0
    if report.get("blocked"):
        print_report(report)
        return 2

    con = readonly(a.journal_db)
    try:
        canonical_before = logical_digest(con, CANONICAL_TABLES)
    finally:
        con.close()
    source_digest = a.source_digest or report["logical_digest"]
    report["source_digest_to_stamp"] = source_digest

    if not a.apply:
        report["dry_run"] = True
        print_report(report)
        return 0

    backup_path = a.backup_path or a.journal_db.with_name(a.journal_db.name + ".pre-instance-stamp-backup.db")
    backed_up = backup(a.journal_db, backup_path)
    report["backup_path"] = str(backup_path)
    report["backup_sha256"] = backed_up["sha256"]
    try:
        after, marker = apply_stamp(a.journal_db, a.instance_id, source_digest)
        con = readonly(a.journal_db)
        try:
            canonical_after = logical_digest(con, CANONICAL_TABLES)
        finally:
            con.close()
        if canonical_after != canonical_before:
            raise RuntimeError("canonical tables changed during stamping; this must never happen")
    except Exception:
        restore = sqlite3.connect(backup_path)
        target = sqlite3.connect(a.journal_db)
        try:
            restore.backup(target)
        finally:
            target.close(); restore.close()
        report["restored_from_backup"] = True
        print_report(report)
        raise
    report["stamped"] = True
    report["marker"] = marker
    print_report(report)
    return 0


def print_report(report):
    import json
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
