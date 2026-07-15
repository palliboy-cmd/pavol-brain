from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


class ProjectionStatus(str, Enum):
    HEALTHY = "HEALTHY"
    NO_CHANGES = "NO_CHANGES"
    REBUILD_REQUIRED = "REBUILD_REQUIRED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ProjectorConfig:
    journal_db_path: Path
    retrieval_db_path: Path
    embedding_model_fingerprint: str
    embedding_dimension: int
    embedding_model_identifier: str = "unknown"
    projection_schema_version: str = "v1"
    projector_version: str = "slice2-v1"
    # Package 1 (closes B2): "legacy" is the exempt default, matching
    # BrainConfig.instance_id's own default — instance-marker enforcement is a
    # no-op unless a caller explicitly declares "personal"/"work".
    instance_id: str = "legacy"


@dataclass
class ProjectionReport:
    status: ProjectionStatus
    cursor_before: str | None = None
    cursor_after: str | None = None
    journal_head: str | None = None
    events_seen: int = 0
    inserted: int = 0
    updated: int = 0
    removed: int = 0
    embeddings_created: int = 0
    embeddings_reused: int = 0
    links_added: int = 0
    links_removed: int = 0
    noops: int = 0
    details: dict = field(default_factory=dict)

    def as_dict(self):
        return {"status": self.status.value, **self.__dict__ | {"status": self.status.value}}


FailureInjector = Callable[[str], None]
