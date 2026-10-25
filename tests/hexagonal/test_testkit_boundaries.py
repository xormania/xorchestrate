"""The production architecture evaluator also governs the active live-test kit.

Faults are planted in copies of the real source, using the real profile. No
test-specific dependency walker or alternate policy decides these verdicts.
"""

import json
import shutil
from pathlib import Path

import pytest

from scripts.architecture.python import check

ROOT = Path(__file__).resolve().parents[2]
PROFILE = json.loads((ROOT / "contracts/hexagonal.json").read_text())


@pytest.fixture
def subject(tmp_path):
    for name in PROFILE["source_roots"]:
        shutil.copytree(ROOT / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def test_live_test_tooling_is_classified_and_passes_the_shared_policy():
    report = check(ROOT, PROFILE)
    assert report["status"] == "pass", report["findings"]
    roles = {entry["module"]: entry["role"] for entry in report["files"]}
    assert roles["scripts.testkit.core"] == "testkit_core"
    assert roles["scripts.testkit.adapters.command"] == "testkit_adapters"
    assert roles["scripts.acceptance.scenario"] == "acceptance_runner"
    assert roles["scripts.verification.multi_harness"] == "verification_runner"
    assert roles["scripts.verification.core"] == "verification_core"
    assert roles["scripts.verification.adapters.native"] == "verification_decoders"


@pytest.mark.parametrize("dependency,rule", [
    ("subprocess", "dependencies.external"),
    ("xorchestrate.application", "dependencies.allowed"),
])
def test_testkit_core_cannot_reach_processes_or_the_product(subject, dependency, rule):
    path = subject / "scripts/testkit/core.py"
    path.write_text(path.read_text() + "\nimport " + dependency + "\n")
    report = check(subject, PROFILE)
    assert report["status"] == "violation"
    assert any(item["rule_id"] == rule and item.get("module") == "scripts.testkit.core"
               and item.get("target") == dependency for item in report["findings"])


def test_new_live_test_module_requires_an_explicit_architectural_role(subject):
    (subject / "scripts/testkit/unclassified.py").write_text("VALUE = 1\n")
    report = check(subject, PROFILE)
    assert report["status"] == "violation"
    assert any(item["rule_id"] == "coverage.classification"
               and item.get("module") == "scripts.testkit.unclassified" for item in report["findings"])


def test_native_decoder_cannot_depend_on_the_verdict_it_supplies(subject):
    path = subject / "scripts/verification/adapters/native.py"
    path.write_text(path.read_text() + "\nfrom scripts.verification.checker import verify\n")
    report = check(subject, PROFILE)
    assert report["status"] == "violation"
    assert any(item["rule_id"] == "dependencies.allowed"
               and item.get("module") == "scripts.verification.adapters.native"
               and item.get("target") == "scripts.verification.checker" for item in report["findings"])


def test_generic_execution_adapter_cannot_import_product_wiring(subject):
    path = subject / "scripts/testkit/adapters/command.py"
    path.write_text(path.read_text() + "\nimport xorchestrate.composition\n")
    report = check(subject, PROFILE)
    assert report["status"] == "violation"
    assert any(item["rule_id"] == "dependencies.allowed"
               and item.get("module") == "scripts.testkit.adapters.command"
               and item.get("target") == "xorchestrate.composition" for item in report["findings"])
