"""Launch the tmux acceptance fixture and retain its evidence."""

import argparse
import json
import math
import os
import sys
from pathlib import Path

from scripts.testkit.adapters.evidence import allocate_run

from .lifecycle import supervise


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run Xorchestrate acceptance in an owned tmux terminal")
    parser.add_argument("--output", type=Path, default=Path(".xorchestrate/acceptance"))
    parser.add_argument("--profile", choices=("fixture", "native"), default="fixture")
    parser.add_argument("--config", type=Path, help="Trusted application configuration for native acceptance")
    parser.add_argument("--driver", help="Configured session driver for native acceptance")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    if args.profile == "native" and (args.config is None or not args.driver):
        parser.error("native profile requires --config and --driver")
    if args.profile == "fixture" and (args.config is not None or args.driver is not None):
        parser.error("--config and --driver select native acceptance; use --profile native")
    os.umask(0o077)
    run_dir = allocate_run(args.output)
    command = [sys.executable, "-m", "scripts.acceptance.scenario", "--run-dir", str(run_dir),
               "--profile", args.profile, "--timeout", str(args.timeout)]
    if args.config is not None:
        command.extend(("--config", str(args.config.expanduser().resolve()), "--driver", args.driver))
    root = Path(__file__).resolve().parents[2]
    report = supervise(command, run_dir, profile=args.profile, timeout=args.timeout, cwd=root)
    print(json.dumps({"status": report["status"], "report": str(run_dir / "report.json")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
