"""Telemetry snapshots at chosen moments and their archive into the companion (spec 4.5).

Each trigger is checked at its moment by observing the bundles written to the
export root; the archive is read back from the companion's files and git, and
the archived bundle is verified and restored with the bundle format's own
functions.
"""
import hashlib
import json
import shutil

import pytest

from tests.hexagonal.support.projects import ProjectFixture, git
from tests.hexagonal.support.upgrades import UpgradeMachine
from xorchestrate.adapters.telemetry_snapshot import restore_bundle, verify_bundle
from xorchestrate.composition import RELEASE, build
from xorchestrate.model import OrchestrationError


def triggers(*names, archive=None, **options):
    snapshots = {"triggers": list(names)}
    if archive is not None:
        snapshots["archive"] = archive
    return {"telemetry": {"options": {**options, "snapshots": snapshots}}}


def bundles(project, prefix=""):
    exports = project.data_dir / "telemetry" / "exports"
    return sorted(path for path in exports.iterdir() if path.name.startswith(prefix)) if exports.is_dir() else []


def with_events(project):
    project.json("call", "create_work", "--json", '{"goal": "Observed before the snapshot"}', project=True)
    return project


@pytest.mark.parametrize("names", [["before_lunch"], ["before_sync", "manual"], "before_sync"])
def test_unknown_snapshot_triggers_are_refused(tmp_path, names):
    project = ProjectFixture(tmp_path)
    project.write_overlay({"telemetry": {"options": {"snapshots": {"triggers": names}}}})
    with pytest.raises(OrchestrationError) as refused:
        project.prepared()
    assert refused.value.code == "configuration_error"
    if isinstance(names, list):
        assert refused.value.details["unknown"] == sorted(set(names) - {"before_sync"})
    # The collector refuses the same option, so telemetry_status names it.
    config = {"version": 1, "telemetry": {"adapter": "local", "options": {
        "runtime_root": str(tmp_path / "corpus"), "project_id": "p", "dataset_id": "d", "collector_id": "c",
        "snapshots": {"triggers": ["before_lunch"]}}}}
    with build(config, tmp_path) as runtime:
        status = runtime.application.call("telemetry_status", {})
    assert status["cause"] == {"code": "invalid_option", "detail": "snapshots"}


def test_before_sync_fires_only_before_a_render_that_changes_the_configuration(tmp_path):
    project = with_events(ProjectFixture(tmp_path, overlay=triggers("before_sync")))
    assert project.prepared().snapshots == (), "an unchanged configuration is not a sync"
    assert bundles(project) == []
    project.write_overlay({**triggers("before_sync"), "guardrail": {"adapter": "rules", "options": {}}})
    report, = project.prepared().snapshots
    bundle, = bundles(project, "before_sync-")
    assert report["triggers"] == ["before_sync"] and report["bundle"] == str(bundle)
    manifest = verify_bundle(bundle)
    assert manifest["source"]["event_count"] >= 1 and manifest["bundle_id"] == report["bundle_id"]
    assert project.prepared().snapshots == () and len(bundles(project)) == 1
    record = json.loads((project.data_dir / "instance.json").read_text())
    assert [item["bundle"] for item in record["snapshots"]] == [str(bundle)]


def test_a_trigger_that_is_not_selected_takes_no_snapshot(tmp_path):
    project = with_events(ProjectFixture(tmp_path))  # the default selects only before_upgrade
    project.write_overlay({"guardrail": {"adapter": "rules", "options": {}}})
    project.write_config({"version": 1, "routing": {"adapter": "configurable", "options": {}}})
    assert project.prepared().snapshots == () and bundles(project) == []


def test_before_approve_fires_when_a_newly_approved_configuration_is_first_rendered(tmp_path):
    project = with_events(ProjectFixture(tmp_path, overlay=triggers("before_approve")))
    project.write_config({"version": 1, "routing": {"adapter": "configurable", "options": {}}}, approved=False)
    project.approve()
    assert bundles(project) == [], "approving records a hash; the configuration takes effect when rendered"
    report, = project.prepared().snapshots
    assert report["triggers"] == ["before_approve"]
    assert [path.name.split("-")[0] for path in bundles(project)] == ["before_approve"]
    project.write_overlay(triggers("before_approve", "before_sync"))
    project.prepared()  # the running configuration now selects both moments
    project.write_config({"version": 1}, approved=True)
    report, = project.prepared().snapshots
    assert report["triggers"] == ["before_approve", "before_sync"], "one bundle serves both moments"


