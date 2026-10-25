"""Console projection of the fixture service."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from service.api import handle
from service.export import export_archive

IDENTITY = re.compile(r"^.+ <[^<>\s]+>$")


def refuse(kind: str, expected: str, found: str, needed: str) -> int:
    print(f"{kind}: expected {expected}; found {found}; needed {needed}", file=sys.stderr)
    return 2


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    verbs = command.add_subparsers(dest="verb")
    verbs.add_parser("serve", add_help=False)
    export = verbs.add_parser("export", add_help=False)
    export.add_argument("name")
    return command


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return refuse("arguments", "serve or export NAME", "none", "choose one verb")
    try:
        parsed = parser().parse_args(args)
    except (argparse.ArgumentError, SystemExit):
        return refuse("arguments", "serve or export NAME", repr(args), "correct the arguments")
    if parsed.verb == "serve":
        status = 0
        root = Path.cwd() / "records"
        for line in sys.stdin:
            result = handle(line, root)
            print(json.dumps(result, sort_keys=True))
            if result.get("ok") is not True:
                status = 1
        return status
    if parsed.verb != "export":
        return refuse("arguments", "serve or export NAME", repr(args), "choose one verb")
    agent = os.environ.get("WF_AGENT", "")
    if IDENTITY.fullmatch(agent) is None:
        return refuse("identity", "Name <address>", agent or "unset", "export WF_AGENT")
    try:
        result = export_archive(Path.cwd(), parsed.name)
    except OSError as error:
        return refuse("export", "a writable archive", str(error), "repair the destination")
    return 0 if result.returncode == 0 else 1
