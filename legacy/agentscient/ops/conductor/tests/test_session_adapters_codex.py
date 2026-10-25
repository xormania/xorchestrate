"""Managed sessions slice 2: the codex adapter over a planted app-server.

Contract: `ops/conductor/sessions-lever.md` §The adapter boundary, extended.
The fake `fakes/session_codex.py` is planted through the kit as `codex`
and spoken to by `session_adapters/channel.py`'s `Channel`, which spawns
it with the contracted argv, `app-server --listen stdio://`, over its
pipes; the world owns the process and reaps it at close. The fake reads
its spec from `TESTKIT_FAKE_SPEC` and logs every line it receives, with
its argv, to the registry's call log, and every frame it sends to
`codex.emitted.jsonl` beside it. `framing.Parser` is the real parser. A
missing adapter or channel fails the row's own reading, not an import
error.

Planted faults: `session_adapters_codex_faults.py` beside this module,
anchored on the adapter at bf924ee7, one mutant per fixed row; the clean
neighbour is the unmodified subject, where every row passes.

The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_session_adapters_codex.py`.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

from ops.testkit import RefusalCase, Result, World, refusal_wire

REPO = Path(__file__).resolve().parents[3]
ADAPTERS = REPO / "ops" / "conductor" / "tools" / "session_adapters"
CODEX = ADAPTERS / "codex.py"
CHANNEL = ADAPTERS / "channel.py"
FRAMING = ADAPTERS / "framing.py"
FAKES = Path(__file__).resolve().parent / "fakes"
FAKE_SRC = FAKES / "session_codex.py"
ORIGIN = "sessions-lever.md#slice-2 adapters"
CHANNEL_ARGV = ["app-server", "--listen", "stdio://"]
HANDSHAKE = ["initialize", "initialized", "thread/start"]
APPROVAL = "item/commandExecution/requestApproval"


def fake_text(path: Path) -> str:
    """A program under fakes/, its shebang this interpreter."""
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


def reading_of(exc: BaseException) -> str:
    return str(getattr(exc, "reading", None) or exc)


def texts(node) -> list[str]:
    """Every string value in a JSON value."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [text for value in node.values() for text in texts(value)]
    if isinstance(node, list):
        return [text for value in node for text in texts(value)]
    return []


