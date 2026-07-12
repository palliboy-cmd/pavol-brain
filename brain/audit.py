"""Privacy-preserving metadata audit for read-only brain operations."""
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler


class AuditLogger:
    def __init__(self, config):
        self.config = config
        self.logger = None
        if config.audit_log_path:
            config.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("brain.audit." + str(config.audit_log_path))
            logger.setLevel(logging.INFO); logger.propagate = False
            if not logger.handlers:
                logger.addHandler(RotatingFileHandler(config.audit_log_path, maxBytes=config.audit_max_bytes,
                                                       backupCount=config.audit_backup_count, encoding="utf-8"))
            self.logger = logger

    def write(self, operation, **fields):
        if not self.logger: return
        blocked = {"query", "payload", "content", "snippet", "conversation", "chain_of_thought"}
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), "operation": operation,
                 "client_identity": self.config.client_identity, "test_call": self.config.audit_test_call}
        event.update({k: v for k, v in fields.items() if k not in blocked and v is not None})
        self.logger.info(json.dumps(event, ensure_ascii=False, sort_keys=True, default=str))
