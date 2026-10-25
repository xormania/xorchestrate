"""F01: public inspection authorizes each record; receipt joins stay internal.

The retained known-bad fault replaces the public projection with the original
global snapshot. The disclosure oracle must detect that activated leak while
direct-denial and valid-public controls still behave correctly.
"""
import json
from copy import deepcopy
from types import MethodType

import pytest

from tests.hexagonal.test_application import application
from tests.hexagonal.test_delivery import Native
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.model import OrchestrationError

CANARY = "F01_PRIVATE_RECORD_CANARY"


@pytest.fixture(params=["memory", "sqlite"])
def scope(request, tmp_path):
    store = MemoryStore() if request.param == "memory" else SQLiteStore(str(tmp_path / "scope.sqlite3"))
    app = application(store, driver=Native())
    for actor in ("limited", "private-owner"):
        app.call("add_actor", {"actor_id": actor})
    bundles = []
    for label, actor, marker in (("public", "limited", "public"), ("private", "private-owner", CANARY)):
        work = app.call("create_work", {"goal": label})["work"]
        app.call("record_result", {"work_id": work["id"], "result": {"marker": marker}})
        session = app.call("start_session", {"driver": "agent", "work_id": work["id"],
            "actor_id": actor, "specification": {"marker": marker}, "idempotency_key": label})["session"]
        message = app.call("send_message", {"work_id": work["id"], "destination": {"actor": actor},
            "body": {"marker": marker}})["message"]
        app.call("dispatch_deliveries", {"message_id": message["id"]})
        source = {"actor": actor, "work_id": work["id"], "session_id": session["id"],
            "native_id": session["backend"]["native_id"], "incarnation": session["backend"]["incarnation"],
            "call_id": label}
        app.observe_native("agent", source, {"native_id": source["native_id"], "incarnation": source["incarnation"],
            "call_id": label, "phase": "observed", "marker": marker})
        app.invoke_native("agent", "record_result", {"work_id": work["id"], "result": {"marker": marker}}, source)
        bundles.append({"work": work, "session": session, "message": message})
    yield app, bundles[0], bundles[1]
    store.close()


def restricted(app, public, *, extra=()):
    app.guardrail = RuleGuardrail({"default": "deny", "rules": [
        {"actors": ["limited"], "actions": ["get_work", "get_delivery", "native_observations", "verify_exchange"],
         "where": {"work_id": public["work"]["id"]}, "effect": "allow"}, *extra]})


def assert_direct_controls(app, public, private):
    for action, args in (("get_work", {"work_id": private["work"]["id"]}),
                         ("get_delivery", {"message_id": private["message"]["id"]})):
        with pytest.raises(OrchestrationError) as denied:
            app.call(action, args, actor="limited")
        assert denied.value.code == "denied", "F01_DIRECT_PRIVATE_DENIED"
    allowed = app.call("get_work", {"work_id": public["work"]["id"]}, actor="limited")
    assert CANARY not in json.dumps(allowed), "F01_PUBLIC_WORK_CONTROL"


def assert_private_absent(response):
    assert CANARY not in json.dumps(response), "F01_NO_PRIVATE_RECORD_DISCLOSURE"


def test_delivery_inspection_authorizes_all_record_sections(scope):
    app, public, private = scope
    unrestricted = app.call("get_delivery", {"message_id": public["message"]["id"]})
    assert CANARY in json.dumps(unrestricted)
    assert len(unrestricted["sessions"]) == 2
    restricted(app, public)
    assert_direct_controls(app, public, private)
    result = app.call("get_delivery", {"message_id": public["message"]["id"]}, actor="limited")
    assert_private_absent(result)
    assert result["message"]["id"] == public["message"]["id"]
    for section in ("messages", "sessions", "attempts", "all_attempts", "observations", "evidence", "deliveries"):
        assert result[section], section
        assert all(row.get("work_id") == public["work"]["id"] for row in result[section]), section


def test_same_work_message_restriction_is_not_bypassed(scope):
    app, public, _ = scope
    hidden = app.call("send_message", {"work_id": public["work"]["id"], "destination": {"actor": "limited"},
        "body": {"marker": CANARY}})["message"]
    app.call("dispatch_deliveries", {"message_id": hidden["id"]})
    restricted(app, public, extra=[{"actors": ["limited"], "actions": ["get_delivery"],
        "where": {"arguments.message_id": hidden["id"]}, "effect": "deny"}])
    result = app.call("get_delivery", {"message_id": public["message"]["id"]}, actor="limited")
    assert_private_absent(result)
    assert hidden["id"] not in {row["id"] for row in result["messages"]}
    assert hidden["id"] not in {row["message_id"] for row in result["all_attempts"]}


def test_native_observation_list_authorizes_each_session(scope):
    app, public, private = scope
    restricted(app, public, extra=[{"actors": ["limited"], "actions": ["native_observations"],
        "where": {"work_id": None}, "effect": "allow"}])
    result = app.call("native_observations", {}, actor="limited")
    assert_private_absent(result)
    assert result["observations"] and result["calls"]
    assert all(row["session_id"] == public["session"]["id"] for section in result.values() for row in section)
    with pytest.raises(OrchestrationError) as denied:
        app.call("native_observations", {"session_id": private["session"]["id"]}, actor="limited")
    assert denied.value.code == "denied"


