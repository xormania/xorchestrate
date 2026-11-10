"""``xorchestrate upgrade`` moves an instance to its approved pinned release through two fake release toolchains.

The source release ``v8.0.0`` and the target ``v9.9.9`` are toolchains built by
the fake ``uv``; both run the current code as their release, and the target
reads data version 2 with a migration step that marks every work record.
Expectations are stated from the documented procedure and read back through
SQLite, the instance record and the files on disk, not from the upgrade report.
"""
import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import xorchestrate.instance.upgrade as upgrade_module
from tests.hexagonal.contracts.project_server import initialize, rpc
from tests.hexagonal.support.project_server import CliEnvironment
from tests.hexagonal.support.projects import ProjectFixture
from tests.hexagonal.support.store_fixtures import raw_state
from tests.hexagonal.support.upgrades import UpgradeMachine, fake_uv, pin_release, raw_store
from xorchestrate.adapters.local_socket import InstanceLock, lock_is_held
from xorchestrate.adapters.telemetry_snapshot import verify_bundle
from xorchestrate.cli import ServerControl
from xorchestrate.composition import RELEASE
from xorchestrate.model import OrchestrationError
from xorchestrate.server import ServerFiles, server_identity

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



# -- review fixes: handoffs, stale journals, guards and locks -------------------------------------

def test_status_and_down_never_hand_off_so_an_older_installed_release_cannot_loop(machine):
    """An installed release from before upgrades hands every project command to the pin; status must not reach it."""
    log = machine.base / "legacy.log"
    legacy = machine.home / "toolchains" / "v8.0.0" / ".venv" / "bin" / "python"
    legacy.write_text(legacy.read_text().replace(" 1 \"$@\"", " 1 --legacy \"$@\""))
    machine.pin("v9.9.9")
    machine.json("upgrade", "--plan-only")  # builds the v9.9.9 toolchain the legacy release would hand to
    try:
        result = machine.run("status", timeout=20, FAKE_TOOLCHAIN_LOG=str(log))
    except subprocess.TimeoutExpired:
        result = None
    handoffs = log.read_text().splitlines() if log.exists() else []
    assert result is not None and result.returncode == 3, (handoffs[:4], result)
    assert json.loads(result.stdout)["running"] is False and handoffs == []
    assert machine.json("down")["running"] is False


def test_a_handed_off_command_is_never_handed_on_to_another_release(machine):
    error = machine.json("describe", expect=2, XORCHESTRATE_HANDOFF="v7.0.0")["error"]
    assert error["code"] == "handoff_loop"
    assert error["details"] == {"handed_off_for": "v7.0.0", "running": RELEASE, "release": "v8.0.0"}


def test_an_abandoned_attempt_is_not_resumed_after_the_pin_was_restored(machine):
    machine.pin("v9.9.9")
    machine.json("upgrade", expect=2, FAKE_UV_DATA_VERSION="2", FAKE_TOOLCHAIN_MIGRATION="fail")
    first, = machine.backups()
    machine.pin("v8.0.0")  # roll back before upgrading; the earlier configuration is still approved
    machine.json("call", "create_work", "--json", '{"goal": "Written after the rollback"}')
    assert machine.journal() is None and len(list(machine.data_dir.glob("upgrade.*.abandoned.json"))) == 1
    machine.pin("v9.9.9")
    report = machine.json("upgrade", FAKE_UV_DATA_VERSION="2")
    assert report["status"] == "upgraded" and report["resumed"] is False
    backup = Path(report["steps"]["backup"]["path"])
    assert backup != first and len(machine.backups()) == 2
    goals = {item["goal"] for item in raw_store(backup / "state.sqlite3")["work"]}
    assert "Written after the rollback" in goals, "the new attempt backed up the current store"
    assert len(machine.bundles("before_upgrade-")) == 2


