"""The test kit takes machine-level defaults from the developer settings, under its own flags and profile.

Precedence for every setting the kit reads: the command-line flag, then a field the operator profile sets,
then a developer setting whose source is not its default, then the kit's built-in default. Developer
settings are loaded from a throwaway repository and HOME and passed to the kit's merge points, so no test
reads the real checkout's settings file or the user's own file.
"""

import importlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from tests.hexagonal.contracts import devconfig as contract
from tests.hexagonal.support.dev_defaults import SEAMS, accepts_dev, trusted_executable, trusted_file, with_dev
from tests.hexagonal.support.devconfig import SettingsWorld, no_processes
from tests.hexagonal.support.git_repositories import REPOSITORY_NAME
from tests.hexagonal.support.managed_profiles import ROOT, dev_shell_subject, profile_file, run_shell
from tests.hexagonal.support.source_scan import imports, tracked

OUTPUT = "XORCHESTRATE_DEV_TESTKIT_OUTPUT"
PROFILE = "XORCHESTRATE_DEV_TESTKIT_PROFILE"
INTERPRETER = "XORCHESTRATE_DEV_TESTKIT_INTERPRETER"
PYCACHE = "XORCHESTRATE_DEV_PYCACHE_PREFIX"
NATIVE_CONFIG = "XORCHESTRATE_DEV_NATIVE_CONFIG"
TERMINAL_BINARY = "XORCHESTRATE_DEV_TERMINAL_BINARY"


def kit(name):
    return importlib.import_module("scripts.testkit." + name)


@pytest.fixture
def world(tmp_path, monkeypatch):
    (tmp_path / "world").mkdir()
    return SettingsWorld(tmp_path / "world", monkeypatch)


def dev(world, **values):
    """Developer settings with ``values`` set in the environment and nothing else."""
    return world.load(env=world.env(**values))


def settings(world, argv, **values):
    cli = kit("operator_cli")
    return with_dev(cli.settings, cli.parser().parse_args(argv), dev=dev(world, **values))


def load_profile(world, path, overrides=None, **values):
    return with_dev(kit("adapters.profiles").load_profile, path, root=ROOT, overrides=overrides or {},
                    dev=dev(world, **values))


def test_every_merge_point_takes_loaded_developer_settings():
    missing = [f"{module}.{name}" for module, name in SEAMS
               if not accepts_dev(getattr(importlib.import_module(module), name))]
    assert missing == [], missing


# Output ------------------------------------------------------------------------------------------

def test_without_a_profile_runs_go_to_the_neutral_default_not_beside_the_checkout(world):
    result = settings(world, ["run"])
    expected = contract.resolve_default(contract.SLICE_C[OUTPUT][1], world.env(), REPOSITORY_NAME)
    assert result["output"] == expected
    assert contract.LEGACY_OUTPUT_NAME not in result["output"]


def test_an_output_setting_replaces_the_default_and_the_flag_replaces_both(world, tmp_path):
    assert settings(world, ["run"], **{OUTPUT: tmp_path / "dev runs"})["output"] == str(tmp_path / "dev runs")
    flagged = settings(world, ["run", "--output", str(tmp_path / "flag runs")], **{OUTPUT: tmp_path / "dev runs"})
    assert flagged["output"] == str(tmp_path / "flag runs")


def test_an_output_setting_inside_the_candidate_is_refused_like_any_output(world, tmp_path):
    inside = ROOT / "runs-inside"
    result = settings(world, ["run", "--suite", "static"], **{OUTPUT: inside})
    assert result["output"] == str(inside)
    with pytest.raises(ValueError, match="outside the candidate"):
        kit("operator_composition").prepare_run(result)
    assert not inside.exists()
    profile = profile_file(tmp_path / "operator")
    with pytest.raises(ValueError, match="outside the measured candidate"):
        load_profile(world, profile, **{OUTPUT: inside})


