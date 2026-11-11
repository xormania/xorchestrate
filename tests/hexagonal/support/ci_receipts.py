"""Reusable independent current-run receipt fixtures for CI aggregate contracts."""

import hashlib
import json

MANDATORY_JOBS = (
    "paths", "changes", "application-tests", "application", "verification",
    "tmux-acceptance", "architecture-status",
)


BEHAVIORAL_JOBS = ("application-tests", "application", "verification", "tmux-acceptance")


def job_needs(*, docs=False):
    needs = {name: {"result": "success", "outputs": {}} for name in MANDATORY_JOBS}
    needs["paths"]["outputs"] = {"code": "false" if docs else "true"}
    needs["architecture"] = {"result": "failure", "outputs": {}}
    if docs:
        for name in BEHAVIORAL_JOBS:
            needs[name]["result"] = "skipped"
    return needs


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document) + "\n")


def shard_receipts(root):
    catalog = {"id": "ci-control", "families": {"behavior": {
        "guarantee": "Two independent behavioral modules execute once.",
        "modules": {"tests/a.py": "contract", "tests/b.py": "adapter"},
    }}}
    catalog_hash = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode()).hexdigest()
    workflow = {"jobs": {"application-tests": {"strategy": {"matrix": {"shards": [2], "shard": [1, 2]}}}}}
    for index, module in enumerate(("tests/a.py", "tests/b.py"), 1):
        directory = root / f"framework-tests-python-3.12-shard-{index}-of-2" / "run"
        inventory = {"schema_version": 1, "status": "passed", "exit_status": 0,
                     "catalog_id": catalog["id"], "catalog_sha256": catalog_hash,
                     "shard": {"index": index, "count": 2, "modules": [module]}, "selected": 1,
                     "cases": [{"id": module + "::test_observable_behavior", "path": module,
                                "family": "behavior", "selected": True, "outcome": "passed"}]}
        write_json(directory / "inventory.json", inventory)
        write_json(directory / "report.json", {
            "schema_version": 2, "suite": "tests", "shard": f"{index}/2", "status": "passed",
            "checks": [{"name": "tests", "status": "passed", "duration_seconds": 1.25,
                        "tool_report": {"sha256": hashlib.sha256((directory / "inventory.json").read_bytes()).hexdigest(),
                                        "selected": 1}}],
        })
        write_json(directory / "candidate.json", {"commit": "a" * 40, "status": ""})
    return workflow, catalog


def complete_receipts(root):
    workflow, catalog = shard_receipts(root)
    workflow["jobs"]["package"] = {}
    needs = job_needs()
    needs["package"] = {"result": "success", "outputs": {}}
    artifacts = (
        ("static-checks-python-3.12", ("static",)),
        ("calibration-python-3.12", ("calibration",)),
        ("composed-protocol-evidence", ("multi-harness",)),
        ("tmux-acceptance-evidence", ("acceptance", "stories")),
    )
    for name, suites in artifacts:
        for suite in suites:
            directory = root / name / suite
            write_json(directory / "report.json", {"schema_version": 2, "suite": suite, "status": "passed"})
            write_json(directory / "candidate.json", {"commit": "a" * 40, "status": ""})
    write_json(root / "package-acceptance-evidence/run/report.json", {
        "schema_version": 1, "status": "passed", "candidate": {"commit": "a" * 40, "status": ""},
    })
    return needs, workflow, catalog
