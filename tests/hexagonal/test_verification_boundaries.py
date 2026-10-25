"""Dependency contracts for the repaired verification and shared test layers.

These checks inspect source; they do not import the modules under review. The
planted violations exercise the checker itself, including transitive helpers.
This is an architecture regression guard, not a sandbox for hostile Python.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PURE = {
    "scripts.verification.model", "scripts.verification.ports",
    "scripts.verification.evidence", "scripts.verification.core",
}
PURE_STDLIB = {
    "__future__", "collections", "copy", "dataclasses", "datetime", "enum",
    "functools", "hashlib", "itertools", "json", "math", "re", "typing",
}
CONTRACTS = "tests.hexagonal.contracts"
SUPPORT = "tests.hexagonal.support"
WIRE_DISCRIMINANTS = {
    "codex", "claude", "grok", "turn/started", "turn/completed",
    "session/update", "_x.ai/session/prompt_complete", "_x.ai/interject",
    "promptId", "sessionUpdate", "threadId", "turnId",
    "claude/PreToolUse", "grok/session/update", "codex/item/tool/call",
}
DYNAMIC_OPERATIONS = {
    "__import__", "__builtins__", "eval", "exec", "compile",
    "import_module", "exec_module", "load_module", "find_spec", "importorskip",
}


def source_index(root):
    """Index project modules, including package initializers, without execution."""
    sources = {}
    packages = set()
    for top in ("scripts", "tests", "xorchestrate", "devtools"):
        for path in (root / top).rglob("*.py"):
            parts = path.relative_to(root).with_suffix("").parts
            if parts[-1] == "__init__":
                name = ".".join(parts[:-1])
                packages.add(name)
            else:
                name = ".".join(parts)
            sources[name] = path.read_text()
    return sources, packages


def dependencies(module, tree, sources, packages):
    package = module if module in packages else module.rpartition(".")[0]
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = "." * node.level + (node.module or "")
            name = importlib.util.resolve_name(name, package) if node.level else name
            found.add(name)
            # ``from package import child`` executes child even though the base
            # package import alone does not name it. Attribute imports do not.
            found.update(name + "." + alias.name for alias in node.names
                         if name + "." + alias.name in sources)
    # Importing a submodule also executes its package initializers.
    # Protect the initial root's own initializers too, even if its source has no
    # project imports. Loading that root still executes those packages.
    for name in (*found, module):
        bits = name.split(".")
        found.update(".".join(bits[:i]) for i in range(1, len(bits))
                     if ".".join(bits[:i]) in packages)
    return sorted(found)


def boundary_violations(roots, sources, packages, *, allowed_local,
                        allowed_external=PURE_STDLIB, wire_discriminants=()):
    pending = [(name, (name,)) for name in roots]
    visited = set()
    violations = []
    while pending:
        name, chain = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        if name not in sources:
            violations.append("missing protected module: " + name)
            continue
        tree = ast.parse(sources[name], filename=name)
        # Dynamic loading must not be a back door around dependency direction.
        # The inspected layers need neither source evaluation nor module loading.
        for node in ast.walk(tree):
            operation = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if operation in DYNAMIC_OPERATIONS:
                violations.append(" -> ".join(chain) + ": dynamic operation " + operation)
            if isinstance(node, ast.ImportFrom) and any(alias.name in DYNAMIC_OPERATIONS for alias in node.names):
                violations.append(" -> ".join(chain) + ": dynamic import alias")
        # The import graph is the primary guard. This additional AST tripwire
        # catches concrete wire decoding pasted directly into an otherwise pure
        # module, which would have no forbidden import to traverse.
        docstrings = {id(node.value) for node in ast.walk(tree)
                      if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                      and isinstance(node.value.value, str)}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings and node.value in wire_discriminants):
                violations.append(" -> ".join(chain) + ": concrete wire discriminator " + repr(node.value))
        for dependency in dependencies(name, tree, sources, packages):
            if dependency.split(".")[0] in {"scripts", "tests", "xorchestrate", "devtools"}:
                if not allowed_local(dependency):
                    violations.append(" -> ".join((*chain, dependency)))
                elif dependency in sources:
                    pending.append((dependency, (*chain, dependency)))
            elif dependency.split(".")[0] not in allowed_external:
                violations.append(" -> ".join((*chain, dependency)))
    return sorted(set(violations))


def pure_local(name):
    return name in PURE or name in {"scripts", "scripts.verification"}


def contracts_local(name):
    return name in {"tests", "tests.hexagonal", CONTRACTS} or name.startswith(CONTRACTS + ".")


def test_shared_verification_invariants_and_normalization_keep_dependency_direction():
    sources, packages = source_index(ROOT)
    assert boundary_violations(PURE, sources, packages, allowed_local=pure_local,
                               wire_discriminants=WIRE_DISCRIMINANTS) == []


def test_shared_scenarios_cannot_depend_on_concrete_fixtures_or_production_oracles():
    sources, packages = source_index(ROOT)
    roots = {name for name in sources if name.startswith(CONTRACTS + ".")}
    required = {CONTRACTS + ".assessment_inspection", CONTRACTS + ".completion", CONTRACTS + ".identity"}
    assert required <= roots, "All three repaired boundaries need persistent shared scenarios"
    assert boundary_violations(roots, sources, packages, allowed_local=contracts_local,
                               allowed_external=PURE_STDLIB | {"pytest"},
                               wire_discriminants=WIRE_DISCRIMINANTS) == []


def test_concrete_decoders_do_not_depend_on_the_verdict_they_supply():
    sources, packages = source_index(ROOT)
    prefix = "scripts.verification.adapters"
    roots = {name for name in sources if name == prefix or name.startswith(prefix + ".")}
    assert roots, "Native decoding must have an explicit adapter boundary"
    allowed = {"scripts", "scripts.verification", "scripts.verification.model", "scripts.verification.ports"}
    assert boundary_violations(roots, sources, packages,
                               allowed_local=lambda name: name in allowed or name == prefix or name.startswith(prefix + ".")) == []


def test_affected_fixture_adapters_do_not_use_collected_test_modules_as_interfaces():
    sources, packages = source_index(ROOT)
    roots = {name for name in sources if name == SUPPORT or name.startswith(SUPPORT + ".")}
    assert roots, "Concrete shared-scenario fixtures must have a support composition boundary"
    # Only test-side dependencies need following here; product/verification
    # implementations are subjects behind the fixture ports and checked above.
    pending, seen, violations = list(roots), set(), []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        tree = ast.parse(sources[name], filename=name)
        for dependency in dependencies(name, tree, sources, packages):
            if dependency.startswith("tests."):
                if any(part.startswith("test_") for part in dependency.split(".")):
                    violations.append(name + " -> " + dependency)
                elif dependency in sources:
                    pending.append(dependency)
    assert violations == []


@pytest.mark.parametrize("dependency", ["subprocess", "sqlite3", "socket", "scripts.verification.adapters.native"])
def test_boundary_guard_detects_transitive_concrete_dependencies(dependency):
    sources = {"scripts.verification.core": "from . import helper\n",
               "scripts.verification.helper": "import " + dependency + "\n"}
    allowed = {"scripts.verification", "scripts.verification.core", "scripts.verification.helper"}
    found = boundary_violations(["scripts.verification.core"], sources, {"scripts.verification"},
                                allowed_local=lambda name: name in allowed)
    assert any("helper -> " + dependency in item for item in found), found


@pytest.mark.parametrize("source", [
    "module = __import__('socket')",
    "from builtins import __import__ as load\nmodule = load('socket')",
    "import importlib as loader\nmodule = loader.import_module('socket')",
    "exec('import socket')",
    "import pytest\nmodule = pytest.importorskip('socket')",
])
def test_boundary_guard_rejects_dynamic_loading_and_aliases(source):
    found = boundary_violations(["scripts.verification.core"], {"scripts.verification.core": source}, set(),
                                allowed_local=pure_local)
    assert found and any("dynamic" in item for item in found), found


@pytest.mark.parametrize("root,package,dependency", [
    ("scripts.verification.core", "scripts.verification", "socket"),
    (CONTRACTS + ".example", CONTRACTS, SUPPORT + ".concrete"),
])
def test_boundary_guard_checks_root_package_initializers(root, package, dependency):
    sources = {root: "VALUE = True\n", package: "import " + dependency + "\n"}
    found = boundary_violations([root], sources, {package},
                                allowed_local=lambda name: name in {root, package})
    assert any(package + " -> " + dependency in item for item in found), found


def test_boundary_guard_detects_inlined_wire_decoding_but_allows_adapter_location():
    source = "def decode(item):\n    return item.get('promptId') if item.get('source') == 'grok' else None\n"
    found = boundary_violations(["scripts.verification.core"], {"scripts.verification.core": source}, set(),
                                allowed_local=pure_local, wire_discriminants=WIRE_DISCRIMINANTS)
    assert len(found) == 2
    assert boundary_violations(["scripts.verification.adapters.native"],
                               {"scripts.verification.adapters.native": source}, set(),
                               allowed_local=lambda name: True) == []


def test_scenario_guard_rejects_an_expected_outcome_borrowed_from_production():
    source = "from scripts.verification.core import check_case\ndef expected(case, run):\n    return check_case(case, run, {})\n"
    assert boundary_violations([CONTRACTS + ".example"], {CONTRACTS + ".example": source}, set(),
                               allowed_local=contracts_local)


def test_scenario_guard_allows_independent_expectations_and_an_injected_subject():
    source = "from typing import Protocol\nclass Subject(Protocol):\n    def complete(self) -> bool: ...\ndef scenario(subject: Subject):\n    assert subject.complete() is False, 'Held work cannot complete'\n"
    assert boundary_violations([CONTRACTS + ".example"], {CONTRACTS + ".example": source}, set(),
                               allowed_local=contracts_local) == []
