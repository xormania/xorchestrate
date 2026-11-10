"""Configuration format versions, structural validation without construction, and --version."""

import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import xorchestrate
from tests.hexagonal.support.projects import ProjectFixture
from xorchestrate import bridge, cli, composition, installation
from xorchestrate.composition import SUPPORTED_CONFIG_VERSIONS, build, config_version, load_config, validate_config
from xorchestrate.model import OrchestrationError

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
EXAMPLE_FILES = sorted(EXAMPLES.glob("*.json"))
RELEASE = "v" + xorchestrate.__version__


def copied_example(tmp_path, source):
    # Example paths reach up to two directories above their own; keep them inside tmp_path.
    target = tmp_path / "one" / "two" / "examples" / source.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    return target


def files_under(path):
    return sorted(str(item.relative_to(path)) for item in path.rglob("*") if item.is_file())


def failure(operation):
    with pytest.raises(OrchestrationError) as raised:
        operation()
    return raised.value.code, str(raised.value), raised.value.details


def add_session(config):
    config.setdefault("sessions", {})["broken"] = {"adapter": "codex", "options": {"cwd": False}}


def replace_store(value):
    def mutate(config):
        config["store"] = value
    return mutate


def set_key(key, value):
    def mutate(config):
        config[key] = value
    return mutate


STRUCTURAL_MUTATIONS = {
    "newer version": set_key("version", 2),
    "string version": set_key("version", "1"),
    "zero version": set_key("version", 0),
    "unknown section": set_key("extra", {}),
    "adapter name type": replace_store({"adapter": 5}),
    "unknown adapter field": replace_store({"adapter": "sqlite", "label": "x"}),
    "options type": replace_store({"adapter": "sqlite", "options": []}),
    "enabled type": replace_store({"adapter": "sqlite", "enabled": "yes"}),
    "unknown builtin": replace_store({"adapter": "postgres"}),
    "store path type": replace_store({"adapter": "sqlite", "options": {"path": 5}}),
    "collection type": set_key("providers", []),
    "collection name": set_key("providers", {"a/b": {"adapter": "cli"}}),
    "session cwd type": add_session,
}


def test_supported_versions_and_missing_version_warning(tmp_path):
    assert SUPPORTED_CONFIG_VERSIONS == {1}
    with build({"version": 1, "store": {"adapter": "memory"}}, tmp_path) as runtime:
        assert runtime.warnings == ()
    with build({"store": {"adapter": "memory"}}, tmp_path) as runtime:
        assert [warning["code"] for warning in runtime.warnings] == ["configuration_version_missing"]
        assert runtime.warnings[0]["assumed_version"] == 1
    assert validate_config({}, tmp_path).version == 1


@pytest.mark.parametrize("version", ["1", 1.0, True, 0, -1, None, [1]])
def test_non_integer_versions_are_configuration_errors(tmp_path, version):
    code, _, _ = failure(lambda: validate_config({"version": version}, tmp_path))
    assert code == "configuration_error"


def test_newer_and_older_versions_are_refused_by_name(tmp_path):
    code, message, details = failure(lambda: validate_config({"version": 2}, tmp_path))
    assert code == "configuration_version_unsupported" and "2" in message
    assert details == {"version": 2, "supported": [1], "release": RELEASE}
    assert failure(lambda: config_version({"version": 1}, supported={2, 3})) == (
        "configuration_migration_required",
        "Configuration format version 1 is older than this release reads; migrate the configuration to "
        "version 2. Configuration is never rewritten on load.",
        {"version": 1, "supported": [2, 3], "release": RELEASE, "target": 2},
    )
    code, _, _ = failure(lambda: config_version({"version": 2}, supported={1, 3}))
    assert code == "configuration_version_unsupported"


def test_refused_configuration_is_not_rewritten_and_creates_no_state(tmp_path):
    source = tmp_path / "config.json"
    text = json.dumps({"version": 2, "store": {"adapter": "sqlite", "options": {"path": "state.db"}}})
    source.write_text(text, encoding="utf-8")
    config, base = load_config(source)
    code, _, _ = failure(lambda: build(config, base))
    assert code == "configuration_version_unsupported"
    assert source.read_text(encoding="utf-8") == text and files_under(tmp_path) == ["config.json"]