def test_a_profile_output_wins_then_a_set_output_setting_then_the_profiles_own_runs(world, tmp_path):
    explicit = profile_file(tmp_path / "explicit", {"output": "profile runs"})
    assert load_profile(world, explicit, **{OUTPUT: tmp_path / "dev runs"})["output"] == str(
        (tmp_path / "explicit" / "profile runs").resolve())
    silent = profile_file(tmp_path / "silent")
    assert load_profile(world, silent, **{OUTPUT: tmp_path / "dev runs"})["output"] == str(tmp_path / "dev runs")
    assert load_profile(world, silent)["output"] == str((tmp_path / "silent" / "runs").resolve()), \
        "a default-sourced setting never replaces the profile's own default"
    via_cli = settings(world, ["run", "--profile", str(silent)], **{OUTPUT: tmp_path / "dev runs"})
    assert via_cli["output"] == str(tmp_path / "dev runs")


# Profile -----------------------------------------------------------------------------------------

def test_a_profile_setting_acts_as_the_profile_flag_and_the_flag_wins(world, tmp_path):
    configured = profile_file(tmp_path / "configured", {"suite": "static"})
    other = profile_file(tmp_path / "other", {"suite": "tests"})
    result = settings(world, ["run"], **{PROFILE: configured})
    assert result.get("profile_path") == str(configured) and result["suite"] == "static"
    flagged = settings(world, ["run", "--profile", str(other)], **{PROFILE: configured})
    assert flagged["profile_path"] == str(other) and flagged["suite"] == "tests"


def test_a_saved_run_ignores_the_profile_setting(world, tmp_path):
    configured = profile_file(tmp_path / "configured", {"suite": "tests"})
    saved = tmp_path / "saved run"
    saved.mkdir()
    (saved / "plan.json").write_text(json.dumps({"suite": "static", "mode": "protocol-fixture",
                                                 "output": str(tmp_path / "runs")}))
    result = settings(world, ["start", "--from", str(saved)], **{PROFILE: configured})
    assert result["suite"] == "static" and "profile_path" not in result
    assert result["interpreter"] == sys.executable


def test_list_and_setup_accept_the_profile_setting_in_place_of_the_flag(world, tmp_path, capsys, monkeypatch):
    runs = tmp_path / "operator runs"
    configured = profile_file(tmp_path / "configured", {"output": str(runs)})
    cli = kit("operator_cli")
    loaded = dev(world, **{PROFILE: configured})
    code = with_dev(cli.main, ["list"], direct_runner=None, preflight=None, dev=loaded)
    listed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (code, listed.get("status"), listed.get("runs")) == (0, "passed", []), listed
    calls = []
    monkeypatch.setattr(cli.profiles, "setup", lambda path, **kwargs: calls.append(Path(path)) or {"status": "passed"})
    code = with_dev(cli.main, ["setup"], direct_runner=None, preflight=None, dev=loaded)
    capsys.readouterr()
    assert code == 0 and calls == [configured]


class Exec(Exception):
    pass


