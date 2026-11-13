"""Public local proof commands and views over the shared test-kit."""

from __future__ import annotations

import argparse
import functools
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

from . import operator_composition as managed
from .adapters import profiles, terminals
from .composition import MODES, ROOT, SUITES
from .operator import FINISHED
from .stories import STORIES, select_stories, validate_limits

CONTROLS = frozenset({"status", "watch", "attach", "wait", "stop", "close", "collect", "verify", "stories"})
"""Commands that use only saved runs or the catalog: they never read developer settings."""
ACTIONS = ("setup", "doctor", "run", "start", "list", "status", "watch", "attach", "wait", "stop", "close", "collect", "verify", "stories", "plan")


def parser():
    value = argparse.ArgumentParser(description="Set up, run and inspect repeatable local proofs")
    value.add_argument("action", choices=ACTIONS)
    value.add_argument("directory", nargs="?", type=Path, help="Explicit managed run directory")
    value.add_argument("--profile", type=Path)
    value.add_argument("--project", type=Path, help="Project workspace used by the selected native drivers")
    value.add_argument("--language", choices=tuple(profiles.PROJECT_TOOLS), help="Selected project prerequisite defaults")
    value.add_argument("--from", dest="source", type=Path, help="Start fresh from saved plan/input snapshots")
    value.add_argument("--suite", choices=SUITES)
    value.add_argument("--mode", choices=MODES)
    value.add_argument("--config", type=Path)
    value.add_argument("--driver", action="append", help="Native acceptance driver; repeat to select several")
    value.add_argument("--family", action="append", help="Declared behavioral family; repeatable")
    value.add_argument("--shard", metavar="K/N", help="Run one-based balanced shard K of N of the tests suite")
    value.add_argument("--timings", type=Path, help="External timing model for a balanced tests shard")
    value.add_argument("--story", action="append", help="Story ID; repeat to select an ordered battery")
    value.add_argument("--max-sessions", type=int, help="Session-start requests admitted per story")
    value.add_argument("--max-turns", type=int, help="Harness turn submissions admitted per story")
    value.add_argument("--max-tool-calls", type=int, help="Public controller tool requests admitted per story")
    value.add_argument("--brief", type=Path)
    value.add_argument("--timeout", type=float)
    value.add_argument("--check-timeout", type=float)
    value.add_argument("--run-timeout", type=float)
    value.add_argument("--shutdown-grace", type=float)
    value.add_argument("--terminal-host", help="Terminal host selection as JSON (default: the product's default)")
    value.add_argument("--keep-after-run", action="store_true", default=None,
                       help="start: keep the run's terminals after it finishes, until close")
    value.add_argument("--output", type=Path)
    value.add_argument("--detach", action="store_true")
    value.add_argument("--target", type=int, help="One-based owned terminal number")
    value.add_argument("--probe", action="store_true",
                       help="doctor: also launch each selected native harness through session start and stop; no turn is submitted")
    return value


def _attach_host(selection, placement):
    """The run's host at its recorded placement, for an attach lookup only.

    The descriptor is writable by the run's harness: only a built-in host name is taken from its selection, never
    options or a ``module:factory``, and that is checked before anything is imported.
    """
    from .adapters import terminal_selection

    return terminal_selection.build(terminal_selection.recorded(selection), owned=True, name=placement["name"],
                                    location=placement["location"], socket=placement["socket"]).host


def overrides(args):
    result = {}
    for field in ("suite", "mode", "config", "brief", "shard", "timings", "timeout", "check_timeout", "run_timeout", "shutdown_grace", "output", "max_sessions", "max_turns", "max_tool_calls", "terminal_host",
                  "keep_after_run"):
        item = getattr(args, field)
        if item is not None:
            result[field] = str(item.expanduser().resolve()) if isinstance(item, Path) else item
    if args.driver is not None:
        result["drivers"] = args.driver
    if args.family is not None:
        result["families"] = args.family
    if args.story is not None:
        result["stories"] = args.story
    if args.project is not None or args.language is not None:
        if args.project is None or args.language is None:
            raise ValueError("Select --project and --language together")
        result["project"] = {"path": str(args.project.expanduser().resolve()), "language": args.language}
    return result