def test_native_inspection_projects_historical_aggregate_results(scope):
    app, public, _ = scope
    session = public["session"]
    source = {"actor": "limited", "work_id": public["work"]["id"], "session_id": session["id"],
        "native_id": session["backend"]["native_id"], "incarnation": session["backend"]["incarnation"],
        "call_id": "historically-unrestricted-read"}
    historical = app.invoke_native("agent", "get_delivery", {"message_id": public["message"]["id"]}, source)
    assert CANARY in json.dumps(historical)
    restricted(app, public)
    response = app.call("native_observations", {"session_id": session["id"]}, actor="limited")
    assert_private_absent(response)
    assert any(row.get("inspection_redacted") for row in response["calls"])
    assert any(row.get("inspection_redacted") for row in response["observations"])
    with app.store.transaction() as tx:
        retained = [row.data for row in tx.list("native_call")]
    assert CANARY in json.dumps(retained), "F01_ORIGINAL_RAW_EVIDENCE_RETAINED"


@pytest.mark.parametrize("read_action", ["get_work", "get_acceptance"])
def test_native_source_cannot_lend_scope_to_historical_target(scope, read_action):
    app, public, private = scope
    # Private acceptance returns this owner objective as well as its state.
    app.call("set_requirements", {"work_id": private["work"]["id"], "expected_revision": 0,
        "objective": CANARY, "outcomes": {"A": "Private outcome"}, "checks": {}})
    session = public["session"]
    source = {"actor": "limited", "work_id": public["work"]["id"], "session_id": session["id"],
        "native_id": session["backend"]["native_id"], "incarnation": session["backend"]["incarnation"],
        "call_id": "cross-work-read"}
    historical = app.invoke_native("agent", read_action, {"work_id": private["work"]["id"]}, source)
    assert CANARY in json.dumps(historical)
    restricted(app, public)
    with pytest.raises(OrchestrationError) as denied:
        app.call(read_action, {"work_id": private["work"]["id"]}, actor="limited")
    assert denied.value.code == "denied"
    response = app.call("native_observations", {"session_id": session["id"]}, actor="limited")
    assert_private_absent(response)
    assert all(row["call_id"] != "cross-work-read" for row in response["calls"])
    assert all(row["observation"].get("call_id") != "cross-work-read" for row in response["observations"])
    assert response["calls"] and response["observations"], "F01_PUBLIC_NATIVE_HISTORY_RETAINED"


def test_receipt_policy_keeps_trusted_cross_work_join(scope):
    app, public, private = scope
    snapshots = []

    class InternalReceipt:
        def evaluate(self, snapshot, recipient, expectation):
            snapshots.append(deepcopy(snapshot))
            return {"verified": True, "diagnostics": []}

    app.receipt_policy = InternalReceipt()
    restricted(app, public)
    receipt = app.call("verify_exchange", {"message_id": public["message"]["id"],
        "recipient": "limited", "expectation": {}}, actor="limited")
    assert receipt["receipt"]["decision"]["verified"]
    assert CANARY in json.dumps(snapshots[0]), "F01_TRUSTED_RECEIPT_INPUT_RETAINED"
    assert private["message"]["id"] in {row["id"] for row in snapshots[0]["messages"]}
    result = app.call("get_delivery", {"message_id": public["message"]["id"]}, actor="limited")
    assert result["receipts"]
    assert_private_absent(result)


def test_f01_sustained_global_snapshot_fault_is_detected(scope):
    app, public, private = scope
    restricted(app, public)
    assert_direct_controls(app, public, private)
    assert_private_absent(app.call("get_delivery", {"message_id": public["message"]["id"]}, actor="limited"))
    activation = []

    def disclose_snapshot(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            activation.append(args["message_id"])
            return self._delivery_snapshot(tx, args["message_id"])

    app._get_delivery = MethodType(disclose_snapshot, app)
    faulty = app.call("get_delivery", {"message_id": public["message"]["id"]}, actor="limited")
    assert activation == [public["message"]["id"]]
    assert CANARY in json.dumps(faulty), "F01_FAULT_ACTIVATED"
    with pytest.raises(AssertionError, match=r"^F01_NO_PRIVATE_RECORD_DISCLOSURE(?:\n|$)"):
        assert_private_absent(faulty)
    assert_direct_controls(app, public, private)


def test_f01_unrelated_inspection_error_receives_no_detection_credit(scope):
    app, public, _ = scope
    restricted(app, public)

    def unavailable(*args):
        raise TimeoutError("unrelated store outage")

    app._delivery_snapshot = unavailable
    with pytest.raises(TimeoutError, match="unrelated store outage"):
        assert_private_absent(app.call("get_delivery", {"message_id": public["message"]["id"]}, actor="limited"))
