"""PR02 execution and false-proof cases; reuse: testkit.world and testkit.run."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from ops.devlane.workflow.checks import ci
from ops.testkit import Check, World, run

REPO = "example/project"
TREE, BASE, HEAD, MERGE, CANONICAL = (char * 40 for char in "abcde")


class EvidenceApi:
    def __init__(self, checks):
        inputs = {"tree": TREE, "git": {"canonical": CANONICAL}, "tools": "fixture"}
        self.key = ci.digest(inputs)
        self.artifact = {"id": 1, "name": f"ci-proof-lane-{self.key}-11-1", "expired": False,
                         "workflow_run": {"id": 11}}
        self.proof = {"schema": ci.SCHEMA, "key": self.key, "inputs": inputs,
                      "repository": REPO, "kind": "lane", "run_id": 11, "attempt": 1,
                      "source_sha": HEAD, "event_revision": MERGE, "tested_revision": CANONICAL,
                      "tree": TREE, "parents": [BASE, HEAD], "plan": sorted(c.name for c in checks),
                      "results": [{"name": c.name, "mode": "executed", "exit": 0, "seconds": 10} for c in checks]}
        self.source = {"path": ci.WORKFLOW, "status": "completed", "conclusion": "failure",
                       "head_sha": HEAD, "event": "pull_request", "repository": {"full_name": REPO},
                       "head_repository": {"full_name": REPO}}
        self.commit = {"tree": {"sha": TREE}, "parents": [{"sha": BASE}, {"sha": HEAD}]}
        self.job = {"name": "lane", "status": "completed", "conclusion": "failure",
                    "html_url": "https://github.com/example/project/actions/runs/11/job/22"}
        self.jobs = [self.job]
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        if path.startswith("/actions/artifacts?"):
            return {"artifacts": [self.artifact]}
        if path.endswith("/jobs?per_page=100"):
            return {"jobs": self.jobs}
        if path.startswith("/git/commits/"):
            return self.commit
        return self.source

    def receipt(self, artifact):
        self.calls.append(("download", artifact["id"]))
        return self.proof


class ToolPreparation(unittest.TestCase):
    def test_missing_or_nonfunctional_bubblewrap_refuses_before_suites(self):
        with World(host_path=False) as world:
            for name in ("git", "python3", "bash", "ruff", "rg", "cue", "jq", "tmux"):
                world.tools.plant(name, stdout="fixture version\n")
            with self.assertRaisesRegex(ValueError, "bwrap"):
                ci.toolchain(world.cwd, world.env(), "local")
            world.tools.plant("bwrap", responses=[{"match": ["--version"], "stdout": "fixture version\n"},
                                                  {"exit": 1}])
            with self.assertRaisesRegex(ValueError, "bwrap"):
                ci.toolchain(world.cwd, world.env(), "local")


class ReuseEvidence(unittest.TestCase):
    def setUp(self):
        self.checks = [Check("one", ("one",), 10), Check("two", ("two",), 10)]
        self.api = EvidenceApi(self.checks)

    def read(self, **kwargs):
        return ci.reusable(self.api, key=self.api.key, checks=self.checks, kind="lane",
                           repository=REPO, current_run=kwargs.get("run", 12),
                           current_attempt=kwargs.get("attempt", 1))

    def test_successful_original_checks_survive_a_failed_companion_and_retry(self):
        self.api.proof["results"][1]["exit"] = 1
        found = self.read()
        self.assertEqual(set(found), {"one"})
        self.assertEqual(found["one"]["url"], self.api.job["html_url"])
        self.assertEqual(set(self.read(run=11, attempt=2)), {"one"})
        self.assertEqual(self.read(run=11, attempt=1), {})
        self.assertIn("/actions/runs/11/attempts/1", self.api.calls)

    def test_inapplicable_or_unproven_receipts_cannot_become_a_pass(self):
        faults = {
            "wrong-workflow": lambda a: a.source.update(path="other.yml"),
            "cancelled-run": lambda a: a.source.update(conclusion="cancelled"),
            "unfinished-job": lambda a: a.job.update(status="in_progress"),
            "wrong-job": lambda a: a.job.update(name="other"),
            "wrong-source": lambda a: a.source.update(head_sha="f" * 40),
            "fork-source": lambda a: a.source.update(head_repository={"full_name": "other/project"}),
            "wrong-tree": lambda a: a.commit.update(tree={"sha": "f" * 40}),
            "wrong-history": lambda a: a.commit.update(parents=[{"sha": "f" * 40}, {"sha": HEAD}]),
            "forged-inputs": lambda a: a.proof["inputs"].update(tools="changed"),
            "wrong-canonical": lambda a: a.proof.update(tested_revision="f" * 40),
            "partial-plan": lambda a: a.proof.update(plan=["one"]),
            "expired": lambda a: a.artifact.update(expired=True),
            "wrong-repository": lambda a: a.proof.update(repository="other/project"),
            "reused-marker": lambda a: [r.update(mode="reused") for r in a.proof["results"]],
            "malformed-exit": lambda a: [r.update(exit=False) for r in a.proof["results"]],
            "duplicate-results": lambda a: a.proof["results"].append(copy.deepcopy(a.proof["results"][0])),
        }
        for origin, mutate in faults.items():
            with self.subTest(origin=origin):
                self.api = EvidenceApi(self.checks)
                mutate(self.api)
                self.assertEqual(self.read(), {})

    def test_missing_results_are_not_invented(self):
        self.api.proof["results"].pop()
        self.assertEqual(set(self.read()), {"one"})

    def test_split_workflow_receipts_belong_to_dev_execution_not_the_final_gate(self):
        self.api.job["name"] = "dev-lane"
        self.api.jobs.append({"name": "lane", "status": "completed", "conclusion": "success"})
        self.assertEqual(set(self.read()), {"one", "two"})
        self.api.job["conclusion"] = "skipped"
        self.assertEqual(self.read(), {})

    def test_application_inputs_keep_ci_changes_and_ignore_independent_lane_code(self):
        common = [f"100644 blob {TREE}\tsrc/Kernel.php", f"100644 blob {BASE}\tops/devlane/workflow/checks/ci.py"]
        before = ci.application_digest(common + [f"100644 blob {BASE}\tops/conductor/tools/sessions.py"])
        after = ci.application_digest(common + [f"100644 blob {HEAD}\tops/conductor/tools/sessions.py"])
        self.assertEqual(before, after)
        self.assertNotEqual(before, ci.application_digest([common[0], f"100644 blob {HEAD}\tops/devlane/workflow/checks/ci.py"]))

    def test_artifact_bytes_are_verified_and_storage_download_has_no_api_token(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("receipt.json", '{"actual":"receipt"}')
        blob = buffer.getvalue()
        artifact = {"id": 1, "digest": "sha256:" + hashlib.sha256(blob).hexdigest()}
        location = "https://fixture.blob.core.windows.net/receipt"
        api = ci.GitHub(REPO, "api-only-token")
        opener = unittest.mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError("api", 302, "redirect", {"Location": location}, None)
        with patch.object(ci.urllib.request, "build_opener", return_value=opener), patch.object(ci.urllib.request, "urlopen", return_value=io.BytesIO(blob)) as download:
            self.assertEqual(api.receipt(artifact), {"actual": "receipt"})
            self.assertEqual(download.call_args.args, (location,))
            self.assertEqual(opener.open.call_args.args[0].get_header("Authorization"), "Bearer api-only-token")
        artifact["digest"] = "sha256:" + "0" * 64
        with patch.object(ci.urllib.request, "build_opener", return_value=opener), patch.object(ci.urllib.request, "urlopen", return_value=io.BytesIO(blob)), self.assertRaisesRegex(ValueError, "integrity"):
            api.receipt(artifact)


class IsolatedExecution(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.root, self.env = self.world.cwd, self.world.env()
        self.git("init", "-b", "fixture")
        self.world.plant_text("cwd/input.txt", "same tree\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("commit", "--allow-empty", "-m", "candidate")
        self.head = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")

    def git(self, *args):
        result = run(("git", *args), cwd=self.root, env=self.env, timeout=10)
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def test_merge_publication_metadata_reuses_the_exact_tested_object(self):
        commits = [self.git("commit-tree", self.tree, "-p", self.base, "-p", self.head, "-m", text)
                   for text in ("temporary PR merge", "published merge")]
        self.assertNotEqual(*commits)
        canonical = [ci.canonical_commit(self.root, self.env, commit)[0] for commit in commits]
        self.assertEqual(*canonical)
        changed = self.git("commit-tree", self.tree, "-p", self.head, "-m", "changed history")
        self.assertNotEqual(canonical[0], ci.canonical_commit(self.root, self.env, changed)[0])

    def test_isolated_child_and_receipt_describe_actual_execution(self):
        observed = self.world.root / "observed.json"
        fixture = Path(__file__).parent / "fixtures/ci_environment.py"
        checks = [Check("fixture", (sys.executable, str(fixture), str(observed)), 10)]
        ambient = self.world.env({"GITHUB_REPOSITORY": REPO, "RUNNER_TEMP": str(self.world.tmp),
                                  "GITHUB_TOKEN": "must-not-reach-child", "WF_AGENT": "operator",
                                  "GITHUB_OUTPUT": str(self.world.root / "outputs")})
        with patch.object(ci, "toolchain", return_value={"fixture": "tools"}), patch.object(ci, "app_plan", return_value=checks):
            code = ci.run_ci("app", root=self.root, ambient=ambient, api=EvidenceApi(checks))
        self.assertEqual(code, 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        seen = json.loads(observed.read_text())
        self.assertNotEqual(seen["HOME"], str(self.world.home))
        self.assertIsNone(seen["GITHUB_TOKEN"])
        self.assertIsNone(seen["WF_AGENT"])
        for key in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "TMPDIR"):
            self.assertNotEqual(seen[key], ambient[key])
        self.assertFalse(Path(seen["HOME"]).exists())
        receipt = json.loads(next(self.world.tmp.glob("ci-proof-*/receipt.json")).read_text())
        self.assertEqual(receipt["results"], [{"name": "fixture", "mode": "executed", "exit": 0,
                                             "seconds": receipt["results"][0]["seconds"]}])
        self.assertNotIn("must-not-reach-child", json.dumps(receipt))

    def test_valid_pass_reuses_but_changed_environment_or_tree_runs_it(self):
        tool = self.world.tools.plant("subject", stdout="actual execution\n")
        checks = [Check("fixture", (str(tool),), 10)]
        ambient = self.world.env({"GITHUB_REPOSITORY": REPO, "RUNNER_TEMP": str(self.world.tmp),
                                  "GITHUB_RUN_ID": "11", "GITHUB_SHA": self.head,
                                  "CI_RUNNER_MODE": "local", "CI_RUNNER_ENVIRONMENT": "self-hosted"})
        api = EvidenceApi(checks)
        with patch.object(ci, "toolchain", return_value={"fixture": "tools"}), patch.object(ci.lane, "plan", return_value=checks):
            self.assertEqual(ci.run_ci("lane", root=self.root, ambient=ambient, api=api), 0)
            receipt = json.loads(next(self.world.tmp.glob("ci-proof-lane-11-1-*/receipt.json")).read_text())
            self.assertEqual(receipt["profile"], "local")
            api.proof = receipt
            api.artifact["name"] = f"ci-proof-lane-{receipt['key']}-11-1"
            api.source.update(event="push", head_sha=self.head)
            api.commit = {"tree": {"sha": self.tree}, "parents": [{"sha": self.base}]}
            ambient["GITHUB_RUN_ID"] = "12"
            self.assertEqual(ci.run_ci("lane", root=self.root, ambient=ambient, api=api), 0)
            self.assertEqual(len(self.world.tools.calls("subject")), 1)
            reused = json.loads(next(self.world.tmp.glob("ci-proof-lane-12-1-*/receipt.json")).read_text())
            self.assertEqual(reused["results"][0]["mode"], "reused")
            ambient.update(CI_RUNNER_MODE="hosted", CI_RUNNER_ENVIRONMENT="github-hosted",
                           ImageOS="ubuntu24", ImageVersion="fixture-image", GITHUB_RUN_ID="14")
            self.assertEqual(ci.run_ci("lane", root=self.root, ambient=ambient, api=api), 0)
            self.assertEqual(len(self.world.tools.calls("subject")), 2)
            hosted = json.loads(next(self.world.tmp.glob("ci-proof-lane-14-1-*/receipt.json")).read_text())
            self.assertEqual(hosted["profile"], "ci")
            self.world.plant_text("cwd/input.txt", "changed tree\n")
            self.git("add", ".")
            self.git("commit", "-m", "changed source")
            ambient["GITHUB_RUN_ID"] = "13"
            ambient["GITHUB_SHA"] = self.git("rev-parse", "HEAD")
            self.assertEqual(ci.run_ci("lane", root=self.root, ambient=ambient, api=api), 0)
            self.assertEqual(len(self.world.tools.calls("subject")), 3)

    def test_partial_app_reuse_still_materializes_dependencies_for_fresh_checks(self):
        install = self.world.tools.plant("install")
        probe = self.world.tools.plant("probe")
        checks = [Check("composer-install", (str(install),), 10), Check("probe", (str(probe),), 10)]
        ambient = self.world.env({"GITHUB_REPOSITORY": REPO, "RUNNER_TEMP": str(self.world.tmp)})
        with patch.object(ci, "toolchain", return_value={}), patch.object(ci, "app_plan", return_value=checks), patch.object(ci, "reusable", return_value={"composer-install": {"url": "original proof"}}):
            self.assertEqual(ci.run_ci("app", root=self.root, ambient=ambient, api=object()), 0)
        self.assertEqual(len(self.world.tools.calls("install")), 1)
        self.assertEqual(len(self.world.tools.calls("probe")), 1)

    def test_ci_bounds_workers_to_available_cpus(self):
        tool = self.world.tools.plant("subject")
        checks = [Check("fixture", (str(tool),), 10)]
        ambient = self.world.env({"RUNNER_TEMP": str(self.world.tmp)})
        with patch.object(ci, "toolchain", return_value={}), patch.object(ci.lane, "plan", return_value=checks), patch.object(ci.os, "cpu_count", return_value=2), patch.object(ci.lane, "execute_checks", wraps=ci.lane.execute_checks) as execute:
            self.assertEqual(ci.run_ci("lane", root=self.root, ambient=ambient), 0)
        self.assertEqual({call.kwargs.get("workers") for call in execute.call_args_list}, {2})

    def test_local_runner_bounds_workers_on_a_large_host(self):
        tool = self.world.tools.plant("subject")
        checks = [Check("fixture", (str(tool),), 10)]
        with patch.object(ci, "toolchain", return_value={}), patch.object(ci.lane, "plan", return_value=checks), patch.object(ci.os, "cpu_count", return_value=32), patch.object(ci.lane, "execute_checks", wraps=ci.lane.execute_checks) as execute:
            self.assertEqual(ci.run_ci("lane", root=self.root, ambient=self.world.env({"RUNNER_TEMP": str(self.world.tmp)})), 0)
        self.assertEqual({call.kwargs.get("workers") for call in execute.call_args_list}, {8})

    def test_decision_probes_read_the_tracked_export_in_a_private_store(self):
        ci.seed_probe_store(ci.ROOT, self.env)
        tool = ci.ROOT / "ops/conductor/tools/decisions.py"
        result = run((sys.executable, str(tool), "show", "D-REPO-2"), cwd=self.root, env=self.env, timeout=10)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn("we're not using MinSpec", result.stdout)
        result = run((sys.executable, str(tool), "list", "--standing", "proposed"), cwd=self.root, env=self.env, timeout=10)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn("D-VOCAB-1", result.stdout)
        skills = ci.ROOT / "ops/conductor/tools/skills.py"
        result = run((sys.executable, str(skills), "list", "--status=nosuch"), cwd=self.root, env=self.env, timeout=10)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn("no rows with status=nosuch", result.stdout)
        with self.assertRaisesRegex(ValueError, "fresh job home"):
            ci.seed_probe_store(ci.ROOT, self.env)
