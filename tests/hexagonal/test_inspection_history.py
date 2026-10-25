"""IV01: current policy governs typed records inside retained aggregates."""
import json
from copy import deepcopy

import pytest

from tests.hexagonal.test_application import application
from tests.hexagonal.test_delivery import Native
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.model import Conflict, OrchestrationError

SECRET = "IV01_DENIED_RECORD"
PUBLIC = "IV01_AUTHORIZED_RECORD"


@pytest.fixture(params=["memory", "sqlite"])
def history(request, tmp_path):
    store = MemoryStore() if request.param == "memory" else SQLiteStore(str(tmp_path / "history.sqlite"))
    app = application(store, driver=Native())
    for actor in ("limited", "private"):
        app.call("add_actor", {"actor_id": actor})
    work = app.call("create_work", {"goal": "shared work"})["work"]["id"]
    app.call("set_requirements", {"work_id": work, "expected_revision": 0,
        "objective": "shared", "outcomes": {"A": "outcome"}, "checks": {}})
    sessions = {}
    for actor, marker in (("limited", PUBLIC), ("private", SECRET)):
        sessions[actor] = app.call("start_session", {"driver": "agent", "work_id": work,
            "actor_id": actor, "specification": {"marker": marker}, "idempotency_key": actor})["session"]
        app.call("assign", {"work_id": work, "actor_id": actor, "metadata": {"marker": marker}}, actor=actor)
        app.call("record_result", {"work_id": work, "result": {"marker": marker}}, actor=actor)
        app.call("create_contribution", {"work_id": work, "goal": marker, "outcomes": ["A"]}, actor=actor)
    message = app.call("send_message", {"work_id": work, "destination": {"actor": "limited"},
        "body": {"marker": PUBLIC}})["message"]["id"]
    yield app, work, sessions, message
    store.close()


def record_history(history, action, args, call_id=None):
    app, work, sessions, _ = history
    session = sessions["limited"]
    source = {"actor": "limited", "work_id": work, "session_id": session["id"],
        "native_id": session["backend"]["native_id"], "incarnation": session["backend"]["incarnation"],
        "call_id": call_id or action}
    return app.invoke_native("agent", action, args, source)


def restrict(history, actions=("native_observations", "get_delivery", "get_work", "get_acceptance")):
    app, work, _, _ = history
    app.guardrail = RuleGuardrail({"default": "deny", "rules": [
        {"actors": ["limited"], "actions": list(actions), "where": {"work_id": work}, "effect": "allow"},
        {"actors": ["limited"], "actions": list(actions), "where": {"session_actor": "private"}, "effect": "deny"},
        {"actors": ["limited"], "actions": list(actions), "where": {"record.created_by": "private"}, "effect": "deny"}]})


def inspect(history):
    app, _, sessions, _ = history
    return app.call("native_observations", {"session_id": sessions["limited"]["id"]}, actor="limited")


def assert_projected(result):
    assert SECRET not in json.dumps(result), "IV01_HISTORICAL_RECORD_SCOPE"
    assert PUBLIC in json.dumps(result), "IV01_AUTHORIZED_PROJECTION_RETAINED"


def direct_controls(history):
    app, _, sessions, message = history
    with pytest.raises(OrchestrationError) as denied:
        app.call("native_observations", {"session_id": sessions["private"]["id"]}, actor="limited")
    assert denied.value.code == "denied"
    delivery = app.call("get_delivery", {"message_id": message}, actor="limited")
    assert {row["id"] for row in delivery["sessions"]} == {sessions["limited"]["id"]}


@pytest.mark.parametrize("read_action", ["get_work", "get_acceptance", "list_sessions", "get_delivery", "native_observations"])
def test_historical_aggregates_project_each_nested_record(history, read_action):
    app, work, sessions, message = history
    # Nested native history exercises more than a single result envelope.
    if read_action == "native_observations":
        record_history(history, "get_work", {"work_id": work})
    args = ({"message_id": message} if read_action == "get_delivery" else
        {"session_id": sessions["limited"]["id"]} if read_action == "native_observations" else {"work_id": work})
    original = record_history(history, read_action, args)
    assert SECRET in json.dumps(original), "control: historical input includes denied records"
    with app.store.transaction() as tx:
        raw_before = [(row.key, deepcopy(row.data)) for kind in ("native_call", "native_observation") for row in tx.list(kind)]
    restrict(history)
    direct_controls(history)
    result = inspect(history)
    assert_projected(result)
    assert any(row.get("inspection_redacted") for row in result["calls"])
    with app.store.transaction() as tx:
        raw_after = [(row.key, deepcopy(row.data)) for kind in ("native_call", "native_observation") for row in tx.list(kind)]
    assert raw_after == raw_before, "IV01_RAW_HISTORY_REMAINS_UNCHANGED"
    # Restoring the current policy restores visibility without rewriting history.
    app.guardrail = RuleGuardrail()
    assert SECRET in json.dumps(inspect(history))


