"""State behavior specified through fixture facts, independent of the engine.

The parcel vocabulary is deliberately unrelated to orchestration. These
scenarios assert observable state and history, not the engine's transition
table or a second invocation of its decision function.
"""
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class StateObservation:
    data: Any
    version: int | None
    history: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class StateOutcome:
    accepted: bool
    code: str
    state: str | None = None
    version: int | None = None


class StateFixture(Protocol):
    def observe(self, key: str = "parcel") -> StateObservation: ...
    def change(self, event: str, version: int | None, *, permitted: bool = False,
               key: str = "parcel", wrong_target: bool = False) -> StateOutcome: ...
    def inspect(self, event: str, *, permitted: bool = False) -> StateOutcome: ...
    def fail_history(self, *, catch_inside: bool, after_append: bool) -> StateOutcome: ...
    def reconstruct(self) -> None: ...
    def contend(self, version: int) -> tuple[StateOutcome, ...]: ...


def legal_progress_and_guard_remedy(fixture: StateFixture) -> None:
    empty = fixture.observe()
    assert empty.data is None and empty.version is None and not empty.history
    created = fixture.change("register", None)
    assert created.accepted and created.state == "draft" and created.version == 1
    draft = fixture.observe()
    assert draft.data == {"status": "draft", "payload": {"label": "owned parcel"}}
    assert len(draft.history) == 1
    inspected = fixture.inspect("claim")
    assert inspected.accepted and inspected.state == "held"
    assert fixture.observe() == draft, "STATE_INSPECTION_IS_READ_ONLY"
    refused = fixture.change("release", draft.version, permitted=True)
    assert not refused.accepted and refused.code == "invalid_transition"
    assert fixture.observe() == draft, "STATE_REFUSAL_LEAVES_STATE_AND_HISTORY_UNCHANGED"
    claimed = fixture.change("claim", draft.version)
    assert claimed.accepted and claimed.state == "held" and claimed.version == 2
    held = fixture.observe()
    assert len(held.history) == 2
    assert not fixture.inspect("release").accepted
    assert fixture.observe() == held, "STATE_REFUSED_INSPECTION_IS_READ_ONLY"
    refused = fixture.change("release", held.version)
    assert not refused.accepted and refused.code == "state_guard_rejected", "STATE_GUARD_REFUSAL_REQUIRED"
    assert fixture.observe() == held, "STATE_GUARD_REFUSAL_IS_ATOMIC"
    # The documented remedy changes only the permission fact. A reject-all
    # implementation cannot satisfy this positive half of the same scenario.
    released = fixture.change("release", held.version, permitted=True)
    assert released.accepted and released.state == "done" and released.version == 3, "STATE_GUARD_REMEDY_REACHES_DONE"
    done = fixture.observe()
    assert [row["target"] for row in done.history] == ["draft", "held", "done"]
    assert [row["source"] for row in done.history] == [None, "draft", "held"]
    assert [row["event"] for row in done.history] == ["register", "claim", "release"]
    assert [row["record_version"] for row in done.history] == [1, 2, 3]
    assert [row["previous_version"] for row in done.history] == [None, 1, 2]
    assert all(row["machine"] == "parcel-flow" and row["definition_version"] == 7 for row in done.history)
    assert all(row["subject"] == {"kind": "parcel", "key": "parcel"} for row in done.history)
    refused = fixture.change("claim", done.version)
    assert not refused.accepted and fixture.observe() == done


def stale_versions_include_same_state_and_aba(fixture: StateFixture) -> None:
    assert fixture.change("register", None).accepted
    original = fixture.observe()
    assert fixture.change("touch", original.version).accepted
    touched = fixture.observe()
    assert touched.data == original.data and touched.version == original.version + 1
    refused = fixture.change("claim", original.version)
    assert not refused.accepted and refused.code == "conflict", "STATE_STALE_VERSION_REJECTED"
    assert fixture.observe() == touched
    assert fixture.change("claim", touched.version).accepted
    assert fixture.change("release", fixture.observe().version, permitted=True).accepted
    assert fixture.change("reset", fixture.observe().version).accepted
    returned = fixture.observe()
    assert returned.data == original.data and returned.version > touched.version
    refused = fixture.change("claim", touched.version)
    assert not refused.accepted and refused.code == "conflict", "STATE_ABA_VERSION_REJECTED"
    assert fixture.observe() == returned
    assert fixture.change("claim", returned.version).accepted


