"""Launch the acceptance fixture in an owned terminal host and retain its evidence."""

import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

from scripts.testkit.adapters import terminal_selection, terminals
from scripts.testkit.adapters.diagnostics import NAME as DIAGNOSTICS
from scripts.testkit.adapters.evidence import allocate_run
from scripts.testkit.adapters.supervised_run import supervise
from scripts.testkit.stories import select_stories, validate_limits


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Xorchestrate acceptance in an owned terminal host")
    parser.add_argument("--output", type=Path, default=Path(".xorchestrate/acceptance"))
    parser.add_argument("--profile", choices=("fixture", "native"), default="fixture")
    parser.add_argument("--config", type=Path, help="Trusted application configuration for native acceptance")
    parser.add_argument("--driver", help="Configured session driver for native acceptance")
    parser.add_argument("--story", help="Select one versioned story")
    parser.add_argument("--terminal-host", help="Terminal host selection as JSON (default: the product's default)")
    parser.add_argument("--max-sessions", type=int, default=2, help="Maximum harness start requests")
    parser.add_argument("--max-turns", type=int, default=4, help="Maximum harness message submissions")
    parser.add_argument("--max-tool-calls", type=int, default=5000, help="Maximum controller MCP tool requests")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--shutdown-grace", type=float, default=10,
                        help="Bounded scenario finalization before owned escalation")
    parser.add_argument("--cleanup-budget", type=float,
                        help="Internal total cleanup bound when nested beneath a command owner")
    args = parser.parse_args(argv)
    try:
        validate_limits(max_sessions=args.max_sessions, max_turns=args.max_turns, max_tool_calls=args.max_tool_calls)
        if args.story:
            select_stories((args.story,), mode=args.profile)
    except ValueError as error:
        parser.error(str(error))
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    for name, value in (("--shutdown-grace", args.shutdown_grace), ("--cleanup-budget", args.cleanup_budget)):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error(name + " must be a positive finite number")
    if args.cleanup_budget is not None and args.shutdown_grace >= args.cleanup_budget:
        parser.error("--shutdown-grace must leave room within --cleanup-budget for owned teardown")
    if args.profile == "native" and (args.config is None or not args.driver):
        parser.error("native profile requires --config and --driver")
    if args.profile == "fixture" and (args.config is not None or args.driver is not None):
        parser.error("--config and --driver select native acceptance; use --profile native")
    try:
        selection = terminal_selection.parse(args.terminal_host)
    except ValueError as error:
        parser.error(str(error))
    os.umask(0o077)
    run_dir = allocate_run(args.output)
    command = [sys.executable, "-m", "scripts.acceptance.scenario", "--run-dir", str(run_dir),
               "--profile", args.profile, "--timeout", str(args.timeout),
               "--max-sessions", str(args.max_sessions), "--max-turns", str(args.max_turns),
               "--max-tool-calls", str(args.max_tool_calls)]
    if args.story:
        command.extend(("--story", args.story))
    if args.config is not None:
        command.extend(("--config", str(args.config.expanduser().resolve()), "--driver", args.driver))
    root = Path(__file__).resolve().parents[2]
    # A short private directory for the host's endpoint, derived from the run (attach re-derives it).
    endpoint = terminals.create_endpoint(run_dir)
    try:
        placement = {"name": terminals.PLACEMENT_NAME, "location": run_dir / "terminal-host", "socket": Path(endpoint) / "host.sock"}
        selected = terminal_selection.build(selection, owned=True, **placement)
        report = supervise(command, run_dir, host=selected.host, host_report=selected.report(), profile=args.profile,
                           timeout=args.timeout, cwd=root, grace=args.shutdown_grace,
                           cleanup_budget=args.cleanup_budget, selection=selection, placement=placement)
    finally:
        shutil.rmtree(endpoint, ignore_errors=True)
    summary = {"status": report["status"], "report": str(run_dir / "report.json")}
    if (run_dir / DIAGNOSTICS).is_file():
        # Only the location: the reasons themselves stay in the private file.
        summary["diagnostics"] = str(run_dir / DIAGNOSTICS)
    print(json.dumps(summary))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

