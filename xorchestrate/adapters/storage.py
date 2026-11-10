"""Transactional state adapters. Stored values never share mutable application data."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..model import JSON, Conflict, OrchestrationError, Record
from .storage_migrations import DATA_VERSION, REGISTRY, RELEASE, MigrationRegistry


def _encode(data: JSON) -> str:
    if not isinstance(data, dict):
        raise OrchestrationError("State and event data must be JSON objects.")
    try:
        encoded = json.dumps(data, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise OrchestrationError("State and event data must contain valid JSON values.") from exc
    # The JSON encoder accepts tuples and non-string keys, which would silently
    # change types on read. Keep the same data contract for both adapters.
    pending = [data]
    seen = set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list)):
            if id(value) in seen:
                continue
            seen.add(id(value))
            if isinstance(value, dict):
                if any(not isinstance(key, str) for key in value):
                    raise OrchestrationError("JSON object keys must be strings.")
                pending.extend(value.values())
            else:
                pending.extend(value)
        elif not isinstance(value, (str, int, float, bool, type(None))):
            raise OrchestrationError("State and event data must contain valid JSON values.")
    return encoded


_MISSING = object()


def _identity(kind: str, key=_MISSING) -> None:
    if not isinstance(kind, str) or not kind:
        raise OrchestrationError("Record kind must be a nonempty string.")
    if key is not _MISSING and (not isinstance(key, str) or not key):
        raise OrchestrationError("Record key must be a nonempty string.")
    try:
        kind.encode("utf-8")
        if key is not _MISSING:
            key.encode("utf-8")
    except UnicodeError as exc:
        raise OrchestrationError("Record identities must be valid Unicode strings.") from exc


def _version(version: int) -> None:
    if type(version) is not int or version < 1:
        raise OrchestrationError("Expected version must be a positive integer.")


def _page(after: int, limit: int) -> None:
    if type(after) is not int or after < 0 or type(limit) is not int or limit < 0:
        raise OrchestrationError("Event cursor and limit must be nonnegative integers.")


class _Transaction:
    def __init__(self):
        self.active = True
        self.owner = threading.get_ident()
        self.aborted = False

    def _check(self):
        if not self.active or self.owner != threading.get_ident():
            raise RuntimeError("Use a transaction only inside its context and on its owning thread.")
        if self.aborted:
            raise OrchestrationError("The transaction was aborted and cannot commit.", "transaction_aborted")

    def abort(self):
        if not self.active or self.owner != threading.get_ident():
            raise RuntimeError("Abort a transaction only inside its context and on its owning thread.")
        self.aborted = True


class _MemoryTransaction(_Transaction):
    def __init__(self, records, events):
        super().__init__()
        self.records = records.copy()
        self.log = events.copy()

    def get(self, kind: str, key: str) -> Record | None:
        self._check()
        _identity(kind, key)
        item = self.records.get((kind, key))
        return Record(kind, key, json.loads(item[0]), item[1]) if item else None

    def list(self, kind: str) -> list[Record]:
        self._check()
        _identity(kind)
        return [
            Record(kind, key, json.loads(data), version)
            for (record_kind, key), (data, version) in sorted(self.records.items())
            if record_kind == kind
        ]

    def put(self, kind: str, key: str, data: JSON, expected_version: int | None = None) -> Record:
        self._check()
        _identity(kind, key)
        if expected_version is not None:
            _version(expected_version)
        encoded = _encode(data)
        existing = self.records.get((kind, key))
        if (expected_version is None and existing is not None) or (
            expected_version is not None and (existing is None or existing[1] != expected_version)
        ):
            raise Conflict()
        version = 1 if existing is None else existing[1] + 1
        self.records[(kind, key)] = (encoded, version)
        return Record(kind, key, json.loads(encoded), version)

    def delete(self, kind: str, key: str, expected_version: int) -> None:
        self._check()
        _identity(kind, key)
        _version(expected_version)
        existing = self.records.get((kind, key))
        if existing is None or existing[1] != expected_version:
            raise Conflict()
        del self.records[(kind, key)]

    def emit(self, event: JSON) -> int:
        self._check()
        self.log.append(_encode(event))
        return len(self.log)

    def events(self, after: int = 0, limit: int = 100) -> list[JSON]:
        self._check()
        _page(after, limit)
        return [
            {**json.loads(data), "seq": seq}
            for seq, data in enumerate(self.log[after : after + limit], start=after + 1)
        ]


class MemoryStore:
    """Process-local serializable storage, useful for embedded use and tests.

    Its data never outlives the process, so it is always at ``DATA_VERSION``.
    """

    data_version = DATA_VERSION

    def data_version_info(self) -> dict:
        return {"data_version": DATA_VERSION, "current": DATA_VERSION, "status": "current",
                "pre_versioned": False, "written_by": None, "migrated_at": None,
                "pending_steps": [], "read_only": False}

    def __init__(self, options: dict | None = None):
        self._lock = threading.RLock()
        self._active = False
        self._closed = False
        self._records = {}
        self._events = []

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("The state store is closed.")
            if self._active:
                raise RuntimeError("Nested transactions are not supported.")
            self._active = True
            transaction = _MemoryTransaction(self._records, self._events)
            try:
                yield transaction
                transaction._check()
                self._records = transaction.records
                self._events = transaction.log
            finally:
                transaction.active = False
                self._active = False

    def close(self) -> None:
        with self._lock:
            if self._active:
                raise RuntimeError("Cannot close a state store inside its transaction.")
            self._closed = True


class _SQLiteTransaction(_Transaction):
    def __init__(self, connection):
        super().__init__()
        self.connection = connection

    def get(self, kind: str, key: str) -> Record | None:
        self._check()
        _identity(kind, key)
        row = self.connection.execute(
            "SELECT data, version FROM records WHERE kind = ? AND key = ?", (kind, key)
        ).fetchone()
        return Record(kind, key, json.loads(row[0]), row[1]) if row else None

    def list(self, kind: str) -> list[Record]:
        self._check()
        _identity(kind)
        rows = self.connection.execute(
            "SELECT key, data, version FROM records WHERE kind = ? ORDER BY key", (kind,)
        ).fetchall()
        return [Record(kind, key, json.loads(data), version) for key, data, version in rows]

    def put(self, kind: str, key: str, data: JSON, expected_version: int | None = None) -> Record:
        self._check()
        _identity(kind, key)
        encoded = _encode(data)
        if expected_version is None:
            try:
                self.connection.execute(
                    "INSERT INTO records (kind, key, data, version) VALUES (?, ?, ?, 1)", (kind, key, encoded)
                )
            except sqlite3.IntegrityError as exc:
                raise Conflict() from exc
            version = 1
        else:
            _version(expected_version)
            version = expected_version + 1
            cursor = self.connection.execute(
                "UPDATE records SET data = ?, version = ? WHERE kind = ? AND key = ? AND version = ?",
                (encoded, version, kind, key, expected_version),
            )
            if cursor.rowcount != 1:
                raise Conflict()
        return Record(kind, key, json.loads(encoded), version)

    def delete(self, kind: str, key: str, expected_version: int) -> None:
        self._check()
        _identity(kind, key)
        _version(expected_version)
        cursor = self.connection.execute(
            "DELETE FROM records WHERE kind = ? AND key = ? AND version = ?", (kind, key, expected_version)
        )
        if cursor.rowcount != 1:
            raise Conflict()

    def emit(self, event: JSON) -> int:
        self._check()
        cursor = self.connection.execute("INSERT INTO events (data) VALUES (?)", (_encode(event),))
        return cursor.lastrowid

    def events(self, after: int = 0, limit: int = 100) -> list[JSON]:
        self._check()
        _page(after, limit)
        rows = self.connection.execute(
            "SELECT seq, data FROM events WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit)
        ).fetchall()
        return [{**json.loads(data), "seq": seq} for seq, data in rows]


def _private_directory(path: Path) -> None:
    if not path.exists():
        _private_directory(path.parent)
        path.mkdir(mode=0o700, exist_ok=True)


def _storage_error(exc: sqlite3.Error) -> OrchestrationError:
    # Extended result codes retain the primary error in the low byte.
    retryable = getattr(exc, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    return OrchestrationError(
        "State storage is busy; retry the transaction." if retryable else "State storage could not complete the operation.",
        code="storage_busy" if retryable else "storage_error",
        details={"retryable": retryable},
    )


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS records ("
    "kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL, "
    "PRIMARY KEY (kind, key))",
    "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)",
)
# The data-version record lives in its own table, outside the application's
# record namespace: no record kind can collide with it, ``list`` and every
# record or event read leave it out, and it needs no optimistic record version.
_META_SCHEMA = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, data TEXT NOT NULL)"
_META_KEY = "data"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _ReadOnlySQLiteTransaction(_SQLiteTransaction):
    def _refuse(self):
        self._check()
        raise OrchestrationError("This state store was opened read-only.", "storage_read_only")

    def put(self, kind: str, key: str, data: JSON, expected_version: int | None = None) -> Record:
        self._refuse()

    def delete(self, kind: str, key: str, expected_version: int) -> None:
        self._refuse()

    def emit(self, event: JSON) -> int:
        self._refuse()


def _read_only_error(exc: sqlite3.Error) -> OrchestrationError:
    # SQLite reads a WAL-mode database only through its -wal/-shm sidecars and
    # must create them when absent, so read-only access needs a writable
    # directory. ``immutable=1`` would avoid that but is unsafe with writers.
    if getattr(exc, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_READONLY, sqlite3.SQLITE_CANTOPEN):
        return OrchestrationError(
            "Read-only access to this state store needs write permission on its directory, where SQLite "
            "creates the store's -wal and -shm files; the database file itself is never written.",
            "storage_read_only_unavailable", {"retryable": False},
        )
    return _storage_error(exc)


class SQLiteStore:
    """Durable serializable transactions across threads, instances and processes.

    Options: ``path`` (required), ``busy_timeout_ms`` (default 5000). Each
    instance owns its connection; newly created state files and directories are
    private to the current user.

    A new store records ``DATA_VERSION`` in ``PRAGMA user_version`` and a
    ``meta`` record in the transaction that creates its schema. Opening an
    existing store never writes for versioning: a pre-versioned store
    (``user_version`` 0) is read as version 1, a newer store is refused with
    ``data_version_unsupported`` and an older one with
    ``data_migration_required``. Only ``migrate=True``, which configuration
    cannot select, stamps a pre-versioned store and runs the migration chain.
    A file with a nonzero ``user_version`` but no store schema is refused
    unchanged (``data_version_unsupported`` or ``storage_unrecognized``).
    ``read_only=True`` opens an existing file without any write, write lock or
    schema change and refuses no version; ``data_version_info()`` reports it.
    SQLite still needs a writable directory for the WAL sidecar files
    (``storage_read_only_unavailable`` otherwise) and may leave them behind.
    """

    def __init__(self, options: dict, *, migrate: bool = False, read_only: bool = False,
                 migrations: MigrationRegistry | None = None):
        if not isinstance(options, dict) or not isinstance(options.get("path"), str) or not options["path"]:
            raise OrchestrationError("SQLite storage requires a nonempty path.")
        timeout = options.get("busy_timeout_ms", 5000)
        if type(timeout) is not int or timeout < 0:
            raise OrchestrationError("SQLite busy_timeout_ms must be a nonnegative integer.")
        if type(migrate) is not bool or type(read_only) is not bool or (migrate and read_only):
            raise OrchestrationError("Select at most one of migrate and read_only.")
        if migrations is not None and not isinstance(migrations, MigrationRegistry):
            raise OrchestrationError("Data migrations must be a migration registry.")
        self._migrations = REGISTRY if migrations is None else migrations
        self._read_only = read_only
        path = options["path"]
        uri = False
        if read_only:
            target = Path(path).expanduser()
            if path == ":memory:" or not target.is_file():
                raise OrchestrationError("Read-only storage requires an existing database file.",
                                         "storage_error", {"retryable": False})
            path, uri = target.resolve().as_uri() + "?mode=ro", True
        elif path != ":memory:":
            target = Path(path).expanduser()
            _private_directory(target.parent)
            try:
                descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            path = str(target)
        self._lock = threading.RLock()
        self._active = False
        self._closed = False
        try:
            self._connection = sqlite3.connect(path, timeout=timeout / 1000, isolation_level=None,
                                               check_same_thread=False, uri=uri)
        except sqlite3.Error as exc:
            raise _storage_error(exc) from exc
        try:
            if read_only:
                self._state = self._read_snapshot()
            else:
                self._open(migrate)
        except sqlite3.Error as exc:
            self._connection.close()
            raise (_read_only_error(exc) if read_only else _storage_error(exc)) from exc
        except BaseException:
            self._connection.close()
            raise

    def _open(self, migrate: bool) -> None:
        # Refuse a file this release cannot use before changing anything in it.
        refusal = self._refusal(self._read_snapshot())
        if refusal is not None:
            raise refusal
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            state = self._read_state()
            refusal = self._refusal(state)
            if refusal is not None:
                raise refusal
            if not state["initialized"]:
                for statement in (*_SCHEMA, _META_SCHEMA):
                    self._connection.execute(statement)
                self._write_version(self._migrations.current)
                self._connection.commit()
                state = self._read_state()
            else:
                # Inspecting an existing store writes nothing.
                self._connection.rollback()
        except BaseException:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        self._state = state
        stored = self._stored_version(state)
        if migrate:
            self._migrate()
        elif stored < self._migrations.current:
            raise OrchestrationError(
                f"Stored state uses data version {stored}; this release reads data version "
                f"{self._migrations.current}. Migrate it explicitly; opening a store never migrates it.",
                "data_migration_required",
                {"stored": stored, "current": self._migrations.current, "release": RELEASE},
            )

    def _read_snapshot(self) -> dict:
        """Read the version metadata from one consistent snapshot, writing nothing.

        In autocommit mode each statement would see its own snapshot, so a
        concurrent initializer could make the schema look absent while the
        version looks set. A deferred read transaction (a shared lock, never
        the write lock) keeps both reads consistent and is always rolled back.
        """
        self._connection.execute("BEGIN")
        try:
            return self._read_state()
        finally:
            self._connection.rollback()

    def _read_state(self) -> dict:
        tables = {row[0] for row in self._connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('records', 'meta')")}
        meta = None
        if "meta" in tables:
            row = self._connection.execute("SELECT data FROM meta WHERE key = ?", (_META_KEY,)).fetchone()
            if row is not None:
                try:
                    meta = json.loads(row[0])
                except ValueError:
                    meta = None
                meta = meta if isinstance(meta, dict) else None
        return {
            "user_version": self._connection.execute("PRAGMA user_version").fetchone()[0],
            "initialized": "records" in tables,
            "meta": meta,
        }

    @staticmethod
    def _stored_version(state: dict) -> int | None:
        if not state["initialized"]:
            return None
        # A store created before data versioning holds the version 1 schema.
        return state["user_version"] or 1

    def _refusal(self, state: dict) -> OrchestrationError | None:
        """Why this release must not open (or create a schema in) this file, if it must not."""
        if state["user_version"] > self._migrations.current:
            return self._unsupported(state)
        if not state["initialized"] and state["user_version"]:
            return OrchestrationError(
                "The state file declares a data version but has no Xorchestrate store schema; "
                "it is not an Xorchestrate store and was left unchanged.",
                "storage_unrecognized", {"user_version": state["user_version"], "retryable": False},
            )
        return None

    def _unsupported(self, state: dict) -> OrchestrationError:
        stored = state["user_version"]
        written_by = (state["meta"] or {}).get("written_by")
        return OrchestrationError(
            f"Stored state uses data version {stored}, written by {written_by or 'an unknown release'}; "
            f"this release reads data version {self._migrations.current}.",
            "data_version_unsupported",
            {"stored": stored, "current": self._migrations.current, "written_by": written_by, "release": RELEASE},
        )

    def _write_version(self, version: int) -> None:
        """Stamp ``version`` inside the caller's open write transaction."""
        self._connection.execute(_META_SCHEMA)
        self._connection.execute(
            "INSERT OR REPLACE INTO meta (key, data) VALUES (?, ?)",
            (_META_KEY, _encode({"data_version": version, "written_by": RELEASE, "migrated_at": _now()})),
        )
        # PRAGMA takes no parameters; the registry guarantees a positive integer.
        self._connection.execute(f"PRAGMA user_version = {int(version)}")

    def _migrate(self) -> None:
        """Stamp a pre-versioned store, then run each step in its own transaction.

        Every transaction re-reads the stored version under the write lock, so a
        concurrent or interrupted migration resumes at the committed version.
        """
        while True:
            source = None
            try:
                with self.transaction() as transaction:
                    state = self._read_state()
                    refusal = self._refusal(state)
                    if refusal is not None:
                        raise refusal
                    stored = self._stored_version(state)
                    if state["user_version"] == 0:
                        self._write_version(1)
                        continue
                    chain = self._migrations.chain(stored)
                    if not chain:
                        break
                    source, step = chain[0]
                    counts = step(transaction)
                    if not isinstance(counts, Mapping) or any(
                        not isinstance(kind, str) or type(count) is not int or count < 0
                        for kind, count in counts.items()
                    ):
                        raise ValueError("A migration step must return nonnegative record counts by kind.")
                    transaction.emit({
                        "type": "data_migrated", "actor": "xorchestrate", "at": _now(),
                        "from": source, "to": source + 1, "release": RELEASE,
                        "counts": dict(sorted(counts.items())),
                    })
                    self._write_version(source + 1)
            except Exception as exc:
                # Refusals precede step selection and keep their own codes. Any
                # failure of a selected step, including an OrchestrationError
                # such as Conflict raised by the step, is a failed migration.
                if source is None and isinstance(exc, OrchestrationError):
                    raise
                raise OrchestrationError(
                    "A data migration step failed; its transaction was rolled back.",
                    "data_migration_failed",
                    {"from": source, "to": None if source is None else source + 1, "release": RELEASE},
                ) from exc
        self._state = self._read_snapshot()

    @property
    def data_version(self) -> int | None:
        """The stored data version as read; a pre-versioned store reads as 1."""
        return self._stored_version(self._state)

    def data_version_info(self) -> dict:
        """The version state found at open, or after migrating; performs no I/O."""
        state = self._state
        stored = self._stored_version(state)
        current = self._migrations.current
        if state["user_version"] > current:
            status, stored = "unsupported", state["user_version"]
        elif stored is None:
            status = "unrecognized" if state["user_version"] else "uninitialized"
        elif stored < self._migrations.oldest():
            status = "migration_unavailable"
        elif stored < current:
            status = "migration_required"
        else:
            status = "current"
        meta = state["meta"] or {}
        return {
            "data_version": stored,
            "current": current,
            "status": status,
            "pre_versioned": state["initialized"] and state["user_version"] == 0,
            "written_by": meta.get("written_by"),
            "migrated_at": meta.get("migrated_at"),
            "pending_steps": [[source, source + 1] for source in range(stored, current)]
            if status == "migration_required" else [],
            "read_only": self._read_only,
        }

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("The state store is closed.")
            if self._active:
                raise RuntimeError("Nested transactions are not supported.")
            transaction = None
            try:
                # A read-only store takes a consistent snapshot without the
                # write lock and never commits.
                self._connection.execute("BEGIN" if self._read_only else "BEGIN IMMEDIATE")
                self._active = True
                transaction = (_ReadOnlySQLiteTransaction if self._read_only else _SQLiteTransaction)(self._connection)
                yield transaction
                transaction._check()
                if self._read_only:
                    self._connection.rollback()
                else:
                    self._connection.commit()
            except sqlite3.Error as exc:
                self._rollback()
                raise _storage_error(exc) from exc
            except BaseException:
                self._rollback()
                raise
            finally:
                if transaction is not None:
                    transaction.active = False
                self._active = False

    def _rollback(self) -> None:
        try:
            self._connection.rollback()
        except sqlite3.Error:
            # A failed rollback makes this connection unsafe to reuse. Preserve
            # the original failure while releasing any surviving transaction.
            self._connection.close()
            self._closed = True

    def close(self) -> None:
        with self._lock:
            if self._active:
                raise RuntimeError("Cannot close a state store inside its transaction.")
            if not self._closed:
                self._connection.close()
                self._closed = True
