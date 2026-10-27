"""Private repeatable setup and real bootstrap preserve configured inputs."""

import json
import stat
import sys
from pathlib import Path

import pytest

from scripts.testkit.adapters.profiles import doctor, load_profile, setup
from tests.hexagonal.contracts.managed_profiles import INVALID_PROFILES
from tests.hexagonal.support.managed_profiles import ROOT, profile_file, run_shell, shell_subject


@pytest.mark.parametrize("values,diagnostic", INVALID_PROFILES)
def test_invalid_profile_rejects_before_work_is_launched(tmp_path, values, diagnostic):
    profile = profile_file(tmp_path / "configuration", values)
    with pytest.raises(ValueError, match=diagnostic):
        load_profile(profile, root=ROOT)


def test_relative_paths_and_dormant_native_preferences_bind_to_profile_directory(tmp_path):
    directory = tmp_path / "operator settings"
    profile = profile_file(directory, {"config": "native.json", "drivers": ["configured-driver"],
                                       "brief": "brief.md", "output": "proof outputs"})
    protocol = load_profile(profile, root=ROOT)
    assert protocol["config"] is None and protocol["drivers"] == []
    native = load_profile(profile, root=ROOT, overrides={"mode": "native"})
    assert native["config"] == str(directory / "native.json")
    assert native["drivers"] == ["configured-driver"]
    assert native["brief"] == str(directory / "brief.md")
    assert native["output"] == str(directory / "proof outputs")
    assert native["interpreter"] == sys.executable
    with pytest.raises(ValueError, match="require native mode"):
        load_profile(profile, root=ROOT, overrides={"drivers": ["explicit-driver"]})


@pytest.mark.parametrize("field", ("output", "environment", "interpreter"))
def test_generated_paths_cannot_resolve_into_candidate(tmp_path, field):
    profile = profile_file(tmp_path, {field: str(ROOT / "generated")})
    with pytest.raises(ValueError, match="outside the measured candidate"):
        load_profile(profile, root=ROOT)


def test_setup_creates_private_reusable_settings_without_replacing_operator_edits(tmp_path):
    profile = tmp_path / "configuration with spaces" / "proof.json"
    first = setup(profile, root=ROOT, install=False)
    stored = json.loads(profile.read_text())
    assert first["created"] and Path(first["interpreter"]).is_file()
    assert stat.S_IMODE(profile.stat().st_mode) == 0o600
    assert stored["config"] == "native.json" and stored["drivers"] == ["codex"]
    native = Path(first["native_config"])
    assert stat.S_IMODE(native.stat().st_mode) == 0o600
    config = json.loads(native.read_text())
    assert {item["options"]["cwd"] for item in config["sessions"].values()} == {first["workspace"]}
    config["operator_marker"] = "preserved"
    native.write_text(json.dumps(config))
    stored["timeout"] = 317
    stored["interpreter"] = first["interpreter"]
    profile.write_text(json.dumps(stored))
    before = profile.read_bytes(), native.read_bytes()
    second = setup(profile, root=ROOT, install=False)
    assert not second["created"]
    assert (profile.read_bytes(), native.read_bytes()) == before
    assert second["commands"] == []
    assert second["python"]["virtual"]
    assert load_profile(profile, root=ROOT)["interpreter"] == first["interpreter"]


def test_doctor_is_read_only_and_identifies_its_dependency_interpreter(tmp_path):
    profile = profile_file(tmp_path / "settings", {"suite": "static"})
    before = profile.read_bytes()
    report = doctor(profile, root=ROOT)
    assert profile.read_bytes() == before
    assert report["authentication"] == "not probed"
    assert [item["name"] for item in report["readiness"]] == ["interpreter", "jsonschema", "pytest", "ruff"]
    assert all(item["observation_interpreter"] == sys.executable
               for item in report["readiness"][1:])
    assert not (profile.parent / "runs").exists()


def test_real_shell_preserves_arguments_spaces_and_child_startup_cache(tmp_path):
    root = tmp_path / "checkout with spaces"
    script = shell_subject(root)
    cache = tmp_path / "external cache with spaces"
    arguments = ["run", "--brief", "brief with spaces and $literal.md", "--timeout", "17",
                 "--family", "first", "--family", "second"]
    for _ in range(2):
        result = run_shell(script, arguments, cache=cache)
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed == {"argv": arguments, "cache": str(cache), "cwd": str(root)}
        assert not list(root.rglob("__pycache__"))


def test_shell_rejects_candidate_cache_before_creation_and_diagnoses_missing_python(tmp_path):
    root = tmp_path / "checkout"
    script = shell_subject(root)
    unsafe = root / "new cache"
    result = run_shell(script, ["doctor"], cache=unsafe)
    assert result.returncode == 2 and "outside the measured candidate" in result.stderr
    assert not unsafe.exists()
    result = run_shell(script, ["doctor"], cache=tmp_path / "cache", PROOF_PYTHON=str(tmp_path / "no python"))
    assert result.returncode == 2 and "Python >=3.12" in result.stderr
    unsupported = tmp_path / "unsupported python"
    unsupported.write_text("#!/bin/sh\nexit 1\n")
    unsupported.chmod(0o700)
    result = run_shell(script, ["doctor"], cache=tmp_path / "cache", PROOF_PYTHON=str(unsupported))
    assert result.returncode == 2 and "supported interpreter" in result.stderr


@pytest.mark.parametrize("action", ("status", "collect", "start"))
def test_controls_and_repeat_select_saved_interpreter_without_profile(tmp_path, action):
    root = tmp_path / "checkout with spaces"
    script = shell_subject(root)
    run = tmp_path / "saved run"
    run.mkdir()
    marker = tmp_path / "selected-interpreter.txt"
    interpreter = tmp_path / "configured python"
    interpreter.write_text("#!/bin/sh\nprintf selected >> '" + str(marker) + "'\nexec '" + sys.executable + "' \"$@\"\n")
    interpreter.chmod(0o700)
    (run / "plan.json").write_text(json.dumps({"interpreter": str(interpreter)}))
    arguments = [action, "--from", str(run)] if action == "start" else [action, str(run)]
    result = run_shell(script, arguments, cache=tmp_path / "external cache")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["argv"] == arguments
    assert marker.read_text() == "selectedselected"
