"""MCP rows for sessions slice 3, fixture 11.

Contract: ops/conductor/sessions-lever.md "Slice 3 — Dispatcher integration"
and ops/conductor/coordination-mcp-lever.md for the tool shape, the error
result and the binding. The subject ops/conductor/tools/dispatch_mcp.py is
not read here. When it is absent, World.stage plants a no-op (exit 0, no
output) so each row fails on its own assertion.

Origin of every row: sessions-lever.md#slice-3 11.

No row checks that the file is present: a presence check asserts
nothing the subject does (docs/tests.md rule 9,
20260923T020916Z-review-codex-5a41cd#4), and every row below runs the staged subject.

Planted faults (docs/tests.md rule 3, 20260923T020916Z-review-codex-5a41cd#5):
dispatch_mcp_faults.py beside this module, anchored on agentscient-10's
subjects at bfa82f5b; its docstring names the calibrate command.
Calibrated over the whole module at 02436b3c. Its three faults reach
mcp-tools-list, mcp-role and cli-role; a fault for each other row is a
declared gap.

    python3 -m unittest discover -s ops/conductor/tests -p test_dispatch_mcp.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import RefusalCase, World, refusal_wire, run, run_table

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "dispatch_mcp.py"
ACTIONS = TOOLS / "dispatch_actions.py"
CORE = TOOLS / "coordination.py"
DISP = TOOLS / "dispatcher.py"
LOCK = TOOLS / "deliverable.py"
CONF = REPO / "ops" / "lane.conf"
FAKES = Path(__file__).resolve().parent / "fakes"

ORIGIN = "sessions-lever.md#slice-3 11"
AGENT = "Slice3 Tester <noreply@example.test>"
LEAD = "lead-s3"
WORK = "work-s3"
HOLD = "disp-s3"
BRANCH = "lane/session-20260923-s3"
RECORD = "20260923T000000Z-code-grok-aabbcc"
TIMEOUT = 30.0
TOOLS_FOUR = {"coord_launch", "coord_status", "coord_wait", "coord_collect"}
MUTATIONS = {"coord_launch", "coord_collect"}
READS = {"coord_status", "coord_wait"}
WAIT_READING = "residue remains after the job"


def _fake_text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"INVALID: planted program missing: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    return f"#!{sys.executable}\n" + text


class DispatchMcp(unittest.TestCase):
    """The four tools over the standalone entry, and the CLI wire beside them."""

    def setUp(self):
        self.world = World(prefix="dispatch-mcp-")
        self.addCleanup(self.world.close)
        self._server = None
        self._actions = None
        self.jobs = self.world.root / "dispatch-jobs"
        self.jobs.mkdir()
        self.input_path = self.world.plant_text(
            self.world.cwd / "assignment-input.txt", "the assignment's full text\n")
        self.wrapper = self.world.tools.plant(
            "dispatch-wrapper", script=_fake_text(FAKES / "dispatch_wrapper.py"))
        self.launcher = self.world.tools.plant(
            "dispatch-launch", script=_fake_text(FAKES / "dispatch_launch.py"))
        self.monitor = self.world.tools.plant("dispatch-monitor")
        # The specs in the fakes' own keys: fakes/dispatch_wrapper.py and
        # fakes/dispatch_launch.py read these and nothing else.
        self._extend_spec("dispatch-wrapper", {"record": RECORD})
        self._extend_spec("dispatch-launch", {
            "status": "running",
            "wait": "not-clean",
            "wait_reading": WAIT_READING,
            "collect_reading": "closed",
        })
        self._assignment()

    def _extend_spec(self, name: str, extra: dict) -> None:
        path = self.world.tools.spec_path(name)
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec.update(extra)
        text = json.dumps(spec, indent=1) + "\n"
        path.write_text(text, encoding="utf-8")
        if json.loads(path.read_text(encoding="utf-8")) != json.loads(text):
            raise AssertionError(f"INVALID: spec for {name} did not land intact")

    def env(self, session: str, *, identity: bool = False) -> dict[str, str]:
        extra = {
            "LANE_CONF": str(CONF),
            "CLAUDE_CODE_SESSION_ID": session,
            "COORDINATION_CAPABILITY": hashlib.sha256(session.encode()).hexdigest(),
            "DISPATCH_WRAPPER": str(self.wrapper),
            "DISPATCH_LAUNCHER": str(self.launcher),
            "DISPATCH_MONITOR": str(self.monitor),
            "DISPATCH_JOBS": str(self.jobs),
        }
        if identity:
            extra["WF_AGENT"] = AGENT
        return self.world.env(extra=extra)

    def tool(self, program, *argv, session: str, identity: bool = True):
        return run([sys.executable, "-B", str(program), *argv], cwd=self.world.cwd,
                   env=self.env(session, identity=identity), timeout=TIMEOUT)

    def core(self, *argv, session: str):
        result = self.tool(CORE, *argv, session=session)
        if result.exit != 0:
            raise AssertionError(
                f"INVALID: coordination {' '.join(argv)}: {result.stderr}")
        return result

    def server_path(self) -> Path:
        if TOOL.is_file():
            return TOOL
        if self._server is None:
            self._server = self.world.stage(TOOL)
        return self._server

    def actions_path(self) -> Path:
        if ACTIONS.is_file():
            return ACTIONS
        if self._actions is None:
            self._actions = self.world.stage(ACTIONS)
        return self._actions

    def actions(self, *argv, session: str, identity: bool = True):
        return self.tool(self.actions_path(), *argv, session=session, identity=identity)

    def _assignment(self) -> None:
        self.core("bind", "--roles", "lead,coordinator", session=LEAD)
        worker = self.core("bind", "--roles", "worker", session=WORK)
        holder = self.core("bind", "--roles", "worker", session=HOLD)
        self.worker = self._id(worker.stdout, "PT")
        self.dispatcher = self._id(holder.stdout, "PT")
        self.core("grant", self.dispatcher, "--roles", "dispatcher", session=LEAD)
        locked = self.tool(LOCK, "add", BRANCH, "--why", "slice 3 mcp fixture", session=LEAD)
        if locked.exit != 0:
            raise AssertionError(f"INVALID: deliverable add: {locked.stderr}")
        drafted = self.core(
            "draft", "slice 3 mcp", "--outcome", "a launched intent",
            "--revision", "bc7ec7cf", session=LEAD)
        self.assignment = drafted.stdout.split()[-1].strip()
        if not re.fullmatch(r"A-\d+", self.assignment):
            raise AssertionError(f"INVALID: draft did not name an assignment: {drafted.stdout!r}")
        self.scope_ref = (
            f"{self.assignment} verify ops/conductor/sessions-lever.md bc7ec7cf")
        self.core("offer", self.assignment, "--to", self.worker, "--generation", "1",
                  session=LEAD)
        shown = self.core("show", self.assignment, session=LEAD)
        generation = re.search(r"generation (\d+)", shown.stdout)
        if not generation:
            raise AssertionError(f"INVALID: show printed no generation: {shown.stdout!r}")
        self.core("claim", self.assignment, "--branch", BRANCH,
                  "--generation", generation.group(1), session=WORK)

    @staticmethod
    def _id(text: str, kind: str) -> str:
        found = re.search(rf"{kind}-\d+", text)
        if not found:
            raise AssertionError(f"INVALID: no {kind}-n in {text!r}")
        return found.group(0)

    def hold_machine(self) -> None:
        result = self.tool(DISP, "set", HOLD, "--why", "slice 3 mcp row", session=HOLD)
        if result.exit != 0:
            raise AssertionError(f"INVALID: dispatcher.py set failed: {result.stderr}")

    def launch_argv(self, key: str) -> tuple[str, ...]:
        return (
            "launch", "--for", self.assignment, "--job", "verify",
            "--harness", "grok", "--branch", BRANCH, "--scope-ref", self.scope_ref,
            "--input", str(self.input_path), "--key", key,
        )

    def launch_fields(self, key: str) -> dict[str, str]:
        """The CLI flags as the tool's argument names; `--for A-n` is
        `assignment` (the XO's ruling, 2026-09-23)."""
        return {
            "assignment": self.assignment,
            "job": "verify",
            "harness": "grok",
            "branch": BRANCH,
            "scope_ref": self.scope_ref,
            "input": str(self.input_path),
            "key": key,
        }

    def mcp(self, method: str, params: dict | None = None, *, session: str) -> list[dict]:
        request = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            request["params"] = params
        result = run(
            [sys.executable, "-B", str(self.server_path()), "serve"],
            cwd=self.world.cwd, env=self.env(session), stdin=json.dumps(request) + "\n",
            timeout=TIMEOUT)
        answers = []
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            try:
                answers.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise AssertionError(
                    f"the server's stdout was not JSON-RPC: {line!r}") from exc
        return answers

    def call(self, name: str, arguments: dict, *, session: str) -> list[dict]:
        return self.mcp("tools/call", {"name": name, "arguments": arguments}, session=session)

    @staticmethod
    def one(answers: list[dict], row: str) -> dict:
        if not answers:
            raise AssertionError(
                f"{row}: the server answered nothing; one JSON-RPC response was expected")
        for item in answers:
            if item.get("id") == 1:
                return item
        raise AssertionError(f"{row}: no answer carried id 1: {answers!r}")

    @staticmethod
    def shown(message: dict, row: str) -> tuple[dict, str]:
        result = message.get("result")
        if not isinstance(result, dict):
            raise AssertionError(
                f"{row}: the server answered nothing; one JSON-RPC response was expected: "
                f"{message!r}")
        parts = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        structured = result.get("structuredContent")
        if structured is not None:
            parts.append(json.dumps(structured))
        return result, "\n".join(parts)

    def assert_error(self, message: dict, cls: str, row: str, named: tuple[str, ...] = ()) -> None:
        result, text = self.shown(message, row)
        if result.get("isError") is not True:
            raise AssertionError(
                f"{row}: class {cls!r} expected on an error result, found {result!r}")
        if not text.startswith(cls + ":"):
            raise AssertionError(f"{row}: class {cls!r} expected, found {text!r}")
        for word in ("expected", "found", "needed", *named):
            if word not in text:
                raise AssertionError(f"{row}: the line must name {word!r}: {text!r}")

    def quiet_intent(self, key: str) -> str:
        """The DI-n a launch returned, or DI-1 when the subject answered nothing.

        The caller asserts its own row; a missing launch must not raise here.
        """
        self.hold_machine()
        answers = self.call("coord_launch", self.launch_fields(key), session=HOLD)
        text = ""
        if answers and isinstance(answers[0].get("result"), dict):
            _result, text = self.shown(answers[0], key)
        found = re.search(r"DI-\d+", text)
        return found.group(0) if found else "DI-1"

    def test_the_standalone_list_names_the_four_and_marks_mutations(self):
        """origin: sessions-lever.md#slice-3 11

        tools/list on dispatch_mcp.py serve, which runs without the
        CATALOGUE registration line. coord_launch and coord_collect mutate.
        """
        row = "mcp-tools-list"
        message = self.one(self.mcp("tools/list", session=HOLD), row)
        result = message.get("result")
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise AssertionError(f"{row}: tools/list did not name the four tools: {message!r}")
        listed = {item.get("name"): item for item in tools}
        self.assertEqual(set(listed), TOOLS_FOUR, row)
        for name in MUTATIONS:
            self.assertIs(listed[name]["annotations"].get("readOnlyHint"), False, name)
        for name in READS:
            self.assertIs(listed[name]["annotations"].get("readOnlyHint"), True, name)
            self.assertIs(listed[name]["annotations"].get("destructiveHint"), False, name)
        for name in TOOLS_FOUR:
            hint = listed[name]["annotations"].get("idempotentHint")
            self.assertIsInstance(hint, bool, name)

    def test_coord_launch_returns_the_intent_the_cli_reads(self):
        """origin: sessions-lever.md#slice-3 11

        A bound dispatcher holding the machine: coord_launch returns DI-n
        and the record id, and the CLI status of that DI-n reads the same row.
        """
        row = "mcp-launch-same-row"
        self.hold_machine()
        message = self.one(
            self.call("coord_launch", self.launch_fields("mcp-launch"), session=HOLD), row)
        result, text = self.shown(message, row)
        self.assertIsNot(result.get("isError"), True, f"{row}: {text!r}")
        found = re.search(r"DI-\d+", text)
        self.assertIsNotNone(found, f"{row}: coord_launch did not return a DI-n: {text!r}")
        intent = found.group(0)
        self.assertIn(RECORD, text, row)
        status = self.actions("status", intent, session=WORK, identity=False)
        self.assertIn(intent, status.stdout, status.stderr)
        self.assertIn(RECORD, status.stdout, status.stderr)
        self.assertIn("launched", status.stdout, status.stderr)

    def test_the_same_key_returns_the_same_intent(self):
        """origin: sessions-lever.md#slice-3 11

        The same key twice returns the same DI-n and the wrapper runs once.
        """
        row = "mcp-same-key"
        self.hold_machine()
        fields = self.launch_fields("same-key")
        first, text = self.shown(self.one(self.call("coord_launch", fields, session=HOLD), row), row)
        self.assertIsNot(first.get("isError"), True, f"{row}: {text!r}")
        found = re.search(r"DI-\d+", text)
        self.assertIsNotNone(found, f"{row}: coord_launch did not return a DI-n: {text!r}")
        intent = found.group(0)
        _second, again = self.shown(
            self.one(self.call("coord_launch", fields, session=HOLD), row), row)
        self.assertIn(intent, again, row)
        self.assertEqual(len(self.world.tools.calls("dispatch-wrapper")), 1, again)

    def test_a_worker_launch_returns_role(self):
        """origin: sessions-lever.md#slice-3 11"""
        row = "mcp-role"
        message = self.one(
            self.call("coord_launch", self.launch_fields("role-key"), session=WORK), row)
        self.assert_error(message, "role", row)

    def test_a_dispatcher_who_does_not_hold_returns_holder(self):
        """origin: sessions-lever.md#slice-3 11"""
        row = "mcp-holder"
        message = self.one(
            self.call("coord_launch", self.launch_fields("holder-key"), session=HOLD), row)
        self.assert_error(message, "holder", row, ("dispatcher.py set",))

    def test_coord_status_needs_no_identity(self):
        """origin: sessions-lever.md#slice-3 11

        A worker, with no WF_AGENT, reads the launched intent.
        """
        row = "mcp-status-no-identity"
        intent = self.quiet_intent("status-key")
        message = self.one(
            self.call("coord_status", {"intent": intent}, session=WORK), row)
        result, text = self.shown(message, row)
        self.assertIsNot(result.get("isError"), True, f"{row}: {text!r}")
        self.assertIn(RECORD, text, row)

    def test_coord_wait_returns_the_not_clean_reading(self):
        """origin: sessions-lever.md#slice-3 11"""
        row = "mcp-wait-not-clean"
        intent = self.quiet_intent("wait-key")
        message = self.one(
            self.call("coord_wait", {"intent": intent, "timeout": 5}, session=HOLD), row)
        result, text = self.shown(message, row)
        self.assertIsNot(result.get("isError"), True, f"{row}: {text!r}")
        self.assertIn(WAIT_READING, text, row)

    def test_coord_collect_unknown_name_returns_verify(self):
        """origin: sessions-lever.md#slice-3 11

        An unknown verification name returns verify and launch.py is not called.
        """
        row = "mcp-collect-verify"
        intent = self.quiet_intent("collect-key")
        message = self.one(
            self.call("coord_collect", {"intent": intent, "verify": "rm -rf"}, session=HOLD),
            row)
        self.assert_error(message, "verify", row)
        self.assertEqual(self.world.tools.calls("dispatch-launch"), [], row)

    def test_the_cli_refuses_role_and_holder_on_the_wire(self):
        """origin: sessions-lever.md#slice-3 11"""
        # A declared gap: no planted faults yet (the module docstring).
        rows = (
            RefusalCase(id="cli-role", argv=self.launch_argv("cli-role"), cls="role",
                        origin=ORIGIN),
            RefusalCase(id="cli-holder", argv=self.launch_argv("cli-holder"), cls="holder",
                        origin=ORIGIN, named=("dispatcher.py set",)),
        )

        def invoke(row: RefusalCase):
            session = WORK if row.id == "cli-role" else HOLD
            return self.actions(*row.argv, session=session)

        run_table(self, rows, invoke)

    def test_the_cli_refuses_an_unknown_verify_name(self):
        """origin: sessions-lever.md#slice-3 11"""
        self.hold_machine()
        launched = self.actions(*self.launch_argv("cli-verify-launch"), session=HOLD)
        found = re.search(r"DI-\d+", launched.stdout)
        intent = found.group(0) if found else "DI-1"
        result = self.actions("collect", intent, "--verify", "rm -rf", session=HOLD)
        refusal_wire(
            RefusalCase(id="cli-verify", argv=("collect", intent, "--verify", "rm -rf"),
                        cls="verify", origin=ORIGIN),
            result)


if __name__ == "__main__":
    unittest.main()
