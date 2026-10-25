"""Policy corpus and process boundary for conductor-enforce.py."""

import importlib.util
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

HOOK = Path(__file__).resolve().parents[1] / "claude" / "conductor-enforce.py"
TIMEOUT = 120


def load_staged(world):
    staged = world.stage(HOOK, siblings=("command_shape.py",))
    shape_spec = importlib.util.spec_from_file_location(
        "command_shape", staged.with_name("command_shape.py")
    )
    shape = importlib.util.module_from_spec(shape_spec)
    previous = sys.modules.get("command_shape")
    sys.modules["command_shape"] = shape
    shape_spec.loader.exec_module(shape)
    spec = importlib.util.spec_from_file_location("conductor_enforce_testkit", staged)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return staged, module, previous


class ConductorPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = World(prefix="conductor-policy-")
        cls.world.enter()
        cls.staged, cls.mod, cls.previous_shape = load_staged(cls.world)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("conductor_enforce_testkit", None)
        if cls.previous_shape is None:
            sys.modules.pop("command_shape", None)
        else:
            sys.modules["command_shape"] = cls.previous_shape
        cls.world.close()

    @staticmethod
    def bash(command):
        return {"tool_name": "Bash", "tool_input": {"command": command}}

    def test_agent_is_denied(self):
        self.assertEqual(
            self.mod.deny_reason({"tool_name": "Agent"}),
            self.mod.AGENT_REASON,
        )

    def test_each_denied_git_subcommand_is_denied(self):
        for subcommand in self.mod.POLICY["deny_git"]:
            with self.subTest(subcommand=subcommand):
                self.assertEqual(
                    self.mod.deny_reason(self.bash(f"git {subcommand}")),
                    self.mod.INVESTIGATION_REASON,
                )

    def test_each_allowed_git_subcommand_is_allowed(self):
        for subcommand in self.mod.POLICY["allow_git"]:
            with self.subTest(subcommand=subcommand):
                self.assertIsNone(
                    self.mod.deny_reason(self.bash(f"git {subcommand}"))
                )

    def test_structured_pr_view_is_denied_but_plain_view_is_allowed(self):
        self.assertEqual(
            self.mod.deny_reason(self.bash("gh pr view --json state")),
            self.mod.INVESTIGATION_REASON,
        )
        self.assertIsNone(
            self.mod.deny_reason(self.bash("gh pr view 64"))
        )

    def test_gh_api_is_denied(self):
        self.assertEqual(
            self.mod.deny_reason(self.bash("gh api repos/o/r")),
            self.mod.INVESTIGATION_REASON,
        )

    def test_each_lever_anywhere_overrides_a_denied_git_subcommand(self):
        for lever in self.mod.POLICY["allow_anywhere"]:
            with self.subTest(lever=lever):
                self.assertIsNone(
                    self.mod.deny_reason(
                        self.bash(f"git log --oneline && {lever} task")
                    )
                )

    def test_fable_dispatch_is_allowed_with_a_denied_git_subcommand(self):
        self.assertIsNone(
            self.mod.deny_reason(
                self.bash("fable-dispatch.sh plan && git diff")
            )
        )

    def test_grep_over_a_tracked_file_is_denied(self):
        self.assertEqual(
            self.mod.deny_reason(
                self.bash("grep conductor AGENTS.md")
            ),
            self.mod.INVESTIGATION_REASON,
        )

    def test_cat_over_a_job_path_is_allowed(self):
        self.assertIsNone(
            self.mod.deny_reason(
                self.bash("cat /tmp/scratchpad/jobs/123/result.txt")
            )
        )

    def test_unrelated_tool_is_allowed(self):
        self.assertIsNone(self.mod.deny_reason({"tool_name": "Read"}))


class ConductorProcess(unittest.TestCase):
    def test_malformed_stdin_exits_zero_and_prints_nothing(self):
        with World(prefix="conductor-process-") as world:
            script = world.stage(HOOK, siblings=("command_shape.py",))
            result = run(
                [sys.executable, str(script)],
                cwd=world.cwd,
                env=world.env(),
                stdin="{not json",
                timeout=TIMEOUT,
            )
            self.assertEqual(result.exit, 0, result.stderr)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
