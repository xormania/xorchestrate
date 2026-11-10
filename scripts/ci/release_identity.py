"""Release identity gate: the tag, ``__version__``, ``pyproject.toml`` and changelog agree.

A release is an annotated tag ``vMAJOR.MINOR.PATCH`` (optionally ``-rc.N``) whose
commit sets ``__version__`` and the ``pyproject.toml`` version to the same string
without the ``v``. ``CHANGELOG.md`` has a ``## X.Y.Z`` entry with a non-empty
``### Upgrade notes`` section. Each alias project (by default ``packaging/xork``)
declares the same version and depends only on exactly that release of the
package. Built distributions, when given, are one wheel and one sdist of the
package and of each alias, all carrying the version. Without ``--tag`` (a manual
dry run) the expected tag is ``v`` + ``__version__``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path

TAG = re.compile(r"v(?P<version>(?P<release>\d+\.\d+\.\d+)(?:-rc\.(?P<rc>\d+))?)")
ENTRY = re.compile(r"##\s+\[?v?(?P<version>[^\]\s]+)\]?(?:\s.*)?")
ALIASES = ("packaging/xork",)


def normalized(version):
    """PEP 440 form used in distribution filenames: ``1.2.3-rc.4`` becomes ``1.2.3rc4``."""
    match = TAG.fullmatch("v" + version)
    return match["release"] + ("rc" + match["rc"] if match["rc"] is not None else "")


def package_version(root, package="xorchestrate"):
    """``__version__`` read from source without importing the package."""
    tree = ast.parse((Path(root) / package / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
                and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            return node.value.value
    return None


def _project(root):
    return tomllib.loads((Path(root) / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})


def project_metadata(root):
    project = _project(root)
    return project.get("name"), project.get("version")


def check_alias(root, directory, package, version, errors):
    """An alias project declares ``version`` and depends only on ``package==version``."""
    location = Path(root) / directory
    if not (location / "pyproject.toml").is_file():
        errors.append(f"Alias project {directory} has no pyproject.toml")
        return {"directory": str(directory), "name": None, "version": None, "dependencies": None}
    project = _project(location)
    name, declared, dependencies = project.get("name"), project.get("version"), project.get("dependencies")
    report = {"directory": str(directory), "name": name, "version": declared, "dependencies": dependencies}
    if not isinstance(name, str) or not name or name == package:
        errors.append(f"Alias project {directory} name {name!r} is not a distinct distribution name")
    if version is not None and declared != version:
        errors.append(f"Alias {name} version {declared!r} does not match {package} version {version!r}")
    pin = f"{package}=={version}"
    pins = [re.sub(r"\s+", "", item) for item in dependencies] if isinstance(dependencies, list) else None
    if version is not None and pins != [pin]:
        errors.append(f"Alias {name} dependencies {dependencies!r} are not exactly [{pin!r}]")
    if project.get("optional-dependencies") or "dependencies" in project.get("dynamic", []):
        errors.append(f"Alias {name} must not declare optional or dynamic dependencies")
    return report


def changelog_entry(text, version):
    """The body of the ``## version`` entry, or ``None`` when there is none."""
    lines, body = text.splitlines(), None
    for line in lines:
        if line.startswith("## ") or line == "##":
            if body is not None:
                break
            match = ENTRY.fullmatch(line.rstrip())
            if match and match["version"] == version:
                body = []
        elif body is not None:
            body.append(line)
    return None if body is None else "\n".join(body).strip("\n")


def upgrade_notes(entry):
    """The non-empty content of the entry's ``### Upgrade notes`` section, or ``None``."""
    notes = None
    for line in entry.splitlines():
        if line.startswith("### "):
            if notes is not None:
                break
            if line[4:].strip().lower() == "upgrade notes":
                notes = []
        elif notes is not None:
            notes.append(line)
    text = "\n".join(notes or []).strip()
    return text or None


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False, timeout=60)


