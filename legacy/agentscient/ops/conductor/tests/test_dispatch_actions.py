"""Dispatcher integration, slice 3: launch, status, wait, collect, recover, reconcile.

Contract: ``ops/conductor/sessions-lever.md`` section "Slice 3 — Dispatcher
integration". The subject ``ops/conductor/tools/dispatch_actions.py`` is run
as a subprocess through ``ops.testkit.run`` with a timeout. A missing
subject is staged as an empty success (``World.stage``), so each row goes
red on its own assertion rather than on a missing-file exit.

The one command that reads this file is
``python3 -m unittest discover -s ops/conductor/tests -p test_dispatch_actions.py``.

Planted faults: ``dispatch_actions_faults.py`` beside this module, anchored
on agentscient-10's subject at bfa82f5b; its docstring names the calibrate
command. Calibrated over the whole module at 02436b3c. Its three faults
reach worker-role, not-holder and store-unwritable; a fault for each
other row is a declared gap. Fakes live in ``fakes/`` and are planted
through the registry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import unittest
import uuid
from pathlib import Path

from ops.testkit import RefusalCase, World, refusal_wire, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "dispatch_actions.py"
COORD = TOOLS / "coordination.py"
DISPATCHER = TOOLS / "dispatcher.py"
LOCK = TOOLS / "deliverable.py"
FAKES = Path(__file__).resolve().parent / "fakes"
JOBS = REPO / "ops" / "devlane" / "task" / "jobs.json"

ORIGIN = "sessions-lever.md#slice-3"
LANE = "agentscient"
AGENT = "Dispatch Actions <noreply@example.test>"
TIMEOUT = 60.0
JOB = "verify"
HARNESS = "grok"
BRANCH = "lane/dispatch-actions-fixture"
SCOPE_REF = "ops/conductor/sessions-lever.md@bc7ec7cf"
DISPATCHER_SESSION = "sess-dispatcher"
WORKER_SESSION = "sess-worker"
PEER_SESSION = "sess-peer"
STALE_BASE = (
    "stale-base: expected a clean worktree to merge origin/dev forward; "
    "found uncommitted changes in the worktree; needed commit or stash them"
)
CONF = (
    f"LANE_NAME='{LANE}'\n"
    "WORKER_HARNESSES='claude codex grok agy'\n"
    "MODEL_GROK='grok-4.7'\n"
    "MODEL_CODEX='gpt-5.6-sol'\n"
    "MODEL_CLAUDE_READ='claude-opus-5'\n"
    "MODEL_CLAUDE_PLAN='claude-fable-5-1'\n"
    "SCOPE_CAP='1024'\n"
)
# The tracked table, read from the repository root ("Verification names").
# A row reads it and never writes it: a fixture that wrote the tree
# errored every row in setUp under a read-only checkout (20260923T020916Z-review-codex-5a41cd#1).
VERIFICATIONS = REPO / "ops" / "conductor" / "verifications.json"


def flag(argv, name: str):
    prefix = name + "="
    for index, item in enumerate(argv):
        if item == name and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(prefix):
            return item[len(prefix):]
    return None


def _fake_text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"INVALID: planted program missing: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    return f"#!{sys.executable}\n" + text


def _ident(text: str, kind: str) -> str:
    match = re.search(rf"{kind}-\d+", text)
    if match is None:
        raise AssertionError(f"INVALID: no {kind}-n in {text!r}")
    return match.group(0)


class DispatchActions(unittest.TestCase):
    """Rows of slice 3, addressed by id, each with its origin."""

    def setUp(self):
        self.world = World(prefix="dispatch-actions-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("lane.conf", CONF)
        self.state = self.world.xdg_state / LANE
        self.store = self.state / "state.db"
        self.jobs = self.world.root / "jobs"
        self.jobs.mkdir()
        self.wrapper = self.world.tools.plant(
            "dispatch-wrapper", script=_fake_text(FAKES / "dispatch_wrapper.py"),
        )
        self.launcher = self.world.tools.plant(
            "dispatch-launch", script=_fake_text(FAKES / "dispatch_launch.py"),
        )
        self.monitor = self.world.tools.plant("dispatch-monitor")
        self.input_path = self.world.plant_text("assignment-input.txt", "the assignment text\n")
        self.scope_cap = self._scope_cap()
        self.subject = TOOL if TOOL.is_file() else self.world.stage(TOOL)
        self.dispatcher = DISPATCHER_SESSION
        self.worker = WORKER_SESSION
        self.dispatcher_pt = _ident(
            self.ok(COORD, "bind", "--roles", "lead,dispatcher", session=self.dispatcher),
            "PT",
        )
        self.worker_pt = _ident(
            self.ok(COORD, "bind", "--roles", "worker", session=self.worker),
            "PT",
        )
        self.ok(DISPATCHER, "set", self.dispatcher, "--why", "the seat", session=self.dispatcher)
        shown = self.ok(DISPATCHER, "show", session=self.dispatcher)
        self.assertIn(self.dispatcher, shown, shown)
        self.ok(LOCK, "add", BRANCH, "--why", "the fixture", session=self.dispatcher)
        drafted = self.ok(
            COORD, "draft", "a dispatch", "--outcome", "one record",
            session=self.dispatcher,
        )
        self.assignment = _ident(drafted, "A")
        self.ok(
            COORD, "offer", self.assignment, "--to", self.worker_pt,
            "--generation", self.generation(), session=self.dispatcher,
        )
        self.ok(
            COORD, "claim", self.assignment, "--branch", BRANCH,
            "--generation", self.generation(), session=self.worker,
        )

    def verification(self) -> tuple[str, list[str]]:
        """The first name of the tracked table and its argv, read, never
        written. origin: 20260923T020916Z-review-codex-5a41cd#1"""
        self.assertTrue(VERIFICATIONS.is_file(), f"the tracked table is absent: {VERIFICATIONS}")
        table = json.loads(VERIFICATIONS.read_text(encoding="utf-8"))
        self.assertIsInstance(table, dict, f"{VERIFICATIONS} is {{name: [argv...]}}")
        self.assertTrue(table, f"the tracked table names no verification: {VERIFICATIONS}")
        name = min(table)
        return name, [str(part) for part in table[name]]

    def _scope_cap(self) -> int:
        match = re.search(r"^SCOPE_CAP='(\d+)'\s*$", self.conf.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertIsNotNone(match, "the planted conf declares SCOPE_CAP")
        return int(match.group(1))

    def env(self, session: str | None, extra: dict | None = None, *,
            identity: bool = True) -> dict:
        fields = {
            "LANE_CONF": str(self.conf),
            "DISPATCH_WRAPPER": str(self.wrapper),
            "DISPATCH_LAUNCHER": str(self.launcher),
            "DISPATCH_MONITOR": str(self.monitor),
            "DISPATCH_JOBS": str(self.jobs),
        }
        if identity:
            fields["WF_AGENT"] = AGENT
        if session:
            fields["CLAUDE_CODE_SESSION_ID"] = session
            fields["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        if extra:
            fields.update(extra)
        return self.world.env(extra=fields)

    def tool(self, path, *argv, session=None, extra=None):
        return run(
            [sys.executable, str(path), *argv],
            cwd=self.world.cwd,
            env=self.env(session, extra),
            timeout=TIMEOUT,
        )

    def ok(self, path, *argv, session=None) -> str:
        proc = self.tool(path, *argv, session=session)
        self.assertEqual(proc.exit, 0, f"{Path(path).name} {argv}: {proc.stderr or proc.stdout}")
        return proc.stdout

    def act(self, *argv, session=None, extra=None, identity=True):
        return run(
            [sys.executable, str(self.subject), *argv],
            cwd=REPO,
            env=self.env(session or self.dispatcher, extra, identity=identity),
            timeout=TIMEOUT,
        )

    def generation(self) -> str:
        shown = self.ok(COORD, "show", self.assignment, session=self.dispatcher)
        found = re.search(r"generation (\d+)", shown)
        self.assertIsNotNone(found, f"show printed no generation: {shown!r}")
        return found.group(1)

    def set_spec(self, name: str, **fields) -> None:
        path = self.world.tools.spec_path(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(fields)
        text = json.dumps(data)
        path.write_text(text, encoding="utf-8")
        if path.read_text(encoding="utf-8") != text:
            raise AssertionError(f"INVALID: spec for {name} did not land")

    def wrapper_calls(self) -> list:
        return self.world.tools.calls("dispatch-wrapper")

    def launcher_calls(self) -> list:
        return self.world.tools.calls("dispatch-launch")

    def intent_count(self) -> int:
        if not self.store.is_file():
            return 0
        conn = sqlite3.connect(f"file:{self.store}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dispatch_intents'"
            ).fetchone()
            if row is None:
                return 0
            return int(conn.execute("SELECT COUNT(*) FROM dispatch_intents").fetchone()[0])
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def launch(self, *, key, job=JOB, harness=HARNESS, branch=BRANCH,
               scope_ref=SCOPE_REF, input_path=None, session=None, extra=None):
        return self.act(
            "launch",
            "--for", self.assignment,
            "--job", job,
            "--harness", harness,
            "--branch", branch,
            "--scope-ref", scope_ref,
            "--input", str(input_path or self.input_path),
            "--key", key,
            session=session,
            extra=extra,
        )

    def launched(self, proc, row: str):
        self.assertEqual(proc.exit, 0, f"{row}: {proc.stderr}")
        match = re.search(r"intent (DI-\d+) launched (\S+)", proc.stdout)
        self.assertIsNotNone(match, f"{row}: {proc.stdout!r}")
        return match.group(1), match.group(2)

    def refuses(self, proc, row: str, cls: str, origin: str, named=()):
        refusal_wire(
            RefusalCase(
                id=row,
                argv=tuple(proc.argv),
                cls=cls,
                named=tuple(named),
                origin=origin,
            ),
            proc,
        )

    def test_launch_records_one_intent(self):
        """origin: sessions-lever.md#slice-3 1"""
        origin = f"{ORIGIN} 1"
        with self.subTest(id="launch-one", origin=origin):
            proc = self.launch(key="once")
            intent, record = self.launched(proc, "launch-one")
            calls = self.wrapper_calls()
            self.assertEqual(len(calls), 1, calls)
            argv = calls[0]["argv"]
            self.assertIn("--detach", argv)
            self.assertEqual(flag(argv, "--for"), self.assignment)
            self.assertEqual(flag(argv, "--job"), JOB)
            self.assertEqual(flag(argv, "--harness"), HARNESS)
            self.assertEqual(flag(argv, "--branch"), BRANCH)
            self.assertEqual(flag(argv, "--input"), str(self.input_path))
            self.assertEqual(flag(argv, "--agent"), AGENT)
            catalogue = json.loads(JOBS.read_text(encoding="utf-8"))
            self.assertEqual(flag(argv, "--timeout"), str(catalogue[JOB]["caps"]["timeout"]))
            correlation = flag(argv, "--correlation")
            self.assertIsNotNone(correlation, argv)
            try:
                parsed = uuid.UUID(correlation)
            except ValueError:
                self.fail(f"launch-one: --correlation is not a uuid: {correlation}")
            self.assertEqual(parsed.version, 4, correlation)
            self.assertEqual(str(parsed), correlation)
            scope = flag(argv, "--scope-file")
            self.assertIsNotNone(scope, argv)
            scope_path = Path(scope)
            text = scope_path.read_text(encoding="utf-8")
            lines = [line for line in text.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1, text)
            for part in (self.assignment, JOB, SCOPE_REF, str(self.input_path)):
                self.assertIn(part, lines[0])
            self.assertEqual(scope_path.name, "scope.txt")
            self.assertEqual(scope_path.parent.name, intent)
            self.assertIn("dispatch-intents", scope_path.parts)
            self.assertTrue(str(scope_path.resolve()).startswith(str(self.state.resolve())), scope)
            state = json.loads((self.jobs / record / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state.get("correlation"), correlation)
            shown = self.act("status", intent)
            self.assertEqual(shown.exit, 0, shown.stderr)
            self.assertIn(
                f"intent {intent} launched record {record} job running closed none",
                shown.stdout,
            )
            self.assertEqual(self.intent_count(), 1)

    def test_the_same_key_is_repeated(self):
        """origin: sessions-lever.md#slice-3 2"""
        origin = f"{ORIGIN} 2"
        with self.subTest(id="same-key-repeated", origin=origin):
            self.launch(key="same")
            second = self.launch(key="same")
            self.assertIn("repeated", second.stdout, second.stdout)
            self.assertEqual(second.exit, 0, second.stderr)
            self.assertEqual(len(self.wrapper_calls()), 1, self.wrapper_calls())
            self.assertEqual(self.intent_count(), 1)

    def test_another_digest_refuses_idempotency(self):
        """origin: sessions-lever.md#slice-3 2"""
        origin = f"{ORIGIN} 2"
        with self.subTest(id="same-key-other-digest", origin=origin):
            first = self.launch(key="same")
            match = re.search(r"DI-\d+", first.stdout)
            intent = match.group(0) if match else "DI-1"
            second = self.launch(key="same", scope_ref=SCOPE_REF + "#other")
            self.refuses(second, "same-key-other-digest", "idempotency", origin, named=(intent,))
            self.assertEqual(self.intent_count(), 1)

    def test_reconcile_with_the_correlation_launches(self):
        """origin: sessions-lever.md#slice-3 3"""
        origin = f"{ORIGIN} 3"
        with self.subTest(id="reconcile-found", origin=origin):
            self.set_spec("dispatch-wrapper", mode="no-record")
            self.launch(key="found")
            calls = self.wrapper_calls()
            correlation = flag(calls[0]["argv"], "--correlation") if calls else None
            if correlation:
                directory = self.jobs / "only"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "state.json").write_text(
                    json.dumps({"correlation": correlation}) + "\n",
                    encoding="utf-8",
                )
            self.act("reconcile", "DI-1")
            shown = self.act("status", "DI-1")
            self.assertRegex(shown.stdout, r"intent DI-1 launched record only\b")
            self.assertEqual(shown.exit, 0, shown.stderr)

    def test_a_relaunch_does_not_call_the_wrapper_again(self):
        """origin: sessions-lever.md#slice-3 3

        The log has one line: the second launch with the same key is
        repeated, and reconcile without a state file leaves the row
        uncertain.
        """
        origin = f"{ORIGIN} 3"
        with self.subTest(id="relaunch-one-line", origin=origin):
            self.set_spec("dispatch-wrapper", mode="no-record")
            self.launch(key="once")
            self.act("reconcile", "DI-1")
            second = self.launch(key="once")
            self.assertEqual(
                len(self.wrapper_calls()), 1,
                "relaunch-one-line: the wrapper log has one line",
            )
            self.assertIn("repeated", second.stdout, second.stdout)
            shown = self.act("status", "DI-1")
            self.assertIn("uncertain", shown.stdout, shown.stdout)

    def test_a_stale_base_refusal_is_launcher(self):
        """origin: sessions-lever.md#slice-3 4"""
        origin = f"{ORIGIN} 4"
        with self.subTest(id="wrapper-stale-base", origin=origin):
            self.set_spec("dispatch-wrapper", mode="refuse", line=STALE_BASE, code=3)
            proc = self.launch(key="stale")
            self.refuses(proc, "wrapper-stale-base", "launcher", origin, named=(STALE_BASE,))
            shown = self.act("status", "DI-1")
            self.assertIn("intent DI-1 refused", shown.stdout, shown.stdout)

    def test_a_dispatcher_who_is_not_the_holder_refuses_holder(self):
        """origin: sessions-lever.md#slice-3 5"""
        origin = f"{ORIGIN} 5"
        with self.subTest(id="not-holder", origin=origin):
            bound = self.ok(COORD, "bind", "--roles", "worker", session=PEER_SESSION)
            peer = _ident(bound, "PT")
            self.ok(COORD, "grant", peer, "--roles", "dispatcher", session=self.dispatcher)
            proc = self.launch(key="holder-ask", session=PEER_SESSION)
            self.refuses(
                proc, "not-holder", "holder", origin, named=("dispatcher.py set",),
            )
            self.assertEqual(self.intent_count(), 0)

    def test_a_worker_refuses_role(self):
        """origin: sessions-lever.md#slice-3 5"""
        origin = f"{ORIGIN} 5"
        with self.subTest(id="worker-role", origin=origin):
            proc = self.launch(key="role-ask", session=self.worker)
            self.refuses(proc, "worker-role", "role", origin, named=("dispatcher",))
            self.assertEqual(self.intent_count(), 0)

    def test_the_seventh_ask_refuses_capacity(self):
        """origin: sessions-lever.md#slice-3 6"""
        origin = f"{ORIGIN} 6"
        with self.subTest(id="seventh-at-cap", origin=origin):
            for number in range(1, 7):
                self.launch(key=f"cap-{number}")
            proc = self.launch(key="cap-7")
            self.refuses(proc, "seventh-at-cap", "capacity", origin, named=("6",))
            self.assertEqual(self.intent_count(), 6)

    def test_wait_on_a_reading_that_is_not_clean(self):
        """origin: sessions-lever.md#slice-3 7"""
        origin = f"{ORIGIN} 7"
        with self.subTest(id="wait-not-clean", origin=origin):
            self.launch(key="wait-dirty")
            self.set_spec(
                "dispatch-launch", wait="not-clean", wait_reading="residue remains",
            )
            proc = self.act("wait", "DI-1", "--timeout", "5", "--interval", "1")
            self.assertEqual(proc.exit, 1, proc.stdout + proc.stderr)
            self.assertIn("residue remains", proc.stdout + proc.stderr)
            self.assertNotEqual(proc.exit, 2)

    def test_wait_on_the_timeout_exits_1(self):
        """origin: sessions-lever.md#slice-3 7"""
        origin = f"{ORIGIN} 7"
        with self.subTest(id="wait-timeout", origin=origin):
            self.launch(key="wait-time")
            self.set_spec("dispatch-launch", wait="timeout")
            proc = self.act("wait", "DI-1", "--timeout", "2", "--interval", "1")
            self.assertEqual(proc.exit, 1, proc.stdout + proc.stderr)
            self.assertRegex(proc.stdout + proc.stderr, r"wait DI-1 timeout after 2")

    def test_collect_verify_tests_closes(self):
        """origin: sessions-lever.md#slice-3 8"""
        origin = f"{ORIGIN} 8"
        with self.subTest(id="collect-tests", origin=origin):
            self.launch(key="collect")
            self.set_spec("dispatch-launch", collect_reading="collected-reading-tests")
            name, expected = self.verification()
            proc = self.act("collect", "DI-1", "--verify", name)
            self.assertIn("collected-reading-tests", proc.stdout, proc.stdout)
            self.assertEqual(proc.exit, 0, proc.stderr)
            calls = self.launcher_calls()
            self.assertTrue(calls, "collect-tests: launch.py was called")
            argv = calls[-1]["argv"]
            for token in expected:
                self.assertIn(token, argv, argv)
            shown = self.act("status", "DI-1")
            self.assertIn("closed", shown.stdout, shown.stdout)

    def test_collect_verify_text_refuses_verify(self):
        """origin: sessions-lever.md#slice-3 8"""
        origin = f"{ORIGIN} 8"
        with self.subTest(id="collect-verify-text", origin=origin):
            self.launch(key="verify-bad")
            before = len(self.launcher_calls())
            proc = self.act("collect", "DI-1", "--verify", "rm -rf")
            self.refuses(proc, "collect-verify-text", "verify", origin)
            self.assertEqual(len(self.launcher_calls()), before, self.launcher_calls())

    def test_recover_after_a_launcher_death(self):
        """origin: sessions-lever.md#slice-3 10"""
        origin = f"{ORIGIN} 10"
        with self.subTest(id="recover-after-death", origin=origin):
            self.set_spec("dispatch-wrapper", mode="write-die")
            self.launch(key="death")
            mid = len(self.wrapper_calls())
            self.act("recover")
            shown = self.act("status", "DI-1")
            self.assertRegex(shown.stdout, r"intent DI-1 launched")
            self.assertEqual(len(self.wrapper_calls()), mid, "recover launched nothing")
            self.assertTrue(
                any(call["argv"][:1] == ["status"] for call in self.launcher_calls()),
                self.launcher_calls(),
            )

    def test_generation_when_the_assignment_is_not_open(self):
        """origin: sessions-lever.md#slice-3 refusal generation

        A released assignment is no longer open at the caller's generation.
        """
        origin = f"{ORIGIN} refusal generation"
        with self.subTest(id="generation-not-open", origin=origin):
            self.ok(
                COORD, "release", self.assignment, "--why", "not open",
                "--generation", self.generation(), session=self.worker,
            )
            proc = self.launch(key="gen-ask")
            self.refuses(proc, "generation-not-open", "generation", origin)

    def test_an_unknown_job_refuses_job(self):
        """origin: sessions-lever.md#slice-3 refusal job"""
        origin = f"{ORIGIN} refusal job"
        with self.subTest(id="job-unknown", origin=origin):
            proc = self.launch(key="job-ask", job="not-a-job")
            self.refuses(proc, "job-unknown", "job", origin)
            shown = self.act("status", "DI-1")
            self.assertIn("intent DI-1 refused", shown.stdout, shown.stdout)

    def test_an_unknown_harness_refuses_harness(self):
        """origin: sessions-lever.md#slice-3 refusal harness"""
        origin = f"{ORIGIN} refusal harness"
        with self.subTest(id="harness-unknown", origin=origin):
            proc = self.launch(key="harness-ask", harness="not-a-harness")
            self.refuses(proc, "harness-unknown", "harness", origin)
            shown = self.act("status", "DI-1")
            self.assertIn("intent DI-1 refused", shown.stdout, shown.stdout)

    def test_wait_on_uncertain_refuses_uncertain(self):
        """origin: sessions-lever.md#slice-3 refusal uncertain"""
        origin = f"{ORIGIN} refusal uncertain"
        with self.subTest(id="wait-uncertain", origin=origin):
            self.set_spec("dispatch-wrapper", mode="no-record")
            self.launch(key="uncertain")
            proc = self.act("wait", "DI-1", "--timeout", "2", "--interval", "1")
            self.refuses(proc, "wait-uncertain", "uncertain", origin, named=("reconcile",))

    def test_an_unknown_intent_refuses_unknown(self):
        """origin: sessions-lever.md#slice-3 refusal unknown"""
        origin = f"{ORIGIN} refusal unknown"
        with self.subTest(id="status-unknown", origin=origin):
            proc = self.act("status", "DI-999")
            self.refuses(proc, "status-unknown", "unknown", origin)

    def test_two_state_files_refuse_correlation(self):
        """origin: sessions-lever.md#slice-3 refusal correlation"""
        origin = f"{ORIGIN} refusal correlation"
        with self.subTest(id="two-correlations", origin=origin):
            self.set_spec("dispatch-wrapper", mode="no-record")
            self.launch(key="corr")
            calls = self.wrapper_calls()
            correlation = "00000000-0000-4000-8000-000000000001"
            if calls:
                correlation = flag(calls[-1]["argv"], "--correlation") or correlation
            for name in ("job-a", "job-b"):
                directory = self.jobs / name
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "state.json").write_text(
                    json.dumps({"correlation": correlation}) + "\n",
                    encoding="utf-8",
                )
            proc = self.act("reconcile", "DI-1")
            self.refuses(proc, "two-correlations", "correlation", origin)

    def test_an_unwritable_store_refuses_store(self):
        """origin: sessions-lever.md#slice-3 refusal store"""
        origin = f"{ORIGIN} refusal store"
        with self.subTest(id="store-unwritable", origin=origin):
            self.assertTrue(self.state.is_dir(), self.state)
            os.chmod(self.state, 0o500)
            try:
                proc = self.launch(key="store-ask")
                self.refuses(proc, "store-unwritable", "store", origin)
                self.assertEqual(self.intent_count(), 0)
            finally:
                os.chmod(self.state, 0o700)

    def test_a_non_executable_wrapper_refuses_launcher(self):
        """origin: sessions-lever.md#slice-3 programs"""
        origin = f"{ORIGIN} programs"
        with self.subTest(id="wrapper-not-executable", origin=origin):
            bad = self.world.plant_text("wrapper-not-exec.txt", "not a program\n")
            self.assertFalse(os.access(bad, os.X_OK), bad)
            proc = self.launch(key="bad-wrapper", extra={"DISPATCH_WRAPPER": str(bad)})
            self.refuses(proc, "wrapper-not-executable", "launcher", origin)
            self.assertEqual(self.intent_count(), 0)

    def test_a_non_executable_launcher_refuses_launcher(self):
        """origin: sessions-lever.md#slice-3 programs"""
        origin = f"{ORIGIN} programs"
        with self.subTest(id="launcher-not-executable", origin=origin):
            bad = self.world.plant_text("launcher-not-exec.txt", "not a program\n")
            self.assertFalse(os.access(bad, os.X_OK), bad)
            proc = self.launch(key="bad-launcher", extra={"DISPATCH_LAUNCHER": str(bad)})
            self.refuses(proc, "launcher-not-executable", "launcher", origin)
            self.assertEqual(self.intent_count(), 0)

    def test_a_non_executable_monitor_refuses_store(self):
        """origin: sessions-lever.md#slice-3 programs"""
        origin = f"{ORIGIN} programs"
        with self.subTest(id="monitor-not-executable", origin=origin):
            bad = self.world.plant_text("monitor-not-exec.txt", "not a program\n")
            self.assertFalse(os.access(bad, os.X_OK), bad)
            proc = self.launch(key="bad-monitor", extra={"DISPATCH_MONITOR": str(bad)})
            self.refuses(proc, "monitor-not-executable", "store", origin)
            self.assertEqual(self.intent_count(), 0)

    def test_an_input_past_the_scope_cap_launches(self):
        """origin: sessions-lever.md#slice-3 clean input"""
        origin = f"{ORIGIN} clean input"
        with self.subTest(id="input-past-scope-cap", origin=origin):
            big = self.world.plant_text("big-input.txt", "x" * (self.scope_cap + 1))
            self.assertGreater(big.stat().st_size, self.scope_cap)
            proc = self.launch(key="wide", input_path=big)
            intent, _record = self.launched(proc, "input-past-scope-cap")
            calls = self.wrapper_calls()
            self.assertEqual(len(calls), 1, calls)
            scope = Path(flag(calls[0]["argv"], "--scope-file"))
            text = scope.read_text(encoding="utf-8")
            lines = [line for line in text.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1, text)
            self.assertLessEqual(len(text.encode("utf-8")), self.scope_cap, text)
            self.assertIn(intent, lines[0])
            self.assertEqual(flag(calls[0]["argv"], "--input"), str(big))

    def test_status_by_a_worker(self):
        """origin: sessions-lever.md#slice-3 clean status

        `status` "reads only; no identity": the worker reads with no
        WF_AGENT (20260923T020916Z-review-codex-5a41cd#2)."""
        origin = f"{ORIGIN} clean status"
        with self.subTest(id="status-by-worker", origin=origin):
            self.launch(key="read")
            proc = self.act("status", "DI-1", session=self.worker, identity=False)
            self.assertRegex(proc.stdout, r"intent DI-1 launched record \S+")
            self.assertEqual(proc.exit, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
