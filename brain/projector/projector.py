"""Crash-safe, bounded journal-to-retrieval projection."""
import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .cursor import get_cursor, set_cursor
from .embedding_cache import pack
from .errors import ProjectorError, RebuildRequired
from .journal_reader import JournalReader
from .models import ProjectionReport, ProjectionStatus
from .projection import FORBIDDEN, document, eligible, unresolved_relations
from .validation import validate


MIGRATION = """
CREATE TABLE IF NOT EXISTS retrieval_projection_cursor (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), last_source_event_id TEXT, last_projected_at TEXT,
 projection_schema_version TEXT NOT NULL, embedding_model_fingerprint TEXT NOT NULL,
 embedding_dimension INTEGER NOT NULL, projector_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_document_links (
 record_id TEXT NOT NULL REFERENCES retrieval_documents(record_id) ON DELETE CASCADE,
 artifact_uri TEXT NOT NULL, relation TEXT NOT NULL, confidence REAL NOT NULL, origin TEXT NOT NULL,
 verified_at TEXT NOT NULL, PRIMARY KEY(record_id, artifact_uri, relation)
);
"""
KNOWN_EVENT_TYPES = {"record_created", "record_approved", "record_rejected", "record_superseded", "record_forgotten", "projection_started", "projection_succeeded", "projection_failed"}


