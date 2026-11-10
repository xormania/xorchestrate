"""Durable state declares its data version and is never migrated on load."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pytest

import xorchestrate
import xorchestrate.adapters.storage as storage_module
from tests.hexagonal.support.application import application
from tests.hexagonal.support.store_fixtures import copy_fixture, raw_state, write_pre_versioned_store
from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.adapters.storage_migrations import DATA_VERSION, REGISTRY, MigrationRegistry
from xorchestrate.model import Conflict, OrchestrationError

RELEASE = "v" + xorchestrate.__version__
PRE_VERSIONED_RECORDS = (
    ("work", "w1", json.dumps({"goal": "Pre-versioned goal", "status": "open"}), 3),
    ("actor", "a1", json.dumps({"roles": []}), 1),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(path, user_version, written_by=None):
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(f"PRAGMA user_version = {user_version}")
        if written_by is not None:
            connection.execute("INSERT OR REPLACE INTO meta (key, data) VALUES ('data', ?)",
                               (json.dumps({"data_version": user_version, "written_by": written_by,
                                            "migrated_at": "2030-01-01T00:00:00+00:00"}),))
    finally:
        connection.close()


def open_store(path, **kwargs):
    return SQLiteStore({"path": str(path)}, **kwargs)


def data_migrated_events(store):
    with store.transaction() as tx:
        return [event for event in tx.events(limit=1000) if event.get("type") == "data_migrated"]


class Steps:
    """A test-only chain 1 -> 2 -> 3 recording the order its steps run in."""

    def __init__(self, fail_at=None, counts=None):
        self.ran, self.fail_at, self.counts = [], fail_at, counts

    def step(self, source):
        def run(tx):
            self.ran.append(source)
            changed = 0
            for record in tx.list("work"):
                tx.put("work", record.key, {**record.data, "migrations": [*record.data.get("migrations", []), source + 1]},
                       record.version)
                changed += 1
            if self.fail_at == source:
                raise RuntimeError("Planted migration failure after writing records.")
            return self.counts if self.counts is not None else {"work": changed}
        return run

    def registry(self):
        return MigrationRegistry(3, {1: self.step(1), 2: self.step(2)})


def test_new_store_stamps_version_and_meta_outside_application_records(tmp_path):
    path = tmp_path / "state.db"
    store = open_store(path)
    try:
        assert store.data_version == DATA_VERSION == REGISTRY.current
        with store.transaction() as tx:
            assert tx.list("meta") == [] and tx.get("meta", "data") is None
            assert tx.events() == []
    finally:
        store.close()
    state = raw_state(path)
    assert state["user_version"] == DATA_VERSION
    [(key, data)] = state["meta"]
    meta = json.loads(data)
    assert key == "data" and meta["data_version"] == DATA_VERSION and meta["written_by"] == RELEASE
    assert datetime.fromisoformat(meta["migrated_at"]).tzinfo is not None
    assert {"records", "events", "meta"} <= state["tables"]


def test_memory_store_reports_the_current_version():
    store = MemoryStore()
    assert store.data_version == DATA_VERSION
    assert store.data_version_info()["status"] == "current"


def test_pre_versioned_store_reads_as_version_one_without_writing(tmp_path):
    path = write_pre_versioned_store(tmp_path / "state.db", PRE_VERSIONED_RECORDS, ['{"type":"old"}'])
    before = digest(path)
    store = open_store(path)
    try:
        info = store.data_version_info()
        assert (store.data_version, info["pre_versioned"], info["status"]) == (1, True, "current")
        with store.transaction() as tx:
            assert tx.get("work", "w1").version == 3
    finally:
        store.close()
    assert digest(path) == before
    assert raw_state(path)["user_version"] == 0 and "meta" not in raw_state(path)["tables"]


def test_pre_versioned_store_is_stamped_only_when_migrating(tmp_path):
    path = write_pre_versioned_store(tmp_path / "state.db", PRE_VERSIONED_RECORDS, ['{"type":"old"}'])
    store = open_store(path, migrate=True)
    try:
        assert store.data_version == 1 and not store.data_version_info()["pre_versioned"]
        with store.transaction() as tx:
            assert [(r.key, r.version) for r in tx.list("work")] == [("w1", 3)]
            assert tx.events() == [{"type": "old", "seq": 1}]
    finally:
        store.close()
    state = raw_state(path)
    assert state["user_version"] == 1
    assert json.loads(state["meta"][0][1])["written_by"] == RELEASE


def test_newer_store_is_refused_naming_the_release_that_wrote_it(tmp_path):
    path = copy_fixture(1, tmp_path / "state.db")
    stamp(path, DATA_VERSION + 1, "v9.9.9")
    before = digest(path)
    for kwargs in ({}, {"migrate": True}):
        with pytest.raises(OrchestrationError) as refused:
            open_store(path, **kwargs)
        assert refused.value.code == "data_version_unsupported"
        assert refused.value.details == {"stored": DATA_VERSION + 1, "current": DATA_VERSION,
                                         "written_by": "v9.9.9", "release": RELEASE}
        assert "v9.9.9" in str(refused.value)
    assert digest(path) == before
    reader = open_store(path, read_only=True)
    try:
        assert reader.data_version_info()["status"] == "unsupported"
    finally:
        reader.close()


def test_older_store_is_refused_without_migration(tmp_path):
    path = copy_fixture(1, tmp_path / "state.db")
    steps = Steps()
    before = digest(path)
    with pytest.raises(OrchestrationError) as refused:
        open_store(path, migrations=steps.registry())
    assert refused.value.code == "data_migration_required"
    assert refused.value.details == {"stored": 1, "current": 3, "release": RELEASE}
    assert steps.ran == [] and digest(path) == before


def test_migration_chain_runs_in_order_and_retains_events(tmp_path):
    path = copy_fixture(1, tmp_path / "state.db")
    steps = Steps()
    store = open_store(path, migrate=True, migrations=steps.registry())
    try:
        assert steps.ran == [1, 2] and store.data_version == 3
        events = data_migrated_events(store)
        assert [(e["from"], e["to"], e["release"], e["counts"]) for e in events] == [
            (1, 2, RELEASE, {"work": 2}), (2, 3, RELEASE, {"work": 2})]
        with store.transaction() as tx:
            assert [(r.data["migrations"], r.version) for r in tx.list("work")] == [([2, 3], 3), ([2, 3], 3)]
    finally:
        store.close()
    assert raw_state(path)["user_version"] == 3
    # A normal open of the migrated store proceeds and the application reads it.
    reopened = open_store(path, migrations=steps.registry())
    try:
        work = application(reopened).call("list_work", {})["work"]
        assert sorted(item["goal"] for item in work) == [
            "Fixture work retained across data versions", "Second fixture work"]
        assert all(item["migrations"] == [2, 3] for item in work)
    finally:
        reopened.close()
    assert steps.ran == [1, 2]


def test_failed_step_rolls_back_and_a_later_migration_resumes(tmp_path):
    path = copy_fixture(1, tmp_path / "state.db")
    failing = Steps(fail_at=2)
    with pytest.raises(OrchestrationError) as failure:
        open_store(path, migrate=True, migrations=failing.registry())
    assert failure.value.code == "data_migration_failed"
    assert failure.value.details == {"from": 2, "to": 3, "release": RELEASE}
    assert raw_state(path)["user_version"] == 2
    resumed = Steps()
    store = open_store(path, migrate=True, migrations=resumed.registry())
    try:
        assert resumed.ran == [2]
        assert [(e["from"], e["to"]) for e in data_migrated_events(store)] == [(1, 2), (2, 3)]
        with store.transaction() as tx:
            assert [r.data["migrations"] for r in tx.list("work")] == [[2, 3], [2, 3]]
    finally:
        store.close()


def test_invalid_step_counts_roll_back_the_step(tmp_path):
    path = copy_fixture(1, tmp_path / "state.db")
    with pytest.raises(OrchestrationError) as failure:
        open_store(path, migrate=True, migrations=Steps(counts={"work": -1}).registry())
    assert failure.value.code == "data_migration_failed"
    assert raw_state(path)["user_version"] == 1
    reader = open_store(path, read_only=True)
    try:
        with reader.transaction() as tx:
            assert all("migrations" not in r.data for r in tx.list("work"))
    finally:
        reader.close()


def test_registry_requires_a_contiguous_chain_and_reports_unreachable_versions():
    def step(tx):
        return {}

    for current, steps in ((3, {1: step}), (3, {3: step}), (0, {}), (2, {1: "not callable"})):
        with pytest.raises(ValueError):
            MigrationRegistry(current, steps)
    registry = MigrationRegistry(4, {2: step, 3: step})
    assert [source for source, _ in registry.chain(2)] == [2, 3] and registry.chain(4) == []
    with pytest.raises(OrchestrationError) as unavailable:
        registry.chain(1)
    assert unavailable.value.code == "data_migration_unavailable"


def test_read_only_open_reports_the_version_without_writing_or_locking(tmp_path):
    path = write_pre_versioned_store(tmp_path / "state.db", PRE_VERSIONED_RECORDS)
    before = digest(path)
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")  # holds the write lock a normal open would need
    try:
        reader = SQLiteStore({"path": str(path), "busy_timeout_ms": 0}, read_only=True,
                             migrations=Steps().registry())
        try:
            info = reader.data_version_info()
            assert (info["data_version"], info["status"], info["pre_versioned"], info["read_only"]) == (
                1, "migration_required", True, True)
            assert info["pending_steps"] == [[1, 2], [2, 3]]
            with reader.transaction() as tx:
                assert tx.get("work", "w1").data["goal"] == "Pre-versioned goal"
                for write in (lambda: tx.put("work", "w2", {}), lambda: tx.delete("work", "w1", 3),
                              lambda: tx.emit({"type": "x"})):
                    with pytest.raises(OrchestrationError) as refused:
                        write()
                    assert refused.value.code == "storage_read_only"
        finally:
            reader.close()
    finally:
        writer.rollback()
        writer.close()
    assert digest(path) == before
    assert raw_state(path)["user_version"] == 0 and "meta" not in raw_state(path)["tables"]


def test_read_only_open_requires_an_existing_file_and_excludes_migration(tmp_path):
    missing = tmp_path / "missing.db"
    for options in ({"path": str(missing)}, {"path": ":memory:"}):
        with pytest.raises(OrchestrationError) as refused:
            SQLiteStore(options, read_only=True)
        assert refused.value.code == "storage_error"
    assert not missing.exists()
    with pytest.raises(OrchestrationError):
        open_store(tmp_path / "state.db", read_only=True, migrate=True)


def test_committed_fixture_store_is_read_by_a_normal_open(tmp_path):
    path = copy_fixture(1, tmp_path / "state.db")
    before = digest(path)
    store = open_store(path)
    try:
        info = store.data_version_info()
        assert (info["data_version"], info["status"], info["pre_versioned"]) == (1, "current", False)
        assert info["written_by"].startswith("v")
        app = application(store)
        assert sorted(item["goal"] for item in app.call("list_work", {})["work"]) == [
            "Fixture work retained across data versions", "Second fixture work"]
        assert [actor["id"] for actor in app.call("list_actors", {})["actors"]] == ["fixture-actor"]
        with store.transaction() as tx:
            assert [event for event in tx.events(limit=1000) if event.get("type") == "data_migrated"] == []
    finally:
        store.close()
    assert digest(path) == before
    # Explicit migration of a current store is a no-op.
    migrated = open_store(path, migrate=True)
    try:
        assert data_migrated_events(migrated) == [] and migrated.data_version == 1
    finally:
        migrated.close()
    assert digest(path) == before


def foreign_file(path, user_version):
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("CREATE TABLE things (x)")
        connection.execute(f"PRAGMA user_version = {user_version}")
    finally:
        connection.close()
    return path


@pytest.mark.parametrize(("user_version", "code", "details"), [
    (DATA_VERSION + 4, "data_version_unsupported",
     {"stored": DATA_VERSION + 4, "current": DATA_VERSION, "written_by": None, "release": RELEASE}),
    (DATA_VERSION, "storage_unrecognized", {"user_version": DATA_VERSION, "retryable": False}),
])
def test_versioned_file_without_store_schema_is_refused_unchanged(tmp_path, user_version, code, details):
    path = foreign_file(tmp_path / "foreign.db", user_version)
    before = digest(path)
    for kwargs in ({}, {"migrate": True}):
        with pytest.raises(OrchestrationError) as refused:
            open_store(path, **kwargs)
        assert (refused.value.code, refused.value.details) == (code, details)
    assert digest(path) == before
    assert raw_state(path)["tables"] == {"things"} and raw_state(path)["user_version"] == user_version
    reader = open_store(path, read_only=True)
    try:
        assert reader.data_version_info()["status"] == ("unsupported" if code == "data_version_unsupported"
                                                         else "unrecognized")
    finally:
        reader.close()


def test_orchestration_errors_from_a_step_are_failed_migrations(tmp_path):
    path = copy_fixture(1, tmp_path / "state.db")

    def conflicting(tx):
        record = tx.list("work")[0]
        tx.put("work", record.key, {**record.data, "changed": True}, record.version)
        tx.put("work", record.key, record.data, record.version)  # stale version: Conflict
        return {"work": 1}

    with pytest.raises(OrchestrationError) as failure:
        open_store(path, migrate=True, migrations=MigrationRegistry(2, {1: conflicting}))
    assert failure.value.code == "data_migration_failed"
    assert failure.value.details == {"from": 1, "to": 2, "release": RELEASE}
    assert type(failure.value.__cause__) is Conflict
    assert raw_state(path)["user_version"] == 1
    reader = open_store(path, read_only=True)
    try:
        with reader.transaction() as tx:
            assert all("changed" not in r.data for r in tx.list("work"))
    finally:
        reader.close()


_READ_ONLY_PROBE = """
import json, sys
from xorchestrate.adapters.storage import SQLiteStore
import xorchestrate.adapters.storage as storage
from xorchestrate.model import OrchestrationError
sys.stderr.write(__import__("os").path.realpath(storage.__file__))
try:
    SQLiteStore({"path": sys.argv[1]}, read_only=True).close()
    print(json.dumps({"opened": True}))
