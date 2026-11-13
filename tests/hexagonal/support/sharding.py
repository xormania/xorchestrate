"""Planted catalog projects and pytest/JUnit runners for shard selection tests."""

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from tests.hexagonal.support.ci_candidate import artifact_zip, lane_lineage, lane_runtime, write_json
from tests.hexagonal.support.ci_receipts import shard_receipts
from tests.hexagonal.support.ci_reuse import candidate_context

ROOT = Path(__file__).resolve().parents[3]

# Two families with uneven module costs; one module has no recorded timing.
FAMILIES = {
    "alpha": {"tests/test_alpha_heavy.py": 3, "tests/test_alpha_light.py": 1, "tests/test_alpha_mid.py": 2},
    "beta": {"tests/test_beta_one.py": 1, "tests/test_beta_two.py": 2},
    "gamma": {"tests/test_gamma_new.py": 1},
}
TIMINGS = {"tests/test_alpha_heavy.py": 9.0, "tests/test_alpha_light.py": 0.5, "tests/test_alpha_mid.py": 4.0,
           "tests/test_beta_one.py": 1.5, "tests/test_beta_two.py": 3.0, "tests/test_retired.py": 50.0}
UNTIMED = "tests/test_gamma_new.py"
DEFAULT_SECONDS = 2.0


def catalog():
    return {"schema_version": 1, "id": "fixture.sharding", "roots": ["tests"], "families": {
        name: {"guarantee": name + " behavior is retained", "modules": {path: "contract" for path in modules}}
        for name, modules in FAMILIES.items()}}


def timings():
    return {"schema_version": 1, "default_seconds": DEFAULT_SECONDS, "modules": dict(TIMINGS)}


def modules():
    return sorted(path for family in FAMILIES.values() for path in family)


def create_project(directory):
    """A minimal checkout whose collection uses the shared catalog plugin."""
    (directory / "contracts").mkdir(parents=True)
    (directory / "tests").mkdir()
    (directory / "contracts/testing.json").write_text(json.dumps(catalog()))
    (directory / "contracts/test-timings.json").write_text(json.dumps(timings()))
    (directory / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (directory / "tests/conftest.py").write_text('pytest_plugins = ["scripts.testkit.adapters.pytest_plugin"]\n')
    for family in FAMILIES.values():
        for path, cases in family.items():
            (directory / path).write_text(
                f"import pytest\n@pytest.mark.parametrize('case', range({cases}))\n"
                "def test_case(case):\n    assert case >= 0\n")
    return directory


def run_pytest(project, *arguments, name="inventory"):
    inventory = project / (name + ".json")
    junit = project / (name + ".xml")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "--test-inventory", str(inventory), "--junitxml=" + str(junit), *arguments],
                            cwd=project, env={**os.environ, "PYTHONPATH": str(ROOT)},
                            capture_output=True, text=True, timeout=60)
    return result, (json.loads(inventory.read_text()) if inventory.exists() else None), junit


def junit_report(path, cases):
    """Write a JUnit report with explicit ``(classname, seconds)`` cases."""
    rows = "".join(f'<testcase classname="{classname}" name="case{index}" time="{seconds}"/>'
                   for index, (classname, seconds) in enumerate(cases))
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{rows}</testsuite></testsuites>')
    return path


def run_timings_tool(*arguments):
    return subprocess.run([sys.executable, "-m", "scripts.testkit.timings", *map(str, arguments)],
                          cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=60)


def model_bytes(document):
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


