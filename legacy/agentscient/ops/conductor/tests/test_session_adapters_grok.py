"""Grok adapter, sessions slice 2.

Contract: `ops/conductor/sessions-lever.md` "The adapter boundary, extended"
(`transport`, `channel`, `handshake`, `submit`, `observe`) and the grok
fake in "Tests". The planted peer is spoken to by
`session_adapters/channel.py`'s `Channel`, which spawns it with the
contracted argv, `--no-auto-update agent stdio`, over its pipes; the
world owns the process and reaps it at close (origin:
20260923T020912Z-review-codex-9d8b8c #19). The fake reads its spec from
`TESTKIT_FAKE_SPEC` and logs every line it receives to the registry's
call log, and every frame it sends to `grok.emitted.jsonl` beside it. A
missing adapter or channel fails the row's own reading, not an import.

`session/update` carrying the chunk text reads `output <text>`.
`_x.ai/session/prompt_complete` carrying the turn reads `done <turn>`.
`session/update` carrying a `tool_call` reads `tool <name>`; the fake
sends one naming `coordination` after `session/new`.
A frame naming another sessionId reads `refused native <id>` (the XO's
ruling of 2026-09-23 on the contract's words).
`session/new` names the binding's ``native_settings.mcp_servers`` as ACP
entries; when one is ``coordination`` the fake answers
`_x.ai/mcp/server_status` ready, as b3 measured grok 1.0.40 do
(7493b716), and that frame reads `tool coordination ready`.
`session/request_permission` offers ACP's `allow-once` and `reject-once`
options; the contract's `decline` is the answer selecting `reject-once`,
and the frame reads `permission <title>`.

The one exception (sessions-lever.md `observe`, at 7952a9a6): a `use_tool`
call whose `rawInput.tool_name` is exactly `coordination__<tool>` with an
input object is answered `allow-once` and reads `permission coordination
<tool> allowed`; the frame is the one agentscient-b3 measured on grok
1.0.40, `session/request_permission` for `coordination__coord_probe`
offering always-allow, allow-once and reject-once. Its neighbours stay
declined: another server's tool, a target with no tool, a call with no
input object, a call that is not grok's own use_tool, and always-allow is
never chosen (origin: A-527, b3's live reading, and the contract line).

A permission request naming another session reads `refused native <id>`
and is never answered, the coordination call included: an answer would
grant a tool in a conversation the bridge does not own (A-522 #2, record
20260923T040605Z-review-codex-6ed2cb, grok.py observe and answer at
c4dd8f12). The clean neighbour is the own-session allow-once row.

A response whose id matches no request the bridge sent, as grok 1.0.40
sends `skills-reload` mid-turn, is dropped and written to the channel's
log, and the turn's later frames still arrive (origin: A-527, aa's
e8cae56c, and the contract line); the RPC rows for it are in
test_session_channel.py.

Planted faults: `session_adapters_grok_faults.py` beside this module,
anchored on the adapter at bf924ee7; the clean neighbours are the
unmodified subject and the no-server row.

The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_session_adapters_grok.py`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ops.testkit import RefusalCase, Result, World, refusal_wire

REPO = Path(__file__).resolve().parents[3]
ADAPTERS = REPO / "ops" / "conductor" / "tools" / "session_adapters"
GROK = ADAPTERS / "grok.py"
CHANNEL = ADAPTERS / "channel.py"
FAKES = Path(__file__).resolve().parent / "fakes"
FAKE_SRC = FAKES / "session_grok.py"
ORIGIN = "sessions-lever.md#slice-2 adapters"
SHORT_ROOT = "/tmp"
SESSION_ID = "sess-grok-506"
OTHER_SESSION = "sess-other"
UPDATE_TEXT = "pane-line"
TURN = "turn-7"
PERMISSION_TITLE = "run the suite"
USE_TOOL_TITLE = "use_tool"
LIVE_OPTIONS = [["always-allow", "allow_always"], ["allow-once", "allow_once"],
                ["reject-once", "reject_once"]]
STRAY = "skills-reload"
ORIGIN_527 = "A-527; sessions-lever.md observe, permission exception, 7952a9a6"
ORIGIN_522 = "A-522 #2, 20260923T040605Z-review-codex-6ed2cb"
FOREIGN_PERMISSION = "perm-foreign"
OPERATOR_TEXT = "operator line for gee"
CHANNEL_TAIL = ["--no-auto-update", "agent", "stdio"]
HANDSHAKE = ["initialize", "session/new"]
HANDSHAKE_AUTH = ["initialize", "authenticate", "session/new"]


def fake_text(path: Path) -> str:
    """The planted peer, its shebang this interpreter."""
    if not path.is_file():
        raise AssertionError(f"INVALID: planted program missing: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    return f"#!{sys.executable}\n" + text


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def channel_module(adapter_module, fallback: Path, name: str):
    """The channel module the adapter itself catches errors from.

    Each by-path load of `channel.py` is a distinct module, and an adapter
    catches only its own copy's `ChannelError`; the bridge builds its
    channel through the adapter for that reason. The Channel is taken from
    the adapter's shared module when it exposes one, else `channel.py` is
    loaded by path.
    """
    common = getattr(adapter_module, "common", None)
    module = getattr(common, "channel", None)
    if module is not None and hasattr(module, "Channel"):
        return module
    return load_module(fallback, name)


def grok_channel_error(error) -> bool:
    """A ChannelError from any by-path load of channel.py."""
    return type(error).__name__ == "ChannelError"


def selected(result) -> str | None:
    """The optionId an ACP permission answer selected, or None."""
    outcome = result.get("outcome") if isinstance(result, dict) else None
    if isinstance(outcome, dict) and outcome.get("outcome") == "selected":
        return outcome.get("optionId")
    return None


def open_world(prefix: str) -> World:
    """A world rooted under /tmp, as the other slice 2 modules root theirs."""
    saved = tempfile.tempdir
    tempfile.tempdir = SHORT_ROOT
    try:
        return World(prefix=prefix)
    finally:
        tempfile.tempdir = saved


class TheGrokAdapter(unittest.TestCase):
    """Slice 2's five grok methods against the planted ACP peer.

    Rows are addressed by id. origin: sessions-lever.md#slice-2 adapters.
    """

    def setUp(self):
        self.world = open_world("session-grok-")
        self.addCleanup(self.world.close)
        self.emitted_path = self.world.tools.log_path("grok").with_name("grok.emitted.jsonl")
        self.planted = self.world.tools.plant("grok", script=fake_text(FAKE_SRC))
        self._module = None
        self._adapter = None
        self.binding_path = self.world.plant_text("binding.json", "{}\n", recognisable="{")

    def adapter(self):
        """One adapter per case, from one load of its module, so the
        channel the case builds is the one that adapter catches from."""
        if self._module is not None:
            return self._adapter
        if not GROK.is_file():
            return None
        try:
            module = load_module(GROK, "session_adapters_grok")
        except Exception:
            return None
        self._module = module
        cls = getattr(module, "Adapter", None)
        if not isinstance(cls, type):
            return None
        try:
            self._adapter = cls()
        except Exception:
            return None
        return self._adapter

    def open_channel(self, spec_path: Path, stderr=None):
        """channel.Channel spawning the planted peer with the contracted argv."""
        if not CHANNEL.is_file():
            return None
        if self._module is None:
            self.adapter()
        try:
            module = channel_module(self._module, CHANNEL, "session_adapters_channel")
            channel = module.Channel(
                [str(self.planted), *CHANNEL_TAIL], jsonrpc=True,
                env=self.world.env(extra={"TESTKIT_FAKE_SPEC": str(spec_path)}),
                cwd=str(self.world.cwd), stderr=stderr,
            )
        except Exception:
            return None
        self.world.own(channel.process)
        self.addCleanup(channel.close)
        return channel

    def member(self) -> dict:
        return {"name": "gee", "harness": "grok"}

    def binding(self) -> dict:
        return {
            "executable": str(self.planted),
            "binding": str(self.binding_path),
            "harness": "grok",
        }

    def lines(self, path: Path) -> list[dict] | None:
        if not path.is_file():
            return None
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def requests(self) -> list[dict]:
        return [call for call in self.world.tools.calls("grok") if "method" in call]

    def methods(self) -> list[str] | None:
        rows = self.requests()
        if not rows:
            return None
        return [row.get("method") for row in rows]

    def spawn(self, stderr=None, **overrides):
        """The planted peer behind a Channel, or None with no channel."""
        return self.open_channel(self.write_spec(**overrides), stderr=stderr)

    def connect_in(self, directory: Path, **overrides):
        """The planted peer spawned the bridge's way, ``adapter.connect``,
        in ``directory``: the member's worktree, not this process's cwd."""
        spec_path = self.write_spec(**overrides)
        channel = self.adapter().connect(
            [str(self.planted), *CHANNEL_TAIL],
            env=self.world.env(extra={"TESTKIT_FAKE_SPEC": str(spec_path)}),
            cwd=str(directory),
        )
        self.world.own(channel.process)
        self.addCleanup(channel.close)
        return channel

    def write_spec(self, **overrides) -> Path:
        spec_path = self.world.tools.spec_path("grok")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        fields = {
            "native_id": SESSION_ID,
            "advertise_auth": False,
            "reject_method": None,
            "die_after": None,
            "permission_title": None,
            "reply": UPDATE_TEXT,
            "turn": TURN,
        }
        fields.update(overrides)
        spec.update(fields)
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
        landed = json.loads(spec_path.read_text(encoding="utf-8"))
        if landed.get("native_id") != fields["native_id"]:
            raise AssertionError("INVALID: grok spec did not land intact")
        return spec_path

    def wait_decline(self) -> bool:
        """The peer received, as the answer to `perm-1`, the outcome
        selecting the `reject-once` option: ACP's decline."""
        deadline = time.monotonic() + 2
        while True:
            answers = [
                call for call in self.world.tools.calls("grok")
                if call.get("response") and call.get("id") == "perm-1"
            ]
            if any(selected(call.get("result")) == "reject-once" for call in answers):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def answer_to_permission(self) -> str | None:
        """What the peer received as the answer to `perm-1`: the selected
        optionId, `cancelled`, `error`, or None when no answer came."""
        deadline = time.monotonic() + 2
        while True:
            for call in self.world.tools.calls("grok"):
                if not (call.get("response") and call.get("id") == "perm-1"):
                    continue
                if "error" in call:
                    return "error"
                outcome = (call.get("result") or {}).get("outcome")
                if isinstance(outcome, dict) and outcome.get("outcome") == "cancelled":
                    return "cancelled"
                return selected(call.get("result"))
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)

    def permission_reading(self, raw_input: dict, options=LIVE_OPTIONS, tool=None):
        """The adapter's reading of a `use_tool` permission request carrying
        ``raw_input``, and the answer the peer received; ``tool`` replaces
        the request's `_meta` tool, `[namespace, name]`."""
        adapter = self.adapter()
        if adapter is None:
            return None, None, "no adapter"
        rpc = self.spawn(permission_title=USE_TOOL_TITLE, permission_raw_input=raw_input,
                         permission_options=options, permission_tool=tool)
        try:
            adapter.handshake(rpc)
            adapter.submit(rpc, OPERATOR_TEXT, busy=False)
            frame = next(
                item for item in (self.lines(self.emitted_path) or [])
                if item.get("method") == "session/request_permission"
            )
            with contextlib.redirect_stdout(io.StringIO()):
                reading = adapter.observe(rpc, frame)
        except Exception as exc:
            return None, None, exc
        return reading, self.answer_to_permission(), None

    def test_observe_a_coordination_use_tool_is_allowed_once(self):
        """origin: A-527, b3's live reading; sessions-lever.md observe at 7952a9a6"""
        with self.subTest(id="grok-permission-coordination-allowed", origin=ORIGIN_527):
            reading, answer, error = self.permission_reading(
                {"tool_name": "coordination__coord_probe", "tool_input": {}})
            self.assertIsNone(error, error)
            self.assertEqual(
                (reading, answer),
                ("permission coordination coord_probe allowed", "allow-once"),
            )

    def test_observe_every_other_use_tool_stays_declined(self):
        """origin: A-527, b3's live reading; sessions-lever.md observe at 7952a9a6"""
        probe = {"tool_name": "coordination__coord_probe", "tool_input": {}}
        rows = [
            ("grok-permission-other-server", {"tool_name": "shell__run", "tool_input": {"cmd": "ls"}},
             LIVE_OPTIONS, None),
            ("grok-permission-no-tool", {"tool_name": "coordination__", "tool_input": {}},
             LIVE_OPTIONS, None),
            ("grok-permission-no-input", {"tool_name": "coordination__coord_probe"},
             LIVE_OPTIONS, None),
            ("grok-permission-input-not-object",
             {"tool_name": "coordination__coord_probe", "tool_input": "{}"}, LIVE_OPTIONS, None),
            ("grok-permission-prefix-only",
             {"tool_name": "xcoordination__coord_probe", "tool_input": {}}, LIVE_OPTIONS, None),
            ("grok-permission-no-allow-once", probe,
             [["always-allow", "allow_always"], ["reject-once", "reject_once"]], None),
            ("grok-permission-not-use-tool", probe, LIVE_OPTIONS, ["grok_build", "run_command"]),
            ("grok-permission-other-namespace", probe, LIVE_OPTIONS, ["mcp", "use_tool"]),
        ]
        for ident, raw_input, options, tool in rows:
            with self.subTest(id=ident, origin=ORIGIN_527):
                self.world.tools.log_path("grok").write_text("", encoding="utf-8")
                self.emitted_path.write_text("", encoding="utf-8")
                reading, answer, error = self.permission_reading(raw_input, options, tool)
                self.assertIsNone(error, error)
                self.assertEqual((reading, answer), (f"permission {USE_TOOL_TITLE}", "reject-once"))

    def answered_before_sentinel(self, rpc, ident: str) -> bool | None:
        """Whether the peer received an answer to ``ident``. A sentinel
        request goes after it; the peer reads its input in order, so once
        the sentinel's answer is back any earlier answer is in its log.
        None when the sentinel never came back."""
        try:
            rpc.request("sentinel/ping", {}, timeout=3)
        except Exception as exc:
            if not grok_channel_error(exc):
                return None
        if "sentinel/ping" not in (self.methods() or []):
            return None
        return any(call.get("response") and call.get("id") == ident
                   for call in self.world.tools.calls("grok"))

    def test_a_foreign_permission_request_is_refused_unanswered(self):
        """origin: A-522 #2, 20260923T040605Z-review-codex-6ed2cb"""
        rows = [
            ("grok-permission-foreign-coordination",
             {"tool_name": "coordination__coord_probe", "tool_input": {}}),
            ("grok-permission-foreign-other-tool",
             {"tool_name": "shell__run", "tool_input": {"cmd": "ls"}}),
        ]
        for ident, raw_input in rows:
            with self.subTest(id=ident, origin=ORIGIN_522):
                adapter = self.adapter()
                reading = None
                answered = None
                error = None
                if adapter is not None:
                    rpc = self.spawn()
                    frame = {
                        "jsonrpc": "2.0", "id": FOREIGN_PERMISSION,
                        "method": "session/request_permission",
                        "params": {
                            "sessionId": OTHER_SESSION, "title": USE_TOOL_TITLE,
                            "toolCall": {
                                "toolCallId": "call-foreign", "title": USE_TOOL_TITLE,
                                "rawInput": raw_input,
                                "_meta": {"x.ai/tool": {"namespace": "grok_build",
                                                        "name": "use_tool"}},
                            },
                            "options": [{"optionId": ident_, "name": ident_, "kind": kind}
                                        for ident_, kind in LIVE_OPTIONS],
                        },
                    }
                    try:
                        adapter.handshake(rpc)
                        with contextlib.redirect_stdout(io.StringIO()):
                            reading = adapter.observe(rpc, frame)
                        answered = self.answered_before_sentinel(rpc, FOREIGN_PERMISSION)
                    except Exception as exc:
                        error = exc
                self.assertIsNone(error, error)
                self.assertEqual((reading, answered), (f"refused native {OTHER_SESSION}", False))

    def test_a_stray_response_mid_turn_is_dropped_and_logged(self):
        """origin: A-527, aa's e8cae56c; sessions-lever.md the bridge at 7952a9a6"""
        with self.subTest(id="grok-stray-response-dropped", origin=ORIGIN_527):
            adapter = self.adapter()
            log_path = self.world.root / "channel.log"
            received = None
            state = None
            logged = None
            error = None
            if adapter is not None:
                with open(log_path, "ab") as log:
                    rpc = self.spawn(stderr=log, stray_id=STRAY, permission_title=PERMISSION_TITLE)
                    try:
                        adapter.handshake(rpc)
                        adapter.submit(rpc, OPERATOR_TEXT, busy=False)
                        received = []
                        deadline = time.monotonic() + 3
                        while "session/request_permission" not in received and time.monotonic() < deadline:
                            frame = rpc.next_frame(0.1)
                            if frame is not None:
                                received.append(frame.get("method"))
                        state = (rpc.dead.is_set(), len(getattr(rpc, "strays", [])))
                    except Exception as exc:
                        error = exc
                logged = [line for line in log_path.read_text(encoding="utf-8").splitlines()
                          if line.startswith("channel: dropped") and repr(STRAY) in line]
            self.assertIsNone(error, error)
            self.assertEqual(received[-3:] if received else received,
                             ["session/update", "_x.ai/session/prompt_complete",
                              "session/request_permission"])
            self.assertEqual(state, (False, 1))
            self.assertEqual(len(logged or []), 1, logged)

    def test_transport_reads_protocol(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-transport-protocol", origin=ORIGIN):
            adapter = self.adapter()
            reading = None
            error = None
            if adapter is not None:
                try:
                    reading = adapter.transport()
                except Exception as exc:
                    error = exc
            self.assertIsNone(error, error)
            self.assertEqual(reading, "protocol")

    def test_channel_argv_ends_with_agent_stdio(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-channel-agent-stdio", origin=ORIGIN):
            adapter = self.adapter()
            parts = None
            error = None
            if adapter is not None:
                try:
                    parts = [str(item) for item in adapter.channel(self.member(), self.binding())]
                except Exception as exc:
                    error = exc
            self.assertIsNone(error, error)
            tail = None if parts is None else parts[-3:]
            self.assertEqual(tail, CHANNEL_TAIL)

    def test_handshake_returns_the_spec_session_id(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-handshake-session-id", origin=ORIGIN):
            adapter = self.adapter()
            native = None
            version = None
            error = None
            if adapter is not None:
                rpc = self.spawn()
                try:
                    native = adapter.handshake(rpc)
                except Exception as exc:
                    error = exc
                for row in self.requests():
                    if row.get("method") == "initialize":
                        version = (row.get("params") or {}).get("protocolVersion")
            self.assertIsNone(error, error)
            self.assertEqual((native, version), (SESSION_ID, 1))

    def test_session_new_names_the_member_worktree(self):
        """origin: sessions-lever.md#slice-2 adapters

        The bridge runs in a directory other than the member's worktree;
        ``session/new`` carries the worktree the harness was spawned in,
        never the bridge's own cwd (0e's review of b3's grok.py, relayed by
        the XO 2026-09-23).
        """
        with self.subTest(id="grok-session-new-cwd", origin=ORIGIN):
            worktree = self.world.root / "member-worktree"
            worktree.mkdir()
            self.assertNotEqual(Path.cwd().resolve(), worktree.resolve())
            adapter = self.adapter()
            self.assertIsNotNone(adapter, "no grok adapter")
            rpc = self.connect_in(worktree)
            adapter.handshake(rpc)
            opened = [row for row in self.requests() if row.get("method") == "session/new"]
            self.assertEqual(len(opened), 1, self.requests())
            self.assertEqual((opened[0].get("params") or {}).get("cwd"), str(worktree))

    def opened_with_servers(self, servers: dict):
        """The binding's servers through ``channel``, then the handshake:
        the ``mcpServers`` session/new carried and the frames' readings."""
        adapter = self.adapter()
        self.assertIsNotNone(adapter, "no grok adapter")
        adapter.channel(self.member(), {"native_settings": {"mcp_servers": servers}})
        rpc = self.spawn()
        adapter.handshake(rpc)
        opened = [row for row in self.requests() if row.get("method") == "session/new"]
        self.assertEqual(len(opened), 1, self.requests())
        sent = (opened[0].get("params") or {}).get("mcpServers")
        readings = [adapter.observe(rpc, frame) for frame in self.lines(self.emitted_path) or []]
        return sent, readings

    def test_session_new_names_the_coordination_server(self):
        """origin: sessions-lever.md#slice-2 adapters

        A binding naming the coordination server sends it in
        ``session/new``, and grok's ready status reads ``tool coordination
        ready`` (11's review of fed6a4ce, grok.py:79, relayed by the XO
        2026-09-23).
        """
        with self.subTest(id="grok-session-new-mcp-named", origin=ORIGIN):
            server = {"command": "python3", "args": ["coordination-mcp.py"], "env": {"LANE": "a"}}
            sent, readings = self.opened_with_servers({"coordination": server})
            self.assertEqual(
                sent,
                [{"name": "coordination", "command": "python3", "args": ["coordination-mcp.py"],
                  "env": [{"name": "LANE", "value": "a"}]}],
            )
            self.assertIn("tool coordination ready", readings)

    def test_session_new_with_no_server_reads_no_ready_tool(self):
        """origin: sessions-lever.md#slice-2 adapters

        The clean neighbour: a binding naming no server sends ``[]`` and
        no frame reads ``tool coordination ready``.
        """
        with self.subTest(id="grok-session-new-mcp-none", origin=ORIGIN):
            sent, readings = self.opened_with_servers({})
            self.assertEqual(sent, [])
            self.assertNotIn("tool coordination ready", readings)

    def test_handshake_calls_authenticate_only_when_advertised(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-handshake-authenticate", origin=ORIGIN):
            adapter = self.adapter()
            native = None
            methods = None
            error = None
            if adapter is not None:
                rpc = self.spawn(advertise_auth=True)
                try:
                    native = adapter.handshake(rpc)
                except Exception as exc:
                    error = exc
                methods = self.methods()
            self.assertIsNone(error, error)
            self.assertEqual((methods, native), (HANDSHAKE_AUTH, SESSION_ID))

    def test_handshake_skips_authenticate_when_it_is_not_advertised(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-handshake-no-authenticate", origin=ORIGIN):
            adapter = self.adapter()
            native = None
            methods = None
            error = None
            if adapter is not None:
                rpc = self.spawn(advertise_auth=False)
                try:
                    native = adapter.handshake(rpc)
                except Exception as exc:
                    error = exc
                methods = self.methods()
            self.assertIsNone(error, error)
            self.assertEqual((methods, native), (HANDSHAKE, SESSION_ID))

    def test_handshake_dying_after_initialize_refuses_channel_naming_session_new(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        case = RefusalCase(
            id="grok-handshake-dies-after-initialize",
            argv=("handshake",),
            cls="channel",
            named=("session/new",),
            origin=ORIGIN,
        )
        with self.subTest(id=case.id, origin=case.origin):
            refusal_wire(case, self.channel_refusal())

    def channel_refusal(self) -> Result:
        adapter = self.adapter()
        cwd = str(self.world.cwd)
        if adapter is None:
            return Result(("handshake",), 0, "", "", 0.0, cwd)
        rpc = self.spawn(die_after="initialize")
        try:
            adapter.handshake(rpc)
        except Exception as exc:
            line = exc.line() if callable(getattr(exc, "line", None)) else str(exc)
            stderr = line if line.endswith("\n") else line + "\n"
            return Result(("handshake",), 2, "", stderr, 0.0, cwd)
        return Result(("handshake",), 0, "handshake returned\n", "", 0.0, cwd)

    def test_submit_idle_sends_session_prompt_and_reads_written(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-submit-idle", origin=ORIGIN):
            reading, sent, carried, error = self.submitted(busy=False)
            self.assertIsNone(error, error)
            self.assertEqual((reading, sent, carried), ("written", True, True))

    def test_submit_busy_sends_interject(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-submit-busy", origin=ORIGIN):
            reading, sent, carried, error = self.submitted(busy=True)
            self.assertIsNone(error, error)
            self.assertEqual((reading, sent, carried), ("written", True, True))

    def submitted(self, *, busy: bool):
        adapter = self.adapter()
        if adapter is None:
            return None, None, None, None
        method = "_x.ai/interject" if busy else "session/prompt"
        other = "session/prompt" if busy else "_x.ai/interject"
        rpc = self.spawn()
        error = None
        reading = None
        try:
            adapter.handshake(rpc)
            reading = adapter.submit(rpc, OPERATOR_TEXT, busy=busy)
        except Exception as exc:
            error = exc
        rows = self.requests()
        methods = [row.get("method") for row in rows]
        sent = method in methods and other not in methods
        carried = any(
            row.get("method") == method and OPERATOR_TEXT in json.dumps(row)
            for row in rows
        )
        return reading, sent, carried, error

    def test_submit_method_not_found_reads_unsupported_session_prompt(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-submit-unsupported", origin=ORIGIN):
            adapter = self.adapter()
            reading = None
            saw = None
            error = None
            if adapter is not None:
                rpc = self.spawn(reject_method="session/prompt")
                try:
                    adapter.handshake(rpc)
                    reading = adapter.submit(rpc, OPERATOR_TEXT, busy=False)
                except Exception as exc:
                    error = exc
                saw = "session/prompt" in (self.methods() or [])
            self.assertIsNone(error, error)
            self.assertEqual((reading, saw), ("unsupported session/prompt", True))

    def test_observe_classifies_each_emitted_frame(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-observe-emitted", origin=ORIGIN):
            adapter = self.adapter()
            readings = None
            error = None
            if adapter is not None:
                rpc = self.spawn()
                try:
                    adapter.handshake(rpc)
                    adapter.submit(rpc, OPERATOR_TEXT, busy=False)
                    readings = [adapter.observe(rpc, frame) for frame in self.lines(self.emitted_path) or []]
                except Exception as exc:
                    error = exc
            self.assertIsNone(error, error)
            self.assertEqual(
                readings, ["tool coordination", f"output {UPDATE_TEXT}", f"done {TURN}"],
            )

    def test_observe_a_frame_naming_another_session_reads_refused_native(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-observe-other-session", origin=ORIGIN):
            adapter = self.adapter()
            reading = None
            error = None
            if adapter is not None:
                rpc = self.spawn()
                frame = {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": OTHER_SESSION,
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "nope"},
                        },
                    },
                }
                try:
                    adapter.handshake(rpc)
                    reading = adapter.observe(rpc, frame)
                except Exception as exc:
                    error = exc
            self.assertIsNone(error, error)
            self.assertEqual(reading, f"refused native {OTHER_SESSION}")

    def test_observe_permission_is_declined_and_reads_the_title(self):
        """origin: sessions-lever.md#slice-2 adapters"""
        with self.subTest(id="grok-observe-permission", origin=ORIGIN):
            adapter = self.adapter()
            reading = None
            declined = None
            printed = None
            error = None
            if adapter is not None:
                rpc = self.spawn(permission_title=PERMISSION_TITLE)
                try:
                    adapter.handshake(rpc)
                    adapter.submit(rpc, OPERATOR_TEXT, busy=False)
                    frame = next(
                        item for item in (self.lines(self.emitted_path) or [])
                        if item.get("method") == "session/request_permission"
                    )
                    buffer = io.StringIO()
                    with contextlib.redirect_stdout(buffer):
                        reading = adapter.observe(rpc, frame)
                    printed = PERMISSION_TITLE in buffer.getvalue()
                    declined = self.wait_decline()
                except Exception as exc:
                    error = exc
            self.assertIsNone(error, error)
            self.assertEqual(
                (reading, declined, printed),
                (f"permission {PERMISSION_TITLE}", True, True),
            )


if __name__ == "__main__":
    unittest.main()
