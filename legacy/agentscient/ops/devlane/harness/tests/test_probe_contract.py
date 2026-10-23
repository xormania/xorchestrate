"""Contract tests for probe.py.

The behavioural result APIs are deliberately not guessed here: their return
shape is absent from CONTRACT-probe.py.md.  The documented process boundary,
where an unsuccessful arm becomes "no answer", is tested directly.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
import uuid

from ops.testkit import World

HARNESS = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, HARNESS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load("probe")


class ProbeContractTests(unittest.TestCase):
    def test_verdict_tokens_are_three_distinct_states(self):
        verdicts = (probe.CLEAN, probe.LEAKING, probe.INVALID)
        self.assertEqual(("CLEAN", "LEAKING", "INVALID"), verdicts)
        self.assertEqual(3, len(set(verdicts)))

    def test_run_executes_in_the_requested_environment_and_returns_all_streams(self):
        with World(prefix="probe cwd with spaces ") as world:
            env = world.env({"PROBE_TEST_SENTINEL": "recognisable-environment"})
            script = (
                "import os, pathlib, sys; "
                "data = sys.stdin.read(); "
                "pathlib.Path('ran.receipt').write_text(data); "
                "print(os.environ['PROBE_TEST_SENTINEL'] + ':' + data); "
                "print('recognisable-stderr', file=sys.stderr)"
            )

            rc, stdout, stderr = probe._run(
                [sys.executable, "-c", script],
                env,
                world.cwd,
                stdin="recognisable-stdin",
            )

            self.assertEqual(0, rc)
            self.assertEqual("recognisable-environment:recognisable-stdin\n", stdout)
            self.assertEqual("recognisable-stderr\n", stderr)
            receipt = world.cwd / "ran.receipt"
            self.assertTrue(receipt.is_file())
            self.assertEqual("recognisable-stdin", receipt.read_text(encoding="utf-8"))

    def test_run_maps_good_looking_output_followed_by_nonzero_to_no_answer(self):
        cases = (
            ("Project Instructions (0)\n", 23),
            ("YES\n", 24),
            ("NO\n", 25),
        )
        self.assertEqual(3, len(cases))
        with World() as world:
            for apparent_answer, exit_status in cases:
                with self.subTest(apparent_answer=apparent_answer.strip()):
                    script = (
                        "import pathlib, sys; "
                        f"sys.stdout.write({apparent_answer!r}); "
                        "sys.stdout.flush(); "
                        "pathlib.Path('arm-ran.receipt').write_text('recognisable-arm'); "
                        "sys.stderr.write('teardown failed\\n'); "
                        f"raise SystemExit({exit_status})"
                    )

                    rc, stdout, stderr = probe._run(
                        [sys.executable, "-c", script],
                        world.env(),
                        world.cwd,
                    )

                    receipt = world.cwd / "arm-ran.receipt"
                    self.assertTrue(receipt.is_file())
                    self.assertEqual("recognisable-arm", receipt.read_text(encoding="utf-8"))
                    self.assertIsNone(rc)
                    self.assertEqual(apparent_answer, stdout)
                    self.assertEqual("teardown failed\n", stderr)

    def test_run_reports_no_answer_when_the_arm_never_starts(self):
        missing_command = f"probe-command-that-does-not-exist-{uuid.uuid4().hex}"
        with World() as world:
            self.assertEqual(0, len(list(world.cwd.iterdir())))

            rc, stdout, stderr = probe._run(
                [missing_command],
                world.env(),
                world.cwd,
            )

            self.assertIsNone(rc)
            self.assertEqual("", stdout)
            self.assertIsInstance(stderr, str)
            self.assertNotEqual("", stderr.strip())
            self.assertEqual(0, len(list(world.cwd.iterdir())))

    def test_structural_probe_is_read_only_even_with_personal_extras_present(self):
        with World() as world:
            real_home = world.root / "operator-codex"
            real_home.mkdir()
            world.plant_text(real_home / "auth.json", "recognisable-auth\n")
            planted_hook = world.plant_text(real_home / "hooks.json", "personal-hook\n")
            before = {
                path.relative_to(real_home): (path.is_symlink(), path.read_bytes())
                for path in real_home.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            self.assertEqual(2, len(before))
            self.assertIn(Path("auth.json"), before)
            self.assertIn(Path("hooks.json"), before)
            self.assertEqual(b"personal-hook\n", planted_hook.read_bytes())

            watch = world.watch(real_home)
            probe.structural("codex", env={"CODEX_HOME": str(real_home)})

            after = {
                path.relative_to(real_home): (path.is_symlink(), path.read_bytes())
                for path in real_home.rglob("*")
                if path.is_file() or path.is_symlink()
            }
            self.assertEqual(before, after)
            watch.assert_unchanged(self)

if __name__ == "__main__":
    unittest.main()
