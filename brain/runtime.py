"""Read-only runtime diagnostics. No query or record content is inspected."""
import json
import sqlite3
import time
import urllib.request
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _tables(con): return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

@contextmanager
def _ro(path):
    con = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row; con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()

def _event_rows(con):
    tables = _tables(con)
    if "memory_events" not in tables: return []
    return list(con.execute("SELECT occurred_at,event_id FROM memory_events ORDER BY occurred_at,event_id"))

def cursor(row): return f"{row['occurred_at']}\x1f{row['event_id']}" if row else None

class RuntimeInspector:
    _probe_cache = {}
    def __init__(self, config, meta): self.config, self.meta = config, meta

    def endpoint(self):
        key = (self.config.embedding_base_url, self.config.embedding_model)
        cached = self._probe_cache.get(key)
        if cached and time.monotonic() - cached[0] < self.config.endpoint_probe_ttl: return cached[1]
        start = time.monotonic(); result = {"status": "unavailable", "latency": None}
        try:
            url = self.config.embedding_base_url.rstrip("/")
            req = urllib.request.Request(url + "/models")
            with urllib.request.urlopen(req, timeout=self.config.endpoint_probe_timeout) as response: data = json.loads(response.read())
            models = {x.get("id") for x in data.get("data", [])}
            result["status"] = "available" if not models or self.config.embedding_model in models else "unavailable"
        except Exception: pass
        result["latency"] = round((time.monotonic() - start) * 1000, 3)
        self._probe_cache[key] = (time.monotonic(), result); return result

    def inspect(self):
        out = {"retrieval_db_available": False, "journal_available": False, "per_workspace_counts": {}}
        events = []
        with ExitStack() as stack:
            try:
                journal = stack.enter_context(_ro(self.config.journal_db_path)); events = _event_rows(journal); out["journal_available"] = True
            except (OSError, sqlite3.Error): journal = None
            try:
                retrieval = stack.enter_context(_ro(self.config.retrieval_db_path)); tables = _tables(retrieval); out["retrieval_db_available"] = "retrieval_documents" in tables
            except (OSError, sqlite3.Error): retrieval = None; tables = set()
            head = cursor(events[-1]) if events else None; out["journal_head_cursor"] = head
            out.update(active_build_id=None,indexed_document_count=None,current_document_count=None,embedding_count=None,
                       embedding_coverage=None,embedding_model=None,embedding_fingerprint=None,embedding_dimension=None,
                       projection_schema_version=None,retrieval_cursor=None,last_successful_projector_run=None,
                       last_successful_full_rebuild=None,last_failed_projector_run=None)
            rebuild = False
            if out["retrieval_db_available"]:
                out["indexed_document_count"] = retrieval.execute("SELECT count(*) FROM retrieval_documents").fetchone()[0]
                out["current_document_count"] = retrieval.execute("SELECT count(*) FROM retrieval_documents WHERE status='accepted'").fetchone()[0]
                out["per_workspace_counts"] = {r[0]: r[1] for r in retrieval.execute("SELECT workspace,count(*) FROM retrieval_documents GROUP BY workspace")}
                if "retrieval_embeddings" in tables: out["embedding_count"] = retrieval.execute("SELECT count(*) FROM retrieval_embeddings").fetchone()[0]
                total = out["indexed_document_count"]; out["embedding_coverage"] = out["embedding_count"] / total if total else 1.0
                meta = self.meta(); out["active_build_id"] = meta.get("build_id"); out["embedding_model"] = meta.get("embedding_model")
                contract = meta.get("contract", {}); out["embedding_fingerprint"] = contract.get("fingerprint"); out["embedding_dimension"] = contract.get("dimension")
                if "retrieval_projection_cursor" in tables:
                    row = retrieval.execute("SELECT * FROM retrieval_projection_cursor WHERE singleton=1").fetchone()
                    if row:
                        out["retrieval_cursor"] = row["last_source_event_id"]; out["projection_schema_version"] = row["projection_schema_version"]
                        out["last_successful_projector_run"] = row["last_projected_at"]
                else: rebuild = bool(head)
            rc = out["retrieval_cursor"]
            positions = {cursor(r): i for i, r in enumerate(events)}
            if rc is None: gap = len(events)
            elif rc in positions: gap = len(events) - positions[rc] - 1
            else: gap = None; rebuild = True
            ahead = bool(rc and head and rc not in positions)
            behind = bool(head and rc != head and not ahead)
            out["cursor_gap_events"] = gap
            unprojected = events[positions[rc]+1 if rc in positions else 0:] if behind else []
            age = None
            if unprojected:
                try: age = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(unprojected[0]["occurred_at"].replace("Z", "+00:00"))).total_seconds())
                except ValueError: pass
            stale = behind and ((age is not None and age > self.config.stale_after_seconds) or
                                (self.config.stale_gap_events is not None and gap is not None and gap > self.config.stale_gap_events))
            probe = self.endpoint() if out["retrieval_db_available"] else {"status":"not_configured","latency":None}
            out.update(index_behind=behind, stale_index=bool(stale), oldest_unprojected_age_seconds=age,
                       rebuild_required=rebuild or ahead, embedding_endpoint_status=probe["status"], endpoint_probe_latency_ms=probe["latency"])
            if not out["retrieval_db_available"] or not out["journal_available"]: out["status"] = "unavailable"
            elif stale or rebuild or ahead or probe["status"] == "unavailable": out["status"] = "degraded"
            else: out["status"] = "healthy"
            return out
