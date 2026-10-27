"""Real filesystem publication, control and export ownership boundaries."""

import json
import os
from pathlib import Path
from zipfile import ZipFile

import pytest

from scripts.testkit.adapters import managed_store
from scripts.testkit.adapters.evidence import write_json
from scripts.testkit.adapters.managed_store import ManagedStore, owner_alive
from tests.hexagonal.contracts.managed_storage import CONTROL_ACTIONS, EXPORT_CASES, TERMINAL_PHASES
from tests.hexagonal.support.managed_storage import concurrent_calls, owned_store


def test_run_allocation_is_private_exclusive_and_never_overwrites_a_plan(tmp_path):
    store = owned_store(tmp_path)
    try:
        second = ManagedStore.allocate(tmp_path)
        assert second != store.directory
        assert store.directory.stat().st_mode & 0o077 == 0
        assert (store.directory / "state.json").stat().st_mode & 0o077 == 0
        with pytest.raises(FileExistsError):
            store.initialize({"suite": "different"})
        assert json.loads((store.directory / "plan.json").read_text())["suite"] == "all"
    finally:
        store.release_owner()


def test_live_owner_cannot_be_stolen_and_unowned_publish_is_rejected(tmp_path):
    store = owned_store(tmp_path)
    other = ManagedStore(store.directory)
    try:
        assert owner_alive(store.read_state()["owner"])
        with pytest.raises(BlockingIOError):
            other.claim_owner()
        with pytest.raises(PermissionError):
            other.publish({"phase": "running"})
        with pytest.raises(PermissionError):
            other.collect()
    finally:
        store.release_owner()
    assert not owner_alive(store.read_state()["owner"])
    with pytest.raises(RuntimeError, match="fresh run"):
        other.claim_owner()


def test_concurrent_stale_publications_cannot_overwrite_the_winning_revision(tmp_path):
    store = owned_store(tmp_path)
    try:
        revision = store.read_state()["revision"]
        results = concurrent_calls(lambda index: store.publish({"winner": index}, expected_revision=revision))
        winners = [result for result in results if isinstance(result, dict)]
        errors = [result for result in results if isinstance(result, ValueError)]
        assert len(winners) == len(errors) == 1
        assert store.read_state() == winners[0]
        assert store.read_state()["revision"] == revision + 1
    finally:
        store.release_owner()


@pytest.mark.parametrize("phase", TERMINAL_PHASES)
def test_terminal_lifecycle_cannot_be_reopened_by_a_late_publication(tmp_path, phase):
    store = owned_store(tmp_path)
    try:
        completed = store.publish({"phase": phase, "proof": "failed"})
        with pytest.raises(ValueError, match="terminal"):
            store.publish({"phase": "running"})
        with pytest.raises(ValueError, match="terminal"):
            store.publish({"proof": "passed"})
        assert store.read_state() == completed
    finally:
        store.release_owner()


def test_interrupted_commit_keeps_last_state_and_uncommitted_generation_can_be_retried(tmp_path, monkeypatch):
    store = owned_store(tmp_path)
    original = store.read_state()
    writer = managed_store.write_json

    def fail_pointer(path, value):
        if Path(path).name == "state.json":
            raise OSError("simulated lost writer before replacement")
        return writer(path, value)

    try:
        with monkeypatch.context() as context:
            context.setattr(managed_store, "write_json", fail_pointer)
            with pytest.raises(OSError):
                store.publish({"phase": "running"})
        assert store.read_state() == original
        with pytest.raises(ValueError, match="Uncommitted"):
            store.collect(original["revision"] + 1)
        result = store.publish({"phase": "running"})
        assert result["revision"] == original["revision"] + 1
    finally:
        store.release_owner()


