PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS memory_records (
 record_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL DEFAULT 1,
 type TEXT NOT NULL CHECK(type IN ('problem','analysis','decision','outcome','fact','correction','artifact_link','preference')),
 workspace TEXT NOT NULL, sensitivity TEXT NOT NULL CHECK(sensitivity IN ('normal','sensitive')),
 raw_input TEXT NOT NULL, payload TEXT NOT NULL, content_hash TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, agent_id TEXT NOT NULL,
 source_assertion TEXT NOT NULL, source_excerpt TEXT, source_ref TEXT, session_ref TEXT,
 confidence REAL NOT NULL, valid_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_ws_type ON memory_records(workspace,type);
CREATE TABLE IF NOT EXISTS memory_events (
 event_id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES memory_records(record_id),
 event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, actor TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_record ON memory_events(record_id,event_id);
CREATE TABLE IF NOT EXISTS record_state (
 record_id TEXT PRIMARY KEY REFERENCES memory_records(record_id), status TEXT NOT NULL,
 review TEXT NOT NULL, invalid_at TEXT, supersedes TEXT, superseded_by TEXT, change_reason TEXT,
 projection TEXT NOT NULL DEFAULT 'none', projection_error TEXT, projected_build TEXT, updated_event_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_links (
 record_id TEXT NOT NULL, artifact_uri TEXT NOT NULL, relation TEXT NOT NULL, confidence REAL NOT NULL,
 origin TEXT NOT NULL, verified_at TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(record_id,artifact_uri,relation)
);
CREATE TABLE IF NOT EXISTS graph_edges (
 edge_uuid TEXT PRIMARY KEY, record_id TEXT NOT NULL, origin TEXT NOT NULL, build_id TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projection_map (
 record_id TEXT NOT NULL, build_id TEXT NOT NULL, episode_uuid TEXT, PRIMARY KEY(record_id,build_id)
);
PRAGMA user_version=2;
