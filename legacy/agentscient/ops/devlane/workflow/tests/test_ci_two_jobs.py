"""CI routing checks real diffs; acceptance requires the selected executions."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from ops.devlane.workflow.checks.ci_contexts import workflow_job_names
from ops.devlane.workflow.checks.ci_runners import RUNS_ON
from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / ".github/ci_scope.py"
SPEC = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
SCOPE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCOPE)


class WorkflowGate(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((ROOT / ".github/workflows/ci.yml").read_text())

    def test_workflows_connect_selected_lanes_to_one_acceptance_result(self):
        jobs = self.doc["jobs"]
        self.assertEqual(workflow_job_names(ROOT), ["lane"])
        for event in ("pull_request", "push", "merge_group"):
            self.assertIn(event, self.doc["on"])
            self.assertNotIn("paths", self.doc["on"][event])
            self.assertNotIn("paths-ignore", self.doc["on"][event])
        self.assertEqual(jobs["scope"]["steps"][-1]["run"], "python3 .github/ci_scope.py")
        for key, output in (("dev-lane", "dev"), ("prod-lane", "prod"), ("symfony-integration", "symfony")):
            self.assertEqual(jobs[key]["needs"], "scope")
            self.assertEqual(jobs[key]["if"], "${{ needs.scope.outputs." + output + " == 'true' }}")
        self.assertEqual(set(jobs["lane"]["needs"]), {"scope", "dev-lane", "prod-lane", "symfony-integration"})
        self.assertEqual(jobs["lane"]["if"], "${{ always() }}")
        for key in ("scope", "dev-lane", "symfony-integration", "lane"):
            self.assertEqual(jobs[key]["runs-on"], RUNS_ON)
            self.assertNotIn("continue-on-error", jobs[key])
            for step in jobs[key]["steps"]:
                self.assertNotIn("continue-on-error", step)
        self.assertEqual(jobs["prod-lane"]["uses"], "./.github/workflows/ci-prod-lane.yml")
        prod = json.loads((ROOT / ".github/workflows/ci-prod-lane.yml").read_text())
        self.assertEqual(set(prod["on"]), {"workflow_call", "workflow_dispatch"})
        self.assertNotIn("ops.", json.dumps(prod))
        self.assertNotIn("ops/", json.dumps(prod))
        integration = jobs["symfony-integration"]
        self.assertIn("ops/devlane/providers/symfony_smoke.py", json.dumps(integration))
        for job in (prod["jobs"]["php"], integration):
            self.assertTrue(any(s.get("uses") == "./.github/actions/php-runtime" for s in job["steps"]))
        commands = "\n".join(step.get("run", "") for step in prod["jobs"]["php"]["steps"])
        for command in ("composer validate --strict", "composer install",
                        "php bin/console lint:container", "vendor/bin/phpunit"):
            self.assertIn(command, commands)

    def test_gate_executes_and_rejects_failed_cancelled_or_missing_selected_jobs(self):
        step = self.doc["jobs"]["lane"]["steps"][0]
        with World(host_path=False, ambient_tools=("bash", "tee")) as world:
            def invoke(dev, prod, dev_result, prod_result, scope="success", symfony="false", symfony_result="skipped"):
                env = world.env({
                    "REVISION": "a" * 40, "SCOPE_RESULT": scope,
                    "DEV_SELECTED": dev, "PROD_SELECTED": prod,
                    "DEV_RESULT": dev_result, "PROD_RESULT": prod_result,
                    "SYMFONY_SELECTED": symfony, "SYMFONY_RESULT": symfony_result,
                    "GITHUB_STEP_SUMMARY": str(world.cwd / "summary"),
                })
                return run(("bash", "-e", "-o", "pipefail", "-c", step["run"]),
                           cwd=world.cwd, env=env, timeout=10).exit

            for dev, prod in (("false", "false"), ("true", "false"),
                              ("false", "true"), ("true", "true")):
                dr = "success" if dev == "true" else "skipped"
                pr = "success" if prod == "true" else "skipped"
                self.assertEqual(invoke(dev, prod, dr, pr), 0)
            for result in ("failure", "cancelled", "skipped", ""):
                self.assertNotEqual(invoke("true", "false", result, "skipped"), 0)
                self.assertNotEqual(invoke("false", "true", "skipped", result), 0)
                self.assertNotEqual(invoke("true", "false", "success", "skipped", symfony="true", symfony_result=result), 0)
            for result in ("failure", "cancelled", "skipped", ""):
                self.assertNotEqual(invoke("false", "false", "skipped", "skipped", result), 0)
            self.assertNotEqual(invoke("", "", "skipped", "skipped"), 0)
            self.assertNotEqual(invoke("false", "false", "failure", "skipped"), 0)
            self.assertEqual(invoke("true", "false", "success", "skipped", symfony="true", symfony_result="success"), 0)
            self.assertNotEqual(invoke("false", "false", "skipped", "skipped", symfony_result="failure"), 0)


class ScopeSelection(unittest.TestCase):
    def test_lane_selection_is_by_files_not_branch_names_or_prose(self):
        cases = [
            (["src/Kernel.php", "composer.lock", ".env.dev"], False, True),
            (["ops/tool.py", ".dev/settings.json", "ruff.toml"], True, False),
            (["src/Kernel.php", "ops/tool.py"], True, True),
            (["README.md", "AGENTS.md", "ops/README.md", "docs/plan.md"], False, False),
            ([".github/ci_scope.py"], True, True),
            ([".github/workflows/ci.yml"], True, True),
            ([".github/workflows/ci-prod-lane.yml"], False, True),
            (["new-unclassified-file"], True, True),
            (["docs/contract.cue"], True, True),
            ([], False, False),
        ]
        for paths, dev, prod in cases:
            with self.subTest(paths=paths):
                self.assertEqual(SCOPE.select(paths), {"dev": dev, "prod": prod, "symfony": prod})

        for path in ("ops/conductor/tools/project_work.py",
                     "ops/conductor/tools/project_providers/symfony.py",
                     "ops/devlane/providers/symfony_smoke.py"):
            with self.subTest(integration=path):
                self.assertEqual(SCOPE.select([path]), {"dev": True, "prod": False, "symfony": True})

    def test_real_event_diffs_include_deletions_and_both_sides_of_renames(self):
        with World(host_path=False, ambient_tools=("git", "python3")) as world:
            env = world.env({
                "GIT_AUTHOR_NAME": "CI fixture", "GIT_AUTHOR_EMAIL": "ci@example.invalid",
                "GIT_COMMITTER_NAME": "CI fixture", "GIT_COMMITTER_EMAIL": "ci@example.invalid",
            })

            def git(*args):
                result = run(("git", *args), cwd=world.cwd, env=env, timeout=10)
                self.assertEqual(result.exit, 0, result.stderr)
                return result.stdout.strip()

            def commit():
                git("add", ".")
                git("commit", "-qm", "fixture")
                return git("rev-parse", "HEAD")

            def route(name, event, expected_revision=None):
                world.plant_text("event.json", json.dumps(event))
                result = run(("python3", str(SCRIPT)), cwd=world.cwd,
                             env={**env, "GITHUB_EVENT_NAME": name,
                                  "GITHUB_EVENT_PATH": str(world.root / "event.json"),
                                  "GITHUB_SHA": expected_revision or git("rev-parse", "HEAD"),
                                  "GITHUB_OUTPUT": str(world.root / "outputs"),
                                  "GITHUB_STEP_SUMMARY": str(world.root / "summary")},
                             timeout=10)
                return result

            git("init", "-q")
            world.plant_text("cwd/src/Kernel.php", "original\n")
            world.plant_text("cwd/ops/tool.py", "original\n")
            base = commit()
            git("checkout", "-qb", "product")
            world.plant_text("cwd/src/Kernel.php", "product change\n")
            product = commit()
            git("checkout", "-qb", "upstream", base)
            world.plant_text("cwd/ops/tool.py", "upstream change\n")
            upstream = commit()
            git("merge", "--no-edit", product)

            # PR and merge-queue candidates compare the tested merge to its base,
            # without attributing already-landed dev work to a prod-only change.
            for name, event in (
                ("pull_request", {"pull_request": {"base": {"sha": upstream}}}),
                ("merge_group", {"merge_group": {"base_sha": upstream}}),
            ):
                result = route(name, event)
                self.assertEqual(result.exit, 0, result.stderr)
                measured = json.loads(result.stdout)
                self.assertEqual(measured["changed_files"], ["src/Kernel.php"])
                self.assertEqual((measured["dev"], measured["prod"]), (False, True))

            result = route("push", {"before": base})
            self.assertEqual(result.exit, 0, result.stderr)
            self.assertEqual(set(json.loads(result.stdout)["changed_files"]),
                             {"ops/tool.py", "src/Kernel.php"})
            before_move = git("rev-parse", "HEAD")
            git("mv", "src/Kernel.php", "ops/moved.php")
            commit()
            measured = json.loads(route("push", {"before": before_move}).stdout)
            self.assertEqual((measured["dev"], measured["prod"]), (True, True))
            self.assertEqual(set(measured["changed_files"]), {"src/Kernel.php", "ops/moved.php"})
            before_delete = git("rev-parse", "HEAD")
            git("rm", "ops/moved.php")
            commit()
            measured = json.loads(route("push", {"before": before_delete}).stdout)
            self.assertEqual((measured["dev"], measured["prod"]), (True, False))
            self.assertNotEqual(route("push", {"before": "f" * 40}).exit, 0)
            self.assertNotEqual(route("push", {"before": base}, "f" * 40).exit, 0)
