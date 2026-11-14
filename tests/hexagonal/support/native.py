"""Concrete native test fixtures shared by contracts and scenario adapters.

Peer command/configuration, safe observation waits, callback scheduling and owned
runtime setup live here. Collected tests declare behavior; they are not fixture
providers. These adapters exercise deterministic protocol peers, not vendor auth.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

from scripts.testkit.adapters.clock import SystemClock
from scripts.testkit.core import ResourceScope
from scripts.testkit.waits import WaitBudget, bounded_wait
from tests.hexagonal.support.operation_observation import OperationLog, OperationObserver
from tests.hexagonal.support.work_acceptance import MeasuringProvider, accept, make_app, measure, setup_work
from xorchestrate.adapters.acp import ACPSessionDriver
from xorchestrate.adapters.bridge_host import SessionBridgeHost
from xorchestrate.adapters.claude import ClaudeSessionDriver
from xorchestrate.adapters.codex import CodexSessionDriver
from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.composition import build
from xorchestrate.model import Capability, Conflict, OrchestrationError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DRIVERS = {"codex": CodexSessionDriver, "claude": ClaudeSessionDriver,
           "grok": GrokSessionDriver, "acp": ACPSessionDriver}


def peer_options(protocol, *, fixture="progress", state=None, **overrides):
    """Select a real protocol peer and expose all options for scenario variation."""
    if fixture == "session" and protocol in {"codex", "acp"}:
        options = {"command": [sys.executable, str(FIXTURES / "session_server.py"), protocol], "timeout": 10}
        if protocol == "acp":
            options["resume_supported"] = True
    elif fixture == "progress" and protocol in {"codex", "grok", "claude"}:
        if protocol == "claude":
            options = {"command": [sys.executable, str(FIXTURES / "claude_native.py")],
                       "terminal": "discard", "startup_timeout": 5, "timeout": 3}
            if state is not None:
                options["env"] = {"CLAUDE_STATE_DIR": str(state)}
        else:
            options = {"command": [sys.executable, str(FIXTURES / "native_progress_peer.py"), protocol], "timeout": 5}
    else:
        raise ValueError("Unsupported test peer: " + protocol + "/" + fixture)
    result = {**options, **overrides}
    cache_environment = {name: os.environ[name] for name in ("PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE")
                         if name in os.environ}
    if cache_environment:
        result["env"] = {**result.get("env", {}), **cache_environment}
    return result


def native_driver(protocol, *, fixture="progress", state=None, **options):
    return DRIVERS[protocol](peer_options(protocol, fixture=fixture, state=state, **options))


def application_message(protocol, action, arguments, **fields):
    """Encode the same application request at each native protocol boundary."""
    if protocol == "claude":
        return {"application_call": {"action": action, "arguments": arguments}, **fields}
    return {"text": json.dumps({"action": "tool", "name": action, "arguments": arguments, **fields})}


def complete_json_lines(path):
    """The JSON records a fixture peer has finished appending to ``path``.

    Peers append one ``json.dumps(...) + "\\n"`` per record while the test
    polls, so a read can end inside a record still being written. Only lines
    terminated by a newline are complete; the unfinished tail is read again on
    the next poll.
    """
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    return [json.loads(line) for line in text[:text.rfind("\n") + 1].splitlines()]


def observation_budget(timeout, *, classification="harness_allowance", timeout_scale=None):
    """Resolve the explicitly selected scale for classified shared-support waits."""
    if timeout_scale is None:
        try:
            timeout_scale = float(os.environ.get("XO_TEST_TIMEOUT_SCALE", "1"))
        except ValueError as error:
            raise ValueError("XO_TEST_TIMEOUT_SCALE must be a positive finite number") from error
    return WaitBudget(timeout, classification, scale=timeout_scale)


def wait_for(predicate, timeout=5, *, message="Expected native observation did not arrive",
             clock=None, sleep=None, poll_interval=.01, classification="harness_allowance", timeout_scale=None,
             deadline=None):
    """Observe with a classified allowance; retry only explicitly safe conflicts.

    XO_TEST_TIMEOUT_SCALE applies here to harness allowances only. An explicit
    timeout_scale takes precedence. Predicates must bound their own I/O: the
    shared wait cannot preempt them and accepts a late truthy observation.
    """
    budget = observation_budget(timeout, classification=classification, timeout_scale=timeout_scale)
    if clock is None:
        clock = deadline.clock if deadline is not None else SystemClock()
    return bounded_wait(predicate, budget, clock=clock, deadline=deadline,
                        sleep=time.sleep if sleep is None else sleep, poll_interval=poll_interval, message=message,
                        retryable=lambda error: isinstance(error, Conflict) and error.details.get("retry_safe") is True)


class ControlledCallbacks:
    """Concurrent callback holds with retained per-invocation activation."""

    def __init__(self, labels=("A", "B"), *, timeout=15, argument="nonce"):
        self.entered, self.release, self.returned = {}, {}, {}
        self.calls, self.tickets = {}, {}
        self._claimed, self._claim_lock = set(), threading.Lock()
        self.finished = []
        self.timeout, self.argument = timeout, argument
        self.log = OperationLog("controlled-callback", uuid4().hex)
        self.observer = OperationObserver(self.log.snapshot)
        for label in labels:
            self.prepare(label, True)

    def prepare(self, label, held):
        if label in self.calls:
            raise ValueError("operation already prepared: " + label)
        self.entered[label], self.release[label], self.returned[label] = (
            threading.Event(), threading.Event(), threading.Event())
        if not held:
            self.release[label].set()
        self.calls[label] = (self.entered[label], self.release[label], self.returned[label])
        self.tickets[label] = self.observer.arm(self.log.source, self.log.incarnation, label, "entered")

    def activation(self, label):
        observation = self.tickets[label].poll()
        return observation.payload if observation else None

    def release_operation(self, label):
        if self.activation(label) is None:
            raise AssertionError("cannot release callback before its activation")
        self.release[label].set()

    def discover(self):
        return [Capability("hold", "Controlled callback", {"type": "object"})]

    def invoke(self, name, arguments, context):
        label = arguments[self.argument]
        with self._claim_lock:
            if label not in self.calls or label in self._claimed:
                raise ValueError("operation is unknown or already invoked: " + label)
            self._claimed.add(label)
        self.log.record(label, "entered", {"source": self.log.source, "incarnation": self.log.incarnation,
                                          "operation": label, "stage": "callback", "invocation": uuid4().hex})
        self.entered[label].set()
        assert self.release[label].wait(self.timeout), "test must release its callbacks"
        self.finished.append(label)
        self.log.record(label, "returned")
        self.returned[label].set()
        return {"released": label}

    def close(self):
        for event in self.release.values():
            event.set()


@contextmanager
def native_drivers():
    """Own each created adapter immediately, draining all shutdown callbacks."""
    resources = ResourceScope()

    def create(protocol, **options):
        driver = native_driver(protocol, **options)
        resources.defer(protocol + "_driver", driver.close)
        return driver

    try:
        yield create
    finally:
        errors = resources.close()
        if errors:
            raise AssertionError("native fixture cleanup failed: " + repr(errors))


def retry(app, action, args):
    for attempt in range(8):
        try:
            return app.call(action, args)
        except Conflict as error:
            if not error.details.get("retry_safe") or attempt == 7:
                raise


@contextmanager
def native_runtime(protocol, tmp_path, bridge=False):
    wire = tmp_path / "wire.jsonl"
    options = peer_options(protocol, timeout=5, application_tools=True,
        env={"PEER_RESULTS": str(wire), "CLAUDE_STATE_DIR": str(tmp_path / "claude")})
    if protocol == "claude":
        options["startup_timeout"] = 10
    if protocol == "grok":
        options["resume_supported"] = True
    config = {"store": {"adapter": "sqlite", "options": {"path": str(tmp_path / "state.sqlite")}},
              "guardrail": {"adapter": "rules", "options": {"rules": [{"actions": ["get_acceptance", "create_work"],
                  "roles": ["revoked"], "effect": "deny"}]}},
              "sessions": {"native": {"adapter": protocol, "options": options}}}
    with ExitStack() as stack:
        if bridge:
            native = DRIVERS[protocol](
                {key: value for key, value in options.items() if key != "application_tools"})
            host = SessionBridgeHost(native, tmp_path / "host", capabilities=make_app().describe(), options={"transport": "fifo"})
            stack.callback(host.close)
            config["sessions"]["native"] = {"adapter": "bridge", "options": {"endpoint": str(host.endpoint),
                "transport": "fifo", "timeout": 5, "application_tools": True}}
        runtime = stack.enter_context(build(config, Path.cwd()))
        app = runtime.application
        app.providers["checks"] = MeasuringProvider()
        actor = app.call("add_actor", {"actor_id": "owner", "roles": ["allowed"]})["actor"]
        work, basis = setup_work(app, {"A": "A"})
        measure(app, work)
        assert accept(app, work)["accepted"]
        session = retry(app, "start_session", {"driver": "native", "specification": {}, "work_id": work["id"], "idempotency_key": "start"})["session"]
        adapter = app.sessions["native"]
        rejected = []
        replay = getattr(adapter, "_application_replay", None)

        def checked(*args):
            try:
                return replay(*args)
            except OrchestrationError as error:
                rejected.append(error.code)
                raise
        if replay is not None:
            adapter._application_replay = checked

        def send(label, action="get_acceptance", arguments=None, fresh=False):
            arguments = {"work_id": work["id"]} if arguments is None else arguments
            body = {"replay_fixture": {"label": label, "call_id": "fresh" if fresh else "fixed",
                                      "mcp_request_id": "fresh" if fresh else "fixed"}}
            message = application_message(protocol, action, arguments, **body)
            retry(app, "send_session", {"session_id": session["id"], "message": message, "idempotency_key": label})
            def observed():
                records = complete_json_lines(wire)
                matches = [row for row in records if row["label"] == label]
                return matches[-1] if matches else None
            row = wait_for(observed, timeout=8, message="native replay fixture did not return a wire result")
            result = row["result"]
            if protocol == "codex":
                return json.loads(result["contentItems"][0]["text"])
            if "error" in result:
                return result
            if protocol == "grok":
                result = result["result"]
            return result["structuredContent"]
        yield app, adapter, work, basis, actor, send, rejected

