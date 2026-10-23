"""Every Skill invocation logs a use in the skills registry.

Until 2026-09-13 a use was counted only when a session typed
`skills.py log-use` by hand: the report read 12 close, 7 deliverable, 6
onboard and zero for thirteen skills, a floor and not a measurement.
A PreToolUse hook on the Skill tool records the invocation with the
skill's name and its arguments, best effort, and never blocks the call:
a registry without the row, an unwritable store or a malformed payload
each leave the tool running and log nothing.

Hermetic, the shape of test_miss_log.py: a temporary HOME holds the
registry, a fake repository holds the lane conf and a copy of the real
skills tool, and the real hook runs as a subprocess. Identity is the
session's WF_AGENT, else the hook signs as itself with the lane's name.

Written by hand with the code it tests under wall grant 209 (owner,
2026-09-13: "hand grant ok'd for new skills #1-4 - do them in one PR");
the second producer is absent and this docstring says so.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path

import support

from ops.testkit import World, run

HOOK = support.CLAUDE_DIR / "skill-use.py"
REPO = Path(__file__).resolve().parents[4]
SKILLS_TOOL = REPO / "ops" / "conductor" / "tools" / "skills.py"
LANE_NAME = "testlane"
AGENT = "Use Tester <use@example.invalid>"


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HOOK.is_file(), f"absent: {HOOK}")
        self.world = World(prefix="skill-use-")
        self.addCleanup(self.world.close)
        self.tmp = self.world.root
        self.home = self.world.home
        self.repo = self.world.root / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "ops").mkdir()
        self.world.plant_text(self.repo / "ops" / "lane.conf",
            f"LANE_NAME='{LANE_NAME}'\nTRUNK_BRANCH='dev'\n"
        )
        self.lever = self.repo / "ops" / "conductor" / "tools" / "skills.py"
        self.lever.parent.mkdir(parents=True)
        self.world.plant_bytes(self.lever, SKILLS_TOOL.read_bytes())
        # The lever imports instrument.py beside itself, which loads
        # laneconf.py beside it and reads ops/lane.conf two levels up:
        # the fake repository's conf above (instrument-conventions.md).
        for sibling in ("instrument.py", "laneconf.py"):
            self.world.plant_bytes(
                self.lever.parent / sibling,
                (SKILLS_TOOL.parent / sibling).read_bytes(),
            )
        self.env = self.world.env({
            "TESTKIT_DIR": str(support.WORKTREE_ROOT / "ops" / "testkit"),
            "WF_AGENT": AGENT,
        })
        self.skills("add", "onboard", "planted onboard")
        self.assertEqual(self.events(), [], "plant: the registry starts with no events")

    def skills(self, *argv):
        proc = run(
            [sys.executable, str(self.lever), *argv],
            cwd=self.repo, env=self.env, timeout=30,
        )
        self.assertEqual(proc.exit, 0, proc.stdout + proc.stderr)
        return proc

    def store(self):
        found = list(self.world.xdg_state.glob("*/skills.db"))
        self.assertEqual(len(found), 1, f"one planted registry, found {found}")
        return found[0]

    def events(self):
        con = sqlite3.connect(self.store())
        try:
            return con.execute(
                "SELECT skill, event, agent, detail FROM skill_events ORDER BY id"
            ).fetchall()
        finally:
            con.close()

    def run_hook(self, payload, env=None):
        return run(
            [sys.executable, str(HOOK)],
            cwd=self.repo, env=env or self.env,
            stdin=payload if isinstance(payload, str) else json.dumps(payload),
            timeout=30,
        )

    def skill_call(self, name, args=""):
        return {"tool_name": "Skill", "cwd": str(self.repo),
                "tool_input": {"skill": name, "args": args}}


class ASkillCallLogsAUse(_Fixture):
    def test_the_invocation_is_recorded_with_its_arguments(self):
        proc = self.run_hook(self.skill_call("onboard", "session start"))
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout, "", "the hook allows silently")
        rows = self.events()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0], ("onboard", "invoked", AGENT, "session start"))

    def test_no_arguments_records_the_skill_with_a_declared_empty_detail(self):
        proc = self.run_hook(self.skill_call("onboard"))
        self.assertEqual(proc.exit, 0, proc.stderr)
        rows = self.events()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][:2], ("onboard", "invoked"))
        self.assertEqual(rows[0][3], "(no arguments)")

    def test_without_wf_agent_the_hook_signs_as_itself_with_the_lane(self):
        env = {k: v for k, v in self.env.items() if k != "WF_AGENT"}
        proc = self.run_hook(self.skill_call("onboard", "x"), env=env)
        self.assertEqual(proc.exit, 0, proc.stderr)
        rows = self.events()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][2], f"skill-use hook <skill-use@{LANE_NAME}>")


class TheHookNeverBlocks(_Fixture):
    def test_a_skill_the_registry_does_not_know_is_allowed_and_not_logged(self):
        proc = self.run_hook(self.skill_call("no-such-skill", "x"))
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(self.events(), [])

    def test_another_tool_is_allowed_and_not_logged(self):
        proc = self.run_hook({"tool_name": "Bash", "cwd": str(self.repo),
                              "tool_input": {"command": "ls"}})
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(self.events(), [])

    def test_malformed_input_is_allowed(self):
        proc = self.run_hook("not json")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(self.events(), [])

    def test_a_missing_lever_is_allowed(self):
        self.lever.unlink()
        proc = self.run_hook(self.skill_call("onboard", "x"))
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")


class TheHookIsWired(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="skill-use-settings-")
        self.world.enter()
        self.addCleanup(self.world.close)

    def test_settings_bind_the_hook_to_the_skill_tool_before_use(self):
        settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
        rows = settings["hooks"]["PreToolUse"]
        bound = [
            h["command"] for r in rows if r.get("matcher") == "Skill"
            for h in r.get("hooks", [])
        ]
        self.assertEqual(len(bound), 1, f"one Skill row, found {bound}")
        self.assertTrue(bound[0].endswith("ops/devlane/hooks/claude/skill-use.py"), bound[0])


if __name__ == "__main__":
    unittest.main()
