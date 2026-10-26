"""Calibrate the reusable architecture checker with valid and faulty structures.

The subjects use unrelated package names. Controls establish that valid port
injection and extension remain possible; failures must name the intended rule.
Actual adapter behavior/composition stays covered by the shared existing suites.
"""
from __future__ import annotations

import copy

import pytest

from scripts.architecture.python import check


def write(root, package, path, content):
    target = root / package / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def subject(root, package="parcels"):
    files = {
        "__init__.py": "",
        "ports.py": "from typing import Protocol\nclass Store(Protocol):\n    def get(self, key: str) -> str: ...\n",
        "core/__init__.py": "",
        "core/service.py": "from ..ports import Store\ndef deliver(store: Store, key: str) -> str:\n    return store.get(key)\n",
        "adapters/__init__.py": "",
        "adapters/memory.py": "from ..ports import Store\nclass MemoryStore:\n    def get(self, key: str) -> str:\n        return key\n",
        "wiring.py": "from .core.service import deliver\nfrom .adapters.memory import MemoryStore\ndef run():\n    return deliver(MemoryStore(), 'parcel')\n",
    }
    for name, source in files.items():
        write(root, package, name, source)
    return {
        "schema_version": 1,
        "id": "fixture-ports-and-adapters",
        "version": "1",
        "source_roots": [package],
        "roles": {
            "package": {
                "modules": [package], "allowed_roles": ["package"],
                "allowed_external": [], "allow_dynamic": False,
            },
            "ports": {
                "modules": [package + ".ports"],
                "allowed_roles": ["package", "ports"],
                "allowed_external": ["typing"], "allow_dynamic": False,
            },
            "core": {
                "modules": [package + ".core", package + ".core.*"],
                "allowed_roles": ["package", "ports", "core"],
                "allowed_external": ["typing"], "allow_dynamic": False,
            },
            "adapters": {
                "modules": [package + ".adapters", package + ".adapters.*"],
                "allowed_roles": ["package", "ports", "adapters"],
                "allowed_external": ["stdlib:*"], "allow_dynamic": True,
            },
            "composition": {
                "modules": [package + ".wiring"],
                "allowed_roles": ["package", "ports", "core", "adapters", "composition"],
                "allowed_external": ["stdlib:*"], "allow_dynamic": True,
            },
        },
    }


def require_finding(report, kind, rule, path=None):
    assert report["status"] in {"violation", "error", "inconclusive"}, report
    findings = [item for item in report["findings"]
                if item["kind"] == kind and item["rule_id"] == rule]
    if path is not None:
        findings = [item for item in findings if item.get("path", "").endswith(path)]
    assert findings, report
    return findings


@pytest.mark.parametrize("package", ["parcels", "another_application"])
def test_generic_profile_accepts_port_injection_and_new_adapter(tmp_path, package):
    profile = subject(tmp_path, package)
    initial = check(tmp_path, profile)
    assert initial["status"] == "pass", initial
    write(tmp_path, package, "adapters/alternative.py",
          "from ..ports import Store\nclass Alternative:\n    def get(self, key: str) -> str:\n        return key.upper()\n")
    extended = check(tmp_path, profile)
    assert extended["status"] == "pass", extended
    assert len(extended["files"]) == len(initial["files"]) + 1
    assert extended["subject"]["source_sha256"] != initial["subject"]["source_sha256"]


def test_direct_concrete_dependency_names_source_and_rule(tmp_path):
    profile = subject(tmp_path)
    assert check(tmp_path, profile)["status"] == "pass"
    write(tmp_path, "parcels", "core/service.py", "from ..adapters.memory import MemoryStore\n")
    findings = require_finding(check(tmp_path, profile), "dependency", "dependencies.allowed", "core/service.py")
    assert any("parcels.adapters.memory" in item.get("chain", []) for item in findings)


def test_transitive_helper_cannot_hide_a_concrete_dependency(tmp_path):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "core/service.py", "from . import helper\n")
    write(tmp_path, "parcels", "core/helper.py", "from ..adapters.memory import MemoryStore\n")
    findings = require_finding(check(tmp_path, profile), "dependency", "dependencies.allowed", "core/helper.py")
    assert any("parcels.adapters.memory" in item.get("chain", []) for item in findings)


