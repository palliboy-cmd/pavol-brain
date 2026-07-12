def get_cursor(con):
    row = con.execute("SELECT * FROM retrieval_projection_cursor WHERE singleton=1").fetchone()
    return dict(row) if row else None


def set_cursor(con, source_event_id, config):
    con.execute("""INSERT INTO retrieval_projection_cursor(singleton,last_source_event_id,last_projected_at,
                 projection_schema_version,embedding_model_fingerprint,embedding_dimension,projector_version)
                 VALUES (1,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),?,?,?,?)
                 ON CONFLICT(singleton) DO UPDATE SET last_source_event_id=excluded.last_source_event_id,
                 last_projected_at=excluded.last_projected_at, projection_schema_version=excluded.projection_schema_version,
                 embedding_model_fingerprint=excluded.embedding_model_fingerprint,
                 embedding_dimension=excluded.embedding_dimension, projector_version=excluded.projector_version""",
                (source_event_id, config.projection_schema_version, config.embedding_model_fingerprint, config.embedding_dimension, config.projector_version))
