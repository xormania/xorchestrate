"""The launcher reports its install source from PEP 610 metadata and its installer's markers."""

import json
import shlex

import pytest

from xorchestrate import cli, installation

GIT = {"url": "https://github.com/xormania/xorchestrate",
       "vcs_info": {"vcs": "git", "commit_id": "0123abcd", "requested_revision": "v0.2.0"}}
LOCAL = {"url": "file:///home/me/my%20checkout", "dir_info": {}}
EDITABLE = {"url": "file:///home/me/checkout", "dir_info": {"editable": True}}
ARCHIVE = {"url": "file:///downloads/xorchestrate-0.2.0-py3-none-any.whl", "archive_info": {"hashes": {}}}
RECORDS = {"index": None, "git": GIT, "local": LOCAL, "editable": EDITABLE, "archive": ARCHIVE}


class Distribution:
    def __init__(self, record):
        self.text = record if record is None or isinstance(record, str) else json.dumps(record)

    def read_text(self, name):
        assert name == "direct_url.json"
        return self.text


def distribution(record):
    return lambda name: Distribution(record)


def environment(root, kind):
    """A directory shaped like the environment each installer creates."""
    if kind == "uv-tool":
        prefix = root / "tools" / "xorchestrate"
        prefix.mkdir(parents=True)
        (prefix / "uv-receipt.toml").write_text("[tool]\n")
    elif kind == "pipx":
        prefix = root / "pipx" / "venvs" / "xorchestrate"
        prefix.mkdir(parents=True)
        (prefix / "pipx_metadata.json").write_text("{}")
    elif kind == "uvx":
        (root / "cache").mkdir()
        (root / "cache" / "CACHEDIR.TAG").write_text(installation.CACHEDIR_SIGNATURE + "\n# uv\n")
        prefix = root / "cache" / "archive-v0" / "ynAiHlAEfV1GhUOd"
        prefix.mkdir(parents=True)
    else:
        prefix = root / "venv"
        prefix.mkdir()
    return prefix


@pytest.mark.parametrize(("record", "source", "commit", "editable"), [
    (None, "index", None, False),
    (GIT, "git", "0123abcd", False),
    (LOCAL, "local", None, False),
    (EDITABLE, "local", None, True),
    (ARCHIVE, "archive", None, False),
    ({"url": "https://example.invalid/x.git", "vcs_info": {"vcs": "git", "commit_id": 5}}, "git", None, False),
])
def test_direct_url_records_map_to_install_sources(record, source, commit, editable):
    assert installation.classify_source(record) == (source, record and record["url"], commit, editable)


@pytest.mark.parametrize("text", [None, "", "not json", "[]", "{\"vcs_info\": "])
def test_absent_or_unreadable_records_read_as_index_installs(text):
    assert installation.direct_url(distribution(text)) is None
    assert installation.installation(distribution(text), prefix="/nonexistent").source == "index"


def test_a_missing_distribution_reads_as_index():
    def missing(name):
        raise installation.metadata.PackageNotFoundError(name)

    assert installation.direct_url(missing) is None and cli.installed_commit(missing) is None


@pytest.mark.parametrize("kind", ["uv-tool", "pipx", "uvx", "environment"])
def test_installers_are_recognised_from_their_environment_markers(tmp_path, kind):
    assert installation.detect_installer(environment(tmp_path, kind)) == kind


def test_a_cache_shaped_directory_without_uvs_tag_is_not_uvx(tmp_path):
    prefix = tmp_path / "cache" / "archive-v0" / "env"
    prefix.mkdir(parents=True)
    (tmp_path / "cache" / "CACHEDIR.TAG").write_text("Signature: something else\n")
    assert installation.detect_installer(prefix) == "environment"


# Independent oracle: the command a user runs to upgrade each (installer, source) pair.
UPGRADES = {
    ("uv-tool", "index"): "uv tool upgrade xorchestrate",
    ("uv-tool", "git"): "uv tool install --force 'git+https://github.com/xormania/xorchestrate@<tag>'",
    ("uv-tool", "local"): "uv tool install --force '/home/me/my checkout'",
    ("uv-tool", "editable"): "uv tool install --force --editable /home/me/checkout",
    ("uv-tool", "archive"): "uv tool install --force '<newer wheel or sdist>'",
    ("pipx", "index"): "pipx upgrade xorchestrate",
    ("pipx", "git"): "pipx install --force 'git+https://github.com/xormania/xorchestrate@<tag>'",
    ("pipx", "editable"): "pipx install --force --editable /home/me/checkout",
    ("uvx", "index"): "uvx --from xorchestrate@latest xorchestrate",
    ("uvx", "git"): "uvx --refresh --from 'git+https://github.com/xormania/xorchestrate@<tag>' xorchestrate",
    ("uvx", "local"): "uvx --refresh --from '/home/me/my checkout' xorchestrate",
    ("environment", "index"): "uv pip install --python /venv/bin/python --upgrade xorchestrate",
    ("environment", "editable"): "uv pip install --python /venv/bin/python --upgrade --editable /home/me/checkout",
}


@pytest.mark.parametrize(("installer", "record"), UPGRADES)
def test_each_source_maps_to_its_upgrade_command(tmp_path, installer, record):
    info = installation.installation(distribution(RECORDS[record]), prefix=environment(tmp_path, installer),
                                     python="/venv/bin/python")
    command = installation.upgrade_command(info)
    assert command == UPGRADES[installer, record]
    assert shlex.split(command)[0] in {"uv", "uvx", "pipx"}


def test_the_upgrade_command_names_the_selected_package(tmp_path):
    info = installation.installation(distribution(None), prefix=environment(tmp_path, "uv-tool"),
                                     name="xorchestrate-pro")
    assert installation.upgrade_command(info, name="xorchestrate-pro") == "uv tool upgrade xorchestrate-pro"


@pytest.mark.parametrize(("installer", "record", "reported"), [
    ("uv-tool", None, "index"), ("uv-tool", GIT, "git"), ("pipx", EDITABLE, "local"),
    ("environment", ARCHIVE, "archive"), ("uvx", None, "uvx"), ("uvx", ARCHIVE, "uvx"),
])
def test_version_reports_the_install_source_and_upgrade_command(tmp_path, installer, record, reported):
    prefix = environment(tmp_path, installer)
    info = cli.version_info(distribution(record), prefix=prefix)
    assert info["install_source"] == reported
    assert info["commit"] == ("0123abcd" if record is GIT else None)
    assert info["upgrade_command"] == installation.upgrade_command(
        installation.installation(distribution(record), prefix=prefix))