class TheCodexAdapter(unittest.TestCase):
    """The five protocol methods against the planted app-server.

    origin: sessions-lever.md#slice-2 adapters
    """

    def setUp(self):
        self.world = World(prefix="session-codex-", host_path=False)
        self.addCleanup(self.world.close)
        self.exe = self.world.tools.plant("codex", script=fake_text(FAKE_SRC))
        self.token = self.world.root.name.removeprefix("session-codex-")
        self.text = f"hello-{self.token}"
        self._module = None
        self._adapter = None

    def adapter(self):
        """One adapter per case, from one load of its module, so the
        channel the case builds is the one that adapter catches from."""
        if self._module is not None:
            return self._adapter
        if not CODEX.is_file():
            return None
        try:
            module = load_module(CODEX, "session_adapters_codex")
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

    def parser(self):
        self.assertTrue(FRAMING.is_file(), f"RED: framing is absent: {FRAMING}")
        return load_module(FRAMING, "session_adapters_framing_codex")

    def as_frame(self, event: dict) -> dict:
        module = self.parser()
        return module.Parser().feed(json.dumps(event, separators=(",", ":")))

    def start(self, **overrides):
        """The planted app-server behind a Channel, or None with no channel."""
        spec_path = self.world.tools.spec_path("codex")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec.update({
            "native_id": f"thread-{self.token}",
            "turn_id": f"turn-{self.token}",
            "reply": f"delta-{self.token}",
            "reject_method": None,
            "die_after": None,
        })
        spec.update(overrides)
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
        if json.loads(spec_path.read_text(encoding="utf-8")) != spec:
            raise AssertionError("INVALID: codex spec did not land intact")
        if not CHANNEL.is_file():
            return None, spec
        if self._module is None:
            self.adapter()
        try:
            module = channel_module(self._module, CHANNEL, "session_adapters_channel_codex")
            channel = module.Channel(
                [str(self.exe), *CHANNEL_ARGV], jsonrpc=False,
                env=self.world.env(extra={"TESTKIT_FAKE_SPEC": str(spec_path)}),
                cwd=str(self.world.cwd),
            )
        except Exception:
            return None, spec
        self.world.own(channel.process)
        self.addCleanup(channel.close)
        return channel, spec

    def requests(self) -> list[dict]:
        return [call for call in self.world.tools.calls("codex") if "method" in call]

    def responses(self) -> list[dict]:
        return [call for call in self.world.tools.calls("codex") if call.get("response")]

    def emitted(self) -> list[dict]:
        path = self.world.tools.log_path("codex").with_name("codex.emitted.jsonl")
        if not path.is_file():
            return []
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def call(self, fn):
        try:
            return fn()
        except Exception as exc:
            return reading_of(exc)

    def test_transport_reads_protocol(self):
        with self.subTest(id="transport-protocol", origin=ORIGIN):
            adapter = self.adapter()
            reading = ""
            if adapter is not None:
                reading = self.call(adapter.transport)
            self.assertEqual(reading, "protocol")

    def test_channel_argv_ends_with_app_server_listen_stdio(self):
        with self.subTest(id="channel-app-server", origin=ORIGIN):
            adapter = self.adapter()
            binding_path = self.world.root / "binding.json"
            self.world.plant_text(binding_path, "{}\n", recognisable="{")
            binding = {
                "executable": str(self.exe),
                "binding": str(binding_path),
                "harness": "codex",
            }
            argv: list = []
            if adapter is not None:
                argv = self.call(
                    lambda: list(adapter.channel({"name": "co", "harness": "codex"}, binding))
                )
                if not isinstance(argv, list):
                    argv = []
            parts = [str(part) for part in argv]
            tail = [Path(parts[-3]).name, parts[-2], parts[-1]] if len(parts) >= 3 else parts
            self.assertEqual(tail, CHANNEL_ARGV)

    def test_handshake_returns_the_spec_thread_id(self):
        """origin: 20260923T020912Z-review-codex-9d8b8c #10, #16

        The thread id comes back, and the fake's log shows `initialize`,
        `initialized`, `thread/start` in that order, each received by a
        process launched with the contracted argv.
        """
        with self.subTest(id="handshake-thread-id", origin=ORIGIN):
            rpc, spec = self.start()
            adapter = self.adapter()
            reading = None
            if adapter is not None and rpc is not None:
                reading = self.call(lambda: adapter.handshake(rpc))
            requests = self.requests()
            self.assertEqual(reading, spec["native_id"])
            self.assertEqual([call["method"] for call in requests], HANDSHAKE)
            self.assertEqual({tuple(call["argv"]) for call in requests}, {tuple(CHANNEL_ARGV)})

    def test_handshake_dying_after_initialize_refuses_channel(self):
        row = RefusalCase(
            id="handshake-dies-after-initialize",
            argv=("app-server", "--listen", "stdio://"),
            cls="channel",
            named=("thread/start",),
            origin=ORIGIN,
        )
        with self.subTest(id=row.id, origin=row.origin):
            refusal_wire(row, self.handshake_refusal())

    def handshake_refusal(self) -> Result:
        # The fake exits once `initialized` has arrived, after answering
        # `initialize`: the first request left unanswered is `thread/start`.
        rpc, _spec = self.start(die_after="initialized")
        adapter = self.adapter()
        cwd = str(self.world.cwd)
        argv = tuple(CHANNEL_ARGV)
        if adapter is None or rpc is None:
            return Result(argv, 0, "", "", 0.0, cwd)
        try:
            value = adapter.handshake(rpc)
        except BaseException as exc:
            line = exc.line() if callable(getattr(exc, "line", None)) else reading_of(exc)
            text = line.strip()
            if text.startswith("channel:"):
                return Result(argv, 2, "", text + "\n", 0.0, cwd)
            return Result(argv, 0, "", text + "\n", 0.0, cwd)
        text = str(value).strip()
        if text.startswith("channel:"):
            return Result(argv, 2, "", text + "\n", 0.0, cwd)
        return Result(argv, 0, text, "", 0.0, cwd)

    def test_submit_idle_reads_accepted_turn(self):
        """origin: 20260923T020912Z-review-codex-9d8b8c #11

        One `turn/start`, carrying the submitted text and no `toolOutput`.
        """
        with self.subTest(id="submit-idle-turn", origin=ORIGIN):
            rpc, spec = self.start()
            adapter = self.adapter()
            reading = None
            if adapter is not None and rpc is not None:
                self.call(lambda: adapter.handshake(rpc))
                reading = self.call(lambda: adapter.submit(rpc, self.text, busy=False))
            self.assertEqual(reading, f"accepted turn {spec['turn_id']}")
            turns = [call for call in self.requests() if call["method"] == "turn/start"]
            self.assertEqual(len(turns), 1, turns)
            self.assertIn(self.text, texts(turns[0]["params"]), turns[0])
            self.assertNotIn("toolOutput", json.dumps(turns[0]["params"]))

    def test_submit_busy_sends_tool_output_into_the_active_turn(self):
        """origin: 20260923T020912Z-review-codex-9d8b8c #12

        The busy `turn/start` carries `toolOutput` and the text submitted,
        never a constant or an empty output, into the thread whose turn is
        active. The contract names no turn-id field for it, so none is
        pinned.
        """
        with self.subTest(id="submit-busy-tool-output", origin=ORIGIN):
            rpc, spec = self.start()
            adapter = self.adapter()
            busy = f"tool says {self.token}"
            if adapter is not None and rpc is not None:
                self.call(lambda: adapter.handshake(rpc))
                self.call(lambda: adapter.submit(rpc, self.text, busy=False))
                self.call(lambda: adapter.submit(rpc, busy, busy=True))
            lines = [
                call for call in self.requests()
                if call["method"] == "turn/start" and "toolOutput" in json.dumps(call["params"])
            ]
            self.assertEqual(len(lines), 1, lines)
            line = json.dumps(lines[0]["params"])
            self.assertIn(busy, texts(lines[0]["params"]), line)
            self.assertEqual(lines[0]["params"].get("threadId"), spec["native_id"], line)

    def test_submit_method_not_found_reads_unsupported_turn_start(self):
        with self.subTest(id="submit-unsupported-turn-start", origin=ORIGIN):
            rpc, _spec = self.start(reject_method="turn/start")
            adapter = self.adapter()
            reading = None
            if adapter is not None and rpc is not None:
                self.call(lambda: adapter.handshake(rpc))
                reading = self.call(lambda: adapter.submit(rpc, self.text, busy=False))
            self.assertEqual(reading, "unsupported turn/start")

    def test_observe_classifies_each_emitted_frame(self):
        rpc, spec = self.start()
        adapter = self.adapter()
        if adapter is not None and rpc is not None:
            self.call(lambda: adapter.handshake(rpc))
            self.call(lambda: adapter.submit(rpc, self.text, busy=False))
        by_method = {frame["method"]: frame for frame in self.emitted()}
        rows = (
            ("observe-native", "thread/started", f"native {spec['native_id']}"),
            ("observe-activity", "turn/started", "activity active"),
            ("observe-output", "item/agentMessage/delta", f"output {spec['reply']}"),
            ("observe-done", "turn/completed", f"done {spec['turn_id']}"),
        )
        for row_id, method, expect in rows:
            with self.subTest(id=row_id, origin=ORIGIN):
                self.assertIn(method, by_method, sorted(by_method))
                frame = self.as_frame(by_method[method])
                reading = ""
                if adapter is not None:
                    reading = str(self.call(lambda frame=frame: adapter.observe(rpc, frame)))
                self.assertEqual(reading, expect)

    def test_observe_other_thread_id_reads_refused_native(self):
        """A frame naming another thread reads `refused native <id>`
        (the XO's ruling of 2026-09-23 on the contract's words)."""
        with self.subTest(id="observe-refused-native", origin=ORIGIN):
            rpc, spec = self.start()
            adapter = self.adapter()
            other = f"other-{spec['native_id']}"
            frame = self.as_frame({
                "method": "item/agentMessage/delta",
                "params": {"threadId": other, "delta": "nope"},
            })
            reading = ""
            if adapter is not None and rpc is not None:
                self.call(lambda: adapter.handshake(rpc))
                reading = str(self.call(lambda: adapter.observe(rpc, frame)))
            self.assertEqual(reading, f"refused native {other}")

    def test_observe_permission_is_declined(self):
        """origin: 20260923T020912Z-review-codex-9d8b8c #15

        An approval request reads `permission <title>`, its prompt is
        printed, and the app-server receives `decline` as the answer to
        that request's id.
        """
        with self.subTest(id="observe-permission-decline", origin=ORIGIN):
            rpc, spec = self.start()
            adapter = self.adapter()
            title = f"permit-{spec['native_id']}"
            frame = self.as_frame({
                "id": "perm-1",
                "method": APPROVAL,
                "params": {
                    "threadId": spec["native_id"], "turnId": spec["turn_id"],
                    "itemId": "item-1", "reason": title,
                },
            })
            reading = ""
            printed = io.StringIO()
            if adapter is not None and rpc is not None:
                self.call(lambda: adapter.handshake(rpc))
                with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
                    reading = str(self.call(lambda: adapter.observe(rpc, frame)))
                rpc.close()
            self.assertEqual(reading, f"permission {title}")
            self.assertIn(title, printed.getvalue(), "the permission prompt was not printed")
            answers = [call for call in self.responses() if call.get("id") == "perm-1"]
            self.assertEqual(len(answers), 1, self.responses())
            self.assertIn("decline", json.dumps(answers[0].get("result")), answers[0])


if __name__ == "__main__":
    unittest.main()
