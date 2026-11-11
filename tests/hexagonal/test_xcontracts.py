"""Xorchestrate's public ports come from xcontracts, its built-in adapters satisfy them, and xcontracts stays independent.

The behavioral kit runs against the built-in adapters where their guarantees
are tested: the StateStore kit in ``test_storage.py`` and the operator port
kit in ``test_operator_ports.py``.
"""

import ast
import importlib
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest
import xcontracts
from xcontracts import factories, model, ports
from xcontracts.testing import factory

import xorchestrate.model
import xorchestrate.ports
from scripts.architecture.core import _allowed_external
from xorchestrate.composition import BUILTINS, NESTED, OPERATOR

ROOT = Path(__file__).resolve().parents[2]

# Host-internal kinds: the lifecycle engine's interface and the local collector's storage are not public ports;
# telemetry's port is published but provisional (it passes host internals), so it is not a selectable public kind.
INTERNAL_KINDS = {"state_engine", "telemetry"} | NESTED
# Driving-side client transport, the new pull request delivery ports and the new terminal host port stay in
# xorchestrate: they are still evolving and not yet published.
LOCAL_PORTS = {"PeerIdentity", "ClientConnection", "ClientTransport", "PullRequestHost", "PullRequestWorkspace",
               "AttachInstruction", "HostedTerminal", "TerminalClosure", "TerminalCommand", "TerminalHost"}
# Selectable, defined locally, not yet published: a kind whose port is one of LOCAL_PORTS.
LOCAL_KINDS = {"terminal_host"}


def test_the_foundation_re_exports_the_xcontracts_ports_and_values_unchanged():
    public = {name for name in vars(ports) if not name.startswith("_") and name[0].isupper()} - {"Protocol", "Callable",
                                                                                                 "AbstractContextManager"}
    public -= {"JSON", "Assessment", "Capability", "Record", "Verdict"}
    assert public <= set(xorchestrate.ports.__all__)
    for name in public:
        assert getattr(xorchestrate.ports, name) is getattr(ports, name), name
    assert set(xorchestrate.ports.__all__) - public == LOCAL_PORTS
    for name in ("JSON", "Record", "Capability", "Assessment", "Verdict", "OrchestrationError", "Conflict"):
        assert getattr(xorchestrate.model, name) is getattr(model, name), name


def test_every_selectable_kind_is_a_public_xcontracts_kind_unless_it_is_host_internal_or_local():
    assert set(BUILTINS) - INTERNAL_KINDS - LOCAL_KINDS == set(factories.KINDS)
    assert {name for name, kind in factories.KINDS.items() if kind.scope == "operator"} == OPERATOR - LOCAL_KINDS


# Built-ins that construct from empty options without launching anything.
CONSTRUCTIBLE = {"store": "memory", "guardrail": "rules", "routing": "configurable", "acceptance": "required-checks",
                 "session_selection": "latest", "delivery_scheduler": "manual", "retry_policy": "definite-failure",
                 "receipt_policy": "complete-exchange", "version_control": "git", "repository_hosting": "github"}


@pytest.mark.parametrize(("kind", "alias"), sorted(CONSTRUCTIBLE.items()))
def test_built_in_adapters_construct_through_the_convention_and_have_their_port_members(kind, alias):
    adapter = factory.construct(kind, {"adapter": BUILTINS[kind][alias], "options": {}})
    assert isinstance(adapter, factories.KINDS[kind].port)
    getattr(adapter, "close", lambda: None)()


def test_the_built_in_companion_receives_version_control_as_the_convention_states(tmp_path):
    version_control = factory.construct("version_control", {"adapter": BUILTINS["version_control"]["git"]})
    companion = factory.construct("companion", {"adapter": BUILTINS["companion"]["git-directory"],
                                                "options": {"path": str(tmp_path / "companion")}},
                                  version_control=version_control)
    assert isinstance(companion, ports.Companion)


@pytest.mark.parametrize(("kind", "alias"), sorted((kind, alias) for kind in ("providers", "sessions", "evaluators")
                                                   for alias in BUILTINS[kind]))
def test_process_backed_built_ins_declare_their_port_methods(kind, alias):
    """These launch processes or need providers, so their classes are checked structurally."""
    module, name = factories.split_reference(BUILTINS[kind][alias])
    adapter_class = getattr(importlib.import_module(module), name)
    port = factories.KINDS[kind].port
    assert factory.missing_members(adapter_class, port) == []


def test_installed_xcontracts_imports_only_the_standard_library():
    root = Path(xcontracts.__file__).resolve().parent
    foreign = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            foreign += [(path.name, name) for name in names
                        if name.split(".")[0] not in {"xcontracts", *sys.stdlib_module_names}]
    assert foreign == []


KIT_ROLES = {"test_support", "test_collection"}


def test_only_test_support_and_collected_tests_may_import_the_kit():
    """Allowing the package prefix would let product code import the kit and its fakes."""
    roles = json.loads((ROOT / "contracts/hexagonal.json").read_text())["roles"]
    for name, role in roles.items():
        patterns = role["allowed_external"]
        if any(pattern.startswith("xcontracts") for pattern in patterns):
            assert _allowed_external("xcontracts.ports", patterns, ()), name
            assert _allowed_external("xcontracts.model", patterns, ()), name
        kit = [_allowed_external(module, patterns, ())
               for module in ("xcontracts.testing", "xcontracts.testing.fakes", "xcontracts.factories")]
        assert all(kit) if name in KIT_ROLES else not any(kit), name


BOOTSTRAP = "uv pip install --python python3 -e ."
UNREACHABLE = ("failed to fetch", "could not connect", "dns error", "network is unreachable", "connection refused",
               "failed to resolve")


def test_the_documented_checkout_bootstrap_installs_xcontracts_in_a_clean_environment(tmp_path):
    """README's bootstrap must resolve xcontracts while it comes from a git source (plain pip cannot)."""
    readme = (ROOT / "README.md").read_text()
    assert BOOTSTRAP in readme.split("## Run locally", 1)[1].split("```", 2)[1], "README bootstrap changed"
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is not installed")
    environment = tmp_path / "consumer"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / "bin" / "python"
    command = [uv, *BOOTSTRAP.split()[1:]]
    command[command.index("python3")] = str(python)
    command[-1] = str(ROOT)
    clean = {key: value for key, value in os.environ.items() if not key.startswith(("VIRTUAL_ENV", "UV_PROJECT"))}
    completed = subprocess.run(command, cwd=tmp_path, env=clean, capture_output=True, text=True, timeout=600)
    if completed.returncode != 0 and any(marker in completed.stderr.lower() for marker in UNREACHABLE):
        pytest.skip("the xcontracts git source is unreachable (offline)")
    assert completed.returncode == 0, completed.stderr[-2000:]
    probe = subprocess.run([str(python), "-I", "-c", "import xcontracts, xorchestrate.ports as p; "
                            "print(xcontracts.__version__, p.StateStore is xcontracts.StateStore)"],
                           cwd=tmp_path, capture_output=True, text=True, timeout=60, check=True)
    assert probe.stdout.split() == ["0.1.0", "True"]
