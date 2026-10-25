"""Completion requirements expressed only through externally controlled work.

The expected answer follows from unreleased fixture gates, never from a
production ledger, wire protocol, or the verifier under test.
"""
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CompletionObservation:
    activity: str
    held_callbacks: frozenset[str]
    held_starts: frozenset[str]
    held_tails: frozenset[str]
    returned_callbacks: frozenset[str]
    evidence: Any


class CompletionFixture(Protocol):
    def submit(self, label: str, *, callback: bool = False, start: bool = False,
               tail: bool = False) -> None: ...
    def release_callback(self, label: str) -> None: ...
    def release_start(self, label: str) -> None: ...
    def release_tail(self, label: str) -> None: ...
    def capture(self) -> CompletionObservation: ...
    def with_false_idle(self, observation: CompletionObservation) -> Any: ...
    def with_completion_claim(self, observation: CompletionObservation, claim: str,
                              value: Any, *, absent: bool = False) -> Any: ...
    def await_idle(self) -> None: ...


class CompletionSubject(Protocol):
    def is_complete(self, evidence: Any) -> bool: ...


def reject_held_work(fixture: CompletionFixture, subject: CompletionSubject) -> CompletionObservation:
    observed = fixture.capture()
    assert observed.held_callbacks or observed.held_starts or observed.held_tails
    assert observed.activity == "active", "live product must retain controlled outstanding work"
    assert not subject.is_complete(observed.evidence), "IV02_HELD_WORK_MUST_NOT_COMPLETE"
    assert not subject.is_complete(fixture.with_false_idle(observed)), "IV02_FALSE_IDLE_MUST_NOT_COMPLETE"
    return observed


def require_eventual_completion(fixture: CompletionFixture, subject: CompletionSubject) -> None:
    fixture.await_idle()
    observed = fixture.capture()
    assert not (observed.held_callbacks or observed.held_starts or observed.held_tails)
    assert observed.activity == "idle"
    assert subject.is_complete(observed.evidence), "IV02_RELEASED_WORK_MUST_COMPLETE"


def accepted_before_start(fixture: CompletionFixture, subject: CompletionSubject) -> None:
    """An accepted second submission remains work before its first start event."""
    fixture.submit("first", tail=True)
    fixture.submit("accepted", start=True)
    observed = reject_held_work(fixture, subject)
    assert observed.held_tails == {"first"} and observed.held_starts == {"accepted"}
    fixture.release_tail("first")
    observed = reject_held_work(fixture, subject)
    assert not observed.held_tails and observed.held_starts == {"accepted"}
    assert observed.returned_callbacks == {"first"}
    fixture.release_start("accepted")
    require_eventual_completion(fixture, subject)


def released_turns_remain_observable(fixture: CompletionFixture) -> None:
    """Exercise product observation delivery without any verification verdict."""
    fixture.submit("first", tail=True)
    fixture.submit("second", tail=True)
    observed = fixture.capture()
    assert observed.activity == "active" and observed.held_tails == {"first", "second"}
    assert observed.returned_callbacks == {"first", "second"}
    fixture.release_tail("first")
    observed = fixture.capture()
    assert observed.activity == "active" and observed.held_tails == {"second"}
    fixture.release_tail("second")
    fixture.await_idle()
    observed = fixture.capture()
    assert observed.activity == "idle" and not observed.held_tails


def final_idle_claims_reconcile(fixture: CompletionFixture, subject: CompletionSubject) -> None:
    """A current final idle must agree with other independently retained claims."""
    fixture.submit("completed", tail=True)
    fixture.release_tail("completed")
    require_eventual_completion(fixture, subject)
    observed = fixture.capture()
    # Omission remains optional. Each contradictory variant differs from this
    # passing evidence in exactly one explicit claim or retained identity field.
    for claim in ("native_actor", "source_work"):
        assert subject.is_complete(fixture.with_completion_claim(observed, claim, None, absent=True))
    for claim in ("native_actor", "source_work", "recorded_actor", "recorded_native", "recorded_incarnation"):
        for value in ("contradictory-claim", None):
            evidence = fixture.with_completion_claim(observed, claim, value)
            assert not subject.is_complete(evidence), "IV03_FINAL_IDLE_CLAIMS_MUST_RECONCILE: " + claim


def overlapping_callbacks_and_tails(fixture: CompletionFixture, subject: CompletionSubject) -> None:
    """Independent callbacks and native continuation tails may finish out of order."""
    fixture.submit("first", callback=True, tail=True)
    fixture.submit("second", callback=True, tail=True)
    observed = reject_held_work(fixture, subject)
    assert observed.held_callbacks == {"first", "second"}
    fixture.release_callback("second")
    observed = reject_held_work(fixture, subject)
    assert observed.held_callbacks == {"first"}
    assert observed.returned_callbacks == {"second"}
    fixture.release_callback("first")
    observed = reject_held_work(fixture, subject)
    assert not observed.held_callbacks and observed.held_tails == {"first", "second"}
    fixture.release_tail("first")
    observed = reject_held_work(fixture, subject)
    assert observed.held_tails == {"second"}
    fixture.release_tail("second")
    require_eventual_completion(fixture, subject)
    # New work after a real idle must still invalidate that earlier completion.
    fixture.submit("after-idle", tail=True)
    reject_held_work(fixture, subject)
    fixture.release_tail("after-idle")
    require_eventual_completion(fixture, subject)


class TurnOwnershipFixture(Protocol):
    def observe_call(self, call: str, turn: str, *, identity: str = "current") -> None: ...
    def complete(self, turn: str) -> bool: ...
    def replace_owner(self) -> None: ...
    def invoke_callback(self, call: str) -> str: ...


def preserve_turn_ownership(fixture: TurnOwnershipFixture) -> None:
    """A fresh call may identify its turn, but cannot transfer an old turn."""
    fixture.observe_call("prior-call", "prior-turn")
    assert fixture.invoke_callback("prior-call") == "current-owner"
    assert fixture.complete("prior-turn"), "a coherent first turn must finish"
    fixture.replace_owner()
    fixture.observe_call("fresh-call", "fresh-turn")
    assert fixture.invoke_callback("fresh-call") == "current-owner"
    assert fixture.complete("fresh-turn"), "a replacement owner's fresh turn must finish"
    fixture.observe_call("another-fresh-call", "prior-turn")
    assert fixture.invoke_callback("another-fresh-call") == "current-owner"
    assert not fixture.complete("prior-turn"), "new calls must not transfer historical turn ownership"
    fixture.observe_call("prior-call", "unclaimed-stale-turn")
    assert fixture.invoke_callback("prior-call") == "stale_connection"
    assert not fixture.complete("unclaimed-stale-turn"), "a stale call must not seed a new turn binding"
    for identity in ("wrong-native", "wrong-incarnation"):
        fixture.observe_call(identity, identity + "-turn", identity=identity)
        assert not fixture.complete(identity + "-turn"), "contradictory native identity cannot seed a turn binding"
