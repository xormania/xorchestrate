"""Built-in stores against the xcontracts StateStore kit, and SQLite persistence checks."""

import multiprocessing
import os
import stat
import tempfile
import unittest
from pathlib import Path

import pytest
from xcontracts.testing import state_store

from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.model import OrchestrationError


def _increment_process(path, iterations, ready, start):
    store = SQLiteStore({"path": path, "busy_timeout_ms": 10000})
    try:
        ready.put(True)
        if not start.wait(timeout=15):
            raise RuntimeError("Other writer processes did not start.")
        for _ in range(iterations):
            with store.transaction() as tx:
                record = tx.get("counter", "shared")
                count = record.data["count"] + 1
                tx.put("counter", "shared", {"count": count}, record.version)
                tx.emit({"count": count})
    finally:
        store.close()


@pytest.mark.parametrize("check", state_store.CHECKS, ids=str)
@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_built_in_stores_pass_the_xcontracts_state_store_kit(adapter, check, tmp_path):
    """The shared StateStore contract now lives in xcontracts' conformance kit."""
    if adapter == "memory":
        check(MemoryStore)
    else:
        check(lambda: SQLiteStore({"path": str(tmp_path / "state.db")}))


@pytest.mark.parametrize("adapter", ["memory", "sqlite"])
def test_built_in_stores_round_trip_lone_surrogates_in_data(adapter, tmp_path):
    """Beyond the port contract: the built-in stores keep any Python string, even an unpaired surrogate."""
    store = MemoryStore() if adapter == "memory" else SQLiteStore({"path": str(tmp_path / "state.db")})
    try:
        with store.transaction() as tx:
            tx.put("job", "one", {"text": "\ud800"})
        with store.transaction() as tx:
            assert tx.get("job", "one").data == {"text": "\ud800"}
    finally:
        store.close()


class SQLiteStoreTests(unittest.TestCase):
    """SQLite persistence beyond the port contract: restarts, processes, files and options."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SQLiteStore({"path": str(Path(self.directory.name) / "state.db")})
        self.addCleanup(self.store.close)

    def test_restart_keeps_records_versions_and_event_sequence(self):
        path = str(Path(self.directory.name) / "state.db")
        with self.store.transaction() as tx:
            first = tx.put("job", "one", {"phase": "initial"})
            changed = tx.put("job", "one", {"phase": "changed"}, first.version)
            tx.emit({"phase": "changed"})
        self.store.close()
        restarted = SQLiteStore({"path": path})
        self.addCleanup(restarted.close)
        with restarted.transaction() as tx:
            self.assertEqual(tx.get("job", "one"), changed)
            self.assertEqual(tx.events(), [{"phase": "changed", "seq": 1}])
            self.assertEqual(tx.emit({"phase": "after restart"}), 2)

    def test_separate_connections_see_only_committed_bundles(self):
        path = str(Path(self.directory.name) / "state.db")
        other = SQLiteStore({"path": path, "busy_timeout_ms": 25})
        self.addCleanup(other.close)
        with self.store.transaction() as tx:
            tx.put("job", "uncommitted", {})
            tx.emit({"kind": "uncommitted"})
            # BEGIN IMMEDIATE serializes reads and writes consistently. A second
            # writer cannot enter until the first bundle has committed.
            with self.assertRaises(OrchestrationError) as busy:
                with other.transaction():
                    self.fail("Second writer entered an uncommitted transaction.")
            self.assertEqual(busy.exception.code, "storage_busy")
            self.assertEqual(busy.exception.details, {"retryable": True})
        with other.transaction() as tx:
            self.assertIsNotNone(tx.get("job", "uncommitted"))
            self.assertEqual(tx.events(), [{"kind": "uncommitted", "seq": 1}])

    def test_multiple_processes_commit_atomic_counter_and_event_updates(self):
        path = str(Path(self.directory.name) / "state.db")
        with self.store.transaction() as tx:
            tx.put("counter", "shared", {"count": 0})
        context = multiprocessing.get_context("spawn")
        ready = context.Queue()
        start = context.Event()
        processes = [context.Process(target=_increment_process, args=(path, 12, ready, start)) for _ in range(3)]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                self.assertTrue(ready.get(timeout=15))
            start.set()
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=5)
            ready.close()
            ready.join_thread()
        with self.store.transaction() as tx:
            self.assertEqual(tx.get("counter", "shared").data["count"], 36)
            self.assertEqual(tx.events(), [{"count": count, "seq": count} for count in range(1, 37)])

    def test_memory_database_lives_until_its_store_closes(self):
        memory = SQLiteStore({"path": ":memory:"})
        self.addCleanup(memory.close)
        with memory.transaction() as tx:
            tx.put("job", "one", {})
        with memory.transaction() as tx:
            self.assertIsNotNone(tx.get("job", "one"))

    def test_unreadable_database_has_transport_independent_error(self):
        path = Path(self.directory.name) / "not-a-database.db"
        path.write_bytes(b"This is not a SQLite database.")
        with self.assertRaises(OrchestrationError) as failure:
            SQLiteStore({"path": str(path)})
        self.assertEqual(failure.exception.code, "storage_error")
        self.assertEqual(failure.exception.details, {"retryable": False})

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_new_state_files_and_directories_are_private(self):
        parent = Path(self.directory.name) / "private" / "nested"
        path = parent / "state.db"
        store = SQLiteStore({"path": str(path)})
        self.addCleanup(store.close)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(parent.parent.stat().st_mode), 0o700)

    def test_invalid_configuration(self):
        for options in ("state.db", {}, {"path": ""}, {"path": "x", "busy_timeout_ms": -1}, {"path": "x", "busy_timeout_ms": True}):
            with self.assertRaises(OrchestrationError):
                SQLiteStore(options)


if __name__ == "__main__":
    unittest.main()
