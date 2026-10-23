#!/usr/bin/env python3
"""The session watchdog: one line per condition worth acting on.

Reads the live dispatch store and the forge; never writes. Every tick
it classifies each job directory by what is on disk and prints, once
per record, the lines the contract (`ops/conductor/watchdog-lever.md`)
names: a record closed, a harness process gone with no exit written,
an exit written that the launcher never collected, a run past its
warning fraction of the wall, a record it cannot judge, CI red or
cancelled on an open pull request into the trunk, mechanical deviations
from the declared plan, and a heartbeat on cadence. A tick that raises
prints the error as a line and the loop goes on: silence is never the
report of a crash.
"""
from __future__ import annotations

import argparse
import codecs
import contextlib
import json
import os
import re
import selectors
import shutil
import stat as stat_mode
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, conf, emit, now, parse_stamp, refuse
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

from dispatch_observation import DispatchRecord

HERE = Path(__file__).resolve()
JOBS_JSON_CANDIDATES = (
    HERE.parents[2] / "devlane" / "task" / "jobs.json",
    Path.cwd() / "ops" / "devlane" / "task" / "jobs.json",
)
DEFAULT_TICK = 300.0
DEFAULT_HEARTBEAT = 6
DEFAULT_FORGE_EVERY = 1
DEFAULT_WARN_FRACTION = 2 / 3
FORGE_LIMIT = 200
FORGE_TIMEOUT = 10
JOB_INPUT_LIMIT = 64 * 1024 * 1024
PROCESS_OUTPUT_LIMIT = 1024 * 1024
CLOSED_CACHE_LIMIT = 16 * 1024 * 1024
CHECK_NAME_LIMIT = 512
FALLBACK_WARN_MIN = 40
SETTLING_TICKS = 2
ASSUMPTION_WORDS = (
    "assumption",
    "assume",
    "substitute",
    "stub",
    "hermetic substitute",
)
LOCK_FILES = ("composer.lock", "Cargo.lock", "requirements.lock", "symfony.lock")
REFUSAL_CLASSES = ("empty-comparison", "scope-cap", "conf-key", "toolchain")
ERROR_TEXT_RE = re.compile(
    r"\berrored\b|\berrors\s*=\s*[1-9]\d*\b",
    re.IGNORECASE,
)
REFUSAL_BYTES_RE = re.compile(
    rb"(?m)^\s*(?:" + b"|".join(
        re.escape(item).encode() for item in REFUSAL_CLASSES
    ) + rb"):"
)
BUCKET_LIMITS = {
    "S": {"files": 3, "lines": 2_000},
}
BUCKET_OVERRUN_FACTOR = 4
MINUTES_OVERRUN_FACTOR = 4
CALIBRATION_MINUTES = {
    "S": {"implement": 4, "author-tests": 21, "check-tests": 10},
}
ROUND_JOBS = {"implement", "author-tests"}
CHECK_BUCKETS = {"pass", "fail", "pending", "skipping", "cancel"}
SHA_RE = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def say(line):
    print(line, flush=True)


def store_root(explicit):
    """The same rule as launch.py `_jobs_root`: DISPATCH_JOBS, then
    XDG_STATE_HOME, then HOME/.local/state."""
    if explicit:
        return Path(explicit)
    if os.environ.get("DISPATCH_JOBS"):
        return Path(os.environ["DISPATCH_JOBS"])
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path(os.environ.get("HOME", "")) / ".local/state"
    return base / conf("LANE_NAME") / "dispatch"


def stamp_or_none(value):
    """The watchdog reads stamps from files it does not own: a malformed
    or missing one is None, never a refusal."""
    try:
        return parse_stamp(value)
    except (TypeError, ValueError):
        return None


