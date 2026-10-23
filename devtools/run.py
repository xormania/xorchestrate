#!/usr/bin/env python3
"""Operator bootstrap and stdio entry point; agents use MCP, not this CLI."""

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devtools import adapters, launch
from devtools.api import API
from devtools.mcp import serve
from devtools.profiles import hook
from devtools.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "init",
        "mcp",
        "agent",
        "start",
        "stop",
        "status",
        "send",
        "hook",
        "operation",
        "serena",
        "doctor",
        "config",
        "connection",
        "evidence",
        "co",
        "watch",
    ):
        command = commands.add_parser(name)
        command.add_argument("--state", required=True, help="Private state directory outside source worktrees")
        if name not in {"init", "status", "doctor", "config", "connection", "evidence", "co", "watch"}:
            command.add_argument("--member", required=True)
        if name in {"mcp", "hook", "serena"}:
            command.add_argument("--generation", help=argparse.SUPPRESS)
        if name == "watch":
            command.add_argument("--action", choices=("start", "stop", "status", "run"), default="status")
            command.add_argument("--detach", action="store_true", help=argparse.SUPPRESS)
        elif name == "co":
            command.add_argument("--profile", help="Select one configured CO profile; default uses availability order")
            command.add_argument("--model", help="Override the model for this CO session")
            command.add_argument("--reasoning", help="Override native reasoning effort for this CO session")
            command.add_argument("--brief-file", type=Path, help="Submit this UTF-8 brief as the initial CO user prompt")
        elif name == "init":
            command.add_argument("--runtime", action="store_true", help="Create shared role/vendor runtime policy")
            command.add_argument("--co", help="Name of the external user-facing CO session")
            command.add_argument("--workspace", required=True)
            command.add_argument("--member", action="append", required=True, help="name:harness (installed adapter or external)")
            command.add_argument(
                "--repository", help="owner/repository; enables project workspaces and installed Symfony tools"
            )
            command.add_argument("--target", default="dev")
            command.add_argument("--remote", default="origin")
            command.add_argument("--serena-image", help="Locally built devtools/serena image; requires --repository")
        elif name == "config":
            command.add_argument("--role", help="Inspect one configured role's effective settings")
            command.add_argument("--job", type=int, help="Inspect one existing job's effective settings")
        elif name == "doctor":
            command.add_argument("--startup", action="store_true", help="Check only capabilities configured for this team")
            command.add_argument(
                "--online", action="store_true", help="Also read the Git remote and GitHub repository access"
            )
        elif name == "evidence":
            command.add_argument("--work-order", type=int, required=True)
        elif name == "mcp":
            command.add_argument("--channel", action="store_true")
            command.add_argument(
                "--operator", action="store_true", help="Trusted setup/diagnostics; never pass to managed agents"
            )
        elif name == "operation":
            command.add_argument("--operation", required=True, type=int)
        elif name == "agent":
            command.add_argument("--binding-revision", type=int, help=argparse.SUPPRESS)
            command.add_argument("--standby-revision", type=int, help=argparse.SUPPRESS)
            command.add_argument("--launch-fd", type=int, help=argparse.SUPPRESS)
        elif name == "send":
            command.add_argument("--to", required=True)
            command.add_argument("--body", required=True)
            command.add_argument("--key", required=True)
        elif name == "hook":
            command.add_argument("--harness", required=True, choices=adapters.names("harness"))
    args = parser.parse_args()
    try:
        if args.command == "watch":
            from devtools import watcher

            if args.action == "run":
                watcher.run(args.state, args.detach)
                return 0
            result = getattr(watcher, args.action)(Store(args.state))
        elif args.command == "co":
            from devtools.console import read_brief, run

            initial_prompt = read_brief(args.brief_file) if args.brief_file is not None else None
            return run(args.state, args.profile, args.model, args.reasoning, initial_prompt=initial_prompt)
        elif args.command == "init":
            from devtools.readiness import project

            if args.serena_image and not args.repository:
                raise ValueError("--serena-image requires --repository")
            configured = (
                project(args.workspace, args.repository, args.target, args.remote, args.serena_image)
                if args.repository
                else None
            )
            result = launch.initialize(args.state, args.workspace, args.member, args.co, configured, args.runtime)
        elif args.command in {"doctor", "config", "connection", "evidence"}:
            from devtools import readiness

            options = (
                {"online": args.online, "startup": args.startup}
                if args.command == "doctor"
                else {"work_order": args.work_order}
                if args.command == "evidence"
                else {"role": args.role, "job": args.job}
                if args.command == "config"
                else {}
            )
            result = getattr(readiness, args.command)(args.state, **options)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if result.get("status") == "blocked" else 0
        elif args.command == "mcp":
            serve(args.state, args.member, args.channel, args.operator, args.generation)
            return 0
        elif args.command == "operation":
            from devtools.operations import execute

            execute(args.state, args.member, args.operation)
            return 0
        elif args.command == "serena":
            from devtools.serena_provider import serve as serena_serve

            serena_serve(args.state, args.member, args.generation)
            return 0
        elif args.command == "agent":
            launch.agent(args.state, args.member, args.binding_revision, args.launch_fd,
                         standby_revision=args.standby_revision)
            return 0
        elif args.command == "start":
            result = launch.start(args.state, args.member)
        elif args.command == "stop":
            result = launch.stop(args.state, args.member)
        elif args.command == "status":
            result = {"members": Store(args.state).members()}
        elif args.command == "send":
            result = API(args.state, args.member, operator=True).call(
                "capability_invoke",
                {"capability": "message_send", "arguments": {"to": args.to, "body": args.body, "key": args.key}},
            )
        else:
            payload = sys.stdin.buffer.read(131073)
            if len(payload) > 131072:
                raise ValueError("Hook payload exceeds 128 KiB")
            result = hook(args.state, args.member, args.harness, json.loads(payload), args.generation)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        if args.command == "hook":
            # Hook errors must not accidentally enable a native tool.
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": "MCP-only hook failed; native tool denied.",
                        }
                    }
                )
            )
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
