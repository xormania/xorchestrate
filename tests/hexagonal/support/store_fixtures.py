"""Versioned state store fixtures and pre-versioned store construction.

Committed fixture stores live in ``tests/fixtures/stores/``, one per data
version, each written by the release that introduced that version. Regenerate
the fixture for the current data version from the repository root with:

    python3 -m tests.hexagonal.support.store_fixtures tests/fixtures/stores/data-v1.db

Do so only in the release that bumps ``DATA_VERSION``; earlier fixtures stay as
they were written so every migration step keeps a real input. Tests copy a
fixture before opening it because opening a SQLite store creates sidecar files.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

from xorchestrate.adapters.storage import SQLiteStore
from xorchestrate.adapters.storage_migrations import DATA_VERSION

from .application import Provider, application, invoke

FIXTURE_STORES = Path(__file__).resolve().parents[2] / "fixtures" / "stores"

# The schema written by every release before data versioning (master 246206a,
# ``xorchestrate/adapters/storage.py``), kept literally as an independent oracle.
PRE_VERSIONED_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS records ("
    "kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL, "
    "PRIMARY KEY (kind, key))",
    "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)",
)


def fixture_store(version):
    return FIXTURE_STORES / f"data-v{version}.db"


def copy_fixture(version, destination):
    """Copy a committed fixture store so a test can open it without changing it."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture_store(version), destination)
    return destination


def write_pre_versioned_store(path, records=(), events=()):
    """Write a store exactly as a release before data versioning would."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        for statement in PRE_VERSIONED_SCHEMA:
            connection.execute(statement)
        connection.executemany("INSERT INTO records (kind, key, data, version) VALUES (?, ?, ?, ?)", records)
        connection.executemany("INSERT INTO events (data) VALUES (?)", [(event,) for event in events])
        connection.commit()
    finally:
        connection.close()
    return Path(path)


def raw_state(path):
    """Read version metadata through SQLite directly, independent of the adapter."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        meta = (connection.execute("SELECT key, data FROM meta").fetchall() if "meta" in tables else None)
        return {"user_version": connection.execute("PRAGMA user_version").fetchone()[0],
                "tables": tables, "meta": meta}
    finally:
        connection.close()


def write_fixture(path):
    """Write representative application records at the current data version."""
    target = Path(path)
    temporary = target.with_name(target.name + ".tmp")
    for leftover in (temporary, Path(f"{temporary}-wal"), Path(f"{temporary}-shm")):
        leftover.unlink(missing_ok=True)
    store = SQLiteStore({"path": str(temporary)})
    try:
        app = application(store, provider=Provider())
        app.call("add_actor", {"actor_id": "fixture-actor", "roles": ["implementer"]})
        app.call("create_work", {"goal": "Fixture work retained across data versions"})
        app.call("create_work", {"goal": "Second fixture work", "inputs": {"priority": "low"}})
        invoke(app, key="fixture-effect", value=7)
    finally:
        store.close()
    if Path(f"{temporary}-wal").exists():
        raise RuntimeError("The fixture store was not checkpointed into a single file.")
    Path(f"{temporary}-shm").unlink(missing_ok=True)
    temporary.replace(target)
    return target


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        raise SystemExit("usage: python3 -m tests.hexagonal.support.store_fixtures OUTPUT")
    written = write_fixture(arguments[0])
    sys.stdout.write(f"Wrote data version {DATA_VERSION} fixture store {written}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
