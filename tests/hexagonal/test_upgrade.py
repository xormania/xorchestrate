"""``xorchestrate upgrade`` moves an instance to its approved pinned release through two fake release toolchains.

The source release ``v8.0.0`` and the target ``v9.9.9`` are toolchains built by
the fake ``uv``; both run the current code as their release, and the target
reads data version 2 with a migration step that marks every work record.
Expectations are stated from the documented procedure and read back through
SQLite, the instance record and the files on disk, not from the upgrade report.
"""
import json
import sys
from pathlib import Path

import pytest

from tests.hexagonal.contracts.project_server import initialize, rpc
from tests.hexagonal.support.project_server import CliEnvironment
from tests.hexagonal.support.store_fixtures import raw_state
from tests.hexagonal.support.upgrades import UpgradeMachine, fake_uv, pin_release, raw_store
from xorchestrate.adapters.telemetry_snapshot import verify_bundle
from xorchestrate.composition import RELEASE

SESSION_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "session_server.py"


@pytest.fixture
def machine(tmp_path):
    machine = UpgradeMachine(tmp_path, release="v8.0.0")
    machine.json("call", "create_work", "--json", '{"goal": "Retained across the upgrade"}')
    assert raw_store(machine.store)["user_version"] == 1
    return machine


def test_upgrade_moves_the_instance_to_the_approved_pin_and_migrates_with_the_target(machine):
    machine.pin("v9.9.9")
    refused = machine.json("describe", expect=2)["error"]
    assert refused["code"] == "upgrade_required"
    assert (refused["details"]["pinned"], refused["details"]["installed"]) == ("v9.9.9", "v8.0.0")

    report = machine.json("upgrade", FAKE_UV_DATA_VERSION="2")
    assert (report["status"], report["from"], report["to"]) == ("upgraded", "v8.0.0", "v9.9.9")

    record = machine.instance()
    assert (record["release"], record["data_version"]) == ("v9.9.9", 2)
    assert record["toolchain"] == str(machine.home / "toolchains" / "v9.9.9" / ".venv" / "bin" / "python")
    entry, = record["upgrades"]
    assert (entry["from"], entry["to"], entry["migrations"]) == ("v8.0.0", "v9.9.9", [[1, 2]])
    # The target release migrated the store: its step marked the work and stamped version 2.
    stored = raw_store(machine.store)
    assert stored["user_version"] == 2
    assert [item["fixture_migrated"] for item in stored["work"]] == [2]
    # The backup holds the store as it was before migrating, with matching digests.
    backup, = machine.backups()
    assert str(backup) == entry["backup"]
    assert raw_state(backup / "state.sqlite3")["user_version"] == 1
    assert raw_store(backup / "state.sqlite3")["work"][0].get("fixture_migrated") is None
    manifest = json.loads((backup / "manifest.json").read_text())
    assert set(manifest["files"]) == {"state.sqlite3", "instance.json", "config.rendered.json"}
    assert json.loads((backup / "instance.json").read_text())["release"] == "v8.0.0"
    # The before_upgrade trigger (the default) left a verified bundle of the corpus.
    bundle, = machine.bundles("before_upgrade-")
    assert str(bundle) == entry["snapshot"] == manifest["telemetry_snapshot"]
    assert verify_bundle(bundle)["status"] == "complete"
    assert not (machine.data_dir / "upgrade.json").exists()
    # Each release's toolchain was built once; project commands now run with the new one.
    assert len(machine.uv_syncs()) == 2
    listed = machine.json("call", "list_work", "--json", "{}")["work"]
    assert [item["fixture_migrated"] for item in listed] == [2]
    assert machine.json("upgrade")["status"] == "current", "re-running a finished upgrade changes nothing"
    assert len(machine.backups()) == 1


def test_a_failed_migration_leaves_the_old_release_and_data_and_a_rerun_resumes(machine):
    machine.pin("v9.9.9")
    error = machine.json("upgrade", expect=2, FAKE_UV_DATA_VERSION="2", FAKE_TOOLCHAIN_MIGRATION="fail")["error"]
    assert error["code"] == "data_migration_failed"
    # Nothing took effect: the instance still records the old release and the store is unchanged.
    assert machine.instance()["release"] == "v8.0.0" and "upgrades" not in machine.instance()
    stored = raw_store(machine.store)
    assert stored["user_version"] == 1 and "fixture_migrated" not in stored["work"][0]
    journal = machine.journal()
    assert (journal["status"], journal["flipped"], journal["error"]["code"]) == ("failed", False, "data_migration_failed")
    assert {"snapshot", "backup", "plan"} <= set(journal["steps"])
    assert machine.json("call", "list_work", "--json", "{}", expect=2)["error"]["code"] == "upgrade_required"
    assert machine.json("status", expect=3)["running"] is False, "status still runs with the installed release"

    report = machine.json("upgrade")
    assert report["status"] == "upgraded" and report["resumed"] is True
    assert raw_store(machine.store)["user_version"] == 2
    assert len(machine.backups()) == 1 and len(machine.bundles("before_upgrade-")) == 1, \
        "finished steps are not repeated when the upgrade resumes"
    assert machine.journal() is None


