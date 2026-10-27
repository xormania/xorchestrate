"""Public local proof commands and views over the shared test-kit."""

from __future__ import annotations

import argparse
import json
import select
import subprocess
import sys
import time
from pathlib import Path

from . import operator_composition as managed
from .adapters import profiles, terminals
from .composition import MODES, ROOT, SUITES
from .operator import FINISHED

ACTIONS = ("setup", "doctor", "run", "start", "list", "status", "watch", "attach", "wait", "stop", "close", "collect")


def parser():
    value = argparse.ArgumentParser(description="Set up, run and inspect repeatable local proofs")
    value.add_argument("action", choices=ACTIONS)
    value.add_argument("directory", nargs="?", type=Path, help="Explicit managed run directory")
    value.add_argument("--profile", type=Path)
    value.add_argument("--from", dest="source", type=Path, help="Start fresh from saved plan/input snapshots")
    value.add_argument("--suite", choices=SUITES)
    value.add_argument("--mode", choices=MODES)
    value.add_argument("--config", type=Path)
    value.add_argument("--driver", action="append", help="Native acceptance driver; repeat to select several")
    value.add_argument("--family", action="append", help="Declared behavioral family; repeatable")
    value.add_argument("--brief", type=Path)
    value.add_argument("--timeout", type=float)
    value.add_argument("--check-timeout", type=float)
    value.add_argument("--run-timeout", type=float)
    value.add_argument("--shutdown-grace", type=float)
    value.add_argument("--output", type=Path)
    value.add_argument("--detach", action="store_true")
    value.add_argument("--json", action="store_true", help="Machine-readable status (also the default)")
    value.add_argument("--target", type=int, help="One-based owned terminal number")
    return value


def overrides(args):
    result = {}
    for field in ("suite", "mode", "config", "brief", "timeout", "check_timeout", "run_timeout", "shutdown_grace", "output"):
        item = getattr(args, field)
        if item is not None:
            result[field] = str(item.expanduser().resolve()) if isinstance(item, Path) else item
    if args.driver is not None:
        result["drivers"] = args.driver
    if args.family is not None:
        result["families"] = args.family
    return result


def settings(args):
    explicit = overrides(args)
    if args.source:
        if args.profile:
            raise ValueError("Choose --from or --profile, not both")
        return managed.repeat_settings(args.source, explicit)
    if args.profile:
        return profiles.load_profile(args.profile, root=ROOT, overrides=explicit)
    return {"suite": "all", "mode": "protocol-fixture", "drivers": [], "families": [],
            "config": None, "brief": str(ROOT / "docs/implementation/owner-brief.md"),
            "timeout": 180, "check_timeout": 600, "shutdown_grace": 10, "run_timeout": None,
            "output": str(ROOT.parent / "xorchestrate-runs"), "interpreter": sys.executable,
            "root": str(ROOT), **explicit}


def _line(state):
    progress = state.get("progress", {})
    event = progress.get("event", progress.get("phase", ""))
    check = progress.get("check_id", progress.get("name", ""))
    return f"{state['run_id']}  {state['phase']}  proof={state.get('proof', 'pending')}  {check} {event}".rstrip()


def watch(directory):
    previous = None
    try:
        while True:
            state = managed.status(directory)
            line = _line(state)
            if line != previous:
                print(line, flush=True)
                previous = line
            if state["phase"] in FINISHED:
                return state
            time.sleep(.25)
    except KeyboardInterrupt:
        return {"status": "viewer_closed", "directory": str(directory)}


