"""The commit-msg hook refuses a bad message and never an environment.

Its two paths are opposite by design and both are pinned here, because
either is easy to reverse by accident: refusing when the checker is
absent would stop every commit in a clone that merely lacks it, and
passing a bad message would make the hook decorative.
"""

import unittest

# Import the module and inherit only its fixture; the crossing-recorder
# cases belong to test_hooks and must not run again as commit-msg cases.
import test_hooks

from ops.testkit import World, run

SPLIT = "subject\n\nbody.\n\nSource: original\n\nCo-Authored-By: A <a@x>\n"
JOINED = "subject\n\nbody.\n\nSource: original\nCo-Authored-By: A <a@x>\n"
TIMEOUT = 120


class TheCommitMsgGate(test_hooks._HookFixture):
    def setUp(self):
        self.world = World(prefix="commit-msg-gate-")
        self.addCleanup(self.world.close)
        self.tmp = self.world.root
        self.home = self.world.home

    def env_for(self, wf_agent=test_hooks.WF, user="tester"):
        extra = {}
        if wf_agent is not None:
            extra["WF_AGENT"] = wf_agent
        if user is not None:
            extra["USER"] = user
        return self.world.env(extra)

    def run_cmd(self, argv, cwd, env=None, expect=0):
        result = run(
            argv,
            cwd=cwd,
            env=env if env is not None else self.env_for(),
            timeout=TIMEOUT,
        )
        if expect is not None:
            self.assertEqual(
                result.exit,
                expect,
                f"command {argv!r} in {cwd} exited {result.exit} "
                f"(wanted {expect})\nstdout: {result.stdout}\n"
                f"stderr: {result.stderr}",
            )
        return result

    def prepared(self, with_checker):
        self.require_sources()
        repo = self.make_repo("gate")
        self.install(repo)
        checker = repo / "ops" / "devlane" / "workflow" / "checks" / "commit_trailers.py"
        self.assertTrue(checker.is_file(),
                        "INVALID: staging did not place the checker")
        if not with_checker:
            checker.unlink()
            self.assertFalse(checker.is_file(),
                             "INVALID: the checker was not removed")
        return repo

    def attempt(self, repo, message, content):
        self.world.plant_text(repo / "f.txt", content)
        self.assertEqual((repo / "f.txt").read_text(encoding="utf-8"), content,
                         "INVALID: the change did not land")
        msg = repo / "m.txt"
        self.world.plant_text(msg, message)
        self.assertEqual(msg.read_text(encoding="utf-8"), message,
                         "INVALID: the message did not land")
        self.git(repo, "add", "f.txt")
        proc = self.run_cmd(
            ["git", "commit", "-F", str(msg)], repo, env=self.env_for(), expect=None
        )
        msg.unlink()
        return proc

    def count(self, repo):
        out = self.run_cmd(
            ["git", "rev-list", "--count", "HEAD"], repo, expect=None
        )
        return int(out.stdout.strip() or 0)

    def test_a_split_block_warns_but_commits(self):
        repo = self.prepared(with_checker=True)
        before = self.count(repo)
        proc = self.attempt(repo, SPLIT, "one\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.count(repo), before + 1)
        self.assertIn("Diagnostic:", proc.stderr)

    def test_a_joined_block_commits(self):
        repo = self.prepared(with_checker=True)
        before = self.count(repo)
        proc = self.attempt(repo, JOINED, "two\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.count(repo), before + 1)

    def test_the_two_messages_differ_only_by_the_blank_line(self):
        """Without this the pair above proves nothing."""
        self.assertEqual(SPLIT.replace("\n\nCo-Authored-By", "\nCo-Authored-By"),
                         JOINED)

    def test_a_missing_checker_warns_and_lets_the_commit_through(self):
        """An environment problem must not stop work; CI is the gate."""
        repo = self.prepared(with_checker=False)
        before = self.count(repo)
        proc = self.attempt(repo, SPLIT, "three\n")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.count(repo), before + 1)
        self.assertIn("trailer checker unavailable", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
