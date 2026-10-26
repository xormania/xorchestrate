import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from tests.hexagonal.support.application import (
    Evaluator,
    Provider,
    ResumableSessions,
    SessionCompletionFailure,
    Sessions,
    WriteFailureStore,
    application,
    invoke,
    resume_setup,
)
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.model import Assessment, Capability, Conflict, OrchestrationError, Verdict


def test_work_lifecycle_has_free_form_roles_context_and_optimistic_updates(store):
    app = application(store)
    work = app.call("create_work", {"goal": "Add a secure Symfony feature", "inputs": {"stack": "declared by tools"}})["work"]
    worker = app.call("add_actor", {"actor_id": "builder", "roles": ["feature-expert", "reviewer"]})["actor"]
    assignment = app.call("assign", {"work_id": work["id"], "actor_id": worker["id"], "role": "independent"})["assignment"]
    assert app.call("get_work", {"work_id": work["id"]})["assignments"] == [assignment]
    updated = app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {"status": "awaiting-project-tool"}})["work"]
    assert updated["status"] == "awaiting-project-tool"
    with pytest.raises(Conflict):
        app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": {"status": "done"}})
    assert app.call("get_work", {"work_id": work["id"]})["work"] == updated
    app.call("remove_assignment", {"assignment_id": assignment["id"], "expected_version": assignment["version"]})
    assert not app.call("get_work", {"work_id": work["id"]})["assignments"]
    assert [event["seq"] for event in app.call("events", {})["events"]] == list(range(1, 6))


@pytest.mark.parametrize("kind,action", [("assignment", "assign"), ("relation", "relate")])
def test_stale_removal_cannot_delete_recreated_assignment_or_relation(store, kind, action):
    app = application(store)
    work = app.call("create_work", {"goal": "Feature"})["work"]
    for actor_id in ["author", "reviewer"]:
        app.call("add_actor", {"actor_id": actor_id})
    fields = ({"work_id": work["id"], "actor_id": "reviewer", "role": "review"}
        if kind == "assignment" else {"source": "author", "target": "reviewer", "relationship": "consults"})
    original_args = {**fields, "metadata": {"request": "original"}}
    original = app.call(action, original_args)[kind]
    assert app.call(action, original_args)[kind] == original
    stale_removal = {f"{kind}_id": original["id"], "expected_version": original["version"]}
    app.call(f"remove_{kind}", stale_removal)
    replacement_args = {**fields, "metadata": {"request": "replacement"}}
    replacement = app.call(action, replacement_args)[kind]
    assert replacement["id"] != original["id"]
    with pytest.raises(OrchestrationError) as error:
        app.call(f"remove_{kind}", stale_removal)
    assert error.value.code in {"conflict", "not_found"}
    assert app.call(action, replacement_args)[kind] == replacement
    assert replacement["metadata"] == {"request": "replacement"}
    with store.transaction() as tx:
        assert [record.key for record in tx.list(kind)] == [replacement["id"]]


def test_messages_route_over_declared_relationship_and_acknowledge_per_recipient(store):
    app = application(store)
    for actor_id in ["author", "security", "ux"]:
        app.call("add_actor", {"actor_id": actor_id})
    for target in ["security", "ux"]:
        app.call("relate", {"source": "author", "target": target, "relationship": "consults"})
    message = app.call("send_message", {
        "destination": {"relationship": {"name": "consults", "direction": "outgoing"}},
        "body": {"question": "Check this feature"},
    }, actor="author")["message"]
    assert set(message["recipients"]) == {"security", "ux"}
    app.call("acknowledge", {"message_id": message["id"]}, actor="security")
    assert app.call("inbox", {}, actor="security")["messages"] == []
    assert len(app.call("inbox", {}, actor="ux")["messages"]) == 1
    with pytest.raises(OrchestrationError, match="recipient"):
        app.call("acknowledge", {"message_id": message["id"]}, actor="author")


def test_discovery_namespaces_capabilities_without_core_switches():
    app = application(provider=Provider())
    app.providers["another"] = Provider()
    assert {c["name"] for c in app.call("capabilities", {})["capabilities"]} == {"tools/change", "another/change"}
    result = app.call("invoke", {"capability": "another/change", "arguments": {"value": 9}, "idempotency_key": "other"})
    assert result["invocation"]["result"] == {"value": 9}


def test_guardrail_denies_provider_before_discovery_and_external_effect():
    provider = Provider()
    app = application(provider=provider, guardrail=RuleGuardrail({"rules": [{"actions": ["capabilities"], "providers": ["tools"], "effect": "deny"}]}))
    with pytest.raises(OrchestrationError) as error:
        invoke(app)
    assert error.value.code == "denied"
    assert provider.discoveries == 0 and provider.calls == []


