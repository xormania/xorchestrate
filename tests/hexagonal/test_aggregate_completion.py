"""IV02: aggregate completion survives order changes and independent evidence vetoes."""
import json
import sys
import threading
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
from itertools import permutations

import pytest

from scripts.verification.checker import VerificationFailure, check_case, completed_native_turns, verify
from scripts.verification.multi_harness import snapshot
from scripts.verification.normalize import normalize
from tests.hexagonal.support.completion_evidence import completion_input
from tests.hexagonal.support.native import (
    ControlledCallbacks as Holds,
)
from tests.hexagonal.support.native import (
    application_message,
    native_driver,
    native_runtime,
    retry,
    wait_for,
)
from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.adapters.sessions import ACPSessionDriver, CodexSessionDriver, _LiveSession
from xorchestrate.delivery import view
from xorchestrate.model import Capability

wait = partial(wait_for, timeout=8, message='controlled native schedule timed out')


def assert_active(status):
    assert status["activity"] == "active", "IV02_PENDING_WORK_IS_ACTIVE"


def composed_overlap(protocol, order, tmp_path, force_idle=False):
    holds = Holds(order)
    rows = []
    with native_runtime(protocol, tmp_path) as (app, adapter, work, basis, actor, send, rejected):
        app.providers["holds"] = holds
        with app.store.transaction() as tx:
            session = view(tx.list("session")[0])
        peers = {protocol: {"session_id": session["id"], "native_id": session["backend"]["native_id"],
                            "incarnation": session["backend"]["incarnation"]}}
        case = {"id": "completion-" + protocol, "kind": "completion", "peer": protocol}
        if force_idle:
            original = adapter._notification

            def faulty(live, method, params):
                original(live, method, params)
                if method == "turn/completed":
                    with live.lock:
                        live.activity = "idle"
                        adapter._observation(live, "activity", state="idle")
            adapter._notification = faulty

        def measured(label, remaining):
            raw = snapshot(app)
            status = adapter.status(session["backend"])
            run = {"raw_records": raw, "events": normalize(raw, peers, [], "protocol-fixture"), "sessions": peers}
            try:
                verdict = {"status": "passed", "evidence": check_case(case, run, {})}
            except VerificationFailure as error:
                verdict = {"status": "failed", "assertion": error.assertion}
            row = {"raw_records": raw, "sessions": peers, "status": status, "verdict": verdict,
                   "remaining": remaining, "finished": holds.finished[:], "case": case}
            (tmp_path / (label + ".json")).write_text(json.dumps(row, indent=2))
            rows.append(row)
            return row

        try:
            for label in sorted(order):
                args = {"capability": "holds/hold", "arguments": {"nonce": label}}
                message = application_message(protocol, "invoke", args)
                retry(app, "send_session", {"session_id": session["id"], "message": message, "idempotency_key": label})
                assert holds.entered[label].wait(5)
            initial = measured("before", list(order))
            assert_active(initial["status"])
            assert initial["verdict"]["status"] == "failed"
            for index, label in enumerate(order):
                old = len([r for r in snapshot(app)["observations"] if r["observation"].get("kind") in {"turn_completed", "native_completion"}])
                holds.release[label].set()
                wait(lambda: len([r for r in snapshot(app)["observations"] if r["observation"].get("kind") in {"turn_completed", "native_completion"}]) > old)
                remaining = list(order[index + 1:])
                if not remaining:
                    wait(lambda: adapter.status(session["backend"])["activity"] == "idle")
                row = measured("after-" + label, remaining)
                assert sorted(holds.finished) == sorted(order[:index + 1])
                assert all(not holds.release[item].is_set() for item in remaining)
                if remaining:
                    assert row["verdict"]["status"] == "failed", "IV02_PENDING_CALLBACK_CHECKER_REJECTS"
                else:
                    assert row["verdict"]["status"] == "passed", row["verdict"]
        finally:
            holds.close()
    # Preserve the final legitimate control before testing the faulty subject.
    for row in rows[:-1]:
        if force_idle and row is not rows[0]:
            assert row["status"]["activity"] == "idle", "fault must activate"
            assert row["verdict"]["assertion"] == "completion_callback_unresolved"
        assert_active(row["status"])
    return rows


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
@pytest.mark.parametrize("order", list(permutations("ABC")))
def test_three_native_callbacks_finish_in_every_order(protocol, order, tmp_path):
    composed_overlap(protocol, order, tmp_path)


def test_known_bad_per_turn_idle_is_detected_by_product_and_independent_checker(tmp_path):
    with pytest.raises(AssertionError, match="^IV02_PENDING_WORK_IS_ACTIVE"):
        composed_overlap("codex", "BA", tmp_path, force_idle=True)


