#!/usr/bin/env python3
"""Non-destructive, gated and all-or-nothing split of the legacy journal.

Package 1 (write-safety-integrity-repair-spec.md §4) adds a digest-aware
recovery state machine: every crash-recovery decision is made by inspecting
what is actually on disk against digests this script itself recorded, never
by unconditionally deleting whatever sits at a target path.
"""
import argparse, hashlib, json, os, sqlite3, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from brain import artifact_validation as av
from brain import instance_identity
from brain.control import PERSONAL_WORKSPACES, WORK_WORKSPACES
from brain.record_references import journal_references

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "spike/schema/journal.sql"
TABLES = ("memory_records", "memory_events", "record_state", "artifact_links", "artifact_validation_events")
CURATION_SCHEMA_VERSION = 1
# This is deliberately a one-record exception, not a general migration language.
CURATED_EXCLUSION = {
    "record_id": "rec-056",
    "workspace": "sap-work",
    "action": "exclude_from_work_split",
    "field_path": "payload.source_record",
    "target_record_id": "rec-001",
}

# Recovery classifications (write-safety-integrity-repair-spec.md §4.3) that
# --apply refuses outright, and the exit code each one uses. "live" and
# "incompatible_existing_state" mean bootstrap is being asked to act on state
# it didn't create; "foreign_corrupted"/"corrupted" mean a target or marker
# disagrees with every digest this script ever recorded. Recovery never
# deletes anything in any of these classifications.
BLOCKING_CLASSIFICATIONS = {
    "live": 3, "incompatible_existing_state": 3, "incompatible_retry": 3,
    "foreign_corrupted": 4, "corrupted": 4,
}


class CurationError(ValueError):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat()


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


def record_filter(workspaces, excluded_records=()):
    marks = ",".join("?" * len(workspaces))
    where = f"workspace IN ({marks})"
    args = list(workspaces)
    if excluded_records:
        excluded_marks = ",".join("?" * len(excluded_records))
        where += f" AND record_id NOT IN ({excluded_marks})"
        args.extend(sorted(excluded_records))
    return where, args


def _require_exact_keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise CurationError(f"{label} must contain exactly: {', '.join(sorted(expected))}")