def test_invocation_denial_prevents_effect_after_authorized_discovery():
    provider = Provider()
    app = application(provider=provider, guardrail=RuleGuardrail({"rules": [{"actions": ["invoke"], "capabilities": ["tools/*"], "effect": "deny"}]}))
    with pytest.raises(OrchestrationError) as error:
        invoke(app)
    assert error.value.code == "denied"
    assert provider.discoveries == 1 and provider.calls == []


def test_metadata_can_authorize_invocation_under_default_deny():
    class AnnotatedProvider(Provider):
        read_only = True
        def discover(self):
            return [Capability(cap.name, cap.description, cap.input_schema, {"readOnlyHint": self.read_only}) for cap in super().discover()]
    provider = AnnotatedProvider()
    app = application(provider=provider, guardrail=RuleGuardrail({"default": "deny", "rules": [
        {"actions": ["capabilities"], "providers": ["tools"], "effect": "allow"},
        {"actions": ["invoke"], "where": {"capability.annotations.readOnlyHint": True}, "effect": "allow"},
    ]}))
    assert invoke(app)["invocation"]["result"] == {"value": 1}
    provider.read_only = False
    with pytest.raises(OrchestrationError) as error:
        invoke(app, key="write")
    assert error.value.code == "denied"
    assert len(provider.calls) == 1


def test_capability_listing_authorizes_each_provider_with_its_name():
    allowed, denied = Provider(), Provider()
    app = application(provider=allowed, guardrail=RuleGuardrail({"default": "deny", "rules": [
        {"actions": ["capabilities"], "providers": ["tools"], "effect": "allow"},
    ]}))
    assert [item["name"] for item in app.call("capabilities", {})["capabilities"]] == ["tools/change"]
    app.providers["private"] = denied
    with pytest.raises(OrchestrationError) as error:
        app.call("capabilities", {"provider": "private"})
    assert error.value.code == "denied"
    assert denied.discoveries == 0
    assert [item["name"] for item in app.call("capabilities", {"provider": "tools"})["capabilities"]] == ["tools/change"]


def test_capability_schema_is_checked_before_invocation():
    provider = Provider()
    app = application(provider=provider)
    with pytest.raises(OrchestrationError):
        invoke(app, value="wrong type")
    assert not provider.calls
    with app.store.transaction() as tx:
        assert not tx.list("operation")


def test_results_supply_evidence_without_becoming_authority(store):
    provider = Provider(lambda name, args, context: {"guardrail": {"default": "allow"}, "roles": ["owner"]})
    class ObservingGuardrail:
        received = None
        def evaluate(self, action, context):
            if action == "invoke":
                self.received = context
            return Verdict(True)
    guardrail = ObservingGuardrail()
    app = application(store, provider=provider, guardrail=guardrail)
    work = app.call("create_work", {"goal": "Feature", "inputs": {"stack": "Symfony"}, "context": {"ux": "owner preference"}})["work"]
    app.call("record_result", {"work_id": work["id"], "result": {"observation": "current project"}})
    invoke(app, work_id=work["id"])
    received = guardrail.received
    assert received["inputs"] == {"stack": "Symfony"}
    assert received["context"] == {"ux": "owner preference"}
    assert received["evidence"][0]["result"] == {"observation": "current project"}
    assert set(provider.calls[0][2]) == {"actor", "work_id", "operation_id", "capability"}
    assert provider.calls[0][2]["capability"]["name"] == "change"
    assert not app.call("list_actors", {})["actors"]
    assert len(app.call("get_work", {"work_id": work["id"]})["evidence"]) == 2
    app.guardrail = RuleGuardrail({"default": "deny"})
    with pytest.raises(OrchestrationError) as error:
        invoke(app, work_id=work["id"])
    assert error.value.code == "denied"


def test_completed_operation_replays_offline_and_rejects_rebound_keys(store):
    provider = Provider()
    app = application(store, provider=provider)
    first = invoke(app)
    app.providers.clear()
    assert invoke(app) == first
    assert len(provider.calls) == 1 and provider.discoveries == 1
    with pytest.raises(Conflict):
        invoke(app, value=2)
    other_process = application(store)
    assert invoke(other_process) == first


@pytest.mark.parametrize("failure,status", [
    (TimeoutError("sensitive SDK request body"), "unknown"),
    (OrchestrationError("Confirmed rejection", "capability_failed", {"outcome": "failed"}), "failed"),
    (OrchestrationError("Unverified protocol response", "provider_protocol"), "unknown"),
])
def test_failed_or_unknown_effects_do_not_repeat(failure, status):
    def fail(*args):
        raise failure
    provider = Provider(fail)
    app = application(provider=provider)
    with pytest.raises(OrchestrationError) as error:
        invoke(app)
    assert error.value.details["outcome"] == status
    assert "sensitive" not in str(error.value)
    operation_id = error.value.details["operation_id"]
    operation = app.call("get_operation", {"operation_id": operation_id})["operation"]
    assert operation["status"] == status
    with pytest.raises(OrchestrationError):
        invoke(app)
    assert len(provider.calls) == 1


