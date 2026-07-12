#!/usr/bin/env python3
"""Idempotent append-only backfill of human-approved artifact validation events.

Dry-run by default; ``--apply`` inserts only events whose idempotency key is
absent, rebuilds and verifies the folded state, and emits an audit report.
The tool refuses incomplete, duplicated, or unknown-relation manifests and
never touches pre-existing canonical rows.
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


def preflight(con, manifest):
    problems = []
    approval = manifest.get("approval") or {}
    for field in ("approved_by", "effective_at", "source"):
        if not approval.get(field):
            problems.append(f"approval.{field} missing")
    decisions = manifest.get("decisions") or []
    seen = set()
    journal = av.journal_relations(con)
    for item in decisions:
        lid = item.get("artifact_link_id", "")
        if lid in seen:
            problems.append(f"duplicate decision: {lid}")
        seen.add(lid)
        if item.get("state") not in ("verified_active", "verified_inactive"):
            problems.append(f"invalid state for {lid}: {item.get('state')}")
        if item.get("reason_code") not in av.REASON_CODES:
            problems.append(f"invalid reason_code for {lid}: {item.get('reason_code')}")
        if not item.get("note"):
            problems.append(f"missing note for {lid}")
        relation = journal.get(lid)
        if relation is None:
            problems.append(f"unknown relation (not in canonical journal): {lid}")
        elif relation["artifact_record_id"] != item.get("artifact_record_id") or relation["artifact_uri"] != item.get("artifact_uri") or relation["relation"] != item.get("relation"):
            problems.append(f"decision fields do not match canonical relation: {lid}")
    uncovered = sorted(set(journal) - seen)
    if uncovered:
        problems.append(f"manifest incomplete; journal relations without a decision: {uncovered}")
    if not all(av.tables_present(con).values()):
        problems.append("artifact validation tables missing; run the migration first")
    return problems


def planned_events(con, manifest):
    approval = manifest["approval"]
    effective_at = av.utc_iso(approval["effective_at"])
    existing = {row["idempotency_key"] for row in av.read_events(con)} if all(av.tables_present(con).values()) else set()
    plan = []
    for item in manifest["decisions"]:
        key = av.idempotency_key(item["artifact_link_id"], item["state"])
        plan.append({
            "event_id": av.event_id_for(key),
            "artifact_link_id": item["artifact_link_id"],
            "artifact_record_id": item["artifact_record_id"],
            "artifact_uri": item["artifact_uri"],
            "relation": item["relation"],
            "effective_at": effective_at,
            "state": item["state"],
            "reason_code": item["reason_code"],
            "actor": approval["approved_by"],
            "source": approval["source"],
            "evidence": json.dumps({"manifest": manifest.get("manifest_id", "artifact-validation-approved"), "approved_effective_at": approval["effective_at"], "basis": approval.get("basis", "")}, sort_keys=True),
            "note": item["note"],
            "idempotency_key": key,
            "already_present": key in existing,
        })
    return plan


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--journal-db", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--output", type=Path)
    a = p.parse_args()

    manifest = json.loads(a.manifest.read_text())
    report = {"timestamp": datetime.now(timezone.utc).isoformat(), "journal_path": str(a.journal_db), "manifest_path": str(a.manifest), "mode": "apply" if a.apply else "dry_run", "journal_sha256_before": sha(a.journal_db)}

    ro = sqlite3.connect(f"file:{a.journal_db.resolve()}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    problems = preflight(ro, manifest)
    report["preflight_problems"] = problems
    if problems:
        report["status"] = "REFUSED"
        text = json.dumps(report, ensure_ascii=False, indent=2)
        if a.output:
            a.output.write_text(text + "\n")
        print(text)
        raise SystemExit(2)
    digest_before = av.canonical_table_digest(ro)
    plan = planned_events(ro, manifest)
    ro.close()
    report["planned_insert_count"] = sum(not e["already_present"] for e in plan)
    report["already_present_count"] = sum(e["already_present"] for e in plan)
    report["decisions"] = [{k: e[k] for k in ("event_id", "artifact_link_id", "state", "reason_code", "already_present")} for e in plan]

    if a.apply:
        con = sqlite3.connect(a.journal_db)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            occurred = datetime.now(timezone.utc).isoformat()
            inserted = 0
            for e in plan:
                if e["already_present"]:
                    continue
                con.execute("INSERT INTO artifact_validation_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                            (e["event_id"], e["artifact_link_id"], e["artifact_record_id"], e["artifact_uri"], e["relation"], occurred, e["effective_at"], e["state"], e["reason_code"], e["actor"], e["source"], e["evidence"], e["note"], e["idempotency_key"]))
                inserted += 1
            av.rebuild_state(con)
            mismatches = av.verify_state(con)
            if mismatches:
                raise RuntimeError(f"fold verification failed: {mismatches}")
            con.commit()
        except Exception:
            con.rollback()
            con.close()
            raise
        report["inserted_events"] = inserted
        report["idempotent_noop"] = inserted == 0
        report["fold_mismatches"] = av.verify_state(con)
        report["folded_states"] = {row["artifact_link_id"]: row["current_state"] for row in con.execute("SELECT artifact_link_id,current_state FROM artifact_validation_state ORDER BY artifact_link_id")}
        report["state_counts"] = {row[0]: row[1] for row in con.execute("SELECT current_state,count(*) FROM artifact_validation_state GROUP BY current_state")}
        report["canonical_tables_unchanged"] = av.canonical_table_digest(con) == digest_before
        con.close()
        if not report["canonical_tables_unchanged"]:
            raise SystemExit("canonical table digest changed; backfill aborted after rollback would be required")

    report["journal_sha256_after"] = sha(a.journal_db)
    report["status"] = "OK"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.output:
        a.output.write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
