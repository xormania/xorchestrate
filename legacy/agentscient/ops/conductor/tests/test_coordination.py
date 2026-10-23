"""The coordination core: participants, assignments, messages, evidence.

Contract: `ops/conductor/coordination-lever.md`. The tool
`ops/conductor/tools/coordination.py` is the subject and is run as a
subprocess under a scratch XDG_STATE_HOME, a staged lane conf, and the
real deliverable lever for the lock a claim gates on. Each session is
given its own CLAUDE_CODE_SESSION_ID, because the defect this module
exists to close is that the engine's holder is the agent string: the
rows below bind three participants that share one WF_AGENT value and
prove they are three.

Written by hand under grant 258 (owner, 2026-09-16: "hand grant ok'd",
relayed by the Lead session agentscient-11), one producer (Claude Opus
5, 1M context), the second producer absent by the owner's word
(AGENTS.md §Ground rules). The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_coordination.py`:
red while `coordination.py` is absent, read again after it is written;
both readings are quoted in the commit that carries it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

from ops.testkit import RefusalCase, World, refusal_wire, run, run_table

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "coordination.py"
LOCK_LEVER = TOOLS / "deliverable.py"

# One WF_AGENT value for every session on the host, which is the state
# .claude/settings.local.json puts a host in (LD-50, instrument.py:93-99).
SHARED = "Claude Opus 5 (1M context) <noreply@example.test>"
LEAD = "sess-lead"
WORKER = "sess-worker"
CHECKER = "sess-checker"
BRANCH = "lane/session-20260916-x"


class _Scratch(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="coordination-")
        self.addCleanup(self.world.close)
        self.xdg = self.world.xdg_state
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")
        self.store = self.xdg / "testlane" / "state.db"

    def env(self, session: str | None, identity: str | None = SHARED) -> dict:
        env = self.world.env(extra={"LANE_CONF": str(self.conf)})
        if identity:
            env["WF_AGENT"] = identity
        if session:
            env["CLAUDE_CODE_SESSION_ID"] = session
            env["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        return env

    def run_tool(self, *argv, session=LEAD, identity=SHARED, tool=None):
        if argv and argv[0] == "bind-for" and "--session" in argv and "--capability-digest" not in argv:
            target = argv[argv.index("--session") + 1]
            capability = self.env(target)["COORDINATION_CAPABILITY"]
            argv = (*argv, "--capability-digest", hashlib.sha256(capability.encode()).hexdigest())
        return run(
            [sys.executable, str(tool or TOOL), *argv],
            cwd=self.world.cwd, env=self.env(session, identity), timeout=60,
        )

    def ok(self, *argv, session=LEAD, identity=SHARED) -> str:
        proc = self.run_tool(*argv, session=session, identity=identity)
        self.assertEqual(proc.exit, 0, f"{argv}: {proc.stderr}")
        return proc.stdout

    def lock(self, branch=BRANCH):
        """The real lever writes the lock a claim reads."""
        proc = run(
            [sys.executable, str(LOCK_LEVER), "add", branch, "--why", "the trial"],
            cwd=self.world.cwd, env=self.env(LEAD), timeout=60,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)

    def gen(self, aid) -> str:
        """The generation the assignment stands at, read and not assumed.

        Every move bumps it, so a row that means "now" asks. A row that
        means a generation the assignment has moved past captures this
        before the move that stales it."""
        import re
        shown = self.ok("show", aid, session=LEAD)
        found = re.search(r"generation (\d+)", shown)
        self.assertIsNotNone(found, f"show printed no generation: {shown!r}")
        return found.group(1)

    def core(self, session_id=LEAD):
        """The module, imported fresh against this world's state root."""
        import importlib.util
        env = self.env(session_id)
        patch = unittest.mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        spec = importlib.util.spec_from_file_location("coordination_under_test", TOOL)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    # -- fixtures ----------------------------------------------------------

    def bind_three(self):
        """Lead, worker and checker: one agent string, three sessions.

        Idempotent by looking, because several rows compose it with a
        fixture that binds them itself: a second self-bind naming roles
        the row has since been granted is a refusal, and that refusal is
        the chain working, not a defect to reach around.
        """
        if "PT-3" in self.ok("participants", session=LEAD):
            return
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        self.ok("bind", "--roles", "worker", session=CHECKER)
        self.ok("grant", "PT-3", "--roles", "verifier", session=LEAD)

    def offered(self, to="PT-2"):
        self.bind_three()
        out = self.ok("draft", "a bounded repair", "--outcome", "the row refuses",
                      "--revision", "a1b2c3d", session=LEAD)
        aid = out.split()[-1]
        self.ok("offer", aid, "--to", to, "--generation", self.gen(aid),
                session=LEAD)
        return aid

    def held(self):
        self.lock()
        aid = self.offered()
        self.ok("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid), session=WORKER)
        return aid

    def submitted(self):
        aid = self.held()
        self.ok("observe", aid, "--observation", "the suite ran",
                "--revision", "a1b2c3d", "--generation", self.gen(aid),
                "--execution", "20260916T010203Z-job-x", session=WORKER)
        self.ok("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session=WORKER)
        return aid


class OwnerTerminalBind(_Scratch):
    """LD-125 and A-548: mint, enrollment and rollback through the real core."""

    def setUp(self):
        super().setUp()
        self.module = self.core()
        import importlib.util
        spec = importlib.util.spec_from_file_location("terminal_sessions", TOOLS / "sessions.py")
        sessions = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sessions)
        with self.module.writer() as conn:
            conn.executescript(sessions.SCHEMA)
            conn.execute("INSERT INTO session_run(id,subject,name,team_file,team_sha256,"
                         "tmux_session,started_by,started_at) VALUES (3,1,'test','team','sha',"
                         "'test',?,?)", (SHARED, self.module.stamp()))
        os.environ.pop("COORDINATION_CAPABILITY", None)

    def call(self, *args, extra=None):
        env = self.env(LEAD)
        env.pop("COORDINATION_CAPABILITY")
        env.update(extra or {})
        return run([sys.executable, str(TOOL), *args], cwd=self.world.cwd, env=env, timeout=60)

    def minted(self):
        result = self.call("bind", "--run", "R-3", "--roles", "lead")
        self.assertEqual(result.exit, 0, result.stderr)
        path = self.xdg / "testlane/runs/R-3/terminals/PT-1/home/profile.json"
        self.assertIn(f"profile {path}\n", result.stdout)
        token = json.loads(path.read_text())["coordination_capability"]
        self.assertNotIn(token, result.stdout + result.stderr)
        return path, token

    def test_plain_terminal_can_authenticate_and_rebind_without_leaking_its_token(self):
        path, token = self.minted()
        drafted = self.call("draft", "bounded work", "--outcome", "done", "--profile", str(path))
        self.assertEqual(drafted.exit, 0, drafted.stderr)
        rebound = self.call("bind", "--roles", "lead", "--run", "R-3", "--profile", str(path))
        self.assertEqual(rebound.exit, 0, rebound.stderr)
        with sqlite3.connect(self.store) as conn:
            row = conn.execute("SELECT terminal_run, capability_digest FROM participants").fetchone()
            self.assertEqual(row, (3, hashlib.sha256(token.encode()).hexdigest()))
            self.assertNotIn(token, "\n".join(conn.iterdump()))
        self.assertEqual(json.loads(path.read_text())["coordination_capability"], token)

    def test_a_fabricated_pointer_for_another_run_or_participant_refuses(self):
        path, _ = self.minted()
        body = json.loads(path.read_text())
        for run_id, terminal in (("R-4", "PT-1"), ("R-3", "PT-2")):
            with self.subTest(run=run_id, terminal=terminal):
                target = self.xdg / "testlane/runs" / run_id / "terminals" / terminal / "home/profile.json"
                target.parent.mkdir(parents=True)
                target.write_text(json.dumps({**body, "run": run_id, "terminal": terminal}))
                target.chmod(0o600)
                refused = self.call("draft", "forged", "--outcome", "must refuse", "--profile", str(target))
                self.assertEqual(refused.exit, 2, refused.stdout + refused.stderr)
                self.assertIn("malformed enrollment", refused.stderr)

    def test_missing_run_and_malformed_credential_refuse_without_enrollment(self):
        for run_id, extra, reason in (("R-9", {}, "absent:"),
                                      ("R-3", {"COORDINATION_CAPABILITY": "bad"}, "malformed capability")):
            with self.subTest(run=run_id):
                result = self.call("bind", "--roles", "lead", "--run", run_id, extra=extra)
                self.assertEqual(result.exit, 2, result.stderr)
                self.assertIn(reason, result.stderr)
        with sqlite3.connect(self.store) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 0)

    def test_failed_pointer_write_or_commit_leaves_no_enrollment_or_pointer(self):
        core = self.module
        path = self.xdg / "testlane/runs/R-3/terminals/PT-1/home/profile.json"

        class FailedCommit:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def commit(self):
                raise OSError("planted commit failure")

        with core.writer() as conn:
            with unittest.mock.patch.object(core.instrument, "mint_terminal_profile", side_effect=OSError("write failure")):
                with self.assertRaises(OSError):
                    core.core_bind(conn, None, agent=SHARED, roles="lead", run="R-3")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 0)
            with self.assertRaises(OSError):
                core.core_bind(FailedCommit(conn), None, agent=SHARED, roles="lead", run="R-3")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 0)
        self.assertFalse(path.exists())
        self.minted()


