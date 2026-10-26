"""F05: bind inference inputs; retain known-bad behavioral calibration (B2/A2/B4)."""
import json
import sys
from copy import deepcopy

import pytest

from tests.hexagonal.support.work_acceptance import accept, issues, make_app, measure, setup_work
from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
from xorchestrate.composition import build
from xorchestrate.model import Assessment, Conflict


class MaterialEvaluator:
    def __init__(self):
        self.contexts = []
        self.after_observation = None

    def assess(self, question, context):
        self.contexts.append(deepcopy(context))
        blocked = context["context"].get("known_defect", False) or any(
            item.get("result", {}).get("known_defect", False) for item in context["evidence"])
        if self.after_observation:
            self.after_observation()
        return Assessment("reject" if blocked else "pass", .99, "Current material inputs govern review")

    def close(self):
        pass


@pytest.fixture
def configured(store):
    evaluator = MaterialEvaluator()
    app = make_app(store, evaluator=evaluator, policy=RequiredChecksAcceptance({"require_assessment": True}))
    work, _ = setup_work(app, {"A": "Functional behavior plus current configured review"})
    measure(app, work)
    yield app, work, evaluator


def assess(app, work):
    return app.call("assess", {"work_id": work["id"], "question": "Review current inputs"})["assessment"]


def change_context(app, work):
    app.call("update_work", {"work_id": work["id"], "expected_version": work["version"],
        "changes": {"context": {"known_defect": True}}})


def require_stale_rejected(app, work, old):
    result = accept(app, work, old)
    assert not result["accepted"], "F05_MATERIAL_ASSESSMENT_INPUTS_MUST_REMAIN_CURRENT"
    assert "assessment_inputs_stale" in issues(result)
    return result


@pytest.mark.parametrize("when", ["during_assessment", "after_assessment", "after_acceptance"])
@pytest.mark.parametrize("changed", ["context", "evidence"])
def test_material_inputs_invalidate_assessment_and_persisted_acceptance(configured, when, changed):
    app, work, evaluator = configured
    def change():
        if changed == "context":
            change_context(app, work)
        else:
            app.call("record_result", {"work_id": work["id"], "result": {"known_defect": True}})
    if when == "during_assessment":
        evaluator.after_observation = change
    old = assess(app, work)
    evaluator.after_observation = None
    assert old["conclusion"]["outcome"] == "pass"
    if when == "after_acceptance":
        assert accept(app, work, old)["accepted"], "F05_VALID_CONTROL_MUST_PASS"
    if when != "during_assessment":
        change()
    if when == "after_acceptance":
        view = app.call("get_acceptance", {"work_id": work["id"]})
        assert not view["accepted"], "F05_PERSISTED_ACCEPTANCE_MUST_REMAIN_CURRENT"
        assert not view["acceptance_history"][-1]["current"]
    require_stale_rejected(app, work, old)
    fresh = assess(app, work)
    assert fresh["conclusion"]["outcome"] == "reject"
    result = accept(app, work, fresh)
    assert not result["accepted"] and "assessment_inputs_stale" not in issues(result)
    assert {r["code"] for r in result["policy_decision"]["reasons"]} == {"assessment_unfavorable"}


@pytest.mark.parametrize("field,value", [
    ("inputs", {"revision": "new"}), ("goal", "Revised plan"),
    ("metadata", {"scope": "new"}), ("status", "reopened"),
])
def test_every_mutable_work_input_supplied_to_evaluator_is_bound(configured, field, value):
    app, work, _ = configured
    old = assess(app, work)
    assert accept(app, work, old)["accepted"]
    app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {field: value}})
    require_stale_rejected(app, work, old)
    assert accept(app, work, assess(app, work))["accepted"]


def test_title_and_unrelated_work_changes_preserve_assessment(configured):
    app, work, evaluator = configured
    old = assess(app, work)
    assert accept(app, work, old)["accepted"]
    app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {"title": "Display only"}})
    other = app.call("create_work", {"goal": "Unrelated work"})["work"]
    app.call("record_result", {"work_id": other["id"], "result": {"known_defect": True}})
    assert app.call("get_acceptance", {"work_id": work["id"]})["accepted"]
    assert accept(app, work, old)["accepted"]
    fresh = assess(app, work)
    assert accept(app, work, fresh)["accepted"]
    assert "title" not in evaluator.contexts[0]["work"]
    assert "version" not in evaluator.contexts[0]["work"]
    assert evaluator.contexts[0]["work"] == evaluator.contexts[-1]["work"]