def check(root, *, tag=None, package="xorchestrate", distributions=None, annotated=False, ancestor_of=None,
          aliases=ALIASES):
    """Return ``(report, errors)``; an empty error list means the identity agrees."""
    root = Path(root)
    errors = []
    version = package_version(root, package)
    name, declared = project_metadata(root)
    expected_tag = tag if tag is not None else (f"v{version}" if version else None)
    report = {"schema_version": 1, "tag": expected_tag, "tag_given": tag is not None, "package": package,
              "version": version, "pyproject": {"name": name, "version": declared}}
    match = TAG.fullmatch(expected_tag or "")
    if match is None:
        errors.append(f"Tag {expected_tag!r} is not vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-rc.N")
    if version is None:
        errors.append(f"{package}/__init__.py does not assign a string __version__")
    if match and version != match["version"]:
        errors.append(f"__version__ {version!r} does not match tag {expected_tag!r}")
    if match and declared != match["version"]:
        errors.append(f"pyproject.toml version {declared!r} does not match tag {expected_tag!r}")
    if name != package:
        errors.append(f"pyproject.toml name {name!r} is not {package!r}")
    report["aliases"] = [check_alias(root, directory, package, match["version"] if match else version, errors)
                         for directory in aliases]
    changelog = root / "CHANGELOG.md"
    entry = changelog_entry(changelog.read_text(encoding="utf-8"), match["version"]) if match and changelog.is_file() else None
    report["changelog"] = {"entry": entry is not None, "upgrade_notes": bool(entry and upgrade_notes(entry))}
    if match and entry is None:
        errors.append(f"CHANGELOG.md has no '## {match['version']}' entry")
    elif match and not upgrade_notes(entry):
        errors.append(f"CHANGELOG.md entry {match['version']} has no non-empty '### Upgrade notes' section")
    report["notes"] = entry
    if distributions is not None:
        names = [package, *(alias["name"] for alias in report["aliases"] if isinstance(alias["name"], str))]
        report["distributions"] = _check_distributions(Path(distributions), names, match, errors)
    if annotated and match:
        kind = _git(root, "cat-file", "-t", f"refs/tags/{expected_tag}").stdout.strip()
        report["annotated"] = kind == "tag"
        if kind != "tag":
            errors.append(f"Tag {expected_tag} is not an annotated tag (found {kind or 'nothing'})")
    if ancestor_of is not None and match:
        contained = _git(root, "merge-base", "--is-ancestor", f"refs/tags/{expected_tag}^{{commit}}", ancestor_of)
        report["ancestor_of"] = {"ref": ancestor_of, "contained": contained.returncode == 0}
        if contained.returncode != 0:
            errors.append(f"Tag {expected_tag} is not contained in {ancestor_of}")
    return report, errors


def _check_distributions(directory, names, match, errors):
    """Exactly one wheel and one sdist of each distribution in ``names``, carrying the tag's version."""
    files = sorted(path.name for path in directory.iterdir() if path.is_file()) if directory.is_dir() else []
    wheels = [name for name in files if name.endswith(".whl")]
    sdists = [name for name in files if name.endswith(".tar.gz")]
    if len(wheels) != len(names) or len(sdists) != len(names) or len(files) != 2 * len(names):
        errors.append(f"Expected exactly one wheel and one sdist of each of {names} in {directory}, found {files}")
    elif match:
        for name in names:
            stem = name.replace("-", "_") + "-" + normalized(match["version"])
            if (sum(wheel.startswith(stem + "-") for wheel in wheels) != 1
                    or sum(sdist == stem + ".tar.gz" for sdist in sdists) != 1):
                errors.append(f"Distributions {files} do not carry {stem}")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--tag", help="Release tag; omitted for a dry run, which expects v + __version__")
    parser.add_argument("--package", default="xorchestrate")
    parser.add_argument("--dist", type=Path, help="Directory holding the built wheels and sdists")
    parser.add_argument("--alias", action="append", metavar="DIR",
                        help=f"Alias project directory, relative to --root (repeatable; default: {', '.join(ALIASES)})")
    parser.add_argument("--annotated", action="store_true", help="Require the tag to be an annotated git tag")
    parser.add_argument("--ancestor-of", metavar="REF", help="Require the tagged commit to be contained in REF")
    parser.add_argument("--notes-output", type=Path, help="Write the changelog entry here")
    parser.add_argument("--github-output", type=Path, help="Append tag/version/prerelease outputs here")
    args = parser.parse_args(argv)
    report, errors = check(args.root, tag=args.tag, package=args.package, distributions=args.dist,
                           annotated=args.annotated, ancestor_of=args.ancestor_of,
                           aliases=ALIASES if args.alias is None else tuple(args.alias))
    report["status"], report["errors"] = ("failed" if errors else "passed"), errors
    print(json.dumps(report, indent=2))
    if errors:
        return 1
    if args.notes_output:
        args.notes_output.write_text(report["notes"] + "\n", encoding="utf-8")
    if args.github_output:
        prerelease = "-rc." in report["tag"]
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write(f"tag={report['tag']}\nversion={report['version']}\n"
                         f"distribution_version={normalized(report['version'])}\nprerelease={str(prerelease).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