@pytest.mark.parametrize("action", ["get_work", "get_acceptance"])
def test_current_work_aggregates_use_the_same_record_policy(history, action):
    app, work, _, _ = history
    restrict(history)
    result = app.call(action, {"work_id": work}, actor="limited")
    assert_projected(result)
    assert result["inspection_redacted"] is True
    assert len(result["contributions"]) == 1
    if action == "get_work":
        assert len(result["assignments"]) == len(result["sessions"]) == len(result["evidence"]) == 1
    with app.store.transaction() as tx:
        assert SECRET in json.dumps(app._work_snapshot(tx, work)), "IV01_INTERNAL_WORK_SNAPSHOT_RETAINED"


def test_known_bad_historical_session_omission_is_detected(history, monkeypatch):
    from xorchestrate.delivery import _INSPECTION_RESULTS

    _, work, _, _ = history
    record_history(history, "get_work", {"work_id": work})
    restrict(history)
    direct_controls(history)
    assert_projected(inspect(history))
    # Restore precisely the missing historical field traversal, leaving the
    # authorization policy and other aggregate projections in operation.
    monkeypatch.setitem(_INSPECTION_RESULTS, "get_work", {key: value for key, value in
        _INSPECTION_RESULTS["get_work"].items() if key != "sessions"})
    faulty = inspect(history)
    assert SECRET in json.dumps(faulty), "IV01_OMITTED_SESSION_PROJECTION_ACTIVATED"
    with pytest.raises(AssertionError, match="^IV01_HISTORICAL_RECORD_SCOPE"):
        assert_projected(faulty)
    direct_controls(history)


def test_unrelated_history_projection_error_is_not_detection(history, monkeypatch):
    app, work, _, _ = history
    record_history(history, "get_work", {"work_id": work})
    restrict(history)

    def unavailable(*args, **kwargs):
        raise TimeoutError("unrelated projection dependency")

    monkeypatch.setattr(app, "_inspection_result", unavailable)
    with pytest.raises(TimeoutError, match="unrelated projection dependency"):
        assert_projected(inspect(history))


@pytest.mark.parametrize("change", ["update", "delete"])
def test_current_work_rechecks_nested_record_changes_during_authorization(history, change):
    app, work, sessions, _ = history
    original_guardrail = app.guardrail
    changed = []

    class ChangingPolicy:
        def evaluate(self, action, context):
            if context.get("inspection", {}).get("kind") == "session" and not changed:
                with app.store.transaction() as tx:
                    row = tx.get("session", sessions["limited"]["id"])
                    if change == "delete":
                        tx.delete("session", row.key, row.version)
                    else:
                        tx.put("session", row.key, {**row.data, "specification": {"changed": True}}, row.version)
                changed.append(True)
            return original_guardrail.evaluate(action, context)

    app.guardrail = ChangingPolicy()
    with pytest.raises(Conflict):
        app.call("get_work", {"work_id": work})
    assert changed == [True]


def test_deleted_historical_session_is_omitted_without_losing_authorized_rows(history):
    app, work, sessions, _ = history
    record_history(history, "get_work", {"work_id": work})
    with app.store.transaction() as tx:
        row = tx.get("session", sessions["private"]["id"])
        tx.delete("session", row.key, row.version)
    result = inspect(history)
    returned = next(row for row in result["calls"] if row["action"] == "get_work")["result"]
    assert {row["id"] for row in returned["sessions"]} == {sessions["limited"]["id"]}


def test_arbitrary_evidence_payload_with_record_named_keys_remains_opaque(history):
    app, work, _, _ = history
    payload = {"sessions": [{"id": "not-a-session", "work_id": "not-a-work"}],
        "work": {"id": "payload-only"}, "result": {"action": "get_work", "sessions": []}}
    evidence = app.call("record_result", {"work_id": work, "result": payload}, actor="limited")["evidence"]
    record_history(history, "get_work", {"work_id": work})
    restrict(history)
    observed = inspect(history)
    work_result = next(row for row in observed["calls"] if row["action"] == "get_work")["result"]
    assert next(row for row in work_result["evidence"] if row["id"] == evidence["id"])["result"] == payload


