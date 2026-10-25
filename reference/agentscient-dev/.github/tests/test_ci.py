"""Exercise file routing, real Git ranges, and the fail-closed required check."""

import contextlib
import importlib.util
import io
import json
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci", ROOT / ".github/ci.py")
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


class RoutingTests(unittest.TestCase):
    def test_pull_requests_include_stacked_bases_without_widening_push_or_permissions(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        pull_request = workflow["on"]["pull_request"]
        self.assertNotIn("branches", pull_request)
        self.assertNotIn("branches-ignore", pull_request)
        self.assertEqual(["opened", "reopened", "synchronize", "edited"], pull_request["types"])
        self.assertEqual({"branches": ["dev", "master"]}, workflow["on"]["push"])
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        self.assertNotIn("pull_request_target", workflow["on"])

    def test_component_boundaries(self):
        dev = {"live-mcp", "maker-mate", "serena"}
        product = {"symfony", "maker-mate"}
        cases = [
            (["devtools/telemetry.py", "docs/runtime-telemetry.md"], dev),
            (["devtools/tests/test_runtime.py", "devtools/tests/native_fixture.py"], {"live-mcp"}),
            (["devtools/tests/support.py"], dev),
            (["devtools/integration/symfony.py"], {"live-mcp", "maker-mate"}),
            (["devtools/integration/php-runtime.php"], {"live-mcp", *product}),
            (["devtools/integration/serena.py", "devtools/serena/entry.py"], {"live-mcp", "serena"}),
            ([".agents/skills/agentscient-xo/SKILL.md"], dev),
            (["AGENTS.md", ".claude/skills/mate-system-information"], dev),
            (["composer.lock", "config/bundles.php", "src/Kernel.php"], product),
            ([".env.test", "phpunit.dist.xml", "tests/bootstrap.php"], product),
            (["tests/DevelopmentBoundaryTest.php", "public/index.php"], {"symfony"}),
            ([".phpactor.json"], {"serena"}),
            (["ruff.toml"], {"live-mcp"}),
            (["README.md", "docs/tmux-control.md", "devtools/README.md", ".github/CI.md"], set()),
            ([".github/workflows/ci.yml"], set(ci.JOBS)),
            ([".github/actions/php-runtime/action.yml"], set(ci.JOBS)),
            ([".github/ci.py", ".github/ci-paths.json"], set(ci.JOBS)),
            (["new-component/code.py"], set(ci.JOBS)),
            (["devtools/runtime.py", "src/Kernel.php"], set(ci.JOBS)),
            ([], set()),
        ]
        for paths, expected in cases:
            with self.subTest(paths=paths):
                self.assertEqual(ci.select(paths), expected)

    def test_workflow_wires_every_selection_into_the_required_gate(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        jobs = workflow["jobs"]
        self.assertEqual(set(jobs["changes"]["outputs"]), set(ci.JOBS))
        self.assertEqual(set(jobs["lane"]["needs"]), {"changes", *ci.JOBS})
        self.assertEqual(jobs["lane"]["if"], "${{ always() }}")
        for job in ci.JOBS:
            self.assertEqual(jobs[job]["needs"], ["changes"])
            self.assertEqual(jobs[job]["if"], "${{ needs.changes.outputs['" + job + "'] == 'true' }}")
        for event in ("pull_request", "push"):
            self.assertNotIn("paths", workflow["on"][event])
            self.assertNotIn("paths-ignore", workflow["on"][event])

    def test_changes_requires_both_cue_rejection_cases_without_repeating_role_suite(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        steps = workflow["jobs"]["changes"]["steps"]
        setup = next(step for step in steps if step.get("uses", "").startswith("cue-lang/setup-cue@"))
        validation = next(step for step in steps if "python3 -m devtools.consistency" in step.get("run", ""))
        self.assertLess(steps.index(setup), steps.index(validation))
        self.assertEqual("v0.17.1", setup["with"]["version"])
        self.assertEqual("${{ steps.select.outputs['live-mcp'] == 'true' }}", setup["if"])
        self.assertEqual(setup["if"], validation["if"])
        # An explicit binary makes missing/broken CUE fail, rather than skip either test.
        self.assertEqual("cue", validation["env"]["AGENTSCIENT_CUE"])
        lines = validation["run"].splitlines()
        self.assertIn("python3 -m devtools.role_packages --check", lines)
        self.assertIn("python3 -m devtools.configuration --check", lines)
        tests = [shlex.split(line) for line in lines if "-m unittest" in line]
        self.assertEqual(1, len(tests))
        self.assertEqual("PYTHONPATH=devtools/tests:.", tests[0][0])
        self.assertEqual([
            "devtools.tests.test_role_packages.PackageCatalogTests.test_cue_source_and_export_agree_and_invalid_definitions_are_rejected",
            "devtools.tests.test_configuration.ConfigurationTests.test_cue_export_matches_and_rejects_stale_unknown_fields",
            "-v",
        ], tests[0][tests[0].index("unittest") + 1:])

    def test_live_suite_requires_its_real_socket_and_terminal_prerequisites(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        verify = next(step for step in workflow["jobs"]["live-mcp"]["steps"] if step.get("id") == "verify")
        self.assertEqual("1", verify["env"]["AGENTSCIENT_REQUIRE_TMUX"])
        self.assertEqual("1", verify["env"]["AGENTSCIENT_REQUIRE_ABSTRACT_SOCKETS"])
        self.assertIn("python3 -m devtools.tests.run --strict ", verify["run"])
        self.assertEqual([
            "test_role_packages.PackageCatalogTests.test_cue_source_and_export_agree_and_invalid_definitions_are_rejected",
            "test_configuration.ConfigurationTests.test_cue_export_matches_and_rejects_stale_unknown_fields",
        ], re.findall(r"--allow-skip (\S+)", verify["run"]))

    def test_security_lint_and_calibration_are_required_after_installation(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        steps = workflow["jobs"]["changes"]["steps"]
        install = next(step for step in steps if step.get("name") == "Install linter")
        lint = next(step for step in steps if step.get("id") == "lint")
        calibration = next(step for step in steps if ".github/lint_checks.py" in step.get("run", ""))
        self.assertLess(steps.index(install), steps.index(lint))
        self.assertLess(steps.index(lint), steps.index(calibration))
        self.assertEqual(["$RUNNER_TEMP/live-mcp-venv/bin/python", "-m", "ruff", "check", "devtools", ".github"],
                         shlex.split(lint["run"]))
        self.assertEqual(["$RUNNER_TEMP/live-mcp-venv/bin/python", ".github/lint_checks.py"],
                         shlex.split(calibration["run"]))
        for step in (install, lint, calibration):
            self.assertEqual("${{ steps.select.outputs['live-mcp'] == 'true' }}", step["if"])
            self.assertNotIn("continue-on-error", step)

    def test_lane_accepts_only_deliberate_skips_and_successful_selected_jobs(self):
        for selected in ("true", "false"):
            needs = {"changes": {"result": "success", "outputs": dict.fromkeys(ci.JOBS, selected)}}
            expected = "success" if selected == "true" else "skipped"
            needs.update({job: {"result": expected} for job in ci.JOBS})
            with contextlib.redirect_stdout(io.StringIO()):
                ci.gate(needs)
                for job in ("changes", *ci.JOBS):
                    for result in ("failure", "cancelled", "skipped", "success", "", None):
                        if result == needs[job]["result"]:
                            continue
                        broken = {**needs, job: {**needs[job], "result": result}}
                        with self.subTest(selected=selected, job=job, result=result), self.assertRaises(ValueError):
                            ci.gate(broken)
                for output in (None, "", "maybe"):
                    needs["changes"]["outputs"][ci.JOBS[0]] = output
                    with self.assertRaises(ValueError):
                        ci.gate(needs)


class GitRangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--initial-branch=dev")
        self.git("config", "user.name", "CI fixture")
        self.git("config", "user.email", "ci@example.invalid")
        self.write("src/old.php")
        self.write("README.md")
        self.base = self.commit()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path + "\n")

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_pr_uses_its_base_and_tested_merge_not_unrelated_base_changes(self):
        self.git("checkout", "-b", "job")
        self.write("devtools/change.py")
        self.commit()
        self.git("checkout", "dev")
        self.write("src/unrelated.php")
        base = self.commit()
        self.git("merge", "--no-ff", "job", "-m", "PR merge")
        head = self.git("rev-parse", "HEAD")
        paths = ci.changed_paths("pull_request", {"pull_request": {"base": {"sha": base}}}, head, self.root)
        self.assertEqual(paths, ["devtools/change.py"])
        self.assertNotIn("symfony", ci.select(paths))

    def test_stacked_pr_compares_its_parent_and_uses_the_same_required_gate(self):
        self.git("checkout", "-b", "codex/command-structure")
        self.write("devtools/commands.py")
        base = self.commit()
        self.git("checkout", "-b", "codex/role-packages")
        self.write("devtools/role-packages.cue")
        self.commit()
        self.git("checkout", "codex/command-structure")
        self.git("merge", "--no-ff", "codex/role-packages", "-m", "stacked PR merge")
        head = self.git("rev-parse", "HEAD")
        event = {"pull_request": {"base": {"ref": "codex/command-structure", "sha": base}}}
        paths = ci.changed_paths("pull_request", event, head, self.root)
        self.assertEqual(["devtools/role-packages.cue"], paths)
        selected = ci.select(paths)
        self.assertEqual({"live-mcp", "maker-mate", "serena"}, selected)
        outputs = {job: "true" if job in selected else "false" for job in ci.JOBS}
        needs = {"changes": {"result": "success", "outputs": outputs}}
        needs.update({job: {"result": "success" if job in selected else "skipped"} for job in ci.JOBS})
        with contextlib.redirect_stdout(io.StringIO()):
            ci.gate(needs)
            needs["live-mcp"]["result"] = "skipped"
            with self.assertRaises(ValueError):
                ci.gate(needs)

    def test_retargeted_pr_runs_again_and_selects_changes_relative_to_its_new_base(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        self.assertIn("edited", workflow["on"]["pull_request"]["types"])
        self.git("checkout", "-b", "work-order/fixture")
        self.write("devtools/change.py")
        previous_base = self.commit()
        self.git("checkout", "-b", "work-order/fixture-job-1")
        self.write("docs/change.md")
        head = self.commit()
        original = {"pull_request": {"base": {"sha": previous_base}}}
        self.assertEqual(set(), ci.select(ci.changed_paths("pull_request", original, head, self.root)))
        self.git("checkout", "dev")
        self.git("merge", "--no-ff", "work-order/fixture-job-1", "-m", "Retargeted PR merge")
        retargeted = {"action": "edited", "changes": {"base": {"ref": {"from": "work-order/fixture"}}},
                      "pull_request": {"base": {"ref": "dev", "sha": self.base}}}
        paths = ci.changed_paths("pull_request", retargeted, self.git("rev-parse", "HEAD"), self.root)
        self.assertEqual({"devtools/change.py", "docs/change.md"}, set(paths))
        self.assertEqual({"live-mcp", "maker-mate", "serena"}, ci.select(paths))

    def test_push_and_merge_group_include_all_commits_deletions_and_rename_sides(self):
        self.git("mv", "src/old.php", "README-moved.md")
        self.commit()
        self.git("rm", "README.md")
        self.write("devtools/file with\nnewline.py")
        head = self.commit()
        events = {
            "push": {"before": self.base, "after": head},
            "merge_group": {"merge_group": {"base_sha": self.base, "head_sha": head}},
        }
        for name, event in events.items():
            with self.subTest(event=name):
                paths = ci.changed_paths(name, event, head, self.root)
                self.assertEqual(set(paths), {"src/old.php", "README-moved.md", "README.md", "devtools/file with\nnewline.py"})
                self.assertEqual(ci.select(paths), set(ci.JOBS))

    def test_manual_initial_push_and_unavailable_history_select_everything(self):
        self.assertIsNone(ci.changed_paths("workflow_dispatch", {}, self.base, self.root))
        for before in ("0" * 40, "f" * 40):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertIsNone(ci.changed_paths("push", {"before": before, "after": self.base}, self.base, self.root))
        with self.assertRaises(ValueError):
            ci.changed_paths("unknown", {}, self.base, self.root)


if __name__ == "__main__":
    unittest.main()
