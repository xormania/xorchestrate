"""The handoff manager: a session's package of threads, each with one place.

Contract: `ops/conductor/handoff-lever.md`. The tool
`ops/conductor/tools/handoff.py` is the subject and is run as a
subprocess under a scratch XDG_STATE_HOME, a staged lane conf, and the
real deliverable lever for the lock it gates on.

Written by hand under grant 220 (owner, 2026-09-13: "hand grant ok'd -
build the engine and the handoff manager"), one producer (Claude Fable
5.1), the second producer absent by the owner's word (AGENTS.md
§Ground rules). The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_handoff.py`:
red while `handoff.py` is absent, read again after it is written; both
readings are quoted in the commit that carries it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from ops.testkit import RefusalCase, World, refusal_wire, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "handoff.py"
LOCK_LEVER = TOOLS / "deliverable.py"
AGENT = "Closing Session <noreply@example.test>"
OTHER = "Next Session <noreply@example.test>"
BRANCH = "lane/session-20260913-x"
OTHER_BRANCH = "lane/session-20260913-y"


class _Scratch(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="handoff-")
        self.addCleanup(self.world.close)
        self.xdg = self.world.xdg_state
        self.conf = self.world.plant_text(
            "lane.conf", "LANE_NAME='testlane'\n"
        )
        self.store = self.xdg / "testlane" / "state.db"

    def env(self, identity: str | None) -> dict[str, str]:
        env = self.world.env(extra={"LANE_CONF": str(self.conf)})
        if identity:
            env["WF_AGENT"] = identity
        return env

    def run_tool(self, *argv: str, identity: str | None = AGENT, tool: Path = TOOL):
        return run(
            [sys.executable, str(tool), *argv],
            cwd=self.world.cwd,
            env=self.env(identity),
            timeout=60,
        )

    def ok(self, *argv: str, identity: str | None = AGENT) -> str:
        proc = self.run_tool(*argv, identity=identity)
        self.assertEqual(proc.exit, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        return proc.stdout

    def lock(self, *branches: str) -> None:
        proc = self.run_tool("set", *branches, "--why", "the scenario", tool=LOCK_LEVER)
        self.assertEqual(proc.exit, 0, proc.stderr)

    def assert_refusal(self, proc, klass: str) -> str:
        return refusal_wire(RefusalCase("fixture-refusal", (), klass), proc).line

    def a_handoff(self, *threads: str) -> list[str]:
        out = self.ok("open", "the session's package", "--summary", "what was left")
        self.assertEqual(out.strip(), "handoff H-1")
        ids = []
        for title in threads:
            line = self.ok("thread", "H-1", title, "--body", f"how to pick up {title}")
            ids.append(line.split()[1])
        return ids


class ThePresence(unittest.TestCase):
    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class OpeningAHandoff(_Scratch):
    def test_open_without_identity_refuses_identity(self):
        proc = self.run_tool("open", "a package", "--summary", "x", identity=None)
        self.assert_refusal(proc, "identity")
        self.assertFalse(self.store.exists())

    def test_open_prints_the_handoff_id_and_counts_up(self):
        first = self.ok("open", "first package", "--summary", "one")
        second = self.ok("open", "second package", "--summary", "two", "--branch", BRANCH,
                         "--prs", "#12 #13")
        self.assertEqual(first.strip(), "handoff H-1")
        self.assertEqual(second.strip(), "handoff H-2")

    def test_open_without_a_summary_refuses_arguments(self):
        self.assert_refusal(self.run_tool("open", "a package"), "arguments")

    def test_thread_on_an_unknown_handoff_refuses_unknown(self):
        proc = self.run_tool("thread", "H-7", "a thread", "--body", "b")
        self.assert_refusal(proc, "unknown")

    def test_thread_prints_its_id_and_list_shows_it_open(self):
        self.a_handoff()
        out = self.ok("thread", "H-1", "the plan reading", "--body", "read PLAN.md with the owner",
                      "--to", "next session")
        self.assertEqual(out.strip(), "thread T-1")
        listing = self.ok("list", identity=None)
        self.assertIn("T-1 open [H-1] the plan reading", listing)
        self.assertIn("to next session", listing)

    def test_a_malformed_id_refuses_id(self):
        self.a_handoff()
        line = self.assert_refusal(self.run_tool("thread", "1", "t", "--body", "b"), "id")
        self.assertIn("H-", line)
        self.assert_refusal(self.run_tool("take", "X1", "--branch", BRANCH), "id")


class TakingAThread(_Scratch):
    def test_take_without_a_lock_refuses_deliverable(self):
        self.a_handoff("a thread")
        line = self.assert_refusal(
            self.run_tool("take", "T-1", "--branch", BRANCH, identity=OTHER), "deliverable"
        )
        self.assertIn("deliverable.py add", line)
        self.assertIn("T-1 open", self.ok("list", identity=None))

    def test_take_with_a_branch_the_lock_does_not_name_refuses_deliverable(self):
        self.a_handoff("a thread")
        self.lock(OTHER_BRANCH)
        line = self.assert_refusal(
            self.run_tool("take", "T-1", "--branch", BRANCH, identity=OTHER), "deliverable"
        )
        self.assertIn(OTHER_BRANCH, line)

    def test_take_without_identity_refuses_identity(self):
        self.a_handoff("a thread")
        self.lock(BRANCH)
        self.assert_refusal(self.run_tool("take", "T-1", "--branch", BRANCH, identity=None), "identity")

    def test_take_claims_the_thread_for_the_identity_on_the_branch(self):
        self.a_handoff("a thread")
        self.lock(BRANCH)
        out = self.ok("take", "T-1", "--branch", BRANCH, identity=OTHER)
        self.assertEqual(out.strip(), f"taken T-1 on {BRANCH}")
        listing = self.ok("list", identity=None)
        self.assertIn("T-1 taken [H-1] a thread", listing)
        self.assertIn(OTHER, listing)
        self.assertIn(BRANCH, listing)

    def test_a_second_take_refuses_taken_naming_the_holder_and_branch(self):
        self.a_handoff("a thread")
        self.lock(BRANCH, OTHER_BRANCH)
        self.ok("take", "T-1", "--branch", BRANCH, identity=OTHER)
        line = self.assert_refusal(
            self.run_tool("take", "T-1", "--branch", OTHER_BRANCH, identity=AGENT), "taken"
        )
        self.assertIn(OTHER, line)
        self.assertIn(BRANCH, line)

    def test_take_of_a_handoff_claims_every_open_thread_at_once(self):
        self.a_handoff("first", "second", "third")
        self.lock(BRANCH)
        out = self.ok("take", "H-1", "--branch", BRANCH, identity=OTHER)
        self.assertEqual(out.strip().splitlines(), [
            f"taken T-1 on {BRANCH}", f"taken T-2 on {BRANCH}", f"taken T-3 on {BRANCH}",
        ])

    def test_take_of_a_handoff_holds_every_thread_since_one_moment(self):
        """T-9: one transaction for the package, so every thread carries
        the same held-since stamp and the same take marking stamp."""
        self.a_handoff("first", "second", "third")
        self.lock(BRANCH)
        self.ok("take", "H-1", "--branch", BRANCH, identity=OTHER)
        stamps = set()
        for tid in ("T-1", "T-2", "T-3"):
            shown = self.ok("show", tid, identity=None)
            take_lines = [ln for ln in shown.splitlines() if " take open -> taken " in ln]
            self.assertEqual(len(take_lines), 1, shown)
            stamps.add(take_lines[0].split()[0])
        self.assertEqual(len(stamps), 1, stamps)

    def test_take_of_a_handoff_with_a_thread_held_by_another_claims_nothing(self):
        self.a_handoff("first", "second")
        self.lock(BRANCH, OTHER_BRANCH)
        self.ok("take", "T-2", "--branch", OTHER_BRANCH, identity=AGENT)
        self.assert_refusal(self.run_tool("take", "H-1", "--branch", BRANCH, identity=OTHER), "taken")
        listing = self.ok("list", identity=None)
        self.assertIn("T-1 open", listing)
        self.assertIn("T-2 taken", listing)

    def test_take_of_a_thread_already_done_refuses_place(self):
        self.a_handoff("a thread")
        self.lock(BRANCH)
        self.ok("take", "T-1", "--branch", BRANCH, identity=OTHER)
        self.ok("done", "T-1", "--evidence", "abc1234", identity=OTHER)
        self.assert_refusal(self.run_tool("take", "T-1", "--branch", BRANCH, identity=OTHER), "place")


class FinishingAThread(_Scratch):
    def setUp(self):
        super().setUp()
        self.a_handoff("a thread")
        self.lock(BRANCH)
        self.ok("take", "T-1", "--branch", BRANCH, identity=OTHER)

    def test_done_by_another_than_the_holder_refuses_holder(self):
        line = self.assert_refusal(
            self.run_tool("done", "T-1", "--evidence", "abc1234", identity=AGENT), "holder"
        )
        self.assertIn(OTHER, line)

    def test_done_without_the_evidence_flag_refuses_arguments(self):
        self.assert_refusal(self.run_tool("done", "T-1", identity=OTHER), "arguments")

    def test_done_with_evidence_of_no_known_shape_refuses_evidence(self):
        line = self.assert_refusal(
            self.run_tool("done", "T-1", "--evidence", "trust me", identity=OTHER), "evidence"
        )
        self.assertIn("sha", line)

    def test_done_with_a_reading_closes_the_thread_and_list_hides_it(self):
        for evidence in ("#123",):
            out = self.ok("done", "T-1", "--evidence", evidence, identity=OTHER)
            self.assertEqual(out.strip(), f"done T-1: {evidence}")
        self.assertNotIn("T-1", self.ok("list", identity=None))
        self.assertIn("T-1 done", self.ok("list", "--all", identity=None))

    def test_the_four_evidence_shapes_are_accepted(self):
        for n, evidence in enumerate(("abc1234", "#12", "20260913T034513Z-plan-claude-892490", "P-ROW-1"), start=2):
            self.ok("thread", "H-1", f"thread {n}", "--body", "b")
            self.ok("take", f"T-{n}", "--branch", BRANCH, identity=OTHER)
            self.ok("done", f"T-{n}", "--evidence", evidence, identity=OTHER)

    def test_release_by_the_holder_reopens_the_thread(self):
        out = self.ok("release", "T-1", "--why", "the owner redirected me", identity=OTHER)
        self.assertEqual(out.strip(), "released T-1")
        listing = self.ok("list", identity=None)
        self.assertIn("T-1 open", listing)
        self.assertNotIn(BRANCH, listing)

    def test_release_by_another_before_the_ttl_refuses_holder(self):
        self.assert_refusal(self.run_tool("release", "T-1", "--why", "mine", identity=AGENT), "holder")

    def test_supersede_by_an_unknown_thread_refuses_unknown(self):
        self.ok("release", "T-1", "--why", "back to open", identity=OTHER)
        self.assert_refusal(self.run_tool("supersede", "T-1", "--by", "T-9"), "unknown")

    def test_supersede_an_open_thread_by_a_newer_one(self):
        self.ok("release", "T-1", "--why", "back to open", identity=OTHER)
        self.ok("thread", "H-1", "the newer thread", "--body", "b")
        out = self.ok("supersede", "T-1", "--by", "T-2")
        self.assertEqual(out.strip(), "superseded T-1 by T-2")
        listing = self.ok("list", identity=None)
        self.assertNotIn("T-1 ", listing)
        self.assertIn("T-2 open", listing)
        self.assertIn("T-1 superseded", self.ok("list", "--all", identity=None))


class ReadingTheBoard(_Scratch):
    def test_list_on_an_absent_store_says_so_and_creates_nothing(self):
        for verb in ("list", "brief"):
            proc = self.run_tool(verb, identity=None)
            self.assertEqual(proc.exit, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "no handoffs")
        self.assertFalse(self.store.exists(), "a read created the store")

    def test_show_on_an_absent_store_refuses_unknown(self):
        self.assert_refusal(self.run_tool("show", "T-1", identity=None), "unknown")

    def test_held_on_an_absent_store_holds_nothing(self):
        proc = self.run_tool("held", "--branch", BRANCH, identity=None)
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "none held")

    def test_held_lists_the_branch_holdings_as_a_finding_until_they_close(self):
        self.a_handoff("first", "second")
        self.lock(BRANCH)
        self.ok("take", "H-1", "--branch", BRANCH, identity=OTHER)
        proc = self.run_tool("held", "--branch", BRANCH, identity=None)
        self.assertEqual(proc.exit, 1, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertIn("T-1 taken", proc.stdout)
        self.assertIn("T-2 taken", proc.stdout)
        self.ok("done", "T-1", "--evidence", "#1", identity=OTHER)
        self.ok("release", "T-2", "--why", "not this session", identity=OTHER)
        proc = self.run_tool("held", "--branch", BRANCH, identity=None)
        self.assertEqual(proc.exit, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.strip(), "none held")

    def test_brief_counts_then_lists_open_and_taken_threads_by_handoff(self):
        self.a_handoff("first", "second")
        self.ok("thread", "H-1", "third", "--body", "b", "--to", "the wall session")
        self.lock(BRANCH)
        self.ok("take", "T-2", "--branch", BRANCH, identity=OTHER)
        self.ok("done", "T-2", "--evidence", "#1", identity=OTHER)
        self.ok("take", "T-1", "--branch", BRANCH, identity=OTHER)
        out = self.ok("brief", identity=None)
        lines = out.strip().splitlines()
        self.assertEqual(lines[0], "handoffs: open=1 taken=1 done=1")
        self.assertIn("H-1 the session's package", lines[1])
        self.assertIn(AGENT, lines[1])
        self.assertIn("T-1 taken", out)
        self.assertIn(f"{OTHER} on {BRANCH}", out)
        self.assertIn("T-3 open", out)
        self.assertIn("to the wall session", out)
        self.assertNotIn("T-2", out)

    def test_list_filters_by_state_addressee_and_handoff(self):
        self.a_handoff("first")
        self.ok("thread", "H-1", "second", "--body", "b", "--to", "wall")
        self.ok("open", "another package", "--summary", "s")
        self.ok("thread", "H-2", "third", "--body", "b")
        self.lock(BRANCH)
        self.ok("take", "T-3", "--branch", BRANCH, identity=OTHER)
        self.assertEqual(self.ok("list", "--to", "wall", identity=None).count("\nT-") + 1, 1)
        self.assertIn("T-2", self.ok("list", "--to", "wall", identity=None))
        taken = self.ok("list", "--state", "taken", identity=None)
        self.assertIn("T-3", taken)
        self.assertNotIn("T-1", taken)
        second = self.ok("list", "--handoff", "H-2", identity=None)
        self.assertIn("T-3", second)
        self.assertNotIn("T-1", second)
        self.assert_refusal(self.run_tool("list", "--state", "flying", identity=None), "arguments")

    def test_show_prints_the_thread_its_body_and_its_marking(self):
        self.a_handoff("a thread")
        self.lock(BRANCH)
        self.ok("take", "T-1", "--branch", BRANCH, identity=OTHER)
        out = self.ok("show", "T-1", identity=None)
        self.assertIn("T-1 taken [H-1] a thread", out)
        self.assertIn("how to pick up a thread", out)
        self.assertIn("create -> open", out)
        self.assertIn(f"take open -> taken by {OTHER}", out)
        self.assertIn(BRANCH, out)

    def test_show_of_a_handoff_prints_its_summary_and_threads(self):
        self.a_handoff("first", "second")
        out = self.ok("show", "H-1", identity=None)
        self.assertIn("H-1 the session's package", out)
        self.assertIn("what was left", out)
        self.assertIn("T-1 open", out)
        self.assertIn("T-2 open", out)

    def test_an_unknown_verb_refuses_arguments(self):
        self.assert_refusal(self.run_tool("teleport", identity=None), "arguments")


if __name__ == "__main__":
    unittest.main()
