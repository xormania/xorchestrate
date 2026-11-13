"""Controlled catalog subjects for external shard-timing execution evidence."""

import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

from . import sharding

ROOT = Path(__file__).resolve().parents[3]
HEAVY = "tests/hexagonal/test_gamma_new.py"


def create_project(directory):
    """Reuse the planted shard catalog at the test-kit's ordinary discovery root."""
    directory = sharding.create_project(directory)
    target = directory / "tests/hexagonal"
    target.mkdir()
    for source in (directory / "tests").glob("test_*.py"):
        source.rename(target / source.name)
    catalog = sharding.catalog()
    catalog["roots"] = ["tests/hexagonal"]
    for family in catalog["families"].values():
        family["modules"] = {path.replace("tests/", "tests/hexagonal/"): layer
                             for path, layer in family["modules"].items()}
    (directory / "contracts/testing.json").write_text(json.dumps(catalog))
    committed = sharding.timings()
    committed["modules"] = {path.replace("tests/", "tests/hexagonal/"): value
                            for path, value in committed["modules"].items()}
    (directory / "contracts/test-timings.json").write_text(json.dumps(committed))
    return directory


def write_model(path, project, *, heavy=HEAVY):
    """A single dominant module must be alone in shard 1/2, independently of LPT code."""
    catalog = json.loads((project / "contracts/testing.json").read_text())
    modules = {name: 100.0 if name == heavy else 1.0
               for family in catalog["families"].values() for name in family["modules"]}
    path.write_text(json.dumps({"schema_version": 1, "default_seconds": 1.0, "modules": modules}, sort_keys=True))
    return path.read_bytes()


def inventory(report):
    return json.loads((Path(report["directory"]) / "inventory.json").read_text())


def selected_modules(report):
    return {case["path"] for case in report["cases"] if case["selected"]}


@contextmanager
def managed_snapshot(project, source, output):
    """Prepare saved inputs without launching a supervisor or terminal host."""
    from scripts.testkit.operator_composition import prepare_run

    settings = {"root": str(project), "output": str(output), "suite": "tests", "mode": "protocol-fixture",
                "shard": "1/2", "timings": str(source), "config": None, "brief": None, "drivers": [],
                "families": [], "timeout": 2, "check_timeout": 3, "run_timeout": None,
                "shutdown_grace": 1, "interpreter": sys.executable}
    directory = prepare_run(settings)
    plan = json.loads((directory / "plan.json").read_text())
    try:
        yield directory, plan
    finally:
        Path(plan["terminal_endpoint"]).rmdir()


def shard_receipts(root, *, count=8):
    """Complete executed receipts with one externally chosen model in each shard."""
    from .ci_receipts import shard_receipts as ordinary_receipts

    workflow, catalog = ordinary_receipts(root, count=count)
    workflow["jobs"]["application-tests"]["env"] = {"CI_TIMINGS_REQUIRED": "true"}
    modules = catalog["families"]["behavior"]["modules"]
    model = json.dumps({"schema_version": 1, "default_seconds": 1, "modules": {name: 1 for name in modules}}).encode()
    identity = hashlib.sha256(model).hexdigest()
    for index in range(1, count + 1):
        directory = root / f"framework-tests-python-3.12-shard-{index}-of-{count}" / "run"
        (directory / "timings.json").write_bytes(model)
        inventory_path, report_path = directory / "inventory.json", directory / "report.json"
        observed = json.loads(inventory_path.read_text())
        observed["shard"]["timings_sha256"] = identity
        inventory_path.write_text(json.dumps(observed))
        report = json.loads(report_path.read_text())
        report["timings"] = {"path": str(directory / "timings.json"), "sha256": identity}
        report["checks"][0]["tool_report"].update(
            sha256=hashlib.sha256(inventory_path.read_bytes()).hexdigest(), shard=dict(observed["shard"]))
        report_path.write_text(json.dumps(report))
    return workflow, catalog


def damage_shard_model(root, fault):
    """Change one model/receipt while retaining a valid qualified inventory digest."""
    directory = root / "framework-tests-python-3.12-shard-2-of-2" / "run"
    model, inventory_path, report_path = directory / "timings.json", directory / "inventory.json", directory / "report.json"
    observed, report = json.loads(inventory_path.read_text()), json.loads(report_path.read_text())
    if fault == "missing_model":
        model.unlink()
    elif fault == "tampered_model":
        model.write_bytes(model.read_bytes() + b"\n")
    elif fault == "different_models":
        model.write_bytes(model.read_bytes() + b"\n")
        identity = hashlib.sha256(model.read_bytes()).hexdigest()
        observed["shard"]["timings_sha256"] = identity
        report["timings"]["sha256"] = identity
        report["checks"][0]["tool_report"]["shard"]["timings_sha256"] = identity
    elif fault == "bound_model":
        report["checks"][0]["tool_report"]["shard"]["timings_sha256"] = "f" * 64
    elif fault == "report_model":
        report["timings"]["sha256"] = "f" * 64
    else:
        raise ValueError("Unknown shard model fault")
    inventory_path.write_text(json.dumps(observed))
    report["checks"][0]["tool_report"]["sha256"] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
    report_path.write_text(json.dumps(report))
