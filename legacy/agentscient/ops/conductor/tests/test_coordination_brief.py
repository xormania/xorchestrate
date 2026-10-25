"""The brief's four readings: executions, requests, decisions, evidence.

Contract: `ops/conductor/coordination-roles.md` (package E). The subject
is `ops/conductor/tools/coordination.py`, run as a subprocess under a
World with its own HOME and XDG roots, a staged lane conf, and the real
deliverable lever for the lock a claim gates on. Nothing here reads the
operator's own state: the dispatch records these rows join against are
planted inside the world, and the absence of that store is asserted
before it is declared.

Written by hand under grant 272 (owner, 2026-09-16: "hand grant ok'd" at
the session's start and "get it done" today, relayed by the Lead session
agentscient-11), one producer (Claude Opus 5, 1M context), the second
producer absent by the owner's word (AGENTS.md §Ground rules). The one
command that reads this file is `python3 -m unittest discover -s
ops/conductor/tests -p test_coordination_brief.py`: red while the four
readings are absent from `core_brief`, read again after they are
written, both readings quoted in the commit that carries it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "coordination.py"
LOCK_LEVER = TOOLS / "deliverable.py"

AGENT = "Claude Opus 5 (1M context) <noreply@example.test>"
LEAD = "sess-e-lead"
WORKER = "sess-e-worker"
CHECKER = "sess-e-checker"
BRANCH = "lane/session-20260916-e-trial"


class CoordinationWorld(unittest.TestCase):
    """One world per row: its own HOME, its own XDG state, its own store."""

    def setUp(self):
        self.world = World(prefix="coordination-brief-")
        self.addCleanup(self.world.close)
        self.xdg = self.world.xdg_state
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")
        self.lane_state = self.xdg / "testlane"
        self.store = self.lane_state / "state.db"
        self.dispatch = self.lane_state / "dispatch"
        env = self.world.env()
        self.assertEqual(Path(env["XDG_STATE_HOME"]), self.xdg)
        self.assertNotEqual(Path(env["HOME"]).resolve(), Path.home().resolve())
        self.assertFalse(self.dispatch.exists(), "the world starts with no dispatch store")

    def env(self, session: str) -> dict:
        env = self.world.env(extra={"LANE_CONF": str(self.conf)})
        env["WF_AGENT"] = AGENT
        env["CLAUDE_CODE_SESSION_ID"] = session
        env["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        return env

    def run_tool(self, *argv, session=LEAD):
        return run([sys.executable, str(TOOL), *argv], cwd=self.world.cwd,
                   env=self.env(session), timeout=60)

    def ok(self, *argv, session=LEAD) -> str:
        proc = self.run_tool(*argv, session=session)
        self.assertEqual(proc.exit, 0, f"{argv}: {proc.stderr}")
        return proc.stdout

    def gen(self, aid: str) -> str:
        """The generation the assignment stands at, read and not assumed."""
        shown = self.ok("show", aid, session=LEAD)
        found = re.search(r"generation (\d+)", shown)
        self.assertIsNotNone(found, f"no generation in: {shown}")
        return found.group(1)

    def lock(self):
        proc = run(
            [sys.executable, str(LOCK_LEVER), "add", BRANCH, "--why", "package E's trial"],
            cwd=self.world.cwd, env=self.env(LEAD), timeout=60,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)

    # -- fixtures ----------------------------------------------------------

    def bind_two(self):
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)

    def held(self) -> str:
        """A worker holding one assignment on a locked branch."""
        self.lock()
        self.bind_two()
        opened = self.ok("draft", "package E's trial row",
                         "--outcome", "the brief reads back",
                         "--revision", "a1b2c3d", session=LEAD)
        aid = opened.split()[-1]
        self.ok("offer", aid, "--to", "PT-2", "--generation", self.gen(aid), session=LEAD)
        self.ok("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid), session=WORKER)
        return aid

    def verified(self) -> str:
        """The same assignment, carried to verified by a second producer,
        which is where `reopen` is declared from."""
        aid = self.held()
        self.ok("bind", "--roles", "worker", session=CHECKER)
        self.ok("grant", "PT-3", "--roles", "verifier", session=LEAD)
        self.ok("observe", aid, "--observation", "the suite ran",
                "--revision", "a1b2c3d", "--generation", self.gen(aid), session=WORKER)
        self.ok("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session=WORKER)
        self.ok("verify", aid, "--candidate", "a1b2c3d",
                "--evidence", "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        return aid

    def plant_execution(self, record_id: str, lineage: dict, *, status="launched") -> str:
        """One dispatch record inside this world, never on the host."""
        directory = self.dispatch / record_id
        directory.mkdir(parents=True)
        path = directory / "record.json"
        path.write_text(json.dumps({
            "id": record_id, "status": status, "lineage": lineage,
            "job": "code", "role": "worker", "at": "20260916T120000Z",
        }), encoding="utf-8")
        self.assertTrue(path.is_file(), "the record did not land")
        return record_id


class Executions(CoordinationWorld):
    """AC-12: what is running against the work this participant holds."""

    def test_the_brief_names_the_live_execution_of_held_work(self):
        aid = self.held()
        record = self.plant_execution(
            "20260916T120000Z-code-claude-aa11bb",
            {"assignment": aid, "branch": BRANCH, "base_sha": "a1b2c3da1b2c3d"},
        )
        brief = self.ok("brief", session=WORKER)
        self.assertIn(record, brief)
        self.assertIn("launched", brief)

    def test_an_execution_reaches_its_assignment_by_branch_when_the_lineage_has_no_id(self):
        """Every record in the store today carries lineage.branch and no
        assignment id; the reading works now and reads the id when the
        dispatcher starts writing it."""
        aid = self.held()
        record = self.plant_execution(
            "20260916T120100Z-code-claude-cc22dd",
            {"branch": BRANCH, "base_sha": "a1b2c3da1b2c3d"},
        )
        brief = self.ok("brief", session=WORKER)
        self.assertIn(record, brief)
        self.assertIn(aid, brief)

    def test_no_dispatch_store_is_could_not_look_and_never_none(self):
        """docs/wire.md: an absent store is a marker, not an empty list."""
        self.held()
        self.assertFalse(self.dispatch.exists())
        brief = self.ok("brief", session=WORKER)
        self.assertIn("could not look", brief)

    def test_a_store_holding_nothing_of_mine_is_an_answer(self):
        self.held()
        self.plant_execution(
            "20260916T120200Z-code-claude-ee33ff",
            {"assignment": "A-99", "branch": "lane/someone-else", "base_sha": "b2c3d4eb2c3d4e"},
        )
        brief = self.ok("brief", session=WORKER)
        self.assertIn("executions: none", brief)
        self.assertNotIn("could not look", brief)


class Requests(CoordinationWorld):
    """AC-12: what is owed, in both directions."""

    def test_the_brief_carries_requests_in_both_directions(self):
        self.held()
        self.ok("send", "--to", "PT-2", "--purpose", "request",
                "--body", "name the reading", session=LEAD)
        self.ok("send", "--to", "lead", "--purpose", "request",
                "--body", "which revision do I build on", session=WORKER)
        brief = self.ok("brief", session=WORKER)
        self.assertIn("name the reading", brief)
        self.assertIn("which revision do I build on", brief)

    def test_a_request_acknowledged_but_not_resolved_is_still_owed(self):
        self.held()
        queued = self.ok("send", "--to", "PT-2", "--purpose", "request",
                         "--body", "name the reading", session=LEAD)
        mid = queued.split()[1]
        self.ok("ack", mid, session=WORKER)
        brief = self.ok("brief", session=WORKER)
        self.assertIn("owed by me", brief)
        self.assertIn(mid, brief)


class Decisions(CoordinationWorld):
    """AC-12: the open decision rows, not a count of them."""

    def test_the_open_decision_rows_are_rows_and_not_a_count(self):
        self.held()
        asked = self.ok("ask", "--question", "ship or hold?",
                        "--alternatives", "ship|hold", session=WORKER)
        did = asked.split()[1]
        brief = self.ok("brief", session=WORKER)
        self.assertIn(did, brief)
        self.assertIn("ship or hold?", brief)

    def test_a_decided_question_leaves_the_brief(self):
        self.held()
        asked = self.ok("ask", "--question", "ship or hold?",
                        "--alternatives", "ship|hold", session=WORKER)
        did = asked.split()[1]
        self.ok("answer", did, "--counter", "holding costs a day",
                "--unknowns", "none", "--consequence", "the lane waits", session=LEAD)
        self.ok("decide", did, "--choice", "ship",
                "--rationale", "the suite is green at a1b2c3d", session=LEAD)
        brief = self.ok("brief", session=WORKER)
        self.assertIn("decisions open: none", brief)


class Evidence(CoordinationWorld):
    """AC-12: whether this participant's own readings still apply."""

    def test_stale_evidence_names_its_reason_and_the_revision(self):
        aid = self.verified()
        self.ok("reopen", aid, "--revision", "b2c3d4e",
                "--why", "the candidate moved under it", "--generation", self.gen(aid),
                session=LEAD)
        brief = self.ok("brief", session=WORKER)
        self.assertIn("stale", brief)
        self.assertIn("the candidate moved under it", brief)
        self.assertIn("a1b2c3d", brief)

    def test_evidence_taken_against_the_applicable_revision_reads_as_current(self):
        aid = self.held()
        self.ok("observe", aid, "--observation", "the suite ran",
                "--revision", "a1b2c3d", "--generation", self.gen(aid), session=WORKER)
        brief = self.ok("brief", session=WORKER)
        self.assertIn("current", brief)
        self.assertNotIn("stale:", brief)


