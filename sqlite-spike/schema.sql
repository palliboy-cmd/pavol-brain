PRAGMA foreign_keys = ON;
CREATE TABLE retrieval_documents (
  doc_id INTEGER PRIMARY KEY,
  record_id TEXT NOT NULL UNIQUE,
  workspace TEXT NOT NULL,
  type TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  status TEXT NOT NULL,
  valid_at TEXT NOT NULL,
  invalid_at TEXT,
  confidence REAL NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  artifacts_text TEXT NOT NULL DEFAULT '',
  canonical_text TEXT NOT NULL,
  projection_hash TEXT NOT NULL
);
CREATE INDEX retrieval_filter_idx ON retrieval_documents(workspace, sensitivity, status, type, valid_at);
CREATE VIRTUAL TABLE retrieval_fts USING fts5(title, body, artifacts_text, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE retrieval_embeddings (
  record_id TEXT PRIMARY KEY REFERENCES retrieval_documents(record_id),
  model_fingerprint TEXT NOT NULL,
  model_identifier TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  vector BLOB NOT NULL,
  vector_norm REAL NOT NULL,
  projection_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE retrieval_embedding_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE retrieval_builds (build_id TEXT PRIMARY KEY, status TEXT NOT NULL);
CREATE TABLE retrieval_active_build (singleton INTEGER PRIMARY KEY CHECK(singleton=1), build_id TEXT NOT NULL);
