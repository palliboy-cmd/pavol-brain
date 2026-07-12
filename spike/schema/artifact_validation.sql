-- Additive canonical artifact-relation validation model (Proposal 008).
-- Append-only events are authority; artifact_validation_state is a reproducible fold.
CREATE TABLE IF NOT EXISTS artifact_validation_events (
 event_id TEXT PRIMARY KEY,
 artifact_link_id TEXT NOT NULL,
 artifact_record_id TEXT NOT NULL REFERENCES memory_records(record_id),
 artifact_uri TEXT NOT NULL,
 relation TEXT NOT NULL,
 occurred_at TEXT NOT NULL,
 effective_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('unknown','verified_active','verified_inactive')),
 reason_code TEXT NOT NULL CHECK(reason_code IN ('manual_verified','wrong_target','malformed_uri','duplicate','superseded','intentionally_retired','migrated_from_baseline_review','other')),
 actor TEXT NOT NULL,
 source TEXT NOT NULL,
 evidence TEXT NOT NULL DEFAULT '{}',
 note TEXT,
 idempotency_key TEXT NOT NULL UNIQUE,
 supersedes_validation_event_id TEXT REFERENCES artifact_validation_events(event_id)
);
CREATE INDEX IF NOT EXISTS artifact_validation_events_link_time
 ON artifact_validation_events(artifact_link_id,effective_at,event_id);
CREATE TABLE IF NOT EXISTS artifact_validation_state (
 artifact_link_id TEXT PRIMARY KEY,
 artifact_record_id TEXT NOT NULL REFERENCES memory_records(record_id),
 current_state TEXT NOT NULL,
 reason_code TEXT NOT NULL,
 effective_at TEXT NOT NULL,
 last_event_id TEXT NOT NULL REFERENCES artifact_validation_events(event_id),
 validated_by TEXT NOT NULL,
 evidence_reference TEXT NOT NULL
);