class ProjectionProjector:
    def __init__(self, config, embedder, failure_injector=None):
        self.config, self.embedder, self.failure_injector = config, embedder, failure_injector
        self.journal = JournalReader(config.journal_db_path)

    @contextmanager
    def _write(self):
        path = Path(self.config.retrieval_db_path); path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path); con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA foreign_keys=ON")
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='retrieval_documents'").fetchone():
                schema = Path(__file__).resolve().parents[2] / "sqlite-spike" / "schema.sql"
                con.executescript(schema.read_text())
            con.executescript(MIGRATION)
            # Existing Slice-1 baseline files are readable without the new columns.
            existing = {row[1] for row in con.execute("PRAGMA table_info(retrieval_documents)")}
            for name, sql in {
                "is_current": "ALTER TABLE retrieval_documents ADD COLUMN is_current INTEGER NOT NULL DEFAULT 0",
                "source_event_id": "ALTER TABLE retrieval_documents ADD COLUMN source_event_id TEXT",
                "supersedes": "ALTER TABLE retrieval_documents ADD COLUMN supersedes TEXT",
                "superseded_by": "ALTER TABLE retrieval_documents ADD COLUMN superseded_by TEXT",
            }.items():
                if name not in existing: con.execute(sql)
            yield con
        finally:
            con.close()

    def status(self):
        head = self.journal.head()
        if not Path(self.config.retrieval_db_path).exists():
            return {"status": ProjectionStatus.HEALTHY.value, "cursor": None, "journal_head": head, "issues": []}
        with closing(sqlite3.connect(self.config.retrieval_db_path)) as con:
            con.row_factory = sqlite3.Row
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "retrieval_projection_cursor" not in tables:
                return {"status": ProjectionStatus.HEALTHY.value, "cursor": None, "journal_head": head, "issues": []}
            issues = validate(con, head, self.config); cursor = get_cursor(con)
            return {"status": ProjectionStatus.REBUILD_REQUIRED.value if issues else ProjectionStatus.HEALTHY.value,
                    "cursor": cursor, "journal_head": head, "issues": issues}

    def validate(self): return self.status()

    def plan(self, batch_size=100):
        head = self.journal.head(); before = None; issues = []
        if Path(self.config.retrieval_db_path).exists():
            with closing(sqlite3.connect(self.config.retrieval_db_path)) as con:
                con.row_factory = sqlite3.Row
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "retrieval_projection_cursor" in tables:
                    cursor = get_cursor(con); before = cursor["last_source_event_id"] if cursor else None; issues = validate(con, head, self.config)
        if issues: return ProjectionReport(ProjectionStatus.REBUILD_REQUIRED, before, before, head, details={"issues": issues})
        events = self.journal.events_after(before, batch_size)
        return ProjectionReport(ProjectionStatus.NO_CHANGES if not events else ProjectionStatus.HEALTHY, before,
                                self.journal.source_event_id(events[-1]) if events else before, head,
                                events_seen=len(events), details={"write_free": True, "record_ids": sorted({event['record_id'] for event in events})})

    def _inject(self, point):
        if self.failure_injector: self.failure_injector(point)

    def _remove(self, con, record_id, report):
        row = con.execute("SELECT record_id FROM retrieval_documents WHERE record_id=?", (record_id,)).fetchone()
        if not row: report.noops += 1; return "already_absent"
        report.links_removed += con.execute("SELECT count(*) FROM retrieval_document_links WHERE record_id=?", (record_id,)).fetchone()[0]
        con.execute("DELETE FROM retrieval_embeddings WHERE record_id=?", (record_id,))
        con.execute("DELETE FROM retrieval_fts WHERE rowid=(SELECT doc_id FROM retrieval_documents WHERE record_id=?)", (record_id,))
        con.execute("DELETE FROM retrieval_documents WHERE record_id=?", (record_id,)); report.removed += 1
        return "removed"

    def _upsert(self, con, doc, report):
        previous = con.execute("SELECT * FROM retrieval_documents WHERE record_id=?", (doc["record_id"],)).fetchone()
        if previous and previous["projection_hash"] == doc["projection_hash"]:
            report.noops += 1; action = "unchanged"
        else:
            values = (doc["record_id"], doc["workspace"], doc["type"], doc["sensitivity"], doc["status"], doc["valid_at"], doc["invalid_at"], doc["confidence"], doc["title"], doc["body"], doc["artifacts_text"], doc["canonical_text"], doc["projection_hash"], int(doc["is_current"]), doc["source_event_id"], doc["supersedes"], doc["superseded_by"])
            if previous:
                con.execute("""UPDATE retrieval_documents SET workspace=?,type=?,sensitivity=?,status=?,valid_at=?,invalid_at=?,confidence=?,title=?,body=?,artifacts_text=?,canonical_text=?,projection_hash=?,is_current=?,source_event_id=?,supersedes=?,superseded_by=? WHERE record_id=?""", values[1:] + (doc["record_id"],))
                con.execute("DELETE FROM retrieval_fts WHERE rowid=?", (previous["doc_id"],)); report.updated += 1
                doc_id = previous["doc_id"]; action = "updated"
            else:
                con.execute("""INSERT INTO retrieval_documents(record_id,workspace,type,sensitivity,status,valid_at,invalid_at,confidence,title,body,artifacts_text,canonical_text,projection_hash,is_current,source_event_id,supersedes,superseded_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
                doc_id = con.execute("SELECT doc_id FROM retrieval_documents WHERE record_id=?", (doc["record_id"],)).fetchone()[0]; report.inserted += 1; action = "inserted"
            con.execute("INSERT INTO retrieval_fts(rowid,title,body,artifacts_text) VALUES (?,?,?,?)", (doc_id, doc["title"], doc["body"], doc["artifacts_text"]))
        existing = {(r["artifact_uri"], r["relation"]) for r in con.execute("SELECT artifact_uri,relation FROM retrieval_document_links WHERE record_id=?", (doc["record_id"],))}
        desired = {(link["artifact_uri"], link["relation"]): link for link in doc["artifact_links"]}
        for key in existing - desired.keys(): con.execute("DELETE FROM retrieval_document_links WHERE record_id=? AND artifact_uri=? AND relation=?", (doc["record_id"], *key)); report.links_removed += 1
        for key, link in desired.items():
            if key not in existing: report.links_added += 1
            con.execute("INSERT OR REPLACE INTO retrieval_document_links VALUES (?,?,?,?,?,?)", (doc["record_id"], link["artifact_uri"], link["relation"], link["confidence"], link["origin"], link["verified_at"]))
        cached = con.execute("SELECT projection_hash,model_fingerprint,dimensions FROM retrieval_embeddings WHERE record_id=?", (doc["record_id"],)).fetchone()
        if cached and cached["projection_hash"] == doc["projection_hash"] and cached["model_fingerprint"] == self.config.embedding_model_fingerprint and cached["dimensions"] == self.config.embedding_dimension:
            report.embeddings_reused += 1; return action
        vector, exact_model = self.embedder.embed_document(doc["canonical_text"])
        if len(vector) != self.config.embedding_dimension: raise ProjectorError("embedding_dimension_changed_during_projection")
        con.execute("""INSERT OR REPLACE INTO retrieval_embeddings(record_id,model_fingerprint,model_identifier,dimensions,vector,vector_norm,projection_hash,created_at) VALUES (?,?,?,?,?,?,?,?)""", (doc["record_id"], self.config.embedding_model_fingerprint, exact_model, len(vector), pack(vector), 1.0, doc["projection_hash"], datetime.now(timezone.utc).isoformat()))
        report.embeddings_created += 1
        return action

    def _assert_projected(self, con, doc):
        """Gate cursor movement on a complete derived document + embedding pair."""
        row = con.execute("""SELECT d.projection_hash document_hash,e.projection_hash embedding_hash,
                          e.model_fingerprint,e.dimensions
                          FROM retrieval_documents d LEFT JOIN retrieval_embeddings e USING(record_id)
                          WHERE d.record_id=?""", (doc["record_id"],)).fetchone()
        if not row:
            raise ProjectorError("accepted_record_missing_document")
        if row["document_hash"] != doc["projection_hash"]:
            raise ProjectorError("accepted_record_document_hash_mismatch")
        if row["embedding_hash"] != doc["projection_hash"]:
            raise ProjectorError("accepted_record_missing_or_mismatched_embedding")
        if row["model_fingerprint"] != self.config.embedding_model_fingerprint or row["dimensions"] != self.config.embedding_dimension:
            raise ProjectorError("accepted_record_embedding_contract_mismatch")

    @staticmethod
    def _skip_reason(record):
        if record["status"] in FORBIDDEN:
            return "status_" + record["status"]
        if record["type"] == "artifact_link":
            return "artifact_no_verified_active_relations"
        return "not_eligible"

    def run_once(self, batch_size=100):
        with self._write() as con:
            head = self.journal.head(); issues = validate(con, head, self.config)
            cursor = get_cursor(con); before = cursor["last_source_event_id"] if cursor else None
            if issues: return ProjectionReport(ProjectionStatus.REBUILD_REQUIRED, before, before, head, details={"issues": issues})
            events = self.journal.events_after(before, batch_size)
            if not events: return ProjectionReport(ProjectionStatus.NO_CHANGES, before, before, head)
            report = ProjectionReport(ProjectionStatus.HEALTHY, before, self.journal.source_event_id(events[-1]), head, events_seen=len(events))
            unknown = sorted({event["event_type"] for event in events if event["event_type"] not in KNOWN_EVENT_TYPES})
            if unknown:
                return ProjectionReport(ProjectionStatus.REBUILD_REQUIRED, before, before, head, events_seen=len(events), details={"issues": ["unknown_projection_event_type"], "event_types": unknown})
            # Relation validity is resolved as of build time (Proposal 008); a
            # validation event with a future effective_at does not apply yet.
            build_time = datetime.now(timezone.utc).isoformat()
            snapshots = {event["record_id"]: self.journal.snapshot(event["record_id"], validation_as_of=build_time) for event in events}
            missing = sorted(record_id for record_id, record in snapshots.items() if record is None)
            if missing:
                return ProjectionReport(ProjectionStatus.REBUILD_REQUIRED, before, before, head, events_seen=len(events),
                                        details={"issues": ["missing_record_snapshot"], "record_ids": missing})
            unresolved = {record_id: links for record_id, record in snapshots.items() if record and (links := unresolved_relations(record))}
            if unresolved:
                return ProjectionReport(ProjectionStatus.REBUILD_REQUIRED, before, before, head, events_seen=len(events),
                                        details={"issues": ["artifact_validation_missing"], "record_ids": sorted(unresolved),
                                                 "artifact_link_ids": sorted(link for links in unresolved.values() for link in links)})
            try:
                con.execute("BEGIN IMMEDIATE"); self._inject("after_batch_read")
                # Coalesce event batches by record while retaining the batch cursor.
                source_ids = {event["record_id"]: self.journal.source_event_id(event) for event in events}
                outcomes = []
                for record_id, source_id in source_ids.items():
                    record = snapshots[record_id]
                    if not eligible(record):
                        outcomes.append({"record_id": record_id, "result": "skipped", "reason": self._skip_reason(record),
                                         "action": self._remove(con, record_id, report)})
                    else:
                        doc = document(record, source_id, self.config.projection_schema_version)
                        action = self._upsert(con, doc, report)
                        self._assert_projected(con, doc)
                        outcomes.append({"record_id": record_id, "result": "projected", "action": action,
                                         "projection_hash": doc["projection_hash"]})
                report.details["record_outcomes"] = outcomes
                self._inject("after_documents"); self._inject("after_embeddings"); self._inject("before_cursor_update")
                set_cursor(con, report.cursor_after, self.config); self._inject("before_commit")
                con.execute("INSERT OR REPLACE INTO retrieval_embedding_meta(key,value) VALUES (?,?)", ("contract", json.dumps({"fingerprint": self.config.embedding_model_fingerprint, "exact_model_identifier": self.config.embedding_model_identifier, "dimension": self.config.embedding_dimension}, sort_keys=True)))
                con.commit(); return report
            except Exception as exc:
                con.rollback()
                if isinstance(exc, RebuildRequired): raise
                raise ProjectorError(f"projection_transaction_rolled_back:{type(exc).__name__}:{exc}") from exc