def wrapper_interpreter(directory):
    """An executable path that runs this test's Python, distinguishable from ``sys.executable``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "configured python"
    path.write_text(f"#!/bin/sh\nexec '{sys.executable}' \"$@\"\n")
    path.chmod(0o700)
    return path


@pytest.mark.parametrize("flag", [False, True], ids=["setting", "flag-wins"])
def test_the_bootstrap_selects_the_interpreter_of_the_profile_setting_unless_a_profile_is_given(
        world, tmp_path, monkeypatch, flag):
    configured = profile_file(tmp_path / "configured", {"interpreter": str(wrapper_interpreter(tmp_path / "a"))})
    other = profile_file(tmp_path / "other", {"interpreter": str(wrapper_interpreter(tmp_path / "b"))})
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "cache"))
    profiles = kit("adapters.profiles")

    def exec_(path, argv):
        raise Exec(path, argv)

    monkeypatch.setattr(profiles.os, "execv", exec_)
    argv = ["run", "--profile", str(other)] if flag else ["run"]
    with pytest.raises(Exec) as selected:
        with_dev(profiles.bootstrap, argv, root=ROOT, dev=dev(world, **{PROFILE: configured}))
    expected = tmp_path / ("b" if flag else "a") / "configured python"
    assert selected.value.args[0] == str(expected)
    assert selected.value.args[1][-len(argv):] == argv


# Interpreter -------------------------------------------------------------------------------------

def test_a_fresh_run_without_a_profile_uses_the_interpreter_setting_and_keeps_its_symlink(world, tmp_path):
    link = tmp_path / "env" / "bin" / "python"
    link.parent.mkdir(parents=True)
    link.symlink_to(sys.executable)
    assert settings(world, ["run"])["interpreter"] == sys.executable
    assert settings(world, ["run"], **{INTERPRETER: link})["interpreter"] == str(link)


def test_a_profile_keeps_its_own_interpreter_rules(world, tmp_path):
    profile = profile_file(tmp_path / "operator")
    elsewhere = trusted_executable(tmp_path / "elsewhere")
    result = settings(world, ["run", "--profile", str(profile)], **{INTERPRETER: elsewhere})
    assert result["interpreter"] == sys.executable


# Native configuration ----------------------------------------------------------------------------

def test_native_mode_takes_the_config_setting_when_neither_flag_nor_profile_sets_one(world, tmp_path):
    configured, flagged, profiled = (tmp_path / name for name in ("dev.json", "flag.json", "profile.json"))
    for path in (configured, flagged, profiled):
        path.write_text("{}")
    values = {NATIVE_CONFIG: configured}
    assert settings(world, ["run", "--mode", "native"], **values)["config"] == str(configured)
    assert settings(world, ["run", "--mode", "native", "--config", str(flagged)], **values)["config"] == str(flagged)
    assert settings(world, ["run", "--mode", "native"])["config"] is None
    silent = profile_file(tmp_path / "silent", {"mode": "native"})
    assert load_profile(world, silent, **values)["config"] == str(configured)
    explicit = profile_file(tmp_path / "explicit", {"mode": "native", "config": str(profiled)})
    assert load_profile(world, explicit, **values)["config"] == str(profiled)


def test_protocol_mode_ignores_the_config_setting(world, tmp_path):
    configured = tmp_path / "dev.json"
    configured.write_text("{}")
    assert settings(world, ["run", "--suite", "static"], **{NATIVE_CONFIG: configured})["config"] is None


# Terminal binary ---------------------------------------------------------------------------------

def default_host(binary=None):
    selection = kit("adapters.terminal_selection").parse(None)
    if binary is None:
        return selection
    return {**selection, "options": {**(selection.get("options") or {}), "binary": str(binary)}}


def selected_host(result):
    return kit("adapters.terminal_selection").parse(result["terminal_host"])


def test_the_terminal_binary_setting_becomes_the_default_hosts_binary_option(world, tmp_path):
    binary = trusted_executable(tmp_path / "bin" / "terminal")
    assert selected_host(settings(world, ["run"], **{TERMINAL_BINARY: binary})) == default_host(binary)
    assert settings(world, ["run"])["terminal_host"] in (None, default_host())


def test_a_terminal_host_flag_or_profile_field_wins_over_the_binary_setting(world, tmp_path):
    binary = trusted_executable(tmp_path / "bin" / "terminal")
    plain = {"adapter": default_host()["adapter"]}
    flagged = settings(world, ["run", "--terminal-host", json.dumps(plain)], **{TERMINAL_BINARY: binary})
    assert selected_host(flagged) == plain
    profile = profile_file(tmp_path / "operator", {"terminal_host": plain})
    assert selected_host(load_profile(world, profile, **{TERMINAL_BINARY: binary})) == plain


# Evidence ----------------------------------------------------------------------------------------

def expected_dev_settings(values):
    """Testkit settings given a non-default value, plus every evidence setting whatever its source."""
    recorded = {key: {"value": "", "source": "default"} for key in contract.EVIDENCE_KEYS}
    recorded.update({key: {"value": str(value), "source": "env"} for key, value in values.items()
                     if key in contract.SLICE_C})
    return recorded


def test_the_plan_records_the_developer_settings_that_shaped_the_run(world, tmp_path):
    values = {INTERPRETER: trusted_executable(tmp_path / "py"), TERMINAL_BINARY: trusted_executable(tmp_path / "terminal"),
              "XORCHESTRATE_DEV_SERENA_VERSION": "9.9.9"}
    result = settings(world, ["start", "--suite", "static", "--output", str(tmp_path / "runs")], **values)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    composition = kit("operator_composition")
    directory = composition.prepare_run({**result, "root": str(candidate)})
    try:
        plan = json.loads((directory / "plan.json").read_text())
    finally:
        composition.remove_endpoint(kit("adapters.terminals").run_endpoint(directory))
    assert plan.get("dev_settings") == expected_dev_settings(values)


def test_a_foreground_report_carries_the_same_developer_settings(world, tmp_path, capsys):
    values = {TERMINAL_BINARY: trusted_executable(tmp_path / "terminal")}
    seen = {}

    def runner(**options):
        seen.update(options)
        return {"status": "passed", "checks": [], "environment": {"python": "test"}}

    cli = kit("operator_cli")
    code = with_dev(cli.main, ["run", "--suite", "static", "--output", str(tmp_path / "runs")],
                    direct_runner=runner, preflight=None, dev=dev(world, **values))
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, report
    assert report.get("environment", {}).get("dev_settings") == expected_dev_settings(values)


# The bootstrap's bytecode cache ------------------------------------------------------------------

@pytest.fixture
def shell(tmp_path):
    subject = tmp_path / "checkout"
    script = dev_shell_subject(subject)
    home = tmp_path / "home"
    home.mkdir()
    return subject, script, home


def test_the_bootstrap_uses_the_pycache_setting_when_the_variable_is_unset(shell, tmp_path):
    subject, script, home = shell
    cache = tmp_path / "dev cache" / "nested"
    result = run_shell(script, ["doctor"], cache=None, home=home, **{PYCACHE: str(cache)})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cache"] == str(cache)
    assert stat.S_IMODE(cache.stat().st_mode) == 0o700
    assert not list(subject.rglob("__pycache__")), "the settings lookup wrote bytecode into the candidate"


def test_the_pycache_variable_wins_and_an_unset_setting_keeps_a_fresh_temporary_cache(shell, tmp_path):
    _, script, home = shell
    variable = tmp_path / "variable cache"
    result = run_shell(script, ["doctor"], cache=variable, home=home, **{PYCACHE: str(tmp_path / "dev cache")})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cache"] == str(variable)
    assert not (tmp_path / "dev cache").exists()
    result = run_shell(script, ["doctor"], cache=None, home=home)
    assert result.returncode == 0, result.stderr
    fresh = Path(json.loads(result.stdout)["cache"])
    try:
        assert fresh.is_dir() and tmp_path not in fresh.parents
        assert Path(tempfile.gettempdir()) in fresh.parents or fresh.parent == Path("/tmp")
    finally:
        shutil.rmtree(fresh, ignore_errors=True)


def test_a_pycache_setting_inside_the_candidate_is_refused(shell):
    subject, script, home = shell
    inside = subject / "cache-inside"
    result = run_shell(script, ["doctor"], cache=None, home=home, **{PYCACHE: str(inside)})
    assert result.returncode == 2 and "outside" in result.stderr, (result.stdout, result.stderr)
    assert not inside.exists()


# Neutrality and documentation --------------------------------------------------------------------

def test_the_kit_reads_the_settings_layer_and_the_product_never_does():
    readers = [path for path in tracked("scripts/testkit/*.py")
               if any(name == "scripts.devconfig" or name.startswith("scripts.devconfig.") for name in imports(path))]
    assert readers, "the test kit does not read developer settings"
    product = [path for path in tracked("xorchestrate/*.py")
               if any(name.startswith("scripts.devconfig") for name in imports(path))]
    assert product == [], product
    roles = json.loads((ROOT / "contracts/hexagonal.json").read_text())["roles"]
    assert any("devconfig" in roles[name].get("allowed_roles", ()) for name in contract.CONSUMER_ROLES
               if name.startswith("testkit_") and name in roles)


def test_the_live_testkit_guide_documents_the_new_default_and_the_precedence():
    text = (ROOT / "docs/live-testkit.md").read_text(encoding="utf-8")
    assert contract.LEGACY_OUTPUT_NAME not in text
    assert OUTPUT in text
    assert "TMPDIR" in text and "PATH" in text


# Loading settings for the kit starts no process ----------------------------------------------------

def kit_section_view(config):
    """What a load says about the test kit's own settings: their explained values and the warnings."""
    lines = [line for line in config.explain() if line.split("=", 1)[0] in contract.SLICE_C]
    return lines, list(config.warnings)