except OrchestrationError as error:
    print(json.dumps({"code": error.code, "message": str(error)}))
"""


def test_read_only_open_needs_a_writable_directory_for_sqlite_sidecars(tmp_path):
    directory = Path(tempfile.mkdtemp(prefix="xorchestrate-ro-"))
    try:
        path = directory / "state.db"
        open_store(path).close()
        assert sorted(item.name for item in directory.iterdir()) == ["state.db"]
        before = digest(path)
        # Writable directory: SQLite may leave its -wal/-shm sidecars; the database is unchanged.
        reader = open_store(path, read_only=True)
        reader.close()
        assert set(item.name for item in directory.iterdir()) <= {"state.db", "state.db-wal", "state.db-shm"}
        assert digest(path) == before
        for sidecar in ("state.db-wal", "state.db-shm"):
            (directory / sidecar).unlink(missing_ok=True)
        os.chmod(path, 0o644)
        os.chmod(directory, 0o555)  # noqa: S103 - read-only, traversable by the unprivileged probe user
        command = [sys.executable, "-c", _READ_ONLY_PROBE, str(path)]
        if os.geteuid() == 0:
            if shutil.which("setpriv") is None:
                pytest.skip("Root cannot be denied directory writes without setpriv")
            command = ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", *command]
        root = str(Path(xorchestrate.__file__).resolve().parents[1])
        result = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                env={**os.environ, "PYTHONPATH": root, "PYTHONPYCACHEPREFIX": str(directory / "x")}, cwd="/")
        # The unprivileged probe must exercise this checkout, not another installation.
        assert result.stderr == str(Path(storage_module.__file__).resolve()), result.stderr
        assert json.loads(result.stdout) == {
            "code": "storage_read_only_unavailable",
            "message": "Read-only access to this state store needs write permission on its directory, where SQLite "
                       "creates the store's -wal and -shm files; the database file itself is never written.",
        }, result.stderr
        assert digest(path) == before
    finally:
        os.chmod(directory, 0o700)
        shutil.rmtree(directory)


class _InterleavingConnection:
    """Delegates to a real connection; runs ``hook`` once after the first schema query."""

    def __init__(self, connection, hook):
        self._connection, self._hook = connection, hook

    def execute(self, sql, *args):
        result = self._connection.execute(sql, *args)
        if self._hook is not None and "sqlite_master" in sql:
            hook, self._hook = self._hook, None
            result = result.fetchall()
            hook()
        return result

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_concurrent_initializer_between_metadata_reads_is_not_unrecognized(tmp_path, monkeypatch):
    """Another process may initialize a fresh file between the schema and version reads."""
    path = tmp_path / "state.db"
    connect = sqlite3.connect
    other = {}

    def initialize_elsewhere():
        try:
            other["store"] = SQLiteStore({"path": str(path)})
        except Exception as error:  # surfaced by the assertions below
            other["error"] = error

    def interleave():
        # The second initializer runs between the first open's two metadata
        # reads. When those reads share one snapshot it waits for the snapshot
        # to end, so wait only briefly here and let it finish afterwards.
        worker = threading.Thread(target=initialize_elsewhere)
        worker.start()
        worker.join(timeout=1)
        other["worker"] = worker

    calls = []

    def first_connection_interleaves(*args, **kwargs):
        connection = connect(*args, **kwargs)
        calls.append(args)
        return _InterleavingConnection(connection, interleave) if len(calls) == 1 else connection

    monkeypatch.setattr(sqlite3, "connect", first_connection_interleaves)
    store = open_store(path)
    try:
        other["worker"].join(timeout=30)
        assert "error" not in other, other.get("error")
        assert store.data_version == DATA_VERSION
        with store.transaction() as tx:
            tx.put("work", "w1", {})
    finally:
        store.close()
        if "store" in other:
            other["store"].close()
    assert raw_state(path)["user_version"] == DATA_VERSION