def target_mismatch_and_duplicate_creation_are_atomic(fixture: StateFixture) -> None:
    assert fixture.change("register", None).accepted
    before = fixture.observe()
    assert not fixture.change("register", None).accepted
    assert fixture.observe() == before
    assert not fixture.change("claim", before.version, wrong_target=True).accepted
    assert fixture.observe() == before, "STATE_DECLARED_TARGET_CANNOT_BE_BYPASSED"
    assert fixture.change("claim", before.version).accepted


def history_failure_cannot_publish_state(fixture: StateFixture, *, catch_inside: bool, after_append: bool) -> None:
    assert fixture.change("register", None).accepted
    before = fixture.observe()
    failed = fixture.fail_history(catch_inside=catch_inside, after_append=after_append)
    assert not failed.accepted
    assert fixture.observe() == before, "STATE_HISTORY_AND_RECORD_COMMIT_TOGETHER"
    assert fixture.change("claim", before.version).accepted


def reconstruction_retains_state_versions_and_history(fixture: StateFixture) -> None:
    assert fixture.change("register", None).accepted
    assert fixture.change("claim", fixture.observe().version).accepted
    before = fixture.observe()
    fixture.reconstruct()
    assert fixture.observe() == before, "STATE_RECONSTRUCTION_PRESERVES_COMMITTED_FACTS"
    assert fixture.change("release", before.version, permitted=True).accepted
    assert fixture.observe().version == before.version + 1


def contention_has_one_winner_and_no_loser_history(fixture: StateFixture) -> None:
    assert fixture.change("register", None).accepted
    before = fixture.observe()
    outcomes = fixture.contend(before.version)
    assert sum(row.accepted for row in outcomes) == 1, "STATE_CONTESTED_TRANSITION_HAS_ONE_WINNER"
    assert sorted(row.code for row in outcomes) == ["", "conflict"]
    after = fixture.observe()
    assert after.data["status"] == "held" and after.version == before.version + 1
    assert len(after.history) == len(before.history) + 1, "STATE_LOSER_CANNOT_APPEND_HISTORY"


def separate_subjects_remain_separate(fixture: StateFixture) -> None:
    assert fixture.change("register", None).accepted
    assert fixture.change("register", None, key="other").accepted
    other = fixture.observe("other")
    assert fixture.change("claim", fixture.observe().version).accepted
    assert fixture.observe("other") == other
    assert fixture.change("claim", other.version, key="other").accepted
    assert fixture.observe("other").data["status"] == "held"


def application_consumes_changed_creation_guard(fixture, kind: str) -> None:
    fixture.set_creation_guard(kind, False)
    before = fixture.snapshot()
    rejected = fixture.start()
    assert not rejected.accepted and rejected.code == "state_guard_rejected", "STATE_APPLICATION_MODEL_CONSUMED"
    assert fixture.driver.start_calls == 0, "STATE_APPLICATION_GUARD_PREVENTS_EFFECT"
    assert fixture.snapshot() == before, "STATE_APPLICATION_CREATION_ROLLS_BACK_TOGETHER"
    fixture.set_creation_guard(kind, True)
    accepted = fixture.start()
    assert accepted.accepted and accepted.state == "active", "STATE_APPLICATION_GUARD_REMEDY_WORKS"
    assert fixture.driver.start_calls == 1
    relevant = [event for event in fixture.snapshot()["events"] if event.get("type") == "state.transition"
                and event.get("machine") == kind]
    assert relevant and all(event["definition_version"] == 2 for event in relevant)


def live_application_records_all_finite_lifecycles(fixture) -> None:
    observed = fixture.live_trace()
    # Expectations are authored from the exercised public operations. Neither
    # the production registry nor StateEngine.decide supplies this oracle.
    expected = {
        "operation": [(None, "pending"), ("pending", "completed")],
        "session": [(None, "starting"), ("starting", "active")],
        "invocation": [(None, "pending"), ("pending", "completed")],
        "delivery": [(None, "queued"), ("queued", "pending"), ("pending", "submitted")],
        "delivery_attempt": [(None, "pending"), ("pending", "submitted")],
        "native_call": [(None, "pending"), ("pending", "completed")],
        "measurement": [(None, "pending"), ("pending", "completed")],
    }
    assert set(observed["records"]) == set(expected)
    for kind, transitions in expected.items():
        record = observed["records"][kind]
        history = observed["histories"][kind]
        assert [(event["source"], event["target"]) for event in history] == transitions, (
            "STATE_ACTIVE_LIFECYCLE_HAS_REQUIRED_HISTORY", kind)
        assert [event["record_version"] for event in history] == list(range(1, len(transitions) + 1))
        assert [event["previous_version"] for event in history] == [None, *range(1, len(transitions))]
        assert all(event["machine"] == kind and event["definition_version"] == 1 for event in history)
        assert record["status"] == transitions[-1][1] and record["version"] == len(transitions)
    assert observed["start_calls"] == 1 and observed["delivery_calls"] == 1 and observed["provider_calls"] == 2
    assert observed["acceptance"]["acceptance_issues"] == []
    assert observed["acceptance"]["accepted"] is False, "STATE_MEASUREMENT_IS_NOT_OWNER_ACCEPTANCE"


