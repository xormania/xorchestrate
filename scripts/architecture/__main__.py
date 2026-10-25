"""Run a declared architecture contract locally or in CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from .python import check


def _read(root: Path, path: str) -> dict:
    value = json.loads((root / path).read_text())
    if not isinstance(value, dict):
        raise ValueError("Configuration must be a JSON object.")
    return value


def _identity(root: Path) -> dict:
    result = {"python": platform.python_version()}
    checker = Path(__file__).parent
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(checker.glob("*.py"))}
    result["checker_sha256"] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                             text=True, timeout=5, check=False)
        if git.returncode == 0:
            result["git_commit"] = git.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--profile", default="contracts/hexagonal.json")
    parser.add_argument("--output", type=Path, help="JSON evidence (stdout when omitted)")
    parser.add_argument("--jev-config", help="Diagnostic adapter JSON configuration; credentials stay in the environment")
    parser.add_argument("--diagnostics-only", action="store_true",
                        help="Report diagnostics without changing the separate deterministic CI gate")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        profile = _read(root, args.profile)
        report = check(root, profile)
    except (OSError, ValueError, TypeError) as error:
        report = {"schema_version": 1, "status": "error", "findings": [
            {"rule_id": "profile.load", "message": str(error)}], "summary": {"errors": 1}}
    report["execution"] = _identity(root)
    if args.jev_config:
        from .diagnostics import diagnose

        try:
            options = _read(root, args.jev_config)
            # Operators can select a deployment without editing the reusable profile.
            for key, variable in (("endpoint", "JEV_ENDPOINT"), ("model", "JEV_MODEL")):
                if os.environ.get(variable):
                    options[key] = os.environ[variable]
            report["diagnostics"] = diagnose(report, root, options)
        except (OSError, ValueError, TypeError):
            report["diagnostics"] = {"status": "error", "reason": "diagnostic_configuration_invalid"}
    else:
        report["diagnostics"] = {"status": "not_configured"}
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
        print(f"Architecture: {report['status']}; Jev diagnostics: {report['diagnostics']['status']}; "
              f"evidence: {args.output}")
    else:
        sys.stdout.write(encoded)
    if args.diagnostics_only:
        return 0
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
