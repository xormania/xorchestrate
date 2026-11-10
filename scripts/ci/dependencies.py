"""Maintain and verify native uv validation locks without narrowing package ranges."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATION_EXPORT = Path(".github/requirements-ci.txt")
PROFILES = {"runtime": (), "validation": ("--only-group", "validation"),
            "tests": ("--extra", "test")}


def _run(root, arguments, *, uv="uv", offline=False):
    command = [uv, *(('--offline',) if offline else ()), *arguments]
    return subprocess.run(command, cwd=root, text=True, capture_output=True, check=True, timeout=180).stdout


def export(root, profile, *, uv="uv", offline=False):
    """Export a locked dependency profile without the editable source project."""
    return _run(root, ["export", "--locked", "--no-default-groups", "--no-emit-project", "--no-hashes",
                       "--no-header", "--no-annotate", *PROFILES[profile]], uv=uv, offline=offline)


def _build_requirements(root):
    metadata = tomllib.loads((Path(root) / "pyproject.toml").read_text())
    required = metadata.get("build-system", {}).get("requires", [])
    validation = metadata.get("dependency-groups", {}).get("validation", [])
    missing = [requirement for requirement in required if requirement not in validation]
    if missing:
        raise ValueError("Build requirements must also be declared in the validation group: " + ", ".join(missing))


def check(root, *, uv="uv", offline=False):
    """Refuse stale manifests/locks and a hand-edited generated validation export."""
    _build_requirements(root)
    _run(root, ["lock", "--check"], uv=uv, offline=offline)
    expected = export(root, "validation", uv=uv, offline=offline)
    path = Path(root) / VALIDATION_EXPORT
    if not path.is_file() or path.is_symlink() or path.read_text() != expected:
        raise ValueError("Generated validation requirements differ; run python -m scripts.ci.dependencies update")


def update(root, *, upgrade=False, uv="uv", offline=False):
    """Resolve intentionally, then derive the pip-compatible validation export."""
    _build_requirements(root)
    _run(root, ["lock", *(('--upgrade',) if upgrade else ())], uv=uv, offline=offline)
    text = export(root, "validation", uv=uv, offline=offline)
    target = Path(root) / VALIDATION_EXPORT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "update", "export"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--uv", default="uv", help="uv executable (CI pins its version)")
    parser.add_argument("--offline", action="store_true", help="Use only cached metadata; useful for local controls")
    parser.add_argument("--upgrade", action="store_true", help="Intentionally refresh versions during update")
    parser.add_argument("--profile", choices=PROFILES, default="runtime")
    parser.add_argument("--output", type=Path, help="Required destination for an exported profile")
    args = parser.parse_args(argv)
    if args.upgrade and args.action != "update":
        parser.error("--upgrade applies only to update")
    if args.action == "export" and args.output is None:
        parser.error("export requires --output")
    try:
        options = {"uv": args.uv, "offline": args.offline}
        if args.action == "check":
            check(args.root, **options)
        elif args.action == "update":
            update(args.root, upgrade=args.upgrade, **options)
        else:
            text = export(args.root, args.profile, **options)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
