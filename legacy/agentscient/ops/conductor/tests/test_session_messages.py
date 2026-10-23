"""Slice 2 session messages: bridge, join, recover, input, readiness, stop.

Contract: ops/conductor/sessions-lever.md section Slice 2. Run from the
repository root:
python3 -m unittest discover -s ops/conductor/tests -p test_session_messages.py

The codex and grok fakes log each line they receive with their own pid
and their ancestors' pids, so a row can tell which member's bridge
carried it. Every world's tmux server is killed at close on its private
socket, through `run` with a timeout.

The transport table's opposite side: `input` on a codex member is its
clean neighbour in the same case. `bridge` on a codex member has no row
of its own here; it is launched by `start` in every codex row, so a
subject refusing it for every harness fails those rows (declared gap:
no direct `bridge` neighbour).

Planted faults: `session_messages_faults.py` beside this module, anchored
on sessions.py at bf924ee7, for the join row. Declared gap: planted faults
follow the implementation; the codex and grok member rows carry none
while LD-134 keeps every such member from starting, and each names LD-134
in its docstring.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ops.conductor.tests.private_tmux import ORIGIN_DAEMON, RecordedTmux, carrying_home
from ops.testkit import RefusalCase, World, refusal_wire, run, run_table

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "sessions.py"
COORD = TOOLS / "coordination.py"
COURIER = TOOLS / "courier.py"
FAKES = Path(__file__).resolve().parent / "fakes"
LANE = "agentscient"
TIMEOUT = 45.0
# A carry is the bridge's next pass after a send, never the send's return:
# a row reading the store or a fake's log waits this long for it.
CARRY_WAIT = 20.0
READY = 15
AGENT = "Sessions Tester <noreply@example.test>"
LEAD_SESSION = "lead-already-bound"
OWNER_SESSION = "owner-already-bound"
SHORT_ROOT = "/tmp"
SOCKET_LIMIT = 108
HOST_TMUX = shutil.which("tmux")
REPLY = "codex-reply-a503"
CARRY = "please-carry-a503"
SECOND_BODY = "only-the-second-a503"
OPERATOR = "operator-text-a503"
DIMENSIONS = (
    "process", "profile", "identity", "participant",
    "role", "tools", "ingress", "response",
)


def origin(number) -> str:
    return f"sessions-lever.md#slice-2 {number}"


def shebang(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    return f"#!{sys.executable}\n" + text


def substitute(text: str, anchor: str, value: str, *, label: str) -> str:
    count = text.count(anchor)
    if count != 1:
        raise AssertionError(f"INVALID: {label} {anchor} count is {count}")
    return text.replace(anchor, value)


def a_team(*members, name: str = "lane", tmux: str = "agentscient") -> dict:
    return {
        "schema": "agentscient-team/1",
        "run": {"name": name, "tmux": tmux},
        "members": list(members),
    }


def claude_member(name: str, roles: list[str]) -> dict:
    return {"name": name, "harness": "claude", "roles": roles}


def protocol_member(name: str, harness: str, roles: list[str]) -> dict:
    return {"name": name, "harness": harness, "roles": roles}


class SessionMessages(RecordedTmux, unittest.TestCase):
    """Rows for sessions.py slice 2, each addressed by id with its origin."""

    def open_world(self, prefix: str) -> World:
        saved = tempfile.tempdir
        tempfile.tempdir = SHORT_ROOT
        try:
            return World(prefix=prefix)
        finally:
            tempfile.tempdir = saved

    def setUp(self):
        self.world = self.open_world("s2msg-")
        self.addCleanup(self.world.close)
        # The tmux server and the harness in its panes are recorded with the
        # kit as the rows start them (private_tmux.RecordedTmux).
        self.conf = self.world.plant_text("lane.conf", f"LANE_NAME='{LANE}'\n")
        self.state = self.world.xdg_state / LANE
        self.socket = self.world.tmp / "tmux.sock"
        if len(os.fsencode(str(self.socket))) >= SOCKET_LIMIT:
            raise AssertionError(
                f"INVALID: private tmux socket must be under {SOCKET_LIMIT} bytes: "
                f"{self.socket}"
            )
        self.team_path = self.world.plant_text(
            "team.json",
            json.dumps(a_team(claude_member("co", ["worker"]))) + "\n",
        )
        self._plant_harness()
        if HOST_TMUX:
            self._plant_tmux()
        self._plant_protocol("codex", FAKES / "session_codex.py")
        self._plant_protocol("grok", FAKES / "session_grok.py")
        self._plant_credentials()
        self.configure(
            "codex", native_id="thread-codex-1", ack_probe=False, ack=False,
            send_reply=False, reply=REPLY, reject_method=None, die_after=None,
        )
        self.configure(
            "grok", native_id="session-grok-1", ack_probe=False, ack=False,
            send_reply=False, reply="grok-reply-a503", reject_method=None,
            die_after=None, advertise_auth=True,
        )

    def _plant_harness(self) -> None:
        text = substitute(shebang(FAKES / "session_harness.py"), "@@REPO@@", str(REPO), label="harness")
        self.world.tools.plant("claude", script=text)
        spec_path = self.world.tools.spec_path("claude")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["session_id"] = None
        spec["ack_probe"] = True
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")

    def _plant_tmux(self) -> None:
        log = self.world.root / "fakes" / "tmux.calls.jsonl"
        text = shebang(FAKES / "session_tmux.py")
        text = substitute(text, "@@TMUX@@", HOST_TMUX, label="tmux")
        text = substitute(text, "@@LOG@@", str(log), label="tmux")
        text = substitute(text, "@@REPO@@", str(REPO), label="tmux")
        self.world.tools.plant("tmux", script=text)

    def _plant_credentials(self) -> None:
        """A credential file where each protocol harness's isolated home
        links it from: the world's home, never the operator's."""
        home = self.world.home.relative_to(self.world.root)
        for harness in ("codex", "grok"):
            self.world.plant_text(str(home / f".{harness}" / "auth.json"), "{}\n")

    def _plant_protocol(self, name: str, source: Path) -> None:
        text = substitute(shebang(source), "@@REPO@@", str(REPO), label=name)
        self.world.tools.plant(name, script=text)

    def configure(self, name: str, **extra) -> None:
        path = self.world.tools.spec_path(name)
        spec = json.loads(path.read_text(encoding="utf-8"))
        spec.update(extra)
        rendered = json.dumps(spec, indent=1) + "\n"
        path.write_text(rendered, encoding="utf-8")
        if json.loads(path.read_text(encoding="utf-8")) != spec:
            raise AssertionError(f"INVALID: {name} spec did not land intact")

    def plant_team(self, team: dict) -> None:
        self.team_path = self.world.plant_text(
            "team.json", json.dumps(team) + "\n", recognisable=team["schema"]
        )

    def env(self, *, identity: str | None = AGENT, session: str | None = None,
            extra: dict | None = None) -> dict:
        payload = {
            "LANE_CONF": str(self.conf),
            "AGENTSCIENT_TMUX_SOCKET": str(self.socket),
            "TESTKIT_FAKE_SPEC": str(self.world.tools.spec_path("claude")),
        }
        if identity:
            payload["WF_AGENT"] = identity
        if session:
            payload["CLAUDE_CODE_SESSION_ID"] = session
            payload["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        if extra:
            payload.update(extra)
        built = self.world.env(extra=payload)
        self.assertEqual(built["HOME"], str(self.world.home))
        self.assertEqual(built["XDG_STATE_HOME"], str(self.world.xdg_state))
        return built

    def sessions(self, *argv: str, identity: str | None = AGENT,
                 session: str | None = None, timeout: float = TIMEOUT,
                 extra: dict | None = None):
        result = run(
            [sys.executable, str(TOOL), *argv],
            cwd=self.world.cwd,
            env=self.env(identity=identity, session=session, extra=extra),
            timeout=timeout,
        )
        self.own_server()
        return result

    def coord(self, *argv: str, session: str = LEAD_SESSION):
        result = run(
            [sys.executable, str(COORD), *argv],
            cwd=self.world.cwd,
            env=self.env(session=session),
            timeout=TIMEOUT,
        )
        self.assertEqual(result.exit, 0, f"{argv}: {result.stderr}")
        return result

    def bind_lead(self) -> None:
        self.coord("bind", "--roles", "lead,coordinator", session=LEAD_SESSION)

    def start_argv(self, member: str = "co", *more: str, ready: int = READY) -> tuple[str, ...]:
        return (
            "start", "--member", member, "--team", str(self.team_path),
            "--ready-timeout", str(ready), *more,
        )

    def skip_without_tmux(self) -> None:
        if not HOST_TMUX:
            raise unittest.SkipTest("tmux is absent; private-socket rows need it")

    def started_run(self, text: str) -> str | None:
        match = re.search(r"\b(R-\d+)\b", text)
        return match.group(1) if match else None

    def started_pt(self, text: str, name: str) -> str | None:
        match = re.search(rf"started {re.escape(name)} SM-\d+ as (PT-\d+)", text)
        return match.group(1) if match else None

    def pid_of(self, member: str) -> int | None:
        for path in (self.state / "sessions").glob(f"*/{member}/launch.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            raw = data.get("pid") if isinstance(data, dict) else None
            if isinstance(raw, int) and raw > 1:
                return raw
            if isinstance(raw, str) and raw.isdigit() and int(raw) > 1:
                return int(raw)
        return None

    def _alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def proc_ticks(self, pid: int) -> str:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return text.rsplit(")", 1)[1].split()[19]

    def log_text(self, name: str) -> str:
        path = self.world.tools.log_path(name)
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8")

    def member_creds(self, member: str) -> tuple[str, str] | None:
        bindings = list((self.state / "sessions").glob(f"*/{member}/profile/binding.json"))
        if not bindings:
            return None
        binding = json.loads(bindings[0].read_text(encoding="utf-8"))
        launch = json.loads((bindings[0].parent.parent / "launch.json").read_text(encoding="utf-8"))
        cap = binding.get("coordination_capability")
        session = launch.get("launch_id")
        if not isinstance(cap, str) or not isinstance(session, str):
            return None
        return session, cap

    def coord_as(self, member: str, *argv: str):
        creds = self.member_creds(member)
        if creds is None:
            return None
        session, cap = creds
        return run(
            [sys.executable, str(COORD), *argv],
            cwd=self.world.cwd,
            env=self.env(session=session, extra={"COORDINATION_CAPABILITY": cap}),
            timeout=TIMEOUT,
        )

    def connect(self):
        db = self.state / "state.db"
        self.assertTrue(db.is_file(), "state.db is absent")
        return sqlite3.connect(db)

    def until(self, probe, timeout: float = CARRY_WAIT):
        """``probe()`` until it returns a true value or the wait ends; the
        last value either way, so the row's own assertion reads it."""
        deadline = time.monotonic() + timeout
        while True:
            value = probe()
            if value or time.monotonic() >= deadline:
                return value
            time.sleep(0.2)

    def log_holding(self, name: str, needle: str) -> str:
        """The fake's call log once it carries ``needle``, else as it stands
        when the wait ends."""
        self.until(lambda: needle in self.log_text(name))
        return self.log_text(name)

    def attempt_outcomes(self, message_id: int | None = None) -> list:
        with self.connect() as conn:
            try:
                if message_id is None:
                    rows = conn.execute("SELECT outcome FROM delivery_attempts").fetchall()
                else:
                    rows = conn.execute(
                        "SELECT outcome FROM delivery_attempts WHERE message = ?",
                        (message_id,),
                    ).fetchall()
            except sqlite3.OperationalError as exc:
                self.fail(f"delivery_attempts is absent: {exc}")
        return [row[0] for row in rows]

    def message_row(self, body: str):
        with self.connect() as conn:
            try:
                row = conn.execute(
                    "SELECT id, delivered_at, delivery_ref, acked_at FROM messages WHERE body = ?",
                    (body,),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                self.fail(f"messages is not readable: {exc}")
        return row

    def marking_count(self) -> int:
        with self.connect() as conn:
            try:
                return conn.execute("SELECT COUNT(*) FROM marking").fetchone()[0]
            except sqlite3.OperationalError as exc:
                self.fail(f"marking is not readable: {exc}")

    def generations(self, name: str) -> list:
        with self.connect() as conn:
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )]
            self.assertIn("session_member", tables, f"tables: {tables}")
            cols = [row[1] for row in conn.execute("PRAGMA table_info(session_member)")]
            self.assertIn("generation", cols, f"session_member columns: {cols}")
            rows = conn.execute(
                "SELECT generation FROM session_member WHERE name = ?", (name,)
            ).fetchall()
        return [row[0] for row in rows]

    def assert_eight_ok(self, text: str) -> None:
        for dim in DIMENSIONS:
            self.assertRegex(text, rf"\b{dim}\s*:?\s*ok\b", f"{dim} is not ok:\n{text}")

    def owned_holder(self) -> tuple[int, object]:
        nonce = str(time.time_ns())
        ready = self.world.tmp / f"holder-{nonce}.ready"
        stop = self.world.tmp / f"holder-{nonce}.stop"
        self.world.tools.plant("holder", script=shebang(FAKES / "session_holder.py"))
        box: dict = {}

        def invoke() -> None:
            try:
                box["result"] = run(
                    [str(self.world.bin / "holder"), str(ready), str(stop)],
                    cwd=self.world.cwd,
                    env=self.world.env(),
                    timeout=120,
                )
            except Exception as exc:
                box["error"] = exc

        thread = threading.Thread(target=invoke)
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not ready.is_file():
            time.sleep(0.02)
        self.assertTrue(ready.is_file(), f"holder was not started through ops.testkit.run: {box!r}")
        pid = int(ready.read_text(encoding="utf-8"))

        def release() -> None:
            if not stop.exists():
                stop.write_text("stop\n", encoding="utf-8")
            thread.join(timeout=15)
            if self._alive(pid):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGTERM)

        self.addCleanup(release)
        return pid, release

    def test_cross_harness_exchange(self):
        """origin: sessions-lever.md#slice-2 1

        A claude request is carried by the codex bridge: one attempt then
        ``accepted turn <id>``, ``delivered_at`` set with the ``native:``
        ref, ``acked_at`` NULL until the fake acks. The codex reply is
        printed by the stdout courier in the claude session.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="cross-harness-exchange", origin=origin(1)):
            self.plant_team(a_team(
                claude_member("co", ["worker"]),
                protocol_member("dispatcher", "codex", ["worker"]),
            ))
            self.configure(
                "codex", native_id="thread-codex-1", ack_probe=True, ack=False,
                send_reply=True, reply=REPLY,
            )
            self.bind_lead()
            claude = self.sessions(*self.start_argv("co"), session=LEAD_SESSION)
            run_id = self.started_run(claude.stdout)
            more = ("--run", run_id) if run_id else ()
            codex = self.sessions(*self.start_argv("dispatcher", *more), session=LEAD_SESSION)
            participant = self.started_pt(codex.stdout, "dispatcher")
            if participant:
                self.coord_as(
                    "co", "send", "--to", participant, "--purpose", "request", "--body", CARRY,
                )
            row = self.until(
                lambda: (found := self.message_row(CARRY)) is not None and found[1] is not None
                and found
            ) or self.message_row(CARRY)
            self.assertIsNotNone(row, f"no message {CARRY!r}")
            self.assertIsNotNone(row[1], row)
            # The request's own attempts: the readiness probe's are the
            # member's too, and are not this carry.
            outcomes = self.attempt_outcomes(row[0])
            self.assertEqual(
                sum(1 for item in outcomes if isinstance(item, str) and item.startswith("accepted turn ")),
                1,
                outcomes,
            )
            self.assertRegex(row[2] or "", r"^native:AT-\d+:")
            self.assertIsNone(row[3], row)
            self.configure("codex", ack=True)
            if run_id:
                self.sessions(
                    "input", "--run", run_id, "--member", "dispatcher", "ack-the-request",
                )
            acked = self.until(
                lambda: (found := self.message_row(CARRY)) is not None and found[3] is not None
                and found
            ) or self.message_row(CARRY)
            self.assertIsNotNone(acked[3], acked)
            creds = self.member_creds("co")
            self.assertIsNotNone(creds, "the claude member has no capability")
            session, cap = creds
            seen: list = []

            def watched() -> bool:
                printed = run(
                    [sys.executable, str(COURIER), "watch", "--once"],
                    cwd=self.world.cwd,
                    env=self.env(session=session, extra={"COORDINATION_CAPABILITY": cap}),
                    timeout=TIMEOUT,
                )
                seen.append(printed.stdout + printed.stderr)
                return REPLY in printed.stdout

            self.until(watched)
            self.assertIn(REPLY, "".join(seen), seen)

    def test_two_codex_members_write_only_the_second_log(self):
        """origin: sessions-lever.md#slice-2 2; 20260923T020912Z-review-codex-9d8b8c #1

        A message to the second codex member is written on the second
        fake's channel only: every log line carrying the body names the
        second bridge among its ancestors and never the first.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="two-codex-second-log", origin=origin(2)):
            self.plant_team(a_team(
                protocol_member("one", "codex", ["worker"]),
                protocol_member("two", "codex", ["worker"]),
            ))
            self.configure("codex", native_id="", ack_probe=True, send_reply=False)
            self.bind_lead()
            first = self.sessions(*self.start_argv("one"), session=LEAD_SESSION)
            run_id = self.started_run(first.stdout)
            more = ("--run", run_id) if run_id else ()
            second = self.sessions(*self.start_argv("two", *more), session=LEAD_SESSION)
            participant = self.started_pt(second.stdout, "two")
            if participant:
                self.coord(
                    "send", "--to", participant, "--purpose", "request",
                    "--body", SECOND_BODY, session=LEAD_SESSION,
                )
            log = self.log_holding("codex", SECOND_BODY)
            self.assertIn(SECOND_BODY, log, log)
            second_pid = self.pid_of("two")
            first_pid = self.pid_of("one")
            self.assertIsNotNone(second_pid, "the second member recorded no pid")
            self.assertIsNotNone(first_pid, "the first member recorded no pid")
            lines = [json.loads(line) for line in log.splitlines() if line.strip()]
            body_lines = [line for line in lines if SECOND_BODY in json.dumps(line)]
            self.assertTrue(body_lines, log)
            # Each fake logs its own ancestry: the second member's bridge
            # spawned the fake that received the body, the first's did not.
            self.assertTrue(
                all(second_pid in line.get("ancestors", []) for line in body_lines), body_lines,
            )
            self.assertFalse(
                any(first_pid in line.get("ancestors", []) for line in body_lines), body_lines,
            )

    def test_method_not_found_leaves_the_bridge_alive(self):
        """origin: sessions-lever.md#slice-2 6

        -32601 to ``turn/start`` records ``unsupported turn/start``, the
        message stays undelivered, ingress reads ``refused channel``, and
        the bridge is alive after the backoff.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="unsupported-turn-start", origin=origin(6)):
            self.plant_team(a_team(protocol_member("dispatcher", "codex", ["worker"])))
            self.configure(
                "codex", native_id="thread-codex-6", reject_method="turn/start",
                ack_probe=False, send_reply=False,
            )
            self.bind_lead()
            started = self.sessions(*self.start_argv("dispatcher", ready=4), session=LEAD_SESSION)
            self.started_run(started.stdout)
            participant = self.started_pt(started.stdout, "dispatcher")
            if participant:
                self.coord(
                    "send", "--to", participant, "--purpose", "request",
                    "--body", "undelivered-a503", session=LEAD_SESSION,
                )
            outcomes = self.attempt_outcomes()
            self.assertTrue(
                any(isinstance(item, str) and item.startswith("unsupported turn/start") for item in outcomes),
                outcomes,
            )
            status = self.sessions("status", identity=None).stdout
            self.assertRegex(status, r"\bingress\s*:?\s*refused\s+channel\b")
            row = self.message_row("undelivered-a503")
            if row is None and "readiness probe" in (started.stdout + status):
                row = self.message_row_like("readiness probe")
            self.assertIsNotNone(row, "no undelivered message")
            self.assertIsNone(row[1], row)
            pid = self.pid_of("dispatcher")
            self.assertIsNotNone(pid, "the bridge recorded no pid")
            self.assertTrue(self._alive(pid), f"the bridge {pid} is not alive")

    def message_row_like(self, needle: str):
        with self.connect() as conn:
            try:
                return conn.execute(
                    "SELECT id, delivered_at, delivery_ref, acked_at FROM messages WHERE body LIKE ?",
                    (f"%{needle}%",),
                ).fetchone()
            except sqlite3.OperationalError as exc:
                self.fail(f"messages is not readable: {exc}")

    def test_recover_alive_then_reused_pid_mints_generation_2(self):
        """origin: sessions-lever.md#slice-2 7

        ``recover`` with the bridge alive moves nothing and launches
        nothing. With that pid reaped and reused by a world-owned child,
        the member is ``lost`` and the child is not signalled. ``start``
        again mints generation 2 and keeps the old row.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="recover-alive-reused-generation-2", origin=origin(7)):
            self.plant_team(a_team(protocol_member("dispatcher", "codex", ["worker"])))
            # Real codex answers every thread/start with a new thread id, so
            # generation 2 must not inherit generation 1's (b3's reading).
            self.configure("codex", native_id="", ack_probe=True, send_reply=False)
            self.bind_lead()
            started = self.sessions(*self.start_argv("dispatcher"), session=LEAD_SESSION)
            run_id = self.started_run(started.stdout)
            recover_argv = ("recover", "--run", run_id) if run_id else ("recover",)
            before = self.sessions("status", identity=None).stdout
            calls_before = len(self.world.tools.calls("codex"))
            recovered = self.sessions(*recover_argv)
            self.assertEqual(recovered.exit, 0, recovered.stderr + recovered.stdout)
            self.assertNotIn("started ", recovered.stdout)
            self.assertEqual(len(self.world.tools.calls("codex")), calls_before)
            after = self.sessions("status", identity=None).stdout
            self.assertEqual(self._place(before, "dispatcher"), self._place(after, "dispatcher"))
            pid = self.pid_of("dispatcher")
            self.assertIsNotNone(pid, "the live bridge recorded no pid")
            ticks = self.proc_ticks(pid)
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 5
            while self._alive(pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(self._alive(pid), "the bridge was not reaped")
            holder, _release = self.owned_holder()
            with self.connect() as conn:
                changed = conn.execute(
                    "UPDATE session_member SET pid = ?, start_ticks = ? WHERE name = ?",
                    (holder, ticks, "dispatcher"),
                ).rowcount
            self.assertEqual(changed, 1, "the reused pid was not planted")
            lost = self.sessions(*recover_argv)
            self.assertEqual(lost.exit, 1, lost.stderr + lost.stdout)
            self.assertTrue(self._alive(holder), "the reused pid was signalled")
            combined = lost.stdout + "\n" + self.sessions("status", identity=None).stdout
            self.assertRegex(combined, r"\blost\b")
            again = self.sessions(
                *self.start_argv("dispatcher", "--run", run_id or "R-1"),
                session=LEAD_SESSION,
            )
            self.assertRegex(again.stdout, r"\bgen 2\b", again.stdout + again.stderr)
            gens = self.generations("dispatcher")
            self.assertIn(1, gens, gens)
            self.assertIn(2, gens, gens)

    def _place(self, text: str, name: str) -> str:
        match = re.search(rf"\b{re.escape(name)} SM-\d+ (\S+)", text)
        self.assertIsNotNone(match, text)
        return match.group(1)

    def test_stop_on_a_stopped_run_exits_2_and_marks_nothing(self):
        """origin: sessions-lever.md#slice-2 8

        ``stop`` on a stopped run exits 2 and the marking row count is
        unchanged.
        """
        self.skip_without_tmux()
        with self.subTest(id="stop-stopped-run", origin=origin(8)):
            started = self.sessions(*self.start_argv("co"))
            self.assertEqual(started.exit, 0, started.stderr)
            run_id = self.started_run(started.stdout)
            self.assertIsNotNone(run_id, started.stdout)
            stopped = self.sessions("stop", "--terminate", "--run", run_id)
            self.assertEqual(stopped.exit, 0, stopped.stderr)
            before = self.marking_count()
            again = self.sessions("stop", "--run", run_id)
            refusal_wire(
                RefusalCase(
                    id="stop-stopped-run", argv=again.argv[2:], cls="place",
                    origin=origin(8),
                ),
                again,
            )
            self.assertEqual(self.marking_count(), before)

    def test_a_108_byte_socket_refuses_before_tmux(self):
        """origin: sessions-lever.md#slice-2 9

        A ``--tmux-socket`` path of 108 bytes refuses ``socket`` and the
        tmux log stays empty.
        """
        self.skip_without_tmux()
        with self.subTest(id="socket-108", origin=origin(9)):
            prefix = "/tmp/"
            path = prefix + ("s" * (108 - len(prefix.encode())))
            self.assertEqual(len(os.fsencode(path)), 108)
            proc = self.sessions(
                "start", "--member", "co", "--team", str(self.team_path),
                "--tmux-socket", path, "--ready-timeout", "2",
            )
            refusal_wire(
                RefusalCase(
                    id="socket-108", argv=proc.argv[2:], cls="socket",
                    origin=origin(9), named=("108",),
                ),
                proc,
            )
            self.assertEqual(self.world.tools.calls("tmux"), [])

    def test_join_then_stop_is_unresolved_unmanaged(self):
        """origin: sessions-lever.md#slice-2 10

        ``join`` records a bound participant at generation 1 with no pid.
        ``stop --terminate`` prints ``unresolved unmanaged`` and signals
        nothing for that member, though a live holder's pid and ticks sit
        on its row; the launched member beside it is stopped
        (20260923T020912Z-review-codex-9d8b8c #3).
        """
        self.skip_without_tmux()
        with self.subTest(id="join-unmanaged", origin=origin(10)):
            self.plant_team(a_team(
                claude_member("co", ["worker"]),
                claude_member("owner", ["worker"]),
            ))
            self.bind_lead()
            bound = self.coord("bind", "--roles", "worker", session=OWNER_SESSION)
            participant = re.search(r"\b(PT-\d+)\b", bound.stdout)
            self.assertIsNotNone(participant, bound.stdout)
            started = self.sessions(*self.start_argv("co"), session=LEAD_SESSION)
            run_id = self.started_run(started.stdout) or "R-1"
            self.assertEqual(started.exit, 0, started.stderr + started.stdout)
            joined = self.sessions(
                "join", "--run", run_id, "--member", "owner",
                "--participant", participant.group(1), "--team", str(self.team_path),
            )
            self.assertEqual(joined.exit, 0, joined.stderr)
            status = self.sessions("status", identity=None).stdout
            self.assertRegex(status, r"\bowner\b")
            self.assertRegex(status, r"\bbound\b")
            self.assertRegex(status, r"\bgen 1\b")
            self.assertRegex(status, r"\bunmanaged\b")
            self.assertIsNone(self.pid_of("owner"), status)
            launched = self.pid_of("co")
            self.assertIsNotNone(launched, "the launched member recorded no pid")
            # A live pid on the joined row, as a stale or fabricated one
            # would be: the join, not a missing pid, is what leaves it
            # unmanaged, so stop must still signal nothing for it.
            holder, _release = self.owned_holder()
            with self.connect() as conn:
                changed = conn.execute(
                    "UPDATE session_member SET pid = ?, start_ticks = ? WHERE name = ?",
                    (holder, self.proc_ticks(holder), "owner"),
                ).rowcount
            self.assertEqual(changed, 1, "the holder pid was not planted on the joined row")
            stopped = self.sessions("stop", "--terminate", "--run", run_id)
            combined = stopped.stdout + stopped.stderr
            self.assertIn("unresolved unmanaged", combined, combined)
            self.assertTrue(self._alive(holder), "stop signalled a pid the join did not own")
            deadline = time.monotonic() + 10
            while self._alive(launched) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(self._alive(launched), "stop left the launched member running")

    def test_readiness_probe_acked(self):
        """origin: sessions-lever.md#slice-2 11

        A fake that acks the readiness probe reads ``response ok M-n``.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="readiness-acked", origin=origin(11)):
            self.plant_team(a_team(protocol_member("dispatcher", "codex", ["worker"])))
            self.configure("codex", native_id="thread-codex-11", ack_probe=True, send_reply=False)
            self.bind_lead()
            started = self.sessions(*self.start_argv("dispatcher"), session=LEAD_SESSION)
            self.assertRegex(
                started.stdout, r"\bresponse\s*:?\s*ok\s+M-\d+",
                started.stdout + started.stderr,
            )

    def test_readiness_probe_never_acked(self):
        """origin: sessions-lever.md#slice-2 11

        A fake that never acks reads ``waiting ack M-n`` and ``start``
        exits 1 at the timeout with the row at ``bound``.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="readiness-never-acked", origin=origin(11)):
            self.plant_team(a_team(protocol_member("dispatcher", "codex", ["worker"])))
            self.configure("codex", native_id="thread-codex-11b", ack_probe=False, send_reply=False)
            self.bind_lead()
            started = self.sessions(
                *self.start_argv("dispatcher", ready=4), session=LEAD_SESSION,
            )
            combined = started.stdout + "\n" + self.sessions("status", identity=None).stdout
            self.assertRegex(combined, r"\bresponse\s*:?\s*waiting\s+ack\s+M-\d+")
            self.assertEqual(started.exit, 1, started.stderr)
            self.assertRegex(combined, r"\bbound\b")

    def test_protocol_verbs_on_a_claude_member_refuse_transport(self):
        """origin: sessions-lever.md#slice-2 transport

        ``bridge`` and ``input`` on a claude member refuse ``transport``;
        ``input`` on a codex member in the same run does not
        (20260923T020912Z-review-codex-9d8b8c #4).

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        run_id = None
        with self.subTest(id="transport-claude", origin="sessions-lever.md#slice-2 transport"):
            self.plant_team(a_team(
                claude_member("co", ["worker"]),
                protocol_member("dispatcher", "codex", ["worker"]),
            ))
            self.configure("codex", native_id="thread-codex-transport", ack_probe=True,
                           send_reply=False)
            self.bind_lead()
            started = self.sessions(*self.start_argv("co"), session=LEAD_SESSION)
            self.assertEqual(started.exit, 0, started.stderr)
            run_id = self.started_run(started.stdout)
            self.assertIsNotNone(run_id, started.stdout)
            rows = (
                RefusalCase(
                    id="transport-bridge-claude",
                    argv=("bridge", "--run", run_id, "--member", "co"),
                    cls="transport",
                    origin="sessions-lever.md#slice-2 transport",
                ),
                RefusalCase(
                    id="transport-input-claude",
                    argv=("input", "--run", run_id, "--member", "co", OPERATOR),
                    cls="transport",
                    origin="sessions-lever.md#slice-2 transport",
                ),
            )
            run_table(self, rows, lambda row: self.sessions(*row.argv))
        with self.subTest(id="transport-input-codex", origin="sessions-lever.md#slice-2 transport"):
            self.assertIsNotNone(run_id, "the claude member's run did not start")
            codex = self.sessions(
                *self.start_argv("dispatcher", "--run", run_id), session=LEAD_SESSION,
            )
            self.assertEqual(codex.exit, 0, codex.stderr + codex.stdout)
            typed = self.sessions("input", "--run", run_id, "--member", "dispatcher", OPERATOR)
            self.assertFalse(typed.stderr.startswith("transport:"), typed.stderr)
            self.assertEqual(typed.exit, 0, typed.stderr)

    def test_a_codex_member_reads_all_eight_ok(self):
        """origin: sessions-lever.md#slice-2 clean

        A codex member reads all eight readiness dimensions ``ok``.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="codex-eight-ok", origin="sessions-lever.md#slice-2 clean"):
            self.plant_team(a_team(protocol_member("dispatcher", "codex", ["worker"])))
            self.configure("codex", native_id="thread-codex-ok", ack_probe=True, send_reply=False)
            self.bind_lead()
            started = self.sessions(*self.start_argv("dispatcher"), session=LEAD_SESSION)
            self.assert_eight_ok(started.stdout)

    def test_a_grok_member_reads_all_eight_ok(self):
        """origin: sessions-lever.md#slice-2 clean

        A grok member reads all eight readiness dimensions ``ok`` through
        ``session/prompt``.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="grok-eight-ok", origin="sessions-lever.md#slice-2 clean"):
            self.plant_team(a_team(protocol_member("grunt", "grok", ["worker"])))
            self.configure(
                "grok", native_id="session-grok-ok", ack_probe=True, send_reply=False,
                advertise_auth=True,
            )
            self.bind_lead()
            started = self.sessions(*self.start_argv("grunt"), session=LEAD_SESSION)
            self.assert_eight_ok(started.stdout)
            log = self.log_text("grok")
            self.assertIn("session/prompt", log, log)

    def test_input_reaches_the_fake_log(self):
        """origin: sessions-lever.md#slice-2 clean

        ``input`` text is in the protocol fake's call log.

        LD-134: red until ``profiles.py run-interactive`` admits the channel
        argv; a codex or grok member cannot start before it.
        """
        self.skip_without_tmux()
        with self.subTest(id="input-reaches-fake-log", origin="sessions-lever.md#slice-2 clean"):
            self.plant_team(a_team(protocol_member("dispatcher", "codex", ["worker"])))
            self.configure("codex", native_id="thread-codex-input", ack_probe=True, send_reply=False)
            self.bind_lead()
            started = self.sessions(*self.start_argv("dispatcher"), session=LEAD_SESSION)
            run_id = self.started_run(started.stdout) or "R-1"
            given = self.sessions("input", "--run", run_id, "--member", "dispatcher", OPERATOR)
            self.assertEqual(given.exit, 0, given.stderr + given.stdout)
            log = self.log_holding("codex", OPERATOR)
            self.assertIn(OPERATOR, log)

    def test_close_stops_a_server_whose_socket_file_is_gone(self):
        """origin: docs/tests.md, Daemons; the kit's measure at a93368b5

        A started codex member's tmux server, its bridge and the fake it
        spawned are recorded with the kit, so with the socket file removed
        the case's cleanups still stop them: close raises no refusal, and
        no process carries the world's HOME after it.
        """
        self.skip_without_tmux()
        with self.subTest(id="server-socket-file-gone", origin=ORIGIN_DAEMON):
            self.plant_team(a_team(protocol_member("dispatcher", "codex", ["worker"])))
            self.configure("codex", native_id="thread-codex-gone", ack_probe=True, send_reply=False)
            self.bind_lead()
            started = self.sessions(*self.start_argv("dispatcher"), session=LEAD_SESSION)
            self.assertEqual(started.exit, 0, started.stderr + started.stdout)
            self.assertTrue(self.socket.exists(), "the start left no socket")
            self.socket.unlink()
            home = str(self.world.home)
            self.doCleanups()
            self.assertEqual(carrying_home(home), [])


if __name__ == "__main__":
    unittest.main()
