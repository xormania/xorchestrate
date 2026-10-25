"""The codex adapter: one Codex app-server conversation, spoken to by the
member's bridge over `app-server --listen stdio://`.

Contract: ops/conductor/sessions-lever.md, "The adapter boundary,
extended". Slice 1's five methods come from the shared module; this file
adds the channel argv, the handshake, the submission and the
classification of what the app-server sends. The method names and frame
shapes are the installed Codex's (0.155.1), read from its generated
schema: `initialize`, `initialized`, `thread/start`, `turn/start` with
`toolOutput`, `thread/read` with `includeTurns`. Codex frames carry no
`jsonrpc` field.

Stdlib only.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


common = _load("session_adapters_common", Path(__file__).with_name("__init__.py"))
# By path, not by name: a module named instrument already in sys.modules
# may be a copy another caller loaded from a tree since deleted, whose
# lane conf reader is gone with it.
lane_name = _load("session_adapters_instrument",
                  Path(__file__).resolve().parent.parent / "instrument.py").lane_name

# What the bridge answers to each request the app-server may send: every
# approval declined, every question left unanswered, nothing granted.
DECLINE = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"permissions": {}},
    "mcpServer/elicitation/request": {"action": "decline"},
    "item/tool/requestUserInput": {"answers": {}},
    "applyPatchApproval": {"decision": "denied"},
    "execCommandApproval": {"decision": "denied"},
}
UNKNOWN = {"code": -32601, "message": "declined: not handled by the sessions bridge"}

def channel_error(error) -> bool:
    """A ChannelError from any load of channel.py. Modules here are loaded by
    path, so one file can yield two classes and `except` on one misses the
    other (a caller's own RPC, as the adapter rows build)."""
    return type(error).__name__ == "ChannelError" and hasattr(error, "reading")


def carries_tool_output(documents) -> bool | None:
    """Whether a TurnStartParams in the schema documents carries input,
    threadId and toolOutput; None when no TurnStartParams was found."""
    def walk(node, key=""):
        if isinstance(node, dict):
            if key == "TurnStartParams" or node.get("title") == "TurnStartParams":
                yield node
            for name, value in node.items():
                yield from walk(value, name)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)
    found = [node for document in documents for node in walk(document)]
    if not found:
        return None
    return any({"input", "threadId", "toolOutput"} <= set(node.get("properties") or {})
               for node in found)


def tool_output_capability(executable) -> str:
    """The installed app-server's own schema, read for TurnStartParams.toolOutput,
    which a busy submission relies on (the proof's compat.py, a944f31):
    `supported`, `unsupported`, or `unverified <why>`."""
    name = "TurnStartParams.toolOutput"
    argv = [str(executable), "app-server", "generate-json-schema"]
    try:
        shown = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
            [*argv, "--help"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=common.PROBE_TIMEOUT, check=False)
        if shown.returncode:
            return f"unverified {name}: generate-json-schema --help exit {shown.returncode}"
        with tempfile.TemporaryDirectory(prefix="codex-schema-") as out:
            wanted = [*argv, "--out", out]
            if "--experimental" in shown.stdout:
                wanted.append("--experimental")
            made = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
                wanted, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=common.PROBE_TIMEOUT, check=False)
            if made.returncode:
                return f"unverified {name}: generate-json-schema exit {made.returncode}"
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in Path(out).rglob("*.json")]
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return f"unverified {name}: {type(error).__name__}"
    carried = carries_tool_output(documents)
    if carried is None:
        return f"unverified {name}: no TurnStartParams in {len(documents)} schema files"
    return f"{'supported' if carried else 'unsupported'} {name} ({len(documents)} schema files)"


def client() -> dict:
    """The clientInfo `initialize` names, under the lane conf's LANE_NAME."""
    lane = lane_name()
    return {"name": f"{lane}-sessions", "title": f"{lane} sessions bridge", "version": "2"}


def thread_of(params) -> str | None:
    if not isinstance(params, dict):
        return None
    named = params.get("threadId")
    if named is None and isinstance(params.get("thread"), dict):
        named = params["thread"].get("id")
    return named if isinstance(named, str) else None


def turn_of(params) -> str | None:
    """The turn a frame names: `turn.id` in the installed schema (0.155.1),
    `turnId` where a frame carries the id flat."""
    turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
    named = turn.get("id") or params.get("turnId")
    return named if isinstance(named, str) and named else None