def attach(directory, target=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Attachment requires an interactive terminal; use status/watch/wait otherwise")
    print("Waiting for owned interaction. q + Enter leaves the viewer; the run continues.", flush=True)
    previous, paused = None, False
    while True:
        state = managed.status(directory)
        if state["phase"] in FINISHED:
            print(_line(state), flush=True)
            return state
        found = terminals.discover(directory)
        # An acceptance progress pane is not proof of native prompt reachability.
        if managed.read_json(Path(directory) / "plan.json").get("mode") == "native":
            found = [item for item in found if item["kind"] == "pty"]
        line = _line(state)
        if line != previous:
            print(line, flush=True)
            previous = line
        if found and not paused:
            if len(found) == 1 or target is not None:
                index = 0 if target is None else target - 1
                if not 0 <= index < len(found):
                    raise ValueError("Selected owned terminal is unavailable")
                print("Attached. Ctrl-] (PTY) or Ctrl-b d (tmux) returns to this dashboard.", flush=True)
                terminals.attach(found[index])
                paused, target = True, None
                print("Dashboard: enter a terminal number to attach, w to follow, or q to leave.", flush=True)
            else:
                paused = True
                print("Owned terminals:\n" + "\n".join(f"{i + 1}: {item.get('purpose', item['kind'])} "
                                                        f"{item['descriptor_path']}" for i, item in enumerate(found)), flush=True)
        try:
            if select.select([sys.stdin], [], [], .25)[0]:
                command = sys.stdin.readline().strip()
                if command == "q" or not command:
                    return {"status": "viewer_closed", "directory": str(directory)}
                if command == "w":
                    paused = False
                elif command.isdigit():
                    target, paused = int(command), False
        except KeyboardInterrupt:
            return {"status": "viewer_closed", "directory": str(directory)}


def dashboard(directory):
    previous = None
    while True:
        state = managed.status(directory)
        line = _line(state)
        if line != previous:
            print(line, flush=True)
            previous = line
        time.sleep(.5)


def main(argv=None, *, direct_runner, preflight):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"_supervise", "_worker", "_dashboard"}:
        if len(argv) != 2:
            raise ValueError("Internal worker requires an explicit run directory")
        action, directory = argv
        if action == "_supervise":
            return managed.supervise(directory)
        if action == "_worker":
            return managed.worker(directory)
        dashboard(directory)
        return 0
    argument_parser = parser()
    args = argument_parser.parse_args(argv)
    try:
        if args.detach and args.action != "start":
            raise ValueError("--detach belongs to start")
        if args.target is not None and args.action != "attach":
            raise ValueError("--target belongs to attach")
        if args.source and args.action != "start":
            raise ValueError("--from creates a fresh managed start")
        if args.action in {"status", "watch", "attach", "wait", "stop", "close", "collect"}:
            if args.directory is None:
                raise ValueError("This command requires the explicit run directory printed by start")
            if args.profile or args.source or overrides(args):
                raise ValueError("Run controls use saved settings; overrides belong to a fresh start")
            if args.action == "status":
                result = managed.status(args.directory)
            elif args.action == "wait":
                result = managed.wait(args.directory)
            elif args.action == "watch":
                result = watch(args.directory)
            elif args.action == "attach":
                result = attach(args.directory, args.target)
            else:
                result = managed.control(args.directory, args.action)
        elif args.action == "setup":
            if not args.profile:
                raise ValueError("setup requires --profile PATH")
            if args.directory or overrides(args):
                raise ValueError("setup takes only the reusable profile path")
            result = profiles.setup(args.profile, root=ROOT)
        elif args.action == "list":
            if not args.profile:
                raise ValueError("list requires --profile PATH")
            if args.directory or overrides(args):
                raise ValueError("list reads the run root configured in its explicit profile")
            options = settings(args)
            root = Path(options["output"])
            results = []
            if root.exists():
                for child in sorted(root.iterdir()):
                    if child.is_dir() and not child.is_symlink() and (child / "state.json").is_file():
                        try:
                            results.append(managed.status(child))
                        except (OSError, ValueError) as error:
                            results.append({"directory": str(child), "status": "unavailable", "diagnostic": str(error)})
            result = {"status": "passed", "runs": results}
        else:
            if args.directory:
                raise ValueError("Run-directory arguments belong to control commands")
            if args.source and args.action != "start":
                raise ValueError("--from creates a fresh managed start")
            options = settings(args)
            if args.action == "doctor":
                result = preflight(root=ROOT, shutdown_grace=options["shutdown_grace"], **managed.execution_options(options))
                if args.profile:
                    environment = profiles.doctor(args.profile, root=ROOT, overrides=overrides(args))
                    result["profile_readiness"] = environment
                    if environment["status"] != "passed":
                        result["status"] = "setup_error"
            elif args.action == "run":
                result = managed.foreground(options, direct_runner)
            else:
                result = managed.start(options, parent=args.source)
                print(json.dumps(result), flush=True)
                if sys.stdin.isatty() and not args.detach:
                    attach(result["directory"])
        if args.action != "start":
            print(json.dumps(result), flush=True)
        if args.action == "wait":
            return 0 if result.get("outcome") == "passed" else 1
        if args.action in {"run", "doctor", "setup"}:
            return 0 if result.get("status") == "passed" else 1
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "setup_error", "type": type(error).__name__, "diagnostic": str(error)}), flush=True)
        return 1