@pytest.mark.parametrize("action", CONTROL_ACTIONS)
def test_identified_control_requests_are_idempotent_and_do_not_edit_state(tmp_path, action):
    store = owned_store(tmp_path)
    try:
        initial = store.read_state()
        requests = concurrent_calls(lambda _: store.submit(action, request_id="same-request"))
        assert requests[0] == requests[1]
        assert store.read_state() == initial
        assert store.requests() == [requests[0]]
        receipt = store.complete_request("same-request", {"accepted": True})
        assert store.request_result("same-request") == receipt
        assert store.requests() == []
        with pytest.raises(ValueError, match="conflicts"):
            store.submit(action, {"different": True}, request_id="same-request")
    finally:
        store.release_owner()


@pytest.mark.parametrize("case", EXPORT_CASES, ids=lambda case: case["name"])
def test_export_binds_generation_redacts_views_and_preserves_private_authority(tmp_path, case):
    store = owned_store(tmp_path)
    try:
        source = store.directory / "checks" / "report.json"
        write_json(source, case["payload"])
        original = source.read_bytes()
        state = store.publish({"artifacts": store.bind_artifacts([source])})
        receipt = store.collect(state["revision"])
        assert receipt["generation"] == state["revision"]
        assert receipt["status"] == "completed"
        assert source.read_bytes() == original
        with ZipFile(receipt["archive"]) as bundle:
            assert json.loads(bundle.read("artifacts/checks/report.json")) == case["expected"]
            exported_state = json.loads(bundle.read("state.json"))
            assert exported_state["owner"]["token"] == "[redacted]"
            assert all(name != "plan.json" for name in bundle.namelist())
        assert Path(receipt["archive"]).stat().st_mode & 0o077 == 0
    finally:
        store.release_owner()


def test_collection_detects_changed_evidence_and_never_follows_symlinks_or_copies_inputs(tmp_path):
    store = owned_store(tmp_path)
    try:
        report = store.directory / "report.json"
        write_json(report, {"status": "passed"})
        private = store.directory / "inputs" / "config.json"
        write_json(private, {"secret": "do-not-export"})
        bindings = store.bind_artifacts([report, private])
        store.publish({"artifacts": bindings})
        write_json(report, {"status": "failed"})
        receipt = store.collect()
        assert receipt["status"] == "partial"
        assert [item["status"] for item in receipt["inventory"]] == ["unavailable", "unavailable"]
        with ZipFile(receipt["archive"]) as bundle:
            assert not any(name.startswith("artifacts/") for name in bundle.namelist())
        outside = tmp_path / "outside.json"
        write_json(outside, {"private": True})
        link = store.directory / "symlink.json"
        link.symlink_to(outside)
        with pytest.raises(ValueError, match="Symlink"):
            store.bind_artifacts([link])
        with pytest.raises(ValueError):
            store.bind_artifacts([outside])
    finally:
        store.release_owner()


def test_simultaneous_exports_are_distinct_and_do_not_block_cancellation_publication(tmp_path):
    store = owned_store(tmp_path)
    try:
        generation = store.read_state()["revision"]
        receipts = concurrent_calls(lambda _: store.collect(generation))
        assert all(isinstance(receipt, dict) for receipt in receipts)
        assert receipts[0]["archive"] != receipts[1]["archive"]
        assert all(Path(receipt["archive"]).is_file() for receipt in receipts)
        assert store.publish({"phase": "cancelling"})["phase"] == "cancelling"
    finally:
        store.release_owner()
    retained = ManagedStore(store.directory).collect(generation)
    assert Path(retained["archive"]).is_file()


def test_input_snapshots_keep_original_bytes_base_and_do_not_overwrite_prior_inputs(tmp_path):
    store = owned_store(tmp_path)
    try:
        original = tmp_path / "operator config.json"
        original.write_bytes(b'{"cwd":"relative workspace"}\n')
        manifest = store.snapshot_inputs({"native.json": original})
        snapshot = Path(manifest["native.json"]["path"])
        assert snapshot.read_bytes() == original.read_bytes()
        assert manifest["native.json"]["source_base"] == str(tmp_path)
        assert snapshot.stat().st_mode & 0o077 == 0
        with pytest.raises(FileExistsError):
            store.snapshot_inputs({"native.json": original})
        assert os.path.samefile(original.parent, manifest["native.json"]["source_base"])
    finally:
        store.release_owner()
