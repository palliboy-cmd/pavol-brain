from dataclasses import dataclass
from pathlib import Path
import os
import json

def instance_paths(instance_id: str):
    state=Path(os.getenv("BRAIN_STATE_DIR",str(Path.home()/"Library/Application Support/Pavol-Brain")))
    if instance_id=="personal":
        return (Path(os.getenv("BRAIN_PERSONAL_JOURNAL_DB",str(state/"personal/journal.db"))),
                Path(os.getenv("BRAIN_PERSONAL_RETRIEVAL_DB",str(state/"personal/retrieval.db"))))
    if instance_id=="work":
        return (Path(os.getenv("BRAIN_WORK_JOURNAL_DB",str(state/"work/journal.db"))),
                Path(os.getenv("BRAIN_WORK_RETRIEVAL_DB",str(state/"work/retrieval.db"))))
    return (Path(os.getenv("BRAIN_JOURNAL_DB","spike/spike.db")),Path(os.getenv("BRAIN_RETRIEVAL_DB","sqlite-spike/retrieval.db")))

@dataclass(frozen=True)
class BrainConfig:
    journal_db_path: Path = Path(os.getenv("BRAIN_JOURNAL_DB", "spike/spike.db"))
    retrieval_db_path: Path = Path(os.getenv("BRAIN_RETRIEVAL_DB", "sqlite-spike/retrieval.db"))
    embedding_base_url: str = os.getenv("BRAIN_EMBEDDING_BASE_URL", "http://localhost:11434/v1")
    embedding_model: str = os.getenv("BRAIN_EMBEDDING_MODEL", "nomic-embed-text:latest")
    embedding_dimension: int | None = int(os.getenv("BRAIN_EMBEDDING_DIMENSION", "768"))
    timeout: float = float(os.getenv("BRAIN_EMBEDDING_TIMEOUT", "60"))
    schema_version: str = "v1"
    stale_after_seconds: float = float(os.getenv("BRAIN_STALE_AFTER_SECONDS", "3600"))
    stale_gap_events: int | None = int(os.environ["BRAIN_STALE_GAP_EVENTS"]) if os.getenv("BRAIN_STALE_GAP_EVENTS") else None
    endpoint_probe_timeout: float = float(os.getenv("BRAIN_ENDPOINT_PROBE_TIMEOUT", "2"))
    endpoint_probe_ttl: float = float(os.getenv("BRAIN_ENDPOINT_PROBE_TTL", "30"))
    audit_log_path: Path | None = Path(os.environ["BRAIN_AUDIT_LOG"]) if os.getenv("BRAIN_AUDIT_LOG") else None
    audit_max_bytes: int = int(os.getenv("BRAIN_AUDIT_MAX_BYTES", "5242880"))
    audit_backup_count: int = int(os.getenv("BRAIN_AUDIT_BACKUPS", "3"))
    client_identity: str = os.getenv("BRAIN_CLIENT_IDENTITY", "local-library")
    audit_test_call: bool = os.getenv("BRAIN_AUDIT_TEST_CALL", "false").lower() in {"1","true","yes"}
    instance_id: str = os.getenv("BRAIN_INSTANCE", "legacy")
    artifact_repo_roots: dict[str,str] | None = None
    sensitive_workspace_floor: frozenset[str] = frozenset(x.strip() for x in os.getenv("BRAIN_SENSITIVE_WORKSPACES","sap-work").split(",") if x.strip())

    def __post_init__(self):
        if self.artifact_repo_roots is None:
            configured=os.getenv("BRAIN_REPO_ROOTS_JSON")
            roots=json.loads(configured) if configured else {"pavol-brain":str(Path(__file__).resolve().parents[1])}
            object.__setattr__(self,"artifact_repo_roots",roots)