@pytest.mark.parametrize("source", EXAMPLE_FILES, ids=lambda path: path.name)
def test_validation_imports_constructs_and_writes_nothing(tmp_path, monkeypatch, source):
    path = copied_example(tmp_path, source)
    config, base = load_config(path)
    assert config["version"] == 1

    def refuse_import(name, *args, **kwargs):
        raise AssertionError(f"validate_config imported {name}")

    monkeypatch.setattr(composition.importlib, "import_module", refuse_import)
    resolved = validate_config(config, base)
    assert (resolved.version, resolved.warnings) == (1, ())
    assert Path(resolved.adapters["store"].options["path"]).parent.is_relative_to(tmp_path)
    assert files_under(tmp_path) == [str(path.relative_to(tmp_path))]
    extension = validate_config({**config, "providers": {"x": {"adapter": "absent_package:factory"}}}, base)
    assert extension.collections["providers"]["x"].target == "absent_package:factory"


@pytest.mark.parametrize("source", EXAMPLE_FILES, ids=lambda path: path.name)
def test_validation_and_build_agree_on_every_example(tmp_path, source):
    path = copied_example(tmp_path, source)
    config, base = load_config(path)
    resolved = validate_config(config, base)
    with build(config, base) as runtime:
        assert runtime.warnings == resolved.warnings
        assert set(runtime.application.providers) == set(resolved.collections["providers"])
        assert set(runtime.application.sessions) == set(resolved.collections["sessions"])
        assert set(runtime.application.evaluators) == set(resolved.collections["evaluators"])
    for index, (name, mutate) in enumerate(STRUCTURAL_MUTATIONS.items()):
        # A fresh directory per mutation shows that no adapter created state.
        fresh = tmp_path / f"mutation-{index}"
        mutated_path = copied_example(fresh, source)
        mutated, mutated_base = load_config(mutated_path)
        mutate(mutated)
        expected = failure(lambda: validate_config(mutated, mutated_base))
        assert expected[0].startswith("configuration_"), name
        assert failure(lambda: build(mutated, mutated_base)) == expected, name
        assert files_under(fresh) == [str(mutated_path.relative_to(fresh))], name


def test_invalid_telemetry_is_a_warning_in_both_paths(tmp_path):
    config = {"version": 1, "store": {"adapter": "memory"}, "telemetry": {"adapter": "local", "options": []}}
    assert [w["code"] for w in validate_config(config, tmp_path).warnings] == ["telemetry_configuration_invalid"]
    with build(config, tmp_path) as runtime:
        assert type(runtime.application.telemetry).__name__ == "UnavailableTelemetry"
        assert [w["code"] for w in runtime.warnings] == ["telemetry_configuration_invalid"]


