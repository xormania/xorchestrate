"""Compose shared checks, check-scoped readiness and exact run evidence."""

from __future__ import annotations

import importlib.util
import math
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import quote

from .adapters.evidence import (
    FileInputs,
    RecordingRunner,
    allocate_run,
    candidate_identity,
    digest,
    verify_candidate,
    write_json,
)
from .adapters.static import STATIC_IDS
from .core import execute_checks, required_inputs, validate_checks
from .model import COMPONENT_SUITES, MODES, SUITES, Check

ROOT = Path(__file__).resolve().parents[2]
LIVE_IDS = ("acceptance", "multi-harness")


def _selection(suite):
    if suite not in SUITES:
        raise ValueError("Unknown suite")
    return COMPONENT_SUITES if suite == "all" else ("static", "tests") if suite == "checks" else (suite,)


def _options(*, suite, mode, timeout, check_timeout, config, driver, families, drivers=(), shutdown_grace=10.0):
    selected = _selection(suite)
    if mode not in MODES:
        raise ValueError("Unknown mode")
    for value in (timeout, check_timeout, shutdown_grace):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Timeout must be a positive finite number")
    if families and "tests" not in selected:
        raise ValueError("--family selects pytest families in the tests suite")
    if (not isinstance(drivers, (tuple, list)) or any(not isinstance(d, str) or not d for d in drivers)
            or len(drivers) != len(set(drivers))):
        raise ValueError("Acceptance drivers must be unique nonempty names")
    if driver is not None and drivers:
        raise ValueError("Select --driver or multiple drivers, not both")
    if mode != "native" and (config is not None or driver is not None or drivers):
        raise ValueError("--config and --driver require native mode")
    return selected


def _brief(root, brief):
    source = Path(brief).expanduser() if brief else Path("docs/implementation/owner-brief.md")
    return source.resolve() if source.is_absolute() else (root / source).resolve()


def cache_environment(root):
    """Startup policy must precede imports, including the framework itself."""
    if sys.pycache_prefix is None:
        raise ValueError("Set PYTHONPYCACHEPREFIX to an external directory before starting Python")
    cache = Path(sys.pycache_prefix).resolve()
    if cache == root or root in cache.parents:
        raise ValueError("PYTHONPYCACHEPREFIX must be outside the measured candidate")
    return {"PYTHONPYCACHEPREFIX": str(cache)}


def plan(*, directory, suite, mode, config=None, driver=None, timeout=180, root=ROOT, brief=None,
         families=(), check_timeout=600, drivers=(), shutdown_grace=10.0):
    """Pure check definitions; filesystem preparation belongs to the owning check."""
    selected = _options(suite=suite, mode=mode, timeout=timeout, check_timeout=check_timeout,
                        config=config, driver=driver, families=families, drivers=drivers,
                        shutdown_grace=shutdown_grace)
    root, directory = Path(root).resolve(), Path(directory).resolve()
    checks = []
    if "static" in selected:
        from .adapters.static import definitions

        checks.extend(definitions(directory=directory, timeout=check_timeout))
    if "tests" in selected:
        argv = [sys.executable, "-m", "pytest", "tests/hexagonal", "-q", "-rs", "-p", "no:cacheprovider",
                "--test-inventory", str(directory / "inventory.json"), "--junitxml=" + str(directory / "tests.xml")]
        for family in families:
            argv += ["--test-family", family]
        checks.append(Check("tests", tuple(argv), check_timeout, ("contracts/testing.json", "tests/conftest.py")))
    if "calibration" in selected:
        for name, module in (("native-calibration", "scripts.verification.calibrate_suite"),
                             ("work-calibration", "tests.hexagonal.calibrate_work_acceptance"),
                             ("state-calibration", "scripts.verification.calibrate_state")):
            options = ("--output", str(directory / name)) if name == "work-calibration" else (
                "--candidate", str(root), "--out", str(directory / name))
            checks.append(Check(name, (sys.executable, "-m", module, *options), check_timeout,
                                (module.replace(".", "/") + ".py",)))
    if "acceptance" in selected:
        for selected_driver in drivers or (driver,):
            check_id = "acceptance:" + quote(selected_driver, safe="") if drivers else "acceptance"
            output_name = "acceptance-" + quote(selected_driver, safe="") if drivers else "acceptance"
            argv = [sys.executable, "-m", "scripts.acceptance", "--output", str(directory / output_name),
                    "--profile", "native" if mode == "native" else "fixture", "--timeout", str(timeout),
                    "--shutdown-grace", str(shutdown_grace * 0.5),
                    "--cleanup-budget", str(shutdown_grace * 0.8)]
            if config:
                argv += ["--config", str(Path(config).expanduser().resolve())]
                if selected_driver:
                    argv += ["--driver", selected_driver]
            checks.append(Check(check_id, tuple(argv), timeout + 60, ("scripts/acceptance/__main__.py",)))
    if "multi-harness" in selected:
        argv = [sys.executable, "-m", "scripts.verification.multi_harness", "--mode", mode,
                "--candidate", str(root), "--brief", str(directory / "owner-brief.md"),
                "--criteria", str(directory / "criteria.json"), "--out", str(directory / "multi-harness"),
                "--timeout", str(timeout)]
        if config:
            argv += ["--config", str(Path(config).expanduser().resolve())]
        checks.append(Check("multi-harness", tuple(argv), timeout * 64 + 60,
                            ("scripts/verification/multi_harness.py",)))
    return validate_checks(checks)


