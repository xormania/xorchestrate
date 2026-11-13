"""Command line: ``python3 -m scripts.devconfig [--repo PATH] {show,example}``."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.devconfig.example import render_example
from scripts.devconfig.keys import SETTINGS
from scripts.devconfig.loader import find_checkout, load
from scripts.devconfig.model import DevConfigError

EXAMPLE_FILE = ".devtools.env.example"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.devconfig",
                                     description="Show or document the optional developer settings.")
    parser.add_argument("--repo", type=Path, default=None,
                        help="any path inside the repository (default: the current directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("show", help="print every setting's effective value and where it came from")
    get = commands.add_parser("get", help="print one setting's effective value (empty when unset)")
    get.add_argument("key")
    example = commands.add_parser("example", help="write or check this tree's committed example file")
    mode = example.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    return parser


def _write_example(target: Path, text: str) -> None:
    """Replace the example atomically, never writing through a symbolic link at its path."""
    if target.is_symlink():
        raise DevConfigError("EXAMPLE_UNSAFE", f"{target} is a symbolic link; refusing to write through it")
    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".devtools.env.example.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            output.write(text)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)  # replaces the path itself; a link swapped in meanwhile is not followed
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    env = os.environ if env is None else env
    repo = arguments.repo or Path.cwd()
    try:
        if arguments.repo is not None and not arguments.repo.is_dir():
            raise DevConfigError("NOT_A_REPOSITORY", f"{arguments.repo} is not a directory")
        if arguments.command == "get":
            print(load(repo, env=env, discovery="files").display(arguments.key))
            return 0
        if arguments.command == "show":
            config = load(repo, env=env, discovery="files")
            for warning in config.warnings:
                code, path, line, key = warning
                if code == "REPO_FILE_SKIPPED":
                    print(f"{code}: {key} ({path or 'no repository'}); the repository settings file was not read",
                          file=sys.stderr)
                    continue
                where = f"{path}:{line}" if path else "environment"
                print(f"{code}: {key} ({where}) is not a declared setting; ignored", file=sys.stderr)
            print("\n".join(config.explain()))
            return 0
        target = find_checkout(repo) / EXAMPLE_FILE
        expected = render_example(SETTINGS)
        if arguments.write:
            _write_example(target, expected)
            print(target)
            return 0
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current != expected:
            print(f"EXAMPLE_DRIFT: {target} differs from the declared settings; "
                  "run python3 -m scripts.devconfig example --write", file=sys.stderr)
            return 1
        return 0
    except DevConfigError as error:
        print(f"{error.code}: {error.detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