def run_cli(*argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            status = cli.main(list(argv))
        except SystemExit as exit_:
            status = exit_.code
    return status, output.getvalue()


def test_describe_reports_configuration_warnings(tmp_path, monkeypatch):
    for index, (config, expected) in enumerate((({"store": {"adapter": "memory"}}, ["configuration_version_missing"]),
                                                ({"version": 1, "store": {"adapter": "memory"}}, []))):
        project = ProjectFixture(tmp_path / str(index), config)
        monkeypatch.setenv("XORCHESTRATE_HOME", str(project.home))
        status, output = run_cli("--project", str(project.repo), "describe")
        reply = json.loads(output)
        assert status == 0 and reply["operations"]
        assert [warning["code"] for warning in reply["warnings"]] == expected


class Distribution:
    def __init__(self, text):
        self.text = text

    def read_text(self, name):
        assert name == "direct_url.json"
        return self.text


def test_installed_commit_comes_only_from_vcs_direct_url():
    vcs = json.dumps({"url": "https://example.invalid/repo.git",
                      "vcs_info": {"vcs": "git", "commit_id": "0123abcd", "requested_revision": "v0.2.0"}})
    assert cli.installed_commit(lambda name: Distribution(vcs)) == "0123abcd"
    for text in (json.dumps({"url": "file:///checkout", "dir_info": {"editable": True}}), None, "not json", "[]",
                 json.dumps({"vcs_info": {"commit_id": 5}})):
        assert cli.installed_commit(lambda name, text=text: Distribution(text)) is None

    def missing(name):
        raise installation.metadata.PackageNotFoundError(name)

    assert cli.installed_commit(missing) is None


def test_version_flag_prints_release_identity_for_a_controlled_installation(tmp_path, monkeypatch):
    prefix = tmp_path / "tools" / "xorchestrate"
    prefix.mkdir(parents=True)
    (prefix / "uv-receipt.toml").write_text('[tool]\nrequirements = [{ name = "xorchestrate", '
                                            'git = "https://example.invalid/repo.git?tag=v0.2.0" }]\n')
    vcs = json.dumps({"url": "https://example.invalid/repo.git",
                      "vcs_info": {"vcs": "git", "commit_id": "0123abcd", "requested_revision": "v0.2.0"}})
    monkeypatch.setattr(installation.metadata, "distribution", lambda name: Distribution(vcs))
    monkeypatch.setattr(sys, "prefix", str(prefix))
    status, output = run_cli("--version")
    assert status == 0
    assert json.loads(output) == {
        "version": xorchestrate.__version__, "commit": "0123abcd", "install_source": "git",
        "upgrade_command": "uv tool install --force 'git+https://example.invalid/repo.git@<tag>'",
        "config_versions": [1], "data_version": 1}


def test_version_flag_matches_between_the_module_entrypoint_and_the_cli():
    status, output = run_cli("--version")
    assert status == 0
    reply = json.loads(output)
    assert list(reply) == ["version", "commit", "install_source", "upgrade_command", "config_versions",
                           "data_version"]
    assert reply["version"] == xorchestrate.__version__
    assert reply["install_source"] in {"index", "git", "local", "archive", "uvx", "unknown"}
    process = subprocess.run([sys.executable, "-m", "xorchestrate", "--version"], capture_output=True,
                             text=True, timeout=60, check=True)
    assert json.loads(process.stdout) == reply


# Literal oracle for build()'s reported error. Structural errors, including path
# types and NUL bytes, have specific messages and are reported before any
# factory runs; a factory's own option error appears only for an otherwise
# structurally valid configuration.
PINNED_BUILD_ERRORS = {
    "store path type": ({"store": {"adapter": "sqlite", "options": {"path": 5}}},
                        ("configuration_error", "store adapter sqlite paths must be strings.")),
    "store path NUL": ({"store": {"adapter": "sqlite", "options": {"path": "state\x00.db"}}},
                       ("configuration_error", "Invalid store adapter sqlite path.")),
    "session cwd type": ({"store": {"adapter": "memory"},
                          "sessions": {"worker": {"adapter": "codex", "options": {"cwd": 7}}}},
                         ("configuration_error", "sessions adapter codex paths must be strings.")),
    "structural before factory": ({"store": {"adapter": "sqlite", "options": {"busy_timeout_ms": -1}}, "routing": 5},
                                  ("configuration_error", "routing requires an adapter and options object.")),
    "factory option": ({"store": {"adapter": "sqlite", "options": {"busy_timeout_ms": -1}}},
                       ("invalid_request", "SQLite busy_timeout_ms must be a nonnegative integer.")),
}


@pytest.mark.parametrize("name", PINNED_BUILD_ERRORS)
def test_build_reports_pinned_errors_with_structural_errors_first(tmp_path, name):
    config, (code, message) = PINNED_BUILD_ERRORS[name]
    with pytest.raises(OrchestrationError) as raised:
        build({"version": 1, **config}, tmp_path)
    assert (raised.value.code, str(raised.value)) == (code, message)
    if code == "configuration_error":
        assert files_under(tmp_path) == []


def bridge_config(**extra):
    return {**extra, "store": {"adapter": "sqlite"},
            "sessions": {"worker": {"adapter": "jsonrpc", "options": {"command": ["unused"]}}}}


def test_bridge_host_enforces_the_configuration_version(tmp_path, monkeypatch):
    assert validate_config(bridge.host_configuration(bridge_config(version=1), "worker"), tmp_path).warnings == ()
    assert [w["code"] for w in validate_config(bridge.host_configuration(bridge_config(), "worker"),
                                               tmp_path).warnings] == ["configuration_version_missing"]
    path = tmp_path / "config.json"
    path.write_text(json.dumps(bridge_config(version=2)), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["xorchestrate.bridge", "--config", str(path), "--driver", "worker",
                                      "--endpoint", str(tmp_path / "bridge.sock")])
    with pytest.raises(OrchestrationError) as refused:
        bridge.main()
    assert refused.value.code == "configuration_version_unsupported"
    assert files_under(tmp_path) == ["config.json"]
