#!/usr/bin/env bash
# Inspect or start existing local organization runner services. Never source this file.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    printf '%s\n' 'Run this script with bash; sourcing performs no actions.' >&2
    return 0
fi
set +x
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Python 3 is required. Run dependencies.sh check first.' >&2
    exit 1
fi
exec python3 -I -B - "$@" <<'PY'
"""Read only registration metadata and control existing user services."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def invoke(argv, timeout=15):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def registration(directory, organization):
    try:
        source = directory / ".runner"
        if source.stat().st_size > 65536:
            raise ValueError("oversized metadata")
        metadata = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(metadata, dict):
            raise ValueError("invalid metadata")
        url = metadata.get("gitHubUrl")
        if not isinstance(url, str) or url.rstrip("/").lower() != f"https://github.com/{organization.lower()}":
            return "registration is not for the selected GitHub organization"
        if not isinstance(metadata.get("agentName"), str) or not metadata["agentName"].strip():
            return "registration has no runner name"
        return None
    except (OSError, UnicodeError, ValueError):
        return "registration metadata is missing or invalid"


def unit_state(unit, directory):
    result = invoke(["systemctl", "--user", "show", unit, "--no-pager",
                     "--property=LoadState,ActiveState,SubState,WorkingDirectory,MainPID"])
    if result is None or result.returncode:
        return None, "cannot inspect the user service; check systemctl --user status"
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if values.get("LoadState") != "loaded":
        return None, "user service is not loaded"
    working = values.get("WorkingDirectory", "")
    if not working or Path(working).resolve() != directory.resolve():
        return None, "user service WorkingDirectory does not match the runner directory"
    return values, None


def running(values):
    return (values.get("ActiveState") == "active" and values.get("SubState") == "running"
            and values.get("MainPID", "0").isdigit() and int(values["MainPID"]) > 0)


def main():
    parser = argparse.ArgumentParser(description="Inspect or start existing Agentscient user runner services.")
    parser.add_argument("command", nargs="?", choices=("status", "start"), default="status")
    parser.add_argument("--runner-root", type=Path, default=Path.home() / "actions-runner")
    parser.add_argument("--count", type=int, default=12, help="number of ag1..agN registrations (default: 12)")
    parser.add_argument("--organization", default="uscient", help="expected GitHub organization (default: uscient)")
    args = parser.parse_args()
    if not 1 <= args.count <= 128:
        parser.error("--count must be between 1 and 128")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", args.organization):
        parser.error("--organization must be a GitHub organization name")
    if os.geteuid() == 0:
        print("Run as the account that owns the runners, without sudo.", file=sys.stderr)
        return 1
    if not shutil.which("systemctl"):
        print("systemctl is missing; this helper needs systemd user services.", file=sys.stderr)
        return 1

    root = args.runner_root.expanduser().resolve()
    print(f"Runner directory: {root}")
    records = []
    problems = False
    for index in range(1, args.count + 1):
        directory = root / f"ag{index}"
        unit = f"agentscient-runner@{index}.service"
        error = registration(directory, args.organization)
        values = None
        if error is None:
            values, error = unit_state(unit, directory)
        if error:
            print(f"FAIL {unit}: {error}")
            problems = True
        records.append((unit, directory, values))

    # Validate the entire selected pool before starting anything.
    if args.command == "start" and problems:
        print("No services started. Correct the registration/service checks above first.")
    for unit, directory, values in records:
        if values is None:
            continue
        if running(values):
            print(f"PASS {unit}: running")
            continue
        if args.command == "start" and not problems and values.get("ActiveState") in ("inactive", "failed"):
            result = invoke(["systemctl", "--user", "start", unit], timeout=60)
            fresh, error = unit_state(unit, directory)
            if result is not None and result.returncode == 0 and error is None and running(fresh):
                print(f"PASS {unit}: started and running")
                continue
            print(f"FAIL {unit}: start did not establish a running service; inspect its user journal")
        else:
            # Never echo arbitrary process output or credential-bearing unit configuration.
            print(f"FAIL {unit}: not running" + ("; use the start command" if args.command == "status" else ""))
        problems = True

    docker = invoke(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20) if shutil.which("docker") else None
    if docker is None or docker.returncode:
        print("FAIL Docker: daemon is unavailable to this user; check Docker Desktop / WSL integration.")
        problems = True
    else:
        print("PASS Docker: daemon is reachable")
    print("Local checks only: GitHub connectivity and job scheduling are not tested.")
    return int(problems)


raise SystemExit(main())
PY
