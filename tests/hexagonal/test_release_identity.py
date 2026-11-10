"""The release identity gate refuses a tag, version, changelog or distribution that disagree."""

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import xorchestrate
from scripts.ci import release_identity

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "scripts/ci/release_identity.py"

ENTRY = """## {version} - 2026-11-10

### Added

- Something.

### Upgrade notes

- **Configuration format:** None.
- **Data:** None.
"""


def alias(root, *, version="1.2.3", dependencies=('xorchestrate==1.2.3',), name="xork", extra=""):
    (root / "packaging/xork").mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f'"{item}"' for item in dependencies)
    (root / "packaging/xork/pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\ndependencies = [{listed}]\n{extra}')


def project(root, *, init="1.2.3", pyproject="1.2.3", name="xorchestrate", changelog=None, alias_version=None):
    (root / "xorchestrate").mkdir(parents=True)
    (root / "xorchestrate/__init__.py").write_text(f'"""Package."""\n\n__version__ = "{init}"\n')
    (root / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "{pyproject}"\n')
    version = alias_version or pyproject
    alias(root, version=version, dependencies=(f"xorchestrate=={version}",))
    text = "# Changelog\n\n## Unreleased\n\n### Upgrade notes\n\n- None.\n\n" + ENTRY.format(version="1.2.3") \
        + "\n## 1.2.2\n\n### Upgrade notes\n\n- None.\n"
    (root / "CHANGELOG.md").write_text(text if changelog is None else changelog)
    return root


def gate(root, *arguments):
    return subprocess.run([sys.executable, "-I", str(COMMAND), "--root", str(root), *arguments],
                          capture_output=True, text=True, timeout=60, check=False)


def errors(root, **keywords):
    return release_identity.check(root, **keywords)[1]


def test_an_agreeing_release_passes_and_writes_its_notes_and_outputs(tmp_path):
    root = project(tmp_path / "candidate")
    notes, outputs = tmp_path / "notes.md", tmp_path / "outputs"
    result = gate(root, "--tag", "v1.2.3", "--notes-output", str(notes), "--github-output", str(outputs))
    assert result.returncode == 0, result.stdout
    report = json.loads(result.stdout)
    assert report["status"] == "passed" and report["changelog"] == {"entry": True, "upgrade_notes": True}
    assert report["aliases"] == [{"directory": "packaging/xork", "name": "xork", "version": "1.2.3",
                                  "dependencies": ["xorchestrate==1.2.3"]}]
    assert notes.read_text().startswith("### Added") and "**Data:** None." in notes.read_text()
    assert "## 1.2.2" not in notes.read_text()
    assert outputs.read_text() == "tag=v1.2.3\nversion=1.2.3\ndistribution_version=1.2.3\nprerelease=false\n"


def test_the_dry_run_expects_the_tag_named_by_the_package_version(tmp_path):
    root = project(tmp_path)
    report, problems = release_identity.check(root)
    assert problems == [] and report["tag"] == "v1.2.3" and report["tag_given"] is False


@pytest.mark.parametrize(("tag", "versions", "message"), [
    ("v1.2.4", {}, "__version__ '1.2.3' does not match tag 'v1.2.4'"),
    ("v1.2.3", {"init": "1.2.4"}, "__version__ '1.2.4' does not match tag 'v1.2.3'"),
    ("v1.2.3", {"pyproject": "1.2.4"}, "pyproject.toml version '1.2.4' does not match tag 'v1.2.3'"),
    ("1.2.3", {}, "is not vMAJOR.MINOR.PATCH"),
    ("v1.2", {}, "is not vMAJOR.MINOR.PATCH"),
    ("v1.2.3rc1", {}, "is not vMAJOR.MINOR.PATCH"),
    ("v1.2.3", {"name": "other"}, "pyproject.toml name 'other' is not 'xorchestrate'"),
])
def test_disagreeing_identities_are_refused(tmp_path, tag, versions, message):
    root = project(tmp_path / "candidate", **versions)
    result = gate(root, "--tag", tag, "--github-output", str(tmp_path / "outputs"))
    assert result.returncode == 1
    assert any(message in error for error in json.loads(result.stdout)["errors"]), result.stdout
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(("changelog", "message"), [
    ("# Changelog\n\n## Unreleased\n\n### Upgrade notes\n\n- None.\n", "has no '## 1.2.3' entry"),
    ("## 1.2.30\n\n### Upgrade notes\n\n- None.\n", "has no '## 1.2.3' entry"),
    ("## 1.2.3\n\n### Added\n\n- Something.\n", "no non-empty '### Upgrade notes'"),
    ("## 1.2.3\n\n### Upgrade notes\n\n### Added\n\n- x\n", "no non-empty '### Upgrade notes'"),
    ("## 1.2.3\n\n### Upgrade notes\n\n## 1.2.2\n\n### Upgrade notes\n\n- None.\n", "no non-empty '### Upgrade notes'"),
])
def test_a_missing_entry_or_upgrade_notes_is_refused(tmp_path, changelog, message):
    problems = errors(project(tmp_path, changelog=changelog), tag="v1.2.3")
    assert len(problems) == 1 and message in problems[0]


@pytest.mark.parametrize("heading", ["## 1.2.3", "## [1.2.3] - 2026-11-10", "## v1.2.3", "## 1.2.3 (2026-11-10)"])
def test_entry_headings_accept_common_forms(tmp_path, heading):
    changelog = f"{heading}\n\n### Upgrade notes\n\n- None.\n"
    assert errors(project(tmp_path, changelog=changelog), tag="v1.2.3") == []


def test_release_candidates_use_their_own_entry_and_pep440_filenames(tmp_path):
    changelog = ENTRY.format(version="1.3.0-rc.2")
    root = project(tmp_path / "candidate", init="1.3.0-rc.2", pyproject="1.3.0-rc.2", changelog=changelog)
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in ("xorchestrate", "xork"):
        (dist / f"{name}-1.3.0rc2-py3-none-any.whl").write_bytes(b"")
        (dist / f"{name}-1.3.0rc2.tar.gz").write_bytes(b"")
    outputs = tmp_path / "outputs"
    result = gate(root, "--tag", "v1.3.0-rc.2", "--dist", str(dist), "--github-output", str(outputs))
    assert result.returncode == 0, result.stdout
    assert outputs.read_text() == "tag=v1.3.0-rc.2\nversion=1.3.0-rc.2\ndistribution_version=1.3.0rc2\nprerelease=true\n"


XORK = ["xork-1.2.3-py3-none-any.whl", "xork-1.2.3.tar.gz"]
CANDIDATE = ["xorchestrate-1.2.3-py3-none-any.whl", "xorchestrate-1.2.3.tar.gz"]


@pytest.mark.parametrize(("files", "message"), [
    (["xorchestrate-1.2.4-py3-none-any.whl", "xorchestrate-1.2.4.tar.gz", *XORK], "do not carry xorchestrate-1.2.3"),
    ([*CANDIDATE, "xork-1.2.4-py3-none-any.whl", "xork-1.2.4.tar.gz"], "do not carry xork-1.2.3"),
    (CANDIDATE, "Expected exactly one wheel and one sdist of each of ['xorchestrate', 'xork']"),
    (["xorchestrate-1.2.3-py3-none-any.whl", *XORK], "Expected exactly one wheel and one sdist"),
    ([*CANDIDATE, *XORK, "extra.txt"], "Expected exactly one"),
    ([*CANDIDATE, "xorchestrate-1.2.3-py3-none-any.whl.1", *XORK], "Expected exactly"),
    ([*CANDIDATE, "other-1.2.3-py3-none-any.whl", "other-1.2.3.tar.gz"], "do not carry xork-1.2.3"),
])
def test_distributions_must_be_one_wheel_and_one_sdist_of_each_name_and_the_version(tmp_path, files, message):
    root = project(tmp_path / "candidate")
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in files:
        (dist / name).write_bytes(b"")
    problems = errors(root, tag="v1.2.3", distributions=dist)
    assert len(problems) == 1 and message in problems[0], problems


def test_both_distributions_of_the_version_pass(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in CANDIDATE + XORK:
        (dist / name).write_bytes(b"")
    report, problems = release_identity.check(project(tmp_path / "candidate"), tag="v1.2.3", distributions=dist)
    assert problems == [] and report["distributions"] == sorted(CANDIDATE + XORK)


@pytest.mark.parametrize(("arguments", "message"), [
    ({"version": "1.2.4", "dependencies": ("xorchestrate==1.2.3",)},
     "Alias xork version '1.2.4' does not match xorchestrate version '1.2.3'"),
    ({"dependencies": ("xorchestrate==1.2.4",)}, "dependencies ['xorchestrate==1.2.4'] are not exactly"),
    ({"dependencies": ("xorchestrate>=1.2.3",)}, "are not exactly ['xorchestrate==1.2.3']"),
    ({"dependencies": ("xorchestrate",)}, "are not exactly ['xorchestrate==1.2.3']"),
    ({"dependencies": ("xorchestrate-pro==1.2.3",)}, "are not exactly ['xorchestrate==1.2.3']"),
    ({"dependencies": ("xorchestrate==1.2.3", "requests")}, "are not exactly ['xorchestrate==1.2.3']"),
    ({"dependencies": ()}, "are not exactly ['xorchestrate==1.2.3']"),
    ({"name": "xorchestrate"}, "is not a distinct distribution name"),
    ({"extra": '[project.optional-dependencies]\nmore = ["requests"]\n'}, "must not declare optional or dynamic"),
])
def test_an_alias_with_another_version_or_pin_is_refused(tmp_path, arguments, message):
    root = project(tmp_path / "candidate")
    alias(root, **arguments)
    result = gate(root, "--tag", "v1.2.3", "--github-output", str(tmp_path / "outputs"))
    assert result.returncode == 1
    found = json.loads(result.stdout)["errors"]
    assert len(found) == 1 and message in found[0], found
    assert not (tmp_path / "outputs").exists()


def test_the_alias_pin_may_be_spaced_and_follows_a_release_candidate(tmp_path):
    changelog = ENTRY.format(version="1.3.0-rc.2")
    root = project(tmp_path, init="1.3.0-rc.2", pyproject="1.3.0-rc.2", changelog=changelog)
    alias(root, version="1.3.0-rc.2", dependencies=("xorchestrate == 1.3.0-rc.2",))
    assert errors(root, tag="v1.3.0-rc.2") == []
    alias(root, version="1.3.0-rc.2", dependencies=("xorchestrate==1.3.0-rc.1",))
    assert errors(root, tag="v1.3.0-rc.2") == [
        "Alias xork dependencies ['xorchestrate==1.3.0-rc.1'] are not exactly ['xorchestrate==1.3.0-rc.2']"]


def test_a_missing_alias_project_is_refused_unless_the_aliases_are_changed(tmp_path):
    root = project(tmp_path)
    (root / "packaging/xork/pyproject.toml").unlink()
    assert errors(root, tag="v1.2.3") == ["Alias project packaging/xork has no pyproject.toml"]
    assert errors(root, tag="v1.2.3", aliases=()) == []
    alias(root / "elsewhere", name="xo-alias")
    result = gate(root, "--tag", "v1.2.3", "--alias", "elsewhere/packaging/xork")
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["aliases"][0]["name"] == "xo-alias"


def git(root, *arguments):
    subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@invalid",
                    "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *arguments],
                   check=True, capture_output=True, timeout=60)


def test_the_tag_must_be_annotated_and_on_the_release_branch(tmp_path):
    root = project(tmp_path)
    git(root, "init", "-q", "-b", "master")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "release")
    git(root, "tag", "-a", "v1.2.3", "-m", "v1.2.3")
    report, problems = release_identity.check(root, tag="v1.2.3", annotated=True, ancestor_of="master")
    assert problems == [] and report["annotated"] and report["ancestor_of"]["contained"]

    git(root, "tag", "-d", "v1.2.3")
    git(root, "tag", "v1.2.3")
    git(root, "checkout", "-q", "-b", "side")
    git(root, "commit", "-q", "--allow-empty", "-m", "unreviewed")
    git(root, "tag", "-f", "v1.2.3")
    problems = errors(root, tag="v1.2.3", annotated=True, ancestor_of="master")
    assert problems == ["Tag v1.2.3 is not an annotated tag (found commit)", "Tag v1.2.3 is not contained in master"]


def test_the_repository_declares_one_version():
    """The checked-in package, project and gate agree on the version they read."""
    name, declared = release_identity.project_metadata(ROOT)
    assert name == "xorchestrate" and declared == release_identity.package_version(ROOT)
    assert xorchestrate.__version__ == declared
    problems = []
    aliases = [release_identity.check_alias(ROOT, directory, name, declared, problems)
               for directory in release_identity.ALIASES]
    assert problems == [] and [item["name"] for item in aliases] == ["xork"]


def test_xork_and_the_package_install_the_same_commands():
    """``xo`` and ``xorchestrate`` run the same entry point from either distribution."""
    package = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    alias_project = tomllib.loads((ROOT / "packaging/xork/pyproject.toml").read_text())
    assert package == {"xo": "xorchestrate.cli:main", "xorchestrate": "xorchestrate.cli:main"}
    assert alias_project["project"]["scripts"] == package
    assert alias_project["tool"]["setuptools"]["packages"] == [], "xork must ship no code"