class CapabilityAuthentication(_Scratch):
    """A387: public session correlation cannot substitute for caller custody.

    Independent core regressions by PT18; core implementation belongs to PT16.
    All capabilities and stores here are synthetic and owned by World.
    """

    def setUp(self):
        super().setUp()
        self.bind_three()

    def snapshot(self):
        with sqlite3.connect(self.store) as conn:
            # writer() refreshes machine registration timestamps even on a
            # refused action. Keep every authority/effect row and all machine
            # definitions, excluding only that unrelated declared_at field.
            rows = tuple(line for line in conn.iterdump() if not line.startswith('INSERT INTO "machines"'))
            definitions = tuple(conn.execute("SELECT name,version,initial,places FROM machines ORDER BY name"))
            return (*rows, repr(definitions))

    def call_as_lead(self, capability, *args):
        env = self.env(LEAD)
        if capability is None:
            env.pop("COORDINATION_CAPABILITY")
        else:
            env["COORDINATION_CAPABILITY"] = capability
        return run([sys.executable, str(TOOL), *args], cwd=self.world.cwd, env=env, timeout=60)

    def assert_denied_without_effect(self, capability, args):
        before = self.snapshot()
        result = self.call_as_lead(capability, *args)
        self.assertEqual(result.exit, 2, (args, result.stdout, result.stderr))
        self.assertEqual(self.snapshot(), before)
        if capability:
            self.assertNotIn(capability, result.stdout + result.stderr)

    def test_copied_lead_session_with_worker_capability_refuses_grant_and_draft(self):
        worker_capability = self.env(WORKER)["COORDINATION_CAPABILITY"]
        for args in (("grant", "PT-2", "--roles", "lead"),
                     ("draft", "replayed authority", "--outcome", "must refuse")):
            with self.subTest(args=args):
                self.assert_denied_without_effect(worker_capability, args)

    def test_missing_wrong_and_malformed_capability_refuse_mutation_and_rebind(self):
        for capability in (None, "f" * 64, "", "A" * 64, "0" * 63):
            for args in (("grant", "PT-2", "--roles", "lead"),
                         ("draft", "unproved caller", "--outcome", "must refuse"),
                         ("bind", "--roles", "lead,coordinator", "--probe", "must not replace")):
                with self.subTest(capability=capability, args=args):
                    self.assert_denied_without_effect(capability, args)

    def test_matching_capability_allows_lead_and_stores_only_digest(self):
        capability = self.env(LEAD)["COORDINATION_CAPABILITY"]
        self.ok("grant", "PT-2", "--roles", "verifier")
        self.assertRegex(self.ok("draft", "admitted lead", "--outcome", "valid custody"), r"assignment A-\d+")
        with sqlite3.connect(self.store) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(participants)")}
            self.assertIn("capability_digest", columns)
            held = conn.execute("SELECT capability_digest FROM participants WHERE session_id=?", (LEAD,)).fetchone()[0]
            self.assertEqual(held, hashlib.sha256(capability.encode()).hexdigest())
        for session in (LEAD, WORKER, CHECKER):
            self.assertNotIn(self.env(session)["COORDINATION_CAPABILITY"], "\n".join(self.snapshot()))

    def test_legacy_null_refuses_without_self_rekey(self):
        with sqlite3.connect(self.store) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(participants)")}
            self.assertIn("capability_digest", columns)
            conn.execute("UPDATE participants SET capability_digest=NULL WHERE session_id=?", (LEAD,))
        for args in (("grant", "PT-2", "--roles", "lead"),
                     ("draft", "legacy caller", "--outcome", "must refuse"),
                     ("bind", "--roles", "lead,coordinator")):
            with self.subTest(args=args):
                self.assert_denied_without_effect(self.env(LEAD)["COORDINATION_CAPABILITY"], args)

    def test_public_participant_views_omit_session_and_capability(self):
        listed = self.ok("participants")
        core = self.core()
        with core.reader() as conn:
            payload = core.CORE["participants"](conn, None)
        for row in payload["participants"]:
            self.assertNotIn("session", row)
            self.assertNotIn("capability_digest", row)
            self.assertNotIn("coordination_capability", row)
        for session in (LEAD, WORKER, CHECKER):
            self.assertNotIn(session, listed)
            self.assertNotIn(self.env(session)["COORDINATION_CAPABILITY"], listed)
        self.assertIn(LEAD, self.ok("who"))

    def test_direct_core_replay_refuses_even_with_real_lead_row(self):
        core = self.core(WORKER)
        before = self.snapshot()
        with core.writer() as conn:
            lead = conn.execute("SELECT * FROM participants WHERE session_id=?", (LEAD,)).fetchone()
            with unittest.mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": LEAD}):
                with self.assertRaises(core.Refusal):
                    core.CORE["grant"](conn, lead, agent=SHARED, participant="PT-2", roles="lead")
        self.assertEqual(self.snapshot(), before)


class Participants(_Scratch):
    def test_one_agent_string_three_sessions_are_three_participants(self):
        """The defect the core closes: machine.py:255 compares the agent
        string, which every session on this host shares."""
        self.bind_three()
        ids = {self.ok("who", session=s).split()[1]
               for s in (LEAD, WORKER, CHECKER)}
        self.assertEqual(len(ids), 3, f"three sessions, {len(ids)} participant(s): {ids}")

    def test_who_names_this_session_not_another(self):
        self.bind_three()
        self.assertIn("worker", self.ok("who", session=WORKER))
        self.assertNotIn("worker", self.ok("who", session=LEAD))

    def test_bind_twice_keeps_one_row_and_its_roles(self):
        """A re-bind is how a session refreshes what it can be reached
        at. It is not how it changes what it may do: the row keeps its
        id, and its roles are the ones it was granted."""
        self.ok("bind", "--roles", "worker", session=WORKER)
        first = self.ok("who", session=WORKER).split()[1]
        self.ok("bind", "--roles", "worker", "--endpoint", "tcp://127.0.0.1:7311",
                "--probe", "answered in 40ms", session=WORKER)
        self.assertEqual(self.ok("who", session=WORKER).split()[1], first)
        self.assertIn("worker", self.ok("who", session=WORKER))

    def test_the_acting_participant_cannot_be_supplied(self):
        """Objective §7: a caller-supplied actor confers no authority."""
        self.bind_three()
        proc = self.run_tool("who", "--participant", "PT-1", session=WORKER)
        refusal_wire(RefusalCase("supplied-participant", ("who",), "arguments"), proc)


class Assignments(_Scratch):
    def test_the_vertical_path_reaches_accepted(self):
        aid = self.submitted()
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        self.ok("accept", aid, "--evidence", "a1b2c3d", "--generation", self.gen(aid),
                session=LEAD)
        self.assertIn("accepted", self.ok("show", aid, session=LEAD))

    def test_a_worker_may_not_offer(self):
        self.bind_three()
        out = self.ok("draft", "a repair", "--outcome", "it refuses", session=LEAD)
        aid = out.split()[-1]
        proc = self.run_tool("offer", aid, "--to", "PT-2", "--generation",
                             self.gen(aid), session=WORKER)
        refusal_wire(RefusalCase("worker-offers", (), "role", named=("lead",)), proc)

    def test_a_claim_by_another_participant_refuses_recipient(self):
        self.lock()
        self.bind_three()
        aid = self.offered(to="PT-2")
        proc = self.run_tool("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid),
                             session=CHECKER)
        refusal_wire(RefusalCase("wrong-claimant", (), "recipient"), proc)

    def test_a_claim_off_the_lock_refuses_deliverable(self):
        self.bind_three()
        aid = self.offered()
        proc = self.run_tool("claim", aid, "--branch", "lane/not-on-the-board",
                             "--generation", self.gen(aid), session=WORKER)
        refusal_wire(RefusalCase("claim-off-lock", (), "deliverable"), proc)

    def test_a_second_claim_refuses_taken(self):
        aid = self.held()
        proc = self.run_tool("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid),
                             session=WORKER)
        refusal_wire(RefusalCase("second-claim", (), "taken"), proc)


