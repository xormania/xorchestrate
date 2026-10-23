"""Adapter contract, concurrency, and real persistence checks."""

import multiprocessing
import os
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.model import Conflict, OrchestrationError


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


class StorageContract:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = self.make_store()
        self.addCleanup(self.store.close)

    def test_create_replace_delete_and_stale_versions(self):
        with self.store.transaction() as tx:
            self.assertIsNone(tx.get("job", "missing"))
            first = tx.put("job", "one", {"value": 1})
            self.assertEqual(first.version, 1)
            with self.assertRaises(Conflict):
                tx.put("job", "one", {"value": 2})
            with self.assertRaises(Conflict):
                tx.put("job", "missing", {}, expected_version=1)
            changed = tx.put("job", "one", {"value": 2}, expected_version=first.version)
            self.assertEqual(changed.version, 2)
            with self.assertRaises(Conflict):
                tx.put("job", "one", {"value": 3}, expected_version=first.version)
            with self.assertRaises(Conflict):
                tx.delete("job", "one", expected_version=first.version)
            self.assertEqual(tx.get("job", "one"), changed)
            tx.delete("job", "one", expected_version=changed.version)
            with self.assertRaises(Conflict):
                tx.delete("job", "one", expected_version=changed.version)
            self.assertIsNone(tx.get("job", "one"))

    def test_inputs_and_returned_records_are_detached(self):
        original = {"nested": [{"value": 1}], "unicode": "\ud800"}
        with self.store.transaction() as tx:
            created = tx.put("job", "one", original)
            original["nested"][0]["value"] = 9
            created.data["nested"][0]["value"] = 8
            fetched = tx.get("job", "one")
            self.assertEqual(fetched.data["nested"][0]["value"], 1)
            fetched.data["nested"].append({"value": 7})
            listed = tx.list("job")
            listed[0].data["nested"][0]["value"] = 6
        with self.store.transaction() as tx:
            self.assertEqual(tx.get("job", "one").data["nested"], [{"value": 1}])

    def test_lists_are_sorted_and_scoped_to_kind(self):
        with self.store.transaction() as tx:
            tx.put("job", "z", {})
            tx.put("actor", "a", {})
            tx.put("job", "a", {})
            self.assertEqual([record.key for record in tx.list("job")], ["a", "z"])
            self.assertEqual(tx.list("missing"), [])

    def test_exception_rolls_back_record_and_event_bundle(self):
        with self.store.transaction() as tx:
            original = tx.put("job", "one", {"phase": "initial"})
            tx.emit({"phase": "initial"})
        for failure in (RuntimeError, KeyboardInterrupt):
            with self.assertRaises(failure):
                with self.store.transaction() as tx:
                    tx.put("job", "one", {"phase": "changed"}, original.version)
                    tx.put("job", "two", {})
                    tx.emit({"phase": "changed"})
                    raise failure()
            with self.store.transaction() as tx:
                self.assertEqual(tx.get("job", "one"), original)
                self.assertIsNone(tx.get("job", "two"))
                self.assertEqual(tx.events(), [{"phase": "initial", "seq": 1}])
        with self.store.transaction() as tx:
            tx.delete("job", "one", original.version)
            seq = tx.emit({"phase": "deleted"})
        with self.store.transaction() as tx:
            self.assertIsNone(tx.get("job", "one"))
            self.assertEqual(tx.events(after=1), [{"phase": "deleted", "seq": seq}])

    def test_events_are_detached_ordered_and_pageable(self):
        event = {"nested": ["original"], "seq": "untrusted input"}
        with self.store.transaction() as tx:
            first = tx.emit(event)
            event["nested"].append("changed")
            tx.emit({"kind": "second"})
            returned = tx.events()
            self.assertEqual(returned[0], {"nested": ["original"], "seq": first})
            returned[0]["nested"].append("changed")
        with self.store.transaction() as tx:
            third = tx.emit({"kind": "third"})
            self.assertEqual(tx.events(after=first, limit=1), [{"kind": "second", "seq": first + 1}])
            self.assertEqual(tx.events(after=first + 1), [{"kind": "third", "seq": third}])
            self.assertEqual(tx.events(limit=0), [])
            self.assertEqual(tx.events(after=third + 100), [])
            self.assertEqual(tx.events(limit=1)[0]["nested"], ["original"])

    def test_invalid_json_rejected_without_partial_writes(self):
        cycle = []
        cycle.append(cycle)
        invalid = [
            {"value": float("nan")},
            {"value": float("inf")},
            {"value": object()},
            {"value": (1, 2)},
            {1: "non-string key"},
            {"nested": [{False: "non-string key"}]},
            {"value": cycle},
            [],
            None,
        ]
        with self.store.transaction() as tx:
            for value in invalid:
                with self.subTest(value=repr(value)):
                    with self.assertRaises(OrchestrationError):
                        tx.put("job", "invalid", value)
                    with self.assertRaises(OrchestrationError):
                        tx.emit(value)
            self.assertIsNone(tx.get("job", "invalid"))
            self.assertEqual(tx.events(), [])

    def test_invalid_identifiers_versions_and_pagination(self):
        with self.store.transaction() as tx:
            for key in (None, "", 2, "\ud800"):
                with self.assertRaises(OrchestrationError):
                    tx.put("job", key, {})
            for version in (True, 0, -1, 1.5, "1"):
                with self.assertRaises(OrchestrationError):
                    tx.put("job", "one", {}, version)
                with self.assertRaises(OrchestrationError):
                    tx.delete("job", "one", version)
            for after, limit in ((-1, 1), (1, -1), (True, 1), (1, 1.5)):
                with self.assertRaises(OrchestrationError):
                    tx.events(after, limit)

    def test_transaction_lifetime_thread_ownership_and_nested_context(self):
        with self.store.transaction() as tx:
            with self.assertRaises(RuntimeError):
                with self.store.transaction():
                    self.fail("Nested transaction entered.")
            with self.assertRaises(RuntimeError):
                self.store.close()
            with ThreadPoolExecutor(max_workers=1) as pool:
                with self.assertRaises(RuntimeError):
                    pool.submit(tx.get, "job", "one").result(timeout=5)
            tx.put("job", "one", {})
        for operation in (
            lambda: tx.get("job", "one"),
            lambda: tx.list("job"),
            lambda: tx.put("job", "two", {}),
            lambda: tx.delete("job", "one", 1),
            lambda: tx.emit({}),
            lambda: tx.events(),
        ):
            with self.assertRaises(RuntimeError):
                operation()

    def test_close_is_idempotent_and_prevents_reuse(self):
        self.store.close()
        self.store.close()
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.fail("Closed store accepted transaction.")

    def test_concurrent_stale_snapshots_allow_exactly_one_winner(self):
        with self.store.transaction() as tx:
            tx.put("job", "one", {"winner": None})
        barrier = threading.Barrier(2, timeout=5)

        def replace(winner):
            with self.store.transaction() as tx:
                previous = tx.get("job", "one")
            barrier.wait()
            try:
                with self.store.transaction() as tx:
                    tx.put("job", "one", {"winner": winner}, previous.version)
                    tx.emit({"winner": winner})
                return winner
            except Conflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(replace, ["first", "second"]))
        winners = [result for result in results if result is not None]
        self.assertEqual(len(winners), 1)
        with self.store.transaction() as tx:
            self.assertEqual(tx.get("job", "one").data["winner"], winners[0])
            self.assertEqual(tx.events(), [{"winner": winners[0], "seq": 1}])

    def test_concurrent_transactional_updates_keep_events_consistent(self):
        with self.store.transaction() as tx:
            tx.put("counter", "one", {"count": 0})

        def increment(_):
            for _ in range(15):
                with self.store.transaction() as tx:
                    previous = tx.get("counter", "one")
                    count = previous.data["count"] + 1
                    tx.put("counter", "one", {"count": count}, previous.version)
                    tx.emit({"count": count})

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(increment, range(4)))
        with self.store.transaction() as tx:
            self.assertEqual(tx.get("counter", "one").data["count"], 60)
            self.assertEqual(tx.events(), [{"count": count, "seq": count} for count in range(1, 61)])


