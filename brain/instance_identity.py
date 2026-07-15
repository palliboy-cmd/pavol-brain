"""Persisted per-database instance identity marker (Package 1, closes B2).

Journals get a dedicated ``brain_instance_identity`` singleton table, stamped
once by bootstrap (or the one-time backfill script for pre-existing live
journals) and never rewritten afterward. A journal configured as ``personal``
or ``work`` must carry a matching marker before any query runs against it;
there is no auto-repair path for journals (they are the source of truth).

Retrieval databases are fully derived and rebuildable (I12), so the projector
is allowed to stamp its own ``retrieval_embedding_meta['instance_id']`` key
the first time it writes to a genuinely empty retrieval database. A retrieval
database that already holds documents but carries no marker is treated the
same as a journal without one: refused, never silently adopted.

``legacy``/spike configurations predate the Personal/WORK split and stay
exempt by design — every function below is a no-op for any instance id other
than ``"personal"``/``"work"``.
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .errors import BrainError

JOURNAL_TABLE = "brain_instance_identity"
RETRIEVAL_MARKER_KEY = "instance_id"
MARKED_INSTANCES = ("personal", "work")


def journal_table_sql():
    return (
        f"CREATE TABLE IF NOT EXISTS {JOURNAL_TABLE} ("
        " singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
        " instance_id TEXT NOT NULL CHECK(instance_id IN ('personal','work')),"
        " created_at TEXT NOT NULL,"
        " source_digest TEXT NOT NULL)"
    )


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# --- journal-side marker -------------------------------------------------

def read_journal_marker(con):
    """Return the marker row as a dict, or ``None`` if the table/row is absent.

    Deliberately does not assume ``con.row_factory`` is ``sqlite3.Row`` — every
    enforcement call site opens its own connection with whatever row factory
    suits its own other queries (some positional, some by name), so this reads
    the three columns positionally, which works identically either way.
    """
    tables = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (JOURNAL_TABLE,))}
    if JOURNAL_TABLE not in tables:
        return None
    row = con.execute(
        f"SELECT instance_id, created_at, source_digest FROM {JOURNAL_TABLE} WHERE singleton=1").fetchone()
    if row is None:
        return None
    return {"instance_id": row[0], "created_at": row[1], "source_digest": row[2]}


def stamp_journal_marker(con, instance_id, source_digest, created_at=None):
    """Stamp a fresh journal's marker. Callers must ensure this runs at most once
    per physical file — the table has no update path, only ever one INSERT."""
    if instance_id not in MARKED_INSTANCES:
        raise ValueError(f"cannot stamp a journal for instance {instance_id!r}")
    con.execute(journal_table_sql())
    con.execute(
        f"INSERT INTO {JOURNAL_TABLE}(singleton, instance_id, created_at, source_digest) VALUES (1,?,?,?)",
        (instance_id, created_at or now_iso(), source_digest),
    )


def enforce_journal(con, instance_id, request_id=""):
    """Raise before any query if ``con``'s journal marker disagrees with ``instance_id``.

    No-op for ``legacy``/spike instances. Never stamps — a journal is the
    source of truth and a missing marker is always an operator-visible error,
    not something this call silently repairs.
    """
    if instance_id not in MARKED_INSTANCES:
        return None
    marker = read_journal_marker(con)
    if marker is None:
        raise BrainError("BRAIN_INSTANCE_MARKER_MISSING",
                          f"{instance_id} journal is missing its instance identity marker", request_id)
    if marker["instance_id"] != instance_id:
        raise BrainError("BRAIN_INSTANCE_MISMATCH",
                          "journal instance marker does not match the configured instance", request_id,
                          {"configured": instance_id, "marker": marker["instance_id"]})
    return marker


def marked_instance_at_path(path):
    """Read-only marker lookup by path, for callers (e.g. the Control DB gate)
    that only need to know whether a journal file exists and what it claims to
    be, without holding it open. Returns the marked instance id, or ``None``
    if the file, table, or row is absent."""
    candidate = Path(path)
    if not candidate.is_file():
        return None
    con = sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        marker = read_journal_marker(con)
        return marker["instance_id"] if marker else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


# --- retrieval-side marker ------------------------------------------------

def read_retrieval_marker(con):
    tables = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='retrieval_embedding_meta'")}
    if "retrieval_embedding_meta" not in tables:
        return None
    row = con.execute("SELECT value FROM retrieval_embedding_meta WHERE key=?", (RETRIEVAL_MARKER_KEY,)).fetchone()
    return row[0] if row else None


def stamp_retrieval_marker(con, instance_id):
    con.execute("INSERT OR REPLACE INTO retrieval_embedding_meta(key,value) VALUES (?,?)",
                (RETRIEVAL_MARKER_KEY, instance_id))


def _retrieval_status(con, instance_id):
    """Return ``(status, marker)`` where status is one of
    ``exempt``/``ok``/``empty``/``missing``/``mismatch``."""
    if instance_id not in MARKED_INSTANCES:
        return "exempt", None
    marker = read_retrieval_marker(con)
    if marker is None:
        doc_count = con.execute("SELECT count(*) FROM retrieval_documents").fetchone()[0]
        return ("empty", None) if doc_count == 0 else ("missing", None)
    return ("ok", marker) if marker == instance_id else ("mismatch", marker)


def enforce_retrieval(con, instance_id, request_id="", allow_stamp=False):
    """Raise before any query if the retrieval DB's marker disagrees with
    ``instance_id``. A genuinely empty (never-projected) retrieval database has
    no marker yet by construction; ``allow_stamp=True`` (the projector's own
    write path) stamps it there and then. A read-only caller
    (``allow_stamp=False``) treats that same empty state as "nothing to
    protect yet" and lets the caller proceed with zero rows — it can never
    write the stamp itself.
    """
    status, marker = _retrieval_status(con, instance_id)
    if status in ("exempt", "ok"):
        return marker
    if status == "empty":
        if allow_stamp:
            stamp_retrieval_marker(con, instance_id)
            # The marker is a structural fact about the physical file, not
            # part of any projection batch: commit it immediately so it
            # survives even if the caller's batch is a no-op or later rolls
            # back (Python's sqlite3 module leaves DML in an open
            # transaction that a bare connection close would discard).
            con.commit()
            return instance_id
        return None
    if status == "missing":
        raise BrainError("BRAIN_INSTANCE_MARKER_MISSING",
                          f"{instance_id} retrieval database is missing its instance identity marker", request_id)
    raise BrainError("BRAIN_INSTANCE_MISMATCH",
                      "retrieval database instance marker does not match the configured instance", request_id,
                      {"configured": instance_id, "marker": marker})


def diagnose_retrieval(con, instance_id):
    """Non-raising variant for the projector's read-only ``validate()`` issue list."""
    status, _ = _retrieval_status(con, instance_id)
    return {"missing": "retrieval_instance_marker_missing", "mismatch": "retrieval_instance_marker_mismatch"}.get(status)
