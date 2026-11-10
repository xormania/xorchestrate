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


def distribution(record, **others):
    """Installed distributions: ``xorchestrate`` with ``record``, and ``others`` by name with theirs."""
    installed = {"xorchestrate": record, **others}

    def find(name):
        if name not in installed:
            raise installation.metadata.PackageNotFoundError(name)
        return Distribution(installed[name])
    return find


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


def missing(name):
    raise installation.metadata.PackageNotFoundError(name)


def test_a_missing_distribution_is_unknown_with_no_upgrade_command(tmp_path):
    assert installation.direct_url(missing) is None and cli.installed_commit(missing) is None
    info = cli.version_info(missing, prefix=environment(tmp_path, "uv-tool"))
    assert (info["install_source"], info["commit"], info["upgrade_command"]) == ("unknown", None, None)


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
    info = installation.installation(distribution(None, **{"xorchestrate-pro": None}),
                                     prefix=environment(tmp_path, "uv-tool"), name="xorchestrate-pro")
    assert installation.upgrade_command(info, name="xorchestrate-pro") == "uv tool upgrade xorchestrate-pro"


# Receipts as uv 0.13.7 writes them for `uv tool install xorchestrate==0.2.0` and `uv tool install xorchestrate`.
PINNED_RECEIPT = """[tool]
requirements = [{ name = "xorchestrate", specifier = "==0.2.0" }]
python = "3.12"
entrypoints = [
    { name = "xorchestrate", install-path = "/home/me/.local/bin/xorchestrate", from = "xorchestrate" },
]
"""
UNPINNED_RECEIPT = PINNED_RECEIPT.replace(', specifier = "==0.2.0"', "")


@pytest.mark.parametrize(("receipt", "expected"), [
    (PINNED_RECEIPT, "uv tool install xorchestrate@latest"),
    (PINNED_RECEIPT.replace("==0.2.0", ">=0.2,<0.3"), "uv tool install xorchestrate@latest"),
    (UNPINNED_RECEIPT, "uv tool upgrade xorchestrate"),
    (PINNED_RECEIPT.replace('name = "xorchestrate", specifier', 'name = "other", specifier'),
     "uv tool upgrade xorchestrate"),
    ("not toml [", "uv tool upgrade xorchestrate"),
])
def test_a_pinned_uv_tool_install_is_reinstalled_without_its_pin(tmp_path, receipt, expected):
    prefix = environment(tmp_path, "uv-tool")
    (prefix / "uv-receipt.toml").write_text(receipt)
    assert cli.version_info(distribution(None), prefix=prefix)["upgrade_command"] == expected


@pytest.mark.parametrize(("requested", "expected"), [
    ("xorchestrate==0.2.0", "pipx install --force xorchestrate"),
    ("Xorchestrate [extra] ~= 0.2", "pipx install --force xorchestrate"),
    ("xorchestrate", "pipx upgrade xorchestrate"),
    ("xorchestrate-pro==1.0", "pipx upgrade xorchestrate"),
])
def test_a_pinned_pipx_install_is_reinstalled_without_its_pin(tmp_path, requested, expected):
    prefix = environment(tmp_path, "pipx")
    (prefix / "pipx_metadata.json").write_text(json.dumps(
        {"main_package": {"package": "xorchestrate", "package_or_url": requested}, "pipx_metadata_version": "0.5"}))
    assert cli.version_info(distribution(None), prefix=prefix)["upgrade_command"] == expected


@pytest.mark.parametrize(("installer", "record", "expected"), [
    ("uv-tool", None, ("index", None, "uv tool upgrade xorchestrate")),
    ("uv-tool", GIT, ("git", "0123abcd",
                      "uv tool install --force 'git+https://github.com/xormania/xorchestrate@<tag>'")),
    ("pipx", EDITABLE, ("local", None, "pipx install --force --editable /home/me/checkout")),
    ("environment", ARCHIVE, ("archive", None,
                              "uv pip install --python /venv/bin/python --upgrade '<newer wheel or sdist>'")),
    ("uvx", None, ("uvx", None, "uvx --from xorchestrate@latest xorchestrate")),
    ("uvx", ARCHIVE, ("uvx", None, "uvx --refresh --from '<newer wheel or sdist>' xorchestrate")),
])
def test_version_reports_the_install_source_and_upgrade_command(tmp_path, monkeypatch, installer, record, expected):
    monkeypatch.setattr(installation.sys, "executable", "/venv/bin/python")
    info = cli.version_info(distribution(record), prefix=environment(tmp_path, installer))
    assert (info["install_source"], info["commit"], info["upgrade_command"]) == expected