def test_unrelated_callback_failure_is_not_pending_activity_detection():
    with pytest.raises(RuntimeError, match="unrelated observation failure"):
        def unrelated():
            raise RuntimeError("unrelated observation failure")
        assert_active(unrelated())


@pytest.mark.parametrize("bad_id", [None, "", 1, "unobserved"])
@pytest.mark.parametrize("protocol", ["codex", "grok"])
def test_unmatched_completion_never_retires_known_turns_and_preserves_uncertainty(bad_id, protocol):
    adapter = {"codex": CodexSessionDriver, "grok": GrokSessionDriver}[protocol]({"command": [sys.executable, "unused"]})
    live = _LiveSession({}, native_id="native")
    if protocol == "grok":
        live.grok = {"current": None, "native_busy": False, "active_prompts": set(), "seen": [], "completed": [], "interjections": {}}
    def notify(method, ident):
        if protocol == "codex":
            adapter._notification(live, method, {"threadId": "native", "turn": {"id": ident}})
        elif method == "turn/started":
            adapter._notification(live, "session/update", {"sessionId": "native", "promptId": ident,
                "update": {"sessionUpdate": "agent_message_chunk"}})
        else:
            adapter._notification(live, "_x.ai/session/prompt_complete", {"sessionId": "native", "promptId": ident})
    def pending():
        return set(live.pending) if protocol == "codex" else live.grok["active_prompts"]
    for label in "ABC":
        notify("turn/started", label)
    notify("turn/completed", "B")
    notify("turn/completed", "B")
    notify("turn/started", "B")
    assert pending() == {"A", "C"} and live.activity == "active"
    notify("turn/completed", bad_id)
    assert pending() == {"A", "C"} and live.activity == "active"
    notify("turn/completed", "C")
    notify("turn/completed", "A")
    assert live.activity == "unknown"
    adapter.close()


@pytest.mark.parametrize("completed_id,expected", [("B", "idle"), ("unrelated", "unknown")])
def test_completion_before_submission_response_must_bind_to_returned_turn(completed_id, expected):
    adapter = CodexSessionDriver({"command": [sys.executable, "unused"]})
    live = _LiveSession({}, native_id="native")
    observations = []
    adapter.bind_observer(lambda context, event: observations.append(event))
    class Process:
        def request(self, method, parameters):
            adapter._notification(live, "turn/completed", {"threadId": "native", "turn": {"id": completed_id}})
            assert live.activity == "active", "submission is still awaiting its response"
            return {"turn": {"id": "B"}}
    live.process = Process()
    result = adapter._submit(live, {})
    adapter._observation(live, "native_submission", result=result)
    if completed_id != "B":
        adapter._notification(live, "turn/completed", {"threadId": "native", "turn": {"id": "B"}})
    assert not live.pending and not live.submissions and live.activity == expected
    peers = {"codex": {"session_id": "app", "native_id": live.native_id, "incarnation": live.incarnation}}
    raw = {"observations": [{"id": str(i), "session_id": "app", "origin": "adapter", "stale": False,
        "created_at": event["at"], "observation": event} for i, event in enumerate(observations)]}
    latest = {"at": observations[-1]["at"]}
    if expected == "idle":
        completed_native_turns(raw, peers, "codex", latest)
    else:
        with pytest.raises(VerificationFailure, match="^completion_turn_unbound$"):
            completed_native_turns(raw, peers, "codex", latest)
    adapter.close()