def test_permissive_intermediate_role_cannot_launder_a_dependency(tmp_path):
    profile = subject(tmp_path)
    profile["roles"]["core"]["allowed_roles"].append("shared")
    profile["roles"]["shared"] = {
        "modules": ["parcels.shared"],
        "allowed_roles": ["package", "ports", "shared", "adapters"],
        "allowed_external": ["typing"], "allow_dynamic": False,
    }
    write(tmp_path, "parcels", "core/service.py", "from .. import shared\n")
    write(tmp_path, "parcels", "shared.py", "from .adapters.memory import MemoryStore\n")
    findings = require_finding(check(tmp_path, profile), "dependency", "dependencies.allowed", "shared.py")
    assert any(item["chain"] == ["parcels.core.service", "parcels.shared", "parcels.adapters.memory"]
               for item in findings)


def test_package_initializer_cannot_hide_a_concrete_dependency(tmp_path):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "core/__init__.py", "from ..adapters.memory import MemoryStore\n")
    require_finding(check(tmp_path, profile), "dependency", "dependencies.allowed", "core/__init__.py")


def test_nested_source_root_includes_its_executed_parent_initializer(tmp_path):
    profile = subject(tmp_path)
    profile["source_roots"] = ["parcels/core"]
    profile["roles"] = {name: role for name, role in profile["roles"].items() if name in {"package", "core"}}
    profile["roles"]["core"]["allowed_roles"] = ["package", "core"]
    write(tmp_path, "parcels", "core/service.py", "VALUE = 1\n")
    control = check(tmp_path, profile)
    assert control["status"] == "pass", control
    assert "parcels" in {item["module"] for item in control["files"]}
    write(tmp_path, "parcels", "__init__.py", "import socket\n")
    require_finding(check(tmp_path, profile), "external", "dependencies.external", "parcels/__init__.py")


@pytest.mark.parametrize("source", [
    "module = __import__('socket')\n",
    "from builtins import __import__ as load\nmodule = load('socket')\n",
    "import importlib as loader\nmodule = loader.import_module('socket')\n",
    "from importlib import import_module as load\nmodule = load('socket')\n",
    "exec('import socket')\n",
])
def test_dynamic_loading_is_detected_in_the_core(tmp_path, source):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "core/service.py", source)
    require_finding(check(tmp_path, profile), "dynamic", "dependencies.dynamic", "core/service.py")


def test_configured_loading_is_permitted_in_composition(tmp_path):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "wiring.py", "import importlib\ndef factory(name):\n    return importlib.import_module(name)\n")
    report = check(tmp_path, profile)
    assert report["status"] == "pass", report


@pytest.mark.parametrize("source", ["open('records.txt').read()\n", "input('prompt')\n"])
def test_declared_builtin_effects_cannot_bypass_the_ports(tmp_path, source):
    profile = subject(tmp_path)
    profile["roles"]["core"]["forbidden_calls"] = ["open", "input"]
    write(tmp_path, "parcels", "core/service.py", source)
    require_finding(check(tmp_path, profile), "operation", "operations.allowed", "core/service.py")


@pytest.mark.parametrize("method", ["open", "compile", "import_module"])
def test_injected_port_may_use_methods_named_like_builtin_operations(tmp_path, method):
    profile = subject(tmp_path)
    profile["roles"]["core"]["forbidden_calls"] = ["open", "input"]
    write(tmp_path, "parcels", "core/service.py", f"def execute(port):\n    return port.{method}()\n")
    report = check(tmp_path, profile)
    assert report["status"] == "pass", report


def test_unknown_external_library_does_not_escape_via_a_blacklist(tmp_path):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "core/service.py", "import newly_invented_database_client\n")
    require_finding(check(tmp_path, profile), "external", "dependencies.external", "core/service.py")


def test_unclassified_new_module_is_visible(tmp_path):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "unassigned.py", "VALUE = 1\n")
    require_finding(check(tmp_path, profile), "unclassified", "coverage.classification", "unassigned.py")


