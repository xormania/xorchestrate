"""B1/B2/A1/A2 through the active application and composed provider boundary."""
import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from tests.hexagonal.support.delivery import Native
from tests.hexagonal.support.work_acceptance import (
    CANDIDATE,
    Evaluator,
    MeasuringProvider,
    accept,
    issues,
    make_app,
    measure,
    setup_work,
)
from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.adapters.storage import SQLiteStore
from xorchestrate.composition import build
from xorchestrate.model import Conflict, OrchestrationError


@pytest.fixture
def app(store):
    instance = make_app(store=store)
    yield instance


def test_full_owner_objective_survives_narrowing_and_cancellation(app):
    work, basis = setup_work(app)
    child = app.call("create_contribution", {"work_id": work["id"], "goal": "Implement A and B", "outcomes": ["A", "B"]})["contribution"]
    app.call("update_contribution", {"work_id": work["id"], "contribution_id": child["id"], "expected_version": child["version"],
        "changes": {"goal": "Only A", "outcomes": ["A"], "status": "cancelled"}})
    app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {"goal": "Narrowed plan", "status": "done"}})
    measure(app, work, "A")
    decision = accept(app, work)
    assert decision["accepted"] is False, "OWNER_OUTCOME_B_MUST_REMAIN_REQUIRED"
    assert decision["coverage"]["B"]["covered"] is False
    view = app.call("get_work", {"work_id": work["id"]})
    assert view["requirements"]["objective"] == basis["objective"]
    assert set(view["requirements"]["outcomes"]) == {"A", "B"}
    assert view["accepted"] is False
    assert view["contributions"][0]["status"] == "cancelled"


def test_explicit_owner_revision_changes_current_basis_without_erasing_history(app):
    work, basis = setup_work(app)
    measure(app, work, "A")
    assert not accept(app, work)["accepted"]
    revised = app.call("set_requirements", {"work_id": work["id"], "expected_revision": 1, "objective": "Owner now requires A",
        "outcomes": {"A": "Happy path"}, "checks": {"A": basis["checks"]["A"]}})["requirements"]
    assert revised["version"] == 2
    assert "measurement_stale" in issues(accept(app, work))
    measure(app, work)
    assert accept(app, work)["accepted"]
    view = app.call("get_acceptance", {"work_id": work["id"]})
    assert view["accepted"]
    assert any(not item["current"] for item in view["acceptance_history"])
    with app.store.transaction() as tx:
        assert len(tx.list("requirement_revision")) == 2


