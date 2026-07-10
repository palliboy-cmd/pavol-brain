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