class GenerationAndRevision(_Scratch):
    def test_a_stale_holder_cannot_submit_after_reassignment(self):
        """AC-03: the place alone returns to `held`, so the generation is
        what refuses the old holder."""
        aid = self.held()
        stale = self.gen(aid)
        self.ok("reassign", aid, "--to", "PT-3", "--why", "the worker went quiet",
                "--generation", stale, session=LEAD)
        proc = self.run_tool("submit", aid, "--evidence", "a1b2c3d",
                             "--revision", "a1b2c3d", "--generation", stale,
                             session=WORKER)
        refusal_wire(RefusalCase("stale-generation", (), "generation",
                                 named=(stale, self.gen(aid))), proc)

    def test_release_bumps_the_generation(self):
        aid = self.held()
        before = int(self.gen(aid))
        self.ok("release", aid, "--why", "blocked on the wall", "--generation",
                str(before), session=WORKER)
        self.assertIn(f"generation {before + 1}", self.ok("show", aid, session=LEAD))

    def test_verify_refuses_an_observation_of_another_revision(self):
        self.lock()
        self.bind_three()
        aid = self.offered()
        self.ok("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid), session=WORKER)
        self.ok("observe", aid, "--observation", "ran at the old tree",
                "--revision", "a1b2c3d", "--generation", self.gen(aid), session=WORKER)
        self.ok("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session=WORKER)
        proc = self.run_tool("verify", aid, "--candidate", "9999999",
                             "--evidence", "20260916T010203Z-job-v",
                             "--generation", self.gen(aid), session=CHECKER)
        refusal_wire(RefusalCase("stale-revision", (), "revision"), proc)

    def test_reopen_stales_the_observations_and_keeps_them(self):
        """AC-10: a changed candidate needs reassessment; the old row stays."""
        aid = self.submitted()
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        self.ok("reopen", aid, "--revision", "beefbee", "--why", "the candidate moved",
                "--generation", self.gen(aid), session=LEAD)
        current = self.ok("observations", aid, session=LEAD)
        self.assertNotIn("the suite ran", current)
        self.assertIn("the suite ran", self.ok("observations", aid, "--all", session=LEAD))


class Producers(_Scratch):
    def test_the_producer_cannot_verify_its_own_candidate(self):
        """AC-11: an independent pass is not a role label away."""
        aid = self.submitted()
        self.ok("grant", "PT-2", "--roles", "verifier", session=LEAD)
        proc = self.run_tool("verify", aid, "--candidate", "a1b2c3d",
                             "--evidence", "20260916T010203Z-job-v",
                             "--generation", self.gen(aid), session=WORKER)
        refusal_wire(RefusalCase("self-verify", (), "producer"), proc)

    def test_the_producer_cannot_accept_its_own_candidate(self):
        aid = self.submitted()
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        self.ok("grant", "PT-2", "--roles", "lead", session=LEAD)
        proc = self.run_tool("accept", aid, "--evidence", "a1b2c3d",
                             "--generation", self.gen(aid), session=WORKER)
        refusal_wire(RefusalCase("self-accept", (), "producer"), proc)


class Messages(_Scratch):
    def test_an_ack_does_not_resolve_an_obligation(self):
        """AC-04: acknowledgment and resolution are separate facts."""
        self.bind_three()
        mid = self.ok("send", "--to", "PT-2", "--purpose", "request",
                      "--body", "name the reading", session=LEAD).split()[1]
        self.ok("ack", mid, session=WORKER)
        self.assertIn(mid, self.ok("outstanding", session=LEAD))
        self.ok("resolve", mid, "--evidence", "a1b2c3d", session=WORKER)
        self.assertNotIn(mid, self.ok("outstanding", session=LEAD))

    def test_the_four_delivery_facts_are_kept_apart(self):
        self.bind_three()
        mid = self.ok("send", "--to", "PT-2", "--purpose", "request",
                      "--body", "name the reading", session=LEAD).split()[1]
        self.ok("deliver", mid, "--ref", "750aa45e-a0c6", session=LEAD)
        shown = self.ok("show", mid, session=LEAD)
        self.assertIn("queued", shown)
        self.assertIn("750aa45e-a0c6", shown)
        self.assertNotIn("acked", shown)

    def test_an_inform_is_not_an_obligation(self):
        self.bind_three()
        self.ok("send", "--to", "PT-2", "--purpose", "inform",
                "--body", "the trunk moved", session=LEAD)
        self.assertIn("none", self.ok("outstanding", session=LEAD))

    def test_a_role_addressee_reaches_the_holder_of_the_role(self):
        self.bind_three()
        mid = self.ok("send", "--to", "verifier", "--purpose", "request",
                      "--body", "examine the candidate", session=LEAD).split()[1]
        self.assertIn(mid, self.ok("inbox", session=CHECKER))
        self.assertNotIn(mid, self.ok("inbox", session=WORKER))

    def test_only_the_recipient_resolves(self):
        self.bind_three()
        mid = self.ok("send", "--to", "PT-2", "--purpose", "request",
                      "--body", "name the reading", session=LEAD).split()[1]
        proc = self.run_tool("resolve", mid, "--evidence", "a1b2c3d", session=CHECKER)
        refusal_wire(RefusalCase("wrong-resolver", (), "recipient"), proc)


class Resources(_Scratch):
    def test_two_sessions_cannot_hold_one_resource(self):
        """AC-09: the engine's hold is the lease; no new machinery."""
        aid = self.held()
        self.ok("reserve", "the-suite", "--for", aid, session=WORKER)
        proc = self.run_tool("reserve", "the-suite", "--for", aid, session=CHECKER)
        refusal_wire(RefusalCase("double-reserve", (), "taken"), proc)
        self.ok("free", "the-suite", "--why", "the run finished", session=WORKER)
        self.ok("reserve", "the-suite", "--for", aid, session=CHECKER)


class Decisions(_Scratch):
    def test_only_a_lead_decides(self):
        self.bind_three()
        did = self.ok("ask", "--question", "ship or hold?",
                      "--alternatives", "ship|hold", session=LEAD).split()[1]
        proc = self.run_tool("decide", did, "--choice", "ship",
                             "--rationale", "the suite is green", session=WORKER)
        refusal_wire(RefusalCase("worker-decides", (), "role", named=("lead",)), proc)

    def test_a_brief_without_counterevidence_says_so(self):
        self.bind_three()
        did = self.ok("ask", "--question", "ship or hold?",
                      "--alternatives", "ship|hold", session=LEAD).split()[1]
        self.assertIn("declared gap", self.ok("decision", did, session=LEAD))


class Brief(_Scratch):
    def test_a_restarted_participant_recovers_its_obligations(self):
        """AC-12: the brief is what a compacted session reads back."""
        aid = self.held()
        self.ok("send", "--to", "PT-2", "--purpose", "request",
                "--body", "name the reading", session=LEAD)
        brief = self.ok("brief", session=WORKER)
        self.assertIn(aid, brief)
        self.assertIn("name the reading", brief)

    def test_coverage_names_the_gap(self):
        aid = self.submitted()
        coverage = self.ok("coverage", session=LEAD)
        self.assertIn(aid, coverage)
        self.assertIn("gap", coverage)


class Absence(_Scratch):
    def test_every_read_declares_an_absent_store_the_same_way(self):
        """A read with no store could not look. Saying so is the whole of
        docs/wire.md's absence discipline, and who said it differently:
        it refused class participant naming the session, which reads as
        having looked and found no participant. Grok #7 against 05b8aebe."""
        self.assertFalse(self.store.exists())
        for verb in ("who", "participants", "assignments", "brief",
                     "outstanding", "resources", "decisions", "coverage"):
            proc = self.run_tool(verb, session=LEAD)
            self.assertEqual(proc.exit, 0, f"{verb}: {proc.stderr}")
            self.assertEqual(proc.stdout.strip(), "no store", verb)
        self.assertFalse(self.store.exists(), "a read created the store")

    def test_a_read_with_no_store_looked_and_found_nothing(self):
        self.assertFalse(self.store.exists())
        self.assertIn("no store", self.ok("assignments", session=LEAD))
        self.assertFalse(self.store.exists(), "a read created the store")


class Wire(_Scratch):
    ROWS = (
        RefusalCase("unknown-verb", ("teleport",), "arguments"),
        RefusalCase("no-identity", ("bind", "--roles", "worker"), "identity"),
        RefusalCase("bad-id", ("show", "A-x"), "id"),
        RefusalCase("unknown-id", ("show", "A-404"), "unknown"),
    )

    def test_the_refusal_wire_holds_on_every_row(self):
        self.bind_three()
        run_table(self, self.ROWS, lambda row: self.run_tool(
            *row.argv, session=LEAD,
            identity=None if row.id == "no-identity" else SHARED))

    def test_a_bind_with_no_session_id_refuses_session(self):
        proc = self.run_tool("bind", "--roles", "worker", session=None)
        refusal_wire(RefusalCase("no-session", (), "session"), proc)

    def test_a_verb_before_bind_refuses_participant(self):
        proc = self.run_tool("draft", "a repair", "--outcome", "it refuses",
                             session=LEAD)
        refusal_wire(RefusalCase("unbound", (), "participant"), proc)


class DispatchedWorkers(_Scratch):
    """A dispatched Codex or Grok worker has no harness session id of its
    own, so it could hold no participant and every mutation it made
    refused class participant. The launcher mints a session id per
    dispatch; a dispatcher binds a participant to it. Raised by
    agentscient-1c against the contract at 537f3a35."""

    MINTED = "dispatch-7f3a4c21"

    def as_worker(self, *argv, sid=None):
        """The worker's environment: the older CLAUDE_SESSION_ID key, which
        is what the launcher sets and instrument.session() already admits."""
        env = self.world.env(extra={"LANE_CONF": str(self.conf)})
        env["WF_AGENT"] = SHARED
        env["CLAUDE_SESSION_ID"] = sid or self.MINTED
        env["COORDINATION_CAPABILITY"] = self.env(sid or self.MINTED)["COORDINATION_CAPABILITY"]
        return run([sys.executable, str(TOOL), *argv],
                   cwd=self.world.cwd, env=env, timeout=60)

    def dispatched(self):
        self.lock()
        self.ok("bind", "--roles", "lead,coordinator,dispatcher", session=LEAD)
        aid = self.ok("draft", "review the core", "--outcome", "findings",
                      "--revision", "a1b2c3d", session=LEAD).split()[-1]
        self.ok("bind-for", "--session", self.MINTED, "--roles", "worker",
                "--for", aid, session=LEAD)
        return aid

    def test_a_dispatched_worker_holds_a_participant_of_its_own(self):
        self.dispatched()
        who = self.as_worker("who")
        self.assertEqual(who.exit, 0, who.stderr)
        self.assertIn("worker", who.stdout)
        self.assertIn(self.MINTED, who.stdout)
        self.assertNotIn("PT-1 ", who.stdout)

    def test_a_dispatched_worker_can_record_its_own_findings(self):
        aid = self.dispatched()
        done = self.as_worker("observe", aid, "--observation", "the row refuses",
                              "--revision", "a1b2c3d", "--generation", self.gen(aid))
        self.assertEqual(done.exit, 0, done.stderr)
        self.assertIn("the row refuses", self.ok("observations", aid, session=LEAD))

    def test_only_a_dispatcher_binds_a_participant_for_another_session(self):
        self.bind_three()
        proc = self.run_tool("bind-for", "--session", self.MINTED, "--roles", "worker",
                             "--for", "A-1", session=WORKER)
        refusal_wire(RefusalCase("worker-binds-for", (), "role",
                                 named=("dispatcher",)), proc)

    def test_the_binding_records_its_issuer_and_its_assignment(self):
        aid = self.dispatched()
        listed = self.ok("participants", session=LEAD)
        self.assertIn("bound by PT-1", listed)
        self.assertIn(f"for {aid}", listed)


class TheCoreSeam(_Scratch):
    """The seam the MCP projection settles against: one function per verb,
    typed arguments and the resolved participant in, a dict out, an
    instrument.Refusal with expected, found and needed on refusal. The CLI
    is a wrapper over the same functions. Ruled by the Lead, 2026-09-16.

    These rows call the module in process rather than as a subprocess,
    because the seam they are about is the function's, not the wire's.
    The world still owns the directories; the environment is patched for
    the call and restored after it.
    """

    def test_every_verb_is_keyed_by_its_wire_name_in_one_table(self):
        module = self.core()
        self.assertIn("return", module.CORE, "the wire name is the key")
        self.assertEqual(
            set(module.CORE), set(module.VERBS),
            "the CLI and the core must offer the same verbs",
        )

    def test_a_core_function_returns_a_dict(self):
        module = self.core()
        with module.writer() as conn:
            part = module.core_bind(
                conn, None, agent=SHARED, roles="lead,coordinator",
            )
            self.assertIsInstance(part, dict)
            self.assertEqual(part["participant"], "PT-1")
            bound = module.bound_participant(conn)
            drafted = module.CORE["draft"](
                conn, bound, agent=SHARED, title="a bounded repair",
                outcome="the row refuses", revision="a1b2c3d",
            )
        self.assertIsInstance(drafted, dict)
        self.assertEqual(drafted["assignment"], "A-1")
        self.assertEqual(drafted["place"], "drafted")
        self.assertEqual(drafted["generation"], 1)

    def test_a_core_function_raises_the_refusal_with_its_three_parts(self):
        module = self.core()
        with module.writer() as conn:
            module.core_bind(conn, None, agent=SHARED, roles="worker")
            part = module.bound_participant(conn)
            with self.assertRaises(module.Refusal) as caught:
                module.CORE["draft"](
                    conn, part, agent=SHARED, title="a repair", outcome="it refuses",
                )
        line = caught.exception.line()
        self.assertTrue(line.startswith("role:"), line)
        for word in ("expected", "found", "needed"):
            self.assertIn(word, line)
        self.assertIn("lead", line)

    def test_the_participant_resolver_is_not_the_resolve_verb(self):
        """`resolve` is the wire name of the message verb. The participant
        resolver is not a verb at all, so it must not answer to that name:
        a projection mapping its tools by wire name would otherwise have
        two things to choose between and no way to tell which. Ruled by
        the Lead session, 2026-09-16."""
        module = self.core()
        self.assertFalse(
            hasattr(module, "resolve"),
            "the participant resolver must not wear the message verb's wire name",
        )
        self.assertTrue(callable(module.bound_participant))
        self.assertIs(module.CORE["resolve"], module.core_resolve)

    def test_the_cli_prints_what_the_function_returned(self):
        """The wrapper parses argv, calls the function and prints; the id
        the CLI shows is the id the function allocated."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        printed = self.ok("draft", "a bounded repair", "--outcome", "the row refuses",
                          session=LEAD).strip()
        module = self.core()
        with module.reader() as conn:
            row = conn.execute("SELECT id FROM assignments ORDER BY id").fetchone()
        self.assertEqual(printed, f"assignment A-{row['id']}")


class ObservationsBindToARevision(_Scratch):
    """An observation is evidence about a candidate, so it names the
    candidate it was made against and the generation it believes.

    Before this, observe inserted applicability 'current' without looking
    at either, so a participant that had not seen a reopen could append
    an observation of the old revision and it read as current evidence of
    the new one. Raised by the Codex review of the MCP server (#242),
    relayed by the Lead session, 2026-09-16.
    """

    def reopened(self):
        """A-n at generation 2, revision beefbee; the worker still believes
        generation 1 and revision a1b2c3d."""
        aid = self.submitted()
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        self.stale = self.gen(aid)
        self.ok("reopen", aid, "--revision", "beefbee", "--why", "the candidate moved",
                "--generation", self.stale, session=LEAD)
        return aid

    def test_an_observation_refuses_a_generation_the_assignment_moved_past(self):
        aid = self.reopened()
        proc = self.run_tool("observe", aid, "--observation", "the suite ran",
                             "--revision", "beefbee", "--generation", self.stale,
                             session=WORKER)
        refusal_wire(RefusalCase("observe-stale-generation", (), "generation"), proc)

    def test_an_observation_refuses_a_revision_that_is_not_the_current_one(self):
        aid = self.reopened()
        proc = self.run_tool("observe", aid, "--observation", "the suite ran",
                             "--revision", "a1b2c3d", "--generation", self.gen(aid),
                             session=WORKER)
        refusal_wire(RefusalCase("observe-old-revision", (), "revision"), proc)

    def test_an_observation_filed_as_stale_is_admitted_and_marked(self):
        """The escape is explicit and it is not a pass: the row lands
        marked stale, so it is readable as history and never as current
        evidence of the candidate that replaced it."""
        aid = self.reopened()
        self.ok("observe", aid, "--observation", "ran before the move",
                "--revision", "a1b2c3d", "--generation", self.stale, "--stale",
                session=WORKER)
        every = self.ok("observations", aid, "--all", session=LEAD)
        self.assertIn("ran before the move", every)
        self.assertIn("[stale:", every)
        self.assertNotIn("ran before the move",
                         self.ok("observations", aid, session=LEAD))

    def test_an_observation_of_the_current_candidate_is_admitted(self):
        """The clean neighbour: the same verb, the readings that agree."""
        aid = self.reopened()
        self.ok("observe", aid, "--observation", "the suite ran again",
                "--revision", "beefbee", "--generation", self.gen(aid), session=WORKER)
        self.assertIn("the suite ran again", self.ok("observations", aid, session=LEAD))


class TheGateUnderTheLock(_Scratch):
    """Every generation-bearing transition checks the generation twice:
    once in the verb so a stale holder reads class generation rather than
    class place, and once inside the engine's gate, under the write lock,
    so a candidate that moved between the two cannot be moved on. return
    had only the first. Codex #3 against 05b8aebe."""

    def racing(self, module, aid):
        """require_generation, wrapped so the first call — the verb's own,
        outside the lock — is followed by another session bumping the
        generation. What refuses afterwards can only be the gate."""
        seen = {"n": 0}
        real = module.require_generation
        number = int(aid.split("-")[1])

        def wrapped(row, stated):
            seen["n"] += 1
            answer = real(row, stated)
            if seen["n"] == 1:
                other = module.writer()
                other.execute(
                    "UPDATE assignments SET generation = generation + 1 WHERE id = ?",
                    (number,),
                )
                other.close()
            return answer

        return seen, wrapped

    def test_return_re_checks_the_generation_inside_the_lock(self):
        aid = self.submitted()
        believed = self.gen(aid)
        module = self.core(CHECKER)
        seen, wrapped = self.racing(module, aid)
        with unittest.mock.patch.object(module, "require_generation", wrapped):
            with module.writer() as conn:
                part = module.bound_participant(conn)
                with self.assertRaises(module.Refusal) as caught:
                    module.CORE["return"](conn, part, agent=SHARED, assignment=aid,
                                          findings="a1b2c3d", generation=believed)
        self.assertEqual(seen["n"], 2, "the gate must check again under the lock")
        self.assertTrue(caught.exception.line().startswith("generation:"),
                        caught.exception.line())

    def test_the_candidate_did_not_move_when_the_gate_refused(self):
        """A refusal inside the gate rolls the whole move back: the place
        is the one the winner left, not the one the loser asked for."""
        aid = self.submitted()
        believed = self.gen(aid)
        module = self.core(CHECKER)
        _seen, wrapped = self.racing(module, aid)
        with unittest.mock.patch.object(module, "require_generation", wrapped):
            with module.writer() as conn:
                part = module.bound_participant(conn)
                with self.assertRaises(module.Refusal):
                    module.CORE["return"](conn, part, agent=SHARED, assignment=aid,
                                          findings="a1b2c3d", generation=believed)
        self.assertIn("submitted", self.ok("show", aid, session=LEAD))


class TheReturnVerb(_Scratch):
    """return is the verb the whole table was reshaped around — it cannot
    be a Python function of that name — and no row exercised it. Grok #5
    against 05b8aebe: its findings were passed to the engine unread, so
    any non-empty string stood as evidence."""

    def test_return_sends_the_candidate_back_to_its_holder(self):
        aid = self.submitted()
        printed = self.ok("return", aid, "--findings", "#243",
                          "--generation", self.gen(aid), session=CHECKER).strip()
        self.assertEqual(printed, f"returned {aid} to PT-2")
        shown = self.ok("show", aid, session=LEAD)
        self.assertIn("held", shown)
        self.assertIn("PT-2", shown)

    def test_the_findings_are_a_reading_and_not_a_sentence(self):
        aid = self.submitted()
        proc = self.run_tool("return", aid, "--findings", "because I said so",
                             "--generation", self.gen(aid), session=CHECKER)
        refusal_wire(RefusalCase("return-prose-findings", (), "evidence"), proc)

    def test_every_pinned_reading_shape_is_admitted_as_findings(self):
        """The clean neighbours: the four shapes the contract pins."""
        for shape in ("a1b2c3d", "#243", "20260916T010203Z-job-x", "P-probe-1"):
            with self.subTest(shape=shape):
                aid = self.submitted()
                self.ok("return", aid, "--findings", shape, "--generation", self.gen(aid),
                        session=CHECKER)

    def test_a_worker_cannot_return_a_candidate(self):
        aid = self.submitted()
        proc = self.run_tool("return", aid, "--findings", "#243",
                             "--generation", self.gen(aid), session=WORKER)
        refusal_wire(RefusalCase("return-by-worker", (), "role",
                                 named=("verifier",)), proc)

    def test_return_refuses_a_generation_the_assignment_moved_past(self):
        """The generation is read before the engine sees the place, so a
        caller holding generation 1 reads class generation and not class
        place — which is why the check sits in the verb at all."""
        aid = self.held()
        stale = self.gen(aid)
        self.ok("release", aid, "--why", "blocked on the wall", "--generation", stale,
                session=WORKER)
        proc = self.run_tool("return", aid, "--findings", "#243",
                             "--generation", stale, session=CHECKER)
        refusal_wire(RefusalCase("return-stale-generation", (), "generation"), proc)

    def test_return_refuses_a_candidate_that_was_never_submitted(self):
        aid = self.held()
        proc = self.run_tool("return", aid, "--findings", "#243",
                             "--generation", self.gen(aid), session=CHECKER)
        refusal_wire(RefusalCase("return-from-held", (), "place"), proc)

    def test_return_refuses_an_assignment_the_store_does_not_hold(self):
        self.submitted()
        proc = self.run_tool("return", "A-99", "--findings", "#243",
                             "--generation", "1", session=CHECKER)
        refusal_wire(RefusalCase("return-unknown", (), "unknown"), proc)

    def test_return_refuses_an_id_of_the_wrong_kind(self):
        self.submitted()
        proc = self.run_tool("return", "M-1", "--findings", "#243",
                             "--generation", "1", session=CHECKER)
        refusal_wire(RefusalCase("return-wrong-kind", (), "id"), proc)

    def test_return_refuses_without_its_findings(self):
        aid = self.submitted()
        proc = self.run_tool("return", aid, "--generation", self.gen(aid), session=CHECKER)
        refusal_wire(RefusalCase("return-no-findings", (), "arguments"), proc)


class TheCandidateAnObservationNames(_Scratch):
    """`if row["revision"] and revision != row["revision"]` lifted the
    revision gate exactly when there was nothing to check against, so on
    an assignment drafted without a candidate an observation of any
    revision landed current, submit then wrote a different revision
    without staling it, and verify and coverage both counted it. Grok #1
    and Codex #2 against 05b8aebe."""

    def no_candidate_yet(self):
        """A drafted assignment with no revision: the contract keeps that
        legal, because work can be assigned before a candidate exists."""
        self.lock()
        self.bind_three()
        aid = self.ok("draft", "no candidate yet", "--outcome", "the gap",
                      session=LEAD).split()[-1]
        self.ok("offer", aid, "--to", "PT-2", "--generation", self.gen(aid),
                session=LEAD)
        self.ok("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid), session=WORKER)
        return aid

    def test_observe_refuses_an_assignment_that_names_no_revision(self):
        aid = self.no_candidate_yet()
        proc = self.run_tool("observe", aid, "--observation", "ran at deadbee",
                             "--revision", "deadbee", "--generation", self.gen(aid),
                             session=WORKER)
        refusal_wire(RefusalCase("observe-no-candidate", (), "revision"), proc)

    def test_stale_does_not_open_the_door_an_assignment_with_no_revision_shut(self):
        """--stale files history against a candidate that was replaced.
        With no candidate at all there is nothing it could be history of."""
        aid = self.no_candidate_yet()
        proc = self.run_tool("observe", aid, "--observation", "ran at deadbee",
                             "--revision", "deadbee", "--generation", self.gen(aid), "--stale",
                             session=WORKER)
        refusal_wire(RefusalCase("observe-no-candidate-stale", (), "revision"), proc)

    def test_the_clean_neighbour_an_assignment_that_names_its_candidate(self):
        aid = self.held()
        self.ok("observe", aid, "--observation", "the suite ran", "--revision",
                "a1b2c3d", "--generation", self.gen(aid), session=WORKER)
        self.assertIn("the suite ran", self.ok("observations", aid, session=LEAD))

    def test_submit_stales_a_current_observation_of_another_revision(self):
        """The second remedy, and defence in depth: with observe gated, no
        verb can now produce this row, but a store written before the gate
        can hold one. It is planted directly and submit must mark it."""
        aid = self.no_candidate_yet()
        believed = self.gen(aid)
        module = self.core(WORKER)
        number = int(aid.split("-")[1])
        with module.writer() as conn:
            conn.execute(
                "INSERT INTO observations(assignment, producer, revision, observation, "
                "applicability, at) VALUES (?, 2, 'deadbee', 'ran at deadbee', "
                "'current', '2026-09-16T00:00:00Z')", (number,),
            )
        self.assertIn("ran at deadbee", self.ok("observations", aid, session=LEAD))
        with module.writer() as conn:
            module.CORE["submit"](conn, module.bound_participant(conn), agent=SHARED,
                                  assignment=aid, evidence="a1b2c3d",
                                  revision="a1b2c3d", generation=believed)
        every = self.ok("observations", aid, "--all", session=LEAD)
        self.assertIn("ran at deadbee", every, "the row is kept, never deleted")
        self.assertIn("[stale:", every)
        self.assertNotIn("ran at deadbee",
                         self.ok("observations", aid, session=LEAD))

    def test_submit_keeps_a_current_observation_of_the_candidate_it_submits(self):
        """The clean neighbour for the second remedy: the row that agrees
        with the candidate stays current."""
        aid = self.submitted()
        self.assertIn("the suite ran", self.ok("observations", aid, session=LEAD))


class TheCandidateAndTheReading(_Scratch):
    """verify asked for one --evidence and used it as two things: it had
    to be a pinned reading shape, and it had to equal the assignment's
    revision exactly. So a candidate submitted with a P- row or a dispatch
    record id as its reading could never be verified at all. Grok #8
    against 05b8aebe."""

    def submitted_with(self, evidence):
        aid = self.held()
        self.ok("submit", aid, "--evidence", evidence, "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session=WORKER)
        return aid

    def test_a_candidate_submitted_with_a_corpus_row_can_be_verified(self):
        aid = self.submitted_with("P-probe-1")
        printed = self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                          "P-probe-1", "--generation", self.gen(aid), session=CHECKER).strip()
        self.assertEqual(printed, f"verified {aid} by PT-3")

    def test_a_candidate_submitted_with_a_dispatch_record_can_be_verified(self):
        aid = self.submitted_with("20260916T010203Z-job-x")
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid), session=CHECKER)

    def test_the_candidate_must_be_the_one_the_assignment_stands_at(self):
        aid = self.submitted_with("a1b2c3d")
        proc = self.run_tool("verify", aid, "--candidate", "9999999", "--evidence",
                             "20260916T010203Z-job-v", "--generation", self.gen(aid),
                             session=CHECKER)
        refusal_wire(RefusalCase("verify-wrong-candidate", (), "revision"), proc)

    def test_the_evidence_must_still_be_a_reading(self):
        aid = self.submitted_with("a1b2c3d")
        proc = self.run_tool("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                             "it looked fine to me", "--generation", self.gen(aid),
                             session=CHECKER)
        refusal_wire(RefusalCase("verify-prose-evidence", (), "evidence"), proc)

    def test_verify_refuses_without_its_candidate(self):
        aid = self.submitted_with("a1b2c3d")
        proc = self.run_tool("verify", aid, "--evidence", "20260916T010203Z-job-v",
                             "--generation", self.gen(aid), session=CHECKER)
        refusal_wire(RefusalCase("verify-no-candidate", (), "arguments"), proc)

    def test_the_verification_reading_is_what_the_marking_records(self):
        """The two are kept apart in the record as well as in the call: the
        marking carries the run that verified, not the candidate's sha."""
        aid = self.submitted_with("a1b2c3d")
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid), session=CHECKER)
        self.assertIn("20260916T010203Z-job-v", self.ok("show", aid, session=LEAD))