def hermetic(monkeypatch, world, calls=None):
    """The kit's own settings lookup, run for real but against the throwaway repository and HOME only."""
    profiles = kit("adapters.profiles")
    real = profiles.load_dev
    monkeypatch.setattr(os, "environ", {**world.env()})

    def load_dev(root):
        if calls is not None:
            calls.append(root)
        return real(world.main)

    monkeypatch.setattr(profiles, "load_dev", load_dev)

@pytest.mark.parametrize("where", ["main", "linked"])
def test_the_kit_loads_developer_settings_without_starting_a_process(world, monkeypatch, where):
    repo = world.main if where == "main" else world.tree
    world.repo_file("XORCHESTRATE_DEV_TESTKIT_OUTPUT=kit-runs\n")
    monkeypatch.setattr(os, "environ", {**world.env()})
    with no_processes(monkeypatch):
        loaded = kit("adapters.profiles").load_dev(repo)
    assert loaded is not None
    assert kit_section_view(loaded) == kit_section_view(world.load(repo)), "the kit reads only its own section"
    assert loaded.get(OUTPUT) == world.main / "kit-runs"


@pytest.mark.parametrize("action", ["doctor", "plan"])
def test_doctor_and_plan_load_the_checkouts_settings_without_starting_a_process(world, monkeypatch, tmp_path, capsys,
                                                                              action):
    cli = kit("operator_cli")
    composition = kit("composition")
    calls = []
    hermetic(monkeypatch, world, calls)
    with no_processes(monkeypatch) as attempts:
        code = cli.main([action, "--suite", "static", "--output", str(tmp_path / "runs")],
                        direct_runner=composition.run, preflight=composition.preflight)
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert attempts == [], attempts
    assert calls, "the kit did not look for developer settings"
    assert code == 0 and report.get("status") == "passed", report


