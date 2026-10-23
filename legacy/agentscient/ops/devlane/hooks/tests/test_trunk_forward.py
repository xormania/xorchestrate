"""trunk-forward.py: the SessionStart hook that fast-forwards the main checkout's trunk.

LD-38 (2026-09-14): every hook in .claude/settings.json runs from the main
checkout through CLAUDE_PROJECT_DIR, nothing moved that checkout's dev when
a PR landed, and it sat 44 commits behind origin/dev with a stale matrix
and settings. Owner, 2026-09-13: "i'll do options 1 and 2 on next session".
Option 1 is this script: at SessionStart it fetches and fast-forwards the
main checkout's trunk only when the checkout is clean, on the trunk, and
git reports the merge as a fast-forward; otherwise it prints one line
saying why not. It prints the old and new sha, because hooks change under
sessions already running. It never blocks a session start: exit 0 always,
and a fetch it cannot make is could-not-look, said on the line.

Hermetic: a bare remote, the main clone behind it, a second clone that
lands commits. The real script runs as a subprocess with CLAUDE_PROJECT_DIR
naming the main clone.

Written by hand with the code it tests under wall grant 223 (owner,
2026-09-14: "blanket hand grant ok'd - get T5,11, and 13 don"); the second
producer is absent and this docstring says so.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import support

from ops.testkit import World, run

HOOK = support.CLAUDE_DIR / "trunk-forward.py"
REPO = Path(__file__).resolve().parents[4]
LANE_CONF = "LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\nRELEASE_BRANCH='master'\n"


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.assertTrue(HOOK.is_file(), f"absent: {HOOK}")
        self.world = World(prefix="trunk-forward-")
        self.addCleanup(self.world.close)
        self.tmp = self.world.root
        self.env = self.world.env({
            "TESTKIT_DIR": str(support.WORKTREE_ROOT / "ops" / "testkit"),
        })
        seed = self.tmp / "seed"
        seed.mkdir()
        support.git(seed, self.env, "-c", "init.defaultBranch=dev", "init", "-q")
        support.configure_identity(seed, self.env)
        (seed / "ops").mkdir()
        self.world.plant_text(seed / "ops" / "lane.conf", LANE_CONF)
        self.world.plant_text(seed / "README.md", "seed\n")
        support.git(seed, self.env, "add", "-A")
        support.git(seed, self.env, "commit", "-q", "-m", "seed")
        self.bare = self.tmp / "origin.git"
        support.git(self.tmp, self.env, "clone", "-q", "--bare", str(seed), str(self.bare))
        self.main = self.tmp / "main"
        support.git(self.tmp, self.env, "clone", "-q", str(self.bare), str(self.main))
        support.configure_identity(self.main, self.env)
        self.ahead = self.tmp / "ahead"
        support.git(self.tmp, self.env, "clone", "-q", str(self.bare), str(self.ahead))
        support.configure_identity(self.ahead, self.env)

    def land(self, message="landed"):
        support.git(self.ahead, self.env, "commit", "-q", "--allow-empty", "-m", message)
        support.git(self.ahead, self.env, "push", "-q", "origin", "dev")

    def head(self, repo):
        return support.git(repo, self.env, "rev-parse", "HEAD").stdout.strip()

    def run_hook(self, project_dir=None, source="startup"):
        env = dict(self.env)
        env["CLAUDE_PROJECT_DIR"] = str(project_dir or self.main)
        payload = json.dumps({"hook_event_name": "SessionStart", "source": source, "cwd": str(self.main)})
        return run(
            [sys.executable, str(HOOK)], stdin=payload, cwd=self.tmp, env=env,
            timeout=120,
        )

    def one_line(self, proc):
        self.assertEqual(proc.exit, 0, proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertTrue(lines[0].startswith("trunk-forward: "), lines[0])
        return lines[0]

class TheCleanTrunkMoves(_Fixture):
    def test_a_clean_trunk_behind_its_remote_is_fast_forwarded_and_both_shas_are_printed(self):
        old = self.head(self.main)
        self.land("one")
        self.land("two")
        line = self.one_line(self.run_hook())
        new = self.head(self.main)
        self.assertEqual(new, self.head(self.ahead))
        self.assertNotEqual(old, new)
        self.assertIn(f"{old[:8]}..{new[:8]}", line)
        self.assertIn("2 commits", line)
        self.assertEqual(support.git(self.main, self.env, "status", "--porcelain").stdout, "")

    def test_a_trunk_at_its_remote_prints_the_sha_and_moves_nothing(self):
        old = self.head(self.main)
        line = self.one_line(self.run_hook())
        self.assertEqual(self.head(self.main), old)
        self.assertIn(f"already at {old[:8]}", line)

    def test_the_project_dir_is_the_checkout_the_hook_moves_not_the_cwd(self):
        self.land("one")
        old = self.head(self.main)
        self.one_line(self.run_hook())
        self.assertNotEqual(self.head(self.main), old)


class TheTrunkStaysWhenItMust(_Fixture):
    def test_uncommitted_changes_hold_the_trunk_and_the_line_says_so(self):
        self.world.plant_text(self.main / "README.md", "edited\n")
        self.land("one")
        old = self.head(self.main)
        line = self.one_line(self.run_hook())
        self.assertEqual(self.head(self.main), old)
        self.assertIn("uncommitted", line)
        self.assertIn("not moved", line)

    def test_another_branch_checked_out_is_not_moved(self):
        support.git(self.main, self.env, "checkout", "-q", "-b", "lane/session-x")
        self.land("one")
        old = self.head(self.main)
        line = self.one_line(self.run_hook())
        self.assertEqual(self.head(self.main), old)
        self.assertIn("lane/session-x", line)
        self.assertIn("not moved", line)

    def test_a_diverged_trunk_is_not_moved_and_no_commit_is_made(self):
        support.git(self.main, self.env, "commit", "-q", "--allow-empty", "-m", "local only")
        self.land("one")
        old = self.head(self.main)
        line = self.one_line(self.run_hook())
        self.assertEqual(self.head(self.main), old)
        self.assertIn("diverged", line)
        self.assertIn("not moved", line)

    def test_an_unreachable_remote_is_could_not_look_and_never_a_pass(self):
        support.git(self.main, self.env, "remote", "set-url", "origin", str(self.tmp / "gone.git"))
        old = self.head(self.main)
        line = self.one_line(self.run_hook())
        self.assertEqual(self.head(self.main), old)
        self.assertIn("fetch", line)
        self.assertIn("could not look", line)

    def test_without_a_lane_conf_the_trunk_is_unknown_and_nothing_moves(self):
        (self.main / "ops" / "lane.conf").unlink()
        support.git(self.main, self.env, "commit", "-q", "-am", "no conf")
        support.git(self.main, self.env, "push", "-q", "origin", "dev")
        old = self.head(self.main)
        line = self.one_line(self.run_hook())
        self.assertEqual(self.head(self.main), old)
        self.assertIn("TRUNK_BRANCH", line)
        self.assertIn("not moved", line)

    def test_a_directory_that_is_not_a_checkout_says_so(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        line = self.one_line(self.run_hook(project_dir=plain))
        self.assertIn("not a git checkout", line)


class TheSettingsBindIt(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="trunk-forward-settings-")
        self.world.enter()
        self.addCleanup(self.world.close)

    def test_settings_bind_the_hook_at_every_session_start_before_the_engine(self):
        settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
        rows = [r for r in settings["hooks"]["SessionStart"] if not r.get("matcher")]
        self.assertEqual(len(rows), 1, f"one unmatched SessionStart row, found {len(rows)}")
        commands = [h["command"] for h in rows[0]["hooks"]]
        forward = [c for c in commands if c.endswith("ops/devlane/hooks/claude/trunk-forward.py")]
        engine = [c for c in commands if c.endswith("ops/devlane/hooks/claude/engine.py")]
        self.assertEqual(len(forward), 1, commands)
        self.assertEqual(len(engine), 1, commands)
        self.assertLess(commands.index(forward[0]), commands.index(engine[0]), commands)


if __name__ == "__main__":
    unittest.main()