class Adapter(common.ProtocolAdapter):
    """Codex's app-server, launched through the profile by the bridge."""

    harness = "codex"
    jsonrpc = False

    def channel(self, member, binding):
        return ["app-server", "--listen", "stdio://"]

    def probe(self, executable):
        """The version, and whether the app-server's schema carries the
        toolOutput a busy submission sends."""
        reading = super().probe(executable)
        reading["capability"] = tool_output_capability(executable)
        return reading

    def handshake(self, rpc):
        """`initialize`, `initialized`, `thread/start`; the thread id."""
        method = "initialize"
        try:
            rpc.request(method, {"clientInfo": client(), "capabilities": {"experimentalApi": True}},
                        timeout=common.SUBMIT_TIMEOUT)
            rpc.notify("initialized")
            method = "thread/start"
            started = rpc.request(method, {}, timeout=common.SUBMIT_TIMEOUT)
        except Exception as error:
            if not channel_error(error):
                raise
            self.refuse_channel(method, error)
        thread = started.get("thread") if isinstance(started, dict) else None
        native = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(native, str) or not native:
            common.refuse("channel", "the codex handshake answering thread/start with thread.id",
                          f"thread/start: {json.dumps(started)[:200]}",
                          "read the member's channel.log; the harness did not speak its protocol")
        self.native = native
        return native

    def submit(self, rpc, text, *, busy):
        """A new turn when idle; into the active turn as `toolOutput` when busy."""
        params = {"threadId": self.native, "input": [{"type": "text", "text": text}]}
        if busy:
            params = {"threadId": self.native, "input": [],
                      "toolOutput": {"name": common.SERVER, "namespace": lane_name(),
                                     "output": text}}
        try:
            result = rpc.request("turn/start", params, timeout=common.SUBMIT_TIMEOUT)
        except Exception as error:
            if not channel_error(error):
                raise
            return self.settled("turn/start", error)
        turn = result.get("turn") if isinstance(result, dict) else None
        ident = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(ident, str) or not ident:
            return f"uncertain turn/start: answered without turn.id: {json.dumps(result)[:200]}"
        return f"accepted turn {ident}"

    def reconcile(self, rpc, message: str) -> str:
        """`seen M-n` when the thread's history carries the message's marker."""
        try:
            read = rpc.request("thread/read", {"threadId": self.native, "includeTurns": True},
                               timeout=common.SUBMIT_TIMEOUT)
        except Exception as error:
            if not channel_error(error):
                raise
            return f"unseen {message}: thread/read {error.reading}"
        if common.marker(message) in json.dumps(read, ensure_ascii=False):
            return f"seen {message}"
        return f"unseen {message}: not in thread/read"

    def observe(self, rpc, frame):
        """One received frame, classified; a request is answered here."""
        if "reconcile" in frame:
            return self.reconcile(rpc, str(frame["reconcile"]))
        method = frame.get("method")
        params = frame.get("params") if isinstance(frame.get("params"), dict) else {}
        # Another thread's frame is refused before anything is sent: a
        # request naming it is that session's to answer, never this bridge's.
        other = self.other_native(thread_of(params))
        if other is not None:
            return other
        if "id" in frame and method is not None:
            return self.answer(rpc, frame, method, params)
        if method == "turn/started":
            self.turn = turn_of(params) or self.turn
            return "activity active"
        if method == "turn/completed":
            done, self.turn = turn_of(params) or self.turn, None
            return f"done {done}"
        if method == "item/agentMessage/delta":
            return f"output {params.get('delta', '')}"
        if method == "mcpServer/startupStatus/updated":
            if params.get("name") == common.SERVER and params.get("status") == "ready":
                return f"tool {common.SERVER} ready"
            return f"activity {params.get('name')} {params.get('status')}"
        if method in ("item/started", "item/completed"):
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            if item.get("type") == "mcpToolCall":
                return f"tool {item.get('server')}/{item.get('tool')}"
            return "activity active"
        named = thread_of(params)
        return f"native {named}" if named else f"other {method}"

    def answer(self, rpc, frame, method, params):
        """Decline what the app-server asks of this thread; the prompt is
        printed, not kept. observe has refused another thread's request."""
        title = self.title(params, method)
        if method in DECLINE:
            rpc.answer(frame, result=DECLINE[method])
            print(f"permission asked: {title}; answered {json.dumps(DECLINE[method])}", flush=True)
            return f"permission {title}"
        rpc.answer(frame, error=UNKNOWN)
        print(f"permission asked: {title}; answered declined, not handled", flush=True)
        return f"permission {title}"

    @staticmethod
    def title(params, method) -> str:
        """What the prompt asked, for the pane: its title, reason, command or
        message, else the method."""
        title = (params.get("title") or params.get("reason") or params.get("command")
                 or params.get("message") or method)
        if isinstance(title, list):
            title = " ".join(str(part) for part in title)
        return str(title)
