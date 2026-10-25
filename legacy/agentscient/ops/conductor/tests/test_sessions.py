"""Managed sessions slice 1: supervisor verbs, refusal classes, fixture set.

Contract: `ops/conductor/sessions-lever.md`. The subject
`ops/conductor/tools/sessions.py` is run as a subprocess through
`ops.testkit.run` with a timeout. A missing tool file is one red
(`test_the_tool_file_is_present`). Every other case runs the real file
when it exists and a no-op stub when it does not, so each assertion
goes red on the contract promise rather than Python's missing-file
exit 2.

The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_sessions.py`.

Calibration gap: planted faults follow the implementation.
``ops/testkit/cases.py`` calibrate anchors each mutant on the subject's
text, and ``ops/conductor/tools/sessions.py`` is absent, so this module
has no mutants file.
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
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ops.conductor.tests.private_tmux import ORIGIN_DAEMON, RecordedTmux, carrying_home
from ops.testkit import RefusalCase, Watch, World, refusal_wire, run, run_table

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "sessions.py"
COORD = TOOLS / "coordination.py"
FAKES = Path(__file__).resolve().parent / "fakes"
HARNESS_SRC = FAKES / "session_harness.py"
TMUX_SRC = FAKES / "session_tmux.py"
HOLDER_SRC = FAKES / "session_holder.py"
CODEX_SRC = FAKES / "session_codex.py"
GROK_SRC = FAKES / "session_grok.py"
ADAPTER_SRC = FAKES / "session_adapter_claude.py"
REVIEW = "20260923T003033Z-review-codex-c99cc9"
ORIGIN = "sessions-lever.md"
FINDING = "20260922T215604Z-review-codex-f6777b"


def origin_finding(number: int) -> str:
    return f"{FINDING}#{number}"
AGENT = "Sessions Tester <noreply@example.test>"
# The profile pins LANE_CONF to the source ops/lane.conf (profiles.py,
# the authority environment), so the guard binds into that lane's store.
# The world names the same lane under its own XDG root, or the store
# splits in two (A-495 item a).
LANE = "agentscient"
TIMEOUT = 60.0
READY = 15
# The world's root, whatever the ambient temp dir: a unix socket path
# is at most 107 bytes, and the private tmux socket lives in the world.
SHORT_ROOT = "/tmp"
SOCKET_LIMIT = 108
LEAD_SESSION = "lead-already-bound"
WORKER_SESSION = "worker-already-bound"
HOST_TMUX = shutil.which("tmux")

READINESS = (
    "process",
    "profile",
    "identity",
    "participant",
    "role",
    "tools",
    "ingress",
    "response",
)
SLICE2 = ("tools", "ingress", "response")


def _fake_text(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(f"INVALID: planted program missing: {path}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    return f"#!{sys.executable}\n" + text


def a_team(*members, name: str = "lane", tmux: str = "agentscient") -> dict:
    return {
        "schema": "agentscient-team/1",
        "run": {"name": name, "tmux": tmux},
        "members": list(members),
    }


def claude_member(name: str, roles: list[str]) -> dict:
    return {"name": name, "harness": "claude", "roles": roles}


class ThePresence(unittest.TestCase):
    def test_zombie_process_is_gone_for_sessions_dispatch_and_routes(self):
        """LD-150: an unreaped process must not hold readiness or route authority."""
        from ops.conductor.tools import coordination, dispatch_intents, sessions

        world = World(prefix="sessions-zombie-")
        self.addCleanup(world.close)
        executable = world.tools.plant("exit-now", exit=0)
        child = world.own(subprocess.Popen([str(executable)], env=world.env(), cwd=world.cwd))
        deadline = time.monotonic() + 5
        while os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
            self.assertLess(time.monotonic(), deadline, "fixture child did not exit")
            time.sleep(0.01)
        fields = Path(f"/proc/{child.pid}/stat").read_text().rsplit(")", 1)[1].split()
        self.assertEqual(fields[0], "Z", "fixture must remain unreaped")
        ticks = fields[19]
        self.assertTrue(sessions.process_reading(child.pid, ticks).startswith("refused gone"))
        self.assertTrue(dispatch_intents.process_reading(child.pid, ticks).startswith("gone"))
        self.assertFalse(coordination.holder_alive({"pid": child.pid, "start_ticks": ticks}))
        own_ticks = coordination.process_ticks(os.getpid())
        self.assertIsNotNone(own_ticks)
        self.assertTrue(sessions.alive(os.getpid(), own_ticks))
        self.assertTrue(dispatch_intents.alive(os.getpid(), own_ticks))

    def test_the_tool_executes_status_on_an_absent_store(self):
        """origin: 20260922T215604Z-review-codex-f6777b#2

        The case runs the subject. An empty file exits 0 with no reading;
        status on an absent store says ``no runs`` and creates nothing.
        """
        world = World(prefix="sessions-presence-")
        self.addCleanup(world.close)
        conf = world.plant_text("lane.conf", f"LANE_NAME='{LANE}'\n")
        argv = (
            [sys.executable, str(TOOL)]
            if TOOL.is_file()
            else [sys.executable, str(world.stage(TOOL))]
        )
        proc = run(
            [*argv, "status"],
            cwd=world.cwd,
            env=world.env(extra={"LANE_CONF": str(conf)}),
            timeout=TIMEOUT,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertIn("no runs", proc.stdout)
        state = world.xdg_state / LANE
        self.assertFalse(state.exists(), f"status created {state}")


class TheSocketPath(unittest.TestCase):
    """origin: 20260923T003033Z-review-codex-c99cc9#2

    A long ambient temp dir, cached before the world opens, still leaves the
    world under SHORT_ROOT and its tmux socket under SOCKET_LIMIT bytes.
    """

    def test_a_long_cached_temp_dir_keeps_the_socket_short(self):
        holder = World(prefix="sessions-long-")
        self.addCleanup(holder.close)
        long_dir = holder.root / ("t" * 120)
        long_dir.mkdir()
        saved = tempfile.tempdir
        tempfile.tempdir = str(long_dir)
        self.addCleanup(setattr, tempfile, "tempdir", saved)
        with self.subTest(id="socket-under-long-tmpdir",
                          origin="20260923T003033Z-review-codex-c99cc9#2"):
            self.assertEqual(tempfile.gettempdir(), str(long_dir))
            scratch = _Scratch()
            world = scratch.open_world("sessions-")
            self.addCleanup(world.close)
            socket = world.tmp / "tmux.sock"
            self.assertEqual(world.root.parent, Path(SHORT_ROOT))
            self.assertLess(len(os.fsencode(str(socket))), SOCKET_LIMIT, str(socket))
            self.assertEqual(tempfile.gettempdir(), str(long_dir))


class _Scratch(RecordedTmux, unittest.TestCase):
    """Hermetic world, planted fake harness, private tmux socket."""

    def open_world(self, prefix: str) -> World:
        """A world rooted under /tmp, so the private tmux socket it owns
        stays under 108 bytes. origin: 20260922T215604Z-review-codex-f6777b#10
        """
        # tempfile caches its directory at the first call, so TMPDIR set
        # here is not read; tempfile.tempdir is what mkdtemp consults.
        # origin: 20260923T003033Z-review-codex-c99cc9#2
        saved = tempfile.tempdir
        tempfile.tempdir = SHORT_ROOT
        try:
            return World(prefix=prefix)
        finally:
            tempfile.tempdir = saved

    def setUp(self):
        self.world = self.open_world("sessions-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("lane.conf", f"LANE_NAME='{LANE}'\n")
        self.xdg = self.world.xdg_state
        self.state = self.xdg / LANE
        self.socket = self.world.tmp / "tmux.sock"
        if len(os.fsencode(str(self.socket))) >= SOCKET_LIMIT:
            raise AssertionError(
                f"INVALID: private tmux socket must be under {SOCKET_LIMIT} bytes: "
                f"{self.socket}"
            )
        # The server and the harness holding in its panes are recorded with
        # the kit as the rows start them (private_tmux.RecordedTmux).
        self.team_path = self.world.plant_text(
            "team.json",
            json.dumps(a_team(claude_member("grunt", ["worker"]))) + "\n",
        )
        self._plant_harness()
        if HOST_TMUX:
            self._plant_tmux()

    def _plant_harness(self, session_id=None) -> Path:
        text = _fake_text(HARNESS_SRC).replace("@@REPO@@", str(REPO))
        planted = self.world.tools.plant("claude", script=text)
        spec_path = self.world.tools.spec_path("claude")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["session_id"] = session_id
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
        landed = json.loads(spec_path.read_text(encoding="utf-8"))
        if landed.get("session_id") != session_id:
            raise AssertionError("INVALID: harness spec did not land intact")
        return planted

    def _plant_tmux(self) -> Path:
        log = self.world.root / "fakes" / "tmux.calls.jsonl"
        text = (
            _fake_text(TMUX_SRC)
            .replace("@@TMUX@@", HOST_TMUX)
            .replace("@@LOG@@", str(log))
            .replace("@@REPO@@", str(REPO))
        )
        return self.world.tools.plant("tmux", script=text)

    def assert_world_roots(self, built: dict[str, str]) -> None:
        """origin: 20260922T215604Z-review-codex-f6777b#1

        The world owns HOME, the XDG roots and the state root. Every row
        asserts on those.
        """
        self.assertEqual(built["HOME"], str(self.world.home))
        self.assertEqual(built["XDG_STATE_HOME"], str(self.world.xdg_state))
        self.assertEqual(built["XDG_CONFIG_HOME"], str(self.world.xdg_config))
        self.assertEqual(built["XDG_CACHE_HOME"], str(self.world.xdg_cache))
        self.assertEqual(self.xdg, self.world.xdg_state)
        self.assertEqual(self.state, self.world.xdg_state / LANE)

    def env(self, *, identity: str | None = AGENT, session: str | None = None,
            extra: dict[str, str] | None = None) -> dict[str, str]:
        payload = {
            "LANE_CONF": str(self.conf),
            "AGENTSCIENT_TMUX_SOCKET": str(self.socket),
            "TESTKIT_FAKE_SPEC": str(self.world.tools.spec_path("claude")),
        }
        if identity:
            payload["WF_AGENT"] = identity
        if session:
            payload["CLAUDE_CODE_SESSION_ID"] = session
            payload["COORDINATION_CAPABILITY"] = hashlib.sha256(
                session.encode()
            ).hexdigest()
        if extra:
            payload.update(extra)
        built = self.world.env(extra=payload)
        self.assert_world_roots(built)
        return built

    def tool_argv(self) -> list[str]:
        if TOOL.is_file():
            return [sys.executable, str(TOOL)]
        return [sys.executable, str(self.world.stage(TOOL))]

    def sessions(self, *argv: str, identity: str | None = AGENT,
                 session: str | None = None, timeout: float = TIMEOUT,
                 extra: dict[str, str] | None = None):
        result = run(
            [*self.tool_argv(), *argv],
            cwd=self.world.cwd,
            env=self.env(identity=identity, session=session, extra=extra),
            timeout=timeout,
        )
        self.own_server()
        return result

    def coord(self, *argv: str, session: str = LEAD_SESSION, expect: int | None = 0):
        return run(
            [sys.executable, str(COORD), *argv],
            cwd=self.world.cwd,
            env=self.env(session=session),
            timeout=TIMEOUT,
            expect=expect,
        )

    def plant_team(self, team: dict) -> Path:
        path = self.world.plant_text(
            "team.json", json.dumps(team) + "\n", recognisable=team["schema"]
        )
        self.team_path = path
        return path

    def start_argv(self, member: str = "grunt", *more: str) -> tuple[str, ...]:
        return (
            "start",
            "--member",
            member,
            "--team",
            str(self.team_path),
            "--ready-timeout",
            str(READY),
            *more,
        )

    def _alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def owned_holder(self) -> tuple[int, object]:
        """A sleeper started through ops.testkit.run with a timeout.

        origin: 20260922T215604Z-review-codex-f6777b#18
        origin: 20260922T215604Z-review-codex-f6777b#19
        """
        nonce = str(time.time_ns())
        ready = self.world.tmp / f"holder-{nonce}.ready"
        stop = self.world.tmp / f"holder-{nonce}.stop"
        self.world.tools.plant("holder", script=_fake_text(HOLDER_SRC))
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
        self.assertTrue(
            ready.is_file(),
            f"holder was not started through ops.testkit.run: {box!r}",
        )
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

    def skip_without_tmux(self) -> None:
        if not HOST_TMUX:
            raise unittest.SkipTest(
                "tmux is absent; private-socket rows need it"
            )

    def host_tmux(self, *argv: str, expect: int | None = None):
        """The host tmux on this world's private socket.

        The planted shim drops the real server's stderr, so a fixture
        that must exist drives the host binary by absolute path.
        """
        return run(
            [HOST_TMUX, "-S", str(self.socket), *argv],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=TIMEOUT,
            expect=expect,
        )

    def assert_refusal(self, proc, klass: str, *, case_id: str) -> str:
        case = RefusalCase(id=case_id, argv=proc.argv[2:], cls=klass, origin=ORIGIN)
        return refusal_wire(case, proc).line

    def assert_readiness(self, text: str, *, role_waiting: str | None = None) -> None:
        """origin: 20260922T215604Z-review-codex-f6777b#11

        ``ready`` requires process, profile, identity, participant and
        role to read ok, never waiting or refused. Slice 2 reads
        unmeasured and never ok. ``role_waiting`` is the one exception the
        contract allows: a request above worker with no lead's grant reads
        ``role waiting <role_waiting>`` and the member is still ready.
        """
        found = {dim: [] for dim in READINESS}
        pattern = re.compile(
            "(?:^|\\s)(process|profile|identity|participant|role|tools|ingress|response)"
            "\\s*:?\\s*(ok|waiting|refused|unmeasured)\\b"
        )
        for line in text.splitlines():
            for match in pattern.finditer(line):
                found[match.group(1)].append(match.group(2))
        for dim in ("process", "profile", "identity", "participant", "role"):
            states = found[dim]
            if dim == "role" and role_waiting is not None:
                self.assertRegex(
                    text,
                    re.compile(
                        rf"(?:^|\s)role\s*:?\s*waiting\s+{re.escape(role_waiting)}\s*$",
                        re.MULTILINE,
                    ),
                )
                self.assertNotIn("ok", states, f"role reads ok: {text!r}")
                self.assertNotIn("refused", states, f"role reads refused: {text!r}")
                continue
            self.assertIn("ok", states, f"{dim} must read ok, found {states!r} in {text!r}")
            self.assertNotIn("waiting", states, f"{dim} reads waiting: {text!r}")
            self.assertNotIn("refused", states, f"{dim} reads refused: {text!r}")
        for dim in SLICE2:
            states = found[dim]
            self.assertIn("unmeasured", states, f"{dim} must read unmeasured: {text!r}")
            self.assertNotIn("ok", states, f"{dim} is slice 2 and never reads ok: {text!r}")

    def bind_lead(self) -> None:
        self.coord("bind", "--roles", "lead,coordinator", session=LEAD_SESSION)

    def store_who(self, member: str = "grunt") -> str:
        """Roles the coordination store records, read through the core."""
        bindings = list((self.state / "sessions").glob(f"*/{member}/profile/binding.json"))
        self.assertTrue(bindings, f"no binding for {member} under {self.state}")
        binding = json.loads(bindings[0].read_text(encoding="utf-8"))
        cap = binding["coordination_capability"]
        # `who` reads the participant of the session in the environment, so
        # it is asked as the member's launch identity (A-495 item d).
        launch = json.loads(
            (bindings[0].parent.parent / "launch.json").read_text(encoding="utf-8")
        )
        result = run(
            [sys.executable, str(COORD), "who"],
            cwd=self.world.cwd,
            env=self.env(
                session=launch["launch_id"],
                extra={"COORDINATION_CAPABILITY": cap},
            ),
            timeout=TIMEOUT,
        )
        self.assertEqual(result.exit, 0, result.stderr)
        return result.stdout


class TheRefusalTable(_Scratch):
    """One row per refusal class the contract names, addressed by id."""

    def test_writing_without_identity_refuses_identity(self):
        """Declared gap: planted faults follow the implementation; no mutants file.

        origin: 20260922T215604Z-review-codex-f6777b#3
        A writing verb whose WF_AGENT is absent, or present but not
        ``Name <address>``, refuses identity.
        """
        rows = (
            RefusalCase(id="start-without-identity", argv=self.start_argv(),
                        cls="identity", origin=ORIGIN),
            RefusalCase(id="stop-without-identity", argv=("stop",),
                        cls="identity", origin=ORIGIN),
            RefusalCase(id="attach-without-identity",
                        argv=("attach", "--member", "grunt"),
                        cls="identity", origin=ORIGIN),
            RefusalCase(id="export-without-identity",
                        argv=("export", "--to", str(self.world.tmp / "out")),
                        cls="identity", origin=ORIGIN),
            RefusalCase(id="start-bare-name", argv=self.start_argv(),
                        cls="identity", origin=origin_finding(3),
                        note="JustAName"),
            RefusalCase(id="stop-address-without-domain", argv=("stop",),
                        cls="identity", origin=origin_finding(3),
                        note="Name <noreply>"),
            RefusalCase(id="attach-name-without-brackets",
                        argv=("attach", "--member", "grunt"),
                        cls="identity", origin=origin_finding(3),
                        note="Sessions Tester noreply@example.test"),
        )

        def invoke(row):
            return self.sessions(*row.argv, identity=row.note)

        run_table(self, rows, invoke)

    def test_unknown_and_slice2_verbs_refuse_arguments(self):
        """Declared gap: planted faults follow the implementation; no mutants file.

        origin: 20260922T215604Z-review-codex-f6777b#4
        An undeclared verb and option, neither of them the examples
        frobnicate and --nope, refuse arguments. ``join`` and ``recover``
        are slice 2 verbs: ``join`` without ``--run`` and ``recover`` with
        an option it does not declare or a malformed run id refuse
        arguments naming what is wrong (sessions-lever.md slice 2).
        """
        extra_verb = "v" + hashlib.sha256(b"sessions-lever arguments").hexdigest()[:12]
        extra_option = "--" + hashlib.sha256(b"sessions-lever option").hexdigest()[:12]
        rows = (
            RefusalCase(id="unknown-verb", argv=("frobnicate",),
                        cls="arguments", origin=ORIGIN),
            RefusalCase(id="join-without-run", argv=("join",),
                        cls="arguments", origin=ORIGIN,
                        named=("--run",)),
            RefusalCase(id="recover-undeclared-option", argv=("recover", "--member", "grunt"),
                        cls="arguments", origin=ORIGIN,
                        named=("--member",)),
            RefusalCase(id="recover-malformed-run", argv=("recover", "--run", "nonsense"),
                        cls="arguments", origin=ORIGIN,
                        named=("nonsense",)),
            RefusalCase(id="unknown-option", argv=("status", "--nope"),
                        cls="arguments", origin=ORIGIN),
            RefusalCase(id="undeclared-verb", argv=(extra_verb,),
                        cls="arguments", origin=origin_finding(4),
                        named=(extra_verb,)),
            RefusalCase(id="undeclared-option", argv=("doctor", extra_option),
                        cls="arguments", origin=origin_finding(4),
                        named=(extra_option,)),
        )
        run_table(self, rows, lambda row: self.sessions(*row.argv))

    def test_malformed_team_files_refuse_team(self):
        """origin: 20260922T215604Z-review-codex-f6777b#5

        Duplicate names, empty roles, a relative executable and an
        undeclared key refuse team, with the shapes the contract already
        names.
        """
        cases = (
            ("team-unknown-harness", ORIGIN,
             a_team({"name": "co", "harness": "gemini", "roles": ["worker"]})),
            ("team-unknown-role", ORIGIN,
             a_team({"name": "co", "harness": "claude", "roles": ["emperor"]})),
            ("team-bad-name", ORIGIN,
             a_team({"name": "CO", "harness": "claude", "roles": ["worker"]})),
            ("team-empty-members", ORIGIN,
             {"schema": "agentscient-team/1",
              "run": {"name": "lane", "tmux": "agentscient"},
              "members": []}),
            ("team-duplicate-name", origin_finding(5),
             a_team(claude_member("co", ["worker"]),
                    claude_member("co", ["worker"]))),
            ("team-empty-roles", origin_finding(5),
             a_team({"name": "co", "harness": "claude", "roles": []})),
            ("team-relative-executable", origin_finding(5),
             a_team({"name": "co", "harness": "claude", "roles": ["worker"],
                     "executable": "claude"})),
            ("team-undeclared-key", origin_finding(5),
             a_team({"name": "co", "harness": "claude", "roles": ["worker"],
                     "model": "unwanted"})),
        )
        rows = []
        for case_id, origin, team in cases:
            path = self.world.plant_text(
                f"{case_id}.json", json.dumps(team) + "\n"
            )
            rows.append(
                RefusalCase(
                    id=case_id,
                    argv=("start", "--member", "co", "--team", str(path),
                          "--ready-timeout", str(READY)),
                    cls="team",
                    origin=origin,
                )
            )
        rows.append(
            RefusalCase(
                id="team-unknown-member",
                argv=self.start_argv("nosuch"),
                cls="team",
                origin=ORIGIN,
                named=("nosuch",),
            )
        )
        run_table(self, rows, lambda row: self.sessions(*row.argv))

    def test_status_on_an_absent_store_says_no_runs_and_creates_nothing(self):
        """origin: 20260922T215604Z-review-codex-f6777b#7

        A read says ``no runs`` and creates no state directory, sessions
        directory, database, or any other artifact under the state root.
        While the subject is absent the row runs a stand-in that prints
        the reading and creates those artifacts, so the watch is what
        goes red.
        """
        if TOOL.is_file():
            argv = [sys.executable, str(TOOL)]
        else:
            stand = self.world.plant_text(
                "status-creates.py",
                "import os, pathlib, sys\n"
                f"root = pathlib.Path(os.environ['XDG_STATE_HOME']) / {LANE!r}\n"
                "root.mkdir(parents=True)\n"
                "(root / 'sessions').mkdir()\n"
                "sys.stdout.write('no runs\\n')\n",
            )
            argv = [sys.executable, str(stand)]
        watch = Watch([self.xdg])
        proc = run(
            [*argv, "status"],
            cwd=self.world.cwd,
            env=self.env(identity=None),
            timeout=TIMEOUT,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertIn("no runs", proc.stdout)
        with self.subTest(id="status-creates-nothing", origin=origin_finding(7)):
            watch.assert_unchanged(self, "status created a store artifact")
            self.assertFalse(self.state.exists())
            self.assertFalse((self.state / "sessions").exists())
            self.assertFalse((self.state / "state.db").exists())

    def test_a_corrupt_store_refuses_store(self):
        store = self.state / "state.db"
        store.parent.mkdir(parents=True)
        store.write_text("not a sqlite database\n", encoding="utf-8")
        with self.subTest(id="store-corrupt", origin=ORIGIN):
            proc = self.sessions("status", identity=None)
            self.assert_refusal(proc, "store", case_id="store-corrupt")

    def test_a_worktree_that_is_not_of_this_repository_refuses_worktree(self):
        """origin: 20260922T215604Z-review-codex-f6777b#8

        A plain directory refuses worktree, and so does a git worktree
        whose common directory is another repository.
        """
        other = self.world.root / "not-a-worktree"
        other.mkdir()
        with self.subTest(id="worktree-plain-dir", origin=ORIGIN):
            proc = self.sessions(*self.start_argv("grunt", "--worktree", str(other)))
            self.assert_refusal(proc, "worktree", case_id="worktree-plain-dir")
        repo = self.world.root / "other-repo"
        repo.mkdir()
        env = self.world.env()
        run(["git", "init", "-b", "main"], cwd=repo, env=env, timeout=TIMEOUT, expect=0)
        run(["git", "commit", "--allow-empty", "-m", "other"], cwd=repo, env=env, timeout=TIMEOUT, expect=0)
        wt = self.world.root / "other-wt"
        run(["git", "worktree", "add", "--detach", str(wt), "HEAD"], cwd=repo, env=env, timeout=TIMEOUT, expect=0)
        inside = run(
            ["git", "-C", str(wt), "rev-parse", "--is-inside-work-tree"],
            cwd=self.world.cwd, env=env, timeout=TIMEOUT, expect=0,
        )
        self.assertEqual(inside.stdout.strip(), "true")
        other_common = run(
            ["git", "-C", str(wt), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=self.world.cwd, env=env, timeout=TIMEOUT, expect=0,
        )
        this_common = run(
            ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=self.world.cwd, env=env, timeout=TIMEOUT, expect=0,
        )
        self.assertNotEqual(other_common.stdout.strip(), this_common.stdout.strip())
        with self.subTest(id="worktree-other-repository", origin=origin_finding(8)):
            proc = self.sessions(*self.start_argv("grunt", "--worktree", str(wt)))
            self.assert_refusal(proc, "worktree", case_id="worktree-other-repository")

    def test_absent_run_member_and_pane_refuse_absent(self):
        rows = (
            RefusalCase(id="stop-absent-run", argv=("stop", "--run", "R-9"),
                        cls="absent", origin=ORIGIN, named=("R-9",)),
            RefusalCase(id="attach-absent-member",
                        argv=("attach", "--member", "grunt", "--run", "R-9"),
                        cls="absent", origin=ORIGIN),
            RefusalCase(id="export-absent-run",
                        argv=("export", "--run", "R-9", "--to",
                              str(self.world.tmp / "exp")),
                        cls="absent", origin=ORIGIN),
        )
        run_table(self, rows, lambda row: self.sessions(*row.argv))

    def test_doctor_when_tmux_is_absent_reports_missing_tmux(self):
        with self.subTest(id="doctor-tmux-absent", origin=ORIGIN):
            world = World(prefix="sessions-notmux-", host_path=False)
            self.addCleanup(world.close)
            conf = world.plant_text("lane.conf", f"LANE_NAME='{LANE}'\n")
            team = world.plant_text(
                "team.json",
                json.dumps(a_team(claude_member("grunt", ["worker"]))) + "\n",
            )
            argv = (
                [sys.executable, str(TOOL)]
                if TOOL.is_file()
                else [sys.executable, str(world.stage(TOOL))]
            )
            proc = run(
                [*argv, "doctor", "--team", str(team)],
                cwd=world.cwd,
                env=world.env(extra={"LANE_CONF": str(conf)}),
                timeout=TIMEOUT,
            )
            self.assertEqual(proc.exit, 1, proc.stdout + proc.stderr)
            lines = proc.stdout.splitlines()
            self.assertTrue(
                any(ln.startswith("missing tmux:") or ln.startswith("missing tmux ")
                    for ln in lines),
                proc.stdout,
            )


class TheTmuxAndLaunchRows(_Scratch):
    """Fixtures that own a private tmux socket or a launched member."""

    def test_close_stops_a_server_whose_socket_file_is_gone(self):
        """origin: docs/tests.md, Daemons; the kit's measure at a93368b5

        A started member's tmux server and the harness in its pane are
        recorded with the kit, so with the socket file removed the case's
        cleanups still stop them: close raises no refusal, and no process
        carries the world's HOME after it.
        """
        self.skip_without_tmux()
        with self.subTest(id="server-socket-file-gone", origin=ORIGIN_DAEMON):
            started = self.sessions(*self.start_argv())
            self.assertEqual(started.exit, 0, started.stderr + started.stdout)
            self.assertTrue(self.socket.exists(), "the start left no socket")
            self.socket.unlink()
            home = str(self.world.home)
            self.doCleanups()
            self.assertEqual(carrying_home(home), [])

    def session_name(self, run_id: str) -> str:
        return f"agentscient-{run_id}"

    def test_no_tmux_server_or_pane_survives_the_world(self):
        """The Lead's p1, 2026-09-23: every row that started a member left
        its private tmux server, and the harness in its pane, alive after
        the world was deleted (1,211 servers on the host). The fixture's
        own cleanups run here, and both pids must be gone after them."""
        self.skip_without_tmux()
        self.sessions(*self.start_argv())
        shown = self.host_tmux("display-message", "-p", "-t", f"={self.session_name('R-1')}:",
                               "#{pid} #{pane_pid}")
        self.assertEqual(shown.exit, 0, shown.stderr)
        server, pane = (int(pid) for pid in shown.stdout.split())
        self.doCleanups()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (Path(f"/proc/{server}").exists()
                                               or Path(f"/proc/{pane}").exists()):
            time.sleep(0.05)
        with self.subTest(id="world-close-stops-server", origin="the Lead, 2026-09-23 01:40Z"):
            self.assertFalse(Path(f"/proc/{server}").exists(),
                             f"tmux server {server} survived the world")
            self.assertFalse(Path(f"/proc/{pane}").exists(),
                             f"the pane's harness {pane} survived the world")

    def plant_owned_run(self, run_id: str, member: str) -> None:
        """An owned run while the subject is absent.

        The contract's session is ``<team.run.tmux>-<R-n>`` with
        ``@agentscient_owner`` equal to the run id and one window named
        by the member. origin: 20260922T234512Z-review-codex-5b18d7#1
        """
        session = self.session_name(run_id)
        self.start_server("new-session", "-d", "-s", session, "-n", member, "sleep", "300")
        self.host_tmux(
            "set-option", "-t", f"={session}:", "remain-on-exit", "on",
            expect=0,
        )
        self.host_tmux(
            "set-option", "-t", f"={session}:", "@agentscient_owner", run_id,
            expect=0,
        )
        self.addCleanup(lambda: self.host_tmux("kill-session", "-t", f"={session}"))

    def window_names(self, session: str) -> list[str]:
        listed = self.host_tmux(
            "list-windows", "-t", f"={session}", "-F", "#{window_name}",
        )
        self.assertEqual(listed.exit, 0, listed.stderr)
        return listed.stdout.split()

    def prove_existing_run(self, run_id: str, member: str) -> str:
        """The existing-run fixture is on the socket before any absent row."""
        session = self.session_name(run_id)
        has = self.host_tmux("has-session", "-t", f"={session}")
        self.assertEqual(
            has.exit,
            0,
            f"existing-run fixture {session} does not exist: {has.stderr}",
        )
        owner = self.host_tmux(
            "show-options", "-t", f"={session}:", "-v", "@agentscient_owner",
        )
        self.assertEqual(owner.exit, 0, owner.stderr)
        self.assertEqual(owner.stdout.strip(), run_id)
        self.assertIn(member, self.window_names(session))
        return session

    def test_a_missing_member_or_pane_of_an_existing_run_refuses_absent(self):
        """origin: 20260922T215604Z-review-codex-f6777b#6
        origin: 20260922T234512Z-review-codex-5b18d7#1

        A run that exists still refuses absent for a member it does not
        contain, and for a member whose pane is gone. The run is proven
        before either absent assertion.
        """
        self.skip_without_tmux()
        run_id = "R-1"
        member = "grunt"
        if TOOL.is_file():
            started = self.sessions(*self.start_argv(member))
            self.assertEqual(started.exit, 0, started.stderr)
        else:
            self.plant_owned_run(run_id, member)
        session = self.prove_existing_run(run_id, member)
        with self.subTest(id="absent-member-existing-run", origin=origin_finding(6)):
            self.assertNotIn("nosuch", self.window_names(session))
            proc = self.sessions("attach", "--member", "nosuch", "--run", run_id)
            refusal_wire(
                RefusalCase(
                    id="absent-member-existing-run",
                    argv=("attach", "--member", "nosuch", "--run", run_id),
                    cls="absent",
                    origin=origin_finding(6),
                    named=("nosuch",),
                ),
                proc,
            )
        with self.subTest(id="absent-pane-existing-run", origin=origin_finding(6)):
            self.drop_owned_pane(session, member)
            self.assertEqual(
                self.host_tmux("has-session", "-t", f"={session}").exit,
                0,
                "the run disappeared with its pane",
            )
            self.assertNotIn(member, self.window_names(session))
            proc = self.sessions("attach", "--member", member, "--run", run_id)
            refusal_wire(
                RefusalCase(
                    id="absent-pane-existing-run",
                    argv=("attach", "--member", member, "--run", run_id),
                    cls="absent",
                    origin=origin_finding(6),
                    named=(member,),
                ),
                proc,
            )

    def drop_owned_pane(self, session: str, member: str) -> None:
        """Remove the member's pane and leave the owned session up.

        A second window keeps the server alive: killing the only pane
        takes the run with it, and the row would then be about a missing
        run.
        """
        self.host_tmux(
            "new-window", "-d", "-t", f"={session}:", "-n", "keeper", "sleep", "300",
            expect=0,
        )
        listed = self.host_tmux(
            "list-panes", "-s", "-t", f"={session}",
            "-F", "#{window_name} #{pane_id}",
        )
        self.assertEqual(listed.exit, 0, listed.stderr)
        killed = False
        for line in listed.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == member:
                gone = self.host_tmux("kill-pane", "-t", parts[1])
                self.assertEqual(gone.exit, 0, gone.stderr)
                killed = True
        self.assertTrue(killed, f"no pane for {member}: {listed.stdout!r}")

    def test_an_unowned_session_of_the_run_name_refuses_ownership(self):
        """origin: 20260922T215604Z-review-codex-f6777b#10
        origin: 20260922T234512Z-review-codex-5b18d7#2

        An unowned session of the run's name refuses ownership. The
        session is created through the host tmux and proven present,
        and unowned, before sessions.py runs.
        """
        self.skip_without_tmux()
        with self.subTest(id="unowned-session", origin=origin_finding(10)):
            self.assertLess(
                len(os.fsencode(str(self.socket))),
                108,
                f"private tmux socket must be under 108 bytes: {self.socket}",
            )
            name = "agentscient-R-1"
            self.start_server("new-session", "-d", "-s", name, "sleep", "300")
            self.addCleanup(lambda: self.host_tmux("kill-session", "-t", f"={name}"))
            has = self.host_tmux("has-session", "-t", f"={name}")
            self.assertEqual(
                has.exit,
                0,
                f"unowned session was not created: {has.stderr}",
            )
            owner = self.host_tmux(
                "show-options", "-t", f"={name}:", "-v", "@agentscient_owner",
            )
            self.assertNotEqual(owner.stdout.strip(), "R-1")
            proc = self.sessions(*self.start_argv())
            self.assert_refusal(proc, "ownership", case_id="unowned-session")

    def test_ready_while_process_is_not_ok_refuses_readiness(self):
        """origin: 20260922T215604Z-review-codex-f6777b#9

        The planted harness exits, and its bind lands only after its pid
        has left /proc, so process is not ok whenever the member reads
        bound. start must refuse readiness rather than report ready. A
        harness that bound first and exited after raced settle() and read
        ready in 2 of 12 full runs (the XO, agentscient-0e, 2026-09-23).
        """
        self.skip_without_tmux()
        marker = self.world.root / "bind-after-exit.txt"
        self._plant_harness()
        spec_path = self.world.tools.spec_path("claude")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["bind_after_exit"] = str(marker)
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
        proc = self.sessions(*self.start_argv())
        refusal_wire(
            RefusalCase(
                id="readiness-process-not-ok",
                argv=self.start_argv(),
                cls="readiness",
                origin=origin_finding(9),
                named=("process",),
            ),
            proc,
        )
        # The bind has to land, or the row read nothing about a bound
        # member whose process is gone.
        deadline = time.monotonic() + 30
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertTrue(marker.is_file(), "the binder never reported")
        self.assertEqual(marker.read_text(encoding="utf-8"), "bound after exit 0\n")

    def test_native_id_mismatch_refuses_native(self):
        self.skip_without_tmux()
        self._plant_harness(session_id="other-native-id")
        with self.subTest(id="native-mismatch", origin=ORIGIN):
            proc = self.sessions(*self.start_argv())
            self.assert_refusal(proc, "native", case_id="native-mismatch")

    def test_role_above_worker_without_lead_capability_refuses_authority(self):
        self.skip_without_tmux()
        self.plant_team(a_team(claude_member("co", ["lead"])))
        self.bind_lead()
        with self.subTest(id="authority-no-lead-cap", origin=ORIGIN):
            proc = self.sessions(
                *self.start_argv("co"),
                session=WORKER_SESSION,
            )
            line = self.assert_refusal(proc, "authority", case_id="authority-no-lead-cap")
            self.assertIn("coordination.py grant", line)

    def test_timeout_with_no_bind_exits_1_and_status_reads_launched(self):
        self.skip_without_tmux()
        self._plant_harness(session_id="")
        with self.subTest(id="timeout-no-bind", origin=ORIGIN):
            proc = self.sessions(
                "start", "--member", "grunt", "--team", str(self.team_path),
                "--ready-timeout", "1",
            )
            self.assertEqual(proc.exit, 1, proc.stdout + proc.stderr)
            status = self.sessions("status", identity=None)
            self.assertEqual(status.exit, 0, status.stderr)
            self.assertIn("launched", status.stdout)

    def test_a_member_already_launched_refuses_place(self):
        self.skip_without_tmux()
        with self.subTest(id="already-launched", origin=ORIGIN):
            first = self.sessions(*self.start_argv())
            self.assertIn(first.exit, (0, 1), first.stderr)
            proc = self.sessions(*self.start_argv())
            self.assert_refusal(proc, "place", case_id="already-launched")

    def test_prepare_failure_refuses_profile(self):
        with self.subTest(id="profile-prepare", origin=ORIGIN):
            self.plant_team(a_team({
                "name": "grunt",
                "harness": "claude",
                "roles": ["worker"],
                "executable": "/nonexistent/claude-binary",
            }))
            proc = self.sessions(*self.start_argv())
            self.assert_refusal(proc, "profile", case_id="profile-prepare")

    def test_start_without_tmux_on_path_refuses_tmux(self):
        with self.subTest(id="start-tmux-missing", origin=ORIGIN):
            world = World(prefix="sessions-notmux-start-", host_path=False)
            self.addCleanup(world.close)
            conf = world.plant_text("lane.conf", f"LANE_NAME='{LANE}'\n")
            team = world.plant_text(
                "team.json",
                json.dumps(a_team(claude_member("grunt", ["worker"]))) + "\n",
            )
            argv = (
                [sys.executable, str(TOOL)]
                if TOOL.is_file()
                else [sys.executable, str(world.stage(TOOL))]
            )
            proc = run(
                [*argv, "start", "--member", "grunt", "--team", str(team),
                 "--ready-timeout", str(READY)],
                cwd=world.cwd,
                env=world.env(extra={"LANE_CONF": str(conf), "WF_AGENT": AGENT}),
                timeout=TIMEOUT,
            )
            case = RefusalCase(
                id="start-tmux-missing", argv=("start",), cls="tmux", origin=ORIGIN
            )
            refusal_wire(case, proc)


class TheCleanNeighbours(_Scratch):
    """Cold start, populated store, second member, attach, stop, export."""

    def check_cold_start(self, proc) -> None:
        """What a clean cold start shows: ready with role waiting on a
        lead's grant, the member a worker, and no lead in the store."""
        started = re.search(
            r"started co SM-\d+ as (PT-\d+) in R-\d+ pane %\d+ gen \d+",
            proc.stdout,
        )
        self.assertIsNotNone(started, proc.stdout)
        participant = started.group(1)
        self.assert_readiness(
            proc.stdout, role_waiting=f"grant {participant} --roles lead"
        )
        who = re.search(
            r"participant (PT-\d+) (\S+) session", self.store_who("co")
        )
        self.assertIsNotNone(who, "who printed no participant line")
        self.assertEqual(who.group(1), participant)
        self.assertEqual(who.group(2).split(","), ["worker"])
        leads = self.coord("participants", "--role", "lead")
        self.assertEqual(leads.stdout.strip(), "no participants")

    def test_cold_start_on_an_empty_store_binds_a_worker_and_no_lead(self):
        """The first native bind in an empty store is a worker; lead comes
        only from a lead's grant (sessions-lever.md at c1a69a78, A-495 b)."""
        self.skip_without_tmux()
        self.plant_team(a_team(claude_member("co", ["lead"])))
        with self.subTest(id="cold-start", origin=origin_finding(11)):
            proc = self.sessions(*self.start_argv("co"))
            self.assertEqual(proc.exit, 0, proc.stderr)
            self.check_cold_start(proc)

    def test_cold_start_catches_a_planted_lead_promotion(self):
        """The fixture row for cold-start (docs/tests.md rule 3): the first
        bound participant of an empty store is promoted to lead, and the
        cold-start checks must fail on it. The promotion is planted in the
        store after start returns, so it reaches who and participants; the
        readiness line was printed before it and is not what catches it."""
        self.skip_without_tmux()
        self.plant_team(a_team(claude_member("co", ["lead"])))
        with self.subTest(id="cold-start-planted-lead", origin=ORIGIN):
            proc = self.sessions(*self.start_argv("co"))
            self.assertEqual(proc.exit, 0, proc.stderr)
            with sqlite3.connect(self.state / "state.db") as conn:
                promoted = conn.execute(
                    "UPDATE participants SET roles = 'lead,worker' "
                    "WHERE id = (SELECT MIN(id) FROM participants)"
                ).rowcount
            self.assertEqual(promoted, 1, "the planted promotion did not land")
            with self.assertRaises(AssertionError):
                self.check_cold_start(proc)

    def test_start_on_a_populated_store_keeps_the_lead_and_binds_worker(self):
        self.skip_without_tmux()
        self.bind_lead()
        with self.subTest(id="populated-store", origin=ORIGIN):
            proc = self.sessions(*self.start_argv("grunt"))
            self.assertEqual(proc.exit, 0, proc.stderr)
            self.assertRegex(
                proc.stdout,
                r"started grunt SM-\d+ as PT-\d+ in R-\d+",
            )
            who = self.store_who("grunt")
            self.assertIn("worker", who)
            lead = self.coord("who", session=LEAD_SESSION)
            self.assertIn("lead", lead.stdout)

    def identity_oks(self, text: str) -> list[str]:
        """Session ids from ``identity ok <id>`` lines."""
        found = []
        for line in text.splitlines():
            match = re.search(r"\bidentity\s*:?\s*ok\s+(\S+)", line)
            if match:
                found.append(match.group(1))
        return found

    def launch_ids(self, run: str) -> list[str]:
        """Launch identities recorded under the run, one per member."""
        root = self.state / "sessions" / run
        found = []
        if not root.is_dir():
            return found
        for path in sorted(root.glob("*/launch.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            for key in ("launch_id", "launch-id", "launch id"):
                value = data.get(key)
                if value:
                    found.append(str(value))
                    break
        return found

    def test_a_second_member_of_the_same_harness_has_distinct_profile_identity_pane(self):
        """origin: 20260922T215604Z-review-codex-f6777b#12

        The second member's launch identity and native identity differ
        from the first member's.
        """
        self.skip_without_tmux()
        self.plant_team(a_team(
            claude_member("co", ["worker"]),
            claude_member("xo", ["worker"]),
        ))
        with self.subTest(id="second-member", origin=origin_finding(12)):
            first = self.sessions(*self.start_argv("co"))
            self.assertEqual(first.exit, 0, first.stderr)
            second = self.sessions(*self.start_argv("xo", "--run", "R-1"))
            self.assertEqual(second.exit, 0, second.stderr)
            first_native = self.identity_oks(first.stdout)
            second_native = self.identity_oks(second.stdout)
            self.assertEqual(len(first_native), 1, f"first identity: {first.stdout!r}")
            self.assertEqual(len(second_native), 1, f"second identity: {second.stdout!r}")
            self.assertNotEqual(first_native[0], second_native[0])
            launches = self.launch_ids("R-1")
            self.assertEqual(len(launches), 2, f"launch identities: {launches!r}")
            self.assertNotEqual(launches[0], launches[1])
            status = self.sessions("status", "--run", "R-1", identity=None)
            self.assertIn("co", status.stdout)
            self.assertIn("xo", status.stdout)
            profiles = list((self.state / "sessions").glob("R-1/*/profile"))
            self.assertGreaterEqual(len(profiles), 2)
            self.assertEqual(len({p.resolve() for p in profiles}), len(profiles))
            panes = [
                ln for ln in status.stdout.splitlines()
                if "pane" in ln and ("co" in ln or "xo" in ln)
            ]
            self.assertGreaterEqual(len(panes), 2)
            self.assertNotEqual(panes[0], panes[1])

    def test_attach_execs_tmux_on_the_owned_window(self):
        """origin: 20260922T215604Z-review-codex-f6777b#13
        origin: 20260922T234512Z-review-codex-5b18d7#3

        attach-session uses the run's socket, the run's session name,
        and the member's window, each as a whole field.
        """
        self.skip_without_tmux()
        with self.subTest(id="attach", origin=origin_finding(13)):
            started = self.sessions(*self.start_argv())
            self.assertEqual(started.exit, 0, started.stderr)
            proc = self.sessions("attach", "--member", "grunt")
            self.assertEqual(proc.exit, 0, proc.stderr)
            run_id = "R-1"
            match = re.search(r"\b(R-\d+)\b", started.stdout)
            if match:
                run_id = match.group(1)
            session = f"agentscient-{run_id}"
            window = "grunt"
            log = self.world.root / "fakes" / "tmux.calls.jsonl"
            calls = []
            if log.is_file():
                calls = [
                    json.loads(line)["argv"]
                    for line in log.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            def exact(argv: list) -> bool:
                """Socket, session and window as whole fields, never substrings.

                agentscient-R-10 does not match agentscient-R-1, and grunt2
                does not match grunt.
                """
                if "attach-session" not in argv and argv[:1] != ["attach"]:
                    return False

                def flag(name: str):
                    if name in argv:
                        index = argv.index(name)
                        if index + 1 < len(argv):
                            return argv[index + 1]
                    prefix = name + "="
                    for part in argv:
                        if part.startswith(prefix):
                            return part[len(prefix):]
                    return None

                socket = str(self.socket)
                if flag("-S") != socket and flag("--socket-path") != socket:
                    return False
                target = flag("-t") or flag("--target")
                if not target:
                    return False
                target = target.removeprefix("=")
                if ":" not in target:
                    return False
                sess, rest = target.split(":", 1)
                win = rest.split(".", 1)[0]
                return sess == session and win == window

            decoy = [
                "-S", str(self.socket), "attach-session", "-t",
                "agentscient-R-10:grunt2",
            ]
            self.assertFalse(exact(decoy), "a substring target must not match")
            matched = [argv for argv in calls if exact(argv)]
            self.assertTrue(
                matched,
                f"attach-session exactly on {self.socket} at {session} window {window}: {calls}",
            )

    def owned_pid_and_window(self, member: str, started: str):
        pid = None
        for path in (self.state / "sessions").glob(f"*/{member}/launch.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            raw = data.get("pid") if isinstance(data, dict) else None
            if isinstance(raw, int) and raw > 1:
                pid = raw
            elif isinstance(raw, str) and raw.isdigit() and int(raw) > 1:
                pid = int(raw)
        if pid is None:
            status = self.sessions("status", identity=None).stdout
            match = re.search(r"\bpid=(\d+)\b", status)
            if match and int(match.group(1)) > 1:
                pid = int(match.group(1))
        run_id = "R-1"
        named = re.search(r"\b(R-\d+)\b", started)
        if named:
            run_id = named.group(1)
        return pid, f"agentscient-{run_id}", member

    def window_present(self, session: str, window: str) -> bool:
        listed = run(
            ["tmux", "-S", str(self.socket), "list-windows", "-t", session,
             "-F", "#{window_name}"],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=TIMEOUT,
        )
        return listed.exit == 0 and window in listed.stdout.split()

    def test_a_clean_stop_terminates_the_owned_pid(self):
        """origin: 20260922T215604Z-review-codex-f6777b#14

        stop --terminate signals the owned pid and removes the owned
        window. Printing stopped while either is still alive fails.
        """
        self.skip_without_tmux()
        with self.subTest(id="clean-stop", origin=origin_finding(14)):
            started = self.sessions(*self.start_argv())
            self.assertEqual(started.exit, 0, started.stderr)
            pid, session, window = self.owned_pid_and_window("grunt", started.stdout)
            self.assertIsNotNone(pid, "stop --terminate recorded no owned pid")
            self.assertTrue(self._alive(pid), "recorded pid was already dead")
            self.assertTrue(
                self.window_present(session, window),
                f"owned window {session}:{window} was absent before stop",
            )
            proc = self.sessions("stop", "--terminate")
            self.assertEqual(proc.exit, 0, proc.stderr)
            self.assertRegex(proc.stdout, r"stopped \S+")
            self.assertFalse(self._alive(pid), "owned pid still alive after stop --terminate")
            self.assertFalse(
                self.window_present(session, window),
                "owned window still alive after stop --terminate",
            )
            status = self.sessions("status", identity=None)
            self.assertRegex(status.stdout, r"\bstopped\b")

    def _plant_export_canaries(self, member: str) -> list[str]:
        """origin: 20260922T215604Z-review-codex-f6777b#15

        The binding's coordination_capability and every
        credential_environment value, with a canary planted before export.
        """
        bindings = list((self.state / "sessions").glob(f"*/{member}/profile/binding.json"))
        if not bindings:
            return []
        binding = json.loads(bindings[0].read_text(encoding="utf-8"))
        if not isinstance(binding, dict):
            return []
        capability = binding.get("coordination_capability")
        if not isinstance(capability, str) or not capability:
            capability = "capability-canary-a8b48e-not-for-export"
            binding["coordination_capability"] = capability
        raw = binding.get("credential_environment")
        if not isinstance(raw, dict):
            raw = {}
        raw["TEST_CREDENTIAL"] = "credential-canary-a8b48e-not-for-export"
        binding["credential_environment"] = raw
        bindings[0].write_text(json.dumps(binding), encoding="utf-8")
        values = [capability]
        for value in raw.values():
            if value is None:
                continue
            rendered = str(value)
            if rendered:
                values.append(rendered)
        return values

    def export_keys(self, dest: Path) -> set[str]:
        """Every JSON key written under an export directory."""
        found: set[str] = set()

        def walk(node) -> None:
            if isinstance(node, dict):
                found.update(str(key) for key in node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for path in dest.rglob("*"):
            if not path.is_file():
                continue
            try:
                walk(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError, ValueError):
                continue
        return found

    def broken_export_keeps_the_capability_key(self, dest: Path):
        """A broken exporter while sessions.py is absent.

        The evidence files are present and the secret values are omitted,
        and the capability key is still written.
        origin: 20260922T234512Z-review-codex-5b18d7#5
        """
        script = self.world.plant_text(
            "broken-export.py",
            "import json, pathlib, sys\n"
            f"dest = pathlib.Path({str(dest)!r})\n"
            "member = dest / 'grunt'\n"
            "member.mkdir(parents=True)\n"
            "(member / 'pane.log').write_text('capture\\n', encoding='utf-8')\n"
            "(member / 'launch.json').write_text('{}\\n', encoding='utf-8')\n"
            "(member / 'adapter.log').write_text('diag\\n', encoding='utf-8')\n"
            "(member / 'diagnostics.json').write_text("
            "json.dumps(['adapter.log']) + '\\n', encoding='utf-8')\n"
            "(dest / 'run.json').write_text("
            "json.dumps({'coordination_capability': '[REDACTED]'}) + '\\n',"
            " encoding='utf-8')\n"
            "files = sum(1 for item in dest.rglob('*') if item.is_file())\n"
            "sys.stdout.write(f'exported {dest} (1 members, {files} files)\\n')\n",
        )
        return run(
            [sys.executable, str(script)],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=TIMEOUT,
        )

    def test_a_full_export_omits_capability_and_credential_values(self):
        """origin: 20260922T215604Z-review-codex-f6777b#15
        origin: 20260922T234512Z-review-codex-5b18d7#5

        The export omits the coordination_capability key itself, not only
        the capability and credential values.
        """
        self.skip_without_tmux()
        dest = self.world.root / "export"
        with self.subTest(id="full-export", origin=origin_finding(15)):
            started = self.sessions(*self.start_argv())
            self.assertEqual(started.exit, 0, started.stderr)
            if not TOOL.is_file():
                bindings = list(
                    (self.state / "sessions").glob("*/grunt/profile/binding.json")
                )
                if not bindings:
                    planted = (
                        self.state / "sessions" / "R-1" / "grunt"
                        / "profile" / "binding.json"
                    )
                    planted.parent.mkdir(parents=True)
                    planted.write_text("{}\n", encoding="utf-8")
            secrets = self._plant_export_canaries("grunt")
            if TOOL.is_file():
                proc = self.sessions("export", "--to", str(dest))
            else:
                proc = self.broken_export_keeps_the_capability_key(dest)
            self.assertEqual(proc.exit, 0, proc.stderr)
            self.assertTrue(
                any(item.is_file() for item in dest.rglob("pane.log")),
                "pane capture is present",
            )
            self.assertTrue(
                any(item.is_file() for item in dest.rglob("launch.json")),
                "launch.json is present",
            )
            diags = [item for item in dest.rglob("diagnostics.json") if item.is_file()]
            self.assertTrue(diags, "adapter diagnostics are present")
            for diag in diags:
                listed = json.loads(diag.read_text(encoding="utf-8"))
                self.assertIsInstance(listed, list)
                self.assertTrue(listed, f"adapter diagnostics empty: {diag}")
                for item in listed:
                    name = Path(str(item)).name
                    self.assertTrue(
                        any(found.is_file() and found.name == name for found in dest.rglob(name)),
                        f"retained diagnostic missing from export: {item}",
                    )
            self.assertRegex(proc.stdout, r"exported .+ \(\d+ members, \d+ files\)")
            self.assertTrue((dest / "run.json").is_file(), "run.json is present")
            self.assertTrue(
                secrets,
                "coordination_capability and credential_environment values are known",
            )
            pieces = []
            for exported in dest.rglob("*"):
                pieces.append(str(exported))
                if exported.is_file():
                    pieces.append(exported.read_text(encoding="utf-8", errors="replace"))
            blob = "\n".join(pieces)
            for secret in secrets:
                self.assertNotIn(secret, blob)
            self.assertNotIn(
                "coordination_capability",
                self.export_keys(dest),
                "the capability key is present in the export",
            )

    def test_unreadable_diagnostics_on_export_exits_1_and_names_the_failure(self):
        """origin: 20260922T215604Z-review-codex-f6777b#16

        The pane capture is unreadable before export. The first failure
        is kept and named, and the export exits 1.
        """
        self.skip_without_tmux()
        dest = self.world.root / "export-partial"
        with self.subTest(id="export-unreadable-diagnostics", origin=origin_finding(16)):
            started = self.sessions(*self.start_argv())
            self.assertEqual(started.exit, 0, started.stderr)
            targets = list((self.state / "sessions").glob("*/grunt/pane.log"))
            if not targets:
                planted = self.state / "sessions" / "R-1" / "grunt" / "pane.log"
                planted.parent.mkdir(parents=True, exist_ok=True)
                planted.write_text("capture\n", encoding="utf-8")
                targets = [planted]
            target = targets[0]
            target.chmod(0)
            unreadable = False
            try:
                target.read_bytes()
            except OSError:
                unreadable = True
            self.assertTrue(unreadable, f"{target} stayed readable after chmod 0")
            try:
                proc = self.sessions("export", "--to", str(dest))
            finally:
                target.chmod(0o644)
            combined = proc.stdout + proc.stderr
            self.assertIn(str(target), combined, f"the first failure was not named: {combined!r}")
            self.assertEqual(proc.exit, 1, combined)

    def proc_start_ticks(self, pid: int) -> str:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return text.rsplit(")", 1)[1].split()[19]

    def test_stop_terminate_does_not_signal_a_stale_pid(self):
        """origin: 20260922T215604Z-review-codex-f6777b#17
        origin: 20260923T003033Z-review-codex-c99cc9#8

        A reused pid reads as a live pid whose start ticks differ from the
        record. The row plants that record: the member's pid becomes a live
        holder the world owns, its start ticks one more than the holder's.
        stop --terminate must leave the holder alive, say unresolved stale
        pid, and exit 1.
        """
        self.skip_without_tmux()
        with self.subTest(id="stale-pid-stop", origin=origin_finding(17)):
            started = self.sessions(*self.start_argv())
            self.assertEqual(started.exit, 0, started.stderr)
            holder, release = self.owned_holder()
            self.addCleanup(release)
            stale = str(int(self.proc_start_ticks(holder)) + 1)
            with sqlite3.connect(self.state / "state.db") as conn:
                planted = conn.execute(
                    "UPDATE session_member SET pid = ?, start_ticks = ? WHERE name = ?",
                    (holder, stale, "grunt"),
                ).rowcount
            self.assertEqual(planted, 1, "the stale record was not planted")
            proc = self.sessions("stop", "--terminate")
            self.assertTrue(self._alive(holder), "the stale pid was signalled")
            combined = proc.stdout + proc.stderr
            self.assertIn(f"unresolved stale pid {holder}", combined)
            self.assertEqual(proc.exit, 1, combined)

def ticks_of(pid: int) -> str:
    """Field 22 of /proc/<pid>/stat, the process's start ticks."""
    text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return text.rsplit(")", 1)[1].split()[19]


class TheSupervisorBoundaries(_Scratch):
    """Rows for A-481 findings #1, #4, #5 and #6, from the contract clauses
    each cites (sessions-lever.md at c1a69a78). Each reaches the subject
    through a record or a tmux option the world plants."""

    def plant_pid(self, member: str, pid: int, ticks: str | None) -> None:
        with sqlite3.connect(self.state / "state.db") as conn:
            changed = conn.execute(
                "UPDATE session_member SET pid = ?, start_ticks = ? WHERE name = ?",
                (pid, ticks, member),
            ).rowcount
        self.assertEqual(changed, 1, f"the planted {member} record did not land")

    def member_count(self) -> int:
        with sqlite3.connect(self.state / "state.db") as conn:
            return conn.execute("SELECT COUNT(*) FROM session_member").fetchone()[0]

    def started(self) -> None:
        proc = self.sessions(*self.start_argv())
        self.assertEqual(proc.exit, 0, proc.stderr)

    # -- #1: stop never acts inside a session another run owns ------------

    def run_session(self, run_id: str) -> str:
        """The contract's session name, ``<team.run.tmux>-<R-n>``."""
        return f"agentscient-{run_id}"

    def windows(self, session: str) -> list[str]:
        listed = self.host_tmux("list-windows", "-t", f"={session}", "-F", "#{window_name}")
        self.assertEqual(listed.exit, 0, listed.stderr)
        return listed.stdout.split()

    def stop_on_a_foreign_session(self, case_id: str, owner: str | None) -> None:
        self.skip_without_tmux()
        self.started()
        holder, release = self.owned_holder()
        self.addCleanup(release)
        self.plant_pid("grunt", holder, ticks_of(holder))
        session = self.run_session("R-1")
        if owner is None:
            self.host_tmux("set-option", "-u", "-t", f"={session}:",
                           "@agentscient_owner", expect=0)
        else:
            self.host_tmux("set-option", "-t", f"={session}:",
                           "@agentscient_owner", owner, expect=0)
        proc = self.sessions("stop", "--terminate")
        refusal_wire(
            RefusalCase(id=case_id, argv=("stop", "--terminate"), cls="ownership",
                        origin=f"{REVIEW}#1"),
            proc,
        )
        self.assertTrue(self._alive(holder), "a member of a foreign session was signalled")
        self.assertIn("grunt", self.windows(session))

    def test_stop_in_a_session_owned_by_another_run_refuses_ownership(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#1

        sessions-lever.md: a session of the run's name with another run's
        id refuses ownership, and the supervisor never kills it."""
        with self.subTest(id="stop-foreign-owner", origin=f"{REVIEW}#1"):
            self.stop_on_a_foreign_session("stop-foreign-owner", "R-99")

    def test_stop_in_a_session_without_the_owner_option_refuses_ownership(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#1"""
        with self.subTest(id="stop-no-owner", origin=f"{REVIEW}#1"):
            self.stop_on_a_foreign_session("stop-no-owner", None)

    # -- #4: liveness is pid plus start ticks, never pid alone -------------

    def process_states(self, text: str) -> list[str]:
        return re.findall(r"(?m)^\s*process\s*:?\s*(ok|waiting|refused|gone)\b", text)

    def test_a_record_without_start_ticks_never_reads_process_ok(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#4

        A gone pid and no recorded ticks compare equal (None == None); the
        readiness must still not read ok."""
        self.skip_without_tmux()
        with self.subTest(id="no-ticks-readiness", origin=f"{REVIEW}#4"):
            self.started()
            holder, release = self.owned_holder()
            release()
            self.assertFalse(self._alive(holder), "the holder was not reaped")
            self.plant_pid("grunt", holder, None)
            status = self.sessions("status", identity=None)
            self.assertEqual(status.exit, 0, status.stderr)
            states = self.process_states(status.stdout)
            self.assertTrue(states, f"no process reading: {status.stdout!r}")
            self.assertNotIn("ok", states, status.stdout)
            self.assertNotIn("start=None", status.stdout)

    def test_stop_never_signals_a_pid_without_recorded_ticks(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#4"""
        self.skip_without_tmux()
        with self.subTest(id="no-ticks-stop", origin=f"{REVIEW}#4"):
            self.started()
            holder, release = self.owned_holder()
            self.addCleanup(release)
            self.plant_pid("grunt", holder, None)
            proc = self.sessions("stop", "--terminate")
            combined = proc.stdout + proc.stderr
            self.assertTrue(self._alive(holder), "a pid with no recorded ticks was signalled")
            self.assertRegex(combined, r"grunt SM-\d+ unresolved", combined)
            self.assertEqual(proc.exit, 1, combined)

    # -- #5: a run is one started team --------------------------------------

    def second_team(self) -> dict:
        return a_team(claude_member("grunt", ["worker"]), claude_member("second", ["worker"]))

    def start_second(self, team: Path):
        return self.sessions("start", "--member", "second", "--run", "R-1",
                             "--team", str(team), "--ready-timeout", str(READY))

    def test_start_into_a_run_with_another_team_file_refuses_team(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#5"""
        self.skip_without_tmux()
        with self.subTest(id="run-other-team-file", origin=f"{REVIEW}#5"):
            self.started()
            other = self.world.plant_text("team-b.json", json.dumps(self.second_team()) + "\n")
            proc = self.start_second(other)
            refusal_wire(
                RefusalCase(id="run-other-team-file", argv=("start",), cls="team",
                            origin=f"{REVIEW}#5"),
                proc,
            )
            self.assertEqual(self.member_count(), 1)

    def test_start_into_a_run_whose_team_file_changed_refuses_team(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#5"""
        self.skip_without_tmux()
        with self.subTest(id="run-changed-team-file", origin=f"{REVIEW}#5"):
            self.started()
            changed = json.dumps(self.second_team()) + "\n"
            self.team_path.write_text(changed, encoding="utf-8")
            self.assertEqual(self.team_path.read_text(encoding="utf-8"), changed)
            proc = self.start_second(self.team_path)
            refusal_wire(
                RefusalCase(id="run-changed-team-file", argv=("start",), cls="team",
                            origin=f"{REVIEW}#5"),
                proc,
            )
            self.assertEqual(self.member_count(), 1)

    # -- #6: the supervisor goes through the adapter boundary ---------------

    def staged_tool(self) -> tuple[Path, Path]:
        """A copy of sessions.py in a mirror of the repository whose
        session_adapters/claude.py is the logging wrapper. Every other
        entry is a symlink to the real one."""
        root = self.world.root / "repo"
        log = self.world.root / "adapter.calls.jsonl"

        def mirror(src: Path, dst: Path, skip: set[str]) -> None:
            dst.mkdir(parents=True)
            for entry in src.iterdir():
                if entry.name not in skip and entry.name != "__pycache__":
                    (dst / entry.name).symlink_to(entry)

        tools = root / "ops" / "conductor" / "tools"
        mirror(REPO, root, {"ops"})
        mirror(REPO / "ops", root / "ops", {"conductor"})
        mirror(REPO / "ops" / "conductor", root / "ops" / "conductor", {"tools"})
        mirror(TOOL.parent, tools, {"sessions.py", "session_adapters"})
        shutil.copy2(TOOL, tools / "sessions.py")
        adapters = tools / "session_adapters"
        mirror(TOOL.parent / "session_adapters", adapters, {"claude.py"})
        text = ADAPTER_SRC.read_text(encoding="utf-8")
        for anchor, value in (("@@REAL@@", str(TOOL.parent / "session_adapters" / "claude.py")),
                              ("@@LOG@@", str(log))):
            if text.count(anchor) != 1:
                raise AssertionError(f"INVALID: {ADAPTER_SRC.name} anchor {anchor}")
            text = text.replace(anchor, value)
        (adapters / "claude.py").write_text(text, encoding="utf-8")
        return tools / "sessions.py", log

    def calls(self, log: Path, method: str) -> list[dict]:
        if not log.is_file():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row.get("method") == method]

    def test_readiness_observes_identity_through_the_adapter(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#6

        sessions-lever.md, the adapter boundary: observe_identity(store,
        launch_id) is a method the supervisor calls."""
        self.skip_without_tmux()
        with self.subTest(id="adapter-observe-identity", origin=f"{REVIEW}#6"):
            tool, log = self.staged_tool()
            proc = run([sys.executable, str(tool), *self.start_argv()],
                       cwd=self.world.cwd, env=self.env(), timeout=TIMEOUT)
            self.own_server()
            self.assertEqual(proc.exit, 0, proc.stderr)
            launches = list((self.state / "sessions").glob("*/grunt/launch.json"))
            self.assertEqual(len(launches), 1, launches)
            launch_id = json.loads(launches[0].read_text(encoding="utf-8"))["launch_id"]
            observed = self.calls(log, "observe_identity")
            self.assertTrue(observed, "readiness did not call observe_identity")
            self.assertIn(launch_id, [row["launch_id"] for row in observed])

    def test_doctor_probes_through_the_adapter(self):
        """origin: 20260923T003033Z-review-codex-c99cc9#6

        sessions-lever.md, the adapter boundary: probe(executable) is a
        method the supervisor calls."""
        with self.subTest(id="adapter-probe", origin=f"{REVIEW}#6"):
            tool, log = self.staged_tool()
            proc = run([sys.executable, str(tool), "doctor", "--team", str(self.team_path)],
                       cwd=self.world.cwd, env=self.env(), timeout=TIMEOUT)
            self.assertIn(proc.exit, (0, 1), proc.stderr)
            probed = self.calls(log, "probe")
            self.assertTrue(probed, f"doctor did not call probe: {proc.stdout!r}")
            self.assertEqual(Path(probed[0]["executable"]).name, "claude")
            self.assertIn("session-harness 0.0.0-fake", proc.stdout)


class TheDoctorDefaultTeam(_Scratch):
    def test_doctor_reads_the_codex_and_grok_adapters(self):
        """origin: sessions-lever.md slice 2

        Slice 2 lands the codex and grok adapters: ``doctor`` reads each
        protocol member's planted executable and its adapter ``ok`` and
        exits 0 (slice 1's ``missing adapter`` reading is withdrawn).
        """
        with self.subTest(id="doctor-protocol-adapters", origin=ORIGIN):
            for name, source in (("codex", CODEX_SRC), ("grok", GROK_SRC)):
                self.world.tools.plant(name, script=_fake_text(source))
            team = a_team(
                claude_member("co", ["lead"]),
                {"name": "dispatcher", "harness": "codex", "roles": ["dispatcher"]},
                {"name": "grunt", "harness": "grok", "roles": ["worker"]},
            )
            path = self.plant_team(team)
            proc = self.sessions("doctor", "--team", str(path), identity=None)
            self.assertEqual(proc.exit, 0, proc.stdout)
            text = proc.stdout
            for member, harness in (("dispatcher", "codex"), ("grunt", "grok")):
                self.assertIn(
                    f"ok adapter {member} ({harness}): "
                    f"ops/conductor/tools/session_adapters/{harness}.py", text,
                )
                self.assertIn(
                    f"ok executable {member} ({harness}): {self.world.bin / harness}", text,
                )
            self.assertNotIn("missing adapter", text)


if __name__ == "__main__":
    unittest.main()
