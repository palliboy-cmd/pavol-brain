"""Regression coverage: SQLite connections must be explicitly closed, not left
for garbage collection. A leaked connection per call is invisible in a short
test but exhausts file descriptors in a long-running production process
(especially under Python 3.14's more deferred incremental GC combined with a
constrained ulimit -n, as observed on mini-core). Each test below repeats an
operation many times and asserts the process's open file-descriptor count
stays bounded rather than growing linearly with the call count.
"""
import gc
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "sqlite-spike" / "scripts")); sys.path.insert(0, str(ROOT / "tests"))

from brain.config import BrainConfig
from brain.control import ControlStore, IntegrationProfile
from brain.models import SearchRequest
from brain.projector import ProjectorConfig, ProjectionProjector
from brain.projector.journal_reader import JournalReader
from brain.repository import Repository
from brain.runtime import RuntimeInspector
from journal_fixture import journal_fixture

REPEATS = 40
# A correctly-closing implementation holds a small, constant number of file
# descriptors regardless of call count. A per-call leak grows by ~REPEATS.
# The bound is generous to avoid flakiness from unrelated fds (stdio, sockets)
# while still catching a real linear leak with a wide margin.
FD_GROWTH_BOUND = 15


def open_fd_count():
    return len(os.listdir("/dev/fd"))


class FakeEmbedder:
    def __init__(self, dimension=4): self.dimension = dimension
    def embed_document(self, text): return [1.0, 2.0, 3.0, 4.0], "fake-embedder"


class ConnectionHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.journal = self.root / "journal.db"; journal_fixture(self.journal)
        self.retrieval = self.root / "retrieval.db"; self.control_db = self.root / "control.db"

    def tearDown(self):
        gc.collect()
        self.tmp.cleanup()

    def assert_fds_bounded(self, before, label):
        gc.collect()
        after = open_fd_count()
        growth = after - before
        self.assertLess(growth, FD_GROWTH_BOUND,
                         f"{label}: fd count grew by {growth} over {REPEATS} calls "
                         f"(before={before}, after={after}) - looks like a per-call leak")

    def test_journal_reader_snapshot_repeated(self):
        reader = JournalReader(self.journal)
        before = open_fd_count()
        for _ in range(REPEATS):
            reader.snapshot("rec-001")
        self.assert_fds_bounded(before, "JournalReader.snapshot")

    def test_journal_reader_events_after_and_audit_repeated(self):
        reader = JournalReader(self.journal)
        before = open_fd_count()
        for _ in range(REPEATS):
            reader.events_after(None, 10)
            reader.audit()
        self.assert_fds_bounded(before, "JournalReader.events_after/audit")

    def test_control_store_get_repeated(self):
        store = ControlStore(self.control_db)
        before = open_fd_count()
        for _ in range(REPEATS):
            store.get("nonexistent-integration")
        self.assert_fds_bounded(before, "ControlStore.get")

    def test_control_store_save_repeated(self):
        store = ControlStore(self.control_db)
        before = open_fd_count()
        for i in range(REPEATS):
            store.save(IntegrationProfile(
                integration_id=f"agent-{i}", display_name="Agent", client_type="claude",
                transport="ssh_stdio", host="mini", enabled=True, allowed_workspaces=["personal"],
                sensitive_workspace_grants=[], allowed_tools=["brain_search"], client_identity="agent",
                brain_instance="personal"))
        self.assert_fds_bounded(before, "ControlStore.save")

    def test_repository_reads_repeated(self):
        config = ProjectorConfig(self.journal, self.retrieval, "fake-fingerprint", 4, "fake-embedder")
        projector = ProjectionProjector(config, FakeEmbedder())
        while projector.run_once(100).status.name == "HEALTHY":
            pass
        repo = Repository(BrainConfig(journal_db_path=self.journal, retrieval_db_path=self.retrieval, embedding_dimension=4))
        request = SearchRequest(query="test", workspaces=["ai-pos", "personal", "sap-work"], limit=10)
        before = open_fd_count()
        for _ in range(REPEATS):
            repo.candidates(request)
            repo.journal_row("rec-001")
            repo.related("rec-001")
        self.assert_fds_bounded(before, "Repository.candidates/journal_row/related")

    def test_runtime_inspect_repeated(self):
        config = BrainConfig(journal_db_path=self.journal, retrieval_db_path=self.retrieval, embedding_dimension=4)
        inspector = RuntimeInspector(config, meta=lambda: {})
        before = open_fd_count()
        for _ in range(REPEATS):
            inspector.inspect()
        self.assert_fds_bounded(before, "RuntimeInspector.inspect")

    def test_projector_run_once_success_path_repeated(self):
        config = ProjectorConfig(self.journal, self.retrieval, "fake-fingerprint", 4, "fake-embedder")
        projector = ProjectionProjector(config, FakeEmbedder())
        before = open_fd_count()
        for _ in range(REPEATS):
            projector.run_once(100)
        self.assert_fds_bounded(before, "ProjectionProjector.run_once (success/no-op path)")

    def test_projector_run_once_failure_path_repeated(self):
        """Each iteration forces a rollback via a failure injector, then retries
        successfully - mirrors tests/test_brain_projector.py's
        test_failure_points_roll_back_and_retry, repeated enough times to
        surface a per-call leak that a single pass would miss."""
        before = open_fd_count()
        for i in range(REPEATS):
            tmp_retrieval = self.root / f"retrieval-{i}.db"
            config = ProjectorConfig(self.journal, tmp_retrieval, "fake-fingerprint", 4, "fake-embedder")

            def fail(got):
                if got == "after_documents":
                    raise RuntimeError("forced failure")

            bad = ProjectionProjector(config, FakeEmbedder(), fail)
            with self.assertRaises(Exception):
                bad.run_once()
            good = ProjectionProjector(config, FakeEmbedder())
            good.run_once()
        self.assert_fds_bounded(before, "ProjectionProjector.run_once (failure + retry path)")

    def test_status_and_plan_repeated(self):
        config = ProjectorConfig(self.journal, self.retrieval, "fake-fingerprint", 4, "fake-embedder")
        projector = ProjectionProjector(config, FakeEmbedder())
        projector.run_once(100)
        before = open_fd_count()
        for _ in range(REPEATS):
            projector.status()
            projector.plan()
        self.assert_fds_bounded(before, "ProjectionProjector.status/plan")


if __name__ == "__main__":
    unittest.main()
