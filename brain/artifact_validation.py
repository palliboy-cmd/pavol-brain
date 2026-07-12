"""Canonical artifact-relation validation: append-only events plus a reproducible fold.

The subject of validation is one typed artifact relation (`artifact_link_id`),
never a filesystem observation. Events are the only authority; the
`artifact_validation_state` table is a deterministic fold that may be rebuilt
at any time and is never edited independently.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "spike" / "schema" / "artifact_validation.sql"
TABLES = ("artifact_validation_events", "artifact_validation_state")
STATES = ("unknown", "verified_active", "verified_inactive")
REASON_CODES = ("manual_verified", "wrong_target", "malformed_uri", "duplicate", "superseded", "intentionally_retired", "migrated_from_baseline_review", "other")
CANONICAL_TABLES = ("memory_records", "memory_events", "record_state", "artifact_links")


def link_id(record_id, relation, artifact_uri):
    return f"artifact:{record_id}:{relation}:{artifact_uri}"


def idempotency_key(artifact_link_id, state):
    return f"artifact-validation:v1:{artifact_link_id}:{state}"


def event_id_for(key):
    """Deterministic event id so replayed backfills cannot mint new identities."""
    return "ave-" + hashlib.sha256(key.encode()).hexdigest()[:32]


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def utc_iso(value):
    return parse_time(value).astimezone(timezone.utc).isoformat()


def tables_present(con):
    names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {table: table in names for table in TABLES}


def apply_migration(con):
    already = all(tables_present(con).values())
    con.executescript(SCHEMA_PATH.read_text())
    return {"already_applied": already, "tables": list(TABLES)}


def read_events(con, artifact_record_id=None):
    sql = "SELECT * FROM artifact_validation_events"
    args = ()
    if artifact_record_id is not None:
        sql += " WHERE artifact_record_id=?"
        args = (artifact_record_id,)
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(sql, args)]


def fold_events(events, as_of=None):
    """Deterministic fold: last state per relation ordered by effective_at, event_id.

    ``as_of`` (ISO-8601) folds only events effective at or before that instant,
    which is the Proposal 008 effective-time semantics for historical builds.
    """
    moment = parse_time(as_of) if as_of else None
    folded = {}
    for event in sorted(events, key=lambda e: (parse_time(e["effective_at"]), e["event_id"])):
        if moment is not None and parse_time(event["effective_at"]) > moment:
            continue
        folded[event["artifact_link_id"]] = event
    return folded


def state_rows(events):
    folded = fold_events(events)
    return [(event["artifact_link_id"], event["artifact_record_id"], event["state"], event["reason_code"], event["effective_at"], event["event_id"], event["actor"], f"event:{event['event_id']}")
            for _, event in sorted(folded.items())]


def rebuild_state(con):
    rows = state_rows(read_events(con))
    con.execute("DELETE FROM artifact_validation_state")
    con.executemany("INSERT INTO artifact_validation_state VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def verify_state(con):
    """Recompute the fold from events and diff it against the stored table."""
    expected = {row[0]: row for row in state_rows(read_events(con))}
    con.row_factory = sqlite3.Row
    stored = {row["artifact_link_id"]: tuple(row) for row in con.execute("SELECT * FROM artifact_validation_state")}
    mismatches = []
    for key in sorted(set(expected) | set(stored)):
        if expected.get(key) != stored.get(key):
            mismatches.append({"artifact_link_id": key, "expected": expected.get(key), "stored": stored.get(key)})
    return mismatches


def canonical_table_digest(con):
    """Content digest proving no UPDATE/DELETE touched pre-existing canonical tables."""
    digest = hashlib.sha256()
    for table in CANONICAL_TABLES:
        for row in con.execute(f"SELECT * FROM {table} ORDER BY 1,2"):
            digest.update(json.dumps(tuple(row), ensure_ascii=False, default=str).encode())
    return digest.hexdigest()


def journal_relations(con):
    """Every canonical artifact relation: explicit artifact_links rows when present,
    otherwise the artifact_link record's single payload relation."""
    con.row_factory = sqlite3.Row
    relations = {}
    rows = {}
    for row in con.execute("SELECT record_id,artifact_uri,relation FROM artifact_links"):
        rows.setdefault(row["record_id"], []).append((row["artifact_uri"], row["relation"]))
    for row in con.execute("SELECT record_id,payload FROM memory_records WHERE type='artifact_link'"):
        payload = json.loads(row["payload"])
        pairs = rows.get(row["record_id"]) or [(payload["artifact_uri"], payload["relation"])]
        for uri, relation in pairs:
            relations[link_id(row["record_id"], relation, uri)] = {"artifact_record_id": row["record_id"], "artifact_uri": uri, "relation": relation}
    return relations
