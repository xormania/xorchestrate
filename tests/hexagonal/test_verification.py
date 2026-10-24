"""Checker calibration tests; synthetic evidence is explicitly not a native run."""
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.verification.__main__ import calibrate
from scripts.verification.calibration import classify
from scripts.verification.capture import (
    candidate_identity,
    canonical,
    capture_candidate,
    digest,
    run_command,
    verify_candidate,
)
from scripts.verification.checker import VerificationFailure, exchange_evidence, native_inventory, tool_audit, verify
from xorchestrate.exchange import evaluate_exchange


def repository(path):
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "fixture"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "fixture@example.invalid"], check=True)
    (path / "module.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    return path


def proof_chain():
    sessions = {p: {"native_id": p + "-native", "session_id": p + "-app", "incarnation": p + "-epoch"}
                for p in ("codex", "grok")}
    case = {"id": "case", "source": "codex", "target": "grok", "kind": "exchange"}
    events = []
    def add(event, peer, **data):
        value = {"seq": len(events) + 1, "event": event, "peer": peer, **sessions[peer], **data}
        events.append(value)
        return value
    def callback(peer, tool, args, event, **data):
        call_id = "call-" + str(len(events))
        add("native_tool", peer, tool=tool, arguments=args, call_id=call_id,
            origin={"codex": "codex/item/tool/call", "grok": "grok/session/update"}[peer])
        add("tool_called", peer, tool=tool, arguments=args, call_id=call_id)
        add(event, peer, tool=tool, arguments=args, call_id=call_id, **data)
    challenge = {"id": "challenge", "to": "grok", "kind": "challenge", "case_id": "case", "nonce": "nonce", "memory": ""}
    callback("codex", "send_message", challenge, "queued", message=challenge)
    add("attempt", "grok", message_id="challenge", attempt_id="attempt-1")
    add("submitted", "grok", message_id="challenge", attempt_id="attempt-1", state="submitted")
    reply = {"id": "reply", "to": "codex", "kind": "reply", "case_id": "case", "nonce": "nonce", "memory": "private"}
    callback("grok", "send_message", reply, "queued", message=reply)
    add("attempt", "codex", message_id="reply", attempt_id="attempt-2")
    add("submitted", "codex", message_id="reply", attempt_id="attempt-2", state="submitted")
    receipt = {"phase": "received", "case_id": "case", "nonce": "nonce", "memory": "private", "peer": "grok"}
    callback("codex", "record_result", receipt, "report", report=receipt)
    return events, sessions, case


def test_candidate_capture_includes_dirty_and_untracked_inputs(tmp_path):
    root = repository(tmp_path / "repo")
    baseline = candidate_identity(root)
    (root / "module.py").write_text("VALUE = 2\n")
    (root / "new.py").write_text("UNTRACKED = True\n")
    current = capture_candidate(root, tmp_path / "snapshot")
    assert current["commit"] == baseline["commit"]
    assert current["content_sha256"] != baseline["content_sha256"]
    assert (tmp_path / "snapshot/content/new.py").read_text() == "UNTRACKED = True\n"
    assert verify_candidate(root, current)["passed"]
    (root / "new.py").write_text("UNTRACKED = False\n")
    assert verify_candidate(root, current)["changed_paths"] == ["new.py"]
    with pytest.raises(ValueError, match="outside"):
        capture_candidate(root, root / "run")


def test_command_capture_retains_setup_errors_as_setup_errors(tmp_path):
    result = run_command(["/certainly/missing"], tmp_path, tmp_path / "run")
    assert result["status"] == "setup_error"
    assert result["exit_status"] is None
    assert (tmp_path / "run/stderr.log").read_text()


def test_complete_exchange_control_has_correlated_native_and_durable_evidence():
    events, sessions, case = proof_chain()
    result = exchange_evidence(events, case, "nonce", "private", sessions)
    assert result["attempt_ids"] == ["attempt-1", "attempt-2"]
    assert tool_audit(events, sessions) == {"audited_calls": 3}


@pytest.mark.parametrize(("fault", "expected"), [
    ("queue_only", "native_submission_missing_or_misbound"),
    ("rpc_only", "retained_context_reply_missing"),
    ("wrong_memory", "retained_context_reply_missing"),
    ("stale_nonce", "retained_context_reply_missing"),
    ("missing_observation", "native_call_missing_or_misbound"),
    ("session_drift", "native_call_missing_or_misbound"),
    ("conflicting_call_id", "native_call_missing_or_misbound"),
    ("discovery_only", "native_call_missing_or_misbound"),
    ("wrong_dispatch", "native_call_missing_or_misbound"),
    ("wrong_attempt", "delivery_attempt_missing_or_misbound"),
    ("stale_incarnation", "native_submission_missing_or_misbound"),
    ("missing_receipt", "sender_receipt_missing"),
])
def test_exchange_faults_fail_the_intended_assertion(fault, expected):
    events, sessions, case = proof_chain()
    exchange_evidence(events, case, "nonce", "private", sessions)
    if fault == "queue_only":
        events = [e for e in events if e["event"] != "submitted"]
    elif fault == "rpc_only":
        events = [e for e in events if not (e["event"] == "queued" and e["peer"] == "grok")]
    elif fault in {"wrong_memory", "stale_nonce"}:
        next(e for e in events if e["event"] == "queued" and e["peer"] == "grok")["message"][
            "memory" if fault == "wrong_memory" else "nonce"] = "wrong"
    elif fault == "missing_receipt":
        events = [e for e in events if e["event"] != "report"]
    elif fault in {"missing_observation", "discovery_only", "wrong_dispatch", "session_drift"}:
        native = next(e for e in events if e["event"] == "native_tool")
        if fault == "missing_observation":
            events.remove(native)
        elif fault == "discovery_only":
            native["event"] = "native_discovery"
        elif fault == "wrong_dispatch":
            native["tool"] = "other_tool"
        else:
            native["native_id"] = "another-native-session"
    elif fault == "conflicting_call_id":
        other = deepcopy(next(e for e in events if e["event"] == "native_tool"))
        other["arguments"] = {"wrong": True}
        events.append(other)
    elif fault == "wrong_attempt":
        next(e for e in events if e["event"] == "attempt")["attempt_id"] = "wrong"
    elif fault == "stale_incarnation":
        next(e for e in events if e["event"] == "submitted")["incarnation"] = "old"
    with pytest.raises(VerificationFailure, match=expected):
        exchange_evidence(events, case, "nonce", "private", sessions)


def test_requested_coverage_and_exact_bindings_override_fabricated_summary():
    events, sessions, case = proof_chain()
    criteria = {"required_cases": [case, {"id": "missing", "kind": "audit"}]}
    fixture = {"batches": []}
    bindings = {"candidate_sha256": "candidate", "checker_sha256": "checker",
                "criteria_sha256": digest(canonical(criteria)), "fixture_sha256": digest(canonical(fixture))}
    run = {"status": "passed", "mode": "fixture", "bindings": bindings, "events": events,
           "sessions": sessions, "nonces": {"case": "nonce"}, "memories": {"grok": "private"},
           "discovered_cases": ["case"], "executed_cases": ["case"]}
    result = verify(run, criteria, bindings, fixture)
    assert result["status"] == "incomplete"
    assert result["cases"][1]["status"] == "not_exercised"
    assert result["native_acceptance"] == "pending_local_run"
    with pytest.raises(VerificationFailure, match="measurement_binding_mismatch"):
        verify(run, criteria, {**bindings, "candidate_sha256": "another-revision"}, fixture)


def test_native_inventory_preserves_complete_requested_counts():
    inventory = native_inventory()
    from collections import Counter
    assert Counter(case["kind"] for case in inventory) == {
        "startup": 3, "exchange": 6, "busy": 3, "work_exchange": 6,
        "work": 9, "completion": 3, "lifecycle": 15, "audit": 1}


@pytest.mark.parametrize(("status", "diagnostic", "exit_status", "expected"), [
    ("failed", "intended_assertion", 1, "detected"),
    ("failed", "unrelated_timeout", 1, "unrelated_failure"),
    ("failed", "intended_assertion", 2, "unrelated_failure"),
    ("error", "ModuleNotFoundError", 1, "setup_error"),
    ("skipped", "missing dependency", 0, "skipped"),
    ("passed", "", 0, "escaped_fault"),
])
def test_calibrator_distinguishes_target_detection_from_environment_failure(status, diagnostic, exit_status, expected):
    control = {"target": {"status": "passed"}, "control": {"status": "passed"}}
    mutant = {"target": {"status": status, "diagnostic": diagnostic}, "control": {"status": "passed"}}
    assert classify(control, mutant, "target", "intended_assertion", ["control"], mutant_exit=exit_status)["classification"] == expected


def test_calibration_runs_isolated_real_control_and_mutation(tmp_path):
    root = repository(tmp_path / "repo")
    (root / "test_product.py").write_text(
        'from module import VALUE\n'
        'def test_target():\n    assert VALUE == 1, "retained_selection"\n'
        'def test_control():\n    assert 2 + 2 == 4\n')
    result = calibrate(root, tmp_path / "evidence", {"path": "module.py", "old": "VALUE = 1", "new": "VALUE = 2"},
                       ["test_product.py"], "test_product::test_target", "retained_selection", ["test_product::test_control"])
    assert result["classification"] == "detected"
    assert (root / "module.py").read_text() == "VALUE = 1\n"
    assert (tmp_path / "evidence/mutant-run/stdout.log").exists()


def durable_exchange():
    expectation = {"run_id": "run", "case_id": "case", "nonce": "nonce", "memory": "secret",
                   "sender": "codex", "recipient": "grok"}
    common = {k: expectation[k] for k in ("run_id", "case_id", "nonce")}
    challenge = {"id": "m1", "sender": "codex", "recipients": ["grok"], "work_id": "w",
                 "body": {"verification": {**common, "kind": "challenge", "memory": ""}}, "created_at": "1"}
    reply = {"id": "m2", "sender": "grok", "recipients": ["codex"], "work_id": "w",
             "body": {"verification": {**common, "kind": "reply", "memory": "secret"}}, "created_at": "2"}
    evidence = {"id": "e1", "work_id": "w", "source": {"type": "caller", "actor": "codex"}, "created_at": "3",
                "result": {"verification": {**common, "phase": "received", "memory": "secret", "peer": "grok"}}}
    sessions = [{"id": peer + "-app", "actor_id": peer, "backend": {"native_id": peer + "-native", "incarnation": peer + "-epoch"}}
                for peer in ("codex", "grok")]
    observations = []
    for index, (peer, action, args, result) in enumerate([
        ("codex", "send_message", {"work_id": "w", "body": challenge["body"]}, {"message": challenge}),
        ("grok", "send_message", {"work_id": "w", "body": reply["body"]}, {"message": reply}),
        ("codex", "record_result", {"work_id": "w", "result": evidence["result"]}, {"evidence": evidence}),
    ]):
        observations.append({"id": f"obs-{index}-native", "session_id": peer + "-app", "origin": "adapter", "stale": False,
            "observation": {"native_id": peer + "-native", "incarnation": peer + "-epoch", "call_id": str(index),
                            "action": action, "arguments": args, "kind": "native_tool_call", "source": peer}})
        for phase in ("requested", "completed"):
            observations.append({"id": f"obs-{index}-{phase}", "session_id": peer + "-app", "origin": "adapter", "stale": False,
                "observation": {"native_id": peer + "-native", "incarnation": peer + "-epoch", "call_id": str(index),
                                "action": action, "arguments": args, "phase": phase, **({"result": result} if phase == "completed" else {})}})
    attempts = [{"id": "a" + str(i), "message_id": message["id"], "recipient": peer, "session_id": peer + "-app",
                 "incarnation": peer + "-epoch", "native_id": peer + "-native", "status": "submitted", "origin": "adapter",
                 "submission": {"submitted": True}, "sequence": 1}
                for i, (message, peer) in enumerate(((challenge, "grok"), (reply, "codex")))]
    return {"message": challenge, "messages": [challenge, reply], "evidence": [evidence], "sessions": sessions,
            "observations": observations, "attempts": attempts[:1], "all_attempts": attempts}, expectation


def test_production_receipt_requires_durable_native_callback_chain():
    snapshot, expectation = durable_exchange()
    result = evaluate_exchange(snapshot, "grok", expectation)
    assert result["verified"], result
    assert result["claims"]["turn_completed"] == "not_measured"
    assert result["claims"]["task_accepted"] == "not_measured"


@pytest.mark.parametrize(("fault", "diagnostic"), [
    ("caller_proof", "native_callback_missing_or_misbound"),
    ("queue_only", "native_submission_missing"),
    ("wrong_memory", "retained_context_reply_missing_or_duplicate"),
    ("missing_receipt", "sender_receipt_missing_or_duplicate"),
    ("wrong_record_id", "native_callback_missing_or_misbound"),
    ("stale_observation", "native_callback_missing_or_misbound"),
    ("stale_attempt", "stale_delivery_incarnation"),
    ("request_missing", "native_request_observation_missing"),
    ("native_missing", "independent_native_observation_missing"),
    ("call_conflict", "native_call_id_conflict"),
    ("uncertain_retry", "native_submission_missing"),
])
def test_production_receipt_faults_have_specific_diagnostics(fault, diagnostic):
    snapshot, expectation = durable_exchange()
    assert evaluate_exchange(snapshot, "grok", expectation)["verified"]
    if fault == "caller_proof":
        for observation in snapshot["observations"]:
            observation["origin"] = "caller"
    elif fault == "queue_only":
        snapshot["all_attempts"][0]["status"] = "queued"
    elif fault == "wrong_memory":
        snapshot["messages"][1]["body"]["verification"]["memory"] = "wrong"
    elif fault == "missing_receipt":
        snapshot["evidence"] = []
    elif fault == "wrong_record_id":
        # Detach the callback result so mutating the response cannot also edit
        # the durable message under test.
        snapshot["observations"][2]["observation"]["result"] = {"message": {"id": "other"}}
    elif fault == "stale_observation":
        snapshot["observations"][2]["stale"] = True
    elif fault == "stale_attempt":
        snapshot["all_attempts"][0]["incarnation"] = "old"
    elif fault == "request_missing":
        del snapshot["observations"][1]
    elif fault == "native_missing":
        del snapshot["observations"][0]
    elif fault == "call_conflict":
        snapshot["observations"][1]["observation"]["arguments"] = {"wrong": True}
    elif fault == "uncertain_retry":
        snapshot["all_attempts"].append({**snapshot["all_attempts"][0], "id": "later", "sequence": 2, "status": "unknown"})
    result = evaluate_exchange(snapshot, "grok", expectation)
    assert result == {"verified": False, "diagnostics": [diagnostic]}


def test_checker_loads_selected_candidate_instead_of_its_own_checkout(tmp_path):
    checker_root = Path(__file__).resolve().parents[2]
    brief = tmp_path / "owner.md"
    brief.write_text("Owner-supplied basis")
    criteria = tmp_path / "criteria.json"
    from scripts.verification.checker import default_criteria
    criteria.write_text(json.dumps(default_criteria(digest(brief.read_bytes()))))
    for label in ("candidate-a", "candidate-b"):
        root = repository(tmp_path / label)
        package = root / "xorchestrate"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "composition.py").write_text(
            "from pathlib import Path\n"
            "def load_config(path): return {}, Path.cwd()\n"
            f"def build(config, base): raise RuntimeError('{label}')\n")
        output = tmp_path / (label + "-run")
        command = [sys.executable, "-m", "scripts.verification.multi_harness", "--mode", "fixture",
                   "--candidate", str(root), "--criteria", str(criteria), "--brief", str(brief), "--out", str(output)]
        result = subprocess.run(command, cwd=checker_root, capture_output=True, text=True, timeout=10)
        assert result.returncode == 1, result.stderr
        assert json.loads((output / "setup-failure.json").read_text())["diagnostic"] == label