class Restart(CoordinationWorld):
    """AC-12: an interrupted session reconstructs its work from the
    record, because there is nowhere else for it to come from."""

    def test_a_restarted_session_rebinds_and_reads_back_its_obligations(self):
        aid = self.held()
        self.ok("observe", aid, "--observation", "the ten rows read Ran 10 OK",
                "--revision", "a1b2c3d", "--generation", self.gen(aid), session=WORKER)
        self.ok("send", "--to", "PT-2", "--purpose", "request",
                "--body", "name the reading the candidate rests on", session=LEAD)
        self.ok("send", "--to", "lead", "--purpose", "request",
                "--body", "which revision do I build the gate on", session=WORKER)
        self.ok("ask", "--question", "does the gate refuse or warn?",
                "--alternatives", "refuse|warn", session=WORKER)
        record = self.plant_execution(
            "20260916T131500Z-code-claude-restart",
            {"assignment": aid, "branch": BRANCH, "base_sha": "a1b2c3da1b2c3d"},
        )
        before = self.ok("brief", session=WORKER)

        # The restart: the session comes back the way a restarted harness
        # does, binding its id again, and reads. Every process above has
        # exited; nothing of the work is in this reader but the store.
        self.ok("bind", "--roles", "worker", session=WORKER)
        after = self.ok("brief", session=WORKER)

        self.assertEqual(before, after, "the restarted session reads something else")
        for token in (aid, record, "name the reading the candidate rests on",
                      "which revision do I build the gate on", "DC-1",
                      "current at a1b2c3d"):
            self.assertIn(token, after, "the recovered brief dropped something")

    def test_the_recovered_reading_comes_from_the_store_and_nowhere_else(self):
        """Moving the store aside empties the brief. A reading that
        survived that would be memory wearing a record's clothes."""
        aid = self.held()
        self.assertIn(aid, self.ok("brief", session=WORKER))
        moved = self.store.with_name("state.db.moved")
        self.store.rename(moved)
        self.assertTrue(moved.is_file() and not self.store.exists())
        proc = self.run_tool("brief", session=WORKER)
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "no store")


if __name__ == "__main__":
    unittest.main()
