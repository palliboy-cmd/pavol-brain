-- M1 schema v2 migration. Run only through scripts/migrate_brain_m1.py,
-- which performs preflight, backup, transaction, FK/integrity checks and rollback.
BEGIN IMMEDIATE;
CREATE TABLE memory_records_m1 (
 record_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL DEFAULT 1,
 type TEXT NOT NULL CHECK(type IN ('problem','analysis','decision','outcome','fact','correction','artifact_link','preference')),
 workspace TEXT NOT NULL, sensitivity TEXT NOT NULL CHECK(sensitivity IN ('normal','sensitive')),
 raw_input TEXT NOT NULL, payload TEXT NOT NULL, content_hash TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, agent_id TEXT NOT NULL,
 source_assertion TEXT NOT NULL, source_excerpt TEXT, source_ref TEXT, session_ref TEXT,
 confidence REAL NOT NULL, valid_at TEXT NOT NULL, created_at TEXT NOT NULL
);
INSERT INTO memory_records_m1 SELECT * FROM memory_records;
DROP TABLE memory_records;
ALTER TABLE memory_records_m1 RENAME TO memory_records;
CREATE INDEX idx_records_ws_type ON memory_records(workspace,type);
PRAGMA user_version=2;
COMMIT;
