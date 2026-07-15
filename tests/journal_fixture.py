"""Canonical-journal fixture mirroring the real mini-core journal shape.

Like the production journal, it contains no ``artifact_links`` rows: artifact
relations live in record payloads and their validity is decided only by
append-only ``artifact_validation_events``.
"""
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "sqlite-spike" / "scripts"))

from brain import artifact_validation as av
from brain import instance_identity
from fts_baseline import source_records

FIXTURE_EFFECTIVE_AT = "2026-07-10T00:00:00+00:00"
FIXTURE_SOURCE_DIGEST = "fixture-source-digest"


def add_validation_event(con, record_id, artifact_uri, relation, state, reason_code, effective_at=FIXTURE_EFFECTIVE_AT, actor="fixture", note="fixture decision", key_suffix=""):
    lid = av.link_id(record_id, relation, artifact_uri)
    key = av.idempotency_key(lid, state) + key_suffix
    con.execute("INSERT INTO artifact_validation_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (av.event_id_for(key), lid, record_id, artifact_uri, relation, effective_at, effective_at, state, reason_code, actor, "fixture", "{}", note, key))
    return lid


def journal_fixture(path, records=None, with_validation=True, instance_id=None):
    """``instance_id`` (Package 1): when given ("personal"/"work"), stamps the
    fixture journal's ``brain_instance_identity`` marker so it can be opened by
    a matching-instance ``BrainConfig``/``ProjectorConfig`` without tripping
    the marker enforcement added in write-safety-integrity-repair-spec.md §3
    B2. Default fixture content spans both Personal and WORK workspaces
    (mirroring the real pre-split legacy journal shape) — that is intentional
    and unrelated to this stamp; it only asserts "this file's marker says X",
    the same narrow thing production enforcement checks."""
    schema = (ROOT / "spike/schema/journal.sql").read_text()
    con = sqlite3.connect(path)
    con.executescript(schema)
    base = datetime(2026, 7, 10, tzinfo=timezone.utc)
    rows = list(records or source_records())
    for index, record in enumerate(rows):
        payload = json.dumps(record["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        valid_at = record["valid_at"]
        confidence = record["confidence"]
        con.execute("INSERT INTO memory_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (record["record_id"], 1, record["type"], record["workspace"], record["sensitivity"], payload, payload, hashlib.sha256(payload.encode()).hexdigest(), record["idempotency_key"], "fixture", record["source_assertion"], None, None, None, confidence, valid_at, valid_at))
        status = record["status"]
        superseded_by = "rec-046" if status == "superseded" else None
        con.execute("INSERT INTO record_state VALUES (?,?,?,?,?,?,?,?,?,?,?)", (record["record_id"], status, "human_approved" if status in {"accepted", "superseded"} else "pending", "2026-07-10T00:00:00+00:00" if status == "superseded" else None, None, superseded_by, "fixture" if superseded_by else None, "none", None, None, f"evt-{index:04d}"))
        occurred = (base + timedelta(seconds=index)).isoformat()
        con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)", (f"evt-{index:04d}", record["record_id"], "record_created", occurred, "fixture", json.dumps({"status": status})))
    if with_validation:
        av.apply_migration(con)
        for record in rows:
            if record["type"] != "artifact_link":
                continue
            payload = record["payload"]
            valid = record["expected"].get("artifact_validation") == "valid"
            add_validation_event(con, record["record_id"], payload["artifact_uri"], payload["relation"],
                                 "verified_active" if valid else "verified_inactive",
                                 "manual_verified" if valid else "wrong_target")
        av.rebuild_state(con)
    if instance_id is not None:
        instance_identity.stamp_journal_marker(con, instance_id, FIXTURE_SOURCE_DIGEST)
    con.commit()
    con.close()
