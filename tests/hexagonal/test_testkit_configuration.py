"""Both live runners preserve source semantics and isolate each run's state."""

import json
from copy import deepcopy

from scripts.acceptance.scenario import native_config
from scripts.testkit.adapters.configuration import isolated_config
from scripts.testkit.adapters.evidence import allocate_run, write_json
from scripts.verification.multi_harness import prepare_config


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