def test_a_target_that_cannot_read_the_data_blocks_before_anything_is_migrated(tmp_path):
    machine = UpgradeMachine(tmp_path, release="v8.0.0")
    machine.json("call", "create_work", "--json", '{"goal": "Written by a newer data version"}',
                 FAKE_UV_DATA_VERSION="3")
    machine.pin("v9.9.9")
    error = machine.json("upgrade", expect=2, FAKE_UV_DATA_VERSION="2")["error"]
    assert error["code"] == "upgrade_blocked"
    assert error["details"]["blockers"] == [{"area": "data", "status": "unsupported"}]
    assert raw_store(machine.store)["user_version"] == 3
    assert machine.instance()["release"] == "v8.0.0" and machine.journal()["status"] == "blocked"


def test_plan_only_changes_nothing_and_the_target_must_be_the_approved_pin(machine):
    machine.pin("v9.9.9")
    planned = machine.json("upgrade", "--plan-only", FAKE_UV_DATA_VERSION="2")
    assert planned["plan_only"] is True and planned["plan"]["release"] == "v9.9.9"
    assert planned["plan"]["data"]["pending_steps"] == [[1, 2]] and planned["plan"]["ready"] is True
    assert machine.instance()["release"] == "v8.0.0" and machine.journal() is None and machine.backups() == []
    assert raw_store(machine.store)["user_version"] == 1
    other = machine.json("upgrade", "--to", "v9.9.8", expect=2)["error"]
    assert other["code"] == "upgrade_target_mismatch" and other["details"] == {"pinned": "v9.9.9",
                                                                               "requested": "v9.9.8"}
    assert machine.json("upgrade", "--to", "v9.9.8", "--plan-only")["plan"]["release"] == "v9.9.8"
    assert machine.journal() is None


def test_an_unapproved_pin_is_never_upgraded_to(machine):
    machine.pin("v9.9.9", approve=False)
    error = machine.json("upgrade", expect=2)["error"]
    assert error["code"] == "approval_required" and error["details"]["files"] == ["project.json"]
    assert machine.instance()["release"] == "v8.0.0" and machine.journal() is None
    assert len(machine.uv_syncs()) == 1, "no toolchain is built for an unapproved pin"


def test_upgrade_is_refused_while_sessions_are_live_unless_detached(tmp_path):
    cli = CliEnvironment(tmp_path)
    try:
        cli.options["sessions"] = {"fixture": {"adapter": "jsonrpc", "options": {
            "command": [sys.executable, str(SESSION_FIXTURE), "jsonrpc"], "timeout": 10}}}
        cli.save()
        process, channel = cli.spawn("connect", "--actor", "worker")
        channel.request(initialize())
        started = channel.request(rpc(2, "tools/call", {"name": "start_session", "arguments": {
            "driver": "fixture", "specification": {"goal": "Live"}, "idempotency_key": "start-1"}}))
        session_id = started["result"]["structuredContent"]["session"]["id"]
        assert cli.finish(process, channel)[0] == 0
        pin_release(cli.repo, cli.home, "v9.9.9")
        environment = {**cli.environment, **fake_uv(tmp_path / "bin", cli.home)}

        refused = cli.json("upgrade", expect=2, environment=environment)["error"]
        assert refused["code"] == "sessions_live" and refused["details"]["sessions"] == [session_id]
        data = cli.home / "projects" / "server"
        assert json.loads((data / "instance.json").read_text())["release"] == RELEASE
        assert not (data / "upgrade.json").exists() and not any((data / "backups").iterdir())
        assert cli.json("status", environment=environment)["live_sessions"] == [session_id], \
            "the server keeps running with the installed release"

        report = cli.json("upgrade", "--detach", environment=environment)
        assert report["status"] == "upgraded" and report["server"]["was_running"] is True
        assert report["server"]["detached_sessions"] == [session_id]
        assert report["server"]["started"]["started"] is True, "the server that was running is started again"
        assert cli.json("status", environment=environment)["running"] is True
    finally:
        cli.cleanup()

