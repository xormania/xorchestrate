"""Per-turn completion must preserve aggregate activity into saved-run checks."""
import json
from functools import partial

import pytest

from scripts.verification.checker import VerificationFailure, check_case
from scripts.verification.multi_harness import snapshot
from scripts.verification.normalize import normalize
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
from xorchestrate.delivery import view

wait = partial(wait_for, timeout=8, message='native completion schedule did not complete')


def completion_events(raw):
    return sorted([r["observation"] for r in raw["observations"]
                   if r["observation"].get("kind") in {"native_completion", "turn_completed"}
                   and not r["observation"].get("stale")], key=lambda event: event["at"])


def assert_completion_matches_state(raw, status):
    assert completion_events(raw)[-1].get("activity") == status["activity"], "NATIVE_COMPLETION_AGGREGATE_STATE"


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
def test_actual_completion_snapshot_keeps_held_callback_visible_to_checker(protocol, tmp_path):
    with native_runtime(protocol, tmp_path) as (app, adapter, work, basis, actor, send, rejected):
        holds = Holds(timeout=10)
        app.providers["holds"] = holds
        with app.store.transaction() as tx:
            session = view(tx.list("session")[0])
        peers = {protocol: {"session_id": session["id"], "native_id": session["backend"]["native_id"],
                            "incarnation": session["backend"]["incarnation"]}}
        case = {"id": "completion-" + protocol, "kind": "completion", "peer": protocol}

        def measured(label):
            raw = snapshot(app)
            status = adapter.status(session["backend"])
            events = normalize(raw, peers, [], "protocol-fixture")
            run = {"events": events, "sessions": peers}
            (tmp_path / (label + ".json")).write_text(json.dumps({"raw_records": raw, "events": events,
                "sessions": peers, "status": status, "callbacks_finished": holds.finished[:]}, indent=2))
            return raw, status, run

        try:
            for label in ("A", "B"):
                arguments = {"capability": "holds/hold", "arguments": {"nonce": label}}
                body = application_message(protocol, "invoke", arguments)
                retry(app, "send_session", {"session_id": session["id"], "message": body, "idempotency_key": "hold-" + label})
                assert holds.entered[label].wait(4)
            _, before, run = measured("before-stops")
            assert before["activity"] == "active"
            with pytest.raises(VerificationFailure, match="native_completion_missing"):
                check_case(case, run, {})
            holds.release["A"].set()
            wait(lambda: any(r["observation"].get("kind") in {"native_completion", "turn_completed"}
                             for r in snapshot(app)["observations"]))
            partial, status, run = measured("after-first-stop")
            assert holds.finished == ["A"] and not holds.release["B"].is_set()
            assert status["activity"] == "active"
            # Codex may mark the older turn stale; even its per-turn record must
            # carry aggregate state, and normalization must not invent idle.
            completions = sorted([r["observation"] for r in partial["observations"]
                                  if r["observation"].get("kind") in {"native_completion", "turn_completed"}],
                                 key=lambda event: event["at"])
            assert completions[-1].get("activity") == "active", "NATIVE_COMPLETION_AGGREGATE_STATE"
            with pytest.raises(VerificationFailure, match="native_completion_missing"):
                check_case(case, run, {})
            holds.release["B"].set()
            wait(lambda: adapter.status(session["backend"])["activity"] == "idle")
            final, status, run = measured("after-both-stops")
            assert_completion_matches_state(final, status)
            assert check_case(case, run, {})

            # Sustained evidence fault: remove the aggregate observation from
            # completed records and omit their separate activity observations.
            # The incomplete subject remains bad; a missing state is not idle.
            bad = json.loads(json.dumps(final))
            bad["observations"] = [r for r in bad["observations"] if r["observation"].get("kind") != "activity"]
            for record in bad["observations"]:
                if record["observation"].get("kind") in {"native_completion", "turn_completed"}:
                    record["observation"].pop("activity", None)
            assert completion_events(bad) and all("activity" not in row for row in completion_events(bad))
            with pytest.raises(AssertionError, match="NATIVE_COMPLETION_AGGREGATE_STATE"):
                assert_completion_matches_state(bad, status)
            bad_events = normalize(bad, peers, [], "protocol-fixture")
            with pytest.raises(VerificationFailure, match="native_completion_missing"):
                check_case(case, {"events": bad_events, "sessions": peers}, {})
            (tmp_path / "known-bad-missing-state.json").write_text(json.dumps({"raw_records": bad, "events": bad_events}, indent=2))
        finally:
            holds.close()


@pytest.mark.parametrize("message,expected_kind,expected_state", [
    ("completed", "native_completion", "idle"), ("exit", "native_completion_unknown", "unknown")])
def test_actual_acp_completion_observation_follows_pending_transition(message, expected_kind, expected_state, tmp_path):
    driver = native_driver("acp", fixture="session", timeout=3)
    records = []
    def observe(context, event):
        if event["kind"] in {"native_completion", "native_completion_unknown"}:
            live = next(iter(driver._sessions.values()))
            records.append({"event": event, "pending_at_observation": list(live.pending), "activity_at_observation": live.activity})
    driver.bind_observer(observe)
    try:
        session = driver.start({})
        driver.send(session, {"text": message})
        wait(lambda: bool(records))
        measured = records[-1]
        assert measured["event"]["kind"] == expected_kind
        assert measured["pending_at_observation"] == [], "ACP_COMPLETION_AFTER_PENDING_TRANSITION"
        assert measured["event"].get("activity") == measured["activity_at_observation"] == expected_state, "NATIVE_COMPLETION_AGGREGATE_STATE"
        (tmp_path / "acp-observations.json").write_text(json.dumps(records, indent=2))
    finally:
        driver.close()