def test_overlapping_roles_cannot_silently_choose_a_policy(tmp_path):
    profile = subject(tmp_path)
    profile["roles"]["ports"]["modules"].append("parcels.core.service")
    require_finding(check(tmp_path, profile), "ambiguous", "coverage.classification", "core/service.py")


def test_deleted_required_module_cannot_produce_an_empty_success(tmp_path):
    profile = subject(tmp_path)
    (tmp_path / "parcels" / "ports.py").unlink()
    require_finding(check(tmp_path, profile), "missing_selector", "coverage.selectors")


def test_renamed_selector_cannot_check_nothing(tmp_path):
    profile = subject(tmp_path)
    profile["roles"]["ports"]["modules"] = ["parcels.no_longer_exists"]
    require_finding(check(tmp_path, profile), "missing_selector", "coverage.selectors")


def test_missing_local_dependency_is_not_treated_as_external(tmp_path):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "core/service.py", "import parcels.core.missing\n")
    require_finding(check(tmp_path, profile), "unresolved", "coverage.dependencies", "core/service.py")


def test_invalid_python_is_an_extraction_error_not_a_dependency_detection(tmp_path):
    profile = subject(tmp_path)
    write(tmp_path, "parcels", "core/service.py", "def broken(:\n")
    report = check(tmp_path, profile)
    assert report["status"] == "error", report
    require_finding(report, "extraction", "source.readable", "core/service.py")


def test_declared_literal_boundary_follows_allowed_helpers_and_ignores_docstrings(tmp_path):
    profile = subject(tmp_path)
    profile["roles"]["core"]["forbidden_literals"] = ["transport_field"]
    profile["roles"]["core"]["allowed_roles"].append("adapters")
    write(tmp_path, "parcels", "core/service.py", '"""transport_field"""\nfrom ..adapters.memory import deliver\n')
    helper = write(tmp_path, "parcels", "adapters/memory.py",
                   '"""transport_field"""\ndef deliver(store, key):\n    """transport_field"""\n    return store.get(key)\n')
    assert check(tmp_path, profile)["status"] == "pass"
    helper.write_text("def deliver(store, key):\n    return store.get('transport_field')\n")
    report = check(tmp_path, profile)
    require_finding(report, "literal", "literals.allowed", "adapters/memory.py")
    assert any(item.get("chain") == ["parcels.core.service", "parcels.adapters.memory"]
               and item.get("value") == "transport_field" for item in report["findings"])


def test_same_literal_is_legal_in_its_declared_adapter_role(tmp_path):
    profile = subject(tmp_path)
    profile["roles"]["core"]["forbidden_literals"] = ["transport_field"]
    write(tmp_path, "parcels", "adapters/memory.py",
          "class MemoryStore:\n    def get(self, key):\n        return {'transport_field': key}\n")
    assert check(tmp_path, profile)["status"] == "pass"


@pytest.mark.parametrize("value", ["transport_field", [None], {"transport_field": True}])
def test_invalid_literal_policy_is_configuration_error(tmp_path, value):
    profile = subject(tmp_path)
    profile["roles"]["core"]["forbidden_literals"] = value
    report = check(tmp_path, profile)
    assert report["status"] == "error"
    require_finding(report, "configuration", "profile.valid")


def test_source_inspection_never_executes_the_subject(tmp_path):
    profile = subject(tmp_path)
    marker = tmp_path / "executed"
    write(tmp_path, "parcels", "adapters/memory.py", f"open({str(marker)!r}, 'w').write('executed')\n")
    check(tmp_path, profile)
    assert not marker.exists()


def test_contract_identity_changes_when_policy_changes(tmp_path):
    profile = subject(tmp_path)
    before = check(tmp_path, profile)
    revised = copy.deepcopy(profile)
    revised["roles"]["core"]["allowed_external"].append("decimal")
    after = check(tmp_path, revised)
    assert before["status"] == after["status"] == "pass"
    assert before["subject"]["source_sha256"] == after["subject"]["source_sha256"]
    assert before["subject"]["contract_sha256"] != after["subject"]["contract_sha256"]


