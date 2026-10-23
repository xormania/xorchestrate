#!/usr/bin/env python3
# testkit: exempt fixture program planted through Tools
"""Stand-in for monitor.py on the dispatch follow-through.

sessions-lever.md slice 3, "The programs it runs": `run --once` reads the
per-pass config and logs it and the record ids it names; `digest`
answers the events in the spec file beside this program whose id is above
the acked cursor, as monitor.py's digest does; `--ack` moves the cursor to
the last event served. The spec's `replay` serves every event regardless.
The spec's `observed` serves only events whose subject a `run --once`
config named, as monitor.py only reports what a pass observed; the ids
observed are kept in `<this-program>.observed`.

The spec is `<this-program>.spec.json`: `{"replay": false, "events":
[...]}`. Each event is a monitor digest event (id, route, consumer,
condition, monitor, subject, state, summary, evidence, observed_at,
delivery). One JSON line per call is appended to
`<this-program>.calls.jsonl`.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def program() -> Path:
    return Path(sys.argv[0]).resolve()


def spec_path() -> Path:
    return program().with_suffix(".spec.json")


def log_path() -> Path:
    return program().with_suffix(".calls.jsonl")


def cursor_path() -> Path:
    return program().with_suffix(".cursor")


def collect_ids(value) -> list[str]:
    """Record ids a per-pass config names under `ids`."""
    found: list[str] = []
    if isinstance(value, dict):
        named = value.get("ids")
        if isinstance(named, list):
            found.extend(item for item in named if isinstance(item, str))
        for item in value.values():
            found.extend(collect_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(collect_ids(item))
    return found


def observed_path() -> Path:
    return program().with_suffix(".observed")


def observed_ids() -> set[str]:
    """Every record id a `run --once` config has named; empty before any."""
    try:
        return set(observed_path().read_text(encoding="utf-8").split())
    except OSError:
        return set()


def acked_through() -> int:
    """The last event id acked; 0 before any ack."""
    try:
        return int(cursor_path().read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def config_body(argv: list[str]):
    """The per-pass config as JSON, or None when absent or unreadable."""
    config, _ids = config_ids(argv)
    if not config:
        return None
    try:
        return json.loads(Path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def config_ids(argv: list[str]) -> tuple[str | None, list[str]]:
    config = None
    if "--config" in argv:
        index = argv.index("--config")
        if index + 1 < len(argv):
            config = argv[index + 1]
    else:
        for arg in argv:
            if arg.startswith("--config="):
                config = arg.split("=", 1)[1]
                break
    if not config:
        return None, []
    try:
        loaded = json.loads(Path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config, []
    return config, collect_ids(loaded)


def log_call(argv: list[str], ids: list[str], config: str | None) -> None:
    line = json.dumps({
        "at": time.time_ns(),
        "pid": os.getpid(),
        "argv": argv,
        "cwd": os.getcwd(),
        "config": config,
        "body": config_body(argv),
        "ids": ids,
        "ack": "--ack" in argv,
    }) + "\n"
    fd = os.open(log_path(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def load_spec() -> dict:
    path = spec_path()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        sys.stderr.write(
            f"arguments: expected a digest spec at {path}; found {error}; "
            "needed the events this pass serves\n"
        )
        raise SystemExit(2) from None
    if not isinstance(loaded, dict):
        sys.stderr.write(
            f"arguments: expected a digest spec object; found {type(loaded).__name__}; "
            "needed events for this pass\n"
        )
        raise SystemExit(2)
    return loaded


def consumer_of(argv: list[str]) -> str | None:
    if "--consumer" not in argv:
        return None
    index = argv.index("--consumer")
    if index + 1 >= len(argv):
        return ""
    return argv[index + 1]


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve_run(argv: list[str], spec: dict) -> int:
    config, ids = config_ids(argv)
    log_call(argv, ids, config)
    if ids:
        with observed_path().open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{item}\n" for item in ids))
    events = spec.get("events") if isinstance(spec.get("events"), list) else []
    emit({"observations": len(ids), "events": len(events), "unavailable": 0})
    return 0


def serve_digest(argv: list[str], spec: dict) -> int:
    config, ids = config_ids(argv)
    log_call(argv, ids, config)
    replay = bool(spec.get("replay"))
    events = spec.get("events") if isinstance(spec.get("events"), list) else []
    asked = consumer_of(argv)
    if asked not in (None, "dispatch"):
        events = []
    if spec.get("observed"):
        seen = observed_ids()
        events = [event for event in events
                  if isinstance(event, dict) and str(event.get("subject")) in seen]
    if not replay:
        cursor = acked_through()
        events = [event for event in events
                  if isinstance(event, dict) and isinstance(event.get("id"), int)
                  and event["id"] > cursor]
    emit({"events": events, "more": False})
    if "--ack" in argv and events:
        last = max(event["id"] for event in events if isinstance(event.get("id"), int))
        cursor_path().write_text(f"{max(last, acked_through())}\n", encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    spec = load_spec()
    if "digest" in argv:
        return serve_digest(argv, spec)
    if "run" in argv:
        return serve_run(argv, spec)
    log_call(argv, [], None)
    sys.stderr.write(
        "arguments: expected run or digest; found "
        + json.dumps(argv)
        + "; needed monitor.py run --once or digest\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