def test_claim_and_done_status_do_not_create_acceptance(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    app.call("record_result", {"work_id": work["id"], "result": {"passed": True, "candidate": CANDIDATE, "all_required": "done"}})
    decision = accept(app, work)
    assert not decision["accepted"], "CLAIMS_ARE_NOT_MEASUREMENTS"
    assert "measurement_missing" in issues(decision)
    with pytest.raises(OrchestrationError):
        app.call("run_measurement", {"work_id": work["id"], "check_id": "A", "measurement_key": "fake", "result": {"passed": True}})


@pytest.mark.parametrize("mutator,diagnostic", [
    (lambda m: m.update(passed=False), "required_check_failed"),
    (lambda m: m.update(passed=1), "required_check_failed"),
    (lambda m: m.update(status="running"), "measurement_incomplete"),
    (lambda m: m.update(status="uncertain"), "measurement_incomplete"),
    (lambda m: m.update(candidate={"identity": "other-tree"}), "measurement_binding_mismatch"),
    (lambda m: m.update(work_id="unrelated-work"), "measurement_binding_mismatch"),
    (lambda m: m.update(check_id="unrelated-check"), "measurement_binding_mismatch"),
    (lambda m: m.update(requirements_identity="another-criteria"), "measurement_binding_mismatch"),
    (lambda m: m.update(requirement_revision=True), "measurement_binding_mismatch"),
    (lambda m: m["bindings"].update(scope="unrelated-scope"), "measurement_binding_mismatch"),
    (lambda m: m["bindings"]["artifact"].update(sha256="wrong-artifact"), "measurement_binding_mismatch"),
    (lambda m: m["bindings"]["native"].update(session_id="other-session"), "measurement_binding_mismatch"),
    (lambda m: m["bindings"]["native"].update(incarnation="stale-incarnation"), "measurement_binding_mismatch"),
    (lambda m: m.update(outcomes={"A": False}), "required_outcome_failed"),
    (lambda m: m.update(outcomes={"other": True}), "required_outcome_failed"),
    (lambda m: m.pop("candidate"), "measurement_binding_mismatch"),
], ids=["failed", "numeric-success", "running", "uncertain", "candidate", "work", "check", "criteria", "revision-type",
        "scope", "artifact", "native-session", "incarnation", "outcome-failed", "outcome-unrelated", "candidate-missing"])
def test_insufficient_or_misbound_measurement_cannot_pass(app, mutator, diagnostic):
    work, _ = setup_work(app, {"A": "Behavior"})
    def transform(result):
        mutator(result["measurement"])
        return result
    app.providers["checks"].transform = transform
    measure(app, work)
    decision = accept(app, work)
    assert not decision["accepted"], "BOUND_REQUIRED_MEASUREMENT_MUST_PASS"
    assert diagnostic in issues(decision)


def test_required_failure_cannot_be_erased_by_unrelated_success_or_high_assessment(app):
    evaluator = Evaluator()
    app.evaluators["review"] = evaluator
    app.assessment = {"triage": "review"}
    work, _ = setup_work(app)
    app.providers["checks"].transform = lambda result: {"measurement": {**result["measurement"], "passed": False}}
    measure(app, work, "B")
    app.providers["checks"].transform = lambda result: result
    measure(app, work, "A")
    assessment = app.call("assess", {"work_id": work["id"], "question": "Is work acceptable?"})["assessment"]
    decision = accept(app, work, assessment)
    assert not decision["accepted"], "FAVORABLE_ASSESSMENT_CANNOT_ERASE_MECHANICAL_FAILURE"
    assert "required_check_failed" in issues(decision)
    assert evaluator.contexts[-1]["acceptance_basis"]["requirements"]["objective"] == "Deliver the complete owner objective"
    measure(app, work, "B")
    assert "assessment_stale" in issues(accept(app, work, assessment))
    assert accept(app, work)["accepted"]


def test_measurement_replay_is_bound_to_request_and_does_not_reexecute(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    first = measure(app, work, key="once")
    second = measure(app, work, key="once")
    assert second["replayed"] and first["measurement"] == second["measurement"]
    assert len(app.providers["checks"].calls) == 1
    with pytest.raises(Conflict):
        app.call("run_measurement", {"work_id": work["id"], "check_id": "A", "measurement_key": "once", "arguments": {"different": True}})


def test_running_measurement_blocks_earlier_pass_and_observation_progresses(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    provider = app.providers["checks"]
    provider.entered, provider.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor() as pool:
        running = pool.submit(measure, app, work)
        assert provider.entered.wait(5)
        try:
            decision = accept(app, work)
            assert not decision["accepted"], "RUNNING_MEASUREMENT_IS_NOT_COMPLETE"
            assert "measurement_unresolved" in issues(decision)
            assert app.call("list_work", {})["work"]
        finally:
            provider.release.set()
        running.result(timeout=5)
    assert accept(app, work)["accepted"]


def test_uncertain_measurement_cannot_be_masked_by_new_pass(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    provider = app.providers["checks"]
    def lost(result):
        raise OrchestrationError("Response lost", "transport_lost", {"outcome": "unknown"})
    provider.transform = lost
    with pytest.raises(OrchestrationError):
        measure(app, work, key="uncertain")
    replay = measure(app, work, key="uncertain")
    assert replay["measurement"]["status"] == "unknown"
    provider.transform = lambda result: result
    measure(app, work)
    decision = accept(app, work)
    assert not decision["accepted"]
    assert "measurement_unresolved" in issues(decision)


@pytest.mark.parametrize("observed_status", ["running", "uncertain"])
def test_incomplete_observed_measurement_cannot_be_hidden_by_later_passing_transport(app, observed_status):
    work, _ = setup_work(app, {"A": "Behavior"})
    provider = app.providers["checks"]
    provider.transform = lambda result: {"measurement": {**result["measurement"], "status": observed_status}}
    measure(app, work)
    provider.transform = lambda result: result
    measure(app, work)
    assert "measurement_unresolved" in issues(accept(app, work)), "UNRESOLVED_MEASUREMENT_CANNOT_BE_MASKED"


def test_caller_reconciliation_cannot_manufacture_measurement_truth(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    provider = app.providers["checks"]
    def lost(result):
        raise OrchestrationError("Lost response", "transport_lost", {"outcome": "unknown"})
    provider.transform = lost
    with pytest.raises(OrchestrationError):
        measure(app, work, key="lost")
    retained = measure(app, work, key="lost")["measurement"]
    operation = app.call("get_operation", {"operation_id": retained["operation_id"]})["operation"]
    fake = {"measurement": {**retained["request_binding"], "status": "completed", "passed": True, "outcomes": {"A": True}}}
    app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"],
        "outcome": "completed", "result": fake, "reason": "Caller says it passed"})
    replay = measure(app, work, key="lost")["measurement"]
    assert replay["status"] == "unknown"
    assert not accept(app, work)["accepted"]


def test_observation_must_match_retained_adapter_operation(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measurement = measure(app, work)["measurement"]
    with app.store.transaction() as tx:
        operation = tx.get("operation", measurement["operation_id"])
        tx.put("operation", operation.key, {**operation.data, "external_result_origin": "caller"}, operation.version)
    assert "measurement_unverified" in issues(accept(app, work)), "ADAPTER_ORIGIN_REQUIRED"


def test_required_outcomes_without_check_remain_uncovered(app):
    work = app.call("create_work", {"goal": "Required capability"})["work"]
    app.call("set_requirements", {"work_id": work["id"], "expected_revision": 0, "objective": "A still required", "outcomes": {"A": "A"}, "checks": {}})
    app.call("set_candidate", {"work_id": work["id"], "expected_revision": 0, "candidate": CANDIDATE})
    decision = accept(app, work)
    assert not decision["accepted"] and "outcome_uncovered" in issues(decision)


def test_duplicate_measurement_json_fields_cannot_supply_a_passing_summary(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    def duplicate(result):
        text = json.dumps(result).replace('"passed": true', '"passed": false, "passed": true')
        return {"content": [{"type": "text", "text": text}]}
    app.providers["checks"].transform = duplicate
    measure(app, work)
    assert "measurement_binding_mismatch" in issues(accept(app, work))


def test_candidate_change_invalidates_existing_acceptance_and_old_evidence(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    assert accept(app, work)["accepted"]
    app.call("set_candidate", {"work_id": work["id"], "expected_revision": 1, "candidate": {"identity": "tree:new"}})
    assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"], "ACCEPTANCE_MUST_MATCH_CURRENT_CANDIDATE"
    assert "measurement_stale" in issues(accept(app, work))
    measure(app, work)
    assert "measurement_binding_mismatch" in issues(accept(app, work))


def test_same_name_provider_configuration_change_invalidates_measurements_and_decisions(app):
    app.provider_configurations = {"checks": "resolved-checker-config:v1"}
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    assert accept(app, work)["accepted"]
    app.provider_configurations["checks"] = "resolved-checker-config:v2"
    view = app.call("get_acceptance", {"work_id": work["id"]})
    assert not view["accepted"], "ACCEPTANCE_MUST_MATCH_CHECKER_CONFIGURATION"
    assert "measurement_stale" in issues(accept(app, work))
    measure(app, work)
    assert accept(app, work)["accepted"]


def test_unrelated_provider_change_does_not_invalidate_current_measurements(app):
    app.provider_configurations = {"checks": "v1", "unrelated": "v1"}
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    assert accept(app, work)["accepted"]
    app.provider_configurations["unrelated"] = "v2"
    assert app.call("get_acceptance", {"work_id": work["id"]})["accepted"]


def test_later_rejection_does_not_leave_earlier_decision_as_current_acceptance(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    class RevisablePolicy:
        identity = "policy:v1"
        allowed = True
        def evaluate(self, snapshot):
            return {"accepted": self.allowed}
    app.acceptance = RevisablePolicy()
    assert accept(app, work)["accepted"]
    app.acceptance.allowed = False
    assert not accept(app, work)["accepted"]
    assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"]


def test_requirements_changed_during_assessment_make_it_historical(app):
    work, basis = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    def change():
        app.call("set_requirements", {"work_id": work["id"], "expected_revision": 1, "objective": "Owner revised objective",
            "outcomes": basis["outcomes"], "checks": basis["checks"]})
    evaluator = Evaluator(change)
    app.evaluators["review"], app.assessment = evaluator, {"triage": "review"}
    assessment = app.call("assess", {"work_id": work["id"], "question": "Assess this basis"})["assessment"]
    assert evaluator.contexts[0]["acceptance_basis"]["requirements"]["version"] == 1
    assert "assessment_stale" in issues(accept(app, work, assessment))


def test_assessment_threshold_change_invalidates_only_decisions_consuming_assessment(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    app.evaluators["review"], app.assessment = Evaluator(), {"triage": "review"}
    assessment = app.call("assess", {"work_id": work["id"], "question": "Review"})["assessment"]
    assert accept(app, work, assessment)["accepted"]
    app.assessment["confidence_threshold"] = 1.0
    assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"]
    decision = accept(app, work, assessment)
    assert not decision["accepted"], "ASSESSMENT_CONFIGURATION_MUST_REMAIN_CURRENT"
    assert "assessment_configuration_stale" in issues(decision)
    fresh = app.call("assess", {"work_id": work["id"], "question": "Review new criteria"})["assessment"]
    assert fresh["unresolved"] and not accept(app, work, fresh)["accepted"]
    # Mechanical proof stays current and may be accepted without an assessment
    # when the configured acceptance policy does not require one.
    assert accept(app, work)["accepted"]
    assert len(app.call("get_acceptance", {"work_id": work["id"]})["measurements"]) == 1


def test_assessment_configuration_change_during_inference_is_retained_as_historical(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    def change():
        app.evaluator_configurations["review"] = "new-model-options"
    app.evaluators["review"] = Evaluator(change)
    app.assessment, app.evaluator_configurations = {"triage": "review"}, {"review": "original-model-options"}
    assessment = app.call("assess", {"work_id": work["id"], "question": "Review"})["assessment"]
    assert "assessment_configuration_stale" in issues(accept(app, work, assessment))


def test_unrelated_evaluator_change_does_not_invalidate_selected_assessment(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    app.evaluators["review"], app.assessment = Evaluator(), {"triage": "review"}
    app.evaluator_configurations = {"review": "selected", "unrelated": "first"}
    assessment = app.call("assess", {"work_id": work["id"], "question": "Review"})["assessment"]
    assert accept(app, work, assessment)["accepted"]
    app.evaluator_configurations["unrelated"] = "replacement"
    assert app.call("get_acceptance", {"work_id": work["id"]})["accepted"]
    assert accept(app, work, assessment)["accepted"]


def test_persisted_assessment_rejects_same_name_evaluator_reconfiguration_after_rebuild(tmp_path):
    path = str(tmp_path / "assessment.sqlite")
    first_store = SQLiteStore(path)
    app = make_app(first_store, evaluator=Evaluator(), policy=RequiredChecksAcceptance({"require_assessment": True}))
    app.evaluator_configurations = {"review": "first-model-config"}
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    assessment = app.call("assess", {"work_id": work["id"], "question": "Review"})["assessment"]
    assert accept(app, work, assessment)["accepted"]
    first_store.close()
    second_store = SQLiteStore(path)
    try:
        rebuilt = make_app(second_store, evaluator=Evaluator(), policy=RequiredChecksAcceptance({"require_assessment": True}))
        rebuilt.evaluator_configurations = {"review": "replacement-model-config"}
        view = rebuilt.call("get_acceptance", {"work_id": work["id"]})
        assert not view["accepted"] and view["coverage"]["A"]["covered"]
        assert "assessment_configuration_stale" in issues(accept(rebuilt, work, assessment))
        fresh = rebuilt.call("assess", {"work_id": work["id"], "question": "Review current configuration"})["assessment"]
        assert accept(rebuilt, work, fresh)["accepted"]
        assert len(rebuilt.call("get_acceptance", {"work_id": work["id"]})["measurements"]) == 1
    finally:
        second_store.close()


@pytest.mark.parametrize("changed_provider", ["review-source", "unrelated-source"])
def test_tool_evaluator_binds_its_provider_configuration_across_composed_rebuild(tmp_path, changed_provider):
    peer = tmp_path / "review.py"
    peer.write_text('import json,sys\njson.load(sys.stdin)\njson.dump({"outcome":"pass","confidence":0.999},sys.stdout)\n')
    provider = {"adapter": "cli", "options": {"command": [sys.executable, str(peer)], "env": {"MODEL": "first"},
        "capabilities": [{"name": "review", "description": "Assess", "input_schema": {"type": "object"}}]}}
    config = {"store": {"adapter": "sqlite", "options": {"path": "composed-assessment.sqlite"}},
        "providers": {"review-source": deepcopy(provider), "unrelated-source": deepcopy(provider)},
        "evaluators": {"review": {"adapter": "tool", "options": {"provider": "review-source", "capability": "review"}}},
        "assessment": {"triage": "review"}, "acceptance": {"adapter": "required-checks", "options": {"require_assessment": True}}}
    with build(config, tmp_path) as runtime:
        app = runtime.application
        app.providers["checks"] = MeasuringProvider()
        work, _ = setup_work(app, {"A": "Behavior"})
        measure(app, work)
        assessment = app.call("assess", {"work_id": work["id"], "question": "Review"})["assessment"]
        assert accept(app, work, assessment)["accepted"]
    config["providers"][changed_provider]["options"]["env"]["MODEL"] = "replacement"
    with build(config, tmp_path) as runtime:
        app = runtime.application
        app.providers["checks"] = MeasuringProvider()
        view = app.call("get_acceptance", {"work_id": work["id"]})
        assert view["coverage"]["A"]["covered"] and len(view["measurements"]) == 1
        decision = accept(app, work, assessment)
        if changed_provider == "review-source":
            assert not view["accepted"] and not decision["accepted"], "ASSESSMENT_PROVIDER_CONFIGURATION_MUST_REMAIN_CURRENT"
            assert "assessment_configuration_stale" in issues(decision)
        else:
            assert view["accepted"] and decision["accepted"]


def test_assessment_configuration_change_during_acceptance_is_fenced(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    app.evaluators["review"], app.assessment = Evaluator(), {"triage": "review"}
    class ChangingPolicy:
        identity = "changing-assessment-options-v1"
        def evaluate(self, snapshot):
            app.assessment["confidence_threshold"] = 1.0
            return {"accepted": True}
    app.acceptance = ChangingPolicy()
    assessment = app.call("assess", {"work_id": work["id"], "question": "Review"})["assessment"]
    with pytest.raises(Conflict, match="configuration changed"):
        accept(app, work, assessment)


@pytest.mark.parametrize("action,diagnostic", [("accept_work", "acceptance_stale"), ("get_acceptance", "stale_native_result")])
def test_native_replay_cannot_present_historical_acceptance_as_current(app, action, diagnostic):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    app.sessions["agent"] = Native()
    session = app.call("start_session", {"driver": "agent", "specification": {}, "work_id": work["id"], "idempotency_key": "native"})["session"]
    context = {"session_id": session["id"], "native_id": session["backend"]["native_id"], "incarnation": session["backend"]["incarnation"],
        "actor": "owner", "work_id": work["id"], "call_id": "acceptance-call"}
    if action == "get_acceptance":
        assert accept(app, work)["accepted"]
    first = app.invoke_native("agent", action, {"work_id": work["id"]}, context)
    assert first["acceptance"]["accepted"] if action == "accept_work" else first["accepted"]
    app.call("set_candidate", {"work_id": work["id"], "expected_revision": 1, "candidate": {"identity": "changed"}})
    with pytest.raises(OrchestrationError) as error:
        app.invoke_native("agent", action, {"work_id": work["id"]}, context)
    assert error.value.code == diagnostic, "NATIVE_REPLAY_CANNOT_REVIVE_STALE_ACCEPTANCE"
    assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"]
    with app.store.transaction() as tx:
        assert len(tx.list("acceptance_decision")) == 1


def test_changed_basis_during_policy_evaluation_cannot_commit_acceptance(app):
    work, basis = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    class ChangingPolicy:
        identity = "changing-policy-v1"
        def evaluate(self, snapshot):
            app.call("set_requirements", {"work_id": work["id"], "expected_revision": 1, "objective": "Revised while deciding",
                "outcomes": basis["outcomes"], "checks": basis["checks"]})
            return {"accepted": True}
    app.acceptance = ChangingPolicy()
    with pytest.raises(Conflict):
        accept(app, work)
    assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"]


def test_acceptance_policy_substitutes_without_weakening_required_proof(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    class OtherPolicy:
        identity = "independent-review-required:v1"
        def evaluate(self, snapshot):
            return {"accepted": False, "reasons": [{"code": "deployment_review_pending"}]}
    measure(app, work)
    assert accept(app, work)["accepted"]
    app.acceptance = OtherPolicy()
    assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"]
    decision = accept(app, work)
    assert not decision["accepted"] and not decision["issues"]
    class OverconfidentPolicy:
        identity = "always-favorable:v1"
        def evaluate(self, snapshot):
            return {"accepted": True}
    app.acceptance = OverconfidentPolicy()
    app.providers["checks"].transform = lambda r: {"measurement": {**r["measurement"], "passed": False}}
    measure(app, work)
    assert not accept(app, work)["accepted"]


def test_required_assessment_policy_and_authorization_remain_independent(app):
    work, _ = setup_work(app, {"A": "Behavior"})
    measure(app, work)
    app.acceptance = RequiredChecksAcceptance({"require_assessment": True})
    assert not accept(app, work)["accepted"]
    app.evaluators["review"], app.assessment = Evaluator(), {"triage": "review"}
    assessment = app.call("assess", {"work_id": work["id"], "question": "Review"})["assessment"]
    assert accept(app, work, assessment)["accepted"]
    app.guardrail = RuleGuardrail({"rules": [{"actions": ["accept_work"], "effect": "deny", "reason": "Owner decision required"}]})
    with pytest.raises(OrchestrationError) as error:
        accept(app, work, assessment)
    assert error.value.code == "denied"


def test_scope_mismatch_and_stale_requirement_revisions_are_rejected(app):
    work, basis = setup_work(app, {"A": "Behavior"})
    other, _ = setup_work(app, {"A": "Other"})
    child = app.call("create_contribution", {"work_id": work["id"], "goal": "A", "outcomes": ["A"]})["contribution"]
    with pytest.raises(OrchestrationError) as error:
        app.call("update_contribution", {"work_id": other["id"], "contribution_id": child["id"], "expected_version": 1, "changes": {"status": "cancelled"}})
    assert error.value.code == "wrong_scope"
    with pytest.raises(Conflict):
        app.call("set_requirements", {"work_id": work["id"], "expected_revision": 0, "objective": "Stale", "outcomes": basis["outcomes"], "checks": basis["checks"]})


def test_composed_cli_measurement_observes_file_identity_instead_of_echoing_request(tmp_path):
    product = tmp_path / "product.txt"
    product.write_text("works\n")
    identity = hashlib.sha256(product.read_bytes()).hexdigest()
    peer = tmp_path / "measure.py"
    peer.write_text('''import hashlib,json,pathlib,sys
request=json.load(sys.stdin)["arguments"]
binding=request["measurement"]
data=pathlib.Path(sys.argv[1]).read_bytes()
actual={key:binding[key] for key in ["work_id","check_id","requirements_identity","requirement_revision"]}
actual.update(candidate={"identity":hashlib.sha256(data).hexdigest()},bindings={},status="completed",passed=data==b"works\\n",outcomes={"A":data==b"works\\n"})
json.dump({"measurement":actual},sys.stdout)
''')
    config = {"store": {"adapter": "sqlite", "options": {"path": "state.sqlite"}},
        "providers": {"checks": {"adapter": "cli", "options": {"command": [sys.executable, str(peer), str(product)],
            "capabilities": [{"name": "measure", "description": "Read actual product", "input_schema": {"type": "object"}}]}}}}
    with build(config, tmp_path) as runtime:
        app = runtime.application
        work = app.call("create_work", {"goal": "Verify product"})["work"]
        app.call("set_requirements", {"work_id": work["id"], "expected_revision": 0, "objective": "File works", "outcomes": {"A": "Works"},
            "checks": {"A": {"capability": "checks/measure", "outcomes": ["A"]}}})
        app.call("set_candidate", {"work_id": work["id"], "expected_revision": 0, "candidate": {"identity": identity}})
        measure(app, work)
        assert accept(app, work)["accepted"]
        product.write_text("broken\n")
        measure(app, work)
        assert "measurement_binding_mismatch" in issues(accept(app, work)), "ACTUAL_PRODUCT_IDENTITY_MUST_BE_MEASURED"
    with build(config, tmp_path) as restarted:
        view = restarted.application.call("get_acceptance", {"work_id": work["id"]})
        assert not view["accepted"]
        assert len(view["measurements"]) == 2


def test_no_requirements_or_acceptance_policy_required_for_simple_messaging():
    with build({"store": {"adapter": "memory"}}) as runtime:
        app = runtime.application
        work = app.call("create_work", {"goal": "Simple messaging"})["work"]
        app.call("add_actor", {"actor_id": "reader"})
        app.call("send_message", {"destination": {"actors": ["reader"]}, "body": "hello", "work_id": work["id"]})
        assert app.call("inbox", {}, actor="reader")["messages"]
        app.acceptance = None
        with pytest.raises(OrchestrationError) as error:
            accept(app, work)
        assert error.value.code == "acceptance_unavailable"
