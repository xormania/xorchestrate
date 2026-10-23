"""Bounded, read-only queries for the conductor lane MCP projection."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, imported by lane_mcp.py and lane_refs.py beside it, or as
# a copy a test staged with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import Refusal, refuse
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

sys.dont_write_bytecode = True

from dispatch_observation import DispatchRecord  # noqa: E402

SCHEMA = "agentscient.lane/v1"
CONFIG_BYTES = 65_536
SOURCE_BYTES = 1_048_576
QUERY_BYTES = 16_384
ALERT_BYTES = 4_096
FINDINGS_BYTES = 8_192
MAX_INTEGER = 9_223_372_036_854_775_807
MAX_JSON_DEPTH = 1_024
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
REVISION_RE = re.compile(r"[0-9a-f]{16}")
CURSOR_RE = re.compile(r"([0-9a-f]{16}):([1-9][0-9]{0,8})")
JOB_NAME_RE = re.compile(r"[a-z][a-z0-9-]{0,63}")
SLOT_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
ALLOWED_SLOTS = {"ref", "base", "diff", "into", "out", "inputs", "scope"}
CONFIG_KEYS = {
    "version",
    "dispatch_store",
    "job_ids",
    "monitor_state",
    "consumer",
    "usage_rollout",
}
CONFIG_KEYS_V2 = CONFIG_KEYS | {"job_registry"}
COUNT_KEYS = ("p1", "p2", "p3", "opinions")
SPEND_KEYS = ("input", "cached", "output", "total", "out", "runs")
EVENT_COLUMNS = (
    "id",
    "route",
    "consumer",
    "condition",
    "monitor",
    "subject",
    "state",
    "summary",
    "evidence",
    "observed_at",
    "delivery",
)


def _pairs(kind: str):
    def collect(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                refuse(
                    kind,
                    "unique JSON object keys",
                    f"duplicate key {key}",
                    "remove the duplicate key",
                )
            result[key] = value
        return result

    return collect


def _json_depth_exceeds(raw: bytes, limit: int = MAX_JSON_DEPTH) -> bool:
    depth = 0
    quoted = False
    escaped = False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
            continue
        if byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > limit:
                return True
        elif byte in (93, 125):
            depth -= 1
    return False


def decode_object(raw: str, where: str = "arguments") -> dict[str, object]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_pairs(where),
            parse_constant=lambda item: refuse(
                where, "finite JSON values", item, "replace the nonfinite value"
            ),
        )
    except Refusal:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError) as exc:
        refuse(where, "a valid JSON object", type(exc).__name__, "supply valid JSON")
    if not isinstance(value, dict):
        refuse(where, "a JSON object", type(value).__name__, "supply an object")
    return value


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def canonical(value: object) -> str:
    return canonical_bytes(value).decode("ascii")


def _bounded_regular(path: Path, limit: int, *, follow: bool) -> tuple[str, bytes | None]:
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    if not follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > limit:
            return "unavailable", None
        chunks = bytearray()
        while len(chunks) <= limit:
            chunk = os.read(descriptor, min(65_536, limit + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > limit:
            return "unavailable", None
        return "ok", bytes(chunks)
    except OSError:
        return "unavailable", None
    finally:
        os.close(descriptor)


def _source_path(value: object, name: str, base: Path) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or not value.isprintable()
    ):
        refuse(
            "config",
            f"{name} to be null or a printable path of at most 1024 UTF-8 bytes",
            repr(value),
            "supply an explicit bounded source path or null",
        )
    try:
        if len(value.encode("utf-8")) > 1_024:
            raise UnicodeError
        return os.path.abspath(value if os.path.isabs(value) else base / value)
    except (OSError, UnicodeError, ValueError):
        refuse("config", f"{name} to be a valid path", repr(value), "repair the path")


def _consumer(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not value.isascii()
        or not value.isprintable()
        or "/" in value
        or "\\" in value
    ):
        refuse(
            "config",
            "consumer to be 1..128 printable ASCII characters without path separators",
            repr(value),
            "supply an isolated consumer id",
        )
    return value


def load_config(path: Path) -> dict[str, object]:
    # Parse v3 under its lexical limits; preserve every legacy parser/refusal.
    status, raw = _bounded_regular(Path(os.path.abspath(path)), CONFIG_BYTES, follow=True)
    if status == "ok" and raw is not None:
        v3 = re.search(rb'"version"\s*:\s*3\b|"profiles"\s*:|"discovery"\s*:', raw) is not None
        try:
            parsed = strict_json(raw)
            v3 = v3 or (isinstance(parsed, dict) and parsed.get('version') == 3)
            if v3:
                return validate_discovery_config(parsed, Path(os.path.abspath(path)).parent)
        except (Refusal, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            if v3:
                discovery_config_refusal()
    return _load_legacy_config(path, status, raw)


def _load_legacy_config(path: Path, status: str, raw: bytes | None) -> dict[str, object]:
    absolute = Path(os.path.abspath(path))
    if status != "ok" or raw is None:
        refuse(
            "config",
            f"a regular UTF-8 config no larger than {CONFIG_BYTES} bytes",
            status,
            "restore the config or pass --config FILE",
        )
    try:
        text = raw.decode("utf-8")
        root = json.loads(
            text,
            object_pairs_hook=_pairs("config"),
            parse_constant=lambda item: refuse(
                "config", "finite JSON values", item, "replace the nonfinite value"
            ),
        )
    except Refusal:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError) as exc:
        refuse("config", "valid UTF-8 JSON", type(exc).__name__, "repair the config")
    if not isinstance(root, dict):
        refuse(
            "config",
            f"exact keys {sorted(CONFIG_KEYS)}",
            type(root).__name__,
            "add missing and remove unknown keys",
        )
    version = root.get("version")
    if type(version) is not int or version not in {1, 2}:
        refuse("config", "version 1 or 2", repr(version), "set a supported version")
    expected_keys = CONFIG_KEYS if version == 1 else CONFIG_KEYS_V2
    if set(root) != expected_keys:
        refuse(
            "config",
            f"exact keys {sorted(expected_keys)}",
            sorted(root),
            "add missing and remove unknown keys",
        )
    ids = root["job_ids"]
    if (
        not isinstance(ids, list)
        or len(ids) > 20
        or any(not isinstance(item, str) or ID_RE.fullmatch(item) is None for item in ids)
    ):
        refuse(
            "config",
            f"job_ids to be at most 20 ids matching {ID_RE.pattern}",
            repr(ids),
            "supply the declared unique identifier list",
        )
    if len(set(ids)) != len(ids):
        refuse("config", "unique job_ids", repr(ids), "remove duplicate ids")
    base = absolute.parent
    config = {
        "version": version,
        "dispatch_store": _source_path(root["dispatch_store"], "dispatch_store", base),
        "job_ids": list(ids),
        "monitor_state": _source_path(root["monitor_state"], "monitor_state", base),
        "consumer": _consumer(root["consumer"]),
        "usage_rollout": _source_path(root["usage_rollout"], "usage_rollout", base),
    }
    if version == 2:
        config["job_registry"] = _source_path(
            root["job_registry"], "job_registry", base
        )
    return config


def check_result(config: dict[str, object]) -> dict[str, object]:
    return {"valid": True, "tools": {1: 4, 2: 7, 3: 11}[config["version"]]}


def _base(name: str, state: str, reason: str | None = None) -> dict[str, object]:
    return {"schema": SCHEMA, "query": name, "state": state, "reason": reason}


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if len(value.encode("utf-8")) <= 256 else None
    except UnicodeError:
        return None


def _aggregate(value: object, keys: tuple[str, ...]) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int | float] = {}
    for key in keys:
        item = value.get(key)
        valid_int = type(item) is int and 0 <= item <= MAX_INTEGER
        valid_float = (
            type(item) is float and math.isfinite(item) and 0 <= item <= MAX_INTEGER
        )
        if valid_int or valid_float:
            result[key] = item
    return result


def _read_record(
    config: dict[str, object], ident: str
) -> tuple[str, str | None, str | None, bytes | None, dict[str, object] | None]:
    evidence = (
        str(Path(config["dispatch_store"]) / ident / "record.json")
        if config["dispatch_store"] is not None
        else None
    )
    if config["dispatch_store"] is None:
        return "unknown", "dispatch store is not configured", evidence, None, None
    store = Path(config["dispatch_store"])
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        store_fd = os.open(store, flags)
    except FileNotFoundError:
        return "unknown", "dispatch store is missing", evidence, None, None
    except OSError:
        return "unavailable", "dispatch store is unavailable", evidence, None, None
    try:
        try:
            job_fd = os.open(ident, flags, dir_fd=store_fd)
        except FileNotFoundError:
            return "unknown", "record is missing", evidence, None, None
        except OSError:
            return "unavailable", "job directory is unavailable", evidence, None, None
        try:
            file_flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            file_flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open("record.json", file_flags, dir_fd=job_fd)
            except FileNotFoundError:
                return "unknown", "record is missing", evidence, None, None
            except OSError:
                return "unavailable", "record is unavailable", evidence, None, None
            try:
                details = os.fstat(descriptor)
                if not stat.S_ISREG(details.st_mode) or details.st_size > SOURCE_BYTES:
                    raise ValueError
                raw = bytearray()
                while len(raw) <= SOURCE_BYTES:
                    chunk = os.read(
                        descriptor, min(65_536, SOURCE_BYTES + 1 - len(raw))
                    )
                    if not chunk:
                        break
                    raw.extend(chunk)
                if len(raw) > SOURCE_BYTES:
                    raise ValueError
                if _json_depth_exceeds(raw):
                    raise ValueError
                source = bytes(raw)
                record = json.loads(source)
                if not isinstance(record, dict):
                    raise ValueError
            except (
                json.JSONDecodeError,
                OSError,
                RecursionError,
                UnicodeError,
                ValueError,
            ):
                return (
                    "unavailable",
                    "record is unreadable or malformed",
                    evidence,
                    None,
                    None,
                )
            finally:
                os.close(descriptor)
        finally:
            os.close(job_fd)
    finally:
        os.close(store_fd)
    return "ok", None, evidence, source, record


def _record_summary(
    record: dict[str, object], ident: str, evidence: str | None
) -> dict[str, object]:
    observation = DispatchRecord(record)
    nested = observation.result
    envelope = observation.envelope
    session = record.get("session") if isinstance(record.get("session"), dict) else {}
    attempts = record.get("attempts")
    model = record.get("model")
    if isinstance(model, dict):
        model = _text(model.get("ran")) or _text(model.get("requested"))
    else:
        model = _text(model)
    return {
        "id": ident,
        "recorded_status": _text(record.get("status")),
        "model": model,
        "head": _text(nested.get("head")),
        "verdict": _text(envelope.get("verdict")),
        "counts": _aggregate(envelope.get("counts"), COUNT_KEYS),
        "changed_count": observation.changed_count,
        "attempt_count": len(attempts) if isinstance(attempts, list) else None,
        "spend": _aggregate(session.get("spend"), SPEND_KEYS),
        "evidence": evidence,
    }


def _record(config: dict[str, object], ident: str) -> dict[str, object]:
    name = "lane_record"
    state, reason, evidence, _raw, record = _read_record(config, ident)
    result = _base(name, state, reason)
    if record is None:
        result["record"] = None
        return result
    summary = _record_summary(record, ident, evidence)
    result = _base(name, "ok")
    result["record"] = summary
    return result


def _monitor_db(config: dict[str, object]) -> tuple[str, Path | None]:
    source = config["monitor_state"]
    if source is None:
        return "disabled", None
    state_path = Path(source)
    try:
        details = state_path.lstat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        return "unavailable", None
    database = state_path / "monitor.db"
    try:
        details = database.lstat()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        return "unavailable", None
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(database, flags)
    except OSError:
        return "unavailable", None
    os.close(descriptor)
    return "ok", database


def _event(row: tuple[object, ...]) -> dict[str, object]:
    if len(row) != len(EVENT_COLUMNS):
        raise ValueError
    event = dict(zip(EVENT_COLUMNS, row, strict=True))
    if type(event["id"]) is not int or event["id"] <= 0:
        raise ValueError
    for key in ("route", "consumer", "condition", "monitor", "state"):
        if not isinstance(event[key], str) or len(event[key].encode("utf-8")) > 256:
            raise ValueError
    if (
        not isinstance(event["subject"], str)
        or len(event["subject"].encode("utf-8")) > 1_024
    ):
        raise ValueError
    if (
        not isinstance(event["summary"], str)
        or len(event["summary"].encode("utf-8")) > 240
        or not isinstance(event["observed_at"], str)
        or len(event["observed_at"].encode("utf-8")) > 256
        or event["delivery"] not in {"queued", "unsupported"}
    ):
        raise ValueError
    if not isinstance(event["evidence"], str):
        raise ValueError
    event["evidence"] = json.loads(event["evidence"])
    return event


def _alerts(config: dict[str, object], after: int, limit: int) -> dict[str, object]:
    name = "lane_alerts"
    status, database = _monitor_db(config)
    if status in {"disabled", "missing"}:
        result = _base(name, "unknown", "monitor database is not armed")
        result.update({"events": [], "more": False, "next_after": after})
        return result
    if status != "ok" or database is None:
        result = _base(name, "unavailable", "monitor database is unavailable")
        result.update({"events": [], "more": False, "next_after": after})
        return result
    try:
        connection = instrument.connect_store(database, timeout=0)
        try:
            rows = connection.execute(
                "SELECT id,route,consumer,condition_id,monitor,subject,state,"
                "summary,evidence,observed_at,delivery FROM events "
                "WHERE consumer=? AND delivery IN ('queued','unsupported') "
                "AND id>? ORDER BY id LIMIT ?",
                (config["consumer"], after, limit + 1),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        result = _base(name, "unavailable", "monitor database is unreadable or corrupt")
        result.update({"events": [], "more": False, "next_after": after})
        return result
    events: list[dict[str, object]] = []
    for row in rows[:limit]:
        try:
            event = _event(row)
        except (json.JSONDecodeError, RecursionError, TypeError, UnicodeError, ValueError):
            if not events:
                result = _base(
                    name, "unavailable", "monitor database is unreadable or corrupt"
                )
                result.update({"events": [], "more": True, "next_after": after})
                return result
            break
        candidate = [*events, event]
        probe = _base(name, "ok")
        probe.update(
            {
                "events": candidate,
                "more": len(rows) > len(candidate),
                "next_after": candidate[-1]["id"],
            }
        )
        if len(canonical_bytes(probe)) > ALERT_BYTES:
            if not events:
                result = _base(name, "unavailable", "next alert exceeds 4096 bytes")
                result.update({"events": [], "more": True, "next_after": after})
                return result
            break
        events = candidate
    result = _base(name, "ok")
    result.update(
        {
            "events": events,
            "more": len(rows) > len(events),
            "next_after": events[-1]["id"] if events else after,
        }
    )
    return result


def _usage_int(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= MAX_INTEGER else None


def _timestamp(value: object) -> tuple[str | None, str | None]:
    reason = "event timestamp invalid or absent"
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isprintable()
        or len(value.encode("ascii")) > 128
    ):
        return None, reason
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None, reason
        normalized = parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError):
        return None, reason
    return (normalized, None) if len(normalized) <= 128 else (None, reason)


def _usage(config: dict[str, object]) -> dict[str, object]:
    name = "lane_usage"
    source = config["usage_rollout"]
    if source is None:
        result = _base(name, "unknown", "usage rollout is not configured")
        result.update({"metrics": {}, "evidence": {"path": None, "event_at": None}})
        return result
    path = Path(source)
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        result = _base(name, "unknown", "usage rollout is missing")
        result.update({"metrics": {}, "evidence": {"path": str(path), "event_at": None}})
        return result
    except OSError:
        result = _base(name, "unavailable", "usage rollout is unavailable")
        result.update({"metrics": {}, "evidence": {"path": str(path), "event_at": None}})
        return result
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise OSError
        offset = max(0, details.st_size - SOURCE_BYTES)
        os.lseek(descriptor, offset, os.SEEK_SET)
        data = bytearray()
        while len(data) <= SOURCE_BYTES:
            chunk = os.read(descriptor, min(65_536, SOURCE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > SOURCE_BYTES:
            raise OSError
    except OSError:
        result = _base(name, "unavailable", "usage rollout is unavailable")
        result.update({"metrics": {}, "evidence": {"path": str(path), "event_at": None}})
        return result
    finally:
        os.close(descriptor)
    raw = bytes(data)
    if offset:
        split = raw.find(b"\n")
        raw = b"" if split < 0 else raw[split + 1 :]
    if raw and not raw.endswith(b"\n"):
        split = raw.rfind(b"\n")
        raw = raw[: split + 1] if split >= 0 else b""
    last: dict[str, object] | None = None
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, RecursionError, UnicodeError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "event_msg":
            continue
        payload = entry.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "token_count":
            last = entry
    if last is None:
        result = _base(name, "unknown", "no complete token_count row in bounded tail")
        result.update({"metrics": {}, "evidence": {"path": str(path), "event_at": None}})
        return result
    payload = last["payload"]
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    totals = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
    recent = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
    names = {
        "input": "input_tokens",
        "cached": "cached_input_tokens",
        "output": "output_tokens",
        "total": "total_tokens",
    }
    metrics = {
        name_: value
        for name_, key in names.items()
        if (value := _usage_int(totals.get(key))) is not None
    }
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
    event_at, timestamp_reason = _timestamp(last.get("timestamp"))
    state = "ok" if any(key in metrics for key in names) else "unknown"
    reason = timestamp_reason if state == "ok" else "retained usage fields invalid or absent"
    result = _base(name, state, reason)
    result.update({"metrics": metrics, "evidence": {"path": str(path), "event_at": event_at}})
    return result


def _revision(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:16]


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return value if len(value.encode("utf-8")) <= limit else None
    except UnicodeError:
        return None


def _finding_entry(value: object, number: int) -> dict[str, object]:
    if not isinstance(value, dict):
        return {
            "n": number,
            "severity": None,
            "where": None,
            "claim": None,
            "reproduce": None,
            "omitted": ["entry"],
        }
    entry: dict[str, object] = {"n": number}
    omitted: list[str] = []
    severity = value.get("severity")
    if isinstance(severity, str) and severity in ("p1", "p2", "p3"):
        entry["severity"] = severity
    else:
        entry["severity"] = None
        if severity is not None:
            omitted.append("severity")
    for field, limit in (("where", 256), ("claim", 1024), ("reproduce", 1024)):
        source = value.get(field)
        projected = _bounded_text(source, limit)
        entry[field] = projected
        if source is not None and projected is None:
            omitted.append(field)
    entry["omitted"] = omitted
    return entry


def _findings_empty(name: str, state: str, reason: str) -> dict[str, object]:
    result = _base(name, state, reason)
    result.update(
        {
            "record": None,
            "note": None,
            "note_bytes": None,
            "findings": [],
            "findings_total": None,
            "page": None,
        }
    )
    return result


def _findings_note(envelope: dict[str, object]) -> tuple[str | None, int | None, str | None]:
    if "note" not in envelope or envelope["note"] is None:
        return None, None, None
    note = envelope["note"]
    if not isinstance(note, str):
        return None, None, "note is not a string"
    try:
        length = len(note.encode("utf-8"))
    except UnicodeError:
        return None, None, "note is not valid Unicode"
    return (note if length <= 2048 else None), length, None


def _findings_page_payload(
    record: dict[str, object],
    note: str | None,
    note_bytes: int | None,
    findings: list[dict[str, object]],
    total: int,
    start: int,
    revision: str,
    reason: str | None,
) -> dict[str, object]:
    count = len(findings)
    last = start + count - 1
    more = last < total
    result = _base("lane_findings", "ok", reason)
    result.update(
        {
            "record": record,
            "note": note,
            "note_bytes": note_bytes,
            "findings": findings,
            "findings_total": total,
            "page": {
                "from": start if count else None,
                "to": last if count else None,
                "count": count,
                "more": more,
                "next_cursor": f"{revision}:{last + 1}" if more else None,
            },
        }
    )
    return result


def _findings(config: dict[str, object], ident: str, cursor: str | None) -> dict[str, object]:
    state, reason, evidence, raw, source = _read_record(config, ident)
    if source is None or raw is None:
        return _findings_empty("lane_findings", state, reason or "record is unavailable")
    revision = _revision(raw)
    nested = source.get("result") if isinstance(source.get("result"), dict) else {}
    envelope = nested.get("envelope") if isinstance(nested.get("envelope"), dict) else {}
    summary = _record_summary(source, ident, evidence)
    record = {
        "id": ident,
        "job": _text(source.get("job")),
        "stage": _text(source.get("stage")),
        "recorded_status": summary["recorded_status"],
        "verdict": summary["verdict"],
        "counts": summary["counts"],
        "revision": revision,
        "evidence": evidence,
    }
    note, note_bytes, note_reason = _findings_note(envelope)
    source_findings = envelope.get("findings")
    if not isinstance(source_findings, list):
        result = _base("lane_findings", "unavailable", "envelope findings is not a list")
        result.update(
            {
                "record": record,
                "note": note,
                "note_bytes": note_bytes,
                "findings": [],
                "findings_total": None,
                "page": None,
            }
        )
        return result
    total = len(source_findings)
    start = 1
    if cursor is not None:
        match = CURSOR_RE.fullmatch(cursor)
        if match is None:
            raise AssertionError
        held_revision, raw_start = match.groups()
        if held_revision != revision:
            refuse(
                "cursor",
                f"cursor for revision {revision}",
                held_revision,
                "restart from the first page",
            )
        start = int(raw_start)
        if start > total:
            refuse(
                "cursor",
                "an index within findings_total",
                start,
                "supply an index within findings_total",
            )
    page_note = note if cursor is None else None
    page_reason = note_reason
    frame = _findings_page_payload(
        record, None, note_bytes, [], total, start, revision, page_reason
    )
    if len(canonical_bytes(frame)) > FINDINGS_BYTES:
        frame.update(
            {
                "state": "unavailable",
                "reason": "page frame exceeds 8192 bytes",
                "page": None,
            }
        )
        return frame
    if page_note is not None:
        with_note = _findings_page_payload(
            record, page_note, note_bytes, [], total, start, revision, page_reason
        )
        if len(canonical_bytes(with_note)) > FINDINGS_BYTES:
            page_note = None
            page_reason = "note exceeds page"
    findings: list[dict[str, object]] = []
    for number in range(start, total + 1):
        entry = _finding_entry(source_findings[number - 1], number)
        candidate = _findings_page_payload(
            record,
            page_note,
            note_bytes,
            [*findings, entry],
            total,
            start,
            revision,
            page_reason,
        )
        if len(canonical_bytes(candidate)) <= FINDINGS_BYTES:
            findings.append(entry)
            continue
        if findings:
            break
        for field in ("reproduce", "claim", "where"):
            if entry[field] is not None:
                entry[field] = None
                if field not in entry["omitted"]:
                    entry["omitted"].append(field)
            candidate = _findings_page_payload(
                record,
                page_note,
                note_bytes,
                [entry],
                total,
                start,
                revision,
                page_reason,
            )
            if len(canonical_bytes(candidate)) <= FINDINGS_BYTES:
                findings.append(entry)
                break
        if findings:
            break
        if page_note is not None:
            page_note = None
            page_reason = "note exceeds page"
            candidate = _findings_page_payload(
                record,
                page_note,
                note_bytes,
                [entry],
                total,
                start,
                revision,
                page_reason,
            )
            if len(canonical_bytes(candidate)) <= FINDINGS_BYTES:
                findings.append(entry)
                break
        failed = _findings_page_payload(
            record, None, note_bytes, [], total, start, revision, None
        )
        failed.update(
            {
                "state": "unavailable",
                "reason": "page frame exceeds 8192 bytes",
                "page": None,
            }
        )
        return failed
    return _findings_page_payload(
        record,
        page_note,
        note_bytes,
        findings,
        total,
        start,
        revision,
        page_reason,
    )


def _dispatch_store_state(config: dict[str, object]) -> tuple[str, str | None]:
    source = config["dispatch_store"]
    if source is None:
        return "unknown", "dispatch store is not configured"
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(Path(source), flags)
    except FileNotFoundError:
        return "unknown", "dispatch store is missing"
    except OSError:
        return "unavailable", "dispatch store is unavailable"
    os.close(descriptor)
    return "ok", None


def _changes(
    config: dict[str, object], checkpoint: dict[str, str]
) -> dict[str, object]:
    ids = config["job_ids"]
    changed: list[dict[str, object]] = []
    unchanged: list[str] = []
    unknown: list[str] = []
    unavailable: list[str] = []
    current: dict[str, str] = {}
    state, reason = _dispatch_store_state(config)
    if state != "ok":
        if state == "unknown":
            unknown.extend(ids)
        else:
            unavailable.extend(ids)
    else:
        for ident in ids:
            rec_state, _rec_reason, evidence, raw, source = _read_record(config, ident)
            if rec_state == "unknown":
                unknown.append(ident)
                continue
            if rec_state != "ok" or raw is None or source is None:
                unavailable.append(ident)
                continue
            revision = _revision(raw)
            current[ident] = revision
            if checkpoint.get(ident) == revision:
                unchanged.append(ident)
                continue
            summary = _record_summary(source, ident, evidence)
            changed.append(
                {
                    "id": ident,
                    "revision": revision,
                    "recorded_status": summary["recorded_status"],
                    "verdict": summary["verdict"],
                    "evidence": evidence,
                }
            )
    result = _base("lane_changes", state, reason)
    result.update(
        {
            "configured_ids": ids,
            "changed": changed,
            "unchanged": unchanged,
            "unknown": unknown,
            "unavailable": unavailable,
            "checkpoint": current,
        }
    )
    return result


def _job_parts(name: object, value: object) -> tuple[str | None, dict[str, object] | None, list[str], bool]:
    try:
        valid_name = isinstance(name, str) and JOB_NAME_RE.fullmatch(name) is not None
        if valid_name:
            name.encode("utf-8")
    except UnicodeError:
        valid_name = False
    if not valid_name or not isinstance(value, dict):
        return None, None, [], False
    prompt = value.get("prompt")
    if isinstance(prompt, str):
        try:
            prompt.encode("utf-8")
            prompt_valid = bool(prompt)
            slots = sorted(set(SLOT_RE.findall(prompt)))
        except UnicodeError:
            prompt_valid = False
            slots = []
    else:
        prompt_valid = False
        slots = []
    dispatchable = (
        value.get("adapter") == "harness"
        and value.get("role") in ("read", "write")
        and value.get("snapshot") == "whole"
        and not value.get("withheld")
        and prompt_valid
        and set(slots).issubset(ALLOWED_SLOTS)
    )
    return name, value, slots, dispatchable


def _job_row(name: object, value: object) -> dict[str, object]:
    projected_name, source, slots, dispatchable = _job_parts(name, value)
    if source is None:
        return {
            "name": None,
            "adapter": None,
            "role": None,
            "dispatchable": False,
            "slots": [],
        }
    return {
        "name": projected_name,
        "adapter": _text(source.get("adapter")),
        "role": _text(source.get("role")),
        "dispatchable": dispatchable,
        "slots": slots,
    }


def _job_card(name: str, value: object) -> dict[str, object]:
    projected_name, source, slots, dispatchable = _job_parts(name, value)
    if source is None:
        source = {}
    prompt = source.get("prompt")
    try:
        prompt_bytes = len(prompt.encode("utf-8")) if isinstance(prompt, str) else None
    except UnicodeError:
        prompt_bytes = None
    constraints = source.get("constraints")
    if isinstance(constraints, list):
        projected_constraints = [
            _bounded_text(item, 256) for item in constraints[:16]
        ]
    else:
        projected_constraints = []
    caps = source.get("caps") if isinstance(source.get("caps"), dict) else {}
    timeout = caps.get("timeout")
    if type(timeout) is int:
        valid_timeout = timeout >= 0
    elif type(timeout) is float:
        valid_timeout = math.isfinite(timeout) and timeout >= 0
    else:
        valid_timeout = False
    return {
        "name": projected_name,
        "adapter": _text(source.get("adapter")),
        "role": _text(source.get("role")),
        "snapshot": _text(source.get("snapshot")),
        "dispatchable": dispatchable,
        "slots": slots,
        "deliverable": _bounded_text(source.get("deliverable"), 512),
        "constraints": projected_constraints,
        "timeout": timeout if valid_timeout else None,
        "prompt_bytes": prompt_bytes,
    }


def _jobs(config: dict[str, object], name: str | None) -> dict[str, object]:
    source = config.get("job_registry")
    path = Path(source) if source is not None else None
    evidence = {"path": str(path) if path is not None else None, "revision": None}
    if path is None:
        result = _base("lane_jobs", "unknown", "job registry is not configured")
        result.update({"evidence": evidence, "jobs": [], "job": None})
        return result
    status, raw = _bounded_regular(path, SOURCE_BYTES, follow=False)
    if status == "missing":
        result = _base("lane_jobs", "unknown", "job registry is missing")
        result.update({"evidence": evidence, "jobs": [], "job": None})
        return result
    if status != "ok" or raw is None:
        result = _base("lane_jobs", "unavailable", "job registry is unavailable")
        result.update({"evidence": evidence, "jobs": [], "job": None})
        return result
    try:
        registry = json.loads(raw.decode("utf-8"))
        if not isinstance(registry, dict):
            raise ValueError
    except (json.JSONDecodeError, RecursionError, UnicodeError, ValueError):
        result = _base("lane_jobs", "unavailable", "job registry is unreadable or malformed")
        result.update({"evidence": evidence, "jobs": [], "job": None})
        return result
    evidence["revision"] = _revision(raw)
    if name is None and len(registry) > 64:
        result = _base("lane_jobs", "unavailable", "registry lists more than 64 jobs")
        result.update({"evidence": evidence, "jobs": [], "job": None})
        return result
    result = _base("lane_jobs", "ok")
    if name is None:
        result.update(
            {
                "evidence": evidence,
                "jobs": [_job_row(key, value) for key, value in registry.items()],
                "job": None,
            }
        )
    else:
        result.update(
            {
                "evidence": evidence,
                "jobs": [],
                "job": _job_card(name, registry[name]) if name in registry else None,
            }
        )
        if name not in registry:
            result["reason"] = "job is not declared in the registry"
    return result


def _arguments(value: object, keys: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not set(value).issubset(keys):
        found = sorted(value) if isinstance(value, dict) else type(value).__name__
        refuse(
            "arguments",
            f"{name} fields drawn from {sorted(keys)}",
            found,
            "remove unknown fields and use the declared types",
        )
    return value


def query(config: dict[str, object], name: str, arguments: object,
          reading: dict[str, object] | None = None) -> dict[str, object]:
    if name in DISCOVERY_QUERIES and config["version"] == 3:
        args = discovery_arguments(config, name, arguments)
        return discovery_query(config, name, args, reading or {
            "state": "unavailable", "reason": "unreadable", "sources": []
        })
    if name == "lane_record":
        args = _arguments(arguments, {"id"}, name)
        if set(args) != {"id"} or not isinstance(args.get("id"), str):
            refuse("arguments", "lane_record object with string id", repr(args), "supply {\"id\":ID}")
        ident = args["id"]
        if ID_RE.fullmatch(ident) is None or ident not in config["job_ids"]:
            refuse("arguments", "id in configured job_ids", repr(ident), "select a configured id")
        result = _record(config, ident)
    elif name == "lane_alerts":
        args = _arguments(arguments, {"after", "limit"}, name)
        after = args.get("after", 0)
        limit = args.get("limit", 5)
        if type(after) is not int or after < 0:
            refuse("arguments", "after integer >= 0", repr(after), "supply a nonnegative cursor")
        if type(limit) is not int or not 1 <= limit <= 10:
            refuse("arguments", "limit integer from 1 through 10", repr(limit), "supply a bounded page size")
        result = _alerts(config, after, limit)
    elif name == "lane_usage":
        args = _arguments(arguments, set(), name)
        if args:
            raise AssertionError
        result = _usage(config)
    elif name == "lane_brief":
        args = _arguments(arguments, set(), name)
        if args:
            raise AssertionError
        records = [_record(config, ident) for ident in config["job_ids"]]
        result = _base(name, "ok")
        result.update(
            {
                "configured_ids": config["job_ids"],
                "records": records,
                "alerts": _alerts(config, 0, 5),
                "omissions": [
                    "no live PR/CI/review readiness",
                    "no watchdog stdout history",
                    "no automatic discovery of other jobs",
                    "no idle-agent wakeup",
                ],
            }
        )
    elif name == "lane_findings" and config["version"] >= 2:
        args = _arguments(arguments, {"id", "cursor"}, name)
        if set(args) not in ({"id"}, {"id", "cursor"}) or not isinstance(
            args.get("id"), str
        ):
            refuse(
                "arguments",
                "lane_findings object with string id and optional cursor",
                repr(args),
                "supply a configured id and bounded cursor",
            )
        ident = args["id"]
        if ID_RE.fullmatch(ident) is None or ident not in config["job_ids"]:
            refuse(
                "arguments",
                "id in configured job_ids",
                repr(ident),
                "select a configured id",
            )
        cursor = args.get("cursor")
        if "cursor" in args and (
            not isinstance(cursor, str) or CURSOR_RE.fullmatch(cursor) is None
        ):
            refuse(
                "arguments",
                f"cursor matching {CURSOR_RE.pattern}",
                repr(cursor),
                "supply a bounded findings cursor",
            )
        result = _findings(config, ident, cursor)
    elif name == "lane_changes" and config["version"] >= 2:
        args = _arguments(arguments, {"checkpoint"}, name)
        checkpoint = args.get("checkpoint", {})
        if not isinstance(checkpoint, dict) or any(
            key not in config["job_ids"]
            or not isinstance(value, str)
            or REVISION_RE.fullmatch(value) is None
            for key, value in checkpoint.items()
        ):
            refuse(
                "arguments",
                "checkpoint mapping configured ids to revisions",
                repr(checkpoint),
                "supply only configured ids and bounded revisions",
            )
        result = _changes(config, checkpoint)
    elif name == "lane_jobs" and config["version"] >= 2:
        args = _arguments(arguments, {"name"}, name)
        job_name = args.get("name")
        if "name" in args and (
            not isinstance(job_name, str) or JOB_NAME_RE.fullmatch(job_name) is None
        ):
            refuse(
                "arguments",
                f"name matching {JOB_NAME_RE.pattern}",
                repr(job_name),
                "supply a bounded declared job name",
            )
        result = _jobs(config, job_name)
    else:
        count = "seven" if config["version"] == 2 else "four"
        refuse(
            "arguments",
            f"one of the {count} lane tools",
            repr(name),
            "select a listed tool",
        )
    if len(canonical_bytes(result)) > QUERY_BYTES:
        return _base(name, "unavailable", "complete query exceeds 16384 bytes")
    return result


# Boot-owned routing definitions from discovery-lever.md; never parsed from records.
CONCEPT_INDEX = [{'concept': 'wire',
  'home': 'docs/wire.md',
  'contract': 'docs/wire.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'unbuilt',
  'intended': 'packages/wire'},
 {'concept': 'workflow-genus',
  'home': 'docs/workflow.md',
  'contract': 'docs/workflow.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'none',
  'intended': 'symfony/workflow'},
 {'concept': 'wf-runtime',
  'home': 'docs/workflow.md',
  'contract': 'docs/workflow.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'unbuilt',
  'intended': None},
 {'concept': 'probes',
  'home': 'docs/probes.md',
  'contract': 'ops/conductor/probes-lever.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'unbuilt',
  'intended': None},
 {'concept': 'concerns',
  'home': 'docs/concerns.md',
  'contract': 'docs/concerns.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'none',
  'intended': None},
 {'concept': 'verified-couplings',
  'home': 'docs/verified-couplings.md',
  'contract': 'docs/verified-couplings.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'none',
  'intended': None},
 {'concept': 'mapping',
  'home': 'docs/mapping.md',
  'contract': 'docs/mapping.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'none',
  'intended': 'four projections'},
 {'concept': 'onboard',
  'home': 'ops/conductor/onboard-lever.md',
  'contract': 'ops/conductor/onboard-lever.md',
  'entrypoint': 'ops/conductor/tools/onboard.py',
  'registry': 'ops/conductor/registry/onboard.py.json',
  'implementation': 'ops/conductor/tools/onboard.py',
  'intended': None},
 {'concept': 'lane-mcp',
  'home': 'ops/conductor/lane-mcp-lever.md',
  'contract': 'ops/conductor/lane-mcp-lever.md',
  'entrypoint': 'ops/conductor/tools/lane_mcp.py',
  'registry': 'ops/conductor/registry/lane_mcp.py.json',
  'implementation': 'ops/conductor/tools/lane_mcp.py',
  'intended': None},
 {'concept': 'lane-read',
  'home': 'ops/conductor/lane-mcp-lever.md',
  'contract': 'ops/conductor/lane-mcp-lever.md',
  'entrypoint': 'ops/conductor/tools/lane_read.py',
  'registry': None,
  'implementation': 'ops/conductor/tools/lane_read.py',
  'intended': None},
 {'concept': 'dispatch',
  'home': 'ops/devlane/dispatch/CONTRACT.md',
  'contract': 'ops/devlane/dispatch/CONTRACT.md',
  'entrypoint': 'ops/devlane/dispatch/launch.py',
  'registry': 'ops/devlane/task/jobs.json',
  'implementation': 'ops/devlane/dispatch/launch.py',
  'intended': None},
 {'concept': 'harness',
  'home': 'ops/devlane/harness/',
  'contract': 'ops/devlane/harness/context.cue',
  'entrypoint': None,
  'registry': None,
  'implementation': 'none',
  'intended': None},
 {'concept': 'jobs',
  'home': 'ops/devlane/task/jobs.json',
  'contract': 'ops/devlane/task/CONTRACT.md',
  'entrypoint': None,
  'registry': 'ops/devlane/task/jobs.json',
  'implementation': 'none',
  'intended': None},
 {'concept': 'handoff',
  'home': 'ops/conductor/handoff/README.md',
  'contract': 'ops/conductor/handoff/README.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'unbuilt',
  'intended': None},
 {'concept': 'aar',
  'home': 'ops/conductor/handoff/README.md',
  'contract': 'ops/conductor/handoff/README.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'unbuilt',
  'intended': None},
 {'concept': 'decisions',
  'home': 'ops/conductor/decisions-register.md',
  'contract': 'ops/conductor/decisions-register.md',
  'entrypoint': 'ops/conductor/tools/decisions.py',
  'registry': 'ops/conductor/registry/decisions.py.json',
  'implementation': 'ops/conductor/tools/decisions.py',
  'intended': None},
 {'concept': 'ledger',
  'home': 'ops/conductor/handwork-ledger.md',
  'contract': 'ops/conductor/handwork-ledger.md',
  'entrypoint': 'ops/conductor/tools/hand.py',
  'registry': 'ops/conductor/registry/hand.py.json',
  'implementation': 'ops/conductor/tools/hand.py',
  'intended': None},
 {'concept': 'watch',
  'home': 'ops/conductor/watch-lever.md',
  'contract': 'ops/conductor/watch-lever.md',
  'entrypoint': 'ops/conductor/tools/watch.py',
  'registry': 'ops/conductor/registry/watch.py.json',
  'implementation': 'ops/conductor/tools/watch.py',
  'intended': None},
 {'concept': 'land',
  'home': 'ops/conductor/land-lever.md',
  'contract': 'ops/conductor/land-lever.md',
  'entrypoint': 'ops/conductor/tools/land.py',
  'registry': 'ops/conductor/registry/land.py.json',
  'implementation': 'ops/conductor/tools/land.py',
  'intended': None},
 {'concept': 'ci-store',
  'home': 'ops/conductor/ci-store.md',
  'contract': 'ops/conductor/ci-store.md',
  'entrypoint': 'ops/conductor/tools/ci.py',
  'registry': 'ops/conductor/registry/ci.py.json',
  'implementation': 'ops/conductor/tools/ci.py',
  'intended': None},
 {'concept': 'branches',
  'home': 'ops/conductor/branch-census.md',
  'contract': 'ops/conductor/branch-census.md',
  'entrypoint': 'ops/conductor/tools/branches.py',
  'registry': 'ops/conductor/registry/branches.py.json',
  'implementation': 'ops/conductor/tools/branches.py',
  'intended': None},
 {'concept': 'skills',
  'home': '.claude/skills/skills/SKILL.md',
  'contract': '.claude/skills/skills/SKILL.md',
  'entrypoint': 'ops/conductor/tools/skills.py',
  'registry': None,
  'implementation': 'ops/conductor/tools/skills.py',
  'intended': None},
 {'concept': 'permission',
  'home': 'ops/conductor/permission-surface.md',
  'contract': 'ops/conductor/permission-surface.md',
  'entrypoint': 'ops/conductor/tools/co_doc.py',
  'registry': None,
  'implementation': 'ops/conductor/tools/co_doc.py',
  'intended': None},
 {'concept': 'monitor',
  'home': 'ops/conductor/monitor-lever.md',
  'contract': 'ops/conductor/monitor-lever.md',
  'entrypoint': 'ops/conductor/tools/monitor.py',
  'registry': 'ops/conductor/registry/monitor.py.json',
  'implementation': 'ops/conductor/tools/monitor.py',
  'intended': None},
 {'concept': 'watchdog',
  'home': 'ops/conductor/watchdog-lever.md',
  'contract': 'ops/conductor/watchdog-lever.md',
  'entrypoint': 'ops/conductor/tools/watchdog.py',
  'registry': 'ops/conductor/registry/watchdog.py.json',
  'implementation': 'ops/conductor/tools/watchdog.py',
  'intended': None},
 {'concept': 'ladder',
  'home': 'ops/conductor/ladder-lever.md',
  'contract': 'ops/conductor/ladder-lever.md',
  'entrypoint': 'ops/conductor/tools/ladder.py',
  'registry': 'ops/conductor/registry/ladder.py.json',
  'implementation': 'ops/conductor/tools/ladder.py',
  'intended': None},
 {'concept': 'onboard-discovery',
  'home': 'ops/conductor/discovery-lever.md',
  'contract': 'ops/conductor/discovery-lever.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'unbuilt',
  'intended': None},
 {'concept': 'conduct',
  'home': 'ops/conductor/conductor-start-plan.md',
  'contract': 'ops/conductor/conductor-start-plan.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'unbuilt',
  'intended': None},
 {'concept': 'packages',
  'home': 'packages/README.md',
  'contract': 'packages/README.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'none',
  'intended': 'bundle gestation'},
 {'concept': 'projections',
  'home': 'AGENTS.md',
  'contract': 'AGENTS.md',
  'entrypoint': None,
  'registry': None,
  'implementation': 'none',
  'intended': 'CLI, TUI, MCP, API'}]

DISCOVERY_QUERIES = ('lane_start', 'lane_homes', 'lane_handoffs', 'lane_actions')
DISCOVERY_ID_RE = re.compile(r'[a-z][a-z0-9-]{0,31}')
DIGEST_RE = re.compile(r'[0-9a-f]{64}')
OID_RE = re.compile(r'[0-9a-f]{40}')
REF_RE = re.compile(r'refs/heads/(?:[A-Za-z0-9_-][A-Za-z0-9._-]*/)*[A-Za-z0-9_-][A-Za-z0-9._-]*')
DISCOVERY_LAW = (
    ('AGENTS.md', 'the operating contract'),
    ('CONTRIB.md', 'naming, PR bodies, fix commits, the review wire'),
    ('ops/conductor/instrument-conventions.md', "the lane's base contract: wire, identity, exits"),
    ('ops/conductor/stack.md', 'the stack: layers, incumbents, open seats'),
    ('ops/conductor/coverage-zones.md', 'what gates what'),
)
ACTION_INDEX = (
    ('onboard', 'ops/conductor/onboard-lever.md', 'ops/conductor/tools/onboard.py'),
    ('handoff', 'ops/conductor/handoff/README.md', None),
    ('aar', 'ops/conductor/handoff/README.md', None),
    ('closeout', 'docs/workflow.md', None),
)
START_OMISSIONS = [
    'no host activation', 'no live readiness', 'no role enforcement',
    'no lifecycle publication', 'no raw documentation dump',
    'HEAD tree only; dirty and untracked files excluded',
    'register ledger prs local readings unavailable',
]


def default_profiles() -> dict:
    """Declarations, not defaults inserted into an incomplete config."""
    concepts = [row['concept'] for row in CONCEPT_INDEX]
    return {
        'dev': dict(label='development machinery', work_roots=['ops/'],
                    startup_homes=['lane-mcp', 'dispatch', 'onboard'],
                    homes=[c for c in concepts if c not in {'mapping', 'packages', 'projections'}],
                    source_ids=['repo'], lineage='dev'),
        'prod': dict(label='Symfony application',
                     work_roots=['src/', 'packages/', 'config/', 'public/'],
                     startup_homes=['mapping', 'packages', 'projections'],
                     homes=[c for c in concepts if c not in {'wf-runtime', 'dispatch', 'harness', 'jobs', 'ci-store', 'ladder'}],
                     source_ids=['repo'], lineage='prod'),
    }


def matches(pattern: re.Pattern, value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def valid_ref(value: object) -> bool:
    return (matches(REF_RE, value) and len(value) <= 256
            and all('..' not in c and not c.endswith(('.', '.lock')) for c in value.split('/')))


def valid_path(value: object, *, absolute: bool = False, root: bool = False) -> bool:
    if (not isinstance(value, str) or not 1 <= len(value) <= 1024
            or not value.isascii() or not value.isprintable()
            or any(c in value for c in '\\~*?[]')
            or value.startswith('/') != absolute or value.endswith('/') != root):
        return False
    parts = value[int(absolute):len(value) - int(root) if root else None].split('/')
    return all(c not in ('', '.', '..') for c in parts)


def strict_json(raw: bytes) -> object:
    """V3 limits are checked lexically before constructing containers."""
    if _json_depth_exceeds(raw, 32):
        raise ValueError('depth')

    def integer(value):
        parsed = int(value)
        if not -MAX_INTEGER - 1 <= parsed <= MAX_INTEGER:
            raise ValueError('integer')
        return parsed

    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('number')
        return parsed

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate')
            result[key] = value
        return result

    def constant(_value):
        raise ValueError('constant')

    return json.loads(raw.decode('utf-8'), parse_int=integer, parse_float=number,
                      parse_constant=constant, object_pairs_hook=pairs)


def discovery_config_refusal() -> NoReturn:
    refuse('config', 'valid versioned configuration', 'invalid',
           'supply the declared keys and bindings')


def validate_discovery_config(root: object, base: Path) -> dict:
    def require(condition):
        if not condition:
            raise ValueError('config')

    def shape(value, keys):
        require(isinstance(value, dict) and set(value) == set(keys.split()))

    def unique(value, low, high, predicate):
        require(isinstance(value, list) and low <= len(value) <= high)
        require(all(predicate(item) for item in value))
        require(len(set(value)) == len(value))

    def ident(value):
        return matches(DISCOVERY_ID_RE, value)

    require(isinstance(root, dict) and set(root) == CONFIG_KEYS_V2 | {'profiles', 'discovery'})
    require(type(root['version']) is int and root['version'] == 3)
    unique(root['job_ids'], 0, 21, lambda v: matches(ID_RE, v))
    result = dict(root)
    for field in ('dispatch_store', 'monitor_state', 'usage_rollout', 'job_registry'):
        result[field] = _source_path(root[field], field, base)
    result['consumer'] = _consumer(root['consumer'])
    discovery = root['discovery']
    shape(discovery, 'audience anchors repository retained_refs dispatch record_bindings work_bindings')
    require(discovery['audience'] == 'conductor')
    anchors = discovery['anchors']
    require(isinstance(anchors, dict) and 1 <= len(anchors) <= 8 and 'repo' in anchors)
    require(all(ident(k) and valid_path(v, absolute=True) for k, v in anchors.items()))
    repository = discovery['repository']
    shape(repository, 'id root gitdir common_gitdir head ref')
    require(ident(repository['id']) and repository['root'] == 'repo')
    require(all(isinstance(repository[k], str) and repository[k] in anchors for k in ('gitdir', 'common_gitdir')))
    require(matches(OID_RE, repository['head']))
    require(repository['ref'] is None or valid_ref(repository['ref']))
    require(discovery['retained_refs'] in (None, 'refs/heads/records/'))
    dispatch = discovery['dispatch']
    if dispatch is not None:
        shape(dispatch, 'anchor path')
        require(isinstance(dispatch['anchor'], str) and dispatch['anchor'] in anchors)
        require(valid_path(dispatch['path']))
        require(os.path.join(anchors[dispatch['anchor']], dispatch['path']) == result['dispatch_store'])
    bindings = discovery['record_bindings']
    require(isinstance(bindings, list) and len(bindings) <= 256)
    revisions, metadata, pairs = {}, {}, set()
    for binding in bindings:
        shape(binding, 'record_id kind revision lineage work_paths supersedes')
        require(ident(binding['record_id']) and ident(binding['lineage']))
        require(binding['kind'] in ('handoff', 'aar') and matches(DIGEST_RE, binding['revision']))
        unique(binding['work_paths'], 1, 64, valid_path)
        unique(binding['supersedes'], 0, 16, ident)
        pair = (binding['record_id'], binding['revision'])
        require(pair not in pairs)
        pairs.add(pair)
        meta = {k: v for k, v in binding.items() if k != 'revision'}
        require(binding['revision'] not in revisions or revisions[binding['revision']] == meta)
        require(binding['record_id'] not in metadata or metadata[binding['record_id']] == meta)
        revisions[binding['revision']] = meta
        metadata[binding['record_id']] = meta
    for binding in bindings:
        for target in binding['supersedes']:
            require(target in metadata and all(metadata[target][k] == binding[k] for k in ('kind', 'lineage')))
    visited, active = set(), set()

    def visit(ident):
        require(ident not in active)
        if ident in visited:
            return
        active.add(ident)
        for target in metadata[ident]['supersedes']:
            visit(target)
        active.remove(ident)
        visited.add(ident)

    for ident_value in metadata:
        visit(ident_value)
    work = discovery['work_bindings']
    require(isinstance(work, list) and len(work) <= 20)
    seen = set()
    for binding in work:
        shape(binding, 'id lineage work_paths')
        require(isinstance(binding['id'], str) and binding['id'] in root['job_ids'] and binding['id'] not in seen)
        seen.add(binding['id'])
        require(ident(binding['lineage']))
        unique(binding['work_paths'], 1, 64, valid_path)
    profiles = root['profiles']
    require(isinstance(profiles, dict) and 2 <= len(profiles) <= 16 and {'dev', 'prod'} <= set(profiles))
    concepts = {row['concept'] for row in CONCEPT_INDEX}
    for name, profile in profiles.items():
        require(ident(name))
        shape(profile, 'label work_roots homes startup_homes source_ids lineage')
        label = profile['label']
        require(isinstance(label, str) and 1 <= len(label) <= 128 and label.isascii() and label.isprintable())
        unique(profile['work_roots'], 1, 16, lambda p: valid_path(p, root=True))
        unique(profile['homes'], 1, 64, lambda c: isinstance(c, str) and c in concepts)
        unique(profile['startup_homes'], 1, 3, lambda c, profile=profile: isinstance(c, str) and c in profile['homes'])
        allowed = {'repo'}
        if discovery['retained_refs'] is not None:
            allowed.add('retained')
        if dispatch is not None:
            allowed.update('dispatch/' + job for job in root['job_ids'])
        unique(profile['source_ids'], 1, 22, lambda s, allowed=allowed: isinstance(s, str) and s in allowed)
        require('repo' in profile['source_ids'] and ident(profile['lineage']))
    return result


def cursor_refusal() -> NoReturn:
    refuse('cursor', 'current snapshot and valid page offset', 'invalid', 'restart from lane_start')


def discovery_arguments(config: dict, name: str, value: object) -> dict:
    extra = {'lane_start': {'cursor'}, 'lane_homes': {'concept', 'cursor'},
             'lane_handoffs': {'key', 'cursor'}, 'lane_actions': {'name'}}[name]
    if (not isinstance(value, dict) or not set(value) <= {'profile', 'snapshot'} | extra
            or not matches(DISCOVERY_ID_RE, value.get('profile'))):
        refuse('arguments', f'closed arguments for {name}', 'invalid', 'use the declared query schema')
    for field, pattern in (('concept', JOB_NAME_RE), ('key', re.compile(r'record/[0-9a-f]{64}')), ('name', DISCOVERY_ID_RE)):
        if field in value and (not matches(pattern, value[field]) or 'cursor' in value):
            refuse('arguments', f'closed arguments for {name}', 'invalid', 'use the declared query schema')
    if value['profile'] not in config['profiles']:
        refuse('profile', 'configured profile', value['profile'], 'use a profile from configuration')
    if 'snapshot' in value and not matches(DIGEST_RE, value['snapshot']):
        cursor_refusal()
    if 'cursor' in value:
        cursor = value['cursor']
        match = re.fullmatch(r'([0-9a-f]{64}):(0|[1-9][0-9]{0,2})', cursor) if isinstance(cursor, str) else None
        if match is None or 'snapshot' not in value:
            cursor_refusal()
        low, high = (0, 20) if name == 'lane_start' else (1, 255)
        if not low <= int(match[2]) <= high or match[1] != value['snapshot']:
            cursor_refusal()
    return dict(value)


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def source_reading(ident: str, binding: dict, observations: list) -> dict:
    """The sole discovery source hash recipe; byte buffers normalize at the seam."""
    normalized = {}
    for observation in observations:
        row = dict(observation)
        if isinstance(row['data'], bytes):
            row['data'] = {'revision': hashlib.sha256(row['data']).hexdigest(), 'bytes': len(row['data'])}
        normalized[canonical_bytes(row)] = row
    ordered = [normalized[key] for key in sorted(normalized)]
    failures = sorted((o for o in ordered if o['state'] != 'ok'),
                      key=lambda o: (o['operation'], canonical_bytes(o['input']), canonical_bytes(o)))
    state = 'unavailable' if any(o['state'] == 'unavailable' and o['reason'] != 'missing' for o in failures) else 'unknown' if failures else 'ok'
    reason = failures[0]['reason'] if failures else None
    return dict(id=ident, state=state, reason=reason,
                fingerprint=digest(dict(id=ident, binding=binding, state=state, reason=reason, observations=ordered)))


def relevant(binding: dict, profile: dict) -> bool:
    return (binding['lineage'] == profile['lineage']
            and any(path.startswith(root) for path in binding['work_paths'] for root in profile['work_roots']))


def _discovery_homes(config: dict, profile: dict, reading: dict) -> list:
    files = reading.get('files', {})
    lock = files.get('composer.lock', {})
    locked = None
    if lock.get('state') == 'ok':
        try:
            parsed = strict_json(lock['data'])
            if not isinstance(parsed, dict) or any(k in parsed and not isinstance(parsed[k], list) for k in ('packages', 'packages-dev')):
                raise ValueError('lock')
            locked = any(isinstance(p, dict) and p.get('name') == 'symfony/workflow'
                         for k in ('packages', 'packages-dev') for p in parsed.get(k, []))
        except (ValueError, TypeError, UnicodeError, RecursionError):
            locked = None
    elif lock.get('reason') == 'missing':
        locked = False

    def presence(path):
        if path is None:
            return None
        source = files.get(path, {})
        if source.get('reason') == 'missing':
            return False
        if source.get('state') != 'ok':
            return None
        return bool(source.get('data'))

    index = {row['concept']: row for row in CONCEPT_INDEX}
    homes = []
    for concept in profile['homes']:
        row = index[concept]
        present = presence(row['contract'])
        implementation = row['implementation']
        implementation_present = (None if implementation == 'none' else False if implementation == 'unbuilt'
                                  else presence(implementation))
        consulted = any(p in files for p in (row['contract'], implementation)) or (row['intended'] == 'symfony/workflow' and 'composer.lock' in files)
        homes.append(dict(concept=concept, home=row['home'], contract=row['contract'],
                          entrypoint=row['entrypoint'] if present else None, registry=row['registry'],
                          ownership='declared' if present else 'unresolved', document_present=present,
                          implementation_present=implementation_present,
                          locked=locked if row['intended'] == 'symfony/workflow' else None,
                          installed=None, activated=None, probed=None, intended=row['intended'],
                          evidence=['contract', 'repo'] if consulted else ['contract']))
    return homes


def _discovery_candidates(config: dict, profile: dict, reading: dict) -> tuple[list, dict]:
    bindings = {b['revision']: b for b in config['discovery']['record_bindings']}
    grouped = {}
    complete = reading.get('records_complete', False)
    for occurrence in reading.get('records', []):
        raw = occurrence.get('data')
        if not isinstance(raw, bytes):
            complete = False
            continue
        revision = hashlib.sha256(raw).hexdigest()
        group = grouped.setdefault(revision, dict(raw=raw, occurrences=[], kinds=set(), conflict=False))
        group['conflict'] |= group['raw'] != raw
        group['occurrences'].append(occurrence['provenance'])
        group['kinds'].add(occurrence['kind'])
    # An incomplete scan cannot establish missing bytes; include missing bindings
    # only after complete enumeration, including measured reads that failed.
    if reading.get('records_complete', False):
        for revision, binding in bindings.items():
            if revision not in grouped and relevant(binding, profile):
                grouped[revision] = dict(raw=None, occurrences=[], kinds={binding['kind']}, conflict=False)
    candidates, readable, selected_bindings = [], set(), {}
    for revision, group in sorted(grouped.items()):
        binding = bindings.get(revision)
        if binding is not None and not relevant(binding, profile):
            continue
        raw = group['raw']
        kinds = group['kinds']
        kind = binding['kind'] if binding else ('handoff' if 'handoff' in kinds else 'aar')
        conflict = group['conflict'] or len(kinds) != 1 or (binding is not None and kind not in kinds)
        reason = ('metadata-conflict' if conflict else 'metadata-missing' if binding is None
                  else 'missing-bound-record' if raw is None else None)
        is_readable = raw is not None
        if raw is not None:
            try:
                raw.decode('utf-8')
            except UnicodeError:
                is_readable = False
                if reason is None:
                    reason = 'malformed'
        occurrences = sorted(group['occurrences'], key=lambda o: (o['ref'] or '', o['commit'], o['path'], o['blob']))
        known = binding is not None and not conflict
        if kind == 'handoff' and (not known or not is_readable):
            complete = False
        card = dict(key='record/' + revision, record_id=binding['record_id'] if known else None,
                    kind=kind, relevance='relevant' if known else 'unknown', freshness='unknown',
                    trust='untrusted-record', revision=revision, occurrences=len(occurrences),
                    provenance=occurrences[0] if known and occurrences else None,
                    body=None, body_bytes=len(raw) if known and raw is not None else None, reason=reason)
        candidates.append(card)
        if known:
            selected_bindings[card['key']] = binding
            if is_readable:
                readable.add(card['key'])
    superseded = set()
    by_id = {b['record_id']: b for b in bindings.values()}

    def mark(ident):
        if ident in superseded:
            return
        superseded.add(ident)
        for target in by_id[ident]['supersedes']:
            mark(target)

    for key in readable:
        for target in selected_bindings[key]['supersedes']:
            mark(target)
    if complete:
        for card in candidates:
            if card['key'] in readable:
                card['freshness'] = 'stale' if card['record_id'] in superseded else 'current'
    current = [c for c in candidates if c['kind'] == 'handoff' and c['freshness'] == 'current']
    stale = [c for c in candidates if c['kind'] == 'handoff' and c['freshness'] == 'stale']
    selection = dict(selection=('none' if not current else 'one' if len(current) == 1 else 'multiple') if complete else 'unknown',
                     selected_key=current[0]['key'] if complete and len(current) == 1 else None,
                     current=len(current) if complete else None, stale=len(stale) if complete else None,
                     unknown=0 if complete else None, complete=complete)
    return candidates, selection


def _discovery_work(config: dict, profile: dict, reading: dict) -> tuple[list, dict]:
    bindings = {b['id']: b for b in config['discovery']['work_bindings']}
    sources = {s['id']: s for s in reading['sources']}
    counts = dict(configured=0, readable=0, unknown=0, unavailable=0)
    cards = []
    for ident in config['job_ids']:
        source_id = 'dispatch/' + ident
        if source_id not in profile['source_ids']:
            continue
        source = sources[source_id]
        counts['configured'] += 1
        counts['readable' if source['state'] == 'ok' else source['state']] += 1
        binding = bindings.get(ident)
        if binding is not None and not relevant(binding, profile):
            continue
        record = reading.get('dispatches', {}).get(ident, {})
        cards.append(dict(id=ident, lineage=binding['lineage'] if binding else None,
                          relevance='relevant' if binding else 'unknown',
                          recorded_status=record.get('recorded_status'), revision=record.get('revision'),
                          reason=source['reason'] if source['state'] != 'ok' else None if binding else 'metadata-missing'))
    return cards, counts


def discovery_query(config: dict, name: str, args: dict, reading: dict) -> dict:
    """Pure projection of one immutable acquisition; no source access here."""
    profile_id = args['profile']
    profile = config['profiles'][profile_id]
    sources = sorted(reading['sources'], key=lambda s: s['id'])
    snapshot = (digest(dict(protocol='discovery/3', audience='conductor', profile=profile_id, sources=sources))
                if reading['state'] == 'ok' else None)
    result = dict(schema=SCHEMA, query=name, state=reading['state'], reason=reading.get('reason'),
                  profile=profile_id, snapshot=snapshot, sources=sources, data=None)
    if reading['state'] != 'ok':
        return result
    if 'snapshot' in args and args['snapshot'] != snapshot:
        refuse('cursor', 'snapshot ' + snapshot, args['snapshot'], 'restart from lane_start')
    candidates, selection = _discovery_candidates(config, profile, reading)
    if len(candidates) > 256:
        return dict(result, state='unavailable', reason='record-overflow')
    homes = _discovery_homes(config, profile, reading)
    files = reading.get('files', {})
    actions = []
    for action_name, owner, entrypoint in ACTION_INDEX:
        source = files.get(entrypoint, {})
        present = (False if entrypoint is None or source.get('reason') == 'missing' else
                   bool(source.get('data')) if source.get('state') == 'ok' else None)
        actions.append(dict(name=action_name, owner=owner, entrypoint=entrypoint,
                            implementation_present=present, available=present, installed=None,
                            activated=None, probed=None,
                            reason='disabled' if entrypoint is None else None if present else 'missing' if present is False else 'unreadable'))
    offset = int(args['cursor'].split(':')[1]) if 'cursor' in args else 0
    cap = 8192 if name == 'lane_start' else QUERY_BYTES

    def failure(reason):
        return dict(result, state='unavailable', reason=reason, sources=[], data=None)

    def fits(data):
        return len(canonical_bytes(dict(result, data=data))) <= cap

    if name == 'lane_actions':
        named = args.get('name')
        action = next((a for a in actions if a['name'] == named), None)
        result['data'] = dict(actions=actions if named is None else [], action=action)
        if named is not None and action is None:
            result['reason'] = 'unknown-action'
    elif name == 'lane_start':
        work, counts = _discovery_work(config, profile, reading)
        if 'cursor' in args and offset >= len(work):
            cursor_refusal()
        law = [dict(path=p, status='present' if files.get(p, {}).get('state') == 'ok' and files[p].get('data') else
                    'missing' if files.get(p, {}).get('reason') == 'missing' or files.get(p, {}).get('data') == b'' else 'unavailable',
                    one_liner=label) for p, label in DISCOVERY_LAW]
        state = [dict(name=n, status='unavailable', summary=None, reason='unsupported-local-reading', evidence=[])
                 for n in ('register', 'ledger', 'prs')]
        state.append(dict(name='dispatches', status='ok', summary=counts, reason=None,
                          evidence=['dispatch/' + i for i in config['job_ids'] if 'dispatch/' + i in profile['source_ids']]))
        head = reading.get('head', {})
        state.append(dict(name='branch', status='ok' if head.get('state') == 'ok' else 'unavailable',
                          summary=dict(ref=head['data']['ref'], commit=head['data']['head']) if head.get('state') == 'ok' else None,
                          reason=head.get('reason'), evidence=['repo']))
        by_concept = {h['concept']: h for h in homes}
        summaries = [{k: by_concept[c][k] for k in ('concept', 'home', 'entrypoint', 'document_present', 'implementation_present')}
                     for c in profile['startup_homes']]
        omissions = list(START_OMISSIONS)
        if 'retained' not in profile['source_ids']:
            omissions.append('retained source not searched')

        def frame(items):
            omitted = len(work) - offset - len(items)
            more = [f'work omitted: {omitted}', f'work next cursor: {snapshot}:{offset + len(items)}'] if omitted else []
            return dict(law=law, state_sources=state, homes=summaries, work=items,
                        handoffs=selection, actions=actions, omissions=omissions + more)

        if not fits(frame([])):
            return failure('page-frame-exceeds-cap')
        items = work[:8] if 'cursor' not in args else []
        if 'cursor' not in args:
            if not fits(frame(items)):
                items = []
        else:
            for card in work[offset:offset + 8]:
                if not fits(frame(items + [card])):
                    break
                items.append(card)
            if not items:
                return failure('item-exceeds-cap')
        result['data'] = frame(items)
    else:
        is_home = name == 'lane_homes'
        values = homes if is_home else candidates
        plural, singular, key, field = ('homes', 'home', 'concept', 'concept') if is_home else ('candidates', 'candidate', 'key', 'key')
        data = {plural: [], singular: None, 'page': None}
        if not is_home:
            data['handoffs'] = selection
        if field in args:
            card = next((dict(c) for c in values if c[key] == args[field]), None)
            if card is not None and not is_home and card['relevance'] == 'relevant' and card['occurrences']:
                raw = next((r['data'] for r in reading.get('records', []) if isinstance(r.get('data'), bytes)
                            and hashlib.sha256(r['data']).hexdigest() == card['revision']), None)
                if raw is not None:
                    if card['reason'] == 'malformed':
                        card['body'] = None
                    elif len(raw) > 2048:
                        card['reason'] = 'body-exceeds-cap'
                    else:
                        try:
                            body = raw.decode('utf-8')
                            escaped = '\n'.join('>' + s if s == 'END UNTRUSTED RECORD' else s for s in body.split('\n'))
                            card['body'] = f"BEGIN UNTRUSTED RECORD key={card['key']} revision={card['revision']}\n{escaped}\nEND UNTRUSTED RECORD\n"
                        except UnicodeError:
                            card['reason'] = 'malformed'
            data[singular] = card
            if card is None:
                result['reason'] = 'unknown-concept' if is_home else 'unknown-key'
        else:
            if 'cursor' in args and offset >= len(values):
                cursor_refusal()

            def page(items):
                more = offset + len(items) < len(values)
                return dict(data, **{plural: items, 'page': dict(offset=offset, count=len(items), total=len(values),
                            more=more, next_cursor=f'{snapshot}:{offset + len(items)}' if more else None)})

            if not fits(page([])):
                return failure('page-frame-exceeds-cap')
            items = []
            for card in values[offset:offset + 16]:
                if not fits(page(items + [card])):
                    break
                items.append(card)
            if values and not items:
                return failure('item-exceeds-cap')
            data = page(items)
        result['data'] = data
    if len(canonical_bytes(result)) > cap:
        return failure('response-exceeds-cap')
    return result