def test_before_upgrade_fires_after_down_and_can_be_deselected(tmp_path):
    selected = UpgradeMachine(tmp_path / "selected", release="v8.0.0")
    selected.json("call", "create_work", "--json", '{"goal": "Observed before the upgrade"}')
    selected.pin("v9.9.9")
    report = selected.json("upgrade")
    bundle, = selected.bundles("before_upgrade-")
    assert report["steps"]["snapshot"]["bundle"] == str(bundle) and verify_bundle(bundle)["status"] == "complete"

    deselected = UpgradeMachine(tmp_path / "deselected", release="v8.0.0",
                                overlay={"telemetry": {"options": {"snapshots": {"triggers": []}}}})
    deselected.pin("v9.9.9")
    report = deselected.json("upgrade")
    assert report["steps"]["snapshot"] == {"status": "skipped", "reason": "trigger_not_selected"}
    assert deselected.bundles() == []


@pytest.fixture
def versioned(tmp_path):
    """A project whose companion is a git repository with one commit."""
    project = with_events(ProjectFixture(tmp_path, overlay={"telemetry": {"options": {"dataset_id": "archived"}}}))
    git("init", "-q", str(project.companion), cwd=project.companion)
    git("add", "-A", cwd=project.companion)
    git("commit", "-q", "-m", "companion", cwd=project.companion)
    return project


def test_archive_copies_a_verified_bundle_with_its_provenance_and_never_commits(versioned, tmp_path):
    project = versioned
    head = git("rev-parse", "HEAD", cwd=project.companion)
    snapshot = project.json("telemetry", "snapshot", "--destination", "manual-1")
    assert snapshot["triggers"] == ["manual"] and "archive" not in snapshot
    archived = project.json("telemetry", "archive", "manual-1")
    assert archived["status"] == "archived"
    instance = json.loads((project.data_dir / "instance.json").read_text())
    directory = (project.companion / "telemetry" / project.name / instance["instance_id"] / snapshot["bundle_id"])
    manifest = json.loads((directory / "manifest.json").read_text())
    overlay = (project.companion / "overlay" / project.name / "overlay.json").read_bytes()
    assert manifest["archive"] == {**manifest["archive"], "companion_commit": head,
                                   "config_sha256": project.prepared().tracked.config_hash,
                                   "overlay_sha256": hashlib.sha256(overlay).hexdigest(), "release": RELEASE,
                                   "project": project.name, "instance_id": instance["instance_id"]}
    # The provenance is not part of the bundle identity: a private copy of the archived files (as a
    # clone would check them out) verifies and restores as written.
    copy = tmp_path / "copy"
    shutil.copytree(directory, copy)
    for path in copy.rglob("*"):
        path.chmod(0o700 if path.is_dir() else 0o600)
    assert verify_bundle(copy)["bundle_id"] == snapshot["bundle_id"]
    restored = restore_bundle(copy, tmp_path / "restored")
    assert restored["manifest"]["source"]["event_count"] == snapshot["event_count"]
    # Nothing was committed; the database is tracked with Git LFS by the companion's attributes.
    assert git("rev-parse", "HEAD", cwd=project.companion) == head
    assert "telemetry/" in git("status", "--porcelain", "--untracked-files=normal", cwd=project.companion)
    relative = directory.relative_to(project.companion) / "telemetry.sqlite3"
    assert git("check-attr", "filter", "--", str(relative), cwd=project.companion).endswith(": filter: lfs")
    again = project.json("telemetry", "archive", "manual-1")
    assert again["status"] == "already_archived" and again["archive"] == manifest["archive"]


def test_an_altered_bundle_is_not_archived(versioned):
    project = versioned
    snapshot = project.json("telemetry", "snapshot", "--destination", "altered")
    database = project.data_dir / "telemetry" / "exports" / "altered" / "telemetry.sqlite3"
    database.write_bytes(database.read_bytes() + b"\0")
    refused = project.json("telemetry", "archive", "altered", expect=2)["error"]
    assert refused["code"] == "telemetry_integrity"
    assert not (project.companion / "telemetry").exists()
    assert snapshot["status"] == "complete"


def test_archive_true_archives_each_triggered_snapshot(versioned):
    project = versioned
    selected = triggers("before_sync", archive=True, dataset_id="archived")
    project.write_overlay(selected)
    assert project.prepared().snapshots == (), "the running configuration did not select before_sync yet"
    project.write_overlay({**selected, "guardrail": {"adapter": "rules", "options": {}}})
    report, = project.prepared().snapshots
    assert report["archive"]["status"] == "archived"
    assert report["archive"]["archive"]["config_sha256"] == project.prepared().tracked.config_hash
    manual = with_events(project).json("telemetry", "snapshot")
    assert manual["triggers"] == ["manual"] and manual["archive"]["status"] == "archived", \
        "a manual snapshot follows the archive setting too"
    archived = sorted((project.companion / "telemetry" / project.name).glob("*/*/manifest.json"))
    assert {path.parent.name for path in archived} == {report["bundle_id"], manual["bundle_id"]}
