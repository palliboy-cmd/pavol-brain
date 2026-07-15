"""Read-only access to the append-only canonical journal."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from brain import artifact_validation as av
from brain import instance_identity


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class JournalReader:
    def __init__(self, path: Path, instance_id: str | None = None):
        self.path = Path(path)
        # Package 1 (closes B2): None preserves the pre-existing behavior of
        # standalone/diagnostic callers (e.g. run_brain_projector.py's
        # --plan/audit path) that never claim an instance and must not be
        # gated by one. ProjectionProjector always passes its configured
        # instance_id, which enforce_journal itself no-ops for "legacy".
        self.instance_id = instance_id

    @contextmanager
    def connect(self):
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        con = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        try:
            if self.instance_id is not None:
                instance_identity.enforce_journal(con, self.instance_id)
            yield con
        finally:
            con.close()

    @staticmethod
    def source_event_id(event):
        """Stable ordering key: canonical event timestamp plus immutable event id.

        ``event_id`` is immutable but current journal ids are random UUID-derived
        strings, so it is not itself chronological. The composite is persisted as
        the cursor and is compared lexically; it never relies on SQLite rowid.
        """
        return f"{event['occurred_at']}\x1f{event['event_id']}"

    def events_after(self, cursor: str | None, limit: int):
        with self.connect() as con:
            rows = con.execute("SELECT * FROM memory_events ORDER BY occurred_at ASC, event_id ASC").fetchall()
        events = [dict(row) for row in rows]
        if cursor is not None:
            events = [event for event in events if self.source_event_id(event) > cursor]
        return events[:limit]

    def head(self):
        events = self.events_after(None, 1_000_000_000)
        return self.source_event_id(events[-1]) if events else None

    def snapshot(self, record_id: str, validation_as_of: str | None = None):
        with self.connect() as con:
            row = con.execute("""SELECT r.*, s.status, s.invalid_at, s.supersedes, s.superseded_by,
                     s.updated_event_id FROM memory_records r JOIN record_state s USING(record_id)
                     WHERE r.record_id=?""", (record_id,)).fetchone()
            if not row:
                return None
            data = dict(row)
            data["payload"] = json.loads(data["payload"])
            links = con.execute("""SELECT artifact_uri, relation, confidence, origin, verified_at, active
                                FROM artifact_links WHERE record_id=? ORDER BY relation, artifact_uri""", (record_id,)).fetchall()
            data["artifact_links"] = [dict(link) for link in links]
            if data["type"] == "artifact_link":
                pairs = [(link["artifact_uri"], link["relation"], link["confidence"]) for link in data["artifact_links"]]
                if not pairs:
                    pairs = [(data["payload"]["artifact_uri"], data["payload"]["relation"], data["confidence"])]
                data["artifact_relations"] = [{"artifact_link_id": av.link_id(record_id, relation, uri), "artifact_uri": uri, "relation": relation, "confidence": confidence}
                                              for uri, relation, confidence in pairs]
                try:
                    events = av.read_events(con, record_id)
                except sqlite3.OperationalError:
                    # Validation tables absent: every relation stays unknown; the
                    # projector must stop with REBUILD_REQUIRED, never guess.
                    events = []
                folded = av.fold_events(events, as_of=validation_as_of)
                data["artifact_validation"] = {rel["artifact_link_id"]: folded.get(rel["artifact_link_id"]) for rel in data["artifact_relations"]}
            return data

    def audit(self):
        with self.connect() as con:
            tables = {}
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                name = row["name"]
                tables[name] = [dict(column) for column in con.execute(f"PRAGMA table_info({name})")]
            counts = {row["status"]: row["n"] for row in con.execute("SELECT status,count(*) n FROM record_state GROUP BY status")}
            return {
                "tables": tables,
                "event_ordering_key": "occurred_at ASC, event_id ASC; persisted source_event_id=occurred_at\\u001fevent_id",
                "status_action_mapping": {"record_created": "initial candidate/accepted state", "record_approved": "accepted", "record_rejected": "rejected", "record_superseded": "superseded", "record_forgotten": "forgotten"},
                "event_count": con.execute("SELECT count(*) FROM memory_events").fetchone()[0],
                "journal_head": self.head(),
                "record_status_counts": {key: counts.get(key, 0) for key in ("accepted", "superseded", "forgotten", "rejected", "candidate")},
                "artifact_link_count": con.execute("SELECT count(*) FROM artifact_links").fetchone()[0],
            }
