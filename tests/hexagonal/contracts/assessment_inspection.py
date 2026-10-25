"""Store/vendor-neutral inspection behavior and independent expected outcomes.

Fixture ports arrange infrastructure and policy; scenarios assert public values,
retained raw inputs and error categories without consulting product verdict code.
"""
from copy import deepcopy
from typing import Protocol


class InspectionFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class InspectionFixture(Protocol):
    def call(self, action: str, arguments: dict, actor: str = "owner") -> dict: ...
    def start_session(self, work_id: str, actor: str) -> dict: ...
    def native(self, session: dict, action: str, arguments: dict, call_id: str) -> dict: ...
    def provider_evidence(self, session: dict, payload: dict) -> dict: ...
    def restrict(self, kind: str, identity: str, effect: str = "deny") -> None: ...
    def permit_all(self) -> None: ...
    def raw_records(self) -> dict: ...
    def evaluated_contexts(self) -> list[dict]: ...
    def change_during_actor_inspection(self, target: str, change: str) -> None: ...


OPAQUE = {
    "actor_record": {"id": "worker", "metadata": {"opaque": "USER_VALUE"}},
    "work": {"id": "user-work-key"},
    "input_binding": {"actor_record": {"id": "worker"}},
    "source": {"type": "assessment", "assessment_id": "user-key"},
    "sessions": [{"id": "user-session-key"}],
}


def prepare(fixture: InspectionFixture):
    private = fixture.call("add_actor", {"actor_id": "worker", "metadata": {"secret": "TYPED_PRIVATE_ACTOR"}})["actor"]
    public = fixture.call("add_actor", {"actor_id": "observer", "metadata": {"public": True}})["actor"]
    work = fixture.call("create_work", {"goal": "shared goal", "inputs": OPAQUE, "context": OPAQUE})["work"]
    session = fixture.start_session(work["id"], "worker")
    original = fixture.native(session, "assess", {"work_id": work["id"], "question": "review", "context": OPAQUE}, "assessment")
    fixture.native(session, "list_actors", {}, "catalog")
    fixture.native(session, "record_result", {"work_id": work["id"], "result": OPAQUE}, "opaque")
    provider = fixture.provider_evidence(session, OPAQUE)
    # Retain get_work itself, exercising assessment evidence inside an older
    # aggregate as well as direct current work and direct assessment history.
    fixture.native(session, "get_work", {"work_id": work["id"]}, "work-history")
    return {"private": private, "public": public, "work": work, "session": session,
        "original": original, "provider": provider}


def inspect(fixture: InspectionFixture, state):
    return fixture.call("native_observations", {"session_id": state["session"]["id"]}, actor="observer")


def assessment_views(history, work):
    """Enumerate only the independently known paths exercised by the scenario."""
    calls = {row["call_id"]: row for row in history["calls"]}
    completed = next(row["observation"] for row in history["observations"]
        if row["observation"].get("call_id") == "assessment" and row["observation"].get("phase") == "completed")
    nested_work = calls["work-history"]["result"]
    return [calls["assessment"]["result"]["assessment"], completed["result"]["assessment"],
        next(row["result"] for row in work["evidence"] if row["source"]["type"] == "assessment"),
        next(row["result"] for row in nested_work["evidence"] if row["source"]["type"] == "assessment")]


def require_actor_scope(history, work):
    for assessment in assessment_views(history, work):
        assert assessment["input_binding"]["actor_record"] is None, "IV01_TYPED_ASSESSMENT_ACTOR_SCOPE"