def _profile(args, dev):
    """The explicit --profile, else the developer settings' profile (never for a saved run)."""
    if args.profile or args.source:
        return args.profile
    return profiles.dev_value(dev, profiles.DEV_PROFILE)


def _default_output(dev):
    if dev is not None:
        return str(dev.get(profiles.DEV_OUTPUT))
    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state) if state and os.path.isabs(state) else Path.home() / ".local/state"
    return str(base / "xorchestrate" / "runs" / ROOT.name)


def _interpreter(dev):
    configured = profiles.dev_value(dev, profiles.DEV_INTERPRETER)
    if configured is None:
        return sys.executable
    profiles._external(configured, ROOT, "interpreter")
    return str(configured)


def settings(args, *, dev=None):
    """Settings by precedence: flags, then the profile, then developer settings, then built-in defaults."""
    dev = profiles.load_dev(ROOT) if dev is None else dev
    explicit = overrides(args)
    if args.source:
        if args.profile:
            raise ValueError("Choose --from or --profile, not both")
        values = managed.repeat_settings(args.source, explicit)
        # A repeat reruns the saved inputs, so it carries the record of the settings that shaped them.
        saved = managed.read_json(Path(args.source).expanduser() / "plan.json")
        if isinstance(saved, dict) and "dev_settings" in saved:
            values["dev_settings"] = saved["dev_settings"]
        return values
    profile = _profile(args, dev)
    if profile:
        values = profiles.load_profile(profile, root=ROOT, overrides=explicit, dev=dev)
        values["dev_settings"] = profiles.dev_record(dev)
        return values
    values = {"suite": "all", "mode": "protocol-fixture", "drivers": [], "families": [], "shard": None,
              "timings": None, "config": None, "brief": str(ROOT / "docs/implementation/owner-brief.md"),
              "stories": [], "max_sessions": 2, "max_turns": 4, "max_tool_calls": 5000,
              "timeout": 180, "check_timeout": 600, "shutdown_grace": 10, "run_timeout": None,
              "terminal_host": None, "keep_after_run": False,
              "output": _default_output(dev),
              "interpreter": _interpreter(dev),
              "root": str(ROOT), **explicit}
    if "config" not in explicit and values["mode"] == "native":
        configured = profiles.dev_value(dev, profiles.DEV_NATIVE_CONFIG)
        values["config"] = None if configured is None else str(configured)
    if "terminal_host" not in explicit and profiles.dev_value(dev, profiles.DEV_TERMINAL_BINARY) is not None:
        values["terminal_host"] = profiles.default_terminal_host(profiles.dev_value(dev, profiles.DEV_TERMINAL_BINARY))
    values["dev_settings"] = profiles.dev_record(dev)
    return values


def _line(state):
    envelope = state.get("progress", {})
    progress = envelope.get("last_event", envelope)
    event = progress.get("event", progress.get("phase", ""))
    check = progress.get("check", progress.get("check_id", progress.get("name", "")))
    status = progress.get("status", progress.get("result", {}).get("status", ""))
    return f"{state['run_id']}  {state['phase']}  proof={state.get('proof', 'pending')}  {check} {event} {status}".rstrip()


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
                print("Attached. Ctrl-] (PTY) or the terminal host's detach key returns to this dashboard.", flush=True)
                terminals.attach(found[index], host_for=_attach_host)
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


