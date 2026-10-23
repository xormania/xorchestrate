"""The role entry points: what a session of each role does first.

Contract: `ops/conductor/coordination-roles.md` §The entry points
(package E). The subject is `ops/conductor/tools/coordination.py`, run
as a subprocess under a World with its own HOME and XDG roots. The verb
is a read: it takes no identity, creates no store, and answers a session
that cannot bind yet — which is the reader it exists for, so the rows
below assert the store is still absent after it runs.

Written by hand under grant 272 (owner, 2026-09-16: "hand grant ok'd" at
the session's start and "get it done" today, relayed by the Lead session
agentscient-11), one producer (Claude Opus 5, 1M context), the second
producer absent by the owner's word (AGENTS.md §Ground rules). The one
command that reads this file is `python3 -m unittest discover -s
ops/conductor/tests -p test_coordination_entry.py`: red while the verb
is absent, read again after it is written, both readings quoted in the
commit that carries it.
"""

from __future__ import annotations

import hashlib
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import RefusalCase, World, refusal_wire, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "coordination.py"
LOCK_LEVER = TOOLS / "deliverable.py"

AGENT = "Claude Opus 5 (1M context) <noreply@example.test>"
LEAD = "sess-entry-lead"
WORKER = "sess-entry-worker"
BRANCH = "lane/session-20260916-entry-trial"


class _World(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="coordination-entry-")
        self.addCleanup(self.world.close)
        self.xdg = self.world.xdg_state
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")
        self.store = self.xdg / "testlane" / "state.db"
        env = self.world.env()
        self.assertEqual(Path(env["XDG_STATE_HOME"]), self.xdg)
        self.assertNotEqual(Path(env["HOME"]).resolve(), Path.home().resolve())

    def env(self, session: str, identity: str | None = AGENT) -> dict:
        env = self.world.env(extra={"LANE_CONF": str(self.conf)})
        if identity:
            env["WF_AGENT"] = identity
        env["CLAUDE_CODE_SESSION_ID"] = session
        env["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        return env

    def run_tool(self, *argv, session=LEAD, identity=AGENT):
        return run([sys.executable, str(TOOL), *argv], cwd=self.world.cwd,
                   env=self.env(session, identity), timeout=60)

    def ok(self, *argv, session=LEAD, identity=AGENT) -> str:
        proc = self.run_tool(*argv, session=session, identity=identity)
        self.assertEqual(proc.exit, 0, f"{argv}: {proc.stderr}")
        return proc.stdout

    def gen(self, aid: str) -> str:
        found = re.search(r"generation (\d+)", self.ok("show", aid, session=LEAD))
        self.assertIsNotNone(found)
        return found.group(1)

    def lock(self):
        proc = run(
            [sys.executable, str(LOCK_LEVER), "add", BRANCH, "--why", "package E's trial"],
            cwd=self.world.cwd, env=self.env(LEAD), timeout=60,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)

    def bind_two(self):
        self.ok("bind", "--roles", "lead,coordinator", session=LEAD)
        self.ok("bind", "--roles", "worker", session=WORKER)

    def held(self) -> str:
        self.lock()
        self.bind_two()
        aid = self.ok("draft", "package E's trial row", "--outcome",
                      "the entry point reads", "--revision", "a1b2c3d",
                      session=LEAD).split()[-1]
        self.ok("offer", aid, "--to", "PT-2", "--generation", self.gen(aid), session=LEAD)
        self.ok("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid),
                session=WORKER)
        return aid


class BeforeBinding(_World):
    """The reader the verb exists for: a session with nothing yet."""

    def test_a_session_that_cannot_bind_yet_still_reads_the_entry_point(self):
        self.assertFalse(self.store.exists())
        proc = self.run_tool("entry", session=LEAD)
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertIn("not bound", proc.stdout)
        self.assertIn("bind --roles", proc.stdout)
        self.assertFalse(self.store.exists(), "the read created the store")

    def test_the_entry_point_takes_no_identity(self):
        """docs/wire.md: a layer able to refuse a read is itself a
        finding, and this read is the one a session takes before it has
        an identity configured."""
        proc = self.run_tool("entry", session=LEAD, identity=None)
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertIn("lead", proc.stdout)

    def test_every_role_is_reachable_without_a_repo_skill(self):
        """Codex loads AGENTS.md and no repo skill; the entry point is
        where its equivalent context lives, so it carries every role and
        names the page to read next."""
        out = self.ok("entry", session=LEAD)
        for role in ("lead", "coordinator", "dispatcher", "worker", "verifier"):
            self.assertIn(f"{role} —", out)
        self.assertIn("coordination-roles.md", out)


class OneRole(_World):
    def test_a_named_role_reads_its_own_block_and_not_another(self):
        out = self.ok("entry", "--role", "verifier", session=LEAD)
        self.assertIn("verifier —", out)
        self.assertIn("coverage", out)
        self.assertNotIn("dispatcher —", out)

    def test_an_unknown_role_refuses_class_arguments(self):
        proc = self.run_tool("entry", "--role", "wizard", session=LEAD)
        refusal_wire(
            RefusalCase("unknown-role", ("entry", "--role", "wizard"), "arguments",
                        named=("wizard", "verifier")),
            proc,
        )


class AfterBinding(_World):
    def test_a_bound_session_reads_its_own_state(self):
        self.held()
        out = self.ok("entry", session=WORKER)
        self.assertIn("PT-2", out)
        self.assertIn("1 assignment held", out)

    def test_several_roles_read_several_blocks_in_the_table_order(self):
        self.bind_two()
        out = self.ok("entry", session=LEAD)
        self.assertIn("lead —", out)
        self.assertIn("coordinator —", out)
        self.assertNotIn("verifier —", out)
        self.assertLess(out.index("lead —"), out.index("coordinator —"))

    def test_the_next_steps_name_the_verb_and_the_argument_it_needs(self):
        self.held()
        out = self.ok("entry", session=WORKER)
        self.assertIn("brief", out)
        self.assertIn("--generation", out)


if __name__ == "__main__":
    unittest.main()
