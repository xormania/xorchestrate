"""IV02: aggregate completion survives order changes and independent evidence vetoes."""
import json
import sys
import threading
import time
from copy import deepcopy
from itertools import permutations

import pytest

from scripts.verification.checker import VerificationFailure, check_case
from scripts.verification.multi_harness import snapshot
from scripts.verification.normalize import normalize
from tests.hexagonal.test_native_cache_currentness import native_runtime, retry
from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.adapters.sessions import ACPSessionDriver, CodexSessionDriver, _LiveSession
from xorchestrate.delivery import view
from xorchestrate.model import Capability


def wait(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("controlled native schedule timed out")


class Holds:
    def __init__(self, labels):
        self.entered = {label: threading.Event() for label in labels}
        self.release = {label: threading.Event() for label in labels}
        self.finished = []

    def discover(self):
        return [Capability("hold", "Controlled callback", {"type": "object"})]

    def invoke(self, name, args, context):
        label = args["nonce"]
        self.entered[label].set()
        assert self.release[label].wait(15), "test must release its callbacks"
        self.finished.append(label)
        return {"released": label}

    def close(self):
        for event in self.release.values():
            event.set()


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
                message = ({"application_call": {"action": "invoke", "arguments": args}} if protocol == "claude"
                           else {"text": json.dumps({"action": "tool", "name": "invoke", "arguments": args})})
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
def test_unmatched_completion_never_retires_known_turns_and_preserves_uncertainty(bad_id):
    adapter = CodexSessionDriver({"command": [sys.executable, "unused"]})
    live = _LiveSession({}, native_id="native")
    def notify(method, ident):
        adapter._notification(live, method, {"threadId": "native", "turn": {"id": ident}})
    for label in "ABC":
        notify("turn/started", label)
    notify("turn/completed", "B")
    notify("turn/completed", "B")
    notify("turn/started", "B")
    assert set(live.pending) == {"A", "C"} and live.activity == "active"
    notify("turn/completed", bad_id)
    assert set(live.pending) == {"A", "C"} and live.activity == "active"
    notify("turn/completed", "C")
    notify("turn/completed", "A")
    assert live.activity == "unknown"
    adapter.close()


@pytest.mark.parametrize("completed_id,expected", [("B", "idle"), ("unrelated", "unknown")])
def test_completion_before_submission_response_must_bind_to_returned_turn(completed_id, expected):
    adapter = CodexSessionDriver({"command": [sys.executable, "unused"]})
    live = _LiveSession({}, native_id="native")
    class Process:
        def request(self, method, parameters):
            adapter._notification(live, "turn/completed", {"threadId": "native", "turn": {"id": completed_id}})
            assert live.activity == "active", "submission is still awaiting its response"
            return {"turn": {"id": "B"}}
    live.process = Process()
    adapter._submit(live, {})
    if completed_id != "B":
        adapter._notification(live, "turn/completed", {"threadId": "native", "turn": {"id": "B"}})
    assert not live.pending and not live.submissions and live.activity == expected
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
        live.grok = {"current": "A", "native_busy": True, "seen": ["A"], "completed": [],
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