def stamp_of(name):
    try:
        return datetime.strptime(name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class InputUnavailable(OSError):
    """A per-job input exists but cannot produce a safe reading."""


class InitialExtent:
    """A binary stream capped at the regular file's size when opened."""

    def __init__(self, stream, size):
        self.stream = stream
        self.remaining = size

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        amount = self.remaining if size < 0 else min(size, self.remaining)
        chunk = self.stream.read(amount)
        self.remaining -= len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.stream.close()


def _open_directory(path, *, dir_fd=None):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NONBLOCK
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError:
        raise InputUnavailable("job input unavailable") from None


def _open_job_file(job_path, relative):
    """Open a bounded regular file beneath a no-follow job directory."""
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InputUnavailable("job input unavailable")
    directories = []
    file_fd = None
    try:
        directories.append(_open_directory(job_path))
        for part in parts[:-1]:
            directories.append(_open_directory(part, dir_fd=directories[-1]))
        flags = os.O_RDONLY | os.O_NONBLOCK
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_fd = os.open(parts[-1], flags, dir_fd=directories[-1])
        except FileNotFoundError:
            raise
        except OSError:
            raise InputUnavailable("job input unavailable") from None
        file_stat = os.fstat(file_fd)
        if (
            not stat_mode.S_ISREG(file_stat.st_mode)
            or file_stat.st_size > JOB_INPUT_LIMIT
        ):
            raise InputUnavailable("job input unavailable")
        stream = os.fdopen(file_fd, "rb")
        file_fd = None
        return InitialExtent(stream, file_stat.st_size), file_stat
    except FileNotFoundError:
        raise
    except InputUnavailable:
        raise
    except OSError:
        raise InputUnavailable("job input unavailable") from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for directory in reversed(directories):
            os.close(directory)


def _read_job_text(job_path, relative):
    stream, _ = _open_job_file(job_path, relative)
    with stream:
        return stream.read().decode("utf-8", "replace")


def _read_job_json(job_path, relative):
    try:
        data = json.loads(_read_job_text(job_path, relative))
    except FileNotFoundError:
        return None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_json(path):
    """A dict, or None when the file is absent or not a JSON object. Any
    other failure to read (a directory in its place, a permission) is
    raised, so the tick guard reports it rather than inferring absence."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def job_caps():
    """The catalogue beside this file, or beside the working directory
    when this file runs as a staged copy."""
    data = {}
    for candidate in JOBS_JSON_CANDIDATES:
        if candidate.is_file():
            data = read_json(candidate) or {}
            break
    caps = {}
    for name, job in data.items():
        if isinstance(job, dict) and isinstance(job.get("caps"), dict):
            timeout = job["caps"].get("timeout")
            if isinstance(timeout, (int, float)):
                caps[name] = float(timeout)
    return caps


def process_alive(pid, expect_word):
    """True when the pid is a live process whose command line carries
    the expected word (the harness name), so a reused pid does not read
    as the worker. EPERM means the process exists."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    proc = Path("/proc") / str(pid)
    if proc.is_dir():
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            return True
        return expect_word in cmdline if expect_word else True
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def run(argv, timeout=90):
    """Stdout of a subprocess, or None when it could not be run or
    exited non-zero. Capture is bounded and never forwarded."""
    exe = shutil.which(argv[0])
    if exe is None:
        return None
    process = None
    selector = selectors.DefaultSelector()
    stdout = []
    captured = 0
    deadline = time.monotonic() + max(0, timeout)
    try:
        process = subprocess.Popen(
            [exe, *argv[1:]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            events = selector.select(remaining)
            if not events:
                return None
            for key, _ in events:
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                captured += len(chunk)
                if captured > PROCESS_OUTPUT_LIMIT:
                    return None
                if key.data == "stdout":
                    stdout.append(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        returncode = process.wait(timeout=remaining)
        if returncode != 0:
            return None
        return b"".join(stdout).decode("utf-8")
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    finally:
        selector.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            with contextlib.suppress(subprocess.SubprocessError):
                process.wait(timeout=max(0, deadline - time.monotonic()))


class Entry:
    """One job directory, classified by what is on disk."""

    def __init__(self, path, kind, rec, state, has_exit, caps):
        self.path = path
        self.name = path.name
        self.kind = kind
        self.rec = rec
        self.state = state
        self.has_exit = has_exit
        self.error = None
        self.from_closed_cache = False
        observation = DispatchRecord(rec)
        self.summary_status = observation.status
        self.summary_envelope = observation.envelope.get("status")
        self.summary_changed = observation.changed_count if observation.changed_count is not None else "unknown"
        at = rec.get("at") if isinstance(rec.get("at"), dict) else {}
        self.launched = stamp_or_none(at.get("launched")) or stamp_of(self.name)
        self.closed_at = stamp_or_none(at.get("closed"))
        job = rec.get("job")
        self.job = job.get("name") if isinstance(job, dict) else job
        harness = rec.get("harness")
        self.harness = harness.get("name") if isinstance(harness, dict) else None
        rec_caps = rec.get("caps") if isinstance(rec.get("caps"), dict) else {}
        timeout = rec_caps.get("timeout")
        if not isinstance(timeout, (int, float)):
            timeout = caps.get(self.job)
        self.wall_min = float(timeout) / 60 if isinstance(timeout, (int, float)) else None

    def minutes(self, until=None):
        if self.launched is None:
            return None
        end = until or now()
        return int((end - self.launched).total_seconds() // 60)

    def summary(self):
        return f"status={self.summary_status} env={self.summary_envelope} changed={self.summary_changed}"

    def closed_projection(self):
        """The fields a previously scanned, unchanged closed job still needs."""
        compact = object.__new__(Entry)
        for name in (
            "path", "name", "kind", "error", "launched", "closed_at", "job",
            "harness", "wall_min", "fingerprint", "exit_mtime", "summary_status",
            "summary_envelope", "summary_changed",
        ):
            setattr(compact, name, getattr(self, name))
        compact.rec = {"status": self.summary_status}
        compact.state = {}
        compact.has_exit = self.has_exit
        compact.from_closed_cache = False
        return compact

    def harness_alive(self):
        pid = self.state.get("pid")
        if isinstance(pid, bool) or not isinstance(pid, (int, str)):
            return None
        try:
            pid = int(pid)
        except ValueError:
            return None
        if pid <= 0:
            return None
        return process_alive(pid, self.harness)


WATCHED_INPUTS = (
    "record.json",
    "state.json",
    "exit",
    "in/plan.md",
    "raw.out",
    "stdout",
    "stderr",
    "launcher.log",
)


def input_fingerprint(path):
    """Mutation-sensitive metadata for every input used by a closed scan."""
    fingerprint = []
    for name in WATCHED_INPUTS:
        try:
            stream, file_stat = _open_job_file(path, name)
        except FileNotFoundError:
            fingerprint.append((name, None))
            continue
        stream.stream.close()
        fingerprint.append((
            name,
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_mode,
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        ))
    return tuple(fingerprint)


def _lstat_watched(dir_fd, relative):
    """State one watched name under a no-follow walk: every directory on
    the way is opened with O_NOFOLLOW and only the last name is stated
    against it, so a symlinked component refuses instead of being
    crossed. os.lstat with a dir_fd follows every component but the last,
    which let a file outside the store move a backlog directory's
    fingerprint (20260916T100908Z-review-grok-965afa#1,
    20260916T100931Z-review-codex-48ebeb#2)."""
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise InputUnavailable("job input unavailable")
    directories = []
    try:
        for part in parts[:-1]:
            directories.append(
                _open_directory(part, dir_fd=directories[-1] if directories else dir_fd)
            )
        return os.lstat(
            parts[-1], dir_fd=directories[-1] if directories else dir_fd
        )
    finally:
        for fd in reversed(directories):
            os.close(fd)


def error_fingerprint(path, exc):
    """Why a job directory's inputs cannot be read, in the same shape a
    readable one has: the refusal's own words, then no-follow metadata
    for every watched input. Nothing is opened or read — each row only
    says what that name is — so one unreadable condition is told from
    another and a condition replaced after start speaks again (LD-70;
    the Daybreak audit, 20260916T092226Z-review-codex-12e47c#2: every
    error entry carried None, so the whole family was one condition)."""
    rows = [("error", type(exc).__name__, str(exc))]
    try:
        dir_fd = _open_directory(path)
    except OSError as open_exc:
        rows.append(("", None, getattr(open_exc, "errno", None)))
        return tuple(rows)
    try:
        for name in WATCHED_INPUTS:
            try:
                file_stat = _lstat_watched(dir_fd, name)
            except OSError as stat_exc:
                rows.append((name, None, getattr(stat_exc, "errno", None)))
                continue
            rows.append((
                name,
                file_stat.st_dev,
                file_stat.st_ino,
                file_stat.st_mode,
                file_stat.st_size,
                file_stat.st_mtime_ns,
                file_stat.st_ctime_ns,
            ))
    finally:
        os.close(dir_fd)
    return tuple(rows)


def retained_size(root, *, exclude=()):
    """Account for a retained Python object graph without recursive descent."""
    seen = {id(value) for value in exclude}
    pending = [root]
    total = 0
    while pending:
        value = pending.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(value)
        if isinstance(value, dict):
            for key, item in value.items():
                pending.append(key)
                pending.append(item)
        elif isinstance(value, (list, tuple, set, frozenset)):
            pending.extend(value)
        elif hasattr(value, "__dict__"):
            pending.append(vars(value))
        for cls in type(value).__mro__:
            slots = getattr(cls, "__slots__", ())
            if isinstance(slots, str):
                slots = (slots,)
            for slot in slots:
                if slot in {"__dict__", "__weakref__"}:
                    continue
                with contextlib.suppress(AttributeError):
                    pending.append(getattr(value, slot))
    return total


class ClosedCache(dict):
    """LRU bounded by a conservative retained-object-graph upper bound."""

    _MISSING = object()

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._entry_sizes = {}
        self._entry_bytes = 0
        self._size_value_bytes = 0
        self.update(*args, **kwargs)

    @property
    def retained_upper_bound(self):
        attribute_keys = sum(sys.getsizeof(key) for key in vars(self))
        return (
            sys.getsizeof(self)
            + self._entry_bytes
            + sys.getsizeof(vars(self))
            + attribute_keys
            + sys.getsizeof(self._entry_sizes)
            + self._size_value_bytes
            + sys.getsizeof(self._entry_bytes)
            + sys.getsizeof(self._size_value_bytes)
        )

    def _drop(self, key):
        entry_size = self._entry_sizes.pop(key)
        self._entry_bytes -= entry_size
        self._size_value_bytes -= sys.getsizeof(entry_size)
        super().__delitem__(key)

    def _trim(self):
        while self and self.retained_upper_bound > CLOSED_CACHE_LIMIT:
            self._drop(next(iter(self)))

    def get(self, key, default=None):
        if key not in self:
            return default
        value = super().pop(key)
        super().__setitem__(key, value)
        self._trim()
        return value

    def __setitem__(self, key, value):
        if key in self:
            self._drop(key)
        entry_size = retained_size((key, value), exclude=(self,))
        super().__setitem__(key, value)
        self._entry_sizes[key] = entry_size
        self._entry_bytes += entry_size
        self._size_value_bytes += sys.getsizeof(entry_size)
        self._trim()

    def __delitem__(self, key):
        self._drop(key)

    def clear(self):
        super().clear()
        self._entry_sizes.clear()
        self._entry_bytes = 0
        self._size_value_bytes = 0

    def pop(self, key, default=_MISSING):
        if key in self:
            value = super().__getitem__(key)
            self._drop(key)
            return value
        if default is self._MISSING:
            raise KeyError(key)
        return default

    def popitem(self):
        if not self:
            raise KeyError("popitem(): dictionary is empty")
        key = next(reversed(self))
        value = super().__getitem__(key)
        self._drop(key)
        return key, value

    def setdefault(self, key, default=None):
        if key in self:
            return self.get(key)
        self[key] = default
        return default

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def __ior__(self, other):
        self.update(other)
        return self


def cache_closed(cache, name, fingerprint, entry):
    """Retain a closed reading only while the aggregate graph fits the budget."""
    cache[name] = (fingerprint, entry)


def entries(store, caps, closed_cache=None):
    errors = []
    dirs = []
    for path in store.iterdir():
        try:
            path_stat = path.stat(follow_symlinks=False)
        except OSError as exc:
            if closed_cache is not None:
                closed_cache.pop(path.name, None)
            entry = Entry(path, "error", {}, {}, False, caps)
            entry.error = exc
            entry.fingerprint = error_fingerprint(path, exc)
            errors.append(entry)
            continue
        if stat_mode.S_ISDIR(path_stat.st_mode) or stat_mode.S_ISLNK(path_stat.st_mode):
            dirs.append(path)
    dirs.sort()
    present = {path.name for path in dirs}
    present.update(entry.name for entry in errors)
    if closed_cache is not None:
        for name in set(closed_cache) - present:
            del closed_cache[name]
    yield from errors
    for d in dirs:
        try:
            fingerprint = input_fingerprint(d)
            cached = closed_cache.get(d.name) if closed_cache is not None else None
            if cached is not None and cached[0] == fingerprint:
                cached[1].from_closed_cache = True
                yield cached[1]
                continue
            rec = _read_job_json(d, "record.json")
            state = _read_job_json(d, "state.json") or {}
        except OSError as exc:
            if closed_cache is not None:
                closed_cache.pop(d.name, None)
            entry = Entry(d, "error", {}, {}, False, caps)
            entry.error = exc
            entry.fingerprint = error_fingerprint(d, exc)
            yield entry
            continue
        exit_row = next(row for row in fingerprint if row[0] == "exit")
        has_exit = exit_row[1] is not None
        kind = DispatchRecord(rec).phase(has_exit)
        entry = Entry(d, kind, rec or {}, state, has_exit, caps)
        entry.fingerprint = fingerprint
        entry.exit_mtime = exit_row[5] / 1_000_000_000 if has_exit else None
        if closed_cache is not None:
            if kind == "closed":
                cache_closed(closed_cache, d.name, fingerprint, entry)
            else:
                closed_cache.pop(d.name, None)
        yield entry


def text_values(value):
    """Yield only values from a JSON-shaped object, never its keys."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from text_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from text_values(item)


def bucket_from(entry):
    """The first bucket line in the copied plan, or in the record scope."""
    try:
        plan = _read_job_text(entry.path, "in/plan.md")
    except FileNotFoundError:
        brief = entry.rec.get("brief")
        plan = brief.get("scope") if isinstance(brief, dict) else None
    if not isinstance(plan, str):
        return None, set()
    for line in plan.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].lower() == "bucket":
            return fields[2].upper(), set(fields[3:])
    return None, set()


def held_text(entry, names):
    """Text from held launcher files; absent files are an empty reading."""
    chunks = []
    for name in names:
        try:
            chunks.append(_read_job_text(entry.path, name))
        except FileNotFoundError:
            continue
    return "\n".join(chunks)


def _is_word(char):
    return char == "_" or char.isalnum()


def _is_digit(char):
    return char.isdecimal()


def _error_stream_matches(stream):
    """Match ERROR_TEXT_RE with bounded state over incrementally decoded text."""
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    stem = "error"
    stem_at = 0
    count_state = 0
    errored_pending = False
    previous = None

    def consume(text):
        nonlocal stem_at, count_state, errored_pending, previous
        for char in text:
            word = _is_word(char)
            if errored_pending:
                if not word:
                    return True
                errored_pending = False

            if count_state == 1:
                if char.isspace():
                    pass
                elif char == "=":
                    count_state = 2
                else:
                    count_state = 0
            elif count_state == 2:
                if char.isspace():
                    pass
                elif char in "123456789":
                    count_state = 3
                else:
                    count_state = 0
            elif count_state == 3:
                if _is_digit(char):
                    pass
                elif not word:
                    return True
                else:
                    count_state = 0

            folded = char.casefold()
            if stem_at < len(stem):
                if folded == stem[stem_at]:
                    if stem_at or previous is None or not _is_word(previous):
                        stem_at += 1
                    else:
                        stem_at = 0
                else:
                    stem_at = (
                        1
                        if folded == "e"
                        and (previous is None or not _is_word(previous))
                        else 0
                    )
            elif stem_at == len(stem):
                if folded == "s":
                    count_state = 1
                    stem_at = 0
                elif folded == "e":
                    stem_at += 1
                else:
                    stem_at = 0
            elif folded == "d":
                errored_pending = True
                stem_at = 0
            else:
                stem_at = (
                    1
                    if folded == "e"
                    and (previous is None or not _is_word(previous))
                    else 0
                )
            previous = char
        return False

    while chunk := stream.read(64 * 1024):
        if consume(decoder.decode(chunk)):
            return True
    if consume(decoder.decode(b"", final=True)):
        return True
    return errored_pending or count_state == 3


def _refusal_stream_matches(stream):
    """Match REFUSAL_BYTES_RE with bounded state, including long whitespace."""
    tokens = tuple(f"{item}:".encode() for item in REFUSAL_CLASSES)
    prefix = bytearray()
    at_line_start = True
    whitespace = b" \t\n\r\v\f"
    while chunk := stream.read(64 * 1024):
        for byte in chunk:
            if not at_line_start and not prefix:
                if byte == ord("\n"):
                    at_line_start = True
                continue
            if at_line_start and not prefix and byte in whitespace:
                continue
            prefix.append(byte)
            candidates = [token for token in tokens if token.startswith(prefix)]
            if any(len(prefix) == len(token) for token in candidates):
                return True
            if not candidates:
                at_line_start = byte == ord("\n")
                prefix.clear()
            else:
                at_line_start = False
    return False


def held_matches(entry, names, matcher):
    """Search bounded initial extents of no-follow regular held files."""
    for name in names:
        try:
            stream, _ = _open_job_file(entry.path, name)
            with stream:
                if matcher(stream):
                    return True
        except FileNotFoundError:
            continue
    return False


def _event_name(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return None
    if len(encoded) > CHECK_NAME_LIMIT:
        return None
    safe = []
    for char in value:
        code = ord(char)
        if unicodedata.category(char).startswith("C") or char in {"\u2028", "\u2029"}:
            if char == "\n":
                safe.append(r"\n")
            elif char == "\r":
                safe.append(r"\r")
            elif char == "\t":
                safe.append(r"\t")
            elif code <= 0xFFFF:
                safe.append(f"\\u{code:04x}")
            else:
                safe.append(f"\\U{code:08x}")
        else:
            safe.append(char)
    return "".join(safe)


def _check_rows(rows):
    if not isinstance(rows, list):
        return None
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        bucket = row.get("bucket")
        if not isinstance(bucket, str) or bucket not in CHECK_BUCKETS:
            return None
        name = _event_name(row.get("name"))
        if name is None:
            return None
        normalized.append((name, bucket))
    return normalized


DELIVERABLE_LOCK = "deliverable.json"
STALL_MIN = 45


def deliverable_lock():
    """The lane's deliverable lock (branches, why, by, at) or None. Owner,
    2026-09-11: "watchdog updated right now to keep you from fucking up":
    the lock names the branches the conductor may work on; the watchdog
    reports work aimed elsewhere (drift) and deliverables going quiet or
    stopping (stall), so the owner does not have to watch the conductor."""
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    try:
        lane = conf("LANE_NAME")
    except Exception:
        return None
    data = read_json(Path(state) / lane / DELIVERABLE_LOCK)
    if not isinstance(data, dict):
        return None
    branches = data.get("branches")
    if not isinstance(branches, list) or not branches:
        return None
    return {"branches": [str(b) for b in branches], "at": data.get("at"),
            "by": data.get("by"), "why": data.get("why")}


def record_branch(rec):
    lineage = rec.get("lineage") if isinstance(rec, dict) else None
    branch = lineage.get("branch") if isinstance(lineage, dict) else None
    return branch if isinstance(branch, str) and branch else None


class Watch:
    def __init__(self, args):
        self.args = args
        self.caps = job_caps()
        self.trunk = conf("TRUNK_BRANCH")
        self.tick = 0
        self.reported = set()
        self.settling_since = {}
        self.launcher_pairs = set()
        self.ci_seen = set()
        self.startup_backlog = {}
        self.silent_now = set()
        self.closed_cache = ClosedCache()
        self.closed_scanned = {}
        self.forge_reading = None
        self.forge_tick = None
        self.startup_error = None
        self.deviations = 0
        try:
            initial = entries(args.store, self.caps, self.closed_cache)
            if not args.report_existing:
                # Backlog is silent in every condition family, and the
                # silence belongs to the condition rather than to the
                # name (owner, 2026-09-16, through the Lead; LD-70): a
                # record already closed, a job directory already there
                # with no record copy, and one whose inputs cannot be
                # read say nothing while they stand as they were. Each
                # name keeps the kind and fingerprint it was found in,
                # so a change after start is a live condition again.
                self.startup_backlog = {
                    entry.name: (entry.kind, entry.fingerprint)
                    for entry in initial
                    if entry.kind in ("closed", "no-copy", "error")
                }
        except OSError as exc:
            self.startup_error = exc

    def once(self, key, line):
        if key not in self.reported:
            self.reported.add(key)
            say(line)
            return True
        return False

    def hold_silence(self, all_entries):
        """The names still standing in the condition they were backlog
        in, read once per tick from the same entries the tick scans."""
        self.silent_now = {
            e.name for e in all_entries
            if self.startup_backlog.get(e.name) == (e.kind, e.fingerprint)
        }

    def silent(self, record_id):
        """True while a record is still the backlog condition this
        watcher found at start: already closed, a job directory with no
        record copy, or one whose inputs cannot be read. Nothing about it
        is reported in any condition family unless --report-existing. A
        kind or fingerprint change after start is not that condition any
        more and speaks in every family — only what changes after start
        speaks (owner, 2026-09-16; LD-70)."""
        return record_id in self.silent_now

    def deviation(self, kind, record_id):
        if self.silent(record_id):
            return
        if self.once(
            ("deviation", kind, record_id),
            f"deviation {kind} {record_id}",
        ):
            self.deviations += 1

    def scan_deviations(self, entry):
        result = entry.rec.get("result")
        result = result if isinstance(result, dict) else {}
        envelope = result.get("envelope")
        envelope = envelope if isinstance(envelope, dict) else {}
        changed = result.get("changed_paths")
        changed = [p for p in changed if isinstance(p, str)] if isinstance(changed, list) else []
        bucket_class, bucket_paths = bucket_from(entry)
        limit = BUCKET_LIMITS.get(bucket_class)

        assumption_text = "\n".join(text_values({
            "note": envelope.get("note"),
            "findings": envelope.get("findings"),
        })).lower()
        if any(word in assumption_text for word in ASSUMPTION_WORDS):
            self.deviation("assumption", entry.name)

        if any(
            Path(path).name in LOCK_FILES and path not in bucket_paths
            for path in changed
        ):
            self.deviation("lock", entry.name)

        if bucket_class is not None and (
            any(path not in bucket_paths for path in changed)
            or (
                limit is not None
                and len(changed) > limit["files"] * BUCKET_OVERRUN_FACTOR
            )
        ):
            self.deviation("bucket", entry.name)

        landed_lines = result.get("landed_lines")
        if entry.job in ROUND_JOBS and limit is not None and (
            len(changed) > limit["files"]
            or (
                isinstance(landed_lines, (int, float))
                and landed_lines > limit["lines"]
            )
        ):
            self.deviation("size", entry.name)

        findings = envelope.get("findings")
        has_findings = isinstance(findings, list) and bool(findings)
        if envelope.get("status") in {"tripped", "invalid"} and (
            has_findings or changed
        ):
            self.deviation("envelope", entry.name)

        if entry.job == "author-tests":
            note = envelope.get("note")
            note_has_errors = isinstance(note, str) and ERROR_TEXT_RE.search(note)
            errors_key = ("deviation", "errors", entry.name)
            if errors_key not in self.reported and (
                note_has_errors
                or held_matches(
                    entry,
                    ("raw.out", "stdout", "stderr", "launcher.log"),
                    _error_stream_matches,
                )
            ):
                self.deviation("errors", entry.name)

        refused_key = ("deviation", "refused", entry.name)
        if refused_key not in self.reported and held_matches(
            entry,
            ("raw.out", "stdout", "stderr", "launcher.log"),
            _refusal_stream_matches,
        ):
            self.deviation("refused", entry.name)

        minutes = entry.minutes(entry.closed_at)
        minute_limit = CALIBRATION_MINUTES.get(bucket_class, {}).get(entry.job)
        if (
            minutes is not None
            and minute_limit is not None
            and minutes > minute_limit * MINUTES_OVERRUN_FACTOR
        ):
            self.deviation("minutes", entry.name)

    def scan_deliverable(self, all_entries):
        lock = deliverable_lock()
        if lock is None:
            return
        branches = lock["branches"]
        since = stamp_or_none(lock["at"]) if isinstance(lock.get("at"), str) else None
        latest = {}
        for e in all_entries:
            if e.kind in ("error", "no-copy"):
                continue
            branch = record_branch(e.rec)
            launched = stamp_or_none(e.rec.get("at", {}).get("launched")) if isinstance(e.rec.get("at"), dict) else None
            if branch in branches:
                stamp = e.rec.get("at", {}).get("closed") if e.kind == "closed" else None
                when = stamp_or_none(stamp) if isinstance(stamp, str) else None
                if e.kind != "closed":
                    latest[branch] = ("running", e.name, None)
                elif latest.get(branch, (None,))[0] != "running":
                    prev = latest.get(branch)
                    if prev is None or (when is not None and (prev[2] is None or when > prev[2])):
                        latest[branch] = ("closed", e.name, when)
                continue
            if since is not None and launched is not None and launched < since:
                continue
            if self.silent(e.name):
                continue
            if self.once(("drift", e.name), f"drift {e.name}: branch {branch or 'unknown'} outside the deliverable ({', '.join(branches)})"):
                self.deviations += 1
        for branch in branches:
            state = latest.get(branch)
            if state is None:
                anchor = since
                name = "the lock"
            elif state[0] == "running":
                continue
            else:
                anchor, name = state[2], state[1]
            if anchor is None:
                continue
            quiet = int((now() - anchor).total_seconds() // 60)
            if quiet >= STALL_MIN and self.once(("stall", branch, name), f"stall {branch}: no dispatch running, {quiet} min since {name}"):
                self.deviations += 1

    def scan_ladders(self):
        ladder_root = self.args.store.parent / "ladders"
        lock = deliverable_lock()
        deliverable_branches = lock["branches"] if lock else []
        try:
            paths = sorted(ladder_root.glob("*.json"))
        except OSError:
            return
        for path in paths:
            ladder = read_json(path)
            if ladder is None:
                continue
            record_id = ladder.get("id")
            if not isinstance(record_id, str) or not record_id:
                record_id = path.stem
            stages = ladder.get("stages")
            stages = stages if isinstance(stages, list) else []
            retried = any(
                isinstance(stage, dict)
                and isinstance(stage.get("attempt"), (int, float))
                and stage["attempt"] > 1
                for stage in stages
            )
            if ladder.get("status") == "stopped" or retried:
                self.deviation("retry", record_id)
            stopped = ladder.get("stopped")
            if ladder.get("branch") in deliverable_branches and ladder.get("status") == "stopped" and isinstance(stopped, dict):
                key = ("stall", record_id, stopped.get("stage"), stopped.get("reason"))
                if self.once(key, f"stall {ladder.get('branch')}: ladder {record_id} stopped {stopped.get('reason')} at {stopped.get('stage')}"):
                    self.deviations += 1

    def run_tick(self):
        self.tick += 1
        if self.startup_error is not None:
            error = self.startup_error
            self.startup_error = None
            raise error
        running = []
        settling = 0
        all_entries = list(entries(self.args.store, self.caps, self.closed_cache))
        self.hold_silence(all_entries)
        for e in all_entries:
            if e.kind == "error":
                self.deviation("unavailable", e.name)
                if not self.silent(e.name):
                    self.once(("error", e.name), f"watchdog error: InputUnavailable: job inputs unavailable for {e.name} (tick {self.tick}; the loop continues)")
                continue
            if e.kind == "no-copy":
                if not self.silent(e.name):
                    self.once(("no-copy", e.name), f"no record copy {e.name}: cannot judge (no record.json in the job directory)")
                continue
            scan_ok = True
            needs_scan = not self.silent(e.name) and (
                e.kind != "closed"
                or self.closed_scanned.get(e.name) != e.fingerprint
            )
            if needs_scan:
                try:
                    self.scan_deviations(e)
                except InputUnavailable:
                    scan_ok = False
                    self.closed_cache.pop(e.name, None)
                    self.deviation("unavailable", e.name)
                else:
                    if e.kind == "closed":
                        self.closed_scanned[e.name] = e.fingerprint
            if e.kind == "closed":
                if not self.silent(e.name):
                    self.once(("closed", e.name), f"closed {e.name} {e.summary()} after {e.minutes(e.closed_at)} min")
                if scan_ok and (needs_scan or not e.from_closed_cache):
                    cache_closed(
                        self.closed_cache,
                        e.name,
                        e.fingerprint,
                        e.closed_projection(),
                    )
                continue
            if e.kind == "settling":
                settling += 1
                first = self.settling_since.setdefault(e.name, self.tick)
                if self.tick - first + 1 >= SETTLING_TICKS:
                    try:
                        since = int((time.time() - e.exit_mtime) // 60)
                    except OSError:
                        since = "unknown"
                    self.once(("settling", e.name), f"exit written, not collected {e.name}: {since} min since exit, record still {e.rec.get('status')}")
                continue
            m = e.minutes()
            harness_alive = e.harness_alive()
            if harness_alive is None:
                self.once(
                    ("unobservable", e.name),
                    f"worker unobservable {e.name}: no usable worker PID, no exit written",
                )
                continue
            if harness_alive is False:
                self.once(("gone", e.name), f"harness gone {e.name} after {m} min: pid {e.state.get('pid')} dead, no exit written")
                continue
            running.append(e)
            if m is None:
                continue
            if e.wall_min is None:
                if m >= FALLBACK_WARN_MIN * self.args.warn_fraction:
                    self.once(("long", e.name), f"long {e.name}: {m} min running, wall unknown")
            elif m >= e.wall_min * self.args.warn_fraction:
                self.once(("long", e.name), f"long {e.name}: {m} min running, wall {int(e.wall_min)} min")
        self.scan_ladders()
        try:
            self.scan_deliverable(all_entries)
        except Exception as exc:
            say(f"watchdog error: deliverable scan {type(exc).__name__}: {exc} (tick {self.tick}; the loop continues)")
        launchers = self.launcher_count()
        if launchers is not None and launchers < len(running):
            pair = (launchers, len(running))
            if pair not in self.launcher_pairs:
                self.launcher_pairs.add(pair)
                say(f"launchers fewer than running records: {launchers} launch.py processes, {len(running)} running (heuristic: the record does not name its launcher)")
        forge_fresh = False
        if not self.args.no_forge and (self.tick - 1) % self.args.forge_every == 0:
            self.forge_reading = self.forge()
            self.forge_tick = self.tick
            forge_fresh = True
        if self.tick % self.args.heartbeat_every == 0:
            live = ", ".join(f"{e.name} {e.minutes()}m/{int(e.wall_min) if e.wall_min else '?'}" for e in running) or "none"
            launchers_txt = "unreachable" if launchers is None else launchers
            if self.args.no_forge:
                prs = "not watched"
            elif self.forge_reading is None:
                prs = "unreachable"
            else:
                n, unreachable = self.forge_reading
                notes = []
                if n == FORGE_LIMIT:
                    count = f"{FORGE_LIMIT}+"
                    notes.append("page full, total unmeasured")
                else:
                    count = str(n)
                if not forge_fresh:
                    notes.append(f"cached from tick {self.forge_tick}")
                if unreachable:
                    numbers = ", ".join(f"#{number}" for number in unreachable)
                    notes.append(f"checks unreachable for {numbers}")
                prs = count + (f" ({'; '.join(notes)})" if notes else "")
            say(f"heartbeat: running={live}; settling={settling}; launchers={launchers_txt}; open PRs into {self.trunk}={prs}; deviations={self.deviations}")

    def launcher_count(self):
        if shutil.which("pgrep") is None:
            return None
        out = run([
            "pgrep",
            "-fc",
            r"^(/([^[:space:]]*/)*)?python3(\.[0-9]+)?[[:space:]]+([^[:space:]]*/)?launch[.]py([[:space:]]|$)",
        ])
        if out is None:
            return 0
        try:
            return int(out.strip() or 0)
        except ValueError:
            return None

    def forge(self):
        deadline = time.monotonic() + FORGE_TIMEOUT
        raw = run([
            "gh", "pr", "list", "--base", self.trunk, "--state", "open",
            "--limit", str(FORGE_LIMIT), "--json", "number,headRefOid,headRefName",
        ], timeout=max(0, deadline - time.monotonic()))
        if raw is None:
            return None
        try:
            prs = json.loads(raw)
        except ValueError:
            return None
        if (
            not isinstance(prs, list)
            or len(prs) > FORGE_LIMIT
            or any(
                not isinstance(pr, dict)
                or not isinstance(pr.get("number"), int)
                or isinstance(pr.get("number"), bool)
                or pr["number"] <= 0
                or not isinstance(pr.get("headRefOid"), str)
                or SHA_RE.fullmatch(pr["headRefOid"]) is None
                for pr in prs
            )
        ):
            return None
        unreachable = []
        lock = deliverable_lock()
        for index, pr in enumerate(prs):
            number = pr.get("number")
            sha = pr.get("headRefOid")
            head = pr.get("headRefName")
            if (lock and isinstance(head, str) and head not in lock["branches"]
                    and self.once(("drift", "pr", number), f"drift PR #{number}: head {head} outside the deliverable ({', '.join(lock['branches'])})")):
                self.deviations += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                unreachable.extend(row["number"] for row in prs[index:])
                break
            checks = run(
                ["gh", "pr", "checks", str(number), "--json", "name,bucket"],
                timeout=remaining,
            )
            try:
                rows = json.loads(checks) if checks is not None else None
            except ValueError:
                rows = None
            normalized = _check_rows(rows)
            if normalized is None:
                unreachable.append(number)
                continue
            for name, bucket in normalized:
                if bucket in {"fail", "cancel"}:
                    key = (number, sha, name)
                    if key not in self.ci_seen:
                        self.ci_seen.add(key)
                        say(f"ci {bucket} #{number} at {sha}: {name}")
        return len(prs), unreachable

    def loop(self):
        ignored = (
            "unreachable"
            if self.startup_error is not None
            else str(len(self.startup_backlog))
        )
        say(f"watchdog armed: store {self.args.store}, {ignored} records ignored as backlog, tick {self.args.tick:g}s")
        while True:
            try:
                self.run_tick()
            except Exception as exc:
                # The loop must outlive any one tick; the error is the event.
                say(f"watchdog error: {type(exc).__name__}: {exc} (tick {self.tick}; the loop continues)")
            if self.args.once:
                return 0
            time.sleep(self.args.tick)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Watch the dispatch store and the forge; print one line per condition.")
    parser.add_argument("--store", help="the dispatch store (default: DISPATCH_JOBS, then XDG_STATE_HOME, then HOME)")
    parser.add_argument("--tick", help="seconds between ticks (default: WATCHDOG_TICK or 300)")
    parser.add_argument("--heartbeat-every", type=int, default=DEFAULT_HEARTBEAT, help="ticks between heartbeats")
    parser.add_argument("--forge-every", default=str(DEFAULT_FORGE_EVERY), help="ticks between forge readings")
    parser.add_argument("--warn-fraction", type=float, default=DEFAULT_WARN_FRACTION, help="fraction of the wall at which a run is long")
    parser.add_argument("--once", action="store_true", help="one tick, then exit 0")
    parser.add_argument("--report-existing", action="store_true", help="report the backlog once: records closed at start, directories with no record copy, and directories whose inputs are unreadable")
    parser.add_argument("--no-forge", action="store_true", help="do not ask the forge")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        return _main(argv)
    except Refusal as refusal:
        return emit(refusal)


def _main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    args.store = store_root(args.store)
    if not args.store.is_dir():
        refuse("store", "a dispatch store directory", f"nothing at {args.store}", "run a dispatch first or pass --store")
    raw_tick = args.tick if args.tick is not None else os.environ.get("WATCHDOG_TICK", str(DEFAULT_TICK))
    try:
        args.tick = float(raw_tick)
    except ValueError:
        args.tick = -1.0
    if args.tick <= 0:
        refuse("tick", "a positive number of seconds", repr(raw_tick), "set --tick or WATCHDOG_TICK to a positive number")
    if args.heartbeat_every <= 0 or not 0 < args.warn_fraction <= 1:
        refuse("arguments", "heartbeat-every above zero and warn-fraction in (0, 1]", f"{args.heartbeat_every}, {args.warn_fraction}", "fix the values")
    raw_forge_every = args.forge_every
    try:
        args.forge_every = int(raw_forge_every)
    except ValueError:
        args.forge_every = 0
    if args.forge_every <= 0:
        refuse("arguments", "forge-every as a positive integer", repr(raw_forge_every), "set --forge-every to a positive integer")
    return Watch(args).loop()


if __name__ == "__main__":
    sys.exit(main())
