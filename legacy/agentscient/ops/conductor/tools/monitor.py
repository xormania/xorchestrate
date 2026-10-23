#!/usr/bin/env python3
"""Configurable lane observations; inherits instrument-conventions.md."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings. The clock is imported as utc_now because main()
# binds a local named now.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import Refusal, identity, lane_name, refuse, state_root
    from instrument import now as utc_now
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

from dispatch_observation import DispatchRecord

HERE = Path(__file__).resolve()
DEFAULT_CONFIG = HERE.parent.parent / "monitor.example.json"
ID_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
BUCKETS = ("core", "graphql", "search")
LOCAL_JSON_BYTES = 1_048_576
PROCESS_OUTPUT_BYTES = 1_048_576
PROCESS_CMDLINE_BYTES = 65_536
SOURCE_REFERENCE_BYTES = 1_024
TIMESTAMP_BYTES = 128
MAX_METRIC_INTEGER = 9_223_372_036_854_775_807
READING_KEYS = {
    "subject", "state", "metrics", "generation", "observed_at", "evidence", "reason"
}


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            refuse("config", "unique JSON object keys", f"duplicate key {key}", "remove the duplicate")
        result[key] = value
    return result


def _object(value: object, where: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        refuse("config", f"{where} to be an object", type(value).__name__, "use the declared object shape")
    if set(value) != keys:
        refuse("config", f"{where} fields {sorted(keys)}", sorted(value), "add missing and remove unknown fields")
    return value


def _number(value: object, where: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        refuse("config", f"{where} to be a finite number", repr(value), "supply a finite JSON number")
    try:
        result = float(value)
    except OverflowError:
        refuse("config", f"{where} to be a finite number", repr(value), "supply a finite JSON number")
    if not math.isfinite(result):
        refuse("config", f"{where} to be a finite number", repr(value), "supply a finite JSON number")
    if minimum is not None and result < minimum:
        refuse("config", f"{where} >= {minimum:g}", repr(value), "raise the value to the declared bound")
    return result


def _enabled(entry: dict[str, object], where: str) -> None:
    if not isinstance(entry["enabled"], bool):
        refuse("config", f"{where}.enabled to be boolean", repr(entry["enabled"]), "use true or false")


def _registry(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        refuse("config", f"{name} to be an object registry", type(value).__name__, "use identifiers as object keys")
    for ident in value:
        if not isinstance(ident, str) or ID_RE.fullmatch(ident) is None:
            refuse("config", f"{name} identifiers matching {ID_RE.pattern}", repr(ident), "rename the identifier")
    return value


def _consumer(value: object, where: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not value.isprintable()
        or "/" in value
        or "\\" in value
    ):
        refuse("consumer", f"{where} to be 1..128 printable characters without path separators", repr(value), "supply an isolated consumer id")
    return value


def _path(value: object, where: str, base: Path) -> str:
    if not isinstance(value, str) or not value or not value.isprintable():
        refuse("config", f"{where} to be a nonempty path", repr(value), "supply the explicit source path")
    try:
        os.fsencode(value)
        path = Path(value)
        resolved = str((base / path).resolve() if not path.is_absolute() else path.resolve())
        encoded = json.dumps(resolved, ensure_ascii=True).encode("ascii")
        if len(encoded) > SOURCE_REFERENCE_BYTES:
            refuse("config", f"{where} to fit in {SOURCE_REFERENCE_BYTES} encoded bytes", "oversized path", "shorten the explicit source path")
        return resolved
    except Refusal:
        raise
    except (OSError, RuntimeError, UnicodeError, ValueError):
        refuse("config", f"{where} to be a valid path", repr(value), "supply the explicit source path")


def _dispatch_params(value: object, base: Path) -> dict[str, object]:
    params = _object(value, "dispatch params", {"store", "ids"})
    ids = params["ids"]
    valid_id = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}")
    if not isinstance(ids, list) or len(ids) > 256 or any(not isinstance(item, str) or valid_id.fullmatch(item) is None for item in ids):
        refuse("config", "dispatch params.ids to be at most 256 path-safe record ids", repr(ids), "supply explicit record basenames")
    if len(set(ids)) != len(ids):
        refuse("config", "dispatch params.ids to be unique", repr(ids), "remove duplicate record ids")
    return {"store": _path(params["store"], "dispatch params.store", base), "ids": list(ids)}


def _codex_params(value: object, base: Path) -> dict[str, object]:
    params = _object(value, "codex-usage params", {"path", "max_bytes"})
    bound = params["max_bytes"]
    if isinstance(bound, bool) or not isinstance(bound, int) or not 1024 <= bound <= 8_388_608:
        refuse("config", "codex-usage params.max_bytes to be an integer from 1024 through 8388608", repr(bound), "use the declared tail bound")
    return {"path": _path(params["path"], "codex-usage params.path", base), "max_bytes": bound}


def _github_params(value: object, _base: Path) -> dict[str, object]:
    params = _object(value, "github-rate params", {"timeout_s"})
    timeout = _number(params["timeout_s"], "github-rate params.timeout_s")
    if not 0 < timeout <= 30:
        refuse("config", "github-rate params.timeout_s in (0, 30]", repr(params["timeout_s"]), "use the declared subprocess deadline")
    return {"timeout_s": timeout}


def _branch_name(value: object, limit: int = 255) -> bool:
    return (
        isinstance(value, str) and 1 <= len(value) <= limit and value.isascii()
        and not value.startswith("-") and not value.endswith(".")
        and ".." not in value and "@{" not in value
        and not any(ord(char) <= 32 or ord(char) == 127 or char in "~^:?*[\\" for char in value)
        and all(part and not part.startswith(".") and not part.endswith(".lock") for part in value.split("/"))
    )


def _git_refs_params(value: object, base: Path) -> dict[str, object]:
    params = _object(value, "git-refs params", {"repo", "remote", "branches", "timeout_s"})
    remote = params["remote"]
    if not isinstance(remote, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", remote) is None:
        refuse("config", "git-refs params.remote to be a literal remote name", repr(remote), "supply a named Git remote")
    branches = params["branches"]
    if not isinstance(branches, list) or not 1 <= len(branches) <= 32 or not all(_branch_name(branch) for branch in branches):
        refuse("config", "git-refs params.branches to contain 1..32 literal branch names", repr(branches), "use valid short Git branch names without patterns")
    if len(set(branches)) != len(branches):
        refuse("config", "git-refs params.branches to be unique", repr(branches), "remove duplicate branches")
    timeout = _number(params["timeout_s"], "git-refs params.timeout_s")
    if not 0 < timeout <= 30:
        refuse("config", "git-refs params.timeout_s in (0, 30]", repr(params["timeout_s"]), "use the declared subprocess deadline")
    return {"repo": _path(params["repo"], "git-refs params.repo", base), "remote": remote, "branches": list(branches), "timeout_s": timeout}


def load_config(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        refuse("config", f"a readable UTF-8 registry at {path}", type(exc).__name__, "restore the registry or pass --config FILE")
    try:
        data = json.loads(text, object_pairs_hook=_pairs, parse_constant=lambda value: refuse("config", "finite JSON numbers", value, "replace the nonfinite value"))
    except Refusal:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError, RecursionError) as exc:
        refuse("config", f"valid JSON at {path}", f"{type(exc).__name__} at line {getattr(exc, 'lineno', '?')}", "repair the registry JSON")
    root = _object(data, "registry", {"version", "monitors", "schedules", "conditions", "routes"})
    if type(root["version"]) is not int or root["version"] != 1:
        refuse("config", "registry version 1", repr(root["version"]), "set version to 1")
    schedules = _registry(root["schedules"], "schedules")
    monitors = _registry(root["monitors"], "monitors")
    conditions = _registry(root["conditions"], "conditions")
    routes = _registry(root["routes"], "routes")
    normalized: dict[str, object] = {"version": 1, "schedules": {}, "monitors": {}, "conditions": {}, "routes": {}}
    for ident, raw in schedules.items():
        entry = _object(raw, f"schedules.{ident}", {"enabled", "every_s"})
        _enabled(entry, f"schedules.{ident}")
        every = _number(entry["every_s"], f"schedules.{ident}.every_s", minimum=1)
        normalized["schedules"][ident] = {"enabled": entry["enabled"], "every_s": every}
    base = path.resolve().parent
    for ident, raw in monitors.items():
        entry = _object(raw, f"monitors.{ident}", {"enabled", "adapter", "schedule", "params"})
        _enabled(entry, f"monitors.{ident}")
        adapter = entry["adapter"]
        if not isinstance(adapter, str) or adapter not in ADAPTERS:
            refuse("config", f"monitors.{ident}.adapter in {sorted(ADAPTERS)}", repr(adapter), "choose a registered observation adapter")
        schedule = entry["schedule"]
        if not isinstance(schedule, str) or schedule not in schedules:
            refuse("reference", f"monitors.{ident}.schedule to name a schedule", repr(schedule), "add the target or correct the reference")
        normalized["monitors"][ident] = {
            "enabled": entry["enabled"], "adapter": adapter, "schedule": schedule,
            "params": ADAPTERS[adapter][0](entry["params"], base),
        }
    for ident, raw in conditions.items():
        entry = _object(raw, f"conditions.{ident}", {"enabled", "monitor", "op", "field", "threshold", "cooldown_s"})
        _enabled(entry, f"conditions.{ident}")
        monitor = entry["monitor"]
        if not isinstance(monitor, str) or monitor not in monitors:
            refuse("reference", f"conditions.{ident}.monitor to name a monitor", repr(monitor), "add the target or correct the reference")
        op = entry["op"]
        if not isinstance(op, str) or op not in {"changed", "gt", "lt", "unavailable"}:
            refuse("config", f"conditions.{ident}.op to be changed, gt, lt, or unavailable", repr(op), "choose a declared operator")
        field = entry["field"]
        threshold = entry["threshold"]
        if op == "unavailable":
            if field is not None or threshold is not None:
                refuse("config", f"conditions.{ident} unavailable field and threshold to be null", repr((field, threshold)), "set both values to null")
        else:
            if not isinstance(field, str) or not field:
                refuse("config", f"conditions.{ident}.field to be nonempty", repr(field), "name a reading metric")
            if op == "changed" and threshold is not None:
                refuse("config", f"conditions.{ident}.threshold to be null for changed", repr(threshold), "set threshold to null")
            if op in {"gt", "lt"}:
                threshold = _number(threshold, f"conditions.{ident}.threshold")
        cooldown = _number(entry["cooldown_s"], f"conditions.{ident}.cooldown_s", minimum=0)
        normalized["conditions"][ident] = {
            "enabled": entry["enabled"], "monitor": monitor, "op": op,
            "field": field, "threshold": threshold, "cooldown_s": cooldown,
        }
    for ident, raw in routes.items():
        entry = _object(raw, f"routes.{ident}", {"enabled", "condition", "adapter", "consumer"})
        _enabled(entry, f"routes.{ident}")
        condition = entry["condition"]
        if not isinstance(condition, str) or condition not in conditions:
            refuse("reference", f"routes.{ident}.condition to name a condition", repr(condition), "add the target or correct the reference")
        adapter = entry["adapter"]
        if not isinstance(adapter, str) or adapter not in {"queue", "stdout", "unsupported"}:
            refuse("config", f"routes.{ident}.adapter to be queue, stdout, or unsupported", repr(adapter), "choose a declared delivery adapter")
        normalized["routes"][ident] = {
            "enabled": entry["enabled"], "condition": condition,
            "adapter": adapter, "consumer": _consumer(entry["consumer"], f"routes.{ident}.consumer"),
        }
    return normalized


def _stamp(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reading(subject: str, state: str, now: datetime, *, metrics: dict[str, object] | None = None, generation: object = None, evidence: object = None, reason: str | None = None) -> dict[str, object]:
    return {"subject": subject, "state": state, "metrics": metrics or {}, "generation": generation, "observed_at": _stamp(now), "evidence": evidence, "reason": reason}


def _read_fd(fd: int, limit: int, offset: int = 0) -> bytes | None:
    try:
        os.lseek(fd, offset, os.SEEK_SET)
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        return data if len(data) <= limit else None
    except OSError:
        return None


def _regular_bytes(path: Path, limit: int, *, tail: bool = False) -> tuple[str, bytes | None, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return "missing", None, 0
    except OSError:
        return "unavailable", None, 0
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return "unavailable", None, 0
        offset = max(0, info.st_size - limit) if tail else 0
        if not tail and info.st_size > limit:
            return "unavailable", None, 0
        data = _read_fd(fd, limit, offset)
        return ("ok", data, offset) if data is not None else ("unavailable", None, 0)
    except OSError:
        return "unavailable", None, 0
    finally:
        os.close(fd)


def _json_bytes(status: str, data: bytes | None) -> tuple[str, dict[str, object] | None]:
    if status != "ok" or data is None:
        return status, None
    try:
        value = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        return "unavailable", None
    return ("ok", value) if isinstance(value, dict) else ("unavailable", None)


def _json_file(path: Path) -> tuple[str, dict[str, object] | None]:
    status, data, _offset = _regular_bytes(path, LOCAL_JSON_BYTES)
    return _json_bytes(status, data)


def _job_directory(path: Path) -> tuple[str, int | None]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        return "ok", fd
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None


def _job_bytes(directory_fd: int, name: str, limit: int) -> tuple[str, bytes | None]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return "unavailable", None
        data = _read_fd(fd, limit)
        return ("ok", data) if data is not None else ("unavailable", None)
    except OSError:
        return "unavailable", None
    finally:
        os.close(fd)


def _json_job_file(directory_fd: int, name: str) -> tuple[str, dict[str, object] | None]:
    status, data = _job_bytes(directory_fd, name, LOCAL_JSON_BYTES)
    return _json_bytes(status, data)


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _finite_number(value: object) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value)
    except OverflowError:
        return False


def _bounded_text(value: object, limit: int = SOURCE_REFERENCE_BYTES) -> str | None:
    if not isinstance(value, str) or not value or not value.isprintable():
        return None
    return value if len(json.dumps(value, ensure_ascii=True).encode("ascii")) <= limit else None


def _process_status(pid: object, rec: dict[str, object], state: dict[str, object]) -> str:
    if not _positive_int(pid) or pid > 2_147_483_647:
        return "unknown"
    harness = rec.get("harness") if isinstance(rec.get("harness"), dict) else {}
    argv = harness.get("argv") if isinstance(harness.get("argv"), list) else []
    expected = _bounded_text(argv[0]) if argv else None
    snapshot = rec.get("snapshot") if isinstance(rec.get("snapshot"), dict) else {}
    expected_cwd = _bounded_text(snapshot.get("root"))
    session = rec.get("session") if isinstance(rec.get("session"), dict) else {}
    expected_session = _bounded_text(session.get("id"), 256)
    state_session = state.get("session") if isinstance(state.get("session"), dict) else {}
    if expected is None or expected_cwd is None or expected_session is None or state_session.get("id") != expected_session:
        return "unknown"
    proc = Path("/proc") / str(pid)
    if proc.is_dir():
        try:
            status, raw, _offset = _regular_bytes(proc / "cmdline", PROCESS_CMDLINE_BYTES)
            if status != "ok" or raw is None:
                return "unknown"
            cmdline = [os.fsdecode(token) for token in raw.split(b"\0") if token]
            cwd = (proc / "cwd").resolve(strict=True)
            recorded_cwd = Path(expected_cwd).resolve(strict=True)
        except (OSError, RuntimeError, UnicodeError):
            return "unknown"
        return "running" if expected in cmdline and cwd == recorded_cwd else "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "death"
    except PermissionError:
        return "unknown"
    except OSError:
        return "unknown"
    return "unknown"


def read_dispatch(params: dict[str, object], now: datetime) -> list[dict[str, object]]:
    store = Path(params["store"])
    rows = []
    for ident in params["ids"]:
        job_path = store / ident
        record_path = job_path / "record.json"
        state_path = job_path / "state.json"
        evidence = str(record_path)
        directory_status, directory_fd = _job_directory(job_path)
        if directory_status == "missing":
            rows.append(_reading(ident, "unknown", now, evidence=evidence, reason="record missing"))
            continue
        if directory_status != "ok" or directory_fd is None:
            rows.append(_reading(ident, "unavailable", now, evidence=evidence, reason="job directory unavailable"))
            continue
        try:
            status, rec = _json_job_file(directory_fd, "record.json")
            state_status, state = _json_job_file(directory_fd, "state.json")
            exit_status, _exit = _job_bytes(directory_fd, "exit", 64)
        finally:
            os.close(directory_fd)
        if status == "missing":
            rows.append(_reading(ident, "unknown", now, evidence=evidence, reason="record missing"))
            continue
        if status != "ok" or rec is None:
            rows.append(_reading(ident, "unavailable", now, evidence=evidence, reason="record unreadable or malformed"))
            continue
        state = state or {}
        observation = DispatchRecord(rec)
        metrics = observation.metrics()
        observed = observation.outcome()
        if observed is None:
            if exit_status == "ok":
                observed = "settling"
            elif exit_status == "unavailable":
                rows.append(_reading(ident, "unavailable", now, metrics=metrics, evidence=str(job_path / "exit"), reason="exit marker unavailable"))
                continue
            elif state_status == "unavailable":
                rows.append(_reading(ident, "unavailable", now, metrics=metrics, evidence=str(state_path), reason="state unreadable or malformed"))
                continue
            else:
                observed = _process_status(state.get("pid"), rec, state)
        metrics["status"] = observed
        attempts = rec.get("attempts") if isinstance(rec.get("attempts"), list) else []
        at = rec.get("at") if isinstance(rec.get("at"), dict) else {}
        launched_raw = attempts[-1].get("launched") if attempts and isinstance(attempts[-1], dict) else at.get("launched")
        launched, _timestamp_reason = _normalized_timestamp(launched_raw)
        attempt = state.get("attempt") if _positive_int(state.get("attempt")) else len(attempts)
        generation = {"attempt": attempt, "launched": launched}
        reading_state = "unknown" if observed == "unknown" else "ok"
        rows.append(_reading(ident, reading_state, now, metrics=metrics, generation=generation, evidence=evidence, reason="worker identity not provable" if observed == "unknown" else None))
    return rows


def _usage_int(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_METRIC_INTEGER else None


def _normalized_timestamp(value: object) -> tuple[str | None, str | None]:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isprintable()
        or len(value.encode("ascii")) > TIMESTAMP_BYTES
    ):
        return None, "event timestamp invalid or absent"
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None, "event timestamp invalid or absent"
        normalized = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError):
        return None, "event timestamp invalid or absent"
    if len(normalized.encode("ascii")) > TIMESTAMP_BYTES:
        return None, "event timestamp invalid or absent"
    return normalized, None


def read_codex_usage(params: dict[str, object], now: datetime) -> list[dict[str, object]]:
    path = Path(params["path"])
    bound = params["max_bytes"]
    status, data, offset = _regular_bytes(path, bound, tail=True)
    if status == "missing":
        return [_reading(str(path), "unknown", now, generation=str(path), evidence=str(path), reason="rollout missing")]
    if status != "ok" or data is None:
        return [_reading(str(path), "unavailable", now, generation=str(path), evidence=str(path), reason="rollout is not a readable regular file")]
    if offset:
        split = data.find(b"\n")
        data = b"" if split < 0 else data[split + 1 :]
    if data and not data.endswith(b"\n"):
        split = data.rfind(b"\n")
        data = data[: split + 1] if split >= 0 else b""
    last: dict[str, object] | None = None
    for raw in data.splitlines():
        try:
            entry = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "event_msg":
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            last = entry
    if last is None:
        return [_reading(str(path), "unknown", now, generation=str(path), evidence=str(path), reason="no complete token_count row in bounded tail")]
    payload = last["payload"]
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    totals = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
    recent = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
    names = {"input": "input_tokens", "cached": "cached_input_tokens", "output": "output_tokens", "total": "total_tokens"}
    metrics = {name: value for name, key in names.items() if (value := _usage_int(totals.get(key))) is not None}
    if "input" in metrics and metrics.get("cached", 0) > metrics["input"]:
        metrics.pop("cached", None)
    last_input = _usage_int(recent.get("input_tokens"))
    last_cached = _usage_int(recent.get("cached_input_tokens"))
    if last_input is not None and last_cached is not None and last_cached > last_input:
        last_cached = None
    window = _usage_int(info.get("model_context_window"))
    if last_input is not None:
        metrics["last_input"] = last_input
    if last_cached is not None:
        metrics["last_cached"] = last_cached
    if window is not None:
        metrics["context_window"] = window
    if metrics.get("input", 0) > 0 and "cached" in metrics:
        metrics["cache_fraction"] = metrics["cached"] / metrics["input"]
    if last_input is not None and window is not None and window > 0:
        metrics["context_fraction"] = last_input / window
    event_at, timestamp_reason = _normalized_timestamp(last.get("timestamp"))
    state = "ok" if any(key in metrics for key in names) else "unknown"
    evidence = {"path": str(path), "event_at": event_at}
    reason = timestamp_reason if state == "ok" else "retained usage fields invalid or absent"
    return [_reading(str(path), state, now, metrics=metrics, generation=str(path), evidence=evidence, reason=reason)]


def _bounded_process(argv: list[str], timeout: float, *, deadline: float | None = None, reject_stderr: bool = False, env: dict[str, str] | None = None) -> tuple[int | None, bytes | None, str | None]:
    proc: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    failure = None
    try:
        if deadline is not None and time.monotonic() >= deadline:
            return None, None, "process deadline exceeded"
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env,
        )
        if deadline is None:
            deadline = time.monotonic() + timeout
        for name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
            if stream is None:
                failure = "rate limit process pipes unavailable"
                break
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while failure is None and selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "rate limit process deadline exceeded"
                break
            ready = selector.select(remaining)
            if not ready:
                failure = "rate limit process deadline exceeded"
                break
            for key, _mask in ready:
                stream = key.fileobj
                name = key.data
                try:
                    chunk = os.read(stream.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                room = PROCESS_OUTPUT_BYTES + 1 - len(buffers[name])
                buffers[name].extend(chunk[:room])
                if len(buffers[name]) > PROCESS_OUTPUT_BYTES:
                    failure = f"rate limit {name} exceeded bounded output"
                    break
        if failure is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = "rate limit process deadline exceeded"
            else:
                try:
                    returncode = proc.wait(timeout=remaining)
                    if reject_stderr and buffers["stderr"]:
                        return returncode, None, "process reported a read failure"
                    return returncode, bytes(buffers["stdout"]), None
                except subprocess.TimeoutExpired:
                    failure = "rate limit process deadline exceeded"
    except (OSError, ValueError):
        failure = "rate limit process unavailable"
    finally:
        selector.close()
        if proc is not None and failure is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
    return None, None, failure


def read_github_rate(params: dict[str, object], now: datetime) -> list[dict[str, object]]:
    def unavailable(reason: str) -> list[dict[str, object]]:
        return [
            _reading(bucket, "unavailable", now, evidence="gh api rate_limit", reason=reason)
            for bucket in BUCKETS
        ]

    executable = shutil.which("gh")
    if executable is None:
        return unavailable("gh executable unavailable")
    returncode, stdout, failure = _bounded_process(
        [executable, "api", "rate_limit"], params["timeout_s"],
    )
    if failure is not None or stdout is None:
        return unavailable(failure or "rate limit process unavailable")
    if returncode:
        return unavailable(f"rate limit process exited {returncode}")
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return unavailable("rate limit response malformed")
    resources = data.get("resources") if isinstance(data, dict) and isinstance(data.get("resources"), dict) else {}
    rows = []
    for bucket in BUCKETS:
        value = resources.get(bucket)
        if not isinstance(value, dict):
            rows.append(_reading(bucket, "unknown", now, evidence="gh api rate_limit", reason="bucket missing"))
            continue
        limit = _usage_int(value.get("limit"))
        remaining = _usage_int(value.get("remaining"))
        reset = _usage_int(value.get("reset"))
        if limit is None or limit <= 0 or remaining is None or remaining > limit or reset is None:
            rows.append(_reading(bucket, "unknown", now, generation=reset, evidence="gh api rate_limit", reason="bucket fields invalid"))
            continue
        metrics = {"limit": limit, "remaining": remaining, "reset": reset, "remaining_fraction": remaining / limit}
        rows.append(_reading(bucket, "ok", now, metrics=metrics, generation=reset, evidence="gh api rate_limit"))
    return rows


def _git_ref_rows(stdout: bytes | None, selected: list[str]) -> dict[str, str] | None:
    if stdout is None:
        return None
    result = {}
    seen = set()
    try:
        text = stdout.decode("ascii")
        if text and not text.endswith("\n"):
            return None
        for line in text.splitlines():
            fields = line.split("\t")
            if len(fields) != 2:
                return None
            oid, ref = fields
            if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None or ref in seen:
                return None
            if not ref.startswith("refs/") or not _branch_name(ref, SOURCE_REFERENCE_BYTES):
                return None
            seen.add(ref)
            if ref in selected:
                result[ref] = oid
    except UnicodeError:
        return None
    return result


# Configuration a source repository may carry that would name a command to run,
# emptied for the observation, and the transports it may use.
GIT_OBSERVATION_CONFIG = (
    ("credential.helper", ""),
    ("core.askPass", ""),
    ("core.alternateRefsCommand", ""),
    ("protocol.allow", "never"),
    ("protocol.file.allow", "always"),
    ("protocol.http.allow", "always"),
    ("protocol.https.allow", "always"),
    ("protocol.ssh.allow", "always"),
)


def _observation_env() -> dict[str, str]:
    """Environment for a Git read that executes nothing the source configures.

    Inherited ``GIT_*`` variables are dropped, system and global configuration
    are disabled, the SSH command is fixed to ``ssh``, and the command-bearing
    keys above are overridden at the highest precedence, so the source
    repository's own configuration cannot name a program to run. A remote that
    needs any of them reads as unavailable.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_SSH_COMMAND="ssh",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_COUNT=str(len(GIT_OBSERVATION_CONFIG)))
    for index, (key, value) in enumerate(GIT_OBSERVATION_CONFIG):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def read_git_refs(params: dict[str, object], now: datetime) -> list[dict[str, object]]:
    deadline = time.monotonic() + params["timeout_s"]
    repo, remote = params["repo"], params["remote"]
    refs = [f"refs/heads/{branch}" for branch in params["branches"]]
    argv = ["git", "-C", repo]
    env = _observation_env()
    local_code, local_out, local_failure = _bounded_process(
        [*argv, "for-each-ref", "--format=%(objectname)%09%(refname)", "--", *refs],
        params["timeout_s"], deadline=deadline, reject_stderr=True, env=env,
    )
    local = _git_ref_rows(local_out, refs) if local_code == 0 and local_failure is None else None
    remote_code, remote_out, remote_failure = _bounded_process(
        [*argv, "ls-remote", "--refs", "--", remote, *refs],
        params["timeout_s"], deadline=deadline, env=env,
    )
    distant = _git_ref_rows(remote_out, refs) if remote_code == 0 and remote_failure is None else None
    rows = []
    for ref in refs:
        local_oid = local.get(ref) if local is not None else None
        remote_oid = distant.get(ref) if distant is not None else None
        reason = None
        if local is None:
            freshness = reason = "local-unavailable"
        elif distant is None:
            freshness = reason = "remote-unavailable"
        elif local_oid is None:
            freshness = "missing-both" if remote_oid is None else "missing-local"
        elif remote_oid is None:
            freshness = "missing-remote"
        else:
            freshness = "equal" if local_oid == remote_oid else "different"
        rows.append(_reading(
            ref, "unavailable" if reason else "ok", now,
            metrics={"local_oid": local_oid, "remote_oid": remote_oid, "freshness": freshness},
            generation=[repo, remote, ref], evidence={"repo": repo, "remote": remote, "ref": ref}, reason=reason,
        ))
    return rows