# Review findings: robustness, trust and evidence ----------------------------------------------------

CONTROL_ACTIONS = ("status", "watch", "attach", "wait", "stop", "close", "collect", "verify")


def last_json(capsys):
    captured = capsys.readouterr()
    return json.loads(captured.out.strip().splitlines()[-1]), captured.err


@pytest.mark.parametrize("action", [*CONTROL_ACTIONS, "stories"])
def test_control_commands_never_load_developer_settings(monkeypatch, tmp_path, capsys, action):
    cli = kit("operator_cli")
    profiles = kit("adapters.profiles")
    calls = []

    def refuse(root):
        calls.append(root)
        raise ValueError("developer settings: planted: a control command loaded developer settings")

    monkeypatch.setattr(profiles, "load_dev", refuse)
    argv = ["stories"] if action == "stories" else [action, str(tmp_path / "no-such-run")]
    cli.main(argv, direct_runner=None, preflight=None)
    capsys.readouterr()
    assert calls == [], f"{action} loaded developer settings"
    if action != "stories":
        monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "cache"))
        monkeypatch.setattr(profiles.os, "execv", lambda *args: (_ for _ in ()).throw(Exec(*args)))
        with pytest.raises((Exec, ValueError, OSError)):
            profiles.bootstrap(argv, root=ROOT)
        assert calls == [], f"the bootstrap loaded developer settings for {action}"


