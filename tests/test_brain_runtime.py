import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))

from brain.api import Brain
from brain.config import BrainConfig


def databases(tmp_path, event_age=0, cursor=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    journal, retrieval = tmp_path / "journal.db", tmp_path / "retrieval.db"
    j = sqlite3.connect(journal); j.execute("CREATE TABLE memory_events(event_id TEXT PRIMARY KEY, occurred_at TEXT)")
    occurred = (datetime.now(timezone.utc) - timedelta(seconds=event_age)).isoformat()
    j.execute("INSERT INTO memory_events VALUES ('evt-1',?)", (occurred,)); j.commit()
    r = sqlite3.connect(retrieval)
    r.executescript("""CREATE TABLE retrieval_documents(record_id TEXT, workspace TEXT, status TEXT);
    CREATE TABLE retrieval_embeddings(record_id TEXT);
    CREATE TABLE retrieval_embedding_meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE retrieval_projection_cursor(singleton INTEGER PRIMARY KEY,last_source_event_id TEXT,last_projected_at TEXT,projection_schema_version TEXT,embedding_model_fingerprint TEXT,embedding_dimension INTEGER,projector_version TEXT);""")
    r.execute("INSERT INTO retrieval_documents VALUES ('rec-1','ai-pos','accepted')")
    r.execute("INSERT INTO retrieval_embeddings VALUES ('rec-1')")
    r.execute("INSERT INTO retrieval_embedding_meta VALUES ('contract',?)", (json.dumps({"fingerprint":"fp","exact_model_identifier":"model","dimension":768,"build_id":"build-1"}),))
    if cursor: r.execute("INSERT INTO retrieval_projection_cursor VALUES (1,?,?,?,?,?,?)", (occurred+'\x1fevt-1',occurred,'v1','fp',768,'test'))
    r.commit(); return journal, retrieval


def test_health_current_and_counts(tmp_path, monkeypatch):
    journal, retrieval = databases(tmp_path); brain = Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,endpoint_probe_ttl=0))
    monkeypatch.setattr("brain.runtime.RuntimeInspector.endpoint", lambda self: {"status":"available","latency":1.0})
    health = brain.health()
    assert health.status == "healthy" and not health.index_behind and not health.stale_index
    assert health.embedding_count == 1 and health.embedding_coverage == 1 and health.per_workspace_counts == {"ai-pos":1}
    assert "content" not in health.model_dump_json() and "payload" not in health.model_dump_json()


def test_health_briefly_behind_and_stale(tmp_path, monkeypatch):
    monkeypatch.setattr("brain.runtime.RuntimeInspector.endpoint", lambda self: {"status":"available","latency":1.0})
    journal, retrieval = databases(tmp_path, event_age=30, cursor=False)
    brief = Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,stale_after_seconds=3600)).health()
    assert brief.index_behind and not brief.stale_index and brief.status == "healthy"
    journal, retrieval = databases(tmp_path / "stale", event_age=7200, cursor=False)
    stale = Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,stale_after_seconds=3600)).health()
    assert stale.index_behind and stale.stale_index and stale.status == "degraded"


def test_health_unavailable_and_endpoint_down(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    health = Brain(BrainConfig(journal_db_path=missing,retrieval_db_path=missing)).health()
    assert health.status == "unavailable" and not missing.exists()
    journal, retrieval = databases(tmp_path / "down")
    monkeypatch.setattr("brain.runtime.RuntimeInspector.endpoint", lambda self: {"status":"unavailable","latency":2.0})
    health = Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval)).health()
    assert health.status == "degraded" and health.embedding_endpoint_status == "unavailable"


def test_audit_is_metadata_only_and_rotates(tmp_path):
    journal, retrieval = databases(tmp_path); log = tmp_path / "logs/audit.jsonl"
    brain = Brain(BrainConfig(journal_db_path=journal,retrieval_db_path=retrieval,audit_log_path=log,audit_max_bytes=100,audit_backup_count=1))
    brain.audit.write("search", query="TOP SECRET QUERY", returned_record_ids=["rec-1"], error_code="BRAIN_TEST")
    first = "".join(p.read_text() for p in log.parent.glob("audit.jsonl*"))
    assert "TOP SECRET QUERY" not in first and "rec-1" in first and "BRAIN_TEST" in first
    for _ in range(10): brain.audit.write("health", returned_record_ids=["rec-1"])
    assert list(log.parent.glob("audit.jsonl.1"))
