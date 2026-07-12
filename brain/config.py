from dataclasses import dataclass
from pathlib import Path
import os

@dataclass(frozen=True)
class BrainConfig:
    journal_db_path: Path = Path(os.getenv("BRAIN_JOURNAL_DB", "spike/spike.db"))
    retrieval_db_path: Path = Path(os.getenv("BRAIN_RETRIEVAL_DB", "sqlite-spike/retrieval.db"))
    embedding_base_url: str = os.getenv("BRAIN_EMBEDDING_BASE_URL", "http://localhost:11434/v1")
    embedding_model: str = os.getenv("BRAIN_EMBEDDING_MODEL", "nomic-embed-text:latest")
    embedding_dimension: int | None = int(os.getenv("BRAIN_EMBEDDING_DIMENSION", "768"))
    timeout: float = float(os.getenv("BRAIN_EMBEDDING_TIMEOUT", "60"))
    schema_version: str = "v1"
