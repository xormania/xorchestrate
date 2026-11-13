"""One immutable external timing model binds selection, execution and qualification."""

import hashlib
import json
from pathlib import Path

import pytest

from scripts.testkit import composition, operator_cli
from scripts.testkit import operator_composition as managed
from scripts.testkit.adapters import profiles
from scripts.testkit.adapters.evidence import RecordingRunner
from scripts.testkit.adapters.pytest_evidence import bind_pytest_report
from tests.hexagonal.support import sharding
from tests.hexagonal.support import timing_model as fixture


def test_execution_and_independent_binder_use_the_same_frozen_external_weights(tmp_path, monkeypatch):
    project = fixture.create_project(tmp_path / "candidate")
    committed = (project / "contracts/test-timings.json").read_bytes()
    source = tmp_path / "automatic-timings.json"
    original_bytes = fixture.write_model(source, project)
    expected_digest = hashlib.sha256(original_bytes).hexdigest()
    monkeypatch.setenv("PYTHONPATH", str(fixture.ROOT))
    original_run = RecordingRunner.run
    invoked = []

    def replace_future_model_before_launch(runner, argv, **options):
        frozen = Path(argv[argv.index("--test-timings") + 1])
        assert frozen != source and frozen.read_bytes() == original_bytes, "EXECUTOR_RECEIVES_FROZEN_TIMING_SNAPSHOT"
        assert not frozen.stat().st_mode & 0o222, "the run's timing model is read-only"
        invoked.append(frozen)
        fixture.write_model(source, project, heavy="tests/hexagonal/test_alpha_heavy.py")
        return original_run(runner, argv, **options)

    monkeypatch.setattr(RecordingRunner, "run", replace_future_model_before_launch)
    report = composition.run(root=project, output=tmp_path / "runs", suite="tests", shard="1/2", timings=source)
    assert report["status"] == "passed", report
    check, = report["checks"]
    observed = fixture.inventory(report)
    assert fixture.selected_modules(observed) == {fixture.HEAVY}, "FROZEN_TIMINGS_SELECT_ORIGINAL_MODULES"
    assert observed["shard"]["timings_sha256"] == expected_digest
    assert check["tool_report"]["shard"]["timings_sha256"] == expected_digest, "QUALIFIER_USES_EXECUTED_MODEL"
    assert report["timings"] == {"path": str(invoked[0]), "sha256": expected_digest}
    assert invoked[0].read_bytes() == original_bytes != source.read_bytes()
    assert (project / "contracts/test-timings.json").read_bytes() == committed, "candidate timing defaults remain untouched"


@pytest.mark.parametrize("fault", ["inventory_digest", "changed_model", "expected_digest"])
def test_independent_binder_rejects_another_model_even_after_a_successful_command(tmp_path, fault):
    project = fixture.create_project(tmp_path / "candidate")
    source = tmp_path / "automatic-timings.json"
    original = fixture.write_model(source, project)
    expected_digest = hashlib.sha256(original).hexdigest()
    result, observed, _ = sharding.run_pytest(project, "tests", "--test-shard", "1/2", "--test-timings", str(source))
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.selected_modules(observed) == {fixture.HEAVY}
    options = dict(root=project, directory=project, shard="1/2", timings=source,
                   timings_sha256=expected_digest, exit_status=0)
    assert bind_pytest_report(**options)["status"] == "passed", "healthy executed model qualifies first"
    if fault == "inventory_digest":
        observed["shard"]["timings_sha256"] = "0" * 64
        (project / "inventory.json").write_text(json.dumps(observed))
    elif fault == "changed_model":
        # Equivalent weights still belong to different frozen input bytes.
        source.write_bytes(original + b"\n")
    else:
        options["timings_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="[Tt]iming|shard"):
        bind_pytest_report(**options)


def test_cli_profile_and_plan_preserve_the_explicit_model_and_reject_ambiguous_scope(tmp_path):
    project = fixture.create_project(tmp_path / "candidate")
    source = tmp_path / "automatic-timings.json"
    fixture.write_model(source, project)
    args = operator_cli.parser().parse_args(["run", "--suite", "tests", "--shard", "1/2", "--timings", str(source)])
    assert operator_cli.overrides(args)["timings"] == str(source)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema_version": 1, "suite": "tests", "shard": "1/2", "timings": source.name}))
    selected = profiles.load_profile(profile, root=project)
    assert selected["timings"] == str(source)
    check, = composition.plan(root=project, directory=tmp_path / "evidence", suite="tests", mode="protocol-fixture",
                             shard="1/2", timings=source)
    assert check.argv[check.argv.index("--test-timings") + 1] == str(source)
    for suite, shard in (("tests", None), ("static", None), ("checks", None)):
        with pytest.raises(ValueError, match="timing|shard"):
            composition.plan(root=project, directory=tmp_path / "evidence", suite=suite, mode="protocol-fixture",
                             shard=shard, timings=source)


def test_managed_repeat_preserves_the_saved_model_and_rejects_snapshot_changes(tmp_path):
    project = fixture.create_project(tmp_path / "candidate")
    source = tmp_path / "automatic-timings.json"
    original = fixture.write_model(source, project)
    identity = hashlib.sha256(original).hexdigest()
    with fixture.managed_snapshot(project, source, tmp_path / "managed-runs") as (directory, plan):
        saved = Path(plan["timings"])
        assert saved != source and saved.read_bytes() == original
        assert plan["input_snapshots"]["timings"]["sha256"] == plan["timings_sha256"] == identity
        fixture.write_model(source, project, heavy="tests/hexagonal/test_alpha_heavy.py")
        repeated = managed.repeat_settings(directory, {})
        assert repeated["timings"] == str(saved) and saved.read_bytes() == original
        saved.chmod(0o600)
        saved.write_bytes(original + b"\n")
        with pytest.raises(ValueError, match="[Tt]iming"):
            managed.repeat_settings(directory, {})
