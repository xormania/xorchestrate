"""Real candidate captures and independent post-run observations for triage."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from scripts.ci.candidate import capture_context
from tests.hexagonal.support.ci_candidate import (
    candidate_repository,
    github_observation,
    lane_lineage,
    lane_runtime,
    write_json,
)
from xorchestrate.model import Assessment


def triage_evidence(directory):
    workflow = {"jobs": {
        "changes": {"name": "Static checks"},
        "application-tests": {"name": "Application tests shard ${{ matrix.shard }}/${{ matrix.shards }} (Python 3.12)",
                              "strategy": {"matrix": {"shards": [2], "shard": [1, 2]}}},
        "verification": {"name": "Calibration and multi-harness verification"},
        "tmux-acceptance": {"name": "Tmux live acceptance (protocol fixture)"},
    }}
    catalog = {"schema_version": 1, "id": "triage-controls", "roots": ["tests/hexagonal"],
               "families": {"work": {"guarantee": "The work port retains the requested value and reports explicit failures.",
                                     "modules": {"tests/hexagonal/test_work.py": "contract",
                                                 "tests/hexagonal/test_other.py": "contract"}}}}
    repository = candidate_repository(directory / "repo", extra_files={
        ".github/workflows/ci.yml": json.dumps(workflow), "contracts/testing.json": json.dumps(catalog),
        "pkg/work.py": "def persist(port, value):\n    return port.save(value)\n",
        "tests/hexagonal/test_work.py": ("from pkg.work import persist\n\nclass FakePort:\n"
                                         "    def save(self, value):\n        return 'changed'\n\n"
                                         "def test_value():\n    assert persist(FakePort(), 'value') == 'value'\n"),
        "tests/hexagonal/test_other.py": "def test_other():\n    assert True\n",
    })
    event, env = github_observation(repository)
    context = capture_context(repository["root"], event, env)
    _, _, metadata, _ = lane_lineage(context)
    metadata["run"]["run_attempt"] = 1
    metadata["jobs"], metadata["artifacts"] = [], []
    artifacts = directory / "artifacts"
    write_json(artifacts / "candidate-context--attempt-1/context.json", context)
    lanes = (
        ("static-checks-python-3.12", "Static checks", "static"),
        ("framework-tests-python-3.12-shard-1-of-2", "Application tests shard 1/2 (Python 3.12)", "tests"),
        ("framework-tests-python-3.12-shard-2-of-2", "Application tests shard 2/2 (Python 3.12)", "tests"),
        ("calibration-python-3.12", "Calibration and multi-harness verification", "calibration"),
        ("composed-protocol-evidence", "Calibration and multi-harness verification", "multi-harness"),
        ("tmux-acceptance-evidence", "Tmux live acceptance (protocol fixture)", "acceptance"),
    )
    catalog_sha = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    job_names = {}
    for index, (lane, job, suite) in enumerate(lanes, 1):
        failed = "shard-1-of-2" in lane
        artifact_name = lane + "--attempt-1"
        target = artifacts / artifact_name / "run"
        if job not in job_names:
            job_names[job] = index
            metadata["jobs"].append({"id": 100 + index, "name": job, "run_id": context["producer"]["run_id"],
                                     "run_attempt": 1, "status": "completed", "conclusion": "failure" if failed else "success"})
        metadata["artifacts"].append({"id": 900 + index, "name": artifact_name,
                                      "digest": "sha256:" + str(index) * 64, "expired": False,
                                      "workflow_run": {"id": context["producer"]["run_id"], "head_sha": context["source_sha"]}})
        write_json(artifacts / artifact_name / "producer.json", {
            "schema_version": 1, "context": deepcopy(context), "lane": lane, "run_attempt": 1, "runtime": lane_runtime(),
        })
        write_json(target / "candidate.json", {**context["checkout"], "status": ""})
        checks = [{"name": suite, "status": "failed" if failed else "passed", "exit_status": 1 if failed else 0}]
        if suite == "tests":
            module = "tests/hexagonal/test_work.py" if failed else "tests/hexagonal/test_other.py"
            inventory = {"schema_version": 1, "catalog_id": catalog["id"], "catalog_sha256": catalog_sha,
                         "selected": 1, "exit_status": 1 if failed else 0, "status": "failed" if failed else "passed",
                         "cases": [{"id": module + "::test_value", "path": module, "family": "work",
                                    "selected": True, "outcome": "failed" if failed else "passed"}]}
            inventory["shard"] = {"index": 1 if failed else 2, "count": 2, "modules": [module]}
            write_json(target / "inventory.json", inventory)
            checks[0]["tool_report"] = {"sha256": hashlib.sha256((target / "inventory.json").read_bytes()).hexdigest(), "selected": 1}
            if failed:
                (target / "tests.xml").write_text(
                    '<testsuite><testcase classname="tests.hexagonal.test_work" name="test_value">'
                    '<failure message="AssertionError: value mismatch">Expected value but got changed.</failure>'
                    '</testcase></testsuite>')
        report = {"schema_version": 2, "suite": suite, "status": "failed" if failed else "passed", "checks": checks}
        if suite == "tests":
            report["shard"] = "1/2" if failed else "2/2"
        write_json(target / "report.json", report)
    result = {"schema_version": 2, "status": "failed", "source_sha": context["source_sha"],
              "tested_sha": context["checkout"]["commit"], "candidate_context": context,
              "documentation_only": False, "errors": ["Required test lane failed"], "evidence_references": [],
              "jobs": {"application-tests": {"result": "failure"}}, "shards": [], "families": {}}
    return {"root": repository["root"], "artifacts": artifacts, "result": result,
            "metadata": metadata, "workflow": workflow, "catalog": catalog, "context": context}


class AtomicProvider:
    """Supply controlled atomic answers while retaining every question and packet."""

    def __init__(self, choices=None):
        self.choices = choices or {}
        self.calls = []

    def __call__(self, options):
        owner = self

        class Evaluator:
            endpoint, model, criteria = options["endpoint"], options["model"], options["criteria"]

            def assess_many(self, questions, context):
                owner.calls.append({"questions": deepcopy(questions), "context": deepcopy(context)})
                answers = {}
                for key, question in questions.items():
                    choice = next((value for marker, value in owner.choices.items() if marker in question), "yes")
                    answers[key] = Assessment(choice, 0.8, "", (), {"model": self.model,
                        "probabilities": {name: int(name == choice) for name in self.criteria}})
                return answers

            def assess(self, question, context):
                return self.assess_many({"q0": question}, context)["q0"]

            def close(self):
                pass

        return Evaluator()


def observe_triage(inputs, provider, **options):
    from scripts.architecture.triage import triage

    return triage(inputs["root"], inputs["artifacts"], inputs["result"], inputs["metadata"],
                  inputs["workflow"], inputs["catalog"],
                  {"endpoint": "https://fixture.invalid/atomic", "model": "fixture-atomic", **options},
                  evaluator_factory=provider)


def replace_inventory(inputs, mutate):
    directory = inputs["artifacts"] / "framework-tests-python-3.12-shard-1-of-2--attempt-1/run"
    path = directory / "inventory.json"
    inventory = json.loads(path.read_text())
    mutate(inventory)
    write_json(path, inventory)
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text())
    report["checks"][0]["tool_report"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    report["checks"][0]["tool_report"]["selected"] = inventory["selected"]
    write_json(report_path, report)


def revision_context(kind="deleted"):
    before = {"unit_id": "old-unit", "path": "sample/work.py", "start_line": 1, "end_line": 2,
              "side": "before", "source": "def authorize():\n    return True\n", "sha256": "a" * 64}
    after = {"unit_id": "new-unit", "path": "sample/work.py", "start_line": 1, "end_line": 2,
             "side": "after", "source": "def authorize(token):\n    return token.valid\n", "sha256": "b" * 64}
    focal = before if kind == "deleted" else after
    candidate = {**{key: focal[key] for key in ("unit_id", "path", "start_line", "end_line", "side")},
                 "candidate_id": focal["unit_id"], "contract": {"role": "core"},
                 "change": {"kind": kind, "before_unit_id": "old-unit",
                            "after_unit_id": "new-unit" if kind == "modified" else None}}
    return {"sources": [after] if kind == "modified" else [], "before_sources": [before],
            "candidates": [candidate], "deterministic": {}, "source_scope": {}}


def repeated_metadata_context():
    units, candidates = [], []
    for index in range(3):
        identifier = "unit-" + str(index)
        unit = {"unit_id": identifier, "path": "sample/work" + str(index) + ".py", "start_line": 1, "end_line": 2,
                "side": "after", "source": "def work(port):\n    return port()\n", "sha256": str(index) * 64,
                "support_for": [identifier]}
        units.append(unit)
        candidates.append({**{key: unit[key] for key in ("unit_id", "path", "start_line", "end_line", "side")},
                           "candidate_id": identifier, "contract": {"role": "core", "description": "R" * 7000}})
    return {"sources": units, "before_sources": [], "candidates": candidates, "deterministic": {}, "source_scope": {}}


def many_complete_units_context():
    """Model a bounded source selection with many complete shared dependencies."""
    owners = [format(index, "024x") for index in range(3)]
    units = []
    grouped = {}
    for index in range(114):
        side = "after" if index < 82 else "before"
        path = "sample/pkg" + str(index % 8) + ".py"
        source = ("def helper_" + str(index).zfill(3) + "(port):\n"
                  "    # Complete domain context " + "p" * 80 + "\n"
                  "    return port('value')\n")
        line = len(grouped.get((path, side), [])) * 3 + 1
        unit = {"unit_id": format(index, "024x"), "path": path, "module": path[:-3].replace("/", "."),
                "side": side, "git_commit": ("a" if side == "before" else "b") * 40,
                "role": "architecture_adapters", "source": source, "kind": "FunctionDef",
                "start_line": line, "end_line": line + 2,
                "symbol": "helper_" + str(index).zfill(3), "relation": "binding", "truncated": False,
                "support_for": list(owners)}
        units.append(unit)
        grouped.setdefault((path, side), []).append(source)
    for unit in units:
        unit["sha256"] = hashlib.sha256("".join(grouped[(unit["path"], unit["side"])]).encode()).hexdigest()
    candidates = [{**{key: unit[key] for key in ("unit_id", "path", "module", "role", "sha256", "start_line", "end_line", "side")},
                   "candidate_id": unit["unit_id"], "contract": {"role": "architecture_adapters", "declaration": {
                       "description": "Use injected domain ports without concrete wire behavior."}},
                   "change": {"kind": "uncompared", "before_unit_id": None, "after_unit_id": unit["unit_id"]}}
                  for unit in units[:3]]
    return {"sources": units[:82], "before_sources": units[82:], "candidates": candidates, "deterministic": {},
            "source_scope": {"source_bytes": sum(len(unit["source"].encode()) for unit in units), "truncated": []}}
