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
    report = commands.add_parser("report", help="judge every tree and scratch directory; changes nothing")
    form = report.add_mutually_exclusive_group()
    form.add_argument("--brief", action="store_true", help="one line when something needs attention, else nothing")
    form.add_argument("--json", action="store_true")
    report.add_argument("--offline", action="store_true", help="judge without asking GitHub")
    prune = commands.add_parser("prune", help="remove what the report measures safe (dry run unless --apply)")
    prune.add_argument("--apply", action="store_true")
    prune.add_argument("--offline", action="store_true", help="skip the fetch and GitHub")
    prune.add_argument("--include-extra", action="store_true", help="also clear old, unused extra-root entries")
    scratch = commands.add_parser("scratch", help="marked scratch directories").add_subparsers(dest="action",
                                                                                                required=True)
    scratch_new = scratch.add_parser("new", help="create a marked scratch directory and print its path")
    scratch_new.add_argument("label", nargs="?", default=None)
    scratch_new.add_argument("--owner", default=None)
    scratch.add_parser("rm", help="delete a marked, unused scratch directory").add_argument("path")
    scratch.add_parser("ls", help="list scratch directories")
    call = serena.add_parser("call", help="call one tool on a tree's server")
    call.add_argument("tree")
    call.add_argument("tool")
    call.add_argument("arguments", nargs="?", default="{}", help="JSON object of tool arguments")
    return parser


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None,
         ports: Ports | None = None) -> int:
    parser = _parser()
    arguments, extra = parser.parse_known_args(argv)
    if extra:
        # A scratch label that looks like an option is still a label, refused by its own rule.
        if arguments.command == "scratch" and arguments.action == "new" and arguments.label is None \
                and len(extra) == 1:
            arguments.label = extra[0]
        else:
            parser.error(f"unrecognized arguments: {' '.join(extra)}")
    env = dict(os.environ if env is None else env)
    flags = {ROOT_KEY: arguments.root, STATE_KEY: arguments.state_dir}
    if arguments.command == "report" and arguments.brief:
        return _brief(arguments, env, flags, ports)
    try:
        services = build(arguments.repo or Path.cwd(), env, flags, ports)
    except DevConfigError as error:
        print(f"{error.code}: {error.detail}", file=sys.stderr)
        return 2
    if arguments.command == "report" and arguments.json:
        print(json.dumps(services.maintenance.report_json(arguments.offline), indent=2, sort_keys=True))
        return 0
    outcome = _dispatch(arguments, services)
    for line in outcome.err:
        print(line, file=sys.stderr)
    for line in outcome.out:
        print(line)
    return outcome.code


def _brief(arguments: argparse.Namespace, env: Mapping[str, str], flags: Mapping[str, str | None],
           ports: Ports | None) -> int:
    """At most one line and always success, so a session-start hook can call it."""
    try:
        line = build(arguments.repo or Path.cwd(), env, flags, ports).maintenance.brief(arguments.offline)
    except Exception as error:  # noqa: BLE001 - the brief form must never fail its caller
        code = getattr(error, "code", type(error).__name__)
        line = f"worktrees: report unavailable ({code})"
    if line:
        print(line)
    return 0


def _dispatch(arguments: argparse.Namespace, services) -> Outcome:
    if arguments.command == "report":
        return services.maintenance.report(arguments.offline)
    if arguments.command == "prune":
        return services.maintenance.prune(arguments.apply, arguments.offline, arguments.include_extra)
    if arguments.command == "scratch":
        maintenance = services.maintenance
        if arguments.action == "new":
            return maintenance.scratch_new(arguments.label or "", arguments.owner or services.config.owner)
        if arguments.action == "rm":
            return maintenance.scratch_rm(str(Path(arguments.path).absolute()))
        return maintenance.scratch_ls()
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