def _native_readiness(check, *, config, driver):
    if config is None:
        raise ValueError("Native mode requires --config; no fixture fallback")
    from xorchestrate.adapters.jsonrpc import command_options
    from xorchestrate.composition import BUILTINS, load_config

    configuration, base = load_config(Path(config).expanduser().resolve())
    if not isinstance(configuration, dict) or not isinstance(configuration.get("sessions", {}), dict):
        raise ValueError("Native configuration needs a sessions object")
    if check.name.startswith("acceptance:"):
        driver = check.argv[check.argv.index("--driver") + 1] if "--driver" in check.argv else None
    if (check.name == "acceptance" or check.name.startswith("acceptance:")) and not driver:
        raise ValueError("Native acceptance requires --driver")
    peers = ("codex", "claude", "grok") if check.name == "multi-harness" else (driver,)
    items = []
    for peer in peers:
        spec = configuration.get("sessions", {}).get(peer, {})
        if not isinstance(spec, dict) or not isinstance(spec.get("options", {}), dict):
            raise ValueError("Session driver and options must be objects: " + peer)
        options = spec.get("options", {})
        items.append({"name": peer + ":application_tools", "passed": bool(spec)
                      and spec.get("enabled", True) is True and options.get("application_tools") is True})
        if spec.get("adapter") in BUILTINS["sessions"]:
            command = command_options(options) if spec.get("adapter") != "bridge" else None
            cwd = (base / options.get("cwd", ".")).resolve()
            items.append({"name": peer + ":cwd", "passed": cwd.is_dir(), "path": str(cwd)})
            if command:
                executable = command["args"][0]
                binary = str(cwd / executable) if "/" in executable else shutil.which(
                    executable, path=command["env"].get("PATH", os.defpath))
                items.append({"name": peer + ":executable", "passed": binary is not None
                              and Path(binary).is_file() and os.access(binary, os.X_OK), "path": binary})
    return items


def readiness(check, *, root, mode, config=None, driver=None, brief=None, families=()):
    """One side-effect-free prerequisite definition for doctor and execution."""
    items = []
    try:
        missing = required_inputs(check, inputs=FileInputs(), cwd=str(root))
        items.extend({"name": path, "passed": False, "reason": "Required input is absent"} for path in missing)
        if check.name == "ruff":
            items.append({"name": "ruff", "passed": importlib.util.find_spec("ruff") is not None})
        if check.name == "tests":
            from .adapters.pytest_plugin import load_catalog
            from .catalog import select_families

            _, assignments = load_catalog(root)
            select_families(assignments, families)
            items.append({"name": "test_catalog", "passed": True, "modules": len(assignments)})
        if check.name == "acceptance" or check.name.startswith("acceptance:"):
            binary = shutil.which("tmux")
            items.append({"name": "tmux", "passed": binary is not None, "path": binary})
        if check.name == "multi-harness":
            source = _brief(root, brief)
            items.append({"name": "owner_brief", "passed": source.is_file(), "path": str(source)})
        if mode == "native" and (check.name in LIVE_IDS or check.name.startswith("acceptance:")):
            items.extend(_native_readiness(check, config=config, driver=driver))
    except Exception as error:
        items.append({"name": "configuration", "passed": False, "reason": str(error)})
    failed = [item for item in items if not item["passed"]]
    return {"name": check.name, "status": "setup_error" if failed else "passed", "readiness": items,
            "reason": "; ".join(item.get("reason", item["name"] + " unavailable") for item in failed)}