def main(argv=None, *, direct_runner, preflight, dev=None):
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
        if args.action in CONTROLS:
            dev = None
        else:
            dev = profiles.load_dev(ROOT) if dev is None else dev
            for line in profiles.dev_warnings(dev):
                print(line, file=sys.stderr)
        profile = _profile(args, dev)
        if args.detach and args.action != "start":
            raise ValueError("--detach belongs to start")
        if args.target is not None and args.action != "attach":
            raise ValueError("--target belongs to attach")
        if args.probe and args.action != "doctor":
            raise ValueError("--probe belongs to doctor")
        if args.source and args.action != "start":
            raise ValueError("--from creates a fresh managed start")
        if args.action == "stories":
            if args.directory or args.profile or args.source or overrides(args):
                raise ValueError("stories lists the catalog without execution options")
            result = {"status": "passed", "stories": [
                {"id": s.id, "version": s.version, "modes": list(s.modes), "description": s.description}
                for s in STORIES]}
        elif args.action in {"status", "watch", "attach", "wait", "stop", "close", "collect", "verify"}:
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
            elif args.action == "verify":
                from .adapters.native_evidence import verify_run

                result = verify_run(args.directory)
            else:
                result = managed.control(args.directory, args.action)
        elif args.action == "setup":
            if not profile:
                raise ValueError("setup requires --profile PATH (or XORCHESTRATE_DEV_TESTKIT_PROFILE)")
            if args.directory:
                raise ValueError("setup takes a reusable profile path and explicit selections")
            result = profiles.setup(profile, root=ROOT, overrides=overrides(args), dev=dev)
        elif args.action == "list":
            if not profile:
                raise ValueError("list requires --profile PATH (or XORCHESTRATE_DEV_TESTKIT_PROFILE)")
            if args.directory or overrides(args):
                raise ValueError("list reads the run root configured in its explicit profile")
            options = settings(args, dev=dev)
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
            options = settings(args, dev=dev)
            if args.action == "plan":
                managed.validate_limits(options)
                checks = managed.check_plan(directory=Path(options["output"]) / "planned", root=ROOT,
                                            shutdown_grace=options["shutdown_grace"],
                                            **managed.execution_options(options))
                selected = select_stories(options.get("stories", ()), options["mode"]) if options["suite"] in {"stories", "all"} else ()
                limits = validate_limits(**{key: options[key] for key in ("max_sessions", "max_turns", "max_tool_calls")})
                count = sum(check.name.startswith("story:") for check in checks)
                result = {"status": "passed", "mode": options["mode"],
                          "stories": [{"id": s.id, "version": s.version} for s in selected],
                          "checks": [{"name": c.name, "timeout": c.timeout, "argv": list(c.argv)} for c in checks],
                          "story_limits": limits, "story_attempts": count,
                          "battery_story_limits": {key: value * count for key, value in limits.items()},
                          "maximum_check_seconds": sum(c.timeout for c in checks),
                          "run_timeout": options.get("run_timeout"), "tokens": "unknown",
                          "limit_scope": "per-story controller requests; native internal calls and tokens are not counted"}
            elif args.action == "doctor":
                if args.probe:
                    # Refused before any readiness work, so the answer never depends on it.
                    managed.probe_selection(options)
                result = preflight(root=ROOT, shutdown_grace=options["shutdown_grace"], output=options["output"],
                                   **managed.execution_options(options))
                if profile:
                    environment = profiles.doctor(profile, root=ROOT, overrides=overrides(args), dev=dev)
                    result["profile_readiness"] = environment
                    if environment["status"] != "passed":
                        result["status"] = "setup_error"
                if args.probe:
                    if result["status"] == "passed":
                        result["probe"] = managed.probe(options, direct_runner)
                    else:
                        # A harness is not launched over prerequisites already known to be missing.
                        result["probe"] = {"status": "not_run", "reason": "readiness is not passed"}
                    if result["probe"]["status"] != "passed":
                        result["status"] = "setup_error"
            elif args.action == "run":
                runner = direct_runner
                if options.get("dev_settings") is not None:
                    runner = functools.partial(direct_runner, dev_settings=options["dev_settings"])
                result = managed.foreground(options, runner)
                if isinstance(result, dict) and options.get("dev_settings") is not None:
                    environment = result.setdefault("environment", {})
                    if isinstance(environment, dict):
                        environment["dev_settings"] = options["dev_settings"]
            else:
                result = managed.start(options, parent=args.source)
                print(json.dumps(result), flush=True)
                if sys.stdin.isatty() and not args.detach:
                    attach(result["directory"])
        if args.action != "start":
            print(json.dumps(result), flush=True)
        if args.action == "wait":
            return 0 if result.get("outcome") == "passed" else 1
        if args.action == "verify":
            return 0 if result.get("status") in {"verified", "disabled", "not_applicable"} else 1
        if args.action in {"run", "doctor", "setup"}:
            return 0 if result.get("status") == "passed" else 1
        return 0
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "setup_error", "type": type(error).__name__, "diagnostic": str(error)}), flush=True)
        return 1