def actor_projection_contract(fixture: InspectionFixture, effect="deny"):
    state = prepare(fixture)
    before = inspect(fixture, state)
    work_before = fixture.call("get_work", {"work_id": state["work"]["id"]}, actor="observer")
    raw_before = fixture.raw_records()
    evaluated_before = deepcopy(fixture.evaluated_contexts())
    assert all(row["input_binding"]["actor_record"] == state["private"]
        for row in assessment_views(before, work_before)), "IV01_AUTHORIZED_TYPED_ACTOR_CONTROL"
    assert evaluated_before[0]["actor_record"] == state["private"], "IV01_INTERNAL_ASSESSMENT_INPUT_CONTROL"

    fixture.restrict("actor", "worker", effect)
    after = inspect(fixture, state)
    work_after = fixture.call("get_work", {"work_id": state["work"]["id"]}, actor="observer")
    require_actor_scope(after, work_after)
    calls = {row["call_id"]: row for row in after["calls"]}
    assert calls["catalog"]["result"]["actors"] == [state["public"]], "IV01_SAME_ACTION_CATALOG_CONTROL"
    assert calls["opaque"]["result"]["evidence"]["result"] == OPAQUE, "IV01_OPAQUE_CALLER_CONTROL"
    assert calls["provider"]["result"] == state["provider"], "IV01_OPAQUE_PROVIDER_CONTROL"
    assert next(row["result"] for row in work_after["evidence"] if row["source"]["type"] == "caller") == OPAQUE
    assert next(row["result"] for row in work_after["evidence"] if row["source"]["type"] == "capability") == OPAQUE
    for assessment in assessment_views(after, work_after):
        assert assessment["request_context"] == OPAQUE, "IV01_OPAQUE_REQUEST_CONTROL"
        assert assessment["input_binding"]["inputs"] == OPAQUE, "IV01_OPAQUE_INPUTS_CONTROL"
        assert assessment["input_binding"]["context"] == OPAQUE, "IV01_OPAQUE_CONTEXT_CONTROL"
        assert assessment["evaluations"] == state["original"]["assessment"]["evaluations"], "IV01_OPAQUE_EVALUATOR_CONTROL"
        assert assessment["input_binding"]["work"] == state["original"]["assessment"]["input_binding"]["work"]
    assert calls["assessment"]["inspection_redacted"] is True
    assert work_after["inspection_redacted"] is True
    assert fixture.raw_records() == raw_before, "IV01_TRUSTED_RAW_UNCHANGED"
    assert fixture.evaluated_contexts() == evaluated_before, "IV01_NO_REEVALUATION_OR_INPUT_MUTATION"
    fixture.permit_all()
    assert inspect(fixture, state) == before, "IV01_GRANT_RESTORES_EXACT_HISTORY"
    assert fixture.call("get_work", {"work_id": state["work"]["id"]}, actor="observer") == work_before


def work_projection_contract(fixture: InspectionFixture):
    state = prepare(fixture)
    before = inspect(fixture, state)
    raw_before = fixture.raw_records()
    original_work = state["original"]["assessment"]["input_binding"]["work"]
    assert original_work is not None
    fixture.restrict("work", state["work"]["id"])
    after = inspect(fixture, state)
    calls = {row["call_id"]: row for row in after["calls"]}
    assessment = calls["assessment"]["result"]["assessment"]
    assert assessment["input_binding"]["work"] is None, "IV01_TYPED_ASSESSMENT_WORK_SCOPE"
    assert assessment["input_binding"]["actor_record"] == state["private"]
    assert assessment["input_binding"]["inputs"] == OPAQUE
    assert assessment["input_binding"]["context"] == OPAQUE
    assert fixture.raw_records() == raw_before
    fixture.permit_all()
    assert inspect(fixture, state) == before, "IV01_CONFIGURED_WORK_PROJECTION_RESTORED"


def absent_optional_binding_contract(fixture: InspectionFixture):
    # Unregistered callers and assessments without work are supported. Absence
    # is not a fabricated actor record, missing work identity, or an error.
    record = fixture.call("assess", {"question": "standalone", "context": OPAQUE}, actor="unregistered")["assessment"]
    assert record["input_binding"]["actor_record"] is None
    assert record["input_binding"]["work"] is None
    assert record["request_context"] == OPAQUE
    state = prepare(fixture)
    fixture.native(state["session"], "assess", {"question": "standalone history", "context": OPAQUE}, "standalone")
    fixture.restrict("actor", "worker")
    retained = next(row["result"]["assessment"] for row in inspect(fixture, state)["calls"] if row["call_id"] == "standalone")
    assert retained["input_binding"]["work"] is None
    assert retained["input_binding"]["actor_record"] is None
    assert retained["request_context"] == OPAQUE


def current_context_race_contract(fixture: InspectionFixture, target, change):
    state = prepare(fixture)
    fixture.change_during_actor_inspection(target, change)
    try:
        inspect(fixture, state)
    except InspectionFailure as error:
        assert error.code == "conflict", "IV01_CURRENT_CONTEXT_RACE_CATEGORY"
    else:
        raise AssertionError("IV01_CURRENT_CONTEXT_RACE_RECHECK")