def test_a_bad_value_in_another_section_never_reaches_the_kit(world, monkeypatch, tmp_path, capsys):
    world.repo_file("XORCHESTRATE_DEV_SERENA_TIMEOUT=abc\nXORCHESTRATE_DEV_TESTKIT_OUTPUT=kit-runs\n")
    hermetic(monkeypatch, world)
    loaded = kit("adapters.profiles").load_dev(ROOT)
    assert loaded is not None and loaded.get(OUTPUT) == world.main / "kit-runs"
    cli = kit("operator_cli")
    composition = kit("composition")
    code = cli.main(["plan", "--suite", "static", "--output", str(tmp_path / "runs")],
                    direct_runner=composition.run, preflight=composition.preflight)
    report, _ = last_json(capsys)
    assert code == 0 and report["status"] == "passed", report


def test_an_unsafe_testkit_setting_is_a_setup_error_naming_it(world, monkeypatch, tmp_path, capsys):
    hermetic(monkeypatch, world)
    monkeypatch.setitem(os.environ, TERMINAL_BINARY, str(tmp_path / "no-such-terminal"))
    cli = kit("operator_cli")
    code = cli.main(["plan", "--suite", "static", "--output", str(tmp_path / "runs")],
                    direct_runner=None, preflight=None)
    report, _ = last_json(capsys)
    assert code == 1 and report["status"] == "setup_error", report
    assert TERMINAL_BINARY in report["diagnostic"] and "UNSAFE_SETTING" in report["diagnostic"], report


def test_an_interpreter_setting_inside_the_candidate_is_refused(world):
    inside = ROOT / "scripts" / "proof.sh"
    with pytest.raises(ValueError, match="outside the measured candidate"):
        settings(world, ["run"], **{INTERPRETER: inside})


def test_doctor_with_a_profile_checks_the_terminal_binary_and_config_settings(world, tmp_path, monkeypatch):
    profiles = kit("adapters.profiles")
    binary = trusted_executable(tmp_path / "bin" / "terminal")
    config = trusted_file(tmp_path / "native.json")
    seen = []
    monkeypatch.setattr(profiles.terminal_selection, "readiness",
                        lambda selection, **kwargs: seen.append(selection) or {"name": "terminal_host", "passed": True})
    acceptance = profile_file(tmp_path / "acceptance", {"suite": "acceptance"})
    with_dev(profiles.doctor, acceptance, root=ROOT, dev=dev(world, **{TERMINAL_BINARY: binary}))
    assert seen and kit("adapters.terminal_selection").parse(seen[-1]) == default_host(binary), seen
    native = profile_file(tmp_path / "native", {"mode": "native", "suite": "static"})
    result = with_dev(profiles.doctor, native, root=ROOT, dev=dev(world, **{NATIVE_CONFIG: config}))
    assert result["profile"]["config"] == str(config)


def test_the_doctor_command_hands_its_developer_settings_to_the_profile_doctor(world, tmp_path, monkeypatch, capsys):
    cli = kit("operator_cli")
    received = []
    monkeypatch.setattr(cli.profiles, "doctor",
                        lambda path, **kwargs: received.append(kwargs.get("dev")) or {"status": "passed"})
    loaded = dev(world)
    profile = profile_file(tmp_path / "operator", {"suite": "static"})
    with_dev(cli.main, ["doctor", "--profile", str(profile)], direct_runner=None,
             preflight=lambda **kwargs: {"status": "passed", "checks": []}, dev=loaded)
    capsys.readouterr()
    assert received == [loaded]