def test_interrupted_call_remains_unresolved_until_verified_reconciliation():
    def interrupt(*args):
        raise KeyboardInterrupt()
    provider = Provider(interrupt)
    app = application(provider=provider)
    with pytest.raises(KeyboardInterrupt):
        invoke(app)
    with app.store.transaction() as tx:
        operation = tx.list("operation")[0]
    assert operation.data["status"] == "pending"
    with pytest.raises(OrchestrationError) as error:
        invoke(app)
    assert error.value.code == "operation_unresolved"
    app.call("resolve_operation", {"operation_id": operation.key, "expected_version": operation.version,
        "outcome": "completed", "result": {"value": 1}, "reason": "Verified the external system"})
    assert invoke(app)["invocation"]["result"] == {"value": 1}
    assert len(provider.calls) == 1


def test_concurrent_duplicate_is_not_executed_and_provider_does_not_hold_transaction(store):
    entered, finish = threading.Event(), threading.Event()
    def blocked(*args):
        entered.set()
        assert finish.wait(5)
        return {"done": True}
    provider = Provider(blocked)
    app = application(store, provider=provider)
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(invoke, app)
        assert entered.wait(5)
        try:
            with pytest.raises(OrchestrationError) as error:
                invoke(app)
            assert error.value.code == "operation_unresolved"
            # This transaction would block if the provider were inside a store transaction.
            work = executor.submit(app.call, "create_work", {"goal": "independent"}).result(timeout=2)
            assert work["work"]["goal"] == "independent"
        finally:
            finish.set()
        assert future.result(timeout=2)["invocation"]["result"] == {"done": True}
    assert len(provider.calls) == 1


def test_completed_effect_survives_local_commit_failure_and_can_be_applied_once():
    provider = Provider()
    app = application(WriteFailureStore("completed"), provider=provider)
    with pytest.raises(OrchestrationError) as error:
        invoke(app)
    assert error.value.code == "result_unapplied"
    operation = app.call("get_operation", {"operation_id": error.value.details["operation_id"]})["operation"]
    assert operation["status"] == "completed_unapplied"
    assert operation["external_result"] == {"value": 1}
    assert "secret" not in operation["application_error"]
    app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"], "outcome": "completed", "reason": "Apply retained result"})
    assert invoke(app)["invocation"]["result"] == {"value": 1}
    assert len(provider.calls) == 1


def test_recording_failure_still_returns_external_outcome_and_operation_identity():
    def fail(*args):
        raise TimeoutError("secret token")
    app = application(WriteFailureStore("unknown"), provider=Provider(fail))
    with pytest.raises(OrchestrationError) as error:
        invoke(app)
    assert error.value.code == "operation_unknown"
    assert error.value.details["operation_id"]
    assert "secret" not in str(error.value.details)
    operation = app.call("get_operation", {"operation_id": error.value.details["operation_id"]})["operation"]
    assert operation["status"] == "pending"


def test_session_lifecycle_retains_handles_context_and_output(store):
    driver = Sessions()
    app = application(store, driver=driver)
    work = app.call("create_work", {"goal": "Feature", "inputs": {"stack": "Symfony"}})["work"]
    app.call("add_actor", {"actor_id": "builder"})
    started = app.call("start_session", {"driver": "agent", "actor_id": "builder", "work_id": work["id"],
        "specification": {"context": {"actor": "forged-owner"}, "prompt": "Implement feature"}, "idempotency_key": "start"})
    session_id = started["session"]["id"]
    trusted_context = driver.specifications[0]["context"]
    assert trusted_context["actor"] == "builder" and trusted_context["delegated_by"] == "owner"
    assert trusted_context["work_id"] == work["id"]
    assert set(trusted_context) == {"actor", "delegated_by", "work_id", "session_id"}
    assert trusted_context["session_id"] == session_id
    app.call("send_session", {"session_id": session_id, "message": {"text": "Begin"}, "idempotency_key": "send"})
    poll_args = {"session_id": session_id, "idempotency_key": "poll"}
    assert app.call("poll_session", poll_args)["output"] == [{"text": "Feature checked"}]
    app.call("poll_session", poll_args)
    stopped = app.call("stop_session", {"session_id": session_id, "idempotency_key": "stop"})
    assert stopped["session"]["status"] == "stopped"
    assert [call[0] for call in driver.calls] == ["send", "poll", "stop"]
    assert all(call[1]["handle"] == "owned-handle" for call in driver.calls)
    assert len(app.call("get_work", {"work_id": work["id"]})["evidence"]) == 2