class TheCliGoesThroughTheTable(_Scratch):
    """The wrappers reach the core through `CORE[verb]`, not by name.

    Codex raised it against 05b8aebe: with the wrappers calling the
    functions directly, the table is a second surface, and a row of it
    pointing at the wrong function would pass every CLI test in this
    file. The projection in #242 reaches the core by that table alone,
    so a misrouted entry is a live defect there and silent here.
    """

    def test_every_wrapper_names_the_table(self):
        """Static, and it covers all of them: one row per verb would
        need a fixture per verb, and this is the property itself."""
        import inspect
        module = self.core()
        by_name = [
            verb for verb, wrapper in module.VERBS.items()
            if "CORE[" not in inspect.getsource(wrapper)
        ]
        self.assertEqual([], by_name,
                         "these wrappers call a core function by name")

    def test_a_misrouted_entry_fails_on_the_cli(self):
        """Live, on one verb: swap the table's row and the wrapper must
        reach the swap. If it calls core_draft by name it never will."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        module = self.core()
        reached = []

        def planted(conn, part, **rest):
            reached.append(rest.get("title"))
            raise module.Refusal("conflict", "the core's own draft",
                                 "a planted entry",
                                 "route the wrapper through CORE[verb]")

        module.CORE["draft"] = planted
        with self.assertRaises(module.Refusal) as caught:
            module.VERBS["draft"](["a bounded repair", "--outcome", "it refuses"])
        self.assertEqual(["a bounded repair"], reached)
        self.assertTrue(caught.exception.line().startswith("conflict:"))


class ThePassedRowIsNotAuthority(_Scratch):
    """A participant argument the caller chose is a claim, not a fact.

    Grok raised it against 05b8aebe: every authority check reads the row
    it was handed, so a caller holding a lead's row acts as that lead.
    The boundary binds the effective caller — the CLI from its own
    environment, the MCP server per call from the inherited session id,
    and a dispatched worker's process carries the id its launcher set —
    so the row's session must be this process's session, and the
    function is where that is checked, because the function is what both
    projections reach.
    """

    def foreign(self, module, conn):
        """The worker's row, read from a process bound as the lead."""
        return conn.execute(
            "SELECT * FROM participants WHERE session_id = ?", (WORKER,)
        ).fetchone()

    def test_a_foreign_row_refuses_class_participant(self):
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        module = self.core(LEAD)
        with module.writer() as conn:
            with self.assertRaises(module.Refusal) as caught:
                module.CORE["draft"](conn, self.foreign(module, conn), agent=SHARED,
                                     title="a repair in another name",
                                     outcome="it refuses", revision="a1b2c3d")
        line = caught.exception.line()
        self.assertTrue(line.startswith("participant:"), line)
        for word in ("expected", "found", "needed"):
            self.assertIn(word, line)

    def test_the_processs_own_row_passes(self):
        """The clean neighbour: the same call with the row this process
        is bound to does the work."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        module = self.core(LEAD)
        with module.writer() as conn:
            drafted = module.CORE["draft"](conn, module.bound_participant(conn),
                                           agent=SHARED, title="its own name",
                                           outcome="it lands", revision="a1b2c3d")
        self.assertEqual("A-1", drafted["assignment"])

    def test_a_read_refuses_a_foreign_row_too(self):
        """A read cannot refuse a caller — but the row it reports on is
        still the caller's own, or the answer is about someone else."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        module = self.core(LEAD)
        with module.writer() as conn:
            with self.assertRaises(module.Refusal) as caught:
                module.CORE["who"](conn, self.foreign(module, conn))
        self.assertTrue(caught.exception.line().startswith("participant:"))


