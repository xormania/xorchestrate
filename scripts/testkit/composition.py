"""Select live scenarios and bind shared test-kit ports for local runs and CI."""

from __future__ import annotations

import math
import os
import shutil
import sys
from pathlib import Path

from .adapters.evidence import FileInputs, RecordingRunner, allocate_run, write_json
from .core import execute_checks
from .model import Check

ROOT = Path(__file__).resolve().parents[2]
SUITES = ("acceptance", "multi-harness", "all")
MODES = ("fixture", "protocol-fixture", "native")


def preflight(*, suite, mode, config=None, driver=None, timeout=180, root=ROOT):
    """Validate selected inputs before launching anything; authentication is live evidence."""
    if suite not in SUITES or mode not in MODES:
        raise ValueError("Unknown suite or mode")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be a positive finite number")
    root = Path(root).resolve()
    selected = ("acceptance", "multi-harness") if suite == "all" else (suite,)
    checks = []
    if "acceptance" in selected:
        binary = shutil.which("tmux")
        checks.append({"name": "tmux", "passed": binary is not None, "path": binary})
    if mode == "native":
        if config is None:
            raise ValueError("Native mode requires --config; no fixture fallback")
        from xorchestrate.adapters.jsonrpc import command_options
        from xorchestrate.composition import BUILTINS, load_config
        from xorchestrate.model import OrchestrationError

        try:
            configuration, base = load_config(Path(config).expanduser().resolve())
        except OrchestrationError as error:
            raise ValueError(str(error)) from error
        if not isinstance(configuration, dict) or not isinstance(configuration.get("sessions", {}), dict):
            raise ValueError("Native configuration needs a sessions object")
        peers = set(("codex", "claude", "grok") if "multi-harness" in selected else ())
        if "acceptance" in selected:
            if not driver:
                raise ValueError("Native acceptance requires --driver")
            peers.add(driver)
        for peer in sorted(peers):
            spec = configuration.get("sessions", {}).get(peer, {})
            if not isinstance(spec, dict) or not isinstance(spec.get("options", {}), dict):
                raise ValueError("Session driver and options must be objects: " + peer)
            options = spec.get("options", {})
            checks.append({"name": peer + ":application_tools", "passed":
                bool(spec) and spec.get("enabled", True) is True and options.get("application_tools") is True})
            if spec.get("adapter") in BUILTINS["sessions"]:
                if spec.get("adapter") != "bridge":
                    try:
                        command = command_options(options)
                    except OrchestrationError as error:
                        raise ValueError(peer + ": " + str(error)) from error
                else:
                    command = None
                cwd = (base / options.get("cwd", ".")).resolve()
                checks.append({"name": peer + ":cwd", "passed": cwd.is_dir(), "path": str(cwd)})
                if command:
                    executable = command["args"][0]
                    binary = str(cwd / executable) if "/" in executable else shutil.which(
                        executable, path=command["env"].get("PATH", os.defpath))
                    checks.append({"name": peer + ":executable", "passed":
                        binary is not None and Path(binary).is_file() and os.access(binary, os.X_OK), "path": binary})
    elif config is not None or driver is not None:
        raise ValueError("--config and --driver require native mode")
    return {"status": "passed" if all(c["passed"] for c in checks) else "setup_error",
            "suite": suite, "mode": mode, "checks": checks,
            "authentication": "verified by live callbacks only" if mode == "native" else "not used"}


def plan(*, directory, suite, mode, config=None, driver=None, timeout=180, root=ROOT, brief=None):
    """Declare scenarios once. Both local and CI entrypoints execute this plan."""
    root, directory = Path(root).resolve(), Path(directory).resolve()
    selected = ("acceptance", "multi-harness") if suite == "all" else (suite,)
    checks = []
    if "acceptance" in selected:
        argv = [sys.executable, "-m", "scripts.acceptance", "--output", str(directory / "acceptance"),
                "--profile", "native" if mode == "native" else "fixture", "--timeout", str(timeout)]
        if config:
            argv += ["--config", str(Path(config).expanduser().resolve()), "--driver", driver]
        checks.append(Check("acceptance", tuple(argv), timeout + 60, ("scripts/acceptance/__main__.py",)))
    if "multi-harness" in selected:
        # Freeze owner intent outside the measured candidate before deriving criteria.
        from scripts.verification.checker import default_criteria

        from .adapters.evidence import digest

        source = Path(brief) if brief else root / "docs/implementation/owner-brief.md"
        frozen = directory / "owner-brief.md"
        frozen.write_bytes(source.read_bytes())
        frozen.chmod(0o600)
        criteria = directory / "criteria.json"
        write_json(criteria, default_criteria(digest(frozen.read_bytes())))
        argv = [sys.executable, "-m", "scripts.verification.multi_harness", "--mode", mode,
                "--candidate", str(root), "--brief", str(frozen), "--criteria", str(criteria),
                "--out", str(directory / "multi-harness"), "--timeout", str(timeout)]
        if config:
            argv += ["--config", str(Path(config).expanduser().resolve())]
        checks.append(Check("multi-harness", tuple(argv), timeout * 64 + 60,
                            ("scripts/verification/multi_harness.py",)))
    return tuple(checks)


def run(*, output, suite="all", mode="protocol-fixture", config=None, driver=None,
        timeout=180, root=ROOT, brief=None):
    root, output = Path(root).resolve(), Path(output).expanduser().resolve()
    if output == root or root in output.parents:
        raise ValueError("Run output must be outside the measured candidate")
    directory = allocate_run(output)
    report = {"schema_version": 1, "suite": suite, "mode": mode, "status": "setup_error",
              "directory": str(directory), "checks": []}
    try:
        readiness = preflight(suite=suite, mode=mode, config=config, driver=driver, timeout=timeout, root=root)
        write_json(directory / "preflight.json", readiness)
        if readiness["status"] != "passed":
            report["preflight"] = readiness
        else:
            checks = plan(directory=directory, suite=suite, mode=mode, config=config, driver=driver,
                          timeout=timeout, root=root, brief=brief)
            # Native Claude inherits the operator's terminal; capturing the
            # outer command would silently replace it with pipes and EOF.
            runner = RecordingRunner(directory / "commands", terminal="inherit" if mode == "native" else "capture")
            results = execute_checks(checks, runner=runner, inputs=FileInputs(), cwd=str(root), env={})
            report["checks"] = [{"name": name, "exit_status": result.exit, "duration_seconds": result.duration,
                                  "command_record": runner.records[index]["directory"] + "/command.json"}
                                 for index, (name, result) in enumerate(results)]
            report["status"] = "passed" if all(result.exit == 0 for _, result in results) else "failed"
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "diagnostic": str(error)}
    write_json(directory / "report.json", report)
    return report