def test_unknown_session_effect_blocks_overlapping_effect_and_reconciles():
    class UncertainDriver(Sessions):
        def send(self, session, message):
            raise TimeoutError()
    app = application(driver=UncertainDriver())
    session = app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})["session"]
    with pytest.raises(OrchestrationError) as error:
        app.call("send_session", {"session_id": session["id"], "message": {}, "idempotency_key": "send"})
    operation = app.call("get_operation", {"operation_id": error.value.details["operation_id"]})["operation"]
    polled = app.call("poll_session", {"session_id": session["id"], "idempotency_key": "inspect"})
    assert polled["session"]["status"] == "unknown"
    assert polled["session"]["pending_operation"] == operation["id"]
    with pytest.raises(Conflict):
        app.call("send_session", {"session_id": session["id"], "message": {}, "idempotency_key": "other-send"})
    app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"],
        "outcome": "completed", "result": {"accepted": True}, "reason": "Observed accepted turn"})
    assert app.call("stop_session", {"session_id": session["id"], "idempotency_key": "stop"})["session"]["status"] == "stopped"


def test_session_output_cannot_redirect_the_driver_handle():
    class MisleadingOutput(Sessions):
        def send(self, session, message):
            return {"backend": {"handle": "different-process"}, "actor": "owner"}
    app = application(driver=MisleadingOutput())
    session = app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})["session"]
    sent = app.call("send_session", {"session_id": session["id"], "message": {}, "idempotency_key": "send"})
    assert sent["output"]["backend"]["handle"] == "different-process"
    assert sent["session"]["backend"] == session["backend"]


@pytest.mark.parametrize("denied_action", ["send_session", "start_session"])
def test_resume_requires_source_control_and_destination_creation(store, denied_action):
    app, driver, source, source_work, destination_work, arguments = resume_setup(store)
    seen = {}
    class ResumeGuardrail:
        def evaluate(self, action, context):
            seen[action] = deepcopy(context)
            return Verdict(action != denied_action)
    app.guardrail = ResumeGuardrail()
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", arguments, actor="alice")
    assert error.value.code == "denied"
    assert driver.resumes == []
    assert seen["send_session"]["session_actor"] == "alice"
    assert seen["send_session"]["session"]["id"] == source["id"]
    assert seen["send_session"]["work"]["id"] == source_work["id"]
    if denied_action == "start_session":
        assert seen["start_session"]["session_actor"] == "bob"
        assert seen["start_session"]["work"]["id"] == destination_work["id"]
    with store.transaction() as tx:
        assert len(tx.list("session")) == 1


@pytest.mark.parametrize("cross_destination", [False, True])
def test_authorized_resume_preserves_source_provenance_and_uses_opaque_backend(store, cross_destination):
    app, driver, source, source_work, destination_work, arguments = resume_setup(store)
    if not cross_destination:
        arguments.update({"actor_id": "alice", "work_id": source_work["id"]})
    app.guardrail = RuleGuardrail({"default": "deny", "rules": [
        {"actions": ["send_session"], "actors": ["alice"], "where": {"session_actor": "alice", "work.id": source_work["id"]}, "effect": "allow"},
        {"actions": ["start_session"], "actors": ["alice"], "where": {"session_actor": arguments["actor_id"], "work.id": arguments["work_id"]}, "effect": "allow"},
    ]})
    result = app.call("start_session", arguments, actor="alice")
    assert result["session"]["actor_id"] == arguments["actor_id"]
    assert result["session"]["work_id"] == arguments["work_id"]
    assert driver.resumes[0][0] == source["backend"]
    assert driver.resumes[0][1]["context"] == {"actor": arguments["actor_id"], "delegated_by": "alice", "work_id": arguments["work_id"], "session_id": result["session"]["id"]}
    provenance = {"session_id": source["id"], "version": source["version"], "start_operation_id": source["start_operation_id"]}
    assert result["session"]["resume_source"] == provenance
    with store.transaction() as tx:
        operation = tx.get("operation", result["operation_id"])
        assert operation.data["resume_source"] == provenance
        assert operation.data["external_result_origin"] == "adapter"


@pytest.mark.parametrize("raw_resume", [None, "native-conversation", {"identity": "forged"}])
def test_start_rejects_raw_resume_identity_in_specification(store, raw_resume):
    driver = ResumableSessions()
    app = application(store, driver=driver)
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", {"driver": "agent", "specification": {"resume": raw_resume}, "idempotency_key": "start"})
    assert error.value.code == "invalid_arguments"
    assert driver.specifications == [] and driver.resumes == []
    with store.transaction() as tx:
        assert tx.list("operation") == []


@pytest.mark.parametrize("problem,code", [("missing", "not_found"), ("driver", "driver_mismatch"), ("unsupported", "unsupported_operation")])
def test_invalid_resume_source_or_driver_fails_before_effect(store, problem, code):
    driver = Sessions() if problem == "unsupported" else ResumableSessions()
    app, driver, source, source_work, destination_work, arguments = resume_setup(store, driver)
    if problem == "missing":
        arguments["resume_session_id"] = "missing-source"
    elif problem == "driver":
        app.sessions["other"] = ResumableSessions()
        arguments["driver"] = "other"
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", arguments, actor="alice")
    assert error.value.code == code
    with store.transaction() as tx:
        assert len(tx.list("session")) == 1 and len(tx.list("operation")) == 1