class MemoryStoreTests(StorageContract, unittest.TestCase):
    def make_store(self):
        return MemoryStore()


class SQLiteStoreTests(StorageContract, unittest.TestCase):
    def make_store(self):
        return SQLiteStore(str(Path(self.directory.name) / "state.db"))

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
        memory = SQLiteStore(":memory:")
        self.addCleanup(memory.close)
        with memory.transaction() as tx:
            tx.put("job", "one", {})
        with memory.transaction() as tx:
            self.assertIsNotNone(tx.get("job", "one"))

    def test_unreadable_database_has_transport_independent_error(self):
        path = Path(self.directory.name) / "not-a-database.db"
        path.write_bytes(b"This is not a SQLite database.")
        with self.assertRaises(OrchestrationError) as failure:
            SQLiteStore(str(path))
        self.assertEqual(failure.exception.code, "storage_error")
        self.assertEqual(failure.exception.details, {"retryable": False})

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_new_state_files_and_directories_are_private(self):
        parent = Path(self.directory.name) / "private" / "nested"
        path = parent / "state.db"
        store = SQLiteStore(str(path))
        self.addCleanup(store.close)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(parent.parent.stat().st_mode), 0o700)

    def test_invalid_configuration(self):
        for options in ({}, {"path": ""}, {"path": "x", "busy_timeout_ms": -1}, {"path": "x", "busy_timeout_ms": True}):
            with self.assertRaises(OrchestrationError):
                SQLiteStore(options)


if __name__ == "__main__":
    unittest.main()
