"""Measured lifecycle cases through application operations and external hosts.

Native mode uses the operator's configured native executables. Fixture mode runs
the same host/application boundaries with a declared deterministic session port.
Private memories are supplied only at initialization, never in retention probes.
"""
from __future__ import annotations

import json
import select
import subprocess
import sys
import time
import uuid
from copy import deepcopy

from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError

from .adapters.native import default_decoders
from .capture import now, write_json
from .checker import PEERS, VerificationFailure


def _records(application):
    with application.store.transaction() as tx:
        return {kind: [{**deepcopy(row.data), "id": row.key, "version": row.version} for row in tx.list(kind)]
                for kind in ("session", "operation", "native_observation", "evidence")}


def _call(application, action, **arguments):
    return application.call(action, arguments, actor="owner")


def _view(session):
    backend = session["backend"]
    return {"session_id": session["id"], "native_id": backend["native_id"], "incarnation": backend["incarnation"],
            "ownership": backend.get("ownership"), "configuration_id": backend.get("configuration_id"),
            "settings": deepcopy(backend.get("settings", {}))}


def _native_tool_matches(decoder, mode, row, action, result):
    return any(fact.get("event") == "native_tool" and fact.get("tool") == action
               and fact.get("arguments", {}).get("result") == result
               for fact in decoder.decode(row, mode))


def _probe(suite, application, peer, session, case_id, initialize=False):
    decoder = getattr(suite, "decoders", None) or default_decoders()
    decoder = decoder[peer]
    command = {"kind": "startup" if initialize else "lifecycle_probe", "run_id": suite.run_id, "case_id": case_id}
    if initialize:
        command["memory"] = suite.memories[peer]
        explanation = ("Retain this PRIVATE_MEMORY for the life of this conversation: " + suite.memories[peer] +
            '. Call record_result with result.verification={run_id:' + json.dumps(suite.run_id) +
            ',phase:"ready",memory:PRIVATE_MEMORY}. End this turn after that callback.')
        phase = "ready"
    else:
        explanation = ('Recall the private memory retained earlier in this conversation. Do not invent a replacement. '
            'Call record_result with result.verification={run_id:' + json.dumps(suite.run_id) +
            ',phase:"lifecycle",case_id:' + json.dumps(case_id) +
            ',memory:the retained private memory}. End this turn after that callback. The memory is intentionally absent from this instruction.')
        phase = "lifecycle"
    _call(application, "send_session", session_id=session["id"],
          message={"text": explanation + "\nVERIFICATION_COMMAND=" + json.dumps(command)}, idempotency_key=uuid.uuid4().hex)
    deadline = time.monotonic() + suite.timeout
    while time.monotonic() < deadline:
        raw = _records(application)
        evidence = {row["id"]: row for row in raw["evidence"]}
        for callback in raw["native_observation"]:
            value = callback.get("observation", {})
            if callback.get("session_id") != session["id"] or callback.get("stale") or callback.get("origin") != "adapter":
                continue
            if value.get("phase") != "completed" or value.get("action") != "record_result":
                continue
            payload = value.get("arguments", {}).get("result", {}).get("verification", {})
            if payload.get("run_id") != suite.run_id or payload.get("phase") != phase or (not initialize and payload.get("case_id") != case_id):
                continue
            saved = evidence.get(value.get("result", {}).get("evidence", {}).get("id"))
            if not saved or saved.get("result") != value["arguments"].get("result"):
                raise VerificationFailure("lifecycle_evidence_binding")
            native = next((row for row in raw["native_observation"] if row.get("session_id") == session["id"]
                and row.get("origin") == "adapter" and not row.get("stale")
                and row["observation"].get("call_id") == value.get("call_id")
                and row["observation"].get("native_id") == session["backend"]["native_id"]
                and row["observation"].get("incarnation") == session["backend"]["incarnation"]
                and _native_tool_matches(decoder, suite.mode, row, "record_result", value["arguments"].get("result"))), None)
            if native is None:
                continue
            if payload.get("memory") != suite.memories[peer]:
                raise VerificationFailure("lifecycle_context_lost")
            return {"memory": payload["memory"], "native_id": value["native_id"], "incarnation": value["incarnation"],
                    "call_id": value["call_id"], "session_id": session["id"], "evidence_id": saved["id"],
                    "native_observation_id": native["id"], "callback_observation_id": callback["id"]}
        time.sleep(.025)
    raise VerificationFailure("lifecycle_retained_native_callback_missing")


def _stale(application, session):
    application_error = None
    try:
        _call(application, "send_session", session_id=session["id"], message={"text": "stale connection probe"},
              idempotency_key=uuid.uuid4().hex)
    except OrchestrationError as error:
        application_error = {"code": error.code, "details": error.details}
    driver = application.sessions[session["driver"]]
    adapter_error = None
    try:
        driver.poll(deepcopy(session["backend"]))
    except OrchestrationError as error:
        adapter_error = {"code": error.code, "details": error.details}
    if application_error is None or adapter_error is None:
        raise VerificationFailure("stale_connection_accepted")
    return {"application": application_error, "adapter": adapter_error}


def _append(suite, peer, operation, before, after, operation_id, **evidence):
    suite.lifecycle_events.append({"event": "lifecycle", "peer": peer, "operation": operation,
        "at": now(), "before": _view(before), "after": _view(after), "operation_id": operation_id, **evidence})
    suite.executed.append(f"lifecycle-{peer}-{operation}")