def test_resume_treats_a_verified_empty_backend_as_opaque_driver_data(store):
    class EmptyBackend(ResumableSessions):
        def start(self, specification):
            return {}
    app, driver, source, source_work, destination_work, arguments = resume_setup(store, EmptyBackend())
    resumed = app.call("start_session", arguments, actor="alice")["session"]
    assert driver.resumes[0][0] == {}
    assert resumed["resume_source"]["session_id"] == source["id"]


@pytest.mark.parametrize("problem", ["unmarked", "no_start_id", "empty_backend"])
def test_resume_requires_verified_adapter_receipt(store, problem):
    app, driver, source, source_work, destination_work, arguments = resume_setup(store)
    with store.transaction() as tx:
        if problem == "unmarked":
            record = tx.get("operation", source["start_operation_id"])
            data = {key: value for key, value in record.data.items() if key != "external_result_origin"}
        else:
            record = tx.get("session", source["id"])
            data = dict(record.data)
            if problem == "no_start_id":
                data.pop("start_operation_id")
            else:
                data["backend"] = {}
        tx.put(record.kind, record.key, data, record.version)
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", arguments, actor="alice")
    assert error.value.code == "unverified_session_result"
    assert driver.resumes == []


@pytest.mark.parametrize("interrupted", [False, True])
def test_resume_cannot_bypass_source_pending_or_unknown_operation(store, interrupted):
    class UncertainSessions(ResumableSessions):
        def send(self, session, message):
            raise KeyboardInterrupt() if interrupted else TimeoutError()
    app, driver, source, source_work, destination_work, arguments = resume_setup(store, UncertainSessions())
    with pytest.raises(KeyboardInterrupt if interrupted else OrchestrationError):
        app.call("send_session", {"session_id": source["id"], "message": {}, "idempotency_key": "uncertain"}, actor="alice")
    with pytest.raises(Conflict, match="pending or unresolved"):
        app.call("start_session", arguments, actor="alice")
    assert driver.resumes == []


@pytest.mark.parametrize("denied_action", ["send_session", "start_session"])
def test_resume_replay_reauthorizes_source_and_destination(store, denied_action):
    app, driver, source, source_work, destination_work, arguments = resume_setup(store)
    result = app.call("start_session", arguments, actor="alice")
    app.guardrail = RuleGuardrail({"rules": [{"actions": [denied_action], "effect": "deny"}]})
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", arguments, actor="alice")
    assert error.value.code == "denied"
    assert len(driver.resumes) == 1
    app.guardrail = RuleGuardrail()
    app.sessions.clear()
    assert app.call("start_session", arguments, actor="alice") == result


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("changed", ["session", "source_work", "source_evidence", "destination_work", "receipt"])
def test_resume_verifies_source_and_destination_snapshots_before_effect_or_replay(store, replay, changed):
    app, driver, source, source_work, destination_work, arguments = resume_setup(store)
    if replay:
        app.call("start_session", arguments, actor="alice")
    class ChangingGuardrail:
        def evaluate(self, action, context):
            if action == "start_session":
                with store.transaction() as tx:
                    if changed == "source_evidence":
                        tx.put("evidence", "new-source-observation", {"work_id": source_work["id"], "result": "Changed safety assessment"})
                    else:
                        kind, key = {
                            "session": ("session", source["id"]), "source_work": ("work", source_work["id"]),
                            "destination_work": ("work", destination_work["id"]), "receipt": ("operation", source["start_operation_id"]),
                        }[changed]
                        current = tx.get(kind, key)
                        tx.put(kind, key, {**current.data, "changed": True}, current.version)
            return Verdict(True)
    app.guardrail = ChangingGuardrail()
    with pytest.raises(Conflict):
        app.call("start_session", arguments, actor="alice")
    assert len(driver.resumes) == int(replay)


@pytest.mark.parametrize("supply_result", [False, True])
def test_unknown_start_cannot_be_bound_using_caller_reconciliation_result(store, supply_result):
    class UncertainStart(Sessions):
        def start(self, specification):
            raise TimeoutError()
    app = application(store, driver=UncertainStart())
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})
    operation = app.call("get_operation", {"operation_id": error.value.details["operation_id"]})["operation"]
    arguments = {"operation_id": operation["id"], "expected_version": operation["version"], "outcome": "completed", "reason": "Claimed identity"}
    if supply_result:
        arguments["result"] = {"native_id": "someone-elses-thread", "external_result_origin": "adapter"}
    with pytest.raises(OrchestrationError) as error:
        app.call("resolve_operation", arguments)
    assert error.value.code == "unverified_session_result"
    session = app.call("list_sessions", {})["sessions"][0]
    assert session["backend"] == {} and session["status"] == "unknown"
    app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"], "outcome": "failed", "reason": "Abandon unverified start"})
    assert app.call("list_sessions", {})["sessions"][0]["status"] == "failed"