def test_the_documented_rollback_after_a_failed_verification_can_be_upgraded_again(machine):
    machine.pin("v9.9.9")
    error = machine.json("upgrade", expect=2, FAKE_UV_DATA_VERSION="2", FAKE_TOOLCHAIN_FAIL="describe")["error"]
    assert error["code"] == "upgrade_verification_failed"
    assert machine.instance()["release"] == "v9.9.9" and machine.journal()["flipped"] is True
    backup, = machine.backups()
    # docs/upgrade.md rollback, without deleting upgrade.json
    machine.pin("v8.0.0")
    for name in ("state.sqlite3-wal", "state.sqlite3-shm"):
        (machine.data_dir / name).unlink(missing_ok=True)
    shutil.copyfile(backup / "state.sqlite3", machine.store)
    shutil.copyfile(backup / "instance.json", machine.data_dir / "instance.json")
    assert raw_store(machine.store)["user_version"] == 1
    machine.pin("v9.9.9")
    report = machine.json("upgrade")
    assert report["status"] == "upgraded" and report["resumed"] is False and "abandoned_journal" in report
    assert raw_store(machine.store)["user_version"] == 2
    assert [entry["to"] for entry in machine.instance()["upgrades"]] == ["v9.9.9"]


def test_the_migrate_step_runs_only_inside_an_upgrade(machine):
    machine.pin("v9.9.9")
    machine.json("upgrade", "--plan-only", FAKE_UV_DATA_VERSION="2")
    target = machine.home / "toolchains" / "v9.9.9" / ".venv" / "bin" / "python"
    result = subprocess.run([str(target), "-P", "-m", "xorchestrate", "--project", str(machine.repo), "upgrade-step",
                             "migrate"], capture_output=True, text=True, env=machine.environment, timeout=60)
    assert result.returncode == 2 and json.loads(result.stderr)["error"]["code"] == "upgrade_required", result
    assert raw_store(machine.store)["user_version"] == 1, "the store was not migrated"
    assert "upgrade-step" not in machine.run("--help").stdout


def test_an_approved_change_that_moves_the_store_blocks_the_upgrade(machine):
    config = {"version": 1, "store": {"adapter": "sqlite", "options": {"path": "${data}/moved.sqlite3"}}}
    (machine.repo / ".xorchestrate" / "config.json").write_text(json.dumps(config))
    machine.pin("v9.9.9")
    error = machine.json("upgrade", expect=2, FAKE_UV_DATA_VERSION="2")["error"]
    assert error["code"] == "upgrade_blocked"
    assert {"area": "store", "status": "moved", "from": str(machine.store),
            "to": str(machine.data_dir / "moved.sqlite3")} in error["details"]["blockers"]
    assert raw_store(machine.store)["user_version"] == 1 and machine.instance()["release"] == "v8.0.0"


def test_a_concurrent_upgrade_is_refused(machine):
    machine.pin("v9.9.9")
    lock = InstanceLock(machine.data_dir / "upgrade.lock")
    assert lock.acquire()
    try:
        error = machine.json("upgrade", expect=2)["error"]
    finally:
        lock.release()
    assert error["code"] == "upgrade_in_progress" and machine.journal() is None


@pytest.fixture
def in_process(machine, monkeypatch):
    """Run ``upgrade()`` in this process with the machine's environment and the CLI's server control."""
    for key, value in machine.environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("FAKE_UV_DATA_VERSION", "2")
    instance_id = machine.instance()["instance_id"]
    return lambda **options: upgrade_module.upgrade(machine.repo, server=ServerControl(instance_id, machine.repo, 30),
                                                    **options)


def test_the_server_and_upgrade_locks_are_held_while_the_target_checks_and_migrates(machine, in_process):
    files = ServerFiles.locate(server_identity(machine.instance()["instance_id"]))
    held = []

    def runner(command, **options):
        if "upgrade-step" in command:
            held.append((command[-1], lock_is_held(files.lock), lock_is_held(machine.data_dir / "upgrade.lock")))
        return subprocess.run(command, **options)

    machine.pin("v9.9.9")
    assert in_process(runner=runner)["status"] == "upgraded"
    assert held == [("check", True, True), ("migrate", True, True), ("check", True, True)]
    assert not lock_is_held(files.lock) and not lock_is_held(machine.data_dir / "upgrade.lock")


def test_a_crash_after_recording_the_release_does_not_record_it_twice(machine, in_process, monkeypatch):
    saved = upgrade_module._save

    def crash_once(instance, journal):
        if journal.get("flipped") and not crash_once.done:
            crash_once.done = True
            raise OSError("simulated crash before the journal is saved")
        saved(instance, journal)

    crash_once.done = False
    monkeypatch.setattr(upgrade_module, "_save", crash_once)
    machine.pin("v9.9.9")
    with pytest.raises(OSError):
        in_process()
    assert machine.instance()["release"] == "v9.9.9"
    in_process()
    assert [(entry["from"], entry["to"]) for entry in machine.instance()["upgrades"]] == [("v8.0.0", "v9.9.9")]