class TheHolderIsAParticipant(_Scratch):
    """The engine's holder is a string its machine's owner chooses, and
    these machines choose the participant.

    `subjects.holder` is compared for equality by the engine and nothing
    else, so what it carries is the machine's decision. Carrying the
    agent string put the holder-or-lapsed transitions — release and free
    — back on the identity every other check in this module had already
    stopped trusting: one WF_AGENT per host means a holder that changed
    its agent string is refused, and a stranger that shares it is not.
    Raised by Grok against 05b8aebe and ruled by the Lead, 2026-09-16.
    """

    OTHER = "Grok 4.6 <noreply@example.test>"

    def subject(self, module, conn, aid):
        return module.machine.subject(conn, module.ASSIGNMENT, int(aid.split("-")[1]))

    def test_the_engine_holds_the_participant_not_the_agent(self):
        aid = self.held()
        module = self.core(LEAD)
        with module.reader() as conn:
            held = self.subject(module, conn, aid)
        self.assertEqual("PT-2", held["holder"],
                         "the machine's holder is the participant it chose")

    def test_the_holder_releases_under_another_agent_string(self):
        """The session that claimed is the session that releases; the
        agent string it runs under is not what makes it the holder."""
        aid = self.held()
        self.assertIn(f"released {aid}", self.ok(
            "release", aid, "--why", "blocked on a question", "--generation", self.gen(aid),
            session=WORKER, identity=self.OTHER))

    def test_a_lapsed_hold_is_released_by_another_participant(self):
        """holder-or-lapsed, as the transition is declared: once the
        lease runs out the hold is anyone's to hand back."""
        import time
        self.lock()
        aid = self.offered()
        self.ok("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid), "--ttl", "0",
                session=WORKER)
        time.sleep(1.2)
        self.assertIn(f"released {aid}", self.ok(
            "release", aid, "--why", "the worker stopped answering",
            "--generation", self.gen(aid), session=LEAD))

    def test_a_live_hold_refuses_another_participant(self):
        """The clean neighbour: while the lease stands, the holder holds."""
        aid = self.held()
        proc = self.run_tool("release", aid, "--why", "I want it back",
                             "--generation", self.gen(aid), session=LEAD)
        refusal_wire(RefusalCase("release-not-the-holder", (), "holder"), proc)

    def test_a_resource_is_freed_by_its_holder_under_another_agent_string(self):
        aid = self.held()
        self.ok("reserve", "port-7311", "--for", aid, session=WORKER)
        self.assertIn("freed port-7311", self.ok(
            "free", "port-7311", "--why", "the run ended", session=WORKER,
            identity=self.OTHER))