def test_retained_start_receipt_can_reconcile_without_caller_backend_override(store):
    wrapper = SessionCompletionFailure(store)
    app, driver, source, source_work, destination_work, arguments = resume_setup(wrapper)
    arguments.pop("actor_id")  # Destination principal is the original caller, alice.
    wrapper.armed = True
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", arguments, actor="alice")
    assert error.value.code == "result_unapplied"
    operation = app.call("get_operation", {"operation_id": error.value.details["operation_id"]})["operation"]
    assert operation["external_result_origin"] == "adapter"
    reconciliation = {"operation_id": operation["id"], "expected_version": operation["version"], "outcome": "completed", "reason": "Apply adapter receipt"}
    with pytest.raises(OrchestrationError) as error:
        app.call("resolve_operation", {**reconciliation, "result": {"handle": "forged"}}, actor="bob")
    assert error.value.code == "unverified_session_result"
    authorizations = []
    class ReconciliationGuardrail:
        def evaluate(self, action, context):
            if action in {"send_session", "start_session", "resolve_operation"}:
                assert context["actor"] == "bob"
                assert context["session_actor"] == "alice"
                assert context["work_id"] == (source_work["id"] if action == "send_session" else destination_work["id"])
                authorizations.append(action)
            return Verdict(True)
    app.guardrail = ReconciliationGuardrail()
    resolved = app.call("resolve_operation", reconciliation, actor="bob")["operation"]
    assert authorizations == ["send_session", "start_session", "resolve_operation"]
    assert resolved["result"]["session"]["actor_id"] == "alice"
    assert resolved["result"]["session"]["backend"] == operation["external_result"]
    assert resolved["external_result_origin"] == "adapter"
    assert len(driver.resumes) == 1


def test_resume_reconciliation_cannot_bypass_revoked_source_permission(store):
    wrapper = SessionCompletionFailure(store)
    app, driver, source, source_work, destination_work, arguments = resume_setup(wrapper)
    wrapper.armed = True
    with pytest.raises(OrchestrationError) as error:
        app.call("start_session", arguments, actor="alice")
    operation = app.call("get_operation", {"operation_id": error.value.details["operation_id"]})["operation"]
    app.guardrail = RuleGuardrail({"rules": [{"actions": ["send_session"], "effect": "deny"}]})
    with pytest.raises(OrchestrationError) as error:
        app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"], "outcome": "completed", "reason": "Rebind"})
    assert error.value.code == "denied"
    with store.transaction() as tx:
        assert tx.get("operation", operation["id"]).data["status"] == "completed_unapplied"


def test_reconciliation_policy_receives_original_work_and_capability_context():
    def fail(*args):
        raise TimeoutError()
    app = application(provider=Provider(fail))
    work = app.call("create_work", {"goal": "Feature"})["work"]
    with pytest.raises(OrchestrationError) as error:
        invoke(app, work_id=work["id"])
    operation = app.call("get_operation", {"operation_id": error.value.details["operation_id"]})["operation"]
    class ReconciliationGuardrail:
        def evaluate(self, action, context):
            if action == "resolve_operation":
                assert context["work"]["id"] == work["id"]
                assert context["capability"]["name"] == "tools/change"
                assert context["operation"]["actor"] == "owner"
                return Verdict(False, "Manual reconciliation is restricted")
            return Verdict(True)
    app.guardrail = ReconciliationGuardrail()
    with pytest.raises(OrchestrationError) as error:
        app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"],
            "outcome": "completed", "result": {"value": 1}, "reason": "Claimed result"})
    assert error.value.code == "denied"


def test_changed_authorization_context_prevents_effect_but_unrelated_actor_does_not():
    provider = Provider()
    app = application(provider=provider)
    work = app.call("create_work", {"goal": "Feature"})["work"]
    other = app.call("add_actor", {"actor_id": "unrelated", "roles": ["old-role"]})["actor"]
    class MutatingGuardrail:
        target = "actor"
        touched = False
        def evaluate(self, action, context):
            if action == "invoke" and not self.touched:
                self.touched = True
                with app.store.transaction() as tx:
                    record = tx.get(self.target, work["id"] if self.target == "work" else other["id"])
                    tx.put(self.target, record.key, {**record.data, "note": "changed"}, record.version)
            return Verdict(True)
    guardrail = MutatingGuardrail()
    app.guardrail = guardrail
    invoke(app, key="unrelated", work_id=work["id"])
    guardrail.target, guardrail.touched = "work", False
    with pytest.raises(Conflict, match="Authorization context"):
        invoke(app, key="relevant", work_id=work["id"])
    assert len(provider.calls) == 1