def test_selected_message_and_historical_message_project_explicit_actor_policy(history):
    app, work, _, _ = history
    for actor, marker in (("limited", PUBLIC), ("private", SECRET)):
        actor_row = next(row for row in app.call("list_actors", {})["actors"] if row["id"] == actor)
        app.call("update_actor", {"actor_id": actor, "expected_version": actor_row["version"],
            "changes": {"metadata": {"marker": marker}}})
    message = app.call("send_message", {"work_id": work, "destination": {"actor": "limited"},
        "body": "public"})["message"]["id"]
    record_history(history, "get_delivery", {"message_id": message})
    app.guardrail = RuleGuardrail({"default": "deny", "rules": [
        {"actors": ["limited"], "actions": ["get_delivery", "native_observations"],
         "where": {"work_id": work}, "effect": "allow"},
        {"actors": ["limited"], "actions": ["get_delivery", "native_observations"],
         "where": {"inspection.kind": "actor"}, "effect": "allow"},
        {"actors": ["limited"], "actions": ["get_delivery", "native_observations"],
         "where": {"inspection.kind": "actor", "record.id": "private"}, "effect": "deny"}]})
    result = app.call("get_delivery", {"message_id": message}, actor="limited")
    selected = result["message"]
    assert {row["id"] for row in selected["routing_snapshot"]["actors"]} == {"limited"}
    assert SECRET not in json.dumps(selected)
    assert PUBLIC in json.dumps(selected)
    assert selected["inspection_redacted"] is True
    historical = next(row for row in inspect(history)["calls"] if row["action"] == "get_delivery")
    assert SECRET not in json.dumps(historical["result"]["message"])


def test_provider_output_with_record_named_keys_remains_opaque(history):
    from tests.hexagonal.test_application import Provider

    app, work, _, _ = history
    payload = {"sessions": [{"id": "provider-only", "work_id": "not-an-application-work"}],
        "work": {"id": "arbitrary"}, "operation": {"id": "not-a-durable-operation"}}
    app.providers["opaque"] = Provider(callback=lambda *args: deepcopy(payload))
    recorded = record_history(history, "invoke", {"work_id": work, "capability": "opaque/change",
        "arguments": {"value": 1}, "idempotency_key": "opaque-output"})
    operation_id = recorded["operation_id"]
    record_history(history, "get_operation", {"operation_id": operation_id})
    restrict(history)
    result = inspect(history)
    invocation = next(row for row in result["calls"] if row["action"] == "invoke")["result"]["invocation"]
    operation = next(row for row in result["calls"] if row["action"] == "get_operation")["result"]["operation"]
    assert invocation["result"] == payload
    assert operation["external_result"] == operation["result"]["invocation"]["result"] == payload


def test_current_acceptance_rechecks_contribution_change_during_authorization(history):
    app, work, _, _ = history
    original_guardrail = app.guardrail
    changed = []

    class ChangingPolicy:
        def evaluate(self, action, context):
            if context.get("inspection", {}).get("kind") == "contribution" and not changed:
                with app.store.transaction() as tx:
                    row = tx.get("contribution", context["record"]["id"])
                    tx.put("contribution", row.key, {**row.data, "goal": "changed after inspection"}, row.version)
                changed.append(True)
            return original_guardrail.evaluate(action, context)

    app.guardrail = ChangingPolicy()
    with pytest.raises(Conflict):
        app.call("get_acceptance", {"work_id": work})
    assert changed == [True]


def test_historical_dispatch_and_session_operation_project_returned_records(history):
    app, work, sessions, message = history
    dispatch = record_history(history, "dispatch_deliveries", {"message_id": message})
    attempt = dispatch["deliveries"][0]["attempt"]
    operation_id = sessions["limited"]["start_operation_id"]
    record_history(history, "get_operation", {"operation_id": operation_id})
    app.guardrail = RuleGuardrail({"rules": [
        {"actors": ["limited"], "actions": ["native_observations"],
         "where": {"inspection.kind": "delivery_attempt", "record.id": attempt["id"]}, "effect": "deny"},
        {"actors": ["limited"], "actions": ["native_observations"],
         "where": {"inspection.kind": "session", "record.id": sessions["limited"]["id"]}, "effect": "deny"}]})
    result = inspect(history)
    dispatch_call = next(row for row in result["calls"] if row["action"] == "dispatch_deliveries")
    operation_call = next(row for row in result["calls"] if row["action"] == "get_operation")
    assert dispatch_call["result"]["deliveries"][0]["attempt"] is None
    assert dispatch_call["result"]["deliveries"][0]["delivery"]["message_id"] == message
    assert operation_call["result"]["operation"]["result"]["session"] is None
    assert dispatch_call["inspection_redacted"] and operation_call["inspection_redacted"]