class TheBindChain(_Scratch):
    """Authority is rooted in the store's first participant.

    Before this, bind wrote whatever roles it was handed: a dispatched
    worker re-bound itself as a lead and drafted its own work (Codex #1),
    and a bind-for rewrote a bound participant's roles (Grok #2). The
    role a caller names is a request; what it may do comes from the chain
    above it.
    """

    MINTED = "dispatch-7f3a"

    def test_the_first_participant_is_the_bootstrap(self):
        """One self-bind with any roles, because a store with nobody in
        it has nobody to grant them."""
        self.ok("bind", "--roles", "lead,coordinator,dispatcher", session=LEAD)
        self.assertIn("lead,coordinator,dispatcher", self.ok("who", session=LEAD))

    def test_a_later_self_bind_above_worker_refuses(self):
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        proc = self.run_tool("bind", "--roles", "lead,dispatcher", session=WORKER)
        refusal_wire(RefusalCase("self-bind-above-worker", (), "role"), proc)

    def test_a_later_self_bind_as_worker_is_admitted(self):
        """The clean neighbour: a session announcing itself is fine, and
        worker is what announcing yourself gets you."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.assertIn("worker", self.ok("bind", "--roles", "worker", session=WORKER))

    def test_a_re_bind_refuses_to_change_its_own_roles(self):
        """The shape Codex reproduced: the bound worker asks again, for
        more."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        proc = self.run_tool("bind", "--roles", "worker,lead", session=WORKER)
        refusal_wire(RefusalCase("re-bind-new-roles", (), "role"), proc)
        self.assertNotIn("lead", self.ok("who", session=WORKER))

    def test_a_lead_grants_a_role_and_the_grantor_is_recorded(self):
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        printed = self.ok("grant", "PT-2", "--roles", "verifier", session=LEAD)
        self.assertIn("granted", printed)
        self.assertIn("PT-1", printed, "the grantor is on the line")
        self.assertIn("verifier", self.ok("who", session=WORKER))
        module = self.core(LEAD)
        with module.reader() as conn:
            row = conn.execute(
                "SELECT granted_by FROM participants WHERE session_id = ?", (WORKER,)
            ).fetchone()
        self.assertEqual(1, row["granted_by"], "the row records who granted it")

    def test_only_a_lead_grants(self):
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        self.ok("bind", "--roles", "worker", session=CHECKER)
        proc = self.run_tool("grant", "PT-3", "--roles", "lead", session=WORKER)
        refusal_wire(RefusalCase("grant-not-a-lead", (), "role"), proc)

    def test_bind_for_refuses_a_session_that_already_has_a_row(self):
        """It never overwrites: the row that exists is the row that
        stands, and the caller is told which one it is."""
        self.ok("bind", "--roles", "lead,coordinator,dispatcher", session=LEAD)
        aid = self.ok("draft", "a bounded repair", "--outcome", "the row refuses",
                      "--revision", "a1b2c3d", session=LEAD).split()[-1]
        self.ok("bind", "--roles", "worker", session=WORKER)
        proc = self.run_tool("bind-for", "--session", WORKER, "--roles", "worker",
                             "--for", aid, session=LEAD)
        refusal_wire(RefusalCase("bind-for-existing-row", (), "conflict"), proc)
        self.assertIn("worker", self.ok("who", session=WORKER))

    def test_bind_for_above_worker_needs_a_lead(self):
        """A dispatcher mints workers. Minting a lead is a grant, and a
        grant is a lead's."""
        self.ok("bind", "--roles", "lead,coordinator,dispatcher", session=LEAD)
        aid = self.ok("draft", "a bounded repair", "--outcome", "the row refuses",
                      "--revision", "a1b2c3d", session=LEAD).split()[-1]
        self.ok("bind", "--roles", "worker", session=WORKER)
        self.ok("grant", "PT-2", "--roles", "dispatcher", session=LEAD)
        proc = self.run_tool("bind-for", "--session", self.MINTED, "--roles",
                             "worker,lead", "--for", aid, session=WORKER)
        refusal_wire(RefusalCase("bind-for-above-worker", (), "role"), proc)

    def test_a_dispatcher_still_mints_a_worker(self):
        """The clean neighbour: what bind-for exists for still works."""
        self.ok("bind", "--roles", "lead,coordinator,dispatcher", session=LEAD)
        aid = self.ok("draft", "a bounded repair", "--outcome", "the row refuses",
                      "--revision", "a1b2c3d", session=LEAD).split()[-1]
        self.assertIn("bound for session", self.ok(
            "bind-for", "--session", self.MINTED, "--roles", "worker", "--for", aid,
            session=LEAD))


class TheDocumentedFormsParse(_Scratch):
    """Every command form the contract prints is run against the parser.

    The two reviewers found the same thing by reading: the table had gone
    stale where the verbs had moved. A table nobody runs goes stale
    silently, and its readers are the projections and the next session.
    Class `arguments` is exactly the answer this row is hunting — an
    unknown option, a wrong positional count, or a required option the
    form does not name.
    """

    CONTRACT = REPO / "ops" / "conductor" / "coordination-lever.md"

    LITERAL = {"R[,R]": "R", "A-n,..": "A-n"}
    VALUES = {
        "A-n": "A-1", "A-m": "A-2", "M-n": "M-1", "DC-n": "DC-1", "PT-n": "PT-1",
        "TITLE": "a bounded repair", "NAME": "port-7311", "R": "worker",
        "REF": "#243", "TEXT": "a reading of the run", "SHA": "a1b2c3d",
        "READING": "a1b2c3d", "REC": "20260916T010203Z-job-x", "WHO": "PT-1",
        "B": BRANCH, "N": "1", "MIN": "5", "ID": "dispatch-7f3a", "P": "request",
        "H": "claude-code", "E": "tcp://127.0.0.1:7311", "S": "held",
        # The sessions slice 2 courier verbs and send's --key.
        "ROUTE": "stdout", "SM-n": "SM-1", "AT-n": "AT-1", "O": "written",
        "K": "event:1:PT-1",
    }

    def forms(self):
        """The first cell of every row of the instrument table."""
        rows, inside = [], False
        for line in self.CONTRACT.read_text(encoding="utf-8").splitlines():
            if line.startswith("| verb | effect |"):
                inside = True
                continue
            if inside:
                if not line.startswith("|"):
                    break
                cell = line.split("|")[1].strip()
                if set(cell) <= set("-: "):
                    continue
                rows.append(cell.strip("`"))
        self.assertTrue(rows, "the instrument table was not found in the contract")
        return rows

    def argv(self, form):
        text = form
        for placeholder, plain in self.LITERAL.items():
            text = text.replace(placeholder, plain)
        out = []
        for token in text.replace("[", " ").replace("]", " ").split():
            if not out or token.startswith("--"):
                out.append(token)
            else:
                self.assertIn(token, self.VALUES,
                              f"no value for {token!r} in the form {form!r}")
                out.append(self.VALUES[token])
        return out

    def test_the_table_names_every_verb_and_only_them(self):
        module = self.core()
        self.assertEqual(set(module.VERBS),
                         {form.split()[0] for form in self.forms()},
                         "the contract's table and the verb table disagree")

    def test_every_documented_form_reaches_the_parser(self):
        self.lock()
        for form in self.forms():
            with self.subTest(form=form):
                proc = self.run_tool(*self.argv(form), session=LEAD)
                self.assertIn(proc.exit, (0, 2), proc.stderr)
                if proc.exit == 2:
                    self.assertNotEqual(
                        "arguments", proc.stderr.split(":")[0].strip(),
                        f"the documented form does not parse: {proc.stderr.strip()}")


if __name__ == "__main__":
    unittest.main()


