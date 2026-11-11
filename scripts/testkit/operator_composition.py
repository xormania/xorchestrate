"""Wire managed lifecycle decisions to shared execution and owned adapters."""

from __future__ import annotations

import contextlib
import importlib.metadata
import json
import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .adapters import terminal_selection, terminals
from .adapters.configuration import isolated_config
from .adapters.diagnostics import failure_lines
from .adapters.evidence import candidate_identity, digest, now, write_json
from .adapters.managed_process import ManagedWorker
from .adapters.managed_store import ManagedStore, owner_alive
from .adapters.native_evidence import collection_supplement
from .adapters.ownership import processes_naming, send_signal, subreaper
from .composition import ROOT, preflight, run
from .composition import plan as check_plan
from .operator import FINISHED, decide, project

RUN_FIELDS = ("suite", "mode", "config", "brief", "drivers", "families", "shard", "timeout", "check_timeout",
              "stories", "max_sessions", "max_turns", "max_tool_calls", "terminal_host")
PROBE_STORY = "native-handshake"


def validate_limits(settings):
    for name in ("run_timeout", "shutdown_grace"):
        value = settings.get(name)
        if name == "run_timeout" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(name + " must be a positive finite number")


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Managed JSON exceeds its size bound")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Managed JSON must contain an object")
    return result


def execution_options(plan):
    return {name: plan[name] for name in RUN_FIELDS if name in plan}


def prepare_run(settings, *, parent=None):
    """Freeze reusable inputs before launching; fresh effects always get fresh IDs."""
    settings = dict(settings)
    config_source_base = settings.pop("config_source_base", None)
    validate_limits(settings)
    root = Path(settings.get("root", ROOT)).resolve()
    output = Path(settings["output"]).expanduser().resolve()
    if output == root or root in output.parents:
        raise ValueError("Managed output must be outside the candidate")
    # Structural validation precedes ownership or check launch. Readiness stays
    # check-scoped inside the existing executor.
    checks = check_plan(directory=output / "planned", root=root, shutdown_grace=settings["shutdown_grace"],
                        **execution_options(settings))
    # Reuse selection validation without gating independent checks on readiness.
    preflight(root=root, shutdown_grace=settings["shutdown_grace"], **execution_options(settings))
    directory = ManagedStore.allocate(output)
    store = ManagedStore(directory)
    settings.update(schema_version=1, root=str(root), output=str(output),
                    interpreter=str(settings.get("interpreter") or sys.executable),
                    parent_run_id=Path(parent).name if parent else None,
                    selected_check_ids=[check.name for check in checks], created_at=now(),
                    keep_after_run=bool(settings.get("keep_after_run")),
                    # Short and private, derived from the run directory; close re-derives it.
                    terminal_endpoint=str(terminals.create_endpoint(directory, ENDPOINT_PREFIX)))
    dependencies = {}
    for package in ("jsonschema", "pytest", "ruff"):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    settings["runtime"] = {"python": sys.version, "dependencies": dependencies}
    store.initialize(settings)
    sources = {name: settings[name] for name in ("config", "brief", "profile_path")
               if settings.get(name) and Path(settings[name]).is_file()}
    snapshots, snapshot_errors = {}, {}
    for name, source in sources.items():
        try:
            snapshots.update(store.snapshot_inputs({name: source}))
        except OSError as error:
            # Unavailable check inputs remain check-scoped readiness failures.
            snapshot_errors[name] = type(error).__name__
    if "config" in snapshots and config_source_base is not None:
        snapshots["config"]["source_base"] = config_source_base
    if snapshots:
        write_json(directory / "inputs" / "manifest.json", snapshots)
    if "brief" in snapshots:
        settings["brief"] = snapshots["brief"]["path"]
    if "config" in snapshots:
        settings["config"] = snapshots["config"]["path"]
        try:
            configuration = read_json(snapshots["config"]["path"])
            resolved = isolated_config(configuration, snapshots["config"]["source_base"], directory / "derived", capture=settings.get("mode") == "native",
                                       project=settings.get("project"))
            target = directory / "inputs" / "native-effective.json"
            write_json(target, resolved)
            settings["config"] = str(target)
            settings["effective_config"] = {"path": str(target), "sha256": digest(target.read_bytes())}
        except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
            snapshot_errors["config"] = type(error).__name__
    settings["input_snapshots"] = snapshots
    settings["input_snapshot_errors"] = snapshot_errors
    if (root / ".git").exists():
        settings["source"] = candidate_identity(root)
    if parent:
        prior = read_json(Path(parent) / "plan.json")
        settings["differences"] = {key: {"from": prior.get(key), "to": settings.get(key)}
                                   for key in (*RUN_FIELDS, "root", "interpreter", "source", "runtime")
                                   if prior.get(key) != settings.get(key)}
    write_json(directory / "plan.json", settings)
    return directory