@pytest.mark.parametrize("action,kind", [("get_work", "work"), ("get_delivery", "message")])
def test_denied_selected_record_rejects_instead_of_returning_empty_success(history, action, kind):
    app, work, _, message = history
    args = {"work_id": work} if kind == "work" else {"message_id": message}
    app.guardrail = RuleGuardrail({"rules": [{"actors": ["limited"], "actions": [action],
        "where": {"inspection.kind": kind}, "effect": "deny"}]})
    with pytest.raises(OrchestrationError) as denied:
        app.call(action, args, actor="limited")
    assert denied.value.code == "denied"
    app.guardrail = RuleGuardrail()
    assert app.call(action, args, actor="limited")[kind]["id"] == args[f"{kind}_id"]


def provider_history(history, measured=False):
    from tests.hexagonal.test_application import Provider
    from tests.hexagonal.test_work_acceptance import BINDINGS, CANDIDATE, MeasuringProvider
    from xorchestrate.model import Capability

    app, work, _, _ = history
    opaque = {"session": {"id": "provider-only"}, "operation": {"id": "payload-only"},
        "result": {"sessions": [{"id": "arbitrary"}]}}

    class SelectedProvider(MeasuringProvider if measured else Provider):
        def __init__(self, marker):
            super().__init__()
            self.marker = marker
            self.discoveries = 0

        def discover(self):
            self.discoveries += 1
            return [Capability("measure" if measured else "change", "Selected provider",
                {"type": "object"}, {"visibility": self.marker})]

        def invoke(self, name, arguments, context):
            result = super().invoke(name, arguments, context) if measured else {}
            if not measured:
                self.calls.append(deepcopy(arguments))
            return {**result, "marker": self.marker, "opaque": deepcopy(opaque)}

    app.providers = {name: SelectedProvider(marker) for name, marker in (("private", SECRET), ("public", PUBLIC))}
    if measured:
        app.call("set_requirements", {"work_id": work, "expected_revision": 1, "objective": "both",
            "outcomes": {"private": "private", "public": "public"}, "checks": {name: {
                "capability": name + "/measure", "outcomes": [name], "bindings": BINDINGS}
                for name in app.providers}})
        app.call("set_candidate", {"work_id": work, "expected_revision": 0, "candidate": CANDIDATE})
    original = {}
    action = "run_measurement" if measured else "invoke"
    for name in app.providers:
        args = ({"work_id": work, "check_id": name, "measurement_key": name} if measured else
            {"work_id": work, "capability": name + "/change", "arguments": {}, "idempotency_key": name + "-invoke"})
        original[name] = record_history(history, action, args, name + "-" + action)
        operation_id = original[name]["measurement"]["operation_id"] if measured else original[name]["operation_id"]
        record_history(history, "get_operation", {"operation_id": operation_id}, name + "-operation")
    if measured:
        for read in ("get_work", "get_acceptance"):
            record_history(history, read, {"work_id": work})
    return original, opaque


def restrict_provider(history, selector):
    history[0].guardrail = RuleGuardrail({"rules": [{"actors": ["limited"],
        "actions": ["native_observations", "get_operation", "get_work", "get_acceptance"],
        **selector, "effect": "deny"}]})


