"""Credential-free bounded probes against frozen product code and protocol peers.

A product fault is a measured observation, never a failure of this probe runner.
Stored raw observations preserve identity and contradictory evidence. Imported
review reproductions retain source bytes; only the checkout path is adapted.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
import operator
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SOURCES = HERE / "data/reproduction_sources"
PRODUCT_COMMIT = "d2d2123bb8d4e866454c8e8e364f7749fa63a198"
PRODUCT_TREE = "7bb88d5fd01a21478b720ad2d42b2b627b74bb92"
PROBES = {
    "p_context": "Change evaluator-consumed context and a cosmetic title independently; compare old and fresh acceptance on the active application.",
    "p_assertion": "Execute an isolated target assertion, healthy return, and helper exception; retain the exception origin and reached assertion.",
    "p_arguments": "Compare original native arguments with callback arguments before normalization and evaluate the durable receipt.",
    "p_attempts": "Inspect all ordered delivery attempts including a later unknown attempt and evaluate the durable receipt.",
    "p_replay": "Replay the same semantic acceptance call through Codex, Grok, and Claude adapters after an owner revision; compare a fresh native read.",
    "p_effect": "Hold a response after an applied effect, reconnect, release the response, then inspect the recovery fence before retry.",
    "p_busy": "Hold two native callbacks, release one, inspect activity while the other remains held, then release the second.",
    "p_authority": "Hold an observed callback across connection replacement and compare original actor/work/incarnation with the executed callback.",
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check(invariant, left, right, *, relation="eq", note="", observed=True):
    return {"invariant": invariant, "left": left, "operator": relation, "right": right,
            "observed": observed, "note": note,
            "subject": {"expected": "operation-current", "observed": "operation-current"}}


def assertion_rows(facts):
    operators = {"eq": operator.eq, "le": operator.le, "ge": operator.ge,
                 "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b}
    rows = []
    for item in facts["checks"]:
        reached = item["observed"] and item["subject"]["expected"] == item["subject"]["observed"]
        rows.append({"invariant": item["invariant"], "exercised": reached,
                     "satisfied": operators[item["operator"]](item["left"], item["right"]) if reached else None,
                     "actual": item["left"], "required": item["right"], "operator": item["operator"]})
    return rows


def _review_module(name, out):
    """Execute retained reproduction definitions, with path adaptation recorded."""
    source = SOURCES / (name + ".py.txt")
    text = source.read_text()
    old = 'ROOT = Path(__file__).resolve().parents[3] / "review-lifecycle"'
    if old not in text:
        raise ValueError("review adaptation anchor missing")
    text = text.replace(old, "ROOT = Path(" + repr(str(ROOT)) + ")", 1)
    if name == "late_effect":
        anchor = 'new = app.call("reconnect_session", {"driver": "external", "specification": {}, "resume_session_id": old["id"], "idempotency_key": "reconnect"})["session"] if replace else old'
        text = text.replace(anchor, anchor + '\n                if replace:\n                    new = app.call("reconnect_session", {"driver": "external", "specification": {}, "resume_session_id": new["id"], "idempotency_key": "reconnect-again"})["session"]')
    elif name == "lifecycle":
        text = text.replace('for name in ("A", "B")', 'for name in ("A", "B", "C")')
        text = text.replace('before = driver.status(session)', 'driver.send(session, {"hold": "C"})\n            assert entered["C"].wait(3)\n            before = driver.status(session)')
        text = text.replace('release["A"].set()', 'release["B"].set()', 1)
        text = text.replace('not release["B"].is_set() and "B" not in finished', 'not release["C"].is_set() and "C" not in finished')
        text = text.replace('release["B"].set()\n            wait(lambda: len', 'release["A"].set()\n            release["C"].set()\n            wait(lambda: len')
        text = text.replace('== 2)', '== 3)')
        anchor = 'new = second.reconnect(old, {"context": {"actor": "new-actor", "session_id": "new-session", "work_id": "new-work"}}) if replace else None'
        text = text.replace(anchor, anchor + '\n                if replace:\n                    new = second.reconnect(new, {"context": {"actor": "final-actor", "session_id": "final-session", "work_id": "final-work"}})')
    write(out / "adaptation.json", {"source_sha256": sha(source), "adapted_sha256": hashlib.sha256(text.encode()).hexdigest(),
          "changes": "Checkout path; v2 chained replacements or three overlapping holds with middle-first completion. Original bytes retained."})
    target = out / (name + ".py")
    target.write_text(text)
    spec = importlib.util.spec_from_file_location("jev_v2_" + name, target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _context(out):
    assert_subject_identity()
    from tests.hexagonal.test_work_acceptance import make_app, setup_work, measure, accept
    from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
    from xorchestrate.adapters.storage import SQLiteStore
    from xorchestrate.model import Assessment

    class Evaluator:
        def assess(self, question, context):
            return Assessment("reject" if context["context"].get("check_result") == "failed" else "pass", .99, "Fixed configured context predicate")
        def close(self):
            pass

    rows = []
    for material in (False, True):
        store = SQLiteStore(str(out / ("material.sqlite" if material else "cosmetic.sqlite")))
        app = make_app(store=store, evaluator=Evaluator(), policy=RequiredChecksAcceptance({"require_assessment": True}))
        try:
            work, _ = setup_work(app, {"A": "Current assessment and required mechanical check"})
            measure(app, work)
            old = app.call("assess", {"work_id": work["id"], "question": "Evaluate current context"})["assessment"]
            initial = accept(app, work, old)
            changes = {"context": {"check_result": "failed"}} if material else {"title": "Display title revised"}
            app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": changes})
            persisted = app.call("get_acceptance", {"work_id": work["id"]})
            replay = accept(app, work, old)
            fresh = app.call("assess", {"work_id": work["id"], "question": "Evaluate current context"})["assessment"]
            current = accept(app, work, fresh)
            rows.append({"changes": changes, "initial": initial, "persisted": persisted,
                         "old_assessment": old, "reused": replay, "fresh_assessment": fresh, "fresh": current})
        finally:
            store.close()
    assert rows[0]["reused"]["accepted"] and rows[0]["fresh"]["accepted"], "cosmetic control"
    assert rows[1]["reused"]["accepted"] and not rows[1]["fresh"]["accepted"], "sustained known-bad material context"
    return {"records": rows, "boundary": "active Application / SQLite / configured evaluator"}


def _assertion(out):
    """Structured observation recorded BEFORE the actual target assert.

    Marker text never determines attribution; the independently recorded returned
    value and reached phase do. Unrelated AssertionError includes the same marker.
    """
    rows = []
    for name, body in [("healthy", "return 1"), ("wrong_return", "return 2"),
                       ("helper_error", "raise AssertionError('TARGET_REQUIREMENT')")]:
        directory = out / name
        directory.mkdir()
        (directory / "module.py").write_text("def compute():\n    " + body + "\n")
        test = '''import json
from pathlib import Path
from module import compute

def test_target():
    Path('phase.json').write_text(json.dumps({'phase':'entered'}))
    value = compute()
    Path('phase.json').write_text(json.dumps({'phase':'comparison', 'actual':value, 'expected':1}))
    assert value == 1, 'TARGET_REQUIREMENT'
'''
        (directory / "test_target.py").write_text(test)
        command = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider",
                   "--rootdir=" + str(directory), "--junitxml=" + str(directory / "results.xml"), "test_target.py"]
        result = subprocess.run(command, cwd=directory, capture_output=True, timeout=10)
        (directory / "stdout.log").write_bytes(result.stdout)
        (directory / "stderr.log").write_bytes(result.stderr)
        phase = json.loads((directory / "phase.json").read_text())
        rows.append({"variant": name, "phase": phase, "returncode": result.returncode,
                     "command": command, "source_sha256": sha(directory / "test_target.py")})
    assert rows[0]["returncode"] == 0
    assert rows[1]["phase"] == {"phase": "comparison", "actual": 2, "expected": 1} and rows[1]["returncode"] == 1
    assert rows[2]["phase"] == {"phase": "entered"} and rows[2]["returncode"] == 1
    from scripts.verification.calibration import classify, junit_cases
    control = junit_cases(out / "healthy/results.xml")
    for row in rows:
        mutant = junit_cases(out / row["variant"] / "results.xml")
        row["candidate_classifier"] = classify(control, mutant, "test_target::test_target", "TARGET_REQUIREMENT", control_exit=0, mutant_exit=row["returncode"])
        directory = out / row["variant"]
        for source in directory.glob("*.py"):
            source.rename(source.with_suffix(source.suffix + ".txt"))
        row["retention_transform"] = "Executed Python source retained byte-for-byte with .py.txt suffix to prevent recursive pytest collection."
    return {"records": rows, "boundary": "real pytest subprocess and actual candidate calibration classifier; controlled fixture"}


def _receipt(out, kind):
    from tests.hexagonal.test_verification import durable_exchange
    from xorchestrate.exchange import evaluate_exchange
    from scripts.verification.normalize import normalize
    snapshot, expectation = durable_exchange()
    # Historical helper supplies schema, not outcome: fresh copies establish both controls.
    control = deepcopy(snapshot)
    changed = deepcopy(snapshot)
    if kind == "arguments":
        changed["observations"][1]["observation"]["arguments"] = {"work_id": "other-work", "body": {"operation": "separate"}}
        changed["all_attempts"].append({**changed["all_attempts"][0], "id": "later-attempt", "sequence": 2, "status": "unknown"})
    else:
        changed["all_attempts"].append({**changed["all_attempts"][0], "id": "later-attempt", "sequence": 2, "status": "unknown"})
    records = []
    for raw in (control, changed):
        verdict = evaluate_exchange(raw, "grok", expectation)
        normalization = None
        if kind == "arguments":
            adapted = deepcopy(raw)
            adapted["attempts"] = adapted["all_attempts"]
            for index, obs in enumerate(adapted["observations"]):
                obs["created_at"] = str(index + 1)
                obs["observation"]["source"] = "fixture"
            for attempt in adapted["attempts"]:
                attempt.update(created_at="10", finished_at="11")
            peers = {s["actor_id"]: {"session_id": s["id"], **s["backend"]} for s in adapted["sessions"]}
            normalization = normalize(adapted, peers, [], "fixture")
        records.append({"snapshot": raw, "expectation": expectation, "receipt": verdict,
                        "normalized": normalization})
    assert records[0]["receipt"]["verified"]
    assert not records[1]["receipt"]["verified"]
    return {"records": records, "boundary": "production receipt and normalizer; controlled durable-record substitution, not live misdelivery"}


def _adapter_replay(out):
    source = SOURCES / "adapter_replay.py.txt"
    script = out / "probe.py"
    text = source.read_text()
    anchor = "  direct=safe(app,'get_acceptance',arguments)"
    replacement = """  intermediate=send('intermediate-replay')
  safe(app,'set_requirements',{'work_id':work['id'],'expected_revision':2,'objective':'Owner adds recovery after intermediate replay','outcomes':{'A':'A','B':'B','C':'Recovery'},'checks':{**basis['checks'],'B':{'capability':'checks/measure','outcomes':['B']},'C':{'capability':'checks/measure','outcomes':['C']}}})
  direct=safe(app,'get_acceptance',arguments)"""
    assert text.count(anchor) == 1
    text = text.replace(anchor, replacement)
    script.write_text(text)
    write(out / "adaptation.json", {"source_sha256": sha(source), "adapted_sha256": sha(script),
          "changes": "Two owner requirement revisions with an intervening semantic replay; original only one revision."})
    command = [sys.executable, str(script), str(ROOT)]
    result = subprocess.run(command, cwd=out, capture_output=True, timeout=75)
    (out / "stdout.log").write_bytes(result.stdout)
    (out / "stderr.log").write_bytes(result.stderr)
    if result.returncode:
        raise RuntimeError("Adapter reproduction failed; inspect retained stderr (returncode %s)" % result.returncode)
    rows = {name: json.loads((out / name / "observed.json").read_text()) for name in ("codex", "grok", "claude")}
    return {"records": rows, "command": command, "boundary": "actual three adapter implementations with controlled native protocol subprocess peers; no installed vendor"}


def assert_subject_identity():
    """Refuse to attribute changed application/test dependencies to frozen d2."""
    paths = ["xorchestrate", "tests", "scripts", "devtools"]
    result = subprocess.run(["git", "diff", "--quiet", PRODUCT_COMMIT, "--", *paths],
                            cwd=ROOT, capture_output=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError("Frozen product subject differs or identity check failed; use an explicitly pinned checkout instead of attributing changed code to d2.")


def run_operation(probe_id, out):
    assert_subject_identity()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    if probe_id == "p_context":
        return _context(out)
    if probe_id == "p_assertion":
        return _assertion(out)
    if probe_id in {"p_arguments", "p_attempts"}:
        return _receipt(out, probe_id.split("_")[1])
    if probe_id == "p_replay":
        return _adapter_replay(out)
    if probe_id == "p_effect":
        module = _review_module("late_effect", out)
        return {"records": [module.scenario(False), module.scenario(True)], "boundary": "active Application / SQLite / real FIFO bridge / controlled counter native port"}
    if probe_id in {"p_busy", "p_authority"}:
        module = _review_module("lifecycle", out)
        if probe_id == "p_busy":
            return {"record": module.claude_overlapping_completion(), "boundary": "actual Claude adapter / subprocess hooks and MCP / controlled protocol peer"}
        return {"records": [module.bridge_callback_after_replacement(False), module.bridge_callback_after_replacement(True)],
                "boundary": "real FIFO bridge / native callback observation / controlled native port"}
    raise KeyError(probe_id)


def view(raw, probe_id, variant):
    """Explicit field selection only; no expected labels or injection names visible."""
    bad = variant != "healthy"
    relationship = None
    if probe_id == "p_context":
        r = raw["records"][int(bad)]
        checks = [check("current_evidence", r["reused"]["accepted"], r["fresh"]["accepted"],
                        note="A reused assessment must agree with the fresh configured predicate after evaluator-consumed inputs change.")]
        details = {"changed_fields": r["changes"], "reused_accepted": r["reused"]["accepted"], "fresh_accepted": r["fresh"]["accepted"]}
    elif probe_id == "p_assertion":
        r = raw["records"][2 if bad else 1]
        reached_target_fault = r["phase"]["phase"] == "comparison" and r["phase"].get("actual") != 1
        checks = [check("assertion_execution", r["candidate_classifier"]["classification"] == "detected", reached_target_fault,
                        note="A verifier may credit intended detection only when the intended comparison actually executes and fails; helper exceptions cannot earn that credit.")]
        details = {"target_phase": r["phase"], "process_returncode": r["returncode"], "candidate_classifier": r["candidate_classifier"],
                   "actual_exception_origin": "test_target assertion" if reached_target_fault else "compute helper before the target comparison"}
    elif probe_id == "p_arguments":
        r = raw["records"][int(bad)]
        native = r["snapshot"]["observations"][0]["observation"]["arguments"]
        callback = r["snapshot"]["observations"][1]["observation"]["arguments"]
        checks = [check("argument_integrity", callback, native, note="Executed callback arguments must match the independently observed native request.")]
        latest = max([a for a in r["snapshot"]["all_attempts"] if a["message_id"] == "m1"], key=lambda a: a["sequence"])
        checks.append(check("effect_uncertainty", latest["status"], "submitted", note="A later unknown delivery attempt cannot inherit an earlier submitted status."))
        details = {"ordered_attempts": r["snapshot"]["all_attempts"], "native_arguments": native, "executed_arguments": callback, "receipt": r["receipt"],
                   "normalized_tool_arguments": [e["arguments"] for e in r["normalized"] if e["event"] == "tool_called"]}
    elif probe_id == "p_attempts":
        r = raw["records"][int(bad)]
        attempts = r["snapshot"]["all_attempts"]
        latest = max([a for a in attempts if a["message_id"] == "m1"], key=lambda a: a["sequence"])
        checks = [check("effect_uncertainty", latest["status"], "submitted", note="Current complete delivery requires the latest attempt to be submitted; an unknown later attempt cannot inherit an older success.")]
        details = {"ordered_attempts": [{k: a[k] for k in ("id", "message_id", "sequence", "status")} for a in attempts], "receipt": r["receipt"]}
    elif probe_id == "p_replay":
        r = raw["records"]["codex"]
        old = r["adapter_replay"] if bad else r["fresh_adapter"]
        checks = [check("current_evidence", old["requirements"]["version"], r["direct"]["requirements"]["version"], note="A claimed current acceptance read must use the current owner requirement revision.")]
        details = {"reported_accepted": old["accepted"], "reported_revision": old["requirements"]["version"],
                   "current_revision": r["direct"]["requirements"]["version"], "fresh_accepted": r["fresh_adapter"]["accepted"]}
        other = raw["records"]["grok"]
        relationship = {"left": {"adapter": "codex", "reported_revision": old["requirements"]["version"], "current_revision": r["direct"]["requirements"]["version"]},
                        "right": {"adapter": "grok", "reported_revision": (other["adapter_replay"] if bad else other["fresh_adapter"])["requirements"]["version"], "current_revision": r["direct"]["requirements"]["version"]},
                        "implementation_evidence": "Adapters have separate completed-cache branches; no shared source function or call stack establishing one implementation cause is supplied.",
                        "question": "Do these observations establish a shared violated invariant, merely similar behavior, or one common implementation cause?"}
    elif probe_id == "p_effect":
        r = raw["records"][int(bad)]
        code = r["first_outcome"].get("code", "completed")
        allowed = ["operation_unknown"] if bad else ["completed"]
        checks = [check("effect_uncertainty", code, allowed, relation="in", note="After a recorded effect and connection replacement, a missing response must retain uncertainty; after acknowledged completion, completion is valid.")]
        if bad:
            checks.append(check("effect_uncertainty", r["next_effect_outcome"].get("code", "completed"), "conflict", note="Retry of the same intended operation must remain fenced while its old outcome is unresolved."))
        details = {"effect_count_before_response": r["first_effect_count"], "connection_replaced": bad,
                   "first_response": r["first_outcome"], "next_response": r["next_effect_outcome"], "final_effect_count": r["final_effect_count"]}
    elif probe_id == "p_busy":
        r = raw["record"]
        status = r["after_a"] if bad else r["before"]
        checks = [check("concurrent_activity", status["activity"], "active", note="At least one explicitly held callback remains unfinished, so the aggregate session must remain active.")]
        details = {"status": status, "unfinished_callbacks": 2 if bad else 3, "released_callbacks": 1 if bad else 0, "after_all_complete": r["after_b_control"]["activity"]}
    elif probe_id == "p_authority":
        r = raw["records"][int(bad)]
        context = r["callbacks"][0]["context"]
        checks = [check("authority_binding", context.get("actor"), "old-actor", note="An already observed native call retains its original actor across reconnect."),
                  check("authority_binding", context.get("work_id"), "old-work", note="An already observed native call retains its original work scope across reconnect.")]
        details = {"observed_original_actor": "old-actor", "observed_original_work": "old-work", "executed_context": context,
                   "old_incarnation": r["old_incarnation"], "new_incarnation": r["new_incarnation"]}
    else:
        raise KeyError(probe_id)
    visible = {"facts": {"checks": checks, "policy_defined": True},
               "observations": [{"boundary": raw["boundary"], "details": details}]}
    if relationship:
        visible["relationship"] = relationship
    return visible


def execute_probe(case, probe_id, output_dir, *, control_mode=None):
    """Run a selected probe against a fresh subject state. No outcomes reused.

    All identifiers and control selectors stay outside the model-visible packet.
    A different probe is a real executable diagnostic but cannot reveal the
    selected case's missing fact; it receives no realized utility credit.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    record = {"probe_id": probe_id, "case_id": case["id"], "status": "not_exercised",
              "exercised": False, "decisive": False, "distinguishing": False,
              "subject": {"commit": PRODUCT_COMMIT, "tree": PRODUCT_TREE},
              "observations": [], "assertions": [], "actual_cost_usd": None,
              "cost_basis": "local runtime measured in seconds; machine monetary rate unavailable"}
    if probe_id not in PROBES:
        record.update(status="unsupported", reason="not an executable candidate")
    elif probe_id != case["probe_id"]:
        record.update(status="unsupported", reason="probe boundary does not apply to this controlled subject")
    else:
        try:
            if control_mode == "setup":
                raise ImportError("controlled unrelated setup failure")
            if control_mode == "helper":
                raise AssertionError("TARGET_REQUIREMENT controlled helper; target not reached")
            if control_mode == "timeout":
                subprocess.run([sys.executable, "-c", "import time; time.sleep(2)"], timeout=.02, capture_output=True)
            raw = run_operation(probe_id, out / "execution")
            write(out / "raw.json", raw)
            visible = view(raw, probe_id, case["scenario"])
            assertions = assertion_rows(visible["facts"])
            record.update(status="executed", exercised=all(a["exercised"] for a in assertions),
                          decisive=all(a["exercised"] for a in assertions), distinguishing=True,
                          observations=visible["observations"], visible=visible, assertions=assertions,
                          raw_reference=str(out / "raw.json"), raw_sha256=sha(out / "raw.json"))
        except subprocess.TimeoutExpired as exc:
            record.update(status="not_exercised", error_type=type(exc).__name__, reason="timeout before target assertion")
        except Exception as exc:
            record.update(status="not_exercised", error_type=type(exc).__name__, reason=str(exc))
    record["elapsed_seconds"] = time.monotonic() - started
    write(out / "probe.json", record)
    return record