@pytest.mark.parametrize("mutation", ["add", "update", "delete"])
def test_changed_evidence_invalidates_authorization_before_external_effect(store, mutation):
    provider = Provider()
    app = application(store, provider=provider)
    work = app.call("create_work", {"goal": "Feature"})["work"]
    evidence = app.call("record_result", {"work_id": work["id"], "result": {"safe": True}})["evidence"]
    class ChangingEvidence:
        def evaluate(self, action, context):
            if action == "invoke":
                with store.transaction() as tx:
                    current = tx.get("evidence", evidence["id"])
                    if mutation == "add":
                        tx.put("evidence", "new-evidence", {"work_id": work["id"], "result": {"safe": False}})
                    elif mutation == "update":
                        tx.put("evidence", current.key, {**current.data, "result": {"safe": False}}, current.version)
                    else:
                        tx.delete("evidence", current.key, current.version)
                return Verdict(all(item["result"].get("safe") for item in context["evidence"]))
            return Verdict(True)
    app.guardrail = ChangingEvidence()
    with pytest.raises(Conflict, match="evidence changed"):
        invoke(app, work_id=work["id"])
    assert provider.calls == []


def test_other_work_evidence_does_not_invalidate_authorization(store):
    provider = Provider()
    app = application(store, provider=provider)
    work = app.call("create_work", {"goal": "Feature"})["work"]
    unrelated = app.call("create_work", {"goal": "Other feature"})["work"]
    class ChangingOtherWork:
        def evaluate(self, action, context):
            if action == "invoke":
                app.call("record_result", {"work_id": unrelated["id"], "result": {"safe": False}})
            return Verdict(True)
    app.guardrail = ChangingOtherWork()
    assert invoke(app, work_id=work["id"])["invocation"]["result"] == {"value": 1}


@pytest.mark.parametrize("subject", ["work", "operation", "session"])
def test_contextual_read_cannot_return_a_newly_private_revision(store, subject):
    app = application(store, provider=Provider(), driver=Sessions())
    work = app.call("create_work", {"goal": "Feature"})["work"]
    if subject == "work":
        action, arguments, target_id = "get_work", {"work_id": work["id"]}, work["id"]
    elif subject == "operation":
        invocation = invoke(app, work_id=work["id"])
        action, arguments, target_id = "get_operation", {"operation_id": invocation["operation_id"]}, invocation["operation_id"]
    else:
        started = app.call("start_session", {"driver": "agent", "specification": {}, "work_id": work["id"], "idempotency_key": "start"})
        action, arguments, target_id = "get_operation", {"operation_id": started["operation_id"]}, started["session"]["id"]
    class PrivatizingGuardrail:
        def evaluate(self, requested, context):
            if requested == action:
                with store.transaction() as tx:
                    current = tx.get(subject, target_id)
                    tx.put(subject, target_id, {**current.data, "private": True, "secret": "newly-private-data"}, current.version)
                return Verdict(not context[subject].get("private", False))
            return Verdict(True)
    app.guardrail = PrivatizingGuardrail()
    with pytest.raises(Conflict, match="Authorization context changed"):
        app.call(action, arguments)


def test_completed_replay_verifies_the_work_snapshot_authorized_for_read(store):
    app = application(store, provider=Provider())
    work = app.call("create_work", {"goal": "Feature"})["work"]
    invoke(app, work_id=work["id"])
    class PrivatizingGuardrail:
        def evaluate(self, action, context):
            if action == "invoke":
                with store.transaction() as tx:
                    current = tx.get("work", work["id"])
                    tx.put("work", current.key, {**current.data, "private": True}, current.version)
                return Verdict(not context["work"].get("private", False))
            return Verdict(True)
    app.guardrail = PrivatizingGuardrail()
    with pytest.raises(Conflict, match="Authorization context changed"):
        invoke(app, work_id=work["id"])


@pytest.mark.parametrize("action", ["list_work", "list_actors", "events", "get_work", "inbox", "list_sessions", "get_operation", "capabilities"])
@pytest.mark.parametrize("permission", ["roles", "metadata"])
def test_reads_reject_permission_revocation_after_authorization(store, action, permission):
    app = application(store, provider=Provider(), driver=Sessions())
    app.call("add_actor", {"actor_id": "reader", "roles": ["reader"], "metadata": {"read": True}})
    work = app.call("create_work", {"goal": "Private feature"})["work"]
    arguments = {"work_id": work["id"]} if action in {"get_work", "inbox", "list_sessions"} else {}
    if action == "get_operation":
        arguments = {"operation_id": invoke(app, work_id=work["id"])["operation_id"]}
    elif action == "inbox":
        app.call("send_message", {"destination": {"actor": "reader"}, "body": "Private message", "work_id": work["id"]})
    elif action == "list_sessions":
        app.call("start_session", {"driver": "agent", "specification": {}, "work_id": work["id"], "idempotency_key": "start"})
    selector = {"roles": ["reader"]} if permission == "roles" else {"where": {"actor_record.metadata.read": True}}
    policy = RuleGuardrail({"default": "deny", "rules": [{"actions": [action], "effect": "allow", **selector}]})
    class RevokingGuardrail:
        changed = False
        def evaluate(self, requested, context):
            decision = policy.evaluate(requested, context)
            if requested == action and not self.changed:
                self.changed = True
                with store.transaction() as tx:
                    current = tx.get("actor", "reader")
                    revoked = [] if permission == "roles" else {"read": False}
                    tx.put("actor", current.key, {**current.data, permission: revoked}, current.version)
            return decision
    app.guardrail = RevokingGuardrail()
    with pytest.raises(Conflict, match="Authorization context changed"):
        app.call(action, arguments, actor="reader")
    with pytest.raises(OrchestrationError) as error:
        app.call(action, arguments, actor="reader")
    assert error.value.code == "denied"


