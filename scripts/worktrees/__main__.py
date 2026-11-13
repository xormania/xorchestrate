"""Command line: ``python3 -m scripts.worktrees [--repo P] [--root P] [--state-dir P] COMMAND``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.devconfig.model import DevConfigError
from scripts.worktrees.composition import ROOT_KEY, STATE_KEY, Ports, build
from scripts.worktrees.model import Outcome


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.worktrees",
                                     description="Optional work trees and per-tree Serena servers for contributors.")
    parser.add_argument("--repo", type=Path, default=None, help="any path in the repository (default: cwd)")
    parser.add_argument("--root", default=None, help=f"work-tree root (overrides {ROOT_KEY})")
    parser.add_argument("--state-dir", default=None, help=f"state directory (overrides {STATE_KEY})")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="create a work tree for a new branch")
    new.add_argument("branch")
    new.add_argument("--base", default=None, help="ref to start from (default: origin/HEAD)")
    remove = commands.add_parser("remove", help="remove a clean, pushed work tree")
    remove.add_argument("branch")
    remove.add_argument("--keep-branch", action="store_true")
    serena = commands.add_parser("serena", help="per-tree Serena servers").add_subparsers(dest="action",
                                                                                           required=True)
    for action, text in (("up", "start or reuse a tree's server"), ("down", "stop a tree's server"),
                         ("tools", "list the server's tools"), ("check", "check a server before trusting it")):
        serena.add_parser(action, help=text).add_argument("tree", help="branch under the root, or a path")
    serena.add_parser("ls", help="list recorded servers")
    serena.add_parser("reap", help="forget dead servers and stop servers whose tree is gone")
    call = serena.add_parser("call", help="call one tool on a tree's server")
    call.add_argument("tree")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON object of tool arguments")
    return parser


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None,
         ports: Ports | None = None) -> int:
    arguments = _parser().parse_args(argv)
    env = dict(os.environ if env is None else env)
    flags = {ROOT_KEY: arguments.root, STATE_KEY: arguments.state_dir}
    try:
        services = build(arguments.repo or Path.cwd(), env, flags, ports)
    except DevConfigError as error:
        print(f"{error.code}: {error.detail}", file=sys.stderr)
        return 2
    outcome = _dispatch(arguments, services)
    for line in outcome.err:
        print(line, file=sys.stderr)
    for line in outcome.out:
        print(line)
    return outcome.code


def _dispatch(arguments: argparse.Namespace, services) -> Outcome:
    if arguments.command == "new":
        return services.trees.new(arguments.branch, arguments.base)
    if arguments.command == "remove":
        return services.trees.remove(arguments.branch, arguments.keep_branch)
    serena = services.serena
    if arguments.action == "call":
        try:
            call_arguments = json.loads(arguments.arguments)
        except ValueError as error:
            return Outcome(code=2, err=[f"USAGE: tool arguments are not JSON: {error}"])
        if not isinstance(call_arguments, dict):
            return Outcome(code=2, err=["USAGE: tool arguments must be a JSON object"])
        return serena.call(arguments.tree, arguments.tool, call_arguments)
    if arguments.action in ("ls", "reap"):
        return getattr(serena, arguments.action)()
    return getattr(serena, arguments.action)(arguments.tree)


if __name__ == "__main__":
    sys.exit(main())