def delivery_uncertainty_survives_restart_until_explicit_remedy(fixture) -> None:
    assert fixture.start().accepted
    fixture.queue()
    fixture.driver.delivery_failure = TimeoutError("submission response lost")
    uncertain = fixture.dispatch()[0]
    delivery = uncertain["delivery"]
    assert delivery["status"] == "unknown" and uncertain["attempt"]["status"] == "unknown"
    assert fixture.driver.delivery_calls == 1
    before = fixture.snapshot()
    fixture.reconstruct()
    assert fixture.snapshot() == before, "STATE_APPLICATION_RECONSTRUCTION_PRESERVES_HISTORY"
    assert fixture.dispatch() == []
    try:
        fixture.retry(delivery)
    except Exception as error:
        rejected = getattr(error, "code", "")
    else:
        rejected = ""
    assert rejected == "delivery_unresolved", "STATE_UNKNOWN_EFFECT_CANNOT_AUTORETRY"
    assert fixture.snapshot() == before and fixture.driver.delivery_calls == 1
    resolved = fixture.resolve_delivery(delivery)
    assert resolved["status"] == "failed"
    reconciled = fixture.rows("delivery_attempt")[0]
    assert reconciled["status"] == "reconciled" and reconciled["original_status"] == "unknown"
    assert reconciled["disposition"] == "failed"
    queued = fixture.retry(resolved)
    assert queued["status"] == "queued"
    fixture.driver.delivery_failure = None
    final = fixture.dispatch()[0]
    assert final["delivery"]["status"] == "submitted" and final["attempt"]["sequence"] == 2
    assert fixture.driver.delivery_calls == 2
    assert [event["target"] for event in fixture.history("delivery", delivery["id"])] == [
        "queued", "pending", "unknown", "failed", "queued", "pending", "submitted"]
    assert [event["target"] for event in fixture.history("delivery_attempt", uncertain["attempt"]["id"])] == [
        "pending", "unknown", "reconciled"]


def retained_measurement_recovers_without_repeating_external_work(fixture) -> None:
    fixture.configure_measurement()
    fixture.set_transition_guard("measurement", "complete", False)
    try:
        fixture.measure()
    except Exception as error:
        rejected = getattr(error, "code", "")
        outcome = getattr(error, "details", {})
    else:
        rejected = ""
        outcome = {}
    assert rejected == "result_unapplied" and outcome.get("outcome") == "completed", (
        "STATE_MEASUREMENT_LOCAL_REFUSAL_PRESERVES_COMPLETED_EFFECT")
    measurement, = fixture.rows("measurement")
    operation, = fixture.rows("operation")
    assert measurement["status"] == "pending" and measurement["version"] == 1
    assert operation["status"] == "completed" and operation["external_result_origin"] == "adapter"
    assert outcome["operation_id"] == operation["id"] and outcome["measurement_id"] == measurement["id"]
    assert outcome["external_result"] == operation["external_result"]
    assert len(fixture.provider.calls) == 1
    before = fixture.snapshot()
    fixture.reconstruct()
    assert fixture.snapshot() == before
    recovered = fixture.measure()
    assert recovered["replayed"] and recovered["measurement"]["status"] == "completed"
    assert recovered["measurement"]["raw_response"] == operation["external_result"]
    assert len(fixture.provider.calls) == 1, "STATE_MEASUREMENT_RECOVERY_DOES_NOT_REPEAT_EFFECT"
    history = fixture.history("measurement", measurement["id"])
    assert [(event["source"], event["event"], event["target"]) for event in history] == [
        (None, "create", "pending"), ("pending", "recover", "completed")]
    assert [event["record_version"] for event in history] == [1, 2]
    after = fixture.snapshot()
    assert fixture.measure() == recovered and fixture.snapshot() == after
    assert len(fixture.provider.calls) == 1
