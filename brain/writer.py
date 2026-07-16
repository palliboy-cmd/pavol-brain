"""Transactional append-only writer for the canonical M1 journal."""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .errors import BrainError
from .models import WriteResponse
from .write_policy import classify, enforce_band_c, validate_evidence_uris
from .artifact_verifier import verify_all
from . import artifact_validation as av
from . import instance_identity
from .control import PERSONAL_WORKSPACES,WORK_WORKSPACES

RECORD_TYPES = {"problem", "analysis", "decision", "outcome"}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def now():
    return datetime.now(timezone.utc).isoformat()

def parse_time(value, request_id):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        raise BrainError("BRAIN_INVALID_VALID_AT", "valid_at must be timezone-aware ISO-8601", request_id)

class JournalWriter:
    def __init__(self, config):
        self.config = config

    def connect(self):
        path = Path(self.config.journal_db_path)
        if not path.is_file():
            raise BrainError("BRAIN_JOURNAL_UNAVAILABLE", "canonical journal is unavailable", "")
        try:
            con = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            sql = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_records'").fetchone()
            if not sql or "'problem'" not in sql[0] or "'analysis'" not in sql[0]:
                raise BrainError("BRAIN_SCHEMA_MIGRATION_REQUIRED", "journal must be migrated to M1 schema v2", "")
            instance_identity.enforce_journal(con, self.config.instance_id)
            return con
        except BrainError:
            raise
        except sqlite3.Error as exc:
            raise BrainError("BRAIN_JOURNAL_UNAVAILABLE", "canonical journal is unavailable", "", {"reason": type(exc).__name__}) from exc

    def _existing(self, con, idempotency_key, content_hash, request_hash, request_id):
        row = con.execute("""SELECT r.record_id,r.type,r.workspace,r.content_hash,r.created_at,
                       s.status,s.review,s.updated_event_id,
                       (SELECT event_id FROM memory_events e WHERE e.record_id=r.record_id AND e.event_type='record_created' ORDER BY occurred_at,event_id LIMIT 1) created_event_id,
                       (SELECT data FROM memory_events e WHERE e.record_id=r.record_id AND e.event_type='record_created' ORDER BY occurred_at,event_id LIMIT 1) created_event_data
                       FROM memory_records r
                       JOIN record_state s USING(record_id) WHERE r.idempotency_key=?""", (idempotency_key,)).fetchone()
        if not row:
            return None
        if row["content_hash"] != content_hash:
            raise BrainError("BRAIN_IDEMPOTENCY_CONFLICT", "idempotency key was already used for different content", request_id)
        created=json.loads(row["created_event_data"] or "{}")
        stored_request_hash = created.get("request_hash")
        if not stored_request_hash:
            raise BrainError("BRAIN_IDEMPOTENCY_CONFLICT", "idempotency key was already used by a legacy record with no stored request_hash",
                             request_id, {"reason": "legacy_record_without_request_hash"})
        if stored_request_hash != request_hash:
            raise BrainError("BRAIN_IDEMPOTENCY_CONFLICT", "idempotency key was already used for a different write request", request_id)
        initial_status=created.get("status","candidate");initial_review=created.get("review","pending")
        band = "A" if initial_status == "accepted" else "B"
        return WriteResponse(request_id=request_id, record_id=row["record_id"], event_id=row["created_event_id"] or row["updated_event_id"],
                             type=row["type"], workspace=row["workspace"], status=initial_status, review=initial_review,
                             policy_band=band, idempotent=True, created_at=row["created_at"],
                             projection_pending=row["status"] == "accepted")

    def record(self, record_type, payload, metadata, *, request_id, agent_id, allowed_workspaces=None):
        if record_type not in RECORD_TYPES:
            raise BrainError("BRAIN_INVALID_TYPE", "unsupported write record type", request_id)
        workspace = metadata.get("workspace")
        if not workspace:
            raise BrainError("BRAIN_WORKSPACE_REQUIRED", "workspace is required after profile scope resolution", request_id)
        if allowed_workspaces is not None and workspace not in set(allowed_workspaces):
            raise BrainError("BRAIN_WORKSPACE_DENIED", "workspace is not granted", request_id, {"workspaces": [workspace]})
        if self.config.instance_id=="legacy":
            raise BrainError("BRAIN_WRITE_DISABLED","legacy Brain instance is permanently read-only",request_id)
        if self.config.instance_id=="personal" and workspace not in PERSONAL_WORKSPACES:
            raise BrainError("BRAIN_INSTANCE_DENIED","workspace does not belong to the Personal Brain instance",request_id)
        if self.config.instance_id=="work" and workspace not in WORK_WORKSPACES:
            raise BrainError("BRAIN_INSTANCE_DENIED","workspace does not belong to the WORK Brain instance",request_id)
        if not agent_id:
            raise BrainError("BRAIN_IDENTITY_REQUIRED", "trusted agent identity is required", request_id)
        if self.config.instance_id=="work" or workspace in self.config.sensitive_workspace_floor:
            metadata["sensitivity"]="sensitive"

        evidence = list(payload.get("evidence", []))
        if record_type == "decision":
            for alternative in payload.get("alternatives", []): evidence.extend(alternative.get("evidence", []))
        artifacts = list(payload.get("artifacts", []))
        if payload.get("commit"): artifacts.append(payload["commit"])
        # F2: every client-controlled string must clear the secret gate before
        # any error path that can echo it -- including the URI-syntax error's
        # `details.values`. A bare (non-URI-shaped) secret in evidence/artifacts
        # would otherwise fail URI syntax first and be echoed back verbatim.
        provenance = {key: metadata.get(key) for key in ("source_assertion", "source_excerpt", "source_ref", "session_ref")}
        enforce_band_c({"payload":payload,"client_metadata":metadata}, provenance, request_id)
        validate_evidence_uris(evidence, request_id)
        validate_evidence_uris(artifacts, request_id, "artifacts")
        artifact_results=verify_all(artifacts,self.config.artifact_repo_roots or {})
        band, status, review, confidence = classify(record_type, payload, metadata["source_assertion"], metadata.get("source_ref"), request_id,artifact_results)
        if metadata.get("supersedes") and not metadata.get("change_reason"):
            raise BrainError("BRAIN_CHANGE_REASON_REQUIRED", "supersede requires change_reason", request_id)
        if status != "accepted" and metadata.get("supersedes"):
            raise BrainError("BRAIN_CANDIDATE_CANNOT_SUPERSEDE", "candidate writes cannot supersede records", request_id)

        created_at = now()
        valid_at = parse_time(metadata["valid_at"], request_id) if metadata.get("valid_at") else created_at
        content_hash = hashlib.sha256(canonical({"type": record_type, "workspace": workspace, "payload": payload}).encode()).hexdigest()
        request_hash = hashlib.sha256(canonical({"instance_id":self.config.instance_id,"agent_id":agent_id,"record_type":record_type,
            "workspace":workspace,"content_hash":content_hash,"sensitivity":metadata["sensitivity"],
            "source_assertion":metadata["source_assertion"],"source_excerpt":metadata.get("source_excerpt"),
            "source_ref":metadata.get("source_ref"),"session_ref":metadata.get("session_ref"),
            "valid_at":metadata.get("valid_at"),"supersedes":metadata.get("supersedes"),
            "change_reason":metadata.get("change_reason"),"links":metadata.get("links",[])}).encode()).hexdigest()
        client_key = metadata.get("idempotency_key") or content_hash
        idempotency_key = "m1:"+hashlib.sha256(canonical({"instance_id":self.config.instance_id,"agent_id":agent_id,"key":client_key}).encode()).hexdigest()
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            existing = self._existing(con, idempotency_key, content_hash, request_hash, request_id)
            if existing:
                con.rollback()
                return existing

            if record_type=="decision" and status=="accepted" and not metadata.get("supersedes"):
                statement=" ".join(payload["statement"].split()).casefold()
                for row in con.execute("""SELECT r.payload FROM memory_records r JOIN record_state s USING(record_id)
                                        WHERE r.workspace=? AND r.type='decision' AND s.status='accepted'""",(workspace,)):
                    current=json.loads(row["payload"])
                    if " ".join(str(current.get("statement","")).split()).casefold()==statement:
                        band,status,review="B","candidate","pending"
                        break

            duplicate=con.execute("""SELECT record_id,agent_id FROM memory_records
                                  WHERE content_hash=? AND workspace=? ORDER BY created_at,record_id LIMIT 1""",
                                  (content_hash,workspace)).fetchone()
            if duplicate:
                band,status,review="B","candidate","pending"
                if metadata.get("supersedes"):
                    raise BrainError("BRAIN_CANDIDATE_CANNOT_SUPERSEDE", "candidate writes cannot supersede records", request_id)

            supersedes = metadata.get("supersedes")
            target = None
            if supersedes:
                target = con.execute("""SELECT r.type,r.workspace,s.status FROM memory_records r
                                      JOIN record_state s USING(record_id) WHERE r.record_id=?""", (supersedes,)).fetchone()
                if not target:
                    raise BrainError("BRAIN_SUPERSEDE_TARGET_NOT_FOUND", "supersede target does not exist", request_id)
                if target["workspace"] != workspace or target["type"] != record_type:
                    raise BrainError("BRAIN_SUPERSEDE_SCOPE_MISMATCH", "supersede target must have the same workspace and type", request_id)
                if target["status"] != "accepted":
                    raise BrainError("BRAIN_INVALID_SUPERSEDE_STATE", "only an accepted record may be superseded", request_id)

            for link in metadata.get("links", []):
                target_id = link["target_record_id"]
                linked = con.execute("""SELECT r.workspace,s.status FROM memory_records r JOIN record_state s USING(record_id)
                                      WHERE r.record_id=?""", (target_id,)).fetchone()
                if not linked or linked["status"] in {"rejected", "forgotten"}:
                    raise BrainError("BRAIN_LINK_TARGET_NOT_FOUND", "record link target is unavailable", request_id, {"record_id": target_id})
                if linked["workspace"] != workspace:
                    raise BrainError("BRAIN_CROSS_WORKSPACE_LINK_DENIED", "record links cannot cross workspace boundaries in M1", request_id)

            record_id = "rec_" + uuid.uuid4().hex
            event_id = "evt_" + uuid.uuid4().hex
            raw = canonical({"record_type":record_type,"payload":payload,"metadata":metadata})
            normalized_payload=canonical(payload)
            con.execute("""INSERT INTO memory_records(record_id,schema_version,type,workspace,sensitivity,raw_input,payload,
                         content_hash,idempotency_key,agent_id,source_assertion,source_excerpt,source_ref,session_ref,
                         confidence,valid_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (record_id, 2, record_type, workspace, metadata["sensitivity"], raw, normalized_payload, content_hash,
                         idempotency_key, agent_id, metadata["source_assertion"], metadata.get("source_excerpt"),
                         metadata.get("source_ref"), metadata.get("session_ref"), confidence, valid_at, created_at))
            event_data = {"status": status, "review": review, "request_hash":request_hash}
            if duplicate:event_data["possible_duplicate_of"]=duplicate["record_id"]
            if supersedes:event_data.update(supersedes=supersedes,change_reason=metadata["change_reason"])
            con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)",
                        (event_id, record_id, "record_created", created_at, agent_id, canonical(event_data)))
            con.execute("""INSERT INTO record_state(record_id,status,review,invalid_at,supersedes,superseded_by,
                         change_reason,projection,projection_error,projected_build,updated_event_id)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (record_id, status, review, None, supersedes, None, metadata.get("change_reason"),
                         "none", None, None, event_id))

            relation_rows = []
            for uri in sorted(set(artifacts + evidence)):
                result=artifact_results.get(uri) or {"valid":False}
                relation_rows.append((record_id, uri, "evidence" if uri in evidence else "touches", confidence, "deterministic" if result.get("valid") else "derived", created_at, 1))
            for link in metadata.get("links", []):
                relation_rows.append((record_id, "record://" + link["target_record_id"], link["relation"], confidence, "deterministic", created_at, 1))
            con.executemany("INSERT INTO artifact_links VALUES (?,?,?,?,?,?,?)", relation_rows)
            validation_rows=[]
            for uri in sorted(set(artifacts)):
                result=artifact_results[uri];relation="touches"
                lid=av.link_id(record_id,relation,uri);key=f"artifact-validation:m1:{lid}:{result['state']}"
                validation_rows.append((av.event_id_for(key),lid,record_id,uri,relation,created_at,created_at,result["state"],result["reason"],
                                        "server-artifact-validator",result["method"],canonical({"uri":uri,"method":result["method"]}),None,key,None))
            if validation_rows:
                con.executemany("INSERT INTO artifact_validation_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",validation_rows)
                av.rebuild_state(con)

            if supersedes:
                supersede_event = "evt_" + uuid.uuid4().hex
                con.execute("INSERT INTO memory_events VALUES (?,?,?,?,?,?)",
                            (supersede_event, supersedes, "record_superseded", created_at, agent_id,
                             canonical({"superseded_by": record_id, "invalid_at": valid_at, "reason": metadata["change_reason"]})))
                con.execute("""UPDATE record_state SET status='superseded',invalid_at=?,superseded_by=?,change_reason=?,
                             updated_event_id=? WHERE record_id=? AND status='accepted'""",
                            (valid_at, record_id, metadata["change_reason"], supersede_event, supersedes))
            con.commit()
            return WriteResponse(request_id=request_id, record_id=record_id, event_id=event_id, type=record_type,
                                 workspace=workspace, status=status, review=review, policy_band=band,
                                 idempotent=False, created_at=created_at, projection_pending=status == "accepted")
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()
