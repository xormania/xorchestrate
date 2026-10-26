"""Both live runners preserve source semantics and isolate each run's state."""

import json
import sys
from copy import deepcopy

import pytest

from scripts.acceptance.scenario import native_config
from scripts.testkit.adapters.configuration import isolated_config
from scripts.testkit.adapters.evidence import allocate_run, write_json
from scripts.verification.multi_harness import prepare_config
from tests.hexagonal.support.native import peer_options
from xorchestrate.adapters.jsonrpc import command_options


def test_both_native_runners_share_relocation_without_mutating_operator_configuration(tmp_path):
    operator = tmp_path / "operator"
    operator.mkdir()
    config = {
        "sessions": {peer: {"adapter": peer, "options": {"application_tools": True}}
                     for peer in ("codex", "claude", "grok")},
        "providers": {
            "builtin": {"adapter": "cli", "options": {"cwd": "tools"}},
            "custom": {"adapter": "operator.factory:make", "options": {"cwd": "factory-relative"}},
            "disabled": {"adapter": "cli", "enabled": False},
        },
        "store": {"adapter": "sqlite", "options": {"path": "operator.sqlite3"}},
    }
    config["sessions"]["codex"]["options"].update(cwd="workspace", pass_env=["OPERATOR_KEY"], thread={"model": "chosen"})
    original = deepcopy(config)
    source = operator / "config.json"
    source.write_text(json.dumps(config))
    source_bytes = source.read_bytes()
    acceptance = native_config(source, tmp_path / "acceptance", "codex")
    verification = prepare_config(config, operator, tmp_path / "verification", "native")

    assert config == original and source.read_bytes() == source_bytes
    for derived in (acceptance, verification):
        assert derived["sessions"]["codex"]["options"]["cwd"] == str(operator / "workspace")
        assert derived["sessions"]["claude"]["options"]["cwd"] == str(operator)
        assert derived["sessions"]["grok"]["options"]["cwd"] == str(operator)
        assert derived["sessions"]["codex"]["options"]["thread"] == {"model": "chosen"}
        assert derived["sessions"]["codex"]["options"]["pass_env"] == ["OPERATOR_KEY"]
        assert derived["providers"]["builtin"]["options"]["cwd"] == str(operator / "tools")
        assert derived["providers"]["custom"] == original["providers"]["custom"]
        assert derived["providers"]["disabled"] == original["providers"]["disabled"]
    assert acceptance["store"]["options"]["path"] == str(tmp_path / "acceptance/state.sqlite3")
    assert verification["store"]["options"]["path"] == str(tmp_path / "verification/state.sqlite3")


def test_runs_have_fresh_private_evidence_and_separate_store_bindings(tmp_path):
    first, second = allocate_run(tmp_path), allocate_run(tmp_path)
    assert first != second
    for directory in (first, second):
        assert directory.stat().st_mode & 0o077 == 0
        receipt = directory / "receipt.json"
        write_json(receipt, {"status": "pending"})
        write_json(receipt, {"status": "passed"})
        assert json.loads(receipt.read_text()) == {"status": "passed"}
        assert receipt.stat().st_mode & 0o077 == 0
    one = isolated_config({}, tmp_path, first)
    two = isolated_config({}, tmp_path, second)
    assert one["store"]["options"]["path"] != two["store"]["options"]["path"]


@pytest.mark.parametrize("cache_name", ["PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"])
def test_live_command_bindings_forward_cache_policy_without_extra_credentials(tmp_path, monkeypatch, cache_name):
    for name in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE"):
        monkeypatch.delenv(name, raising=False)
    cache_value = str(tmp_path / "external-bytecode") if cache_name == "PYTHONPYCACHEPREFIX" else "1"
    monkeypatch.setenv(cache_name, cache_value)
    monkeypatch.setenv("UNRELATED_OPERATOR_SECRET", "do-not-forward")
    monkeypatch.setenv("OPERATOR_KEY", "declared-value")
    options = {"command": [sys.executable, "-c", "pass"],
               "pass_env": ["OPERATOR_KEY"], "env": {"DECLARED_ENV": "preserved"}}
    config = {"sessions": {"native": {"adapter": "codex", "options": options}},
              "providers": {"builtin": {"adapter": "cli", "options": deepcopy(options)},
                            "custom": {"adapter": "operator.factory:make", "options": deepcopy(options)},
                            "disabled": {"adapter": "cli", "enabled": False, "options": deepcopy(options)}}}
    original = deepcopy(config)
    derived = isolated_config(config, tmp_path, tmp_path / "run")
    assert config == original
    for binding in (derived["sessions"]["native"], derived["providers"]["builtin"]):
        environment = command_options(binding["options"])["env"]
        assert environment == {cache_name: cache_value, "DECLARED_ENV": "preserved", "OPERATOR_KEY": "declared-value"}
        assert binding["options"]["pass_env"] == ["OPERATOR_KEY"]
    assert derived["providers"]["custom"] == original["providers"]["custom"]
    assert derived["providers"]["disabled"] == original["providers"]["disabled"]


@pytest.mark.parametrize("protocol", ["codex", "claude"])
def test_protocol_fixture_children_keep_external_bytecode_policy(tmp_path, monkeypatch, protocol):
    cache = str(tmp_path / "external-bytecode")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", cache)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.setenv("UNRELATED_OPERATOR_SECRET", "do-not-forward")
    options = peer_options(protocol, state=tmp_path / "state")
    environment = command_options(options)["env"]
    assert environment["PYTHONPYCACHEPREFIX"] == cache
    assert "UNRELATED_OPERATOR_SECRET" not in environment
    assert set(environment) == ({"PYTHONPYCACHEPREFIX", "CLAUDE_STATE_DIR"} if protocol == "claude"
                                else {"PYTHONPYCACHEPREFIX"})