# -- Codex review fixes: store resolution, restart failures, forwarded options --------------------

def test_a_relative_store_path_is_backed_up_and_migrated_where_the_runtime_opens_it(tmp_path):
    config = {"version": 1, "store": {"adapter": "sqlite", "options": {"path": "custom.sqlite3"}}}
    machine = UpgradeMachine(tmp_path, release="v8.0.0", config=config)
    machine.json("call", "create_work", "--json", '{"goal": "Kept in a relatively named store"}')
    store = machine.data_dir / "custom.sqlite3"
    assert raw_store(store)["user_version"] == 1 and not machine.store.exists()
    machine.pin("v9.9.9")
    report = machine.json("upgrade", FAKE_UV_DATA_VERSION="2")
    assert report["steps"]["plan"]["data"]["path"] == str(store)
    backup = Path(report["steps"]["backup"]["path"])
    assert raw_store(backup / "state.sqlite3")["user_version"] == 1, "the real store was backed up"
    assert raw_store(store)["user_version"] == 2, "the real store was migrated"
    assert [item["fixture_migrated"] for item in raw_store(store)["work"]] == [2]


def test_a_store_that_disappeared_after_the_instance_had_data_blocks_the_upgrade(machine):
    machine.pin("v9.9.9")
    machine.json("upgrade", FAKE_UV_DATA_VERSION="2")
    for name in ("state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm"):
        (machine.data_dir / name).unlink(missing_ok=True)
    machine.pin("v9.9.10")
    error = machine.json("upgrade", expect=2, FAKE_UV_DATA_VERSION="2")["error"]
    assert error["code"] == "upgrade_blocked"
    assert {"area": "data", "status": "absent", "path": str(machine.store)} in error["details"]["blockers"]
    assert machine.instance()["release"] == "v9.9.9"


class RecordingServer:
    """A server control for a server that was running; ``start`` fails until ``healthy``."""

    def __init__(self):
        self.healthy, self.starts = False, 0

    def stop(self, detach):
        return {"was_running": True}

    def hold(self):
        return contextlib.nullcontext()

    def start(self):
        self.starts += 1
        return {"running": True, "started": True} if self.healthy else {"error": "simulated start failure"}


def test_a_failed_restart_is_reported_and_a_rerun_resumes_only_the_restart(machine, monkeypatch):
    for key, value in machine.environment.items():
        monkeypatch.setenv(key, value)
    server = RecordingServer()
    machine.pin("v9.9.9")
    with pytest.raises(OrchestrationError) as failed:
        upgrade_module.upgrade(machine.repo, server=server)
    assert failed.value.code == "upgrade_restart_failed"
    journal = machine.journal()
    assert (journal["status"], journal["flipped"]) == ("restart_failed", True) and "verify" in journal["steps"]
    assert machine.instance()["release"] == "v9.9.9"
    server.healthy = True
    describes = []
    runner = lambda command, **options: (describes.append(command[-1]), subprocess.run(command, **options))[1]  # noqa: E731
    report = upgrade_module.upgrade(machine.repo, server=server, runner=runner)
    assert report["status"] == "upgraded" and report["server"]["started"]["started"] is True
    assert server.starts == 2 and describes == [], "only the restart was repeated"
    assert machine.journal() is None


def test_skip_snapshots_reaches_the_detached_server(tmp_path):
    project = ProjectFixture(tmp_path, overlay={"telemetry": {"options": {"snapshots": {"triggers": ["before_sync"]}}}})
    project.json("call", "create_work", "--json", '{"goal": "Observed"}')
    exports = project.data_dir / "telemetry" / "exports"
    shutil.rmtree(exports, ignore_errors=True)
    exports.write_text("not a directory")  # every snapshot fails
    project.write_overlay({"telemetry": {"options": {"snapshots": {"triggers": ["before_sync"]}}},
                           "guardrail": {"adapter": "rules", "options": {}}})
    try:
        started = project.json("--skip-snapshots", "up", "--start-timeout", "15", timeout=60)
        assert started["started"] is True and started["running"] is True
        record = json.loads((project.data_dir / "instance.json").read_text())
        assert record["snapshots"][-1]["reason"] == "skip_requested"
    finally:
        project.run("down", "--detach")