def load_exclusion_manifest(path, snapshot_digest, source):
    """Load the one approved legacy exception and bind it to this snapshot."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CurationError(f"invalid exclusion manifest: {error}") from error
    _require_exact_keys(data, {"schema_version", "expected_source_logical_digest", "exclusions"}, "exclusion manifest")
    if data["schema_version"] != CURATION_SCHEMA_VERSION:
        raise CurationError("unsupported exclusion manifest schema_version")
    if not isinstance(data["expected_source_logical_digest"], str) or data["expected_source_logical_digest"] != snapshot_digest:
        raise CurationError("exclusion manifest source logical digest does not match snapshot")
    exclusions = data["exclusions"]
    if not isinstance(exclusions, list) or len(exclusions) != 1:
        raise CurationError("exclusion manifest must contain exactly one approved exclusion")
    entry = exclusions[0]
    _require_exact_keys(entry, {"record_id", "workspace", "action", "reason", "approval", "expected_reference"}, "exclusion entry")
    _require_exact_keys(entry["approval"], {"approved_by", "approved_at", "approval_ref"}, "exclusion approval")
    _require_exact_keys(entry["expected_reference"], {"field_path", "target_record_id"}, "expected_reference")
    for key in ("record_id", "workspace", "action"):
        if entry[key] != CURATED_EXCLUSION[key]:
            raise CurationError(f"unsupported exclusion {key}")
    for key in ("field_path", "target_record_id"):
        if entry["expected_reference"][key] != CURATED_EXCLUSION[key]:
            raise CurationError(f"unsupported exclusion expected_reference.{key}")
    if not isinstance(entry["reason"], str) or not entry["reason"].strip():
        raise CurationError("exclusion reason is required")
    approval = entry["approval"]
    if not all(isinstance(approval[key], str) and approval[key].strip() for key in approval):
        raise CurationError("exclusion approval metadata is required")
    try:
        datetime.fromisoformat(approval["approved_at"].replace("Z", "+00:00"))
    except ValueError as error:
        raise CurationError("exclusion approval.approved_at must be ISO-8601") from error
    row = source.execute("SELECT workspace,payload FROM memory_records WHERE record_id=?", (entry["record_id"],)).fetchone()
    if row is None or row[0] != entry["workspace"]:
        raise CurationError("exclusion record or workspace does not match snapshot")
    refs = [ref for ref in journal_references_for_payload(row[1]) if ref["field_path"] == entry["expected_reference"]["field_path"]]
    if refs != [{"target_record": entry["expected_reference"]["target_record_id"], "field_path": entry["expected_reference"]["field_path"], "relation": "payload_source"}]:
        raise CurationError("exclusion payload reference does not match snapshot")
    return entry


def journal_references_for_payload(payload):
    # Keep the curation check coupled to the canonical reference parser.
    from brain.record_references import payload_references
    return payload_references(payload)


def exclusion_audit(con, record_ids):
    record_ids = sorted(record_ids)
    if not record_ids:
        return {"records": [], "events": [], "artifact_validation_events": [],
                "table_counts": {table: 0 for table in TABLES},
                "legacy_retention": {"records_present": [], "events_present": [], "artifact_validation_events_present": []}}
    marks = ",".join("?" * len(record_ids))
    records = [{"record_id": row[0], "workspace": row[1], "type": row[2], "sensitivity": row[3]} for row in con.execute(
        f"SELECT record_id,workspace,type,sensitivity FROM memory_records WHERE record_id IN ({marks}) ORDER BY record_id", record_ids
    ).fetchall()]
    events = [{"event_id": row[0], "record_id": row[1], "event_type": row[2], "occurred_at": row[3]} for row in con.execute(
        f"SELECT event_id,record_id,event_type,occurred_at FROM memory_events WHERE record_id IN ({marks}) ORDER BY occurred_at,event_id", record_ids
    ).fetchall()]
    validation_events = [{"event_id": row[0], "artifact_record_id": row[1], "artifact_link_id": row[2], "state": row[3], "effective_at": row[4]} for row in con.execute(
        f"SELECT event_id,artifact_record_id,artifact_link_id,state,effective_at FROM artifact_validation_events WHERE artifact_record_id IN ({marks}) ORDER BY effective_at,event_id", record_ids
    ).fetchall()]
    counts = {"memory_records": len(records), "memory_events": len(events)}
    for table in ("record_state", "artifact_links", "artifact_validation_events"):
        key = "artifact_record_id" if table == "artifact_validation_events" else "record_id"
        counts[table] = con.execute(f"SELECT count(*) FROM {table} WHERE {key} IN ({marks})", record_ids).fetchone()[0]
    retained_records = [row["record_id"] for row in records]
    retained_events = [row["event_id"] for row in events]
    retained_validation_events = [row["event_id"] for row in validation_events]
    return {"records": records, "events": events, "artifact_validation_events": validation_events, "table_counts": counts,
            "legacy_retention": {"records_present": retained_records, "events_present": retained_events,
                                  "artifact_validation_events_present": retained_validation_events}}


def partition_counts(con, workspaces, excluded_records=()):
    where, args = record_filter(workspaces, excluded_records)
    counts = {"memory_records": con.execute(f"SELECT count(*) FROM memory_records WHERE {where}", args).fetchone()[0]}
    for table in ("memory_events", "record_state", "artifact_links", "artifact_validation_events"):
        key = "artifact_record_id" if table == "artifact_validation_events" else "record_id"
        counts[table] = con.execute(
            f"SELECT count(*) FROM {table} WHERE {key} IN (SELECT record_id FROM memory_records WHERE {where})", args
        ).fetchone()[0]
    return counts


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


def build(source_path, target_path, workspaces, excluded_records=(), *, instance_id, source_digest):
    """Build one instance's staged journal, ending with its stamped identity
    marker committed in the same transaction as the copied rows (§4.2 "Instance
    creation"). ``instance_id`` must be "personal" or "work"."""
    source = readonly(source_path)
    target_path = Path(target_path)
    if target_path.exists():
        raise FileExistsError(target_path)
    target = sqlite3.connect(target_path)
    try:
        target.executescript(SCHEMA.read_text())
        av.apply_migration(target)
        record_where, record_args = record_filter(workspaces, excluded_records)
        target.execute("BEGIN IMMEDIATE")
        counts = {"memory_records": copy_rows(source, target, "memory_records", record_where, record_args)}
        for table in ("memory_events", "record_state", "artifact_links", "artifact_validation_events"):
            key = "artifact_record_id" if table == "artifact_validation_events" else "record_id"
            counts[table] = copy_rows(source, target, table, f"{key} IN (SELECT record_id FROM memory_records WHERE {record_where})", record_args)
        av.rebuild_state(target)
        instance_identity.stamp_journal_marker(target, instance_id, source_digest)
        target.commit()
        integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [tuple(row) for row in target.execute("PRAGMA foreign_key_check")]
        refs=reference_audit(target,set(workspaces),set())
        blocking_refs=[row for row in refs if row["status"]=="blocking"]
        marker=instance_identity.read_journal_marker(target)
        marker_ok=bool(marker) and marker["instance_id"]==instance_id and marker["source_digest"]==source_digest
        if integrity != "ok" or foreign_keys or av.verify_state(target) or blocking_refs or not marker_ok:
            raise RuntimeError("staging journal validation failed")
        report = {"path": str(target_path), "counts": counts, "integrity_check": integrity,
                  "foreign_key_violations": foreign_keys,
                  "record_references":refs,"cross_partition_references":blocking_refs,
                  "workspaces": [row[0] for row in target.execute("SELECT DISTINCT workspace FROM memory_records ORDER BY workspace")],
                  "logical_digest": logical_digest(target, TABLES), "instance_marker": marker}
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


# --- Recovery state machine (write-safety-integrity-repair-spec.md §4.3) ---

def inspect_target(path):
    """Best-effort, read-only inspection of a file that may or may not be a
    valid journal. Never raises. ``sha256`` is always populated when the file
    exists; ``logical_digest``, ``marker``, ``foreign_key_violations``, and
    ``workspaces`` are populated only when the file is a readable,
    structurally intact journal — comparisons against them therefore fail
    safe (a corrupted or foreign file never accidentally matches a recorded
    digest or identity, and is never treated as FK/partition-clean)."""
    path = Path(path)
    if not path.is_file():
        return None
    info = {"sha256": sha(path), "logical_digest": None, "integrity_check": None, "marker": None,
            "foreign_key_violations": None, "workspaces": None}
    try:
        con = readonly(path)
    except sqlite3.Error:
        return info
    try:
        info["integrity_check"] = con.execute("PRAGMA integrity_check").fetchone()[0]
        if info["integrity_check"] == "ok":
            info["logical_digest"] = logical_digest(con, TABLES)
            info["marker"] = instance_identity.read_journal_marker(con)
            info["foreign_key_violations"] = [tuple(row) for row in con.execute("PRAGMA foreign_key_check")]
            info["workspaces"] = sorted({row[0] for row in con.execute("SELECT DISTINCT workspace FROM memory_records")})
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return info


def _digest_matches(info, recorded_digest):
    """Strict digest comparison: a missing/unreadable file (``info is None`` or
    ``info["logical_digest"] is None``) never "matches" a missing recorded
    digest just because both sides happen to be ``None``. Absence of proof is
    never treated as proof."""
    return (info is not None and info["logical_digest"] is not None
            and recorded_digest is not None and info["logical_digest"] == recorded_digest)


def _identity_matches(info, role, source_digest):
    """Whether ``info``'s own stamped ``brain_instance_identity`` row proves it
    is the output of the bootstrap run that recorded ``source_digest`` for
    ``role`` ("personal"/"work") — the same test ``build()`` itself performs
    before publishing (§4.2 "Instance creation" gate).

    Unlike ``_digest_matches``, this does not require the file's *current*
    content to be byte-for-byte what was staged: the identity row is written
    once, inside the same transaction as the initial data copy, and nothing
    else ever touches it — so it still holds after a legitimate write lands
    in the journal following a successful publish. Using full-content digest
    equality here would treat "recovery ran after new data was already
    written" the same as "this is a different file entirely", and delete or
    refuse to acknowledge a perfectly valid, already-live instance. Identity
    proof is exactly what §4.3 row 6 (crash after publish, before the
    manifest write) needs — the pair *is* published and live the moment
    ``publish_pair`` succeeds, before this script ever gets to write the
    manifest describing that fact."""
    marker = info.get("marker") if info else None
    return bool(marker) and marker.get("instance_id") == role and marker.get("source_digest") == source_digest and source_digest is not None


def _forward_completion_issues(personal_info, work_info):
    """FK and workspace-partition re-check gating forward-completion (M-1).
    Both targets already passed this at build() time; this re-proves it still
    holds for whatever is on disk now, without re-verifying content digest
    (which a legitimate post-publish write is expected to change)."""
    issues = []
    for role, info, expected in (("personal", personal_info, PERSONAL_WORKSPACES), ("work", work_info, WORK_WORKSPACES)):
        if info["foreign_key_violations"]:
            issues.append({"target": role, "issue": "foreign_key_violations", "detail": info["foreign_key_violations"]})
        if info["workspaces"] is not None and not set(info["workspaces"]) <= set(expected):
            issues.append({"target": role, "issue": "workspace_partition_violation",
                            "detail": sorted(set(info["workspaces"]) - set(expected))})
    return issues


def classify_recovery(marker_path, manifest_path, personal_path, work_path):
    """Classify current on-disk state per §4.3's ten-row table. Read-only:
    never deletes, writes, or modifies anything. Returns ``(classification,
    detail)``; callers act on the classification."""
    marker_path = Path(marker_path)
    manifest_path = Path(manifest_path) if manifest_path else None
    personal_path = Path(personal_path); work_path = Path(work_path)
    marker = json.loads(marker_path.read_text()) if marker_path.exists() else None
    manifest = json.loads(manifest_path.read_text()) if manifest_path and manifest_path.exists() else None
    personal_info = inspect_target(personal_path)
    work_info = inspect_target(work_path)
    published = bool(manifest and manifest.get("published"))
    result_digests = (manifest or {}).get("result_journal_digests") or {}

    def published_pair_matches():
        return (_digest_matches(personal_info, result_digests.get("personal"))
                and _digest_matches(work_info, result_digests.get("work")))

    if marker is None:
        if personal_info is None and work_info is None:
            return "fresh", {}
        if published:
            if published_pair_matches():
                return "already_bootstrapped", {"manifest": manifest}
            return "live", {"personal": personal_info, "work": work_info, "manifest_digests": result_digests}
        return "incompatible_existing_state", {
            "personal_exists": personal_info is not None, "work_exists": work_info is not None,
            "reason": "target file(s) present with no publish-pending marker and no published manifest",
        }

    expected_targets = {str(personal_path.resolve()), str(work_path.resolve())}
    marker_targets = {(marker.get("personal") or {}).get("path"), (marker.get("work") or {}).get("path")}
    if marker_targets != expected_targets:
        return "incompatible_retry", {"marker_targets": sorted(x for x in marker_targets if x),
                                       "requested_targets": sorted(expected_targets)}

    if published:
        if published_pair_matches():
            return "completed_crash_after_manifest", {}
        return "corrupted", {"reason": "manifest is published but on-disk targets do not match its recorded digests",
                              "personal": personal_info, "work": work_info, "manifest_digests": result_digests}

    source_digest = marker.get("source_digest")
    personal_is_ours = _identity_matches(personal_info, "personal", source_digest)
    work_is_ours = _identity_matches(work_info, "work", source_digest)
    if personal_info is not None and work_info is not None and personal_is_ours and work_is_ours:
        # Both os.replace calls in publish_pair succeeded; the pair is fully
        # published and may already have legitimate writes in it. Only the
        # manifest write is missing — complete it, never re-derive or verify
        # the pair's *current* content against the pre-publish staged digest.
        # But a legitimate write can only ever add FK-clean rows in the
        # target's own workspace partition (the writer enforces both) — if
        # either target now fails FK or partition validation, something else
        # is wrong with it, and forward-completion must not paper over that.
        validation_issues = _forward_completion_issues(personal_info, work_info)
        if validation_issues:
            return "corrupted", {"reason": "forward-completion pre-check failed", "issues": validation_issues,
                                  "personal": personal_info, "work": work_info}
        return "crash_after_publish_before_manifest", {}
    unexpected = []
    if personal_info is not None and not personal_is_ours: unexpected.append("personal")
    if work_info is not None and not work_is_ours: unexpected.append("work")
    if unexpected:
        return "foreign_corrupted", {"unexpected": unexpected, "personal": personal_info, "work": work_info, "marker": marker}
    return "recoverable_partial", {}


def cleanup_recoverable_partial(marker_data, personal_path, work_path):
    """Delete only files whose digest matches what this bootstrap staged (the
    digest-verified-own-output rule, I2), plus staging remnants. Never deletes
    a file it cannot verify — including when the marker itself carries no
    recorded digest for that side."""
    for role, path in (("personal", Path(personal_path)), ("work", Path(work_path))):
        info = inspect_target(path)
        if _digest_matches(info, (marker_data.get(role) or {}).get("logical_digest")):
            path.unlink(missing_ok=True)
        Path(str(path) + ".staging").unlink(missing_ok=True)


def _observation(info):
    """The subset of inspect_target()'s output worth recording as evidence
    that this target was actually inspected at recovery time, not merely
    trusted from the (necessarily stale) marker file."""
    if info is None:
        return {"exists": False}
    return {"exists": True, "sha256": info["sha256"], "logical_digest": info["logical_digest"],
            "integrity_check": info["integrity_check"], "foreign_key_violations": info["foreign_key_violations"],
            "marker": info["marker"], "workspaces": info["workspaces"]}


def forward_complete_from_marker(marker_data, manifest_path, personal_path, work_path):
    """Row 6: publish_pair already succeeded but the manifest write never
    happened. Write the manifest from what the marker recorded — the staged
    digests remain the load-bearing result_journal_digests, since content is
    expected to have moved on and later `live` detection depends on them
    describing what was *published*, not what is on disk right now — plus a
    recovered_observation of each target's actual current state, so this
    manifest is not silently weaker evidence than a normal one. Never
    touches the (already correctly published) target files. Callers must
    have already gated FK/partition validity via classify_recovery's
    forward-completion check before calling this."""
    personal = marker_data.get("personal") or {}
    work = marker_data.get("work") or {}
    report = {
        "recovered_from": "crash_after_publish_before_manifest",
        "recovered_at": now_iso(),
        "source_logical_digest": marker_data.get("source_digest"),
        "personal": dict(personal),
        "work": dict(work),
        "result_journal_digests": {"personal": personal.get("logical_digest"), "work": work.get("logical_digest")},
        "recovered_observation": {"personal": _observation(inspect_target(personal_path)),
                                   "work": _observation(inspect_target(work_path))},
        "published": True,
    }
    write_json_atomic(manifest_path, report)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--personal-journal", type=Path, required=True); p.add_argument("--work-journal", type=Path, required=True)
    p.add_argument("--personal-workspaces", required=True); p.add_argument("--work-workspaces", required=True)
    p.add_argument("--exclusion-manifest", type=Path,
                   help="one snapshot-bound, operator-approved exclusion for rec-056 only")
    p.add_argument("--manifest", type=Path); p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    personal = {x.strip() for x in a.personal_workspaces.split(",") if x.strip()}
    work = {x.strip() for x in a.work_workspaces.split(",") if x.strip()}
    if a.apply and not a.manifest:raise SystemExit("--apply requires --manifest as the publish authority")
    if a.personal_journal == a.work_journal:raise SystemExit("targets must be distinct")
    marker=a.manifest.with_name(a.manifest.name+".publish-pending") if a.manifest else None

    recovery_classification = None
    if marker is not None:
        recovery_classification, recovery_detail = classify_recovery(marker, a.manifest, a.personal_journal, a.work_journal)
        if a.apply:
            if recovery_classification == "already_bootstrapped":
                print(a.manifest.read_text(), end=""); return
            if recovery_classification == "completed_crash_after_manifest":
                marker.unlink(missing_ok=True)
                print(a.manifest.read_text(), end=""); return
            if recovery_classification == "crash_after_publish_before_manifest":
                marker_data = json.loads(marker.read_text())
                forward_complete_from_marker(marker_data, a.manifest, a.personal_journal, a.work_journal)
                marker.unlink(missing_ok=True)
                print(a.manifest.read_text(), end=""); return
            if recovery_classification in BLOCKING_CLASSIFICATIONS:
                blocked_report = {"recovery_classification": recovery_classification, "detail": recovery_detail,
                                   "personal_journal": str(a.personal_journal), "work_journal": str(a.work_journal)}
                print(json.dumps(blocked_report, ensure_ascii=False, indent=2))
                raise SystemExit(BLOCKING_CLASSIFICATIONS[recovery_classification])
            if recovery_classification == "recoverable_partial":
                marker_data = json.loads(marker.read_text()) if marker.exists() else {}
                cleanup_recoverable_partial(marker_data, a.personal_journal, a.work_journal)
                # B-1: cleanup only ever deletes a digest-verified own output;
                # a target that survived it (a legitimate write changed its
                # content, or it is genuinely foreign) must never be silently
                # folded into a fresh build/publish below. The marker is kept
                # in that refusal state — it is the forensic record of what
                # this bootstrap staged and is still needed to classify the
                # next retry precisely; it is only removed once we know we
                # are actually proceeding to a fresh run.
                surviving = [name for name, path in (("personal", a.personal_journal), ("work", a.work_journal)) if Path(path).exists()]
                if surviving:
                    surviving_report = {"recovery_classification": "recoverable_partial_cleanup_incomplete",
                                         "detail": {"surviving_targets": surviving,
                                                    "reason": "target(s) remained after digest-verified cleanup; refusing rather than overwriting"},
                                         "personal_journal": str(a.personal_journal), "work_journal": str(a.work_journal)}
                    print(json.dumps(surviving_report, ensure_ascii=False, indent=2))
                    raise SystemExit(4)
                marker.unlink(missing_ok=True)
            # "fresh": nothing to clean up; fall through to a normal build.

    source_sha_before = sha(a.source)
    with tempfile.TemporaryDirectory(prefix="brain-bootstrap-") as tmp:
        snapshot = Path(tmp) / "source.snapshot.db"
        snapshot_digest = snapshot_source(a.source, snapshot)
        report = {"source": str(a.source), "source_sha256_before": source_sha_before,
                  "source_logical_digest": snapshot_digest, "partition": {"personal": sorted(personal), "work": sorted(work)},
                  **inspect_snapshot(snapshot, personal, work)}
        if recovery_classification is not None:
            report["recovery_classification"] = recovery_classification
        source_snapshot = readonly(snapshot)
        try:
            exclusion = None
            exclusion_error = None
            if a.exclusion_manifest:
                try:
                    exclusion = load_exclusion_manifest(a.exclusion_manifest, snapshot_digest, source_snapshot)
                except CurationError as error:
                    exclusion_error = str(error)
            excluded_ids = {exclusion["record_id"]} if exclusion else set()
            excluded = exclusion_audit(source_snapshot, excluded_ids)
            report["expected_partition_counts"] = {
                "personal": partition_counts(source_snapshot, personal),
                "work": partition_counts(source_snapshot, work, excluded_ids),
                "approved_exclusions": excluded["table_counts"],
            }
        finally:
            source_snapshot.close()
        refs = report.pop("record_references")
        cross_refs = report.pop("cross_partition_references")
        approved_refs = []
        if exclusion:
            expected = exclusion["expected_reference"]
            approved_refs = [ref for ref in cross_refs if ref["source_record"] == exclusion["record_id"]
                             and ref["field_path"] == expected["field_path"]
                             and ref["target_record"] == expected["target_record_id"]]
        unapproved_refs = [ref for ref in cross_refs if ref not in approved_refs]
        report["operator_audit"] = {
            "record_references": refs,
            "cross_partition_references": cross_refs,
            "approved_exclusions": ({
                "manifest_sha256": sha(a.exclusion_manifest),
                "record_ids": sorted(excluded_ids),
                "records": excluded["records"],
                "events": excluded["events"],
                "artifact_validation_events": excluded["artifact_validation_events"],
                "table_counts": excluded["table_counts"],
                "legacy_retention": excluded["legacy_retention"],
                "approved_cross_partition_references": approved_refs,
                "remaining_cross_partition_references": unapproved_refs,
            } if exclusion else {"record_ids": [], "records": [], "events": [], "artifact_validation_events": [],
                                  "table_counts": {table: 0 for table in TABLES},
                                  "legacy_retention": {"records_present": [], "events_present": [], "artifact_validation_events_present": []},
                                  "approved_cross_partition_references": [],
                                  "remaining_cross_partition_references": unapproved_refs}),
        }
        if exclusion_error:
            report["operator_audit"]["exclusion_manifest_error"] = exclusion_error
        count_mismatches = {table: {
            "legacy": report["table_counts"][table],
            "partitioned": sum(report["expected_partition_counts"][instance][table] for instance in ("personal", "work"))
                           + report["expected_partition_counts"]["approved_exclusions"][table],
        } for table in TABLES}
        count_mismatches = {table: values for table, values in count_mismatches.items() if values["legacy"] != values["partitioned"]}
        report["operator_audit"]["partition_count_mismatches"] = count_mismatches
        partition_mismatch = {}
        if personal != set(PERSONAL_WORKSPACES): partition_mismatch["personal"] = {"requested": sorted(personal), "expected": sorted(PERSONAL_WORKSPACES)}
        if work != set(WORK_WORKSPACES): partition_mismatch["work"] = {"requested": sorted(work), "expected": sorted(WORK_WORKSPACES)}
        report["operator_audit"]["partition_constant_mismatch"] = partition_mismatch
        blocked = (report["overlap"] or report["unassigned"] or report["unknown_requested"] or unapproved_refs
                   or exclusion_error or count_mismatches or report["integrity_check"] != "ok" or report["foreign_key_violations"]
                   or partition_mismatch)
        if not blocked and a.apply:
            a.personal_journal.parent.mkdir(parents=True, exist_ok=True); a.work_journal.parent.mkdir(parents=True, exist_ok=True)
            a.manifest.parent.mkdir(parents=True,exist_ok=True)
            staged_personal = a.personal_journal.with_name(a.personal_journal.name + ".staging")
            staged_work = a.work_journal.with_name(a.work_journal.name + ".staging")
            for path in (staged_personal, staged_work): path.unlink(missing_ok=True)
            try:
                report["personal"] = build(snapshot, staged_personal, personal, instance_id="personal", source_digest=snapshot_digest)
                report["work"] = build(snapshot, staged_work, work, excluded_ids, instance_id="work", source_digest=snapshot_digest)
                for instance in ("personal","work"):
                    if report[instance]["integrity_check"]!="ok" or report[instance]["foreign_key_violations"] or report[instance]["cross_partition_references"]:
                        raise RuntimeError(f"{instance} staging validation failed")
                for table, count in report["table_counts"].items():
                    if sum(report[x]["counts"][table] for x in ("personal", "work")) + excluded["table_counts"][table] != count:
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
                # B-1 TOCTOU guard: staging both journals can take a while;
                # re-verify absence immediately before claiming (marker) or
                # performing (publish_pair) a publish. A target appearing in
                # this window (a concurrent bootstrap, a restored backup, an
                # operator mistake) is refused, never silently overwritten.
                surviving = [name for name, path in (("personal", a.personal_journal), ("work", a.work_journal)) if Path(path).exists()]
                if surviving:
                    raise RuntimeError(f"target(s) appeared before publish, refusing to overwrite: {surviving}")
                write_json_atomic(marker,{
                    "source_digest":snapshot_digest,"started_at":now_iso(),
                    "personal":{"path":str(a.personal_journal.resolve()),"sha256":report["personal"]["sha256"],"logical_digest":report["personal"]["logical_digest"]},
                    "work":{"path":str(a.work_journal.resolve()),"sha256":report["work"]["sha256"],"logical_digest":report["work"]["logical_digest"]},
                })
                try:publish_pair(staged_personal, staged_work, a.personal_journal, a.work_journal)
                except Exception:
                    marker.unlink(missing_ok=True);raise
                report["published"] = True
                write_json_atomic(a.manifest,report);marker.unlink(missing_ok=True)
            finally:
                staged_personal.unlink(missing_ok=True); staged_work.unlink(missing_ok=True)
        if a.manifest and not report.get("published"):
            a.manifest.parent.mkdir(parents=True, exist_ok=True)
            # H-1: a manifest already recording a real publish is the state
            # machine's own input (classify_recovery reads it back). A run
            # that is not itself completing a publish — chiefly a plain
            # preflight dry run — must never overwrite it; the preflight
            # report goes to a sibling file instead. (--apply reaching this
            # line with an already-published on-disk manifest cannot happen:
            # classify_recovery would have already returned/raised via
            # already_bootstrapped/live/completed_crash_after_manifest above.)
            existing_published = a.manifest.exists() and json.loads(a.manifest.read_text()).get("published") is True
            if existing_published:
                preflight_path = a.manifest.with_name(a.manifest.name + ".preflight.json")
                report["manifest_preserved"] = True
                report["preflight_report_path"] = str(preflight_path)
                write_json_atomic(preflight_path, report)
            else:
                write_json_atomic(a.manifest,report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if blocked:
            raise SystemExit(2)


if __name__ == "__main__": main()