class EveryMoveBumpsTheGeneration(_Scratch):
    """The generation is what refuses a caller acting on a view the
    assignment has moved past, and it only does that if every move moves
    it.

    Grok reproduced the hole against b1d868b0: return and submit both
    left the generation alone, so a verifier that began a return against
    one submission had it land on the next one, with the marking reading
    `return submitted -> held by PT-3; evidence a1b2c3d` after
    `submit held -> submitted by PT-2; evidence beefbee`. The Daybreak
    audit found the same shape in `supersede`, which stated no generation
    at all. Ruled by the Lead, 2026-09-16: every move bumps it, so a
    delayed verb refuses on the gate it already has.
    """

    def test_each_transition_bumps_the_generation(self):
        self.lock()
        self.bind_three()
        aid = self.ok("draft", "the whole ladder", "--outcome", "one bump a move",
                      "--revision", "a1b2c3d", session=LEAD).split()[-1]
        # The acceptance at the end of this walk rests on an examination
        # of the candidate (AC-10, coordination-roles.md §The acceptance
        # gate). `observe` is not a transition and bumps nothing, so the
        # five moves below are the five this row measures.
        self.ok("observe", aid, "--observation", "the suite ran",
                "--revision", "a1b2c3d", "--generation", self.gen(aid),
                session=CHECKER)
        seen = [int(self.gen(aid))]
        for argv, who in (
            (("offer", aid, "--to", "PT-2"), LEAD),
            (("claim", aid, "--branch", BRANCH), WORKER),
            (("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d"), WORKER),
            (("verify", aid, "--candidate", "a1b2c3d", "--evidence",
              "20260916T010203Z-job-v"), CHECKER),
            (("accept", aid, "--evidence", "a1b2c3d"), LEAD),
        ):
            self.ok(*argv, "--generation", self.gen(aid), session=who)
            seen.append(int(self.gen(aid)))
        self.assertEqual(seen, list(range(seen[0], seen[0] + len(seen))),
                         f"a move left the generation where it was: {seen}")

    def test_a_delayed_return_refuses_the_submission_it_never_saw(self):
        """Grok's sequence, run through the CLI: the verifier reads the
        generation, a competing return and a resubmit go past it, and the
        return it then makes is of a candidate it never looked at."""
        aid = self.submitted()
        believed = self.gen(aid)
        self.ok("return", aid, "--findings", "#243", "--generation", believed,
                session=LEAD)
        self.ok("submit", aid, "--evidence", "beefbee", "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session=WORKER)
        proc = self.run_tool("return", aid, "--findings", "a1b2c3d",
                             "--generation", believed, session=CHECKER)
        refusal_wire(RefusalCase("delayed-return", (), "generation"), proc)

    def test_a_delayed_submit_refuses_after_a_return(self):
        """The other half of the same race: the holder's resubmit is of a
        candidate the return has already sent back."""
        aid = self.submitted()
        believed = self.gen(aid)
        self.ok("return", aid, "--findings", "#243", "--generation", believed,
                session=CHECKER)
        proc = self.run_tool("submit", aid, "--evidence", "beefbee",
                             "--revision", "a1b2c3d", "--generation", believed,
                             session=WORKER)
        refusal_wire(RefusalCase("delayed-submit", (), "generation"), proc)

    def test_supersede_states_the_generation_it_believes(self):
        """The Daybreak audit's ABA: release returns the assignment to
        `offered` at a new generation, and the coordinator's stale view
        supersedes the assignment that now stands there."""
        self.lock()
        self.bind_three()
        old = self.ok("draft", "the older", "--outcome", "x", "--revision",
                      "a1b2c3d", session=LEAD).split()[-1]
        new = self.ok("draft", "the newer", "--outcome", "x", "--revision",
                      "beefbee", session=LEAD).split()[-1]
        self.ok("offer", old, "--to", "PT-2", "--generation", self.gen(old),
                session=LEAD)
        self.ok("claim", old, "--branch", BRANCH, "--generation", self.gen(old),
                session=WORKER)
        believed = self.gen(old)
        self.ok("release", old, "--why", "retry", "--generation", believed,
                session=WORKER)
        proc = self.run_tool("supersede", old, "--by", new, "--generation",
                             believed, session=LEAD)
        refusal_wire(RefusalCase("supersede-stale-generation", (), "generation"), proc)

    def test_supersede_refuses_without_a_generation(self):
        self.lock()
        self.bind_three()
        old = self.ok("draft", "the older", "--outcome", "x", session=LEAD).split()[-1]
        new = self.ok("draft", "the newer", "--outcome", "x", session=LEAD).split()[-1]
        self.ok("offer", old, "--to", "PT-2", "--generation", self.gen(old),
                session=LEAD)
        proc = self.run_tool("supersede", old, "--by", new, session=LEAD)
        refusal_wire(RefusalCase("supersede-no-generation", (), "arguments"), proc)

    def test_offer_refuses_without_a_generation(self):
        """The other verb the audit of the table found: it moves the
        assignment and stated nothing about the view it moved from."""
        self.bind_three()
        aid = self.ok("draft", "a repair", "--outcome", "x",
                      session=LEAD).split()[-1]
        proc = self.run_tool("offer", aid, "--to", "PT-2", session=LEAD)
        refusal_wire(RefusalCase("offer-no-generation", (), "arguments"), proc)

    def test_the_clean_neighbour_a_current_view_still_moves_it(self):
        """A verb that states the generation the assignment stands at is
        not refused: the gate is a stale-view check, not a second lock."""
        aid = self.submitted()
        self.ok("return", aid, "--findings", "#243", "--generation", self.gen(aid),
                session=CHECKER)
        self.assertIn("held", self.ok("show", aid, session=LEAD))


class TheParticipantIsReadNotSupplied(_Scratch):
    """A caller hands the participant in; nothing it hands in is believed.

    Round two found the first repair had moved the boundary rather than
    closed it. `same_session` compared the supplied mapping's own
    `session_id` against the environment and read nothing else from the
    store, so a mapping carrying this session's id and another
    participant's id and roles passed and then acted as that participant
    (Codex 834580#2, Daybreak 1833de#3). `core_bind` took the session id
    as an argument and never called `instrument.session()` at all, so a
    worker process minted participants for sessions that do not exist and
    rewrote a bound lead's agent, endpoint and probe (Grok bcfee6#2).

    The rule, ruled by the Lead on 2026-09-16: the mapping carries an id
    and nothing more. The row is reloaded from the store by that id and
    its `session_id` must be this process's; every field the function
    then reads — the roles above all — is the store's.
    """

    def forged(self, module, **fields):
        """A mapping shaped like a participant row and written by hand."""
        base = {"id": 1, "session_id": WORKER, "roles": "lead,coordinator",
                "agent": SHARED, "harness": None, "endpoint": None,
                "probe": None, "bound_at": "2026-09-16T00:00:00Z",
                "bound_by": None, "bound_for": None, "granted_by": None,
                "granted_at": None}
        base.update(fields)
        return base

    def test_a_hybrid_row_of_two_participants_refuses(self):
        """Daybreak's mapping: this session's id, another's id and roles."""
        self.bind_three()
        module = self.core(WORKER)
        with module.writer() as conn:
            with self.assertRaises(module.Refusal) as caught:
                module.CORE["draft"](conn, self.forged(module), agent=SHARED,
                                     title="forged", outcome="opened as PT-1")
        line = caught.exception.line()
        self.assertTrue(line.startswith("participant:"), line)

    def test_a_row_the_store_does_not_hold_refuses(self):
        """Codex's invented id, from a session with no participant at all."""
        self.bind_three()
        module = self.core(WORKER)
        with module.writer() as conn:
            with self.assertRaises(module.Refusal) as caught:
                module.CORE["draft"](conn, self.forged(module, id=999),
                                     agent=SHARED, title="forged",
                                     outcome="opened as nobody")
        line = caught.exception.line()
        self.assertTrue(line.startswith("participant:"), line)

    def test_the_roles_are_the_stores_and_not_the_mappings(self):
        """The half a session-id comparison cannot reach: the caller's own
        row, with the roles it wishes it held."""
        self.bind_three()
        module = self.core(WORKER)
        with module.writer() as conn:
            with self.assertRaises(module.Refusal) as caught:
                module.CORE["draft"](conn,
                                     self.forged(module, id=2, session_id=WORKER,
                                                 roles="lead,coordinator"),
                                     agent=SHARED, title="forged",
                                     outcome="drafted as a lead")
        line = caught.exception.line()
        self.assertTrue(line.startswith("role:"), line)

    def test_the_clean_neighbour_the_stores_own_row_works(self):
        self.bind_three()
        module = self.core(LEAD)
        with module.writer() as conn:
            shown = module.CORE["draft"](conn, module.bound_participant(conn),
                                         agent=SHARED, title="a real repair",
                                         outcome="it lands")
        self.assertTrue(shown["assignment"].startswith("A-"), shown)

    def test_bind_binds_the_session_this_process_runs_under(self):
        """Grok's mint: the session id is read, never passed."""
        module = self.core(WORKER)
        with module.writer() as conn:
            shown = module.CORE["bind"](conn, None, agent=SHARED, roles="worker")
        self.assertEqual(WORKER, shown["session"])
        listed = self.ok("participants", session=WORKER)
        self.assertEqual(1, len(listed.strip().splitlines()), listed)

    def test_bind_cannot_rewrite_another_participants_row(self):
        """Grok's hijack: a worker process re-binding the lead's session,
        rewriting its agent, its endpoint and its probe."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        module = self.core(WORKER)
        with module.writer() as conn:
            module.CORE["bind"](conn, None, agent="Attacker <a@e.test>",
                                roles="worker", endpoint="tcp://attacker:9",
                                probe="hijacked")
        listed = self.ok("participants", session=LEAD)
        lead = next(line for line in listed.splitlines() if line.startswith("PT-1 "))
        self.assertIn(f"PT-1 lead,coordinator — {SHARED}", lead)
        self.assertNotIn("Attacker", lead)
        self.assertNotIn("attacker:9", lead)
        # The worker refreshing its own agent string is the declared
        # behaviour of a re-bind, and it is the only row that moved.
        self.assertIn("PT-2 worker — Attacker <a@e.test>", listed)


class TheBootstrapIsOneRoot(_Scratch):
    """The store's first participant is the root of the authority chain,
    and a store has one of them.

    `writer()` connects with `isolation_level=None`, so the count that
    decides whether a bind is the bootstrap and the insert that acts on
    it were two autocommit statements with a window between them. All
    three reviewers of round two reproduced two roots through that
    window: `[('session-two', 'lead'), ('session-one', 'lead')]` against
    b1d868b0.
    """

    def racing_bind(self, module, first, second):
        """The first caller between its count and its insert, and the
        second binding into that window.

        Returns what each call came back with — a participant dict or the
        Refusal it raised.
        """
        import threading
        module.writer().close()
        local = threading.local()
        counted = threading.Event()
        out = {}

        def whose_session(default=""):
            return getattr(local, "sid", default)

        class Counting:
            """A cursor that says when the bootstrap count has been read."""

            def __init__(self, cursor):
                self.cursor = cursor

            def fetchone(self):
                row = self.cursor.fetchone()
                counted.set()
                time.sleep(0.8)
                return row

            def __getattr__(self, name):
                return getattr(self.cursor, name)

        class Held:
            def __init__(self, raw):
                self.raw = raw

            def execute(self, sql, args=()):
                cursor = self.raw.execute(sql, args)
                if sql.startswith("SELECT COUNT(*) AS n FROM participants"):
                    return Counting(cursor)
                return cursor

            def __getattr__(self, name):
                return getattr(self.raw, name)

        def call(name, sid, roles, hold):
            local.sid = sid
            if not hold:
                counted.wait(timeout=10)
            conn = module.writer()
            try:
                out[name] = module.CORE["bind"](
                    Held(conn) if hold else conn, None, agent=SHARED, roles=roles)
            except module.Refusal as refusal:
                out[name] = refusal
            finally:
                conn.close()

        with unittest.mock.patch.object(module, "session", whose_session):
            threads = [
                threading.Thread(target=call, args=("first", first, "lead,coordinator", True)),
                threading.Thread(target=call, args=("second", second, "lead,dispatcher", False)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        return out

    def test_two_first_binds_do_not_both_take_lead(self):
        module = self.core(LEAD)
        out = self.racing_bind(module, "session-one", "session-two")
        self.assertIsInstance(out.get("first"), dict, out)
        self.assertIsInstance(out.get("second"), module.Refusal, out)
        self.assertEqual("role", out["second"].kind, out["second"].line())
        with module.reader() as conn:
            roles = [row["roles"] for row in
                     conn.execute("SELECT roles FROM participants ORDER BY id")]
        self.assertEqual(["lead,coordinator"], roles,
                         f"the chain has more than one root: {roles}")

    def test_the_clean_neighbour_two_binds_apart_still_work(self):
        """One root and one worker under it: the lock refuses nothing that
        was not racing."""
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        listed = self.ok("participants", session=LEAD)
        self.assertIn("PT-1 lead,coordinator", listed)
        self.assertIn("PT-2 worker", listed)


class AnEmptyRevisionIsNotARevision(_Scratch):
    """A revision is the identity of the candidate the work is against,
    and the empty string is not an identity.

    Every revision gate is spelled `if row["revision"] and ...`, so the
    column holding something falsy lifts all of them: the hole the
    no-revision repair closed reopened one character to the left. Grok
    and Codex both reproduced it against b1d868b0 — `submit --revision ''`
    exit 0, `show` reading `revision none`, and then `verify --candidate
    deadbee` exit 0 against a submission that named no candidate at all.
    """

    def test_submit_refuses_an_empty_revision(self):
        aid = self.held()
        proc = self.run_tool("submit", aid, "--evidence", "a1b2c3d",
                             "--revision", "", "--generation", self.gen(aid),
                             session=WORKER)
        refusal_wire(RefusalCase("submit-empty-revision", (), "revision"), proc)

    def test_submit_refuses_a_revision_of_whitespace(self):
        aid = self.held()
        proc = self.run_tool("submit", aid, "--evidence", "a1b2c3d",
                             "--revision", "   ", "--generation", self.gen(aid),
                             session=WORKER)
        refusal_wire(RefusalCase("submit-blank-revision", (), "revision"), proc)

    def test_a_draft_refuses_an_empty_revision(self):
        """`--revision` stays optional on a draft; naming it empty is not
        the same as leaving it out."""
        self.bind_three()
        proc = self.run_tool("draft", "a repair", "--outcome", "x",
                             "--revision", "", session=LEAD)
        refusal_wire(RefusalCase("draft-empty-revision", (), "revision"), proc)

    def test_reopen_refuses_an_empty_revision(self):
        aid = self.submitted()
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        proc = self.run_tool("reopen", aid, "--revision", "", "--why", "moved",
                             "--generation", self.gen(aid), session=LEAD)
        refusal_wire(RefusalCase("reopen-empty-revision", (), "revision"), proc)

    def test_verify_refuses_an_assignment_that_stands_at_no_revision(self):
        """The half a check on submit cannot reach: a store written before
        that check can hold such a row, so it is planted directly and
        verify must refuse it rather than compare against nothing."""
        aid = self.submitted()
        module = self.core(LEAD)
        number = int(aid.split("-")[1])
        conn = module.writer()
        try:
            conn.execute("UPDATE assignments SET revision = '' WHERE id = ?",
                         (number,))
        finally:
            conn.close()
        self.assertIn("revision none", self.ok("show", aid, session=LEAD))
        proc = self.run_tool("verify", aid, "--candidate", "deadbee", "--evidence",
                             "20260916T010203Z-job-v", "--generation", self.gen(aid),
                             session=CHECKER)
        refusal_wire(RefusalCase("verify-no-revision", (), "revision"), proc)

    def test_the_clean_neighbour_a_named_revision_still_submits_and_verifies(self):
        aid = self.held()
        self.ok("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session=WORKER)
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        self.assertIn("verified", self.ok("show", aid, session=LEAD))


class BindForNamesWhoGrantedTheRoles(_Scratch):
    """A participant above worker holds roles somebody granted, and the
    row says who and when.

    `grant` writes `granted_by` and `granted_at`; `bind-for` wrote
    neither, so a dispatcher could mint a verifier and the store carried
    no record of the grant at all — the audit's fifth finding against
    b1d868b0. `bound_by` is not that record: it says who ran the verb,
    not that anything above worker was conferred.
    """

    MINTED = "dispatch-9c14ba77"

    def minting(self, roles):
        self.lock()
        self.ok("bind", "--roles", "lead,coordinator,dispatcher", session=LEAD)
        aid = self.ok("draft", "review the core", "--outcome", "findings",
                      "--revision", "a1b2c3d", session=LEAD).split()[-1]
        self.ok("bind-for", "--session", self.MINTED, "--roles", roles,
                "--for", aid, session=LEAD)
        module = self.core(LEAD)
        conn = module.reader()
        try:
            return conn.execute(
                "SELECT roles, granted_by, granted_at FROM participants "
                "WHERE session_id = ?", (self.MINTED,)).fetchone()
        finally:
            conn.close()

    def test_a_minted_role_above_worker_records_its_grantor(self):
        row = self.minting("worker,verifier")
        self.assertEqual("worker,verifier", row["roles"])
        self.assertEqual(1, row["granted_by"],
                         "the dispatcher that minted the roles is not recorded")
        self.assertTrue(row["granted_at"], "the grant carries no stamp")

    def test_a_minted_worker_records_no_grant_because_none_was_made(self):
        """Announcing yourself is worth worker, so minting one confers
        nothing and the row says nothing. Absence declared, not implied."""
        row = self.minting("worker")
        self.assertEqual("worker", row["roles"])
        self.assertIsNone(row["granted_by"])
        self.assertIsNone(row["granted_at"])

    def test_the_minted_participant_still_reads_as_bound_by_its_issuer(self):
        """The clean neighbour: recording the grant does not disturb what
        bind-for already recorded."""
        self.minting("worker,verifier")
        self.assertIn("bound by PT-1", self.ok("participants", session=LEAD))


class AReadOpensTheStoreReadOnly(_Scratch):
    """A read never writes, and a store it cannot read is a refusal, not
    a store it builds.

    `reader()` connected read-write, ran `executescript(SCHEMA)` and then
    `ensure_columns`, so every read of this instrument migrated the store
    on the way past: the server session's reviewers found a read creating
    tables and adding columns under a caller that had asked for nothing
    of the kind (codex-beb7b6#3, grok-7bbe10#2). Reads need no identity,
    which makes an unidentified writer of exactly the reach of a read.
    """

    def test_a_reader_cannot_write_through_its_connection(self):
        self.bind_three()
        module = self.core(LEAD)
        conn = module.reader()
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                conn.execute("INSERT INTO participants(session_id, agent, roles, "
                             "bound_at) VALUES ('x', 'x', 'lead', 'x')")
        finally:
            conn.close()
        self.assertIn("readonly", str(caught.exception))

    def test_a_store_without_the_schema_refuses_class_state(self):
        """"Could not look" is a marker, never a store built to order."""
        module = self.core(LEAD)
        path = module.machine.store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        sqlite3.connect(path).close()
        proc = self.run_tool("participants", session=LEAD)
        refusal_wire(RefusalCase("bare-store", (), "state"), proc)

    def test_a_read_leaves_the_stores_bytes_unchanged(self):
        """The pin the ruling asks for, over every file the store is: the
        database, its write-ahead log and its shared-memory index."""
        import hashlib
        aid = self.submitted()
        module = self.core(LEAD)
        path = module.machine.store_path()

        def digest():
            return hashlib.sha256(path.read_bytes()).hexdigest()

        def frames():
            """The bytes the write-ahead log holds, which a read adds none
            of. The log and the shared-memory index are apparatus, not
            content: a read-only connection materialises both — the index
            is how any reader finds the log's frames — and the writer had
            checkpointed them away. A log of no frames is a log of
            nothing, and that is what a read leaves behind."""
            log = path.with_name(path.name + "-wal")
            return log.stat().st_size if log.is_file() else 0

        before = digest()
        self.ok("assignments", session=LEAD)
        self.ok("show", aid, session=LEAD)
        self.ok("participants", session=LEAD)
        self.assertEqual(before, digest(), "a read moved the store's bytes")
        self.assertEqual(0, frames(), "a read wrote frames to the log")


class TheQueueToMeNobodyHasDeliveredYet(_Scratch):
    """`undelivered` is what a deliverer asks: the messages queued to the
    calling participant that have not left the store yet.

    It is package D's one piece in this file; the server session's
    deliverer calls it. It closes no finding — it is a read the core
    lacked, and `outstanding` is not it: an obligation stays outstanding
    long after it was delivered, and an inform is never outstanding at
    all yet still has to be carried.
    """

    def queued(self, purpose="request", to="PT-2"):
        self.bind_three()
        return self.ok("send", "--to", to, "--purpose", purpose,
                       "--body", "read the core", session=LEAD).split()[1]

    def test_it_lists_what_is_queued_to_me_and_not_delivered(self):
        mid = self.queued()
        listed = self.ok("undelivered", session=WORKER)
        self.assertIn(mid, listed)
        self.assertIn("read the core", listed)

    def test_a_delivered_message_leaves_the_queue(self):
        mid = self.queued()
        self.ok("deliver", mid, "--ref", "750aa45e-a0c6", session=LEAD)
        self.assertIn("none", self.ok("undelivered", session=WORKER))

    def test_it_never_shows_another_participants_queue(self):
        mid = self.queued()
        self.assertNotIn(mid, self.ok("undelivered", session=LEAD))

    def test_an_inform_is_carried_too_though_it_is_no_obligation(self):
        """The line `outstanding` cannot answer: an inform has to be
        delivered and is never outstanding."""
        mid = self.queued(purpose="inform")
        self.assertIn(mid, self.ok("undelivered", session=WORKER))
        self.assertIn("none", self.ok("outstanding", session=LEAD))

    def test_a_message_addressed_to_a_role_reaches_its_holder(self):
        mid = self.queued(to="verifier")
        self.assertIn(mid, self.ok("undelivered", session=CHECKER))
        self.assertNotIn(mid, self.ok("undelivered", session=WORKER))

    def test_with_no_store_it_says_so_rather_than_answering_none(self):
        proc = self.run_tool("undelivered", session=LEAD)
        self.assertEqual(0, proc.exit, proc.stderr)
        self.assertIn("no store", proc.stdout)


class ARoleGivenCanBeTakenBack(_Scratch):
    """`grant` is additive and nothing subtracted, so a store accumulated
    authority and never shed it.

    The live run found it, not a review: the trial store stands at two
    leads because the roles were granted for a trial and could not be put
    down afterwards. Taking a role back is a lead's, it is recorded the
    way granting is, and it stops at the one thing the chain cannot do
    without — a store with no lead in it has nobody who can grant
    anything ever again, and that is unrecoverable rather than merely
    inconvenient.
    """

    def two_leads(self):
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)
        self.ok("grant", "PT-2", "--roles", "lead", session=LEAD)
        return self.ok("participants", session=LEAD)

    def test_a_lead_takes_a_role_back(self):
        # `grant` appends, so the granted row reads `worker,lead` and
        # `PT-2 worker` is a prefix of it rather than another row. Both
        # assertions read to the end of the field.
        self.assertIn("PT-2 worker,lead —", self.two_leads())
        self.ok("revoke", "PT-2", "--roles", "lead", session=LEAD)
        listed = self.ok("participants", session=LEAD)
        self.assertIn("PT-2 worker —", listed)
        self.assertIn("PT-1 lead,coordinator —", listed)

    def test_the_revocation_says_who_took_it_and_when(self):
        self.two_leads()
        self.ok("revoke", "PT-2", "--roles", "lead", session=LEAD)
        module = self.core(LEAD)
        conn = module.reader()
        try:
            row = conn.execute("SELECT revoked_by, revoked_at FROM participants "
                               "WHERE id = 2").fetchone()
        finally:
            conn.close()
        self.assertEqual(1, row["revoked_by"])
        self.assertTrue(row["revoked_at"])

    def test_only_a_lead_revokes(self):
        """A verifier is not a lead, and holding one granted role is not
        holding the one that grants."""
        self.bind_three()
        proc = self.run_tool("revoke", "PT-1", "--roles", "coordinator",
                             session=CHECKER)
        refusal_wire(RefusalCase("verifier-revokes", (), "role",
                                 named=("lead",)), proc)

    def test_a_worker_may_not_revoke_a_lead(self):
        self.bind_three()
        proc = self.run_tool("revoke", "PT-1", "--roles", "lead", session=WORKER)
        refusal_wire(RefusalCase("worker-revokes-lead", (), "role",
                                 named=("lead",)), proc)

    def test_the_last_lead_is_not_revocable(self):
        """The floor: a store with no lead has nobody who can ever grant
        one, so the check counts what is left, not what is asked for."""
        self.bind_three()
        proc = self.run_tool("revoke", "PT-1", "--roles", "lead", session=LEAD)
        refusal_wire(RefusalCase("last-lead", (), "role"), proc)

    def test_the_last_lead_is_not_revocable_by_a_second_lead_either(self):
        """Counting leads after the subtraction, whoever the subject is:
        two leads, one revokes the other, and then neither may take the
        last one down — including from itself."""
        self.two_leads()
        self.ok("revoke", "PT-2", "--roles", "lead", session=LEAD)
        proc = self.run_tool("revoke", "PT-1", "--roles", "lead", session=LEAD)
        refusal_wire(RefusalCase("last-lead-self", (), "role"), proc)

    def test_a_role_the_participant_does_not_hold_refuses(self):
        """Absence is declared: taking back what was never given is a
        refusal, not a silent no-op that reads as success."""
        self.bind_three()
        proc = self.run_tool("revoke", "PT-2", "--roles", "verifier", session=LEAD)
        refusal_wire(RefusalCase("not-held", (), "role"), proc)

    def test_what_is_left_is_never_nothing(self):
        """Announcing yourself is worth worker, so a participant stripped
        of everything stands where a fresh self-bind would, not at no
        roles at all."""
        self.bind_three()
        self.ok("grant", "PT-3", "--roles", "coordinator", session=LEAD)
        self.ok("revoke", "PT-3", "--roles", "coordinator,verifier,worker",
                session=LEAD)
        self.assertIn("PT-3 worker", self.ok("participants", session=LEAD))

    def test_the_clean_neighbour_a_second_lead_still_leads_until_it_is_taken(self):
        self.two_leads()
        aid = self.ok("draft", "a bounded repair", "--outcome", "the row refuses",
                      "--revision", "a1b2c3d", session=WORKER).split()[-1]
        self.assertIn(aid, self.ok("assignments", session=LEAD))