ADAPTERS = {
    "dispatch": (_dispatch_params, read_dispatch),
    "codex-usage": (_codex_params, read_codex_usage),
    "github-rate": (_github_params, read_github_rate),
    "git-refs": (_git_refs_params, read_git_refs),
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (monitor TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, next_due REAL);
CREATE TABLE IF NOT EXISTS condition_state (
 condition_id TEXT NOT NULL, subject TEXT NOT NULL, fingerprint TEXT NOT NULL,
 generation TEXT NOT NULL, epoch INTEGER NOT NULL, baseline TEXT,
 matching INTEGER NOT NULL, pending INTEGER NOT NULL, last_emit REAL, sequence INTEGER NOT NULL,
 PRIMARY KEY(condition_id, subject)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, route TEXT NOT NULL, consumer TEXT NOT NULL,
 condition_id TEXT NOT NULL, monitor TEXT NOT NULL, subject TEXT NOT NULL,
 state TEXT NOT NULL, summary TEXT NOT NULL, evidence TEXT, observed_at TEXT NOT NULL,
 delivery TEXT NOT NULL, dedup_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS cursors (consumer TEXT PRIMARY KEY, event_id INTEGER NOT NULL);
"""


def _fingerprint(value: object) -> str:
    packed = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(packed.encode()).hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _paths_overlap(first: Path, second: Path) -> bool:
    return _inside(first, second) or _inside(second, first)


def _repository() -> Path | None:
    for parent in HERE.parents:
        marker = parent / ".git"
        try:
            mode = marker.lstat().st_mode
            if stat.S_ISDIR(mode):
                git_dir = marker
            elif stat.S_ISREG(mode):
                with marker.open(encoding="utf-8") as handle:
                    pointer = handle.read(4097)
                line = pointer.rstrip("\r\n")
                if len(pointer) > 4096 or "\n" in line or "\r" in line or not line.startswith("gitdir: "):
                    continue
                target = line.removeprefix("gitdir: ")
                if not target:
                    continue
                git_dir = Path(target)
                if not git_dir.is_absolute():
                    git_dir = parent / git_dir
            else:
                continue
        except (OSError, UnicodeError, ValueError):
            continue
        if (git_dir / "HEAD").is_file():
            return parent
    return None


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            break
        except OSError:
            return True
        if stat.S_ISLNK(info.st_mode):
            return True
    return False


def _git_source_directories(repo: Path) -> list[Path]:
    """Protect worktrees and their external Git storage without executing Git."""
    protected = [repo]
    try:
        if not repo.is_dir():
            return protected
        for root in (repo, *repo.parents):
            marker = root / ".git"
            if marker.is_dir():
                git_dir = marker.resolve()
            else:
                status, data, _ = _regular_bytes(marker, SOURCE_REFERENCE_BYTES)
                if status == "missing":
                    continue
                if status != "ok" or data is None:
                    raise ValueError("unreadable Git pointer")
                pointer = data.decode("utf-8").rstrip("\r\n")
                if not pointer.startswith("gitdir: ") or not pointer[8:] or "\n" in pointer or "\r" in pointer:
                    # A readable invalid pointer names no external storage.
                    # Protect this root and let observation report unavailable.
                    protected.append(root)
                    break
                git_dir = (root / pointer[8:]).resolve()
            protected.extend((root, git_dir))
            status, data, _ = _regular_bytes(git_dir / "commondir", SOURCE_REFERENCE_BYTES)
            if status == "missing":
                break
            if status != "ok" or data is None:
                raise ValueError("unreadable Git common directory")
            pointer = data.decode("utf-8").rstrip("\r\n")
            if not pointer or "\n" in pointer or "\r" in pointer:
                raise ValueError("invalid Git common directory")
            protected.append((git_dir / pointer).resolve())
            break
    except (OSError, UnicodeError, ValueError, RuntimeError):
        refuse("state", "inspectable Git source storage for state separation", "unavailable Git storage path", "restore the source metadata before writing monitor state")
    return protected


def _validate_state(state: Path, config_path: Path, config: dict[str, object]) -> Path:
    state = Path(os.path.abspath(state))
    resolved = state.resolve(strict=False)
    repository = _repository()
    if repository is not None and _inside(resolved, repository):
        refuse("state", "monitor state outside the source repository", str(resolved), "pass an isolated state directory")
    if _has_symlink_component(state):
        refuse("state", "a state path without symlinked components", str(state), "use the real isolated state directory")
    try:
        if state.exists() and not state.is_dir():
            refuse("state", "a state directory or absent path", str(state), "remove the non-directory destination")
        db = state / "monitor.db"
        if db.is_symlink() or (db.exists() and not db.is_file()):
            refuse("state", "monitor.db to be a regular file or absent", str(db), "remove the unsafe database destination")
    except OSError:
        refuse("state", "an inspectable state destination", str(state), "repair the destination path")
    protected_files = [config_path.resolve()]
    protected_directories = []
    for monitor in config["monitors"].values():
        params = monitor["params"]
        if monitor["adapter"] == "dispatch":
            protected_directories.append(Path(params["store"]).resolve(strict=False))
        elif monitor["adapter"] == "codex-usage":
            protected_files.append(Path(params["path"]).resolve(strict=False))
        elif monitor["adapter"] == "git-refs":
            protected_directories.extend(_git_source_directories(Path(params["repo"])))
    state_storage = []
    if any(monitor["adapter"] == "git-refs" for monitor in config["monitors"].values()):
        existing = next(parent for parent in (resolved, *resolved.parents) if parent.is_dir())
        # A linked worktree shares source Git storage even when its path is
        # outside the configured worktree. State must not write there either.
        state_storage = _git_source_directories(existing)[2:]
    for source in protected_directories:
        if any(_paths_overlap(path, source) for path in (resolved, *state_storage)):
            refuse("state", "state disjoint from configured source stores", str(resolved), "choose an isolated state directory")
    for source in protected_files:
        if _inside(source, resolved) or source == resolved / "monitor.db":
            refuse("state", "state disjoint from configuration and source files", str(resolved), "choose an isolated state directory")
    return state


def _connect(path: Path, *, write: bool) -> sqlite3.Connection | None:
    db = path / "monitor.db"
    if write:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or not path.is_dir() or db.is_symlink() or (db.exists() and not db.is_file()):
                refuse("state", "a real state directory and regular monitor.db", str(db), "remove the unsafe destination")
            conn = instrument.connect_store(db, mode="rwc", timeout=30)
            conn.executescript(SCHEMA)
            return conn
        except (OSError, sqlite3.Error) as exc:
            refuse("state", f"a writable SQLite state at {db}", type(exc).__name__, "repair permissions or pass --state DIR")
    if not db.exists():
        return None
    try:
        if db.is_symlink() or not db.is_file():
            refuse("state", "monitor.db to be a regular file", str(db), "remove the unsafe database destination")
        return instrument.connect_store(db, timeout=30)
    except sqlite3.Error as exc:
        refuse("state", f"a readable SQLite state at {db}", type(exc).__name__, "repair or replace the state database")


def _claim_due(conn: sqlite3.Connection, config: dict[str, object], now_epoch: float) -> list[str]:
    due = []
    monitors = config["monitors"]
    schedules = config["schedules"]
    try:
        with instrument.transaction(conn):
            for ident, monitor in monitors.items():
                schedule = schedules[monitor["schedule"]]
                fingerprint = _fingerprint({"monitor": monitor, "schedule": schedule})
                row = conn.execute("SELECT fingerprint,next_due FROM schedules WHERE monitor=?", (ident,)).fetchone()
                active = monitor["enabled"] and schedule["enabled"]
                if not active:
                    conn.execute("INSERT INTO schedules(monitor,fingerprint,next_due) VALUES(?,?,NULL) ON CONFLICT(monitor) DO UPDATE SET fingerprint=excluded.fingerprint,next_due=NULL", (ident, fingerprint))
                    continue
                clock_rewound = row is not None and row[1] is not None and row[1] - now_epoch > schedule["every_s"]
                if row is None or row[0] != fingerprint or row[1] is None or now_epoch >= row[1] or clock_rewound:
                    due.append(ident)
                    conn.execute("INSERT INTO schedules(monitor,fingerprint,next_due) VALUES(?,?,?) ON CONFLICT(monitor) DO UPDATE SET fingerprint=excluded.fingerprint,next_due=excluded.next_due", (ident, fingerprint, now_epoch + schedule["every_s"]))
    except sqlite3.Error as exc:
        refuse("state", "valid writable monitor state", type(exc).__name__, "repair or replace the state database")
    return due


def _truncate(text: str, limit: int = 240) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def _event_summary(condition: dict[str, object], reading: dict[str, object]) -> str:
    field = condition["field"]
    if field is None:
        return _truncate(f"state={reading['state']}")
    value = reading["metrics"].get(field)
    return _truncate(f"{field}={json.dumps(value, ensure_ascii=True, separators=(',', ':'))}")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _evaluate(conn: sqlite3.Connection, config: dict[str, object], observations: dict[str, list[dict[str, object]]], now_epoch: float) -> list[dict[str, object]]:
    emitted: list[dict[str, object]] = []
    conditions = config["conditions"]
    monitors = config["monitors"]
    routes = config["routes"]
    try:
        with instrument.transaction(conn):
            known_conditions = set(conditions)
            stale = conn.execute("SELECT DISTINCT condition_id FROM condition_state").fetchall()
            for (condition_id,) in stale:
                if condition_id not in known_conditions:
                    conn.execute(
                        "UPDATE condition_state SET fingerprint=?,baseline=NULL,matching=0,pending=0,last_emit=NULL WHERE condition_id=?",
                        (_fingerprint({"removed": condition_id}), condition_id),
                    )
            for ident, condition in conditions.items():
                monitor_id = condition["monitor"]
                fingerprint = _fingerprint({"condition": condition, "source": monitors[monitor_id]})
                if not condition["enabled"] or not monitors[monitor_id]["enabled"]:
                    conn.execute(
                        "UPDATE condition_state SET fingerprint=?,baseline=NULL,matching=0,pending=0,last_emit=NULL WHERE condition_id=?",
                        (_fingerprint({"inactive": fingerprint}), ident),
                    )
                    continue
                if monitor_id not in observations:
                    continue
                for reading in observations[monitor_id]:
                    if set(reading) != READING_KEYS:
                        raise ValueError("reader returned an invalid reading shape")
                    subject = reading["subject"]
                    generation = _canonical(reading["generation"])
                    row = conn.execute(
                        "SELECT fingerprint,generation,epoch,baseline,matching,pending,last_emit,sequence FROM condition_state WHERE condition_id=? AND subject=?",
                        (ident, subject),
                    ).fetchone()
                    if row is None:
                        epoch, baseline, matching, pending, last_emit, sequence = 1, None, False, False, None, 0
                    elif row[0] != fingerprint or row[1] != generation:
                        epoch, baseline, matching, pending, last_emit, sequence = row[2] + 1, None, False, False, None, row[7]
                    else:
                        epoch, baseline, matching, pending, last_emit, sequence = row[2], row[3], bool(row[4]), bool(row[5]), row[6], row[7]
                    op = condition["op"]
                    field = condition["field"]
                    known = field is None or field in reading["metrics"]
                    current = reading["metrics"].get(field) if field is not None else None
                    if op in {"gt", "lt"}:
                        numeric = _finite_number(current)
                        matches = bool(reading["state"] == "ok" and known and numeric and (current > condition["threshold"] if op == "gt" else current < condition["threshold"]))
                    elif op == "unavailable":
                        matches = reading["state"] == "unavailable"
                    else:
                        current_json = _canonical(current) if known else None
                        matches = bool(known and (baseline is None or current_json != baseline))
                    entered = matches and not matching
                    if entered:
                        pending = True
                        sequence += 1
                    if not matches:
                        pending = False
                    eligible = last_emit is None or now_epoch - last_emit >= condition["cooldown_s"]
                    should_emit = matches and pending and eligible
                    if should_emit:
                        active_routes = [
                            (route_id, route) for route_id, route in routes.items()
                            if route["enabled"] and route["condition"] == ident
                        ]
                        while any(
                            conn.execute(
                                "SELECT 1 FROM events WHERE dedup_key=?",
                                (_fingerprint([ident, subject, epoch, sequence, route_id]),),
                            ).fetchone()
                            for route_id, _route in active_routes
                        ):
                            sequence += 1
                        for route_id, route in active_routes:
                            delivery = "unsupported" if route["adapter"] == "unsupported" else ("stdout" if route["adapter"] == "stdout" else "queued")
                            dedup = _fingerprint([ident, subject, epoch, sequence, route_id])
                            evidence = _canonical(reading["evidence"])
                            summary = _event_summary(condition, reading)
                            cursor = conn.execute(
                                "INSERT OR IGNORE INTO events(route,consumer,condition_id,monitor,subject,state,summary,evidence,observed_at,delivery,dedup_key) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (route_id, route["consumer"], ident, monitor_id, subject, reading["state"], summary, evidence, reading["observed_at"], delivery, dedup),
                            )
                            if cursor.rowcount:
                                event_id = cursor.lastrowid
                                emitted.append({"id": event_id, "route": route_id, "consumer": route["consumer"], "condition": ident, "monitor": monitor_id, "subject": subject, "state": reading["state"], "summary": summary, "evidence": reading["evidence"], "observed_at": reading["observed_at"], "delivery": delivery})
                        pending = False
                        last_emit = now_epoch
                        if op == "changed":
                            baseline = current_json
                            matches = False
                    elif op == "changed" and known and not matches:
                        baseline = current_json
                    conn.execute(
                        "INSERT INTO condition_state(condition_id,subject,fingerprint,generation,epoch,baseline,matching,pending,last_emit,sequence) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(condition_id,subject) DO UPDATE SET fingerprint=excluded.fingerprint,generation=excluded.generation,epoch=excluded.epoch,baseline=excluded.baseline,matching=excluded.matching,pending=excluded.pending,last_emit=excluded.last_emit,sequence=excluded.sequence",
                        (ident, subject, fingerprint, generation, epoch, baseline, int(matches), int(pending), last_emit, sequence),
                    )
    except (sqlite3.Error, TypeError, ValueError) as exc:
        refuse("state", "valid monitor state and adapter readings", type(exc).__name__, "repair state or the named reader")
    return emitted


def run_once(config: dict[str, object], state: Path, now: datetime, *, clock_epoch: float | None = None) -> tuple[dict[str, int], list[dict[str, object]]]:
    conn = _connect(state, write=True)
    try:
        due_clock = now.timestamp() if clock_epoch is None else clock_epoch
        due = _claim_due(conn, config, due_clock)
        observed: dict[str, list[dict[str, object]]] = {}

        def observe(ident: str) -> tuple[str, list[dict[str, object]]]:
            monitor = config["monitors"][ident]
            try:
                rows = ADAPTERS[monitor["adapter"]][1](monitor["params"], now)
            except (ArithmeticError, LookupError, OSError, TypeError, ValueError) as exc:
                rows = [_reading(ident, "unavailable", now, evidence=ident, reason=f"reader {type(exc).__name__}")]
            return ident, rows

        if due:
            with ThreadPoolExecutor(max_workers=min(4, len(due)), thread_name_prefix="monitor") as pool:
                futures = [pool.submit(observe, ident) for ident in due]
                for future in as_completed(futures):
                    ident, rows = future.result()
                    observed[ident] = rows
        events = _evaluate(conn, config, observed, due_clock)
        count = sum(len(rows) for rows in observed.values())
        unavailable = sum(row["state"] == "unavailable" for rows in observed.values() for row in rows)
        return {"observations": count, "events": len(events), "unavailable": unavailable}, events
    finally:
        conn.close()


def _event_from_row(row: tuple[object, ...]) -> dict[str, object]:
    return {"id": row[0], "route": row[1], "consumer": row[2], "condition": row[3], "monitor": row[4], "subject": row[5], "state": row[6], "summary": row[7], "evidence": json.loads(row[8]), "observed_at": row[9], "delivery": row[10]}


def _packed(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8") + b"\n"


def digest(state: Path, consumer: str, limit: int, max_bytes: int, ack: bool) -> bytes:
    conn = _connect(state, write=ack)
    if conn is None:
        return _packed({"events": [], "more": False})
    try:
        try:
            with instrument.transaction(conn) if ack else nullcontext():
                cursor_row = conn.execute("SELECT event_id FROM cursors WHERE consumer=?", (consumer,)).fetchone()
                cursor = cursor_row[0] if cursor_row else 0
                rows = conn.execute(
                    "SELECT id,route,consumer,condition_id,monitor,subject,state,summary,evidence,observed_at,delivery FROM events WHERE consumer=? AND delivery IN ('queued','unsupported') AND id>? ORDER BY id LIMIT ?",
                    (consumer, cursor, limit + 1),
                ).fetchall()
                events: list[dict[str, object]] = []
                for row in rows[:limit]:
                    candidate = [*events, _event_from_row(row)]
                    if len(_packed({"events": candidate, "more": False})) > max_bytes:
                        break
                    events = candidate
                more_row = conn.execute(
                    "SELECT 1 FROM events WHERE consumer=? AND delivery IN ('queued','unsupported') AND id>? LIMIT 1",
                    (consumer, events[-1]["id"] if events else cursor),
                ).fetchone()
                payload = {"events": events, "more": more_row is not None}
                body = _packed(payload)
                if len(body) > max_bytes:
                    refuse("arguments", f"--max-bytes large enough for digest metadata ({len(body)})", max_bytes, "raise --max-bytes")
                if ack and events:
                    conn.execute(
                        "INSERT INTO cursors(consumer,event_id) VALUES(?,?) ON CONFLICT(consumer) DO UPDATE SET event_id=MAX(cursors.event_id,excluded.event_id)",
                        (consumer, events[-1]["id"]),
                    )
            return body
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            refuse("state", "a valid monitor database", type(exc).__name__, "repair or replace the state database")
    finally:
        conn.close()


def _integer(value: str, option: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except ValueError:
        refuse("arguments", f"{option} to be an integer", value, "supply a decimal integer")
    if not minimum <= number <= maximum:
        refuse("arguments", f"{option} from {minimum} through {maximum}", number, "use the declared bound")
    return number


def _clock(value: str) -> datetime:
    if not value.isascii() or not value.isprintable() or len(value.encode("ascii")) > TIMESTAMP_BYTES:
        refuse("arguments", f"--now to fit in {TIMESTAMP_BYTES} printable ASCII bytes", "invalid timestamp", "supply a bounded injected clock")
    try:
        parsed = datetime.fromisoformat(value)
    except (OverflowError, ValueError):
        refuse("arguments", "--now to be an ISO timestamp with timezone", value, "supply an injected UTC clock")
    if parsed.tzinfo is None:
        refuse("arguments", "--now to include a timezone", value, "append Z or an offset")
    try:
        return parsed.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        refuse("arguments", "--now to normalize within the supported UTC range", value, "supply a non-boundary injected clock")


def _global(argv: list[str]) -> tuple[Path, Path | None, str, list[str]]:
    config = DEFAULT_CONFIG
    state = None
    index = 0
    while index < len(argv) and argv[index] in {"--config", "--state"}:
        option = argv[index]
        if index + 1 >= len(argv):
            refuse("arguments", f"a value after {option}", "missing", "supply the path")
        if option == "--config":
            config = Path(argv[index + 1]).resolve()
        else:
            state = Path(os.path.abspath(argv[index + 1]))
        index += 2
    if index >= len(argv):
        refuse("arguments", "check, run, or digest", "no verb", "choose one documented verb")
    return config, state, argv[index], argv[index + 1 :]


def _state_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    lane = lane_name()
    if ID_RE.fullmatch(lane) is None:
        refuse("conf-key", f"LANE_NAME matching {ID_RE.pattern}", repr(lane), "use a namespace identifier")
    base = Path(os.path.abspath(state_root()))
    target = base / lane / "monitor"
    if not _inside(target.resolve(strict=False), base.resolve(strict=False)):
        refuse("state", "default monitor state confined below XDG state", str(target), "remove escaping symlinks")
    return target


def _parse_run(args: list[str]) -> tuple[bool, str | None]:
    once = args.count("--once")
    loop = args.count("--loop")
    if once + loop != 1:
        refuse("arguments", "exactly one of --once or --loop", repr(args), "choose one run mode")
    rest = [item for item in args if item not in {"--once", "--loop"}]
    now = None
    if rest:
        if len(rest) == 2 and rest[0] == "--now":
            now = rest[1]
        else:
            refuse("arguments", "run --once|--loop [--now ISO]", repr(args), "use documented run options")
    if loop and now is not None:
        refuse("arguments", "--now only with --once", "--loop --now", "remove the injected clock")
    return bool(loop), now


def _parse_digest(args: list[str]) -> tuple[str, int, int, bool]:
    consumer = None
    limit, max_bytes, ack = 10, 4096, False
    index = 0
    while index < len(args):
        option = args[index]
        if option == "--ack":
            if ack:
                refuse("arguments", "--ack at most once", "duplicate --ack", "remove the duplicate")
            ack = True
            index += 1
            continue
        if option not in {"--consumer", "--limit", "--max-bytes"} or index + 1 >= len(args):
            refuse("arguments", "digest --consumer ID [--limit N] [--max-bytes N] [--ack]", repr(args), "use documented digest options")
        value = args[index + 1]
        if option == "--consumer":
            if consumer is not None:
                refuse("arguments", "one --consumer", "duplicate --consumer", "remove the duplicate")
            consumer = value
        elif option == "--limit":
            limit = _integer(value, "--limit", 1, 1000)
        else:
            max_bytes = _integer(value, "--max-bytes", 32, 8_388_608)
        index += 2
    if consumer is None:
        refuse("consumer", "--consumer ID", "missing", "name the isolated consumer")
    return _consumer(consumer, "--consumer"), limit, max_bytes, ack


def main(argv: list[str] | None = None) -> int:
    try:
        config_path, explicit_state, verb, args = _global(list(sys.argv[1:] if argv is None else argv))
        if verb not in {"check", "run", "digest"}:
            refuse("arguments", "check, run, or digest", verb, "choose one documented verb")
        config = load_config(config_path)
        if verb == "check":
            if args:
                refuse("arguments", "check with no verb options", repr(args), "remove the extra arguments")
            print(json.dumps({"valid": True, "monitors": len(config["monitors"]), "schedules": len(config["schedules"]), "conditions": len(config["conditions"]), "routes": len(config["routes"])}, separators=(",", ":")))
            return 0
        if verb == "digest":
            consumer, limit, max_bytes, ack = _parse_digest(args)
            if ack:
                identity()
            state = _validate_state(_state_path(explicit_state), config_path, config)
            sys.stdout.buffer.write(digest(state, consumer, limit, max_bytes, ack))
            return 0
        loop, injected = _parse_run(args)
        identity()
        once_now = _clock(injected) if injected else utc_now()
        state = _validate_state(_state_path(explicit_state), config_path, config)
        if not loop:
            counts, events = run_once(config, state, once_now)
            for event in events:
                if event["delivery"] == "stdout":
                    print(json.dumps(event, ensure_ascii=True, separators=(",", ":")))
            print(json.dumps(counts, separators=(",", ":")))
            return 0
        # Persist epoch-shaped deadlines, but advance the live clock only by
        # monotonic elapsed time. A restart translates them through wall time.
        wall_anchor = utc_now().timestamp()
        monotonic_anchor = time.monotonic()
        while True:
            try:
                cycle = load_config(config_path)
                _validate_state(state, config_path, cycle)
                now = utc_now()
                live_epoch = wall_anchor + time.monotonic() - monotonic_anchor
                counts, events = run_once(cycle, state, now, clock_epoch=live_epoch)
                for event in events:
                    if event["delivery"] == "stdout":
                        print(json.dumps(event, ensure_ascii=True, separators=(",", ":")), flush=True)
                time.sleep(1 if counts["observations"] == 0 else 0.1)
            except Refusal as exc:
                print(exc.line(), file=sys.stderr)
                return 3
    except KeyboardInterrupt:
        return 3
    except Refusal as exc:
        print(exc.line(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