def test_lifecycle_summary_without_raw_records_cannot_manufacture_a_pass():
    from scripts.verification.checker import check_case
    case = {"id": "lifecycle-codex-resume", "kind": "lifecycle", "peer": "codex", "operation": "resume"}
    run = {"events": [{"seq": 1, "event": "lifecycle", "peer": "codex", "operation": "resume",
                       "before": {"native_id": "native", "incarnation": "old"},
                       "after": {"native_id": "native", "incarnation": "new"},
                       "stale_rejected": True, "retained_observation": {"memory": "private"}}],
           "sessions": {}, "memories": {"codex": "private"}}
    with pytest.raises(VerificationFailure, match="lifecycle_operation_binding"):
        check_case(case, run, {"batches": []})


def test_successful_transport_does_not_hide_wrong_work_missing_completion_or_unsupported_interjection():
    from scripts.verification.checker import check_case
    events, sessions, case = proof_chain()
    peer = "grok"
    args = {"batch": 0, "failed_jobs": [], "failed_tests": 0}
    common = {"peer": peer, **sessions[peer], "tool": "invoke", "arguments": args, "call_id": "work-call"}
    events.extend([
        {"seq": 20, "event": "native_tool", **common, "origin": "grok/session/update"},
        {"seq": 21, "event": "tool_called", **common},
        {"seq": 22, "event": "work_submitted", **common, "batch": 0, "answer": {"failed_jobs": [], "failed_tests": 0}},
    ])
    fixture = {"batches": [[{"job": "required", "attempt": 1, "status": "FAIL", "failed_tests": 2}]]}
    run = {"events": events, "sessions": sessions}
    # The communication predicate stays successful despite an incorrect answer.
    assert exchange_evidence(events, case, "nonce", "private", sessions)
    with pytest.raises(VerificationFailure, match="incorrect_work"):
        check_case({"id": "work", "kind": "work", "peer": peer, "batch": 0}, run, fixture)
    with pytest.raises(VerificationFailure, match="native_completion_missing"):
        check_case({"id": "completion", "kind": "completion", "peer": peer}, run, fixture)
    events.append({"seq": 23, "event": "activity", "peer": peer, **sessions[peer], "state": "idle", "basis": "native"})
    # A late submission acknowledgement cannot invalidate actual completion.
    events.append({"seq": 24, "event": "submitted", "peer": peer, **sessions[peer]})
    assert check_case({"id": "completion", "kind": "completion", "peer": peer}, run, fixture)
    events.append({"seq": 25, "event": "unsupported", "peer": peer})
    with pytest.raises(VerificationFailure, match="native_protocol_violation"):
        tool_audit(events, sessions)