class TimingHistory:
    """Independent GitHub history, publication bytes and authenticated lineage."""

    def __init__(self, document=None):
        self.document = deepcopy(document if document is not None else timings())
        self.context = candidate_context()
        repository = self.context["repository"]
        self.run = {"id": 101, "run_attempt": 1, "event": "push", "path": ".github/workflows/ci.yml",
                    "head_branch": "master", "head_sha": "f" * 40, "status": "completed",
                    "conclusion": "success", "repository": deepcopy(repository),
                    "head_repository": deepcopy(repository)}
        self.job = {"id": 501, "run_id": 101, "head_sha": "f" * 40, "name": "CI result",
                    "run_attempt": 1, "status": "completed", "conclusion": "success"}
        self.snapshot = {"schema_version": 1, "status": "updated", "reason": "complete_qualified_shards",
                         "source": {"kind": "ci", "run_id": 101},
                         "timings_sha256": hashlib.sha256(model_bytes(self.document)).hexdigest(),
                         "publication": {"repository": deepcopy(repository), "run_id": 101,
                                         "run_attempt": 1, "source_sha": "f" * 40}}
        self.requests = []
        self.repack()

    def repack(self):
        self.payload = artifact_zip([(name, model_bytes(value), "file") for name, value in (
            ("timings.json", self.document), ("snapshot.json", self.snapshot))])
        self.artifact = {"id": 901, "name": "ci-test-timings--attempt-1", "expired": False,
                         "size_in_bytes": len(self.payload),
                         "digest": "sha256:" + hashlib.sha256(self.payload).hexdigest(),
                         "workflow_run": {"id": 101, "head_sha": "f" * 40}}

    def get(self, url):
        self.requests.append(url)
        path = urlsplit(url).path
        if path.endswith("/actions/workflows/ci.yml/runs"):
            return {"workflow_runs": [deepcopy(self.run)]}
        if path.endswith(f"/actions/runs/{self.run['id']}"):
            return deepcopy(self.run)
        if path.endswith(f"/actions/runs/{self.run['id']}/jobs"):
            return {"jobs": [deepcopy(self.job)]}
        if path.endswith(f"/actions/runs/{self.run['id']}/artifacts"):
            return {"artifacts": [deepcopy(self.artifact)]}
        raise AssertionError("Unexpected timing API request: " + url)

    def archive(self, url, token, limit):
        assert url.endswith("/actions/artifacts/901/zip")
        assert token == "timing-test-token" and len(self.payload) <= limit
        return self.payload


def timing_project(root):
    project = create_project(root)
    workflow = {"jobs": {"ci-result": {"name": "CI result"}}}
    write_json(project / ".github/workflows/ci.yml", workflow)
    return project


def timing_publication(root, artifacts, *, count=8):
    """Reuse aggregate shard receipts with independent platform producer facts."""
    workflow, catalog_document = shard_receipts(artifacts, count=count)
    workflow["jobs"]["application-tests"]["name"] = "Application tests shard ${{ matrix.shard }}/${{ matrix.shards }} (Python 3.12)"
    for job, name in (("changes", "Static checks"), ("verification", "Calibration and multi-harness verification"),
                      ("tmux-acceptance", "Tmux live acceptance"), ("ci-result", "CI result")):
        workflow["jobs"][job] = {"name": name}
    write_json(root / ".github/workflows/ci.yml", workflow)
    write_json(root / "contracts/testing.json", catalog_document)
    for path in catalog_document["families"]["behavior"]["modules"]:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("def test_observable_behavior():\n    assert True\n")
    context, _, metadata, _ = lane_lineage(candidate_context())
    metadata["run"]["run_attempt"] = 1
    metadata["jobs"], metadata["artifacts"] = [], []
    references = []
    for index in range(1, count + 1):
        lane = f"framework-tests-python-3.12-shard-{index}-of-{count}"
        artifact_name = lane + "--attempt-1"
        source = artifacts / lane
        directory = artifacts / artifact_name
        source.rename(directory)
        manifest = {"schema_version": 1, "context": deepcopy(context), "lane": lane,
                    "run_attempt": 1, "runtime": lane_runtime()}
        write_json(directory / "producer.json", manifest)
        write_json(directory / "run/candidate.json", {"commit": context["checkout"]["commit"],
                   "tree": context["checkout"]["tree"], "status": ""})
        inventory = json.loads((directory / "run/inventory.json").read_text())
        module = inventory["shard"]["modules"][0]
        junit_report(directory / "run/tests.xml", [(module[:-3].replace("/", "."), float(index))])
        job_name = f"Application tests shard {index}/{count} (Python 3.12)"
        metadata["jobs"].append({"id": 100 + index, "run_id": context["producer"]["run_id"],
                                 "name": job_name, "run_attempt": 1, "status": "completed", "conclusion": "success"})
        artifact = {"id": 900 + index, "name": artifact_name, "digest": "sha256:" + str(index % 10) * 64,
                    "expired": False, "workflow_run": {"id": context["producer"]["run_id"],
                                                         "head_sha": context["source_sha"]}}
        metadata["artifacts"].append(artifact)
        references.append({"lane": lane, "decision": "executed", "run_id": context["producer"]["run_id"],
                           "run_attempt": 1, "job_id": 100 + index, "observation_job_id": 100 + index,
                           "observation_attempt": 1, "carried_forward": False, "artifact_id": artifact["id"],
                           "artifact_name": artifact_name, "artifact_digest": artifact["digest"],
                           "runtime": manifest["runtime"], "original_context": deepcopy(context)})
    result = {"schema_version": 2, "status": "passed", "errors": [], "documentation_only": False,
              "candidate_context": deepcopy(context), "source_sha": context["source_sha"],
              "tested_sha": context["checkout"]["commit"], "evidence_references": references}
    return context, metadata, result
