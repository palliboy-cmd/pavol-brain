#!/usr/bin/env python3
"""Non-destructive, gated and all-or-nothing split of the legacy journal."""
import argparse, hashlib, json, os, shutil, sqlite3, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brain import artifact_validation as av
from brain.record_references import journal_references

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "spike/schema/journal.sql"
TABLES = ("memory_records", "memory_events", "record_state", "artifact_links", "artifact_validation_events")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def logical_digest(con, tables):
    digest = hashlib.sha256()
    for table in tables:
        for row in con.execute(f"SELECT * FROM {table} ORDER BY 1,2"):
            digest.update(table.encode())
            digest.update(json.dumps(tuple(row), ensure_ascii=False, separators=(",", ":")).encode())
    return digest.hexdigest()


def readonly(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")
    return con


def snapshot_source(source_path, snapshot_path):
    source = readonly(source_path)
    try:
        source.execute("BEGIN")
        digest = logical_digest(source, TABLES)
        target = sqlite3.connect(snapshot_path)
        try:
            source.backup(target)
        finally:
            target.close()
        source.commit()
        return digest
    finally:
        source.close()


def copy_rows(source, target, table, where, args):
    columns = [row[1] for row in source.execute(f"PRAGMA table_info({table})")]
    target_columns = {row[1] for row in target.execute(f"PRAGMA table_info({table})")}
    columns = [column for column in columns if column in target_columns]
    rows = source.execute(f"SELECT {','.join(columns)} FROM {table} WHERE {where}", args).fetchall()
    if rows:
        target.executemany(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' * len(columns))})", rows)
    return len(rows)


def reference_audit(con, personal, work):
    owner = {row[0]: row[1] for row in con.execute("SELECT record_id,workspace FROM memory_records")}
    partition = lambda workspace: "personal" if workspace in personal else "work" if workspace in work else None
    audit = []
    for ref in journal_references(con):
        source_workspace = ref.get("source_workspace") or owner.get(ref["source_record"])
        target_workspace = owner.get(ref["target_record"])
        row = {**ref, "source_workspace": source_workspace, "target_workspace": target_workspace,
               "source_partition": partition(source_workspace), "target_partition": partition(target_workspace)}
        row["status"] = "ok" if target_workspace and row["source_partition"] == row["target_partition"] else "blocking"
        audit.append(row)
    return audit


def inspect_snapshot(source_path, personal, work):
    con = readonly(source_path)
    try:
        workspaces = {row[0] for row in con.execute("SELECT DISTINCT workspace FROM memory_records")}
        overlap = personal & work
        missing = workspaces - personal - work
        extra = (personal | work) - workspaces
        counts = {table: con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in TABLES}
        refs = reference_audit(con, personal, work)
        return {"workspaces": sorted(workspaces), "personal_workspaces": sorted(personal),
                "work_workspaces": sorted(work), "overlap": sorted(overlap), "unassigned": sorted(missing),
                "unknown_requested": sorted(extra), "record_references": refs,
                "cross_partition_references": [row for row in refs if row["status"] == "blocking"],
                "record_count": con.execute("SELECT count(*) FROM memory_records").fetchone()[0],
                "table_counts": counts, "logical_digest": logical_digest(con, TABLES),
                "integrity_check": con.execute("PRAGMA integrity_check").fetchone()[0],
                "foreign_key_violations": [tuple(row) for row in con.execute("PRAGMA foreign_key_check")]}
    finally:
        con.close()


def build(source_path, target_path, workspaces):
    source = readonly(source_path)
    target_path = Path(target_path)
    if target_path.exists():
        raise FileExistsError(target_path)
    target = sqlite3.connect(target_path)
    try:
        target.executescript(SCHEMA.read_text())
        av.apply_migration(target)
        marks = ",".join("?" * len(workspaces))
        where = f"record_id IN (SELECT record_id FROM memory_records WHERE workspace IN ({marks}))"
        target.execute("BEGIN IMMEDIATE")
        counts = {"memory_records": copy_rows(source, target, "memory_records", f"workspace IN ({marks})", list(workspaces))}
        for table in ("memory_events", "record_state", "artifact_links", "artifact_validation_events"):
            key = "artifact_record_id" if table == "artifact_validation_events" else "record_id"
            counts[table] = copy_rows(source, target, table, f"{key} IN (SELECT record_id FROM memory_records WHERE workspace IN ({marks}))", list(workspaces))
        av.rebuild_state(target)
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in target.execute("PRAGMA foreign_key_check")]
        refs=reference_audit(target,set(workspaces),set())
        blocking_refs=[row for row in refs if row["status"]=="blocking"]
        if integrity != "ok" or foreign_keys or av.verify_state(target) or blocking_refs:
            raise RuntimeError("staging journal validation failed")
        report = {"path": str(target_path), "counts": counts, "integrity_check": integrity,
                  "foreign_key_violations": foreign_keys,
                  "record_references":refs,"cross_partition_references":blocking_refs,
                  "workspaces": [row[0] for row in target.execute("SELECT DISTINCT workspace FROM memory_records ORDER BY workspace")],
                  "logical_digest": logical_digest(target, TABLES)}
    except Exception:
        target.rollback()
        target.close()
        target_path.unlink(missing_ok=True)
        raise
    else:
        target.close()
        report["sha256"] = sha(target_path)
        return report
    finally:
        source.close()


def publish_pair(staged_personal, staged_work, personal, work):
    published = []
    try:
        os.replace(staged_personal, personal); published.append(Path(personal))
        os.replace(staged_work, work); published.append(Path(work))
    except Exception:
        for path in published:
            path.unlink(missing_ok=True)
        raise


def write_json_atomic(path,data):
    path=Path(path);temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
    os.replace(temporary,path)


def recover_interrupted_publish(marker,personal,work,manifest=None):
    marker=Path(marker)
    if not marker.exists():return False
    data=json.loads(marker.read_text())
    expected={str(Path(personal).resolve()),str(Path(work).resolve())}
    if set(data.get("targets",[]))!=expected:raise RuntimeError("publish recovery marker does not match requested targets")
    if manifest and Path(manifest).exists():
        completed=json.loads(Path(manifest).read_text())
        if completed.get("published") and Path(personal).exists() and Path(work).exists():
            marker.unlink();return "completed"
    for path in (personal,work,Path(str(personal)+".staging"),Path(str(work)+".staging")):
        Path(path).unlink(missing_ok=True)
    marker.unlink();return "cleaned"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--personal-journal", type=Path, required=True); p.add_argument("--work-journal", type=Path, required=True)
    p.add_argument("--personal-workspaces", required=True); p.add_argument("--work-workspaces", required=True)
    p.add_argument("--manifest", type=Path); p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    personal = {x.strip() for x in a.personal_workspaces.split(",") if x.strip()}
    work = {x.strip() for x in a.work_workspaces.split(",") if x.strip()}
    if a.apply and not a.manifest:raise SystemExit("--apply requires --manifest as the publish authority")
    marker=a.manifest.with_name(a.manifest.name+".publish-pending") if a.manifest else None
    recovery=recover_interrupted_publish(marker,a.personal_journal,a.work_journal,a.manifest) if marker else False
    if recovery=="completed":
        print(a.manifest.read_text(),end="");return
    if a.personal_journal == a.work_journal or (a.apply and (a.personal_journal.exists() or a.work_journal.exists())):
        raise SystemExit("targets must be distinct and must not exist")
    source_sha_before = sha(a.source)
    with tempfile.TemporaryDirectory(prefix="brain-bootstrap-") as tmp:
        snapshot = Path(tmp) / "source.snapshot.db"
        snapshot_digest = snapshot_source(a.source, snapshot)
        report = {"source": str(a.source), "source_sha256_before": source_sha_before,
                  "source_logical_digest": snapshot_digest, "partition": {"personal": sorted(personal), "work": sorted(work)},
                  **inspect_snapshot(snapshot, personal, work)}
        blocked = report["overlap"] or report["unassigned"] or report["unknown_requested"] or report["cross_partition_references"] or report["integrity_check"] != "ok" or report["foreign_key_violations"]
        if not blocked and a.apply:
            a.personal_journal.parent.mkdir(parents=True, exist_ok=True); a.work_journal.parent.mkdir(parents=True, exist_ok=True)
            a.manifest.parent.mkdir(parents=True,exist_ok=True)
            staged_personal = a.personal_journal.with_name(a.personal_journal.name + ".staging")
            staged_work = a.work_journal.with_name(a.work_journal.name + ".staging")
            for path in (staged_personal, staged_work): path.unlink(missing_ok=True)
            try:
                report["personal"] = build(snapshot, staged_personal, personal)
                report["work"] = build(snapshot, staged_work, work)
                for instance in ("personal","work"):
                    if report[instance]["integrity_check"]!="ok" or report[instance]["foreign_key_violations"] or report[instance]["cross_partition_references"]:
                        raise RuntimeError(f"{instance} staging validation failed")
                for table, count in report["table_counts"].items():
                    if sum(report[x]["counts"][table] for x in ("personal", "work")) != count:
                        raise RuntimeError(f"split count mismatch: {table}")
                if report["personal"]["workspaces"] != sorted(personal) or report["work"]["workspaces"] != sorted(work):
                    raise RuntimeError("partition validation failed")
                source_sha_after = sha(a.source)
                source_after = readonly(a.source)
                try: source_digest_after = logical_digest(source_after, TABLES)
                finally: source_after.close()
                if source_sha_after != source_sha_before or source_digest_after != snapshot_digest:
                    raise RuntimeError("legacy source changed during bootstrap")
                report["source_sha256_after"] = source_sha_after
                report["result_journal_digests"] = {"personal": report["personal"]["logical_digest"], "work": report["work"]["logical_digest"]}
                write_json_atomic(marker,{"targets":[str(a.personal_journal.resolve()),str(a.work_journal.resolve())],"source_digest":snapshot_digest})
                try:publish_pair(staged_personal, staged_work, a.personal_journal, a.work_journal)
                except Exception:
                    marker.unlink(missing_ok=True);raise
                report["published"] = True
                write_json_atomic(a.manifest,report);marker.unlink(missing_ok=True)
            finally:
                staged_personal.unlink(missing_ok=True); staged_work.unlink(missing_ok=True)
        if a.manifest and not report.get("published"):
            a.manifest.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(a.manifest,report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if blocked:
            raise SystemExit(2)


if __name__ == "__main__": main()