def test_contribution_input_and_actor_context_remain_bound(configured):
    app, work, _ = configured
    app.call("add_actor", {"actor_id": "owner", "roles": ["reviewer"]})
    old = assess(app, work)
    assert accept(app, work, old)["accepted"]
    app.call("create_contribution", {"work_id": work["id"], "goal": "Known plan change", "outcomes": ["A"]})
    require_stale_rejected(app, work, old)
    fresh = assess(app, work)
    assert accept(app, work, fresh)["accepted"]
    app.call("update_actor", {"actor_id": "owner", "expected_version": 1, "changes": {"roles": ["new role"]}})
    require_stale_rejected(app, work, fresh)


def test_material_change_during_acceptance_cannot_commit(configured):
    app, work, _ = configured
    original = app.acceptance
    class ChangingPolicy:
        identity = original.identity
        def evaluate(self, snapshot):
            change_context(app, work)
            return original.evaluate(snapshot)
    old = assess(app, work)
    app.acceptance = ChangingPolicy()
    with pytest.raises(Conflict):
        accept(app, work, old)
    with app.store.transaction() as tx:
        assert tx.list("acceptance_decision") == []


def test_mechanical_only_acceptance_does_not_depend_on_unused_evaluator_inputs(configured):
    app, work, _ = configured
    app.acceptance = RequiredChecksAcceptance()
    assert accept(app, work)["accepted"]
    change_context(app, work)
    assert app.call("get_acceptance", {"work_id": work["id"]})["accepted"]
    assert accept(app, work)["accepted"]


def test_known_bad_stale_input_fault_is_detected_by_intended_assertion(configured, monkeypatch):
    """Fault: bypass freshness. Activation: changed input; oracle: named assertion."""
    app, work, _ = configured
    old = assess(app, work)
    assert accept(app, work, old)["accepted"], "F05_VALID_CONTROL_MUST_PASS"
    change_context(app, work)
    require_stale_rejected(app, work, old)  # Repaired subject rejects before fault planting.
    calls = []
    def ignore_input_changes(tx, assessment):
        calls.append(assessment.key)
        return True
    monkeypatch.setattr(app, "_assessment_inputs_current", ignore_input_changes)
    # The faulty subject remains faulty. Only the outer calibration passes.
    assert accept(app, work, old)["accepted"]
    with pytest.raises(AssertionError, match="^F05_MATERIAL_ASSESSMENT_INPUTS_MUST_REMAIN_CURRENT"):
        require_stale_rejected(app, work, old)
    assert calls and set(calls) == {old["id"]}, "F05_FAULT_MUST_ACTIVATE"


def test_unrelated_evaluator_failure_is_not_target_detection(configured):
    app, work, evaluator = configured
    def unrelated_failure():
        raise TimeoutError("unrelated provider outage")
    evaluator.after_observation = unrelated_failure
    with pytest.raises(TimeoutError, match="unrelated provider outage"):
        assess(app, work)
    with app.store.transaction() as tx:
        assert tx.list("assessment") == []
        assert tx.list("acceptance_decision") == []


