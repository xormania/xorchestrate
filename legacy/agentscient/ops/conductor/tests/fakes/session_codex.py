# testkit: exempt fixture program planted as the codex app-server
"""Fake Codex app-server for sessions slice 2.

Planted through Tools.plant("codex", script=...) by the bridge rows
(test_session_messages.py) and the adapter rows
(test_session_adapters_codex.py). It answers only the contracted argv,
`codex app-server --listen stdio://` (sessions-lever.md §The adapter
boundary, extended, `channel`), alone or after the one top-level flag
profiles.py run-interactive puts before it,
`--dangerously-bypass-hook-trust` (`codex --help` lists it under the
top-level options and `codex app-server --help` does not, so the real CLI
takes it before the subcommand); any other argv is logged and refused
`channel` on the wire. `--version` answers the profile's version probe.

The registry spec beside this executable (fakes/<name>.spec.json, or
TESTKIT_FAKE_SPEC when it names this program's spec) says:

- ``native_id``: the thread id to answer; empty means ``thread-<pid>``;
- ``turn_id``: the turn id ``turn/start`` answers (default ``turn-1``);
- ``reply``: the ``item/agentMessage/delta`` text;
- ``reject_method``: the method answered -32601;
- ``die_after``: the method after whose answer the process exits;
- ``stream_deltas`` and ``stream_interval``: ``turn/start`` streams that
  many ``item/agentMessage/delta`` frames, ``stream_interval`` seconds
  apart, instead of one (a turn streaming across the bridge's tick);
- ``ack_probe``, ``ack``, ``send_reply``, and ``session`` with
  ``capability``: the bridge rows' acks and replies, carried through
  coordination.py as the member's own participant.

Speaks JSON lines on stdio, no ``jsonrpc`` field, the frame shapes of
codex-cli 0.155.1's generated schema as the XO relayed b3's reading of
it on 2026-09-23: ``initialize``, ``initialized``, ``thread/start`` →
``thread.id``, then ``thread/started`` and ``mcpServer/startupStatus/updated``
naming ``coordination`` ready; ``turn/start`` → ``turn.id``, then
``turn/started``, ``item/agentMessage/delta`` and ``turn/completed``, each
with that ``threadId`` and the turn as ``turn.id``; ``thread/read`` → the
thread with every ``turn/start`` this process received as a turn.

Two logs beside the spec. ``<name>.calls.jsonl``: one JSON line per line
received, with this process's ``pid``, its ``ancestors`` (so a row can
tell which member's bridge spawned it), its ``argv``, and either the
request (``method``, ``id``, ``params``, ``native_id``) or the client's
answer to a request this side sent (``response`` true, ``id``,
``result`` or ``error``). ``<name>.emitted.jsonl``: every frame this side
writes that is not a response, verbatim.

The repository root is substituted once at plant; unsubstituted, the
acks and replies are skipped.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

REPO = "@@REPO@@"
CHANNEL_ARGV = ["app-server", "--listen", "stdio://"]
# The top-level flag the member's profile adds before the subcommand.
PROFILE_FLAG = "--dangerously-bypass-hook-trust"
TURNS: list = []


def spec_path() -> Path | None:
    exe = Path(sys.argv[0]).resolve()
    own = exe.parent.parent / "fakes" / f"{exe.name}.spec.json"
    named = os.environ.get("TESTKIT_FAKE_SPEC", "")
    if named:
        path = Path(named)
        if path.is_file() and path.name.startswith(exe.name + "."):
            return path
    return own if own.is_file() else None


def load_spec() -> dict:
    path = spec_path()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ancestor_pids() -> list[int]:
    found: list[int] = []
    pid = os.getpid()
    for _ in range(12):
        status = Path(f"/proc/{pid}/status")
        if not status.is_file():
            break
        ppid = None
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
                break
        if not ppid or ppid in found or ppid <= 1:
            break
        found.append(ppid)
        pid = ppid
    return found


def append(suffix: str, entry: dict) -> None:
    path = spec_path()
    if path is None:
        return
    log = path.with_name(path.name.replace(".spec.json", suffix))
    line = json.dumps(entry) + "\n"
    fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def log_line(fields: dict) -> None:
    entry = {"pid": os.getpid(), "ancestors": ancestor_pids(), "argv": sys.argv[1:]}
    entry.update(fields)
    append(".calls.jsonl", entry)


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def stream(req_id, value: dict, frames: list, interval: float = 0.0) -> None:
    """The result, then its frames; the frames are logged first, so a
    row reading the emitted log after the result never races them."""
    for frame in frames:
        append(".emitted.jsonl", frame)
    result(req_id, value)
    for frame in frames:
        if interval:
            time.sleep(interval)
        emit(frame)


def result(req_id, value: dict) -> None:
    emit({"id": req_id, "result": value})


def method_not_found(req_id) -> None:
    emit({"id": req_id, "error": {"code": -32601, "message": "Method not found"}})


def texts(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, str):
        found.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            found.extend(texts(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(texts(value))
    return found


def credentials() -> tuple[str, str] | None:
    if REPO.startswith("@@"):
        return None
    spec = load_spec()
    session = spec.get("session")
    capability = spec.get("capability")
    if isinstance(session, str) and session and isinstance(capability, str) and capability:
        return session, capability
    root = Path(os.environ.get("XDG_STATE_HOME", ""))
    if not root.is_dir():
        return None
    ancestors = set(ancestor_pids())
    matched = []
    only = []
    for launch_path in root.glob("*/sessions/*/*/launch.json"):
        try:
            launch = json.loads(launch_path.read_text(encoding="utf-8"))
            binding = json.loads(
                (launch_path.parent / "profile" / "binding.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        cap = binding.get("coordination_capability") if isinstance(binding, dict) else None
        launch_id = launch.get("launch_id") if isinstance(launch, dict) else None
        if not isinstance(cap, str) or not cap or not isinstance(launch_id, str) or not launch_id:
            continue
        pair = (launch_id, cap)
        only.append(pair)
        pid = launch.get("pid") if isinstance(launch, dict) else None
        if isinstance(pid, str) and pid.isdigit():
            pid = int(pid)
        if isinstance(pid, int) and pid in ancestors:
            matched.append(pair)
    if len(matched) == 1:
        return matched[0]
    if len(only) == 1:
        return only[0]
    return None


def coordination(*argv: str):
    creds = credentials()
    if creds is None:
        return None
    _session, capability = creds
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from ops.testkit import run
    env = os.environ.copy()
    # The participant resolves by the capability's digest under the member's
    # profile; an alias naming the launch id conflicts with the registered
    # native id and inbox refuses session-profile (b3's reading, 2026-09-23).
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env["COORDINATION_CAPABILITY"] = capability
    tool = str(Path(REPO) / "ops" / "conductor" / "tools" / "coordination.py")
    try:
        return run([sys.executable, tool, *argv], cwd=os.getcwd(), env=env, timeout=30)
    except Exception:
        return None


def inbox() -> str:
    shown = coordination("inbox")
    if shown is None or shown.exit != 0:
        return ""
    return shown.stdout


def ack_lines(predicate) -> None:
    for line in inbox().splitlines():
        if not predicate(line):
            continue
        for token in line.split():
            if re.fullmatch(r"M-\d+", token):
                coordination("ack", token)


def native_id(spec: dict) -> str:
    named = spec.get("native_id")
    if isinstance(named, str) and named:
        return named
    return f"thread-{os.getpid()}"


def turn_id(spec: dict) -> str:
    named = spec.get("turn_id")
    return named if isinstance(named, str) and named else "turn-1"


def turn_frames(spec: dict, native: str) -> list:
    reply = spec.get("reply") if isinstance(spec.get("reply"), str) else "codex-reply-a503"
    turn = turn_id(spec)
    count = spec.get("stream_deltas")
    deltas = [
        {"method": "item/agentMessage/delta", "params": {"threadId": native, "delta": f"{reply}{index} "}}
        for index in range(count)
    ] if isinstance(count, int) and count > 0 else [
        {"method": "item/agentMessage/delta", "params": {"threadId": native, "delta": reply}},
    ]
    return [
        {"method": "turn/started", "params": {"threadId": native, "turn": {"id": turn}}},
        *deltas,
        {"method": "turn/completed", "params": {"threadId": native, "turn": {"id": turn}}},
    ]


def after_submit(spec: dict, params: dict) -> None:
    blob = "\n".join(texts(params))
    reply = spec.get("reply") if isinstance(spec.get("reply"), str) else "codex-reply-a503"
    if "readiness probe" in blob:
        if spec.get("ack_probe"):
            ack_lines(lambda line: "readiness probe" in line)
        return
    if spec.get("send_reply") and blob.strip():
        # The carried message names its id, `[coordination M-n]`; the inbox
        # line for it starts with that id.
        carried = set(re.findall(r"\bM-\d+\b", blob))
        for line in inbox().splitlines():
            if not line.split() or line.split()[0] not in carried:
                continue
            sender = re.search(r"\bfrom (PT-\d+)\b", line)
            if sender:
                coordination("send", "--to", sender.group(1), "--purpose", "inform", "--body", reply)
                break
    if spec.get("ack"):
        ack_lines(lambda line: True)


def handle(raw: str, msg: dict) -> bool:
    method = msg.get("method")
    req_id = msg.get("id")
    if not isinstance(method, str):
        answer = {"response": True, "id": req_id, "raw": raw}
        for key in ("result", "error"):
            if key in msg:
                answer[key] = msg[key]
        log_line(answer)
        return False
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}
    spec = load_spec()
    native = native_id(spec)
    log_line({"method": method, "id": req_id, "params": params, "native_id": native, "raw": raw})
    die = spec.get("die_after") == method
    if spec.get("reject_method") == method:
        if req_id is not None:
            method_not_found(req_id)
        return die
    if method == "initialize" and req_id is not None:
        result(req_id, {"userAgent": "session-codex-fake", "capabilities": {}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and req_id is not None:
        stream(req_id, {"thread": {"id": native}}, [
            {"method": "thread/started", "params": {"threadId": native}},
            {"method": "mcpServer/startupStatus/updated",
             "params": {"name": "coordination", "status": "ready"}},
        ])
    elif method == "turn/start" and req_id is not None:
        TURNS.append({"id": turn_id(spec), "params": params})
        interval = spec.get("stream_interval")
        stream(req_id, {"turn": {"id": turn_id(spec)}}, turn_frames(spec, native),
               float(interval) if isinstance(interval, (int, float)) else 0.0)
        after_submit(spec, params)
    elif method == "thread/read" and req_id is not None:
        result(req_id, {"thread": {"id": native, "turns": list(TURNS)}})
    elif req_id is not None:
        method_not_found(req_id)
    return die


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        sys.stdout.write("session-codex 0.0.0-fake\n")
        return 0
    if argv not in (CHANNEL_ARGV, [PROFILE_FLAG, *CHANNEL_ARGV]):
        log_line({"refused": "channel"})
        sys.stderr.write(
            f"channel: expected {' '.join(CHANNEL_ARGV)}; found {argv!r}; "
            "needed the contracted codex channel argv\n"
        )
        return 2
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if handle(line, msg):
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