@pytest.mark.parametrize("protocol", ["codex", "acp", "grok"])
@pytest.mark.parametrize("failure", [False, True])
def test_native_completion_before_callback_return_keeps_running_callback_visible(protocol, failure):
    entered, release = threading.Event(), threading.Event()
    def handler(*args):
        entered.set()
        assert release.wait(5)
        if failure:
            raise RuntimeError("unrelated callback failure")
        return {"finished": True}
    adapter = {"codex": CodexSessionDriver, "acp": ACPSessionDriver, "grok": GrokSessionDriver}[protocol](
        {"command": [sys.executable, "unused"]}, handler)
    live = _LiveSession({}, native_id="native")
    if protocol in {"codex", "grok"}:
        adapter.bind_application_tools([Capability("hold", "Hold")], handler)
    if protocol == "grok":
        live.grok = {"current": "A", "native_busy": True, "active_prompts": {"A"}, "seen": ["A"], "completed": [],
                     "interjections": {}, "mcp_requests": {}, "condition": threading.Condition(live.lock),
                     "calls": {"call": {"action": "hold", "arguments": {}, "used": False}}}
    errors = []
    def invoke():
        try:
            if protocol == "grok":
                adapter._gateway(live, "tools/call", {"name": "hold", "arguments": {},
                    "mcp_connection_id": "connection", "mcp_request_id": "request"})
            elif protocol == "codex":
                adapter._request(live, "item/tool/call", {"threadId": "native", "callId": "call", "tool": "hold", "arguments": {}})
            else:
                adapter._request(live, "custom/callback", {"sessionId": "native"})
        except RuntimeError as error:
            errors.append(str(error))
    thread = threading.Thread(target=invoke)
    try:
        if protocol == "codex":
            adapter._notification(live, "turn/started", {"threadId": "native", "turn": {"id": "A"}})
        elif protocol == "acp":
            live.pending["A"] = None
        thread.start()
        assert entered.wait(3)
        if protocol == "codex":
            adapter._notification(live, "turn/completed", {"threadId": "native", "turn": {"id": "A"}})
        elif protocol == "acp":
            with live.lock:
                live.pending.pop("A")
                adapter._prompt_finished(live, "A", True)
        else:
            adapter._notification(live, "_x.ai/session/prompt_complete", {"sessionId": "native", "promptId": "A"})
        assert live.activity == "active" and live.active_callbacks
    finally:
        release.set()
        thread.join(5)
        adapter.close()
    assert not thread.is_alive() and not live.active_callbacks
    assert live.activity == ("unknown" if failure else "idle")
    assert errors == (["unrelated callback failure"] if failure else [])


def test_checker_independently_rejects_pending_operation_despite_false_terminal_and_idle(tmp_path):
    rows = composed_overlap("codex", "BA", tmp_path)
    final = deepcopy(rows[-1])
    peers, raw, case = final["sessions"], final["raw_records"], final["case"]
    def check():
        return check_case(case, {"raw_records": raw, "events": normalize(raw, peers, [], "protocol-fixture"), "sessions": peers}, {})
    assert check()
    operation = next(item for item in raw["operations"] if item.get("action") == "invoke" and item["arguments"].get("arguments", {}).get("nonce") == "A")
    operation["status"] = "pending"
    with pytest.raises(VerificationFailure, match="^completion_operation_unresolved$"):
        check()
    (tmp_path / "pending-operation.json").write_text(json.dumps(final, indent=2))
    operation["status"] = "unknown"
    with pytest.raises(VerificationFailure, match="^completion_operation_unresolved$"):
        check()
    (tmp_path / "unknown-operation.json").write_text(json.dumps(final, indent=2))
    operation["status"] = "completed"
    assert check()
    unrelated = {**deepcopy(operation), "id": "unrelated-operation", "idempotency_key": "other", "status": "pending"}
    raw["operations"].append(unrelated)
    assert check(), "unrelated work must not block this native completion"
    (tmp_path / "unrelated-operation-control.json").write_text(json.dumps(final, indent=2))
    native = next(item for item in raw["native_calls"] if item["arguments"].get("arguments", {}).get("nonce") == "A")
    native["status"] = "unknown"
    with pytest.raises(VerificationFailure, match="^completion_native_call_unresolved$"):
        check()
    (tmp_path / "corrupt-operation-and-native-call.json").write_text(json.dumps(final, indent=2))