@pytest.mark.parametrize("mode", ["provider", "capability", "descriptor"])
@pytest.mark.parametrize("measured", [False, True], ids=["invocation", "measurement"])
def test_provider_bound_history_uses_saved_scope_without_reinvoking(history, mode, measured):
    app, work, _, _ = history
    original, opaque = provider_history(history, measured)
    before = inspect(history)
    # Other fixture records deliberately carry the same marker. Compare only
    # the typed callback/provider results rather than arbitrary user evidence.
    def results(observed):
        return [row["result"] for row in observed["calls"] if row["action"] in {"invoke", "run_measurement", "get_operation"}]
    assert SECRET in json.dumps(results(before)) and PUBLIC in json.dumps(results(before))
    counts = {name: (provider.discoveries, len(provider.calls)) for name, provider in app.providers.items()}
    capability = "private/measure" if measured else "private/change"
    selector = {"provider": {"providers": ["private"]}, "capability": {"capabilities": [capability]},
        "descriptor": {"where": {"capability.annotations.visibility": SECRET}}}[mode]
    restrict_provider(history, selector)
    operation_id = original["private"]["measurement"]["operation_id"] if measured else original["private"]["operation_id"]
    with pytest.raises(OrchestrationError) as denied:
        app.call("get_operation", {"operation_id": operation_id}, actor="limited")
    assert denied.value.code == "denied"
    visible = results(inspect(history))
    assert SECRET not in json.dumps(visible), "IV01_PROVIDER_SCOPE"
    assert PUBLIC in json.dumps(visible), "IV01_PUBLIC_PROVIDER_RETAINED"
    returned = next(item["measurement" if measured else "invocation"] for item in visible
        if ("measurement" if measured else "invocation") in item)
    assert returned["raw_response" if measured else "result"]["opaque"] == opaque
    if measured:
        for action in ("get_work", "get_acceptance"):
            rows = app.call(action, {"work_id": work}, actor="limited")["measurements"]
            assert len(rows) == 1 and rows[0]["raw_response"]["marker"] == PUBLIC
        for row in inspect(history)["calls"]:
            if row["action"] in {"get_work", "get_acceptance"}:
                assert [m["raw_response"]["marker"] for m in row["result"]["measurements"]] == [PUBLIC]
    app.guardrail = RuleGuardrail()
    assert inspect(history) == before, "IV01_RESTORED_PROVIDER_HISTORY_EXACT"
    assert counts == {name: (provider.discoveries, len(provider.calls)) for name, provider in app.providers.items()}


@pytest.mark.parametrize("selector", [{"providers": ["private"]},
    {"capabilities": ["private/change"]}, {"where": {"capability.annotations.visibility": SECRET}}],
    ids=["provider", "capability", "descriptor"])
def test_historical_capability_catalog_has_current_descriptor_scope(history, selector):
    app, _, _, _ = history
    provider_history(history)
    for provider in (None, "private", "public"):
        record_history(history, "capabilities", {} if provider is None else {"provider": provider}, provider or "catalog")
    before = inspect(history)
    counts = {name: provider.discoveries for name, provider in app.providers.items()}
    restrict_provider(history, selector)
    catalogs = [row["result"]["capabilities"] for row in inspect(history)["calls"] if row["action"] == "capabilities"]
    assert catalogs and all(item["name"] == "public/change" for rows in catalogs for item in rows)
    assert any(rows for rows in catalogs), "IV01_AUTHORIZED_CATALOG_RETAINED"
    app.guardrail = RuleGuardrail()
    assert inspect(history) == before
    assert counts == {name: provider.discoveries for name, provider in app.providers.items()}


def test_direct_operation_projects_same_nested_result_as_history(history):
    app, _, sessions, _ = history
    args = {"operation_id": sessions["limited"]["start_operation_id"]}
    original = record_history(history, "get_operation", args)
    app.guardrail = RuleGuardrail({"rules": [{"actors": ["limited"],
        "actions": ["get_operation", "native_observations"],
        "where": {"inspection.kind": "session", "record.id": sessions["limited"]["id"]}, "effect": "deny"}]})
    direct = app.call("get_operation", args, actor="limited")
    historical = next(row["result"] for row in inspect(history)["calls"] if row["action"] == "get_operation")
    for result in (direct, historical):
        assert result["operation"]["result"]["session"] is None, "IV01_OPERATION_NESTED_SCOPE"
        assert result["operation"]["id"] == args["operation_id"]
        assert result["operation"]["external_result"] == original["operation"]["external_result"]
    app.guardrail = RuleGuardrail({"rules": [{"actors": ["limited"], "actions": ["get_operation"],
        "where": {"inspection.kind": "operation"}, "effect": "deny"}]})
    with pytest.raises(OrchestrationError) as denied:
        app.call("get_operation", args, actor="limited")
    assert denied.value.code == "denied", "IV01_SELECTED_OPERATION_DENIAL"
    app.guardrail = RuleGuardrail()
    assert app.call("get_operation", args, actor="limited") == original