# `uv tool install xork` and `pipx install xork` record the alias, not the package it depends on.
XORK_RECEIPT = PINNED_RECEIPT.replace('name = "xorchestrate", specifier', 'name = "xork", specifier')
UNPINNED_XORK_RECEIPT = UNPINNED_RECEIPT.replace('name = "xorchestrate" }', 'name = "xork" }')
# `uv tool install xorchestrate --with xork`: the package is requested, the alias only rides along.
WITH_XORK_RECEIPT = UNPINNED_RECEIPT.replace('name = "xorchestrate" }', 'name = "xorchestrate" }, { name = "xork" }')


@pytest.mark.parametrize(("installer", "record", "receipt", "expected"), [
    ("uv-tool", None, UNPINNED_XORK_RECEIPT, ("index", None, "uv tool upgrade xork")),
    ("uv-tool", None, XORK_RECEIPT, ("index", None, "uv tool install xork@latest")),
    ("pipx", None, {"main_package": {"package": "xork", "package_or_url": "xork==0.2.0"}},
     ("index", None, "pipx install --force xork")),
    ("pipx", None, {"main_package": {"package": "xork", "package_or_url": "xork"}}, ("index", None, "pipx upgrade xork")),
    ("uvx", None, None, ("uvx", None, "uvx --from xork@latest xorchestrate")),
    ("uv-tool", ARCHIVE, UNPINNED_XORK_RECEIPT, ("archive", None, "uv tool install --force '<newer wheel or sdist>'")),
    # The commit is the running package's; here it came from the index.
    ("uv-tool", GIT, UNPINNED_XORK_RECEIPT, ("git", None, "uv tool install --force 'git+https://github.com/xormania/xorchestrate@<tag>'")),
    ("environment", None, None, ("index", None, "uv pip install --python /venv/bin/python --upgrade xork")),
])
def test_an_installed_xork_alias_is_what_the_launcher_reports_and_upgrades(tmp_path, monkeypatch, installer, record,
                                                                            receipt, expected):
    """The package itself came from the index as the alias's dependency; the alias's record decides."""
    monkeypatch.setattr(installation.sys, "executable", "/venv/bin/python")
    prefix = environment(tmp_path, installer)
    if isinstance(receipt, str):
        (prefix / "uv-receipt.toml").write_text(receipt)
    elif receipt is not None:
        (prefix / "pipx_metadata.json").write_text(json.dumps(receipt))
    info = cli.version_info(distribution(None, xork=record), prefix=prefix)
    assert (info["install_source"], info["commit"], info["upgrade_command"]) == expected


def test_without_the_package_an_alias_alone_is_unknown(tmp_path):
    def only_alias(name):
        if name != "xork":
            raise installation.metadata.PackageNotFoundError(name)
        return Distribution(None)
    info = cli.version_info(only_alias, prefix=environment(tmp_path, "uv-tool"))
    assert (info["install_source"], info["upgrade_command"]) == ("unknown", None)


@pytest.mark.parametrize(("installer", "receipt", "own", "expected"), [
    # An editable checkout with xork also installed: the checkout is what runs and what to upgrade.
    ("environment", None, EDITABLE,
     ("local", None, "uv pip install --python /venv/bin/python --upgrade --editable /home/me/checkout")),
    ("uvx", None, ARCHIVE, ("uvx", None, "uvx --refresh --from '<newer wheel or sdist>' xorchestrate")),
    ("uv-tool", WITH_XORK_RECEIPT, None, ("index", None, "uv tool upgrade xorchestrate")),
    ("pipx", {"main_package": {"package": "xorchestrate", "package_or_url": "xorchestrate"}}, None,
     ("index", None, "pipx upgrade xorchestrate")),
])
def test_an_alias_the_user_did_not_request_does_not_take_over(tmp_path, monkeypatch, installer, receipt, own,
                                                              expected):
    monkeypatch.setattr(installation.sys, "executable", "/venv/bin/python")
    prefix = environment(tmp_path, installer)
    if isinstance(receipt, str):
        (prefix / "uv-receipt.toml").write_text(receipt)
    elif receipt is not None:
        (prefix / "pipx_metadata.json").write_text(json.dumps(receipt))
    info = cli.version_info(distribution(own, xork=None), prefix=prefix)
    assert (info["install_source"], info["commit"], info["upgrade_command"]) == expected