def test_a_foreground_runs_report_file_carries_the_developer_settings(world, tmp_path, capsys, monkeypatch):
    values = {TERMINAL_BINARY: trusted_executable(tmp_path / "terminal")}
    cli = kit("operator_cli")
    composition = kit("composition")
    child_environments = []
    real = composition.cache_environment

    def recorded(root):
        environment = real(root)
        child_environments.append(environment)
        return environment

    monkeypatch.setattr(composition, "cache_environment", recorded)
    with_dev(cli.main, ["run", "--suite", "static", "--output", str(tmp_path / "runs")],
             direct_runner=composition.run, preflight=composition.preflight, dev=dev(world, **values))
    report, _ = last_json(capsys)
    on_disk = json.loads((Path(report["directory"]) / "report.json").read_text())
    assert report["status"] == "passed" and on_disk["status"] == "passed", "the run's own checks failed"
    assert child_environments, "the run never prepared its child commands' environment"
    for environment in child_environments:
        assert all(isinstance(name, str) and isinstance(value, str) for name, value in environment.items()), \
            environment
    assert on_disk.get("environment", {}).get("dev_settings") == expected_dev_settings(values)


def test_a_repeated_run_keeps_the_developer_settings_of_the_run_it_repeats(world, tmp_path):
    recorded = {TERMINAL_BINARY: {"value": "/recorded/terminal", "source": "user_file /recorded/devtools.env:1"}}
    saved = tmp_path / "saved run"
    saved.mkdir()
    (saved / "plan.json").write_text(json.dumps({"suite": "static", "mode": "protocol-fixture",
                                                 "output": str(tmp_path / "runs"), "dev_settings": recorded}))
    current = {TERMINAL_BINARY: trusted_executable(tmp_path / "current-terminal")}
    result = settings(world, ["start", "--from", str(saved)], **current)
    assert result.get("dev_settings") == recorded


def test_settings_warnings_go_to_stderr_one_line_each(world, tmp_path, monkeypatch, capsys):
    world.repo_file("XORCHESTRATE_DEV_NO_SUCH_SETTING=1\n")
    loaded = dev(world)
    assert [warning[0] for warning in loaded.warnings] == ["UNKNOWN_KEY"]
    cli = kit("operator_cli")
    composition = kit("composition")
    with_dev(cli.main, ["plan", "--suite", "static", "--output", str(tmp_path / "runs")],
             direct_runner=composition.run, preflight=composition.preflight, dev=loaded)
    report, err = last_json(capsys)
    assert report["status"] == "passed", report
    lines = [line for line in err.splitlines() if "UNKNOWN_KEY" in line]
    assert len(lines) == 1 and "XORCHESTRATE_DEV_NO_SUCH_SETTING" in lines[0], err
    profiles = kit("adapters.profiles")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "cache"))
    monkeypatch.setattr(profiles.os, "execv", lambda *args: (_ for _ in ()).throw(Exec(*args)))
    with pytest.raises(Exec):
        with_dev(profiles.bootstrap, ["plan"], root=ROOT, dev=loaded)
    err = capsys.readouterr().err
    assert len([line for line in err.splitlines() if "UNKNOWN_KEY" in line]) == 1, err


def test_a_skipped_settings_file_is_recorded_in_the_plan(world, tmp_path):
    repo_file = world.repo_file("XORCHESTRATE_DEV_TESTKIT_OUTPUT=never-read\n")
    repo_file.chmod(0o664)
    result = settings(world, ["start", "--suite", "static", "--output", str(tmp_path / "runs")])
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    composition = kit("operator_composition")
    directory = composition.prepare_run({**result, "root": str(candidate)})
    try:
        plan = json.loads((directory / "plan.json").read_text())
    finally:
        composition.remove_endpoint(kit("adapters.terminals").run_endpoint(directory))
    skipped = plan.get("dev_settings", {}).get("_skipped")
    assert isinstance(skipped, list) and [entry.get("file") for entry in skipped] == [str(repo_file)], plan
    assert all(isinstance(entry.get("reason"), str) and entry["reason"] for entry in skipped), skipped