def start(settings, *, parent=None):
    directory = prepare_run(settings, parent=parent)
    plan = read_json(directory / "plan.json")
    log_path = directory / "supervisor.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen([plan["interpreter"], "-m", "scripts.testkit", "_supervise", str(directory)],
                                   cwd=plan["root"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    deadline = time.monotonic() + 20
    store = ManagedStore(directory)
    while time.monotonic() < deadline:
        state = store.read_state()
        if state.get("terminal") and owner_alive(state.get("owner")):
            return {"status": "launched", "run_id": directory.name, "directory": str(directory)}
        if state.get("phase") in FINISHED or process.poll() is not None:
            raise RuntimeError(f"Managed startup failed; inspect {directory}/supervisor.log and state.json")
        time.sleep(.05)
    raise RuntimeError(f"Managed startup acknowledgement timed out; inspect {directory}")


def status(directory):
    store = ManagedStore(directory)
    state = store.read_state()
    supervisor = owner_alive(state.get("owner"))
    # The terminal host exposes no process identities: the worker is alive while its live supervisor has not seen it exit.
    worker = supervisor and state.get("worker_state") != "exited" and state.get("phase") not in FINISHED
    result = project(state, owner_alive=supervisor, worker_alive=worker)
    result["directory"] = str(store.directory)
    progress = store.directory / "checks" / "progress.json"
    if progress.exists():
        result["progress"] = read_json(progress)
    # Computed for this reader only: the saved state and its export keep codes.
    reasons = failure_lines(store.directory / "checks")
    if reasons:
        result["failures"] = reasons
    return result


def wait(directory):
    while True:
        result = status(directory)
        if result["phase"] in FINISHED:
            return result
        time.sleep(.1)


def control(directory, action):
    store = ManagedStore(directory)
    state = store.read_state()
    supervisor_alive = owner_alive(state.get("owner"))
    if action == "close" and state.get("phase") not in FINISHED and supervisor_alive:
        raise ValueError("Active runs must finalize before their dashboard can close")
    if action == "stop" and state.get("phase") in FINISHED:
        return {"status": "already_finished", "outcome": state.get("outcome", "failed")}
    if not supervisor_alive:
        if action == "collect":
            return store.collect(supplemental=collection_supplement)
        if action == "close":
            # Finished, or abandoned by a supervisor that died: end whatever of the run is still reachable.
            return close_without_supervisor(directory, state)
        raise RuntimeError("No live supervisor; run is incomplete and cannot be safely controlled")
    request = store.submit(action)
    if action == "stop":
        return {"status": "requested", "request_id": request["id"], "directory": str(store.directory)}
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = store.request_result(request["id"])
        if response:
            result = response["result"]
            if result.get("status") == "error":
                raise RuntimeError(result.get("diagnostic", "Managed control failed"))
            return result
        if not owner_alive(store.read_state().get("owner")):
            raise RuntimeError("Supervisor exited before acknowledging the request")
        time.sleep(.05)
    raise TimeoutError("Control is still pending; inspect status and its identified request")


def worker(directory):
    directory = Path(directory).resolve()
    plan = read_json(directory / "plan.json")
    interrupted = []
    handlers = {}
    os.environ["XORCHESTRATE_PROOF_DIRECTORY"] = str(directory)
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        handlers[number] = signal.signal(number, lambda number, frame: interrupted.append(f"signal_{number}"))

    def cancellation():
        if interrupted:
            return interrupted[0]
        path = directory / "cancel.json"
        return read_json(path).get("reason") if path.exists() else None

    event_directory = directory / "events"
    event_directory.mkdir(mode=0o700)
    sequence = 0

    def publish(event):
        nonlocal sequence
        sequence += 1
        write_json(event_directory / f"{sequence:08d}.json", {**event, "sequence": sequence, "recorded_at": now()})

    try:
        report = run(directory=directory / "checks", output=directory, root=Path(plan["root"]),
                     cancellation=cancellation, result_sink=publish,
                     shutdown_grace=plan.get("shutdown_grace", 10), **execution_options(plan))
        write_json(directory / "worker.json", {"status": report["status"], "completed_at": now(),
                                              "report": str(directory / "checks" / "report.json")})
        return 0 if report["status"] == "passed" else 1
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)


