"""BDD traceability cases not already proved by the hook contract suite."""

import json
import unittest
from types import MethodType

import test_hooks as hook_contract

from ops.testkit import World, run

TIMEOUT = 120


def env_for(fixture, wf_agent=hook_contract.WF, user="tester"):
    extra = {}
    if wf_agent is not None:
        extra["WF_AGENT"] = wf_agent
    if user is not None:
        extra["USER"] = user
    return fixture.world.env(extra)


def run_cmd(fixture, argv, cwd, env=None, expect=0):
    result = run(
        argv,
        cwd=cwd,
        env=env if env is not None else fixture.env_for(),
        timeout=TIMEOUT,
    )
    if expect is not None:
        fixture.assertEqual(
            result.exit,
            expect,
            f"command {argv!r} in {cwd} exited {result.exit} "
            f"(wanted {expect})\nstdout: {result.stdout}\n"
            f"stderr: {result.stderr}",
        )
    return result


class MultiWorktreeScenarios(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="bdd-traceability-")
        self.addCleanup(self.world.close)
        self.fixture = hook_contract.HookContractTest(
            "test_h3_checkout_appends_one_branch_entry"
        )
        self.fixture.world = self.world
        self.fixture.tmp = self.world.root
        self.fixture.home = self.world.home
        self.fixture.env_for = MethodType(env_for, self.fixture)
        self.fixture.run_cmd = MethodType(run_cmd, self.fixture)

    def test_a_file_checkout_is_not_a_crossing(self):
        """Scenario: a file checkout is not a crossing"""
        h = self.fixture
        h.require_sources()
        repo = h.make_repo("file-checkout")
        h.install(repo)

        stream_before = h.stream_lines(repo)
        tracked = repo / "file.txt"
        self.world.plant_text(tracked, "dirty replacement\n")
        self.assertEqual(tracked.read_text(), "dirty replacement\n")
        self.assertIn("file.txt", h.git(repo, "status", "--short").stdout)

        h.git(repo, "checkout", "--", "file.txt")

        self.assertEqual(tracked.read_text(), "one\n")
        self.assertEqual(
            h.stream_lines(repo),
            stream_before,
            "a file checkout was recorded as a branch crossing",
        )

    def test_commits_from_both_worktrees_name_their_worktrees(self):
        """Scenario: a commit is recorded from whichever worktree made it"""
        h = self.fixture
        h.require_sources()
        repo = h.make_repo("primary-clone")
        h.git(repo, "branch", "linked")
        h.install(repo)

        linked = h.tmp / "second-tree"
        h.git(repo, "worktree", "add", str(linked), "linked")
        before = h.stream_lines(repo)

        primary_sha = h.commit_change(repo, "primary\n", "primary commit")
        linked_sha = h.commit_change(linked, "linked\n", "linked commit")

        after = h.stream_lines(repo)
        self.assertEqual(after[: len(before)], before)
        added = after[len(before):]
        self.assertEqual(
            len(added),
            2,
            f"two commits should append two records, observed {added!r}",
        )
        entries = [h.parse_entry(line) for line in added]
        self.assertEqual([entry["kind"] for entry in entries], ["head", "head"])
        self.assertEqual(
            [entry["worktree"] for entry in entries],
            ["primary-clone", "second-tree"],
        )
        self.assertIn(primary_sha, entries[0]["what"])
        self.assertIn(linked_sha, entries[1]["what"])

    def test_a_unicode_line_separator_in_a_branch_stays_one_json_object(self):
        """Scenario: control characters cannot corrupt the stream"""
        h = self.fixture
        h.require_sources()
        repo = h.make_repo("branch-controls")
        branch = "line\u2028separator"
        self.assertIn("\u2028", branch, "the newline-adjacent plant did not land")
        h.git(repo, "branch", branch)
        h.install(repo)

        before = h.stream_lines(repo)
        h.git(repo, "checkout", "-q", branch)
        raw = h.stream_path(repo).read_text()
        records = [line for line in raw.split("\n") if line]
        self.assertEqual(
            len(records),
            len(before) + 1,
            f"one checkout must add one LF-delimited JSON object: {raw!r}",
        )
        entry = json.loads(records[-1])
        self.assertIsInstance(entry, dict)
        self.assertIn(branch, entry["what"])


if __name__ == "__main__":
    unittest.main()
