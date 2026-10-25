# testkit: exempt fixture program planted as the grok ACP agent
"""Fake Grok ACP agent for sessions slice 2.

Planted through Tools.plant("grok", script=...) by the bridge rows
(test_session_messages.py) and the adapter rows
(test_session_adapters_grok.py). It answers only the contracted argv,
`grok --no-auto-update agent stdio` (sessions-lever.md §The adapter
boundary, extended, `channel`); any other argv is logged and refused
`channel` on the wire. `--version` answers the profile's version probe.

The registry spec beside this executable (fakes/<name>.spec.json, or
TESTKIT_FAKE_SPEC when it names this program's spec) says:

- ``native_id``: the session id to answer; empty means ``session-<pid>``;
- ``advertise_auth``: whether ``initialize`` advertises ``authenticate``
  (default true);
- ``reply``: the ``agent_message_chunk`` text;
- ``turn``: the turn ``_x.ai/session/prompt_complete`` names (default
  ``turn-1``);
- ``permission_title``: when set, a prompt also asks
  ``session/request_permission`` with that title, id ``perm-1``, offering
  ACP's two options, ``allow-once`` (allow_once) and ``reject-once``
  (reject_once);
- ``permission_raw_input``: when set with ``permission_title``, the
  request's ``toolCall`` is a ``use_tool`` call in grok's own namespace
  (``_meta`` ``x.ai/tool`` ``grok_build``/``use_tool``) carrying this
  object verbatim as ``rawInput``, as grok 1.0.40 asks before a call on
  an MCP server (sessions-lever.md, ``observe``'s permission exception);
  ``permission_tool`` ``[namespace, name]`` replaces that ``_meta`` tool;
- ``permission_options``: ``[optionId, kind]`` pairs replacing the two
  default options; live grok offers ``always-allow`` (allow_always) too;
- ``stray_id``: when set, a prompt's frames carry, after the first
  ``session/update``, a response whose id is this value and which no
  request of the client's matches, as grok 1.0.40 sends its own
  ``skills-reload`` response mid-turn;
- ``reject_method``: the method answered -32601;
- ``die_after``: the method after whose answer the process exits;
- ``ack_probe``, ``ack``, ``send_reply``, and ``session`` with
  ``capability``: the bridge rows' acks and replies, carried through
  coordination.py as the member's own participant.

Speaks JSON lines on stdio: ``initialize`` (protocolVersion 1),
``authenticate``, ``session/new`` → ``sessionId`` and a ``session/update``
``tool_call`` naming ``coordination``; ``session/prompt`` and
``_x.ai/interject`` → a ``session/update`` ``agent_message_chunk``, then
``_x.ai/session/prompt_complete``, then the permission request when the
spec names a title, then the result.

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
from pathlib import Path

REPO = "@@REPO@@"
CHANNEL_ARGV = ["--no-auto-update", "agent", "stdio"]


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


def stream(req_id, value: dict, frames: list) -> None:
    """The frames, then the result, as ACP orders a prompt's updates
    before its answer; the frames are logged before either is written."""
    for frame in frames:
        if "method" in frame:
            append(".emitted.jsonl", frame)
    for frame in frames:
        emit(frame)
    result(req_id, value)


def result(req_id, value: dict) -> None:
    emit({"jsonrpc": "2.0", "id": req_id, "result": value})


def method_not_found(req_id) -> None:
    emit({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": "Method not found"},
    })


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
    return f"session-{os.getpid()}"


def prompt_frames(spec: dict, native: str) -> list:
    reply = spec.get("reply") if isinstance(spec.get("reply"), str) else "grok-reply-a503"
    turn = spec.get("turn") if isinstance(spec.get("turn"), str) and spec.get("turn") else "turn-1"
    frames = [
        {"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": native,
            "update": {"sessionUpdate": "agent_message_chunk",
                       "content": {"type": "text", "text": reply}},
        }},
        {"jsonrpc": "2.0", "method": "_x.ai/session/prompt_complete",
         "params": {"sessionId": native, "turn": turn}},
    ]
    stray = spec.get("stray_id")
    if isinstance(stray, str) and stray:
        frames.insert(1, {"jsonrpc": "2.0", "id": stray, "result": {}})
    title = spec.get("permission_title")
    if isinstance(title, str) and title:
        call = {"toolCallId": "call-1", "title": title}
        raw = spec.get("permission_raw_input")
        if isinstance(raw, dict):
            call["rawInput"] = raw
            named = spec.get("permission_tool")
            namespace, name = named if isinstance(named, list) else ["grok_build", "use_tool"]
            call["_meta"] = {"x.ai/tool": {"namespace": namespace, "name": name}}
        pairs = spec.get("permission_options")
        if not isinstance(pairs, list):
            pairs = [["allow-once", "allow_once"], ["reject-once", "reject_once"]]
        options = [{"optionId": ident, "name": ident, "kind": kind} for ident, kind in pairs]
        frames.append({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission",
                       "params": {"sessionId": native, "title": title,
                                  "toolCall": call, "options": options}})
    return frames


def after_submit(spec: dict, params: dict) -> None:
    blob = "\n".join(texts(params))
    reply = spec.get("reply") if isinstance(spec.get("reply"), str) else "grok-reply-a503"
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
        body = {"protocolVersion": 1}
        if spec.get("advertise_auth", True):
            body["authMethods"] = [{"id": "agent", "name": "agent"}]
        result(req_id, body)
    elif method == "authenticate" and req_id is not None:
        result(req_id, {})
    elif method == "session/new" and req_id is not None:
        tool = {"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": native,
            "update": {"sessionUpdate": "tool_call", "toolCallId": "discover-1",
                       "title": "coordination", "name": "coordination"},
        }}
        frames = [tool]
        named = [entry.get("name") for entry in params.get("mcpServers") or []
                 if isinstance(entry, dict)]
        if "coordination" in named:
            frames.append({"jsonrpc": "2.0", "method": "_x.ai/mcp/server_status", "params": {
                "name": "coordination", "source": "local", "status": "ready",
                "reason": "initialized",
            }})
        stream(req_id, {"sessionId": native}, frames)
    elif method in {"session/prompt", "_x.ai/interject"} and req_id is not None:
        stream(req_id, {"stopReason": "end_turn"}, prompt_frames(spec, native))
        after_submit(spec, params)
    elif req_id is not None:
        method_not_found(req_id)
    return die


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        sys.stdout.write("session-grok 0.0.0-fake\n")
        return 0
    if argv != CHANNEL_ARGV:
        log_line({"refused": "channel"})
        sys.stderr.write(
            f"channel: expected {' '.join(CHANNEL_ARGV)}; found {argv!r}; "
            "needed the contracted grok channel argv\n"
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