def execute_action(case, action, output_dir):
    """Controlled end-to-end action consequence, restricted to context boundary.

    Every call uses fresh SQLite and actual Application acceptance APIs. No earlier
    acceptance exists. 'accept' consumes the old assessment; other actions withhold
    acceptance. 'investigate' additionally obtains a fresh observation. Independent
    truth uses the configured predicate on material context, measured after action.
    This is a local action-effect test, not a production economic outcome.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    record = {"case_id": case["id"], "action": action, "status": "unsupported",
              "subject": {"commit": PRODUCT_COMMIT, "tree": PRODUCT_TREE},
              "cost_usd": None, "cost_basis": "local runtime; compute price and review time unknown"}
    if case["probe_id"] != "p_context" or action not in {"accept", "reject", "investigate", "defer"}:
        write(out / "action.json", record)
        return record
    assert_subject_identity()
    from tests.hexagonal.test_work_acceptance import make_app, setup_work, measure, accept
    from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
    from xorchestrate.adapters.storage import SQLiteStore
    from xorchestrate.model import Assessment

    class Evaluator:
        def assess(self, question, context):
            return Assessment("reject" if context["context"].get("check_result") == "failed" else "pass", .99, "Fixed configured context predicate")
        def close(self):
            pass

    store = SQLiteStore(str(out / "state.sqlite"))
    app = make_app(store=store, evaluator=Evaluator(), policy=RequiredChecksAcceptance({"require_assessment": True}))
    try:
        work, _ = setup_work(app, {"A": "Current configured predicate and mechanical measurement"})
        measure(app, work)
        old = app.call("assess", {"work_id": work["id"], "question": "Evaluate current context"})["assessment"]
        material = case["scenario"] != "healthy"
        changes = {"context": {"check_result": "failed"}} if material else {"title": "Revised display label"}
        app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": changes})
        before = app.call("get_acceptance", {"work_id": work["id"]})
        action_result = None
        if action == "accept":
            action_result = accept(app, work, old)
        elif action == "investigate":
            action_result = app.call("assess", {"work_id": work["id"], "question": "Evaluate current context"})["assessment"]
        after = app.call("get_acceptance", {"work_id": work["id"]})
        independent = app.call("assess", {"work_id": work["id"], "question": "Evaluate current context"})["assessment"]
        record.update(status="executed", before=before, after=after, old_assessment=old,
                      action_result=action_result, independent_assessment=independent,
                      context_change=changes, accepted_after=after["accepted"],
                      should_accept=not material, consequential_false_accept=bool(material and after["accepted"]),
                      unnecessary_withholding=bool(not material and not after["accepted"]),
                      behavioral_outcome_correct=after["accepted"] == (not material),
                      limitation="Only actual acceptance state is measured. Reject/defer/investigate withhold acceptance; downstream repair, scheduling and business loss are not simulated as observed.")
    finally:
        store.close()
    record["elapsed_seconds"] = time.monotonic() - start
    write(out / "action.json", record)
    return record