ENDPOINT_PREFIX = "xp-"


def remove_endpoint(path):
    """Remove a run's endpoint directory only if it is one this kit created: ``xp-*`` directly in the temporary
    root, a real directory (not a symlink), owned by this user and private. Anything else is refused and left."""
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    if (path.parent != Path(tempfile.gettempdir()) or not path.name.startswith(ENDPOINT_PREFIX)
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def endpoint_problem(directory, plan):
    """Why the run's endpoint cannot be used (``None`` when it can), read without following links.

    The endpoint is derived from the run's own directory; a ``plan.json`` naming any other (the run's harness
    can rewrite it) is refused. If anything is left there, it is a private directory of this user and its
    socket is a socket, no link to another one.
    """
    socket = host_placement(directory, plan)["socket"]
    endpoint = socket.parent
    if plan.get("terminal_endpoint") not in (None, str(endpoint)):
        return "endpoint_not_owned"
    try:
        info = endpoint.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        return "endpoint_not_owned"
    try:
        if not stat.S_ISSOCK(socket.lstat().st_mode):
            return "endpoint_not_owned"
    except FileNotFoundError:
        pass
    return None


def host_placement(directory, plan):
    """Where a managed run's terminal host lives: its state under the run, its endpoint in a short private
    directory derived from the run directory (never read from ``plan.json``)."""
    endpoint = terminals.run_endpoint(directory, ENDPOINT_PREFIX)
    return {"name": "proof", "location": Path(directory) / "terminal-host", "socket": endpoint / "host.sock"}


def managed_worker(directory, plan):
    """The run's host from its selection: owned, unless the run keeps its terminals for a later close."""
    keep = bool(plan.get("keep_after_run"))
    selected = terminal_selection.build(plan.get("terminal_host"), owned=not keep, **host_placement(directory, plan))
    if selected.host is None:
        raise FileNotFoundError(f"Managed start requires a ready terminal host ({selected.reason}); "
                                "foreground run remains available")
    return ManagedWorker(directory, host=selected.host, host_report=selected.report(), root=plan["root"],
                         interpreter=plan["interpreter"], grace=plan.get("shutdown_grace", 10), keep_after_run=keep)


def close_without_supervisor(directory, state):
    """Close a finished run whose supervisor is gone: end its host at the recorded placement, then stop leftovers.

    Kept terminals are adopted from their receipts first. ``shutdown`` reaches
    terminals no live controller holds; anything still naming the run (an
    owned host does not end with a supervisor that was killed outright) is
    stopped by identity. Cleanup is reported from what is actually left.
    """
    directory = Path(directory)
    terminal = state.get("terminal") or {}
    keep = bool(terminal.get("keep_after_run"))
    plan = read_json(directory / "plan.json")
    placement = host_placement(directory, plan)
    before = processes_naming(directory)
    found = bool(before) or placement["socket"].exists()
    adopted, errors = [], []
    # The run's harness can rewrite plan.json: a built-in host name only, at an endpoint that is still the run's own.
    try:
        selection = terminal_selection.recorded(plan.get("terminal_host"))
        problem = endpoint_problem(directory, plan)
    except ValueError:
        selection, problem = None, "terminal_host_refused"
    selected = None if problem else terminal_selection.build(selection, owned=not keep, **placement)
    if problem:
        errors.append(problem)
    elif selected.host is None:
        errors.append("terminal_host_unavailable")
    else:
        host = selected.host
        try:
            if keep:
                for receipt in (terminal.get("receipts") or {}).values():
                    try:
                        adopted.append(host.adopt(receipt).name)
                    except ValueError as error:
                        if getattr(error, "code", None) != "terminal_not_recoverable":
                            raise
            host.shutdown()
        except (OSError, ValueError) as error:
            errors.append(getattr(error, "code", "terminal_host_shutdown"))
        finally:
            host.close()
    for number in (signal.SIGTERM, signal.SIGKILL):
        survivors = processes_naming(directory)
        for identity in survivors:
            send_signal(identity, number)
        deadline = time.monotonic() + 3
        while survivors and time.monotonic() < deadline:
            time.sleep(.05)
            survivors = processes_naming(directory)
        if not survivors:
            break
    remaining = processes_naming(directory)
    if not remove_endpoint(placement["socket"].parent):  # the derived one: never what the plan names
        errors.append("endpoint_not_owned")
    cleanup = {"verified": not remaining and not errors and not placement["socket"].exists(),
               "stopped_processes": len(before), "remaining_processes": len(remaining)}
    if errors:
        cleanup["errors"] = sorted(set(errors))
    result = {"status": "closed" if found or adopted else "already_closed", "cleanup": cleanup}
    if keep:
        result["kept_terminals"] = sorted(adopted)
    return result


def supervise(directory):
    directory = Path(directory).resolve()
    store = ManagedStore(directory)
    plan = read_json(directory / "plan.json")
    store.claim_owner()
    process = None
    signals = []
    handlers = {number: signal.signal(number, lambda n, frame: signals.append(n))
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    exports, futures, handled = ThreadPoolExecutor(max_workers=2), {}, set()
    stop_started = None
    started, watermark, closing_requests = time.monotonic(), 0, []
    try:
        with subreaper():
            process = managed_worker(directory, plan)
            details = process.start()
            store.publish({**details, **decide(store.read_state(), "ready")})
            exit_status, finalized, close_requested = None, False, False
            while not close_requested:
                state = store.read_state()
                reason = "supervisor_signal" if signals else None
                limit = plan.get("run_timeout")
                if not finalized and limit and time.monotonic() - started >= limit:
                    reason = reason or "run_deadline"
                for request in store.requests():
                    identifier = request["id"]
                    if identifier in handled:
                        continue
                    handled.add(identifier)
                    if request["action"] == "stop":
                        if state["phase"] not in FINISHED:
                            reason = reason or "operator_stop"
                        store.complete_request(identifier, {"status": "already_finished" if finalized else "requested"})
                    elif request["action"] == "close":
                        if finalized:
                            closing_requests.append(identifier)
                            close_requested = True
                        else:
                            store.complete_request(identifier, {"status": "error", "diagnostic": "Run is still active"})
                    else:
                        futures[identifier] = exports.submit(store.collect, supplemental=collection_supplement)
                if reason and not finalized:
                    changes = decide(state, "stop", reason=reason)
                    if changes:
                        state = store.publish(changes)
                    cancel = directory / "cancel.json"
                    if not cancel.exists():
                        write_json(cancel, {"reason": state.get("reason") or reason, "scope": "run", "at": now()})
                        stop_started = time.monotonic()
                if stop_started and not finalized and time.monotonic() - stop_started > 2 * plan.get("shutdown_grace", 10) + 5:
                    process.interrupt()
                    if time.monotonic() - stop_started > 3 * plan.get("shutdown_grace", 10) + 10:
                        process.cleanup_work()
                for identifier, future in list(futures.items()):
                    if future.done():
                        try:
                            response = future.result()
                        except Exception as error:
                            response = {"status": "error", "diagnostic": str(error)}
                        store.complete_request(identifier, response)
                        del futures[identifier]
                if not finalized:
                    events = sorted((directory / "events").glob("*.json"))
                    if len(events) > watermark:
                        watermark = len(events)
                        store.publish({"artifacts": store.bind_artifacts(events),
                                       "progress": read_json(events[-1])})
                    exit_status = process.poll()
                    if exit_status is not None:
                        path = directory / "checks" / "report.json"
                        report = read_json(path) if path.exists() else None
                        cleanup = process.cleanup_work()
                        terminal_receipts = [read_json(path) for path in directory.rglob("terminal-exit.json")]
                        cleanup["native_terminals"] = terminal_receipts
                        cleanup["forced"] = cleanup["forced"] or any(row.get("forced") for row in terminal_receipts)
                        state = store.publish(decide(store.read_state(), "worker_finished", report=report,
                                                     exit_status=exit_status, cleanup=cleanup))
                        files = sorted((directory / "events").glob("*.json"))
                        if path.exists():
                            files.append(path)
                        files.extend((directory / "checks" / "commands").rglob("command.json"))
                        files.extend(directory.rglob("terminal-exit.json"))
                        store.publish({"artifacts": store.bind_artifacts(files), "report": str(path),
                                       "completed_at": now()})
                        try:
                            collection = store.collect(supplemental=collection_supplement)
                        except Exception as error:
                            collection = {"status": "error", "diagnostic": str(error)}
                        final_state = store.publish(decide(store.read_state(), "finalized", collection=collection))
                        summary = (f"Run: {directory.name}\nPhase: {final_state['phase']}\nProof: {final_state['proof']}\n"
                                   f"Outcome: {final_state['outcome']}\nCleanup verified: {cleanup['verified']}\n"
                                   f"Collection: {collection.get('status')}\nReport: {path}\n")
                        # This summary stays in the private run directory; the export writes its own.
                        summary += "".join(f"Failure: {line}\n" for line in failure_lines(directory / "checks"))
                        (directory / "summary.txt").write_text(summary, encoding="utf-8")
                        (directory / "summary.txt").chmod(0o600)
                        finalized = True
                if finalized and signals:
                    close_requested = True
                time.sleep(.05)
    except BaseException as error:
        with contextlib.suppress(Exception):
            state = store.read_state()
            if state["phase"] not in FINISHED:
                cleanup = process.cleanup_work() if process else {"verified": True, "forced": False}
                store.publish({"phase": "incomplete", "ready": False, "proof": "incomplete", "outcome": "failed",
                               "failure": {"type": type(error).__name__, "diagnostic": str(error)}, "cleanup": cleanup})
        raise
    finally:
        exports.shutdown(wait=True, cancel_futures=True)
        if process:
            # Kept terminals outlive a supervisor that ends without a close request; close adopts them later.
            keep = bool(plan.get("keep_after_run")) and not closing_requests
            process.close(keep=keep)
            if not keep:
                remove_endpoint(host_placement(directory, plan)["socket"].parent)
        for identifier in closing_requests:
            store.complete_request(identifier, {"status": "closed"})
        store.release_owner()
        for number, handler in handlers.items():
            signal.signal(number, handler)
    return 0


def probe_selection(settings):
    """A probe launches configured native harnesses; anything else is a selection error."""
    if settings.get("mode") != "native" or not settings.get("drivers"):
        raise ValueError("--probe launches configured native harnesses; select --mode native and a driver")


def probe(settings, runner):
    """Opt-in doctor step: take each selected native harness through its session handshake.

    The handshake story launches the configured harness, starts its session and
    stops it, using the same executor, evidence and cleanup as any run. The
    story admits no turn. A launched harness is still a real process using the
    operator's account, which is why this is never implied by plain ``doctor``.
    """
    probe_selection(settings)
    selected = {**settings, "suite": "stories", "stories": [PROBE_STORY], "families": [], "shard": None}
    report = foreground(selected, runner)
    return {"status": "passed" if report.get("status") == "passed" else "failed", "story": PROBE_STORY,
            "directory": report.get("directory"),
            "checks": [{"name": row.get("name"), "status": row.get("status")} for row in report.get("checks", [])],
            "failures": failure_lines(report["directory"]) if report.get("directory") else []}


def repeat_settings(directory, overrides):
    prior = read_json(Path(directory) / "plan.json")
    allowed = (*RUN_FIELDS, "output", "shutdown_grace", "run_timeout", "project", "keep_after_run")
    settings = {name: prior[name] for name in allowed if name in prior}
    settings.update(overrides)
    if "config" not in overrides:
        snapshot = prior.get("input_snapshots", {}).get("config")
        if snapshot:
            saved = Path(snapshot["path"])
            if digest(saved.read_bytes()) != snapshot["sha256"]:
                raise ValueError("Saved configuration snapshot differs from its recorded input")
            settings.update(config=str(saved), config_source_base=snapshot["source_base"])
    settings.update(root=str(ROOT), interpreter=sys.executable)
    return settings


def foreground(settings, runner):
    """The same executor owns foreground signals and an optional whole-run bound."""
    validate_limits(settings)
    if settings.get("keep_after_run"):
        raise ValueError("--keep-after-run applies to managed runs (start), which close later")
    reason = []
    started = time.monotonic()
    handlers = {number: signal.signal(number, lambda n, frame: reason.append(f"signal_{n}"))
                for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}

    def cancelled():
        if reason:
            return reason[0]
        limit = settings.get("run_timeout")
        return "run_deadline" if limit and time.monotonic() - started >= limit else None

    try:
        return runner(output=settings["output"], root=ROOT, cancellation=cancelled,
                      shutdown_grace=settings["shutdown_grace"], **execution_options(settings))
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)