@pytest.mark.parametrize("protocol", ["codex", "grok"])
@pytest.mark.parametrize("order", list(permutations("ABC")))
def test_finished_callbacks_do_not_hide_unfinished_native_turns(protocol, order, tmp_path):
    driver = native_driver(protocol)
    observations, called = [], []
    def observe(context, event):
        observations.append(event)
    def callback(action, arguments, context, key):
        for phase in ("requested", "completed"):
            observe(context, {"kind": "application_tool", "phase": phase, "action": action,
                "arguments": arguments, "call_id": context["call_id"], "native_id": context["native_id"],
                "incarnation": context["incarnation"], "at": datetime.now(timezone.utc).isoformat()})
        called.append(arguments["attempt_id"])
        return {"ok": True}
    driver.bind_observer(observe)
    driver.bind_application_tools([Capability("echo", "Echo")], callback)
    try:
        session = driver.start({"context": {"session_id": "app", "actor": "owner"}})
        peers = {protocol: {"session_id": "app", "native_id": session["native_id"], "incarnation": session["incarnation"]}}
        case = {"kind": "completion", "peer": protocol}
        def measure(label, remaining):
            with driver._get(session).lock:
                captured = deepcopy(observations)
                status = driver.status(session)
            raw = {"observations": [{"id": str(i), "session_id": "app", "origin": "adapter", "stale": False,
                    "created_at": event["at"], "observation": event} for i, event in enumerate(captured)],
                   "messages": [], "attempts": [], "evidence": []}
            events = normalize(raw, peers, [], "protocol-fixture")
            run = {"raw_records": raw, "events": events, "sessions": peers}
            if remaining:
                assert_active(status)
                with pytest.raises(VerificationFailure):
                    check_case(case, run, {})
                # Sustained false producer state: retain all actual starts/ends
                # and successful callbacks, append a plausible native idle.
                bad = deepcopy(raw)
                at = datetime.now(timezone.utc).isoformat()
                bad["observations"].append({"id": "false-idle", "session_id": "app", "origin": "adapter", "stale": False,
                    "created_at": at, "observation": {**peers[protocol], "kind": "activity", "source": protocol, "at": at, "state": "idle"}})
                with pytest.raises(VerificationFailure, match="^completion_native_turn_unresolved$"):
                    check_case(case, {"raw_records": bad, "events": normalize(bad, peers, [], "protocol-fixture"), "sessions": peers}, {})
                (tmp_path / (label + "-false-idle.json")).write_text(json.dumps(bad, indent=2))
            else:
                assert status["activity"] == "idle"
                assert check_case(case, run, {})
            (tmp_path / (label + ".json")).write_text(json.dumps({"raw_records": raw, "status": status, "held_tails": remaining}, indent=2))
        for label in "ABC":
            driver.deliver(session, {"attempt_id": label, "body": {"action": "tool", "name": "echo",
                "arguments": {"attempt_id": label}, "tail_gate": str(tmp_path / (label + ".release")),
                "tail_entered": str(tmp_path / (label + ".entered"))}})
            wait(lambda: (tmp_path / (label + ".entered")).exists())
        assert sorted(called) == list("ABC") and not driver._get(session).active_callbacks
        measure("before", list(order))
        for index, label in enumerate(order):
            old = len([event for event in observations if event["kind"] == "native_completion"])
            (tmp_path / (label + ".release")).touch()
            wait(lambda: len([event for event in observations if event["kind"] == "native_completion"]) > old)
            if protocol == "grok" and label == "A":
                wait(lambda: not driver._get(session).pending)
            if index == len(order) - 1:
                wait(lambda: driver.status(session)["activity"] == "idle")
            measure("after-" + label, list(order[index + 1:]))
    finally:
        for label in "ABC":
            (tmp_path / (label + ".release")).touch()
        driver.close()


@pytest.mark.parametrize("peer", ["codex", "claude", "grok"])
def test_unbound_native_completion_cannot_borrow_forged_idle(peer):
    inputs, add = completion_input(peer)
    add("native_completion", activity="idle")
    assert verify(*inputs)["status"] == "passed"
    if peer == "claude":
        add("turn_completed", turn_id="unobserved", activity="idle")
    else:
        add("native_completion", activity="idle", **{"turn_id" if peer == "codex" else "prompt_id": "unobserved"})
    report = verify(*inputs)
    assert report["status"] == "incomplete"
    assert report["cases"][0]["assertion"] == "completion_turn_unbound"


@pytest.mark.parametrize("starts,ends,expected", [(1, 1, "passed"), (2, 2, "incomplete"), (1, 2, "incomplete")])
def test_single_anonymous_claude_turn_control_and_ambiguous_overlap(starts, ends, expected):
    inputs, add = completion_input("claude")
    for _ in range(starts):
        add("turn_started")
    for _ in range(ends):
        add("turn_completed", activity="idle")
    report = verify(*inputs)
    assert report["status"] == expected
    if expected == "incomplete":
        assert report["cases"][0]["assertion"] == "completion_native_turn_unresolved"


@pytest.mark.parametrize("peer", ["codex", "grok"])
def test_submission_bound_completion_must_precede_idle_even_without_start(peer):
    inputs, add = completion_input(peer)
    if peer == "codex":
        add("native_submission", result={"turn": {"id": "C"}})
    else:
        add("native_submission", prompt_id="C", result={})
    add("activity", state="idle")
    if peer == "codex":
        add("native_notification", method="turn/completed", payload={"turn": {"id": "C"}})
    else:
        add("native_notification", method="_x.ai/session/prompt_complete", payload={"promptId": "C"})
    report = verify(*inputs)
    assert report["status"] == "incomplete"
    assert report["cases"][0]["assertion"] == "completion_before_turn_finished"
    add("activity", state="idle")
    assert verify(*inputs)["status"] == "passed"