def preflight(*, suite, mode, config=None, driver=None, timeout=180, root=ROOT,
              families=(), check_timeout=600, brief=None, drivers=(), shutdown_grace=10.0):
    root = Path(root).resolve()
    cache_environment(root)
    checks = plan(directory=root.parent / "testkit-preflight", suite=suite, mode=mode, config=config,
                  driver=driver, timeout=timeout, root=root, brief=brief, families=families,
                  check_timeout=check_timeout, drivers=drivers, shutdown_grace=shutdown_grace)
    if families:
        try:
            from .adapters.pytest_plugin import load_catalog
            from .catalog import select_families

            _, assignments = load_catalog(root)
        except (OSError, ValueError, TypeError, ImportError):
            # Unavailable catalog/configuration blocks tests, not independent
            # checks. A known invalid selection still rejects the entire plan.
            pass
        else:
            select_families(assignments, families)
    rows = [readiness(check, root=root, mode=mode, config=config, driver=driver, brief=brief, families=families)
            for check in checks]
    return {"status": "passed" if all(row["status"] == "passed" for row in rows) else "setup_error",
            "suite": suite, "mode": mode, "checks": rows,
            "authentication": "verified by live callbacks only" if mode == "native" else "not used"}


def run(*, output, suite="all", mode="protocol-fixture", config=None, driver=None,
        timeout=180, root=ROOT, brief=None, families=(), check_timeout=600,
        directory=None, cancellation=None, result_sink=None, shutdown_grace=10.0, drivers=()):
    root, output = Path(root).resolve(), Path(output).expanduser().resolve()
    if output == root or root in output.parents:
        raise ValueError("Run output must be outside the measured candidate")
    if directory is None:
        directory = allocate_run(output)
    else:
        directory = Path(directory).expanduser().resolve()
        if directory == root or root in directory.parents:
            raise ValueError("Run directory must be outside the measured candidate")
        if directory.exists() and any(directory.iterdir()):
            raise ValueError("Run directory must be new or empty")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {"schema_version": 2, "suite": suite, "mode": mode, "status": "setup_error",
              "directory": str(directory), "checks": [], "runtime": {"python": sys.version}}
    progress = {"schema_version": 1, "revision": 0, "checks": {}}

    def publish(event):
        # This composition is the only canonical result writer. The external
        # observer receives each transition only after its checkpoint commits.
        progress["revision"] += 1
        event = {**event, "revision": progress["revision"]}
        progress["last_event"] = event
        if event["event"] == "inventory":
            progress["selected_check_ids"] = [item["name"] for item in event["checks"]]
            report["checks"] = [{"name": item["name"], "status": "selected",
                                 "validation": "pending", "launched": False} for item in event["checks"]]
        if "check" in event:
            progress["checks"][event["check"]] = event
            row = next((item for item in report["checks"] if item["name"] == event["check"]), None)
            if row is not None and event["event"] != "qualified":
                row.update({key: event[key] for key in (
                    "status", "exit_status", "launched", "reason", "shutdown", "validation") if key in event})
                if event["event"] == "started":
                    row.update(status="running", launched=None)
        write_json(directory / "progress.json", progress)
        write_json(directory / "report.json", report)
        if result_sink is not None:
            result_sink(event)

    try:
        env = cache_environment(root)
        report["environment"] = env
        checks = plan(directory=directory, suite=suite, mode=mode, config=config, driver=driver,
                      timeout=timeout, root=root, brief=brief, families=families,
                      check_timeout=check_timeout, drivers=drivers, shutdown_grace=shutdown_grace)
        report["selected_check_ids"] = [check.name for check in checks]
        ready = preflight(suite=suite, mode=mode, config=config, driver=driver, timeout=timeout, root=root,
                          brief=brief, families=families, check_timeout=check_timeout, drivers=drivers,
                          shutdown_grace=shutdown_grace)
        write_json(directory / "preflight.json", ready)
        report["preflight"] = ready
        before = candidate_identity(root) if (root / ".git").exists() else None
        report["candidate_validation"] = "pending" if before is not None else "not_applicable"
        if before is not None:
            write_json(directory / "candidate.json", before)
            report["candidate"] = str(directory / "candidate.json")
        runners = {check.name: RecordingRunner(directory / "commands" / check.name,
                   terminal="inherit" if mode == "native" and (
                       check.name in LIVE_IDS or check.name.startswith("acceptance:")) else "capture",
                   cancellation=cancellation, shutdown_grace=shutdown_grace,
                   shutdown_target="owner" if check.name in LIVE_IDS or check.name.startswith("acceptance:") else "group")
                   for check in checks}
        resolved, manifests = {}, {}

        def prepare(check):
            row = readiness(check, root=root, mode=mode, config=config, driver=driver, brief=brief, families=families)
            if row["status"] != "passed":
                raise ValueError(row["reason"])
            prepared = check
            if check.name in STATIC_IDS:
                from .adapters.static import prepare_static

                prepared, manifest = prepare_static(check, root=root, directory=directory,
                                                    runner=runners[check.name], env=env)
                manifests[check.name] = manifest
                write_json(directory / (check.name + "-inputs.json"), manifest)
            elif check.name == "multi-harness":
                from scripts.verification.checker import default_criteria

                frozen = directory / "owner-brief.md"
                frozen.write_bytes(_brief(root, brief).read_bytes())
                frozen.chmod(0o600)
                write_json(directory / "criteria.json", default_criteria(digest(frozen.read_bytes())))
            resolved[check.name] = {"name": prepared.name, "argv": list(prepared.argv),
                                    "timeout": prepared.timeout, "required": list(prepared.required)}
            return prepared

        report["status"] = "running"
        results = execute_checks(checks, runner=next(iter(runners.values())), runners=runners,
                                 inputs=FileInputs(), cwd=str(root), env=env, prepare=prepare,
                                 cancellation=cancellation, result_sink=publish)
        for name, result in results:
            runner = runners[name]
            main = next((record for record in reversed(runner.records)
                         if record["command"] == list(result.argv)), None)
            row = {"name": name, "status": result.status if result.status != "completed" else (
                   "passed" if result.exit == 0 else "failed"), "launched": result.launched,
                   "exit_status": main["exit_status"] if main else result.exit, "duration_seconds": result.duration,
                   "command_record": str(Path(main["directory"]) / "command.json") if main else None,
                   "reason": result.stderr if result.exit != 0 else "",
                   "shutdown": result.shutdown, "validation": "complete"}
            if name in manifests:
                from .adapters.static import bind_static_report, static_manifest

                if result.launched:
                    try:
                        row["tool_report"] = bind_static_report(name, directory=directory, manifest=manifests[name])
                        if row["status"] == "passed" and row["tool_report"]["status"] != "passed":
                            row.update(status="evidence_error", reason="Tool report contradicts successful command")
                    except Exception as error:
                        row["evidence_error"] = str(error)
                        if row["status"] == "passed":
                            row.update(status="evidence_error", reason=str(error))

                try:
                    after = static_manifest(name, root=root, directory=directory, runner=runner,
                                            env=env, timeout=check_timeout)
                    passed = manifests[name]["content_sha256"] == after["content_sha256"]
                    row["integrity"] = {"passed": passed, "before": manifests[name]["content_sha256"],
                                        "after": after["content_sha256"]}
                    write_json(directory / (name + "-inputs-after.json"), after)
                    if not passed and row["status"] == "passed":
                        row.update(status="integrity_error", reason="Effective inputs changed during validation")
                except Exception as error:
                    row["integrity"] = {"passed": False, "reason": str(error)}
                    if row["status"] == "passed":
                        row.update(status="integrity_error", reason=str(error))
                row["input_manifest"] = str(directory / (name + "-inputs.json"))
            row["command_records"] = [str(Path(record["directory"]) / "command.json") for record in runner.records]
            existing = next((index for index, item in enumerate(report["checks"]) if item["name"] == name), None)
            if existing is None:
                report["checks"].append(row)
            else:
                report["checks"][existing] = row
            publish({"event": "qualified", "check": name, "result": row})
        write_json(directory / "plan.json", {"schema_version": 1, "checks": list(resolved.values()),
                   "declared": [{"name": check.name, "argv": list(check.argv), "timeout": check.timeout,
                                 "required": list(check.required)} for check in checks]})
        report["plan"] = str(directory / "plan.json")
        report["status"] = "passed" if len(results) == len(checks) and all(
            row["status"] == "passed" for row in report["checks"]) else "failed"
        if before is not None:
            report["candidate_integrity"] = verify_candidate(root, before)
            report["candidate_validation"] = "passed" if report["candidate_integrity"]["passed"] else "failed"
            if not report["candidate_integrity"]["passed"]:
                report["status"] = "failed"
    except Exception as error:
        report["status"] = "failed" if report["checks"] else "setup_error"
        report["error"] = {"type": type(error).__name__, "diagnostic": str(error)}
    except BaseException as error:
        report["status"] = "interrupted"
        report["error"] = {"type": type(error).__name__, "diagnostic": str(error)}
        write_json(directory / "report.json", report)
        raise
    write_json(directory / "report.json", report)
    return report
