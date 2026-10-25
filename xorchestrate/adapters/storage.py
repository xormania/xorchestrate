"""Transactional state adapters. Stored values never share mutable application data."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ..model import JSON, Conflict, OrchestrationError, Record


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
    """Process-local serializable storage, useful for embedded use and tests."""

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


class SQLiteStore:
    """Durable serializable transactions across threads, instances and processes.

    Options: ``path`` (required), ``busy_timeout_ms`` (default 5000). A string is
    shorthand for a path. Each instance owns its connection; newly created state
    files and directories are private to the current user.
    """

    def __init__(self, options: dict | str):
        options = {"path": options} if isinstance(options, str) else options
        if not isinstance(options, dict) or not isinstance(options.get("path"), str) or not options["path"]:
            raise OrchestrationError("SQLite storage requires a nonempty path.")
        timeout = options.get("busy_timeout_ms", 5000)
        if type(timeout) is not int or timeout < 0:
            raise OrchestrationError("SQLite busy_timeout_ms must be a nonnegative integer.")
        path = options["path"]
        if path != ":memory:":
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
            self._connection = sqlite3.connect(path, timeout=timeout / 1000, isolation_level=None, check_same_thread=False)
        except sqlite3.Error as exc:
            raise _storage_error(exc) from exc
        try:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS records ("
                "kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, version INTEGER NOT NULL, "
                "PRIMARY KEY (kind, key))"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)"
            )
            self._connection.commit()
        except sqlite3.Error as exc:
            self._connection.close()
            raise _storage_error(exc) from exc
        except BaseException:
            self._connection.close()
            raise

    @contextmanager
    def transaction(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("The state store is closed.")
            if self._active:
                raise RuntimeError("Nested transactions are not supported.")
            transaction = None
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._active = True
                transaction = _SQLiteTransaction(self._connection)
                yield transaction
                transaction._check()
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