def test_capability_inventory_checks_permission_again_after_discovery(store):
    class RevokingProvider(Provider):
        def discover(self):
            with store.transaction() as tx:
                current = tx.get("actor", "reader")
                tx.put("actor", current.key, {**current.data, "roles": []}, current.version)
            return super().discover()
    provider = RevokingProvider()
    app = application(store, provider=provider)
    app.call("add_actor", {"actor_id": "reader", "roles": ["reader"]})
    app.guardrail = RuleGuardrail({"default": "deny", "rules": [{"actions": ["capabilities"], "roles": ["reader"], "effect": "allow"}]})
    with pytest.raises(Conflict, match="Authorization context changed"):
        app.call("capabilities", {}, actor="reader")
    assert provider.discoveries == 1


@pytest.mark.parametrize("confidence,outcome,expected_escalation", [(0.95, "proceed", False), (None, "proceed", True), (0.6, "proceed", True), (0.99, "borderline", True)])
def test_triage_escalates_uncertainty_and_remains_evidence(store, confidence, outcome, expected_escalation):
    triage = Evaluator(Assessment(outcome, confidence, "triage", ({"test": "observed"},)))
    heavy = Evaluator(Assessment("proceed", 0.99, "careful review"))
    app = application(store, evaluators={"jev": triage, "heavy": heavy}, assessment={"triage": "jev", "escalate": "heavy"})
    work = app.call("create_work", {"goal": "Feature"})["work"]
    assessment = app.call("assess", {"question": "Is the design sufficient?", "work_id": work["id"]})["assessment"]
    assert bool(heavy.calls) is expected_escalation
    assert assessment["unresolved"] is False
    assert app.call("get_work", {"work_id": work["id"]})["work"]["status"] == "open"
    assert app.call("list_actors", {}) == {"actors": []}
    assert len(app.call("get_work", {"work_id": work["id"]})["evidence"]) == 1


def test_unavailable_heavier_evaluator_preserves_uncertainty_instead_of_authorizing():
    triage = Evaluator(Assessment("proceed", None))
    app = application(evaluators={"jev": triage}, assessment={"triage": "jev", "escalate": "missing"})
    result = app.call("assess", {"question": "Proceed?"})["assessment"]
    assert result["unresolved"] is True
    assert result["escalation_error"]["code"] == "evaluation_unavailable"
    app = application()
    assert app.call("create_work", {"goal": "No mandatory evaluator"})["work"]["status"] == "open"


def test_every_advertised_action_has_a_schema_and_dispatch_target():
    app = application()
    capabilities = app.describe()
    assert len({cap.name for cap in capabilities}) == len(capabilities)
    assert all(callable(getattr(app, f"_{cap.name}")) for cap in capabilities)
    with pytest.raises(OrchestrationError) as error:
        app.call("create_work", {"goal": "Feature", "guardrail": {"default": "allow"}})
    assert error.value.code == "invalid_arguments"


@pytest.mark.parametrize("configuration", [
    False, [], "disabled", {"triage": 3}, {"escalate": ""},
    {"confidence_threshold": "high"}, {"confidence_threshold": True},
    {"confidence_threshold": float("nan")}, {"confidence_threshold": 1.1},
    {"uncertain_outcomes": "borderline"}, {"uncertain_outcomes": [False]},
])
def test_invalid_assessment_configuration_is_rejected_before_use(configuration):
    with pytest.raises(OrchestrationError) as error:
        application(assessment=configuration)
    assert error.value.code == "invalid_configuration"


def test_assessment_configuration_is_detached_from_callers():
    configuration = {"triage": "jev", "confidence_threshold": 0.5, "uncertain_outcomes": ["borderline"]}
    app = application(evaluators={"jev": Evaluator(Assessment("proceed", 0.6))}, assessment=configuration)
    configuration["confidence_threshold"] = 0.9
    configuration["uncertain_outcomes"].append("proceed")
    assert app.call("assess", {"question": "Proceed?"})["assessment"]["unresolved"] is False