def test_known_bad_provider_binding_omission_is_detected(history, monkeypatch):
    app, _, _, _ = history
    original, _ = provider_history(history)
    restrict_provider(history, {"providers": ["private"]})

    def assert_scope():
        callbacks = [row["result"] for row in inspect(history)["calls"] if row["action"] == "invoke"]
        assert SECRET not in json.dumps(callbacks), "IV01_PROVIDER_SCOPE"
        assert PUBLIC in json.dumps(callbacks)

    assert_scope()
    previous = app._inspection_context

    def omit_binding(actor, kind, record, cache=None):
        context = previous(actor, kind, record, cache)
        if kind == "invocation":
            for key in ("provider", "capability", "operation"):
                context.pop(key, None)
        return context

    monkeypatch.setattr(app, "_inspection_context", omit_binding)
    monkeypatch.setattr(app, "_inspection_execution", lambda actor, payload, cache, executing_actor:
        app._inspection_basis(actor, payload["arguments"], cache))
    with pytest.raises(AssertionError, match="^IV01_PROVIDER_SCOPE(?:\\n|$)"):
        assert_scope()
    with pytest.raises(OrchestrationError) as denied:
        app.call("get_operation", {"operation_id": original["private"]["operation_id"]}, actor="limited")
    assert denied.value.code == "denied", "IV01_PROVIDER_CONTROL_STILL_DENIED"


def test_unrelated_provider_scope_error_is_not_detection(history, monkeypatch):
    app, _, _, _ = history
    provider_history(history)
    restrict_provider(history, {"providers": ["private"]})

    def unavailable(*args, **kwargs):
        raise TimeoutError("unrelated provider scope dependency")

    monkeypatch.setattr(app, "_inspection_execution", unavailable)
    with pytest.raises(TimeoutError, match="unrelated provider scope dependency"):
        callbacks = [row["result"] for row in inspect(history)["calls"] if row["action"] == "invoke"]
        assert SECRET not in json.dumps(callbacks), "IV01_PROVIDER_SCOPE"


@pytest.mark.parametrize("measured", [False, True], ids=["invocation", "measurement"])
@pytest.mark.parametrize("inspector", ["limited", "owner"])
def test_requested_native_phase_uses_original_executor_saved_descriptor(history, measured, inspector):
    app, _, sessions, _ = history
    provider_history(history, measured)
    args = {"session_id": sessions["limited"]["id"]}
    before = app.call("native_observations", args, actor=inspector)
    app.guardrail = RuleGuardrail({"rules": [{"actors": [inspector], "actions": ["native_observations"],
        "where": {"capability.annotations.visibility": SECRET}, "effect": "deny"}]})
    visible = app.call("native_observations", args, actor=inspector)
    action = "run_measurement" if measured else "invoke"
    phases = [row["observation"] for row in visible["observations"] if row["observation"].get("action") == action]
    assert {row["phase"] for row in phases} == {"requested", "completed"}
    assert all(row["call_id"] == "public-" + action for row in phases), "IV01_REQUESTED_DESCRIPTOR_SCOPE"
    assert all(row["call_id"] != "private-" + action for row in visible["calls"])
    app.guardrail = RuleGuardrail()
    assert app.call("native_observations", args, actor=inspector) == before


@pytest.mark.parametrize("measured", [False, True], ids=["invocation", "measurement"])
@pytest.mark.parametrize("selector", ["provider", "capability"])
def test_conflicting_operation_key_cannot_supply_another_requests_inspection_scope(history, measured, selector):
    app, work, sessions, _ = history
    provider_history(history, measured)
    action = "run_measurement" if measured else "invoke"
    args = ({"work_id": work, "check_id": "private", "measurement_key": "public"} if measured else
        {"work_id": work, "capability": "private/change", "arguments": {}, "idempotency_key": "public-invoke"})
    with pytest.raises(Conflict):
        record_history(history, action, args, "conflicting-private")
    before = inspect(history)
    assert any(row["call_id"] == "conflicting-private" and row["status"] == "unknown" for row in before["calls"])
    selected = {"providers": ["private"]} if selector == "provider" else {
        "capabilities": ["private/measure" if measured else "private/change"]}
    app.guardrail = RuleGuardrail({"rules": [{"actors": ["owner"], "actions": ["native_observations"],
        **selected, "effect": "deny"}]})
    visible = app.call("native_observations", {"session_id": sessions["limited"]["id"]}, actor="owner")
    assert all(row["call_id"] != "conflicting-private" for row in visible["calls"]), "IV01_CONFLICTING_KEY_SCOPE"
    assert all(row["observation"].get("call_id") != "conflicting-private" for row in visible["observations"])
    assert any(row["call_id"] == "public-" + action for row in visible["calls"])
    app.guardrail = RuleGuardrail()
    assert inspect(history) == before
