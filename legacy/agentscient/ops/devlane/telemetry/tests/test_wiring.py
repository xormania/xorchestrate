"""Execute the published review recipes; runtime wire semantics live in breaker tests.

Wording and Markdown layout are not a protocol. These integration cases prove
the launch/PID binding, stream selection and CLI arguments the examples promise.
"""

import os
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[4]
DOC = REPO / "ops/process/cross-review.md"
BREAKER = REPO / "ops/devlane/telemetry/breaker.py"


class ReviewExamples(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="review-example-", host_path=False,
                           ambient_tools=("bash", "nohup", "touch", "find", "sort", "tail", "cut", "sleep"))
        self.addCleanup(self.world.close)
        self.snapshot = self.world.path("snapshot with spaces")
        self.snapshot.mkdir()

    def example(self, marker):
        blocks = [block for block in re.findall(r"```sh\s*\n(.*?)```", DOC.read_text(), re.DOTALL)
                  if marker in block]
        self.assertEqual(len(blocks), 1, f"one runnable example containing {marker!r}")
        return blocks[0]

    def shell(self, script):
        result = run(["bash", "-c", script], cwd=self.snapshot,
                     env=self.world.env({"SNAP": str(self.snapshot)}), timeout=5)
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)
        return result

    def test_launch_binds_the_reviewer_pid_and_passes_the_prompt_file(self):
        prompt = "Review only. Literal `touch injected` and $(touch injected).\n"
        self.world.plant_text(self.snapshot / "prompt.txt", prompt)
        self.world.tools.plant("grok", stdout="review complete\n")
        self.shell(self.example("nohup grok") + '\nwait "$RPID"\nprintf "%s" "$RPID" > reviewer.pid\n')
        calls = self.world.tools.calls("grok")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["pid"], int((self.snapshot / "reviewer.pid").read_text()))
        self.assertEqual(calls[0]["cwd"], str(self.snapshot))
        self.assertEqual(calls[0]["argv"], ["--prompt-file", "prompt.txt", "--output-format", "plain", "--max-turns", "40"])
        self.assertTrue((self.snapshot / "launched").is_file())
        self.assertEqual((self.snapshot / "prompt.txt").read_text(), prompt)
        self.assertEqual((self.snapshot / "review.txt").read_text(), "review complete\n")
        self.assertFalse((self.snapshot / "injected").exists())

    def battery(self):
        self.world.tools.plant("python3")
        self.world.plant_text(self.snapshot / "launched", "")
        os.utime(self.snapshot / "launched", (1000, 1000))
        store = self.world.home / ".grok/sessions"
        store.mkdir(parents=True)
        return store

    def test_battery_selects_the_newest_matching_stream_and_arms_real_cli_flags(self):
        store = self.battery()
        selected = None
        for name, timestamp in (("stale/updates.jsonl", 999), ("older/updates.jsonl", 1001),
                                ("new session/updates.jsonl", 1002), ("decoy/events.jsonl", 1003)):
            path = self.world.plant_text(store / name, "")
            os.utime(path, (timestamp, timestamp))
            if name.startswith("new session/"):
                selected = path
        self.shell("RPID=2147483647\n" + self.example("STORE=") + "\nwait\n")
        calls = self.world.tools.calls("python3")
        self.assertEqual(len(calls), 1)
        argv = calls[0]["argv"]
        self.assertEqual(argv[:2], ["ops/devlane/telemetry/breaker.py", str(selected)])
        self.assertEqual(argv[argv.index("--pid") + 1], "2147483647")
        for flag in ("--cap", "--cap-out", "--stall", "--size-mb"):
            self.assertGreater(float(argv[argv.index(flag) + 1]), 0, flag)
        self.assertIn("--terminate", argv)
        self.assertNotIn("--disable", argv)
        self.assertEqual(argv[argv.index("--tripped-file") + 1], "TRIPPED.md")
        # Exercise argparse and a real one-pass battery, not a second flag parser.
        result = run([sys.executable, str(BREAKER), *argv[1:], "--once"],
                     cwd=self.snapshot, env=self.world.env(), timeout=5)
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)

    def test_battery_stops_discovery_when_the_reviewer_dies_without_a_stream(self):
        self.battery()
        self.shell("RPID=2147483647\n" + self.example("STORE=") + "\nwait\n")
        self.assertEqual(self.world.tools.calls("python3"), [])