@pytest.mark.parametrize("variant", ["benign-title", "material-context", "material-provider-evidence"])
def test_actual_cli_tool_evaluator_binds_context_and_provider_evidence(tmp_path, variant):
    peer = tmp_path / "assessment_peer.py"
    peer.write_text('''import json,sys
request=json.load(sys.stdin)
a=request["arguments"]
if request["name"]=="measure":
 result={"measurement":{**a["measurement"],"status":"completed","passed":True,"outcomes":{"A":True}}}
elif request["name"]=="diagnostic":
 result={"known_defect":True}
else:
 c=a["context"]
 bad=c["context"].get("known_defect",False) or any(e.get("source",{}).get("type")=="capability" and e.get("result",{}).get("known_defect") for e in c["evidence"])
 result={"outcome":"reject" if bad else "pass","confidence":0.99}
json.dump(result,sys.stdout)
''')
    config = {"store": {"adapter": "sqlite", "options": {"path": str(tmp_path / "state.sqlite")}},
        "providers": {"checks": {"adapter": "cli", "options": {"command": [sys.executable, str(peer)],
            "capabilities": [{"name": name, "input_schema": {"type": "object"}} for name in ["measure", "review", "diagnostic"]]}}},
        "evaluators": {"review": {"adapter": "tool", "options": {"provider": "checks", "capability": "review"}}},
        "assessment": {"triage": "review"}, "acceptance": {"adapter": "required-checks", "options": {"require_assessment": True}}}
    with build(config, tmp_path) as runtime:
        app = runtime.application
        work, _ = setup_work(app, {"A": "A plus current configured review"})
        measure(app, work)
        old = assess(app, work)
        assert accept(app, work, old)["accepted"]
        if variant == "material-provider-evidence":
            app.call("invoke", {"work_id": work["id"], "capability": "checks/diagnostic", "arguments": {}, "idempotency_key": "new-diagnostic"})
        elif variant == "material-context":
            change_context(app, work)
        else:
            app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {"title": "Display only"}})
        view = app.call("get_acceptance", {"work_id": work["id"]})
        stale = accept(app, work, old)
        fresh = assess(app, work)
        current = accept(app, work, fresh)
        benign = variant == "benign-title"
        assert view["accepted"] is benign, "F05_COMPOSED_CURRENT_ACCEPTANCE_MUST_MATCH_INPUTS"
        assert stale["accepted"] is benign, "F05_COMPOSED_STALE_ASSESSMENT_MUST_REJECT"
        assert ("assessment_inputs_stale" in issues(stale)) is not benign
        assert current["accepted"] is benign
        assert "assessment_inputs_stale" not in issues(current)
        (tmp_path / "observations.json").write_text(json.dumps({"variant": variant,
            "persisted": view, "old": old, "stale": stale, "fresh": fresh, "current": current}, indent=2))


def test_explicit_work_projection_can_consume_and_bind_title(configured):
    app, work, evaluator = configured
    app.assessment["work_input_fields"] = ["id", "goal", "title"]
    old = assess(app, work)
    assert evaluator.contexts[-1]["work"]["title"] == work["title"]
    assert accept(app, work, old)["accepted"]
    app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {"title": "Material in this deployment"}})
    require_stale_rejected(app, work, old)
    fresh = assess(app, work)
    assert accept(app, work, fresh)["accepted"]
    app.assessment["work_input_fields"] = ["id", "goal"]
    assert "assessment_configuration_stale" in issues(accept(app, work, fresh))


@pytest.mark.parametrize("changed", ["actor", "contribution"])
def test_mutable_evaluator_dependencies_changed_during_assessment_are_historical(configured, changed):
    app, work, evaluator = configured
    app.call("add_actor", {"actor_id": "owner", "roles": ["before"]})
    def change():
        if changed == "actor":
            app.call("update_actor", {"actor_id": "owner", "expected_version": 1, "changes": {"roles": ["after"]}})
        else:
            app.call("create_contribution", {"work_id": work["id"], "goal": "Changed during review", "outcomes": ["A"]})
    evaluator.after_observation = change
    old = assess(app, work)
    evaluator.after_observation = None
    require_stale_rejected(app, work, old)
    assert accept(app, work, assess(app, work))["accepted"]


@pytest.mark.parametrize("field", ["context", "inputs", "metadata"])
@pytest.mark.parametrize("before,after", [(0, False), (1, True), (0, 0.0)])
def test_material_json_type_changes_do_not_compare_as_equal(configured, field, before, after):
    app, work, _ = configured
    class TypeEvaluator:
        def assess(self, question, context):
            value = context["work"][field]["mode"]
            return Assessment("pass" if type(value) is int else "reject", .99, "JSON type matters")
    app.evaluators["review"] = TypeEvaluator()
    work = app.call("update_work", {"work_id": work["id"], "expected_version": work["version"],
        "changes": {field: {"mode": before}}})["work"]
    old = assess(app, work)
    assert accept(app, work, old)["accepted"]
    app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {field: {"mode": after}}})
    assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"], "F05_JSON_TYPES_ARE_MATERIAL"
    require_stale_rejected(app, work, old)
    fresh = assess(app, work)
    assert fresh["conclusion"]["outcome"] == "reject"
    assert not accept(app, work, fresh)["accepted"]