def _host(config_file, peer, endpoint, directory, timeout):
    stderr = (directory / "host-stderr.log").open("w")
    command = [sys.executable, "-m", "xorchestrate.bridge", "--config", str(config_file), "--driver", peer,
        "--endpoint", str(endpoint), "--transport", "fifo", "--state", str(directory / "host-state.json"), "--application-tools"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr, text=True)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if select.select([process.stdout], [], [], .1)[0]:
                line = process.stdout.readline()
                if not line:
                    break
                ready = json.loads(line)
                if ready.get("ready"):
                    write_json(directory / "host-launch.json", {"command": command, "pid": process.pid, "ready": ready, "at": now()})
                    return process, stderr
            if process.poll() is not None:
                break
        raise VerificationFailure("external_host_start_failed")
    except Exception:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)
        stderr.close()
        raise


def run_lifecycle(suite, config, base):
    """Execute all five required lifecycle scenarios for every configured peer."""
    suite.lifecycle_records = []
    for peer in PEERS:
        suite.phase = f"lifecycle-{peer}-resume"
        original = next(row for row in _records(suite.application)["session"] if row["id"] == suite.sessions[peer]["session_id"])
        resumed = _call(suite.application, "start_session", driver=peer, specification={}, actor_id=peer,
            work_id=suite.work_id, resume_session_id=original["id"], idempotency_key=uuid.uuid4().hex)
        after = resumed["session"]
        retained = _probe(suite, suite.application, peer, after, suite.phase)
        stale = _stale(suite.application, original)
        _append(suite, peer, "resume", original, after, resumed["operation_id"],
            retained_observation=retained, stale_rejected=True, stale_errors=stale)
        _call(suite.application, "stop_session", session_id=after["id"], idempotency_key=uuid.uuid4().hex)

        directory = suite.directory / "lifecycle" / peer
        directory.mkdir(parents=True, mode=0o700)
        config_file = directory / "native-host-config.json"
        host_config = deepcopy(config)
        for specification in host_config.get("sessions", {}).values():
            specification.setdefault("options", {}).setdefault("cwd", str(base.resolve()))
        write_json(config_file, host_config)
        endpoint = directory / "host-endpoint"
        process, stderr = _host(config_file, peer, endpoint, directory, suite.timeout)
        runtime = None
        try:
            runtime = build({"store": {"adapter": "sqlite", "options": {"path": str(directory / "state.sqlite3")}},
                "sessions": {peer: {"adapter": "bridge", "options": {"endpoint": str(endpoint), "transport": "fifo", "application_tools": True,
                    "timeout": suite.timeout}}}}, base)
            application = runtime.application
            _call(application, "add_actor", actor_id=peer)
            work = _call(application, "create_work", goal="Verify external native conversation lifecycle")["work"]["id"]
            arguments = {"driver": peer, "specification": {}, "actor_id": peer, "work_id": work}
            first = _call(application, "attach_session", **arguments, idempotency_key=uuid.uuid4().hex)["session"]
            _probe(suite, application, peer, first, f"lifecycle-{peer}-initialize", initialize=True)
            _call(application, "detach_session", session_id=first["id"], idempotency_key=uuid.uuid4().hex)
            suite.phase = f"lifecycle-{peer}-attach"
            attached = _call(application, "attach_session", **arguments, idempotency_key=uuid.uuid4().hex)
            retained = _probe(suite, application, peer, attached["session"], suite.phase)
            stale = _stale(application, first)
            _append(suite, peer, "attach", first, attached["session"], attached["operation_id"],
                retained_observation=retained, stale_rejected=True, stale_errors=stale)

            suite.phase = f"lifecycle-{peer}-detach"
            detached = _call(application, "detach_session", session_id=attached["session"]["id"], idempotency_key=uuid.uuid4().hex)
            alive = process.poll() is None
            if not alive:
                raise VerificationFailure("external_process_terminated")
            _append(suite, peer, "detach", attached["session"], detached["session"], detached["operation_id"], process_alive_after=alive,
                host_process={"pid": process.pid, "returncode": process.poll(), "observed_at": now()})
            suite.lifecycle_events[-1]["after"]["connection"] = "detached"

            previous = detached["session"]
            for operation in ("reconnect", "replace"):
                suite.phase = f"lifecycle-{peer}-{operation}"
                replacement = _call(application, "reconnect_session", **arguments,
                    resume_session_id=previous["id"], idempotency_key=uuid.uuid4().hex)
                retained = _probe(suite, application, peer, replacement["session"], suite.phase)
                stale = _stale(application, previous)
                _append(suite, peer, operation, previous, replacement["session"], replacement["operation_id"],
                    retained_observation=retained, stale_rejected=True, stale_errors=stale)
                previous = replacement["session"]
        finally:
            if runtime is not None:
                suite.lifecycle_records.append({"peer": peer, "records": _records(runtime.application)})
                write_json(directory / "raw-records.json", suite.lifecycle_records[-1])
                runtime.close()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            stderr.close()
            write_json(directory / "cleanup.json", {"host_returncode": process.returncode,
                "endpoint_removed": not endpoint.exists(), "at": now()})
            if process.returncode != 0 or endpoint.exists():
                raise VerificationFailure("external_host_cleanup_failed")
