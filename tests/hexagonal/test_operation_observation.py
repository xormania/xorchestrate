"""Retained operation acknowledgements and explicit source-window failures."""
from dataclasses import replace

import pytest

from tests.hexagonal.contracts.operation_observation import (
    completion_before_wait_is_retained,
    unrelated_completions_do_not_release_operation,
)
from tests.hexagonal.support.operation_observation import (
    Observation,
    ObservationError,
    ObservationSnapshot,
    OperationLog,
    OperationObserver,
)


def test_completion_before_wait_is_retained():
    log = OperationLog("peer", "instance-1")
    completion_before_wait_is_retained(log, OperationObserver(log.snapshot), "peer", "instance-1")


def test_unrelated_operations_and_boundaries_cannot_release_target():
    log = OperationLog("peer", "instance-1")
    unrelated_completions_do_not_release_operation(log, OperationObserver(log.snapshot), "peer", "instance-1")


def test_matched_completion_survives_later_retention_eviction():
    log = OperationLog("peer", "instance-1", limit=1)
    ticket, completed = completion_before_wait_is_retained(log, OperationObserver(log.snapshot), "peer", "instance-1")
    log.record("later", "complete", {})
    assert ticket.poll() == completed, "MATCHED_COMPLETION_REMAINS_RETAINED_BY_TICKET"


@pytest.mark.parametrize("field,value", [("source", "other-peer"), ("incarnation", "prior-instance")])
def test_foreign_observation_cannot_supply_completion(field, value):
    current = ObservationSnapshot("peer", "instance-1", 0, ())
    observer = OperationObserver(lambda: current)
    ticket = observer.arm("peer", "instance-1", "target", "complete")
    record = Observation("peer", "instance-1", "target", "complete", 1, {"effect": "foreign"})
    current = ObservationSnapshot("peer", "instance-1", 1, (replace(record, **{field: value}),))
    assert ticket.poll() is None, "FOREIGN_SOURCE_OR_INCARNATION_CANNOT_COMPLETE_OPERATION"
    correct = Observation("peer", "instance-1", "target", "complete", 2, {"effect": "current"})
    current = ObservationSnapshot("peer", "instance-1", 2, (current.observations[0], correct))
    assert ticket.poll() == correct


@pytest.mark.parametrize("field,value", [("source", "replacement-peer"), ("incarnation", "instance-2")])
def test_source_replacement_invalidates_pending_ticket(field, value):
    current = ObservationSnapshot("peer", "instance-1", 0, ())
    ticket = OperationObserver(lambda: current).arm("peer", "instance-1", "target", "complete")
    current = replace(current, **{field: value})
    with pytest.raises(ObservationError):
        ticket.poll()


def test_dropped_window_is_not_repaired_by_matching_retained_tail():
    log = OperationLog("peer", "instance-1", limit=1)
    ticket = OperationObserver(log.snapshot).arm("peer", "instance-1", "target", "complete")
    log.record("competing", "entered")
    log.record("target", "complete")
    with pytest.raises(ObservationError):
        ticket.poll()


def test_consumed_nonmatching_prefix_may_be_evicted_without_losing_proof():
    log = OperationLog("peer", "instance-1", limit=1)
    ticket = OperationObserver(log.snapshot).arm("peer", "instance-1", "target", "complete")
    log.record("competing", "complete")
    assert ticket.poll() is None
    log.record("target", "complete", {"result": "fresh"})
    assert ticket.poll().payload == {"result": "fresh"}, "CONSUMED_PREFIX_EVICTION_IS_HARMLESS"


def test_new_ticket_can_arm_after_old_window_was_evicted():
    log = OperationLog("peer", "instance-1", limit=1)
    log.record("old-1", "complete")
    log.record("old-2", "complete")
    ticket = OperationObserver(log.snapshot).arm("peer", "instance-1", "target", "complete")
    assert ticket.poll() is None
    log.record("target", "complete", {"result": "post-arm"})
    assert ticket.poll().payload == {"result": "post-arm"}


def test_unannounced_missing_record_cannot_supply_a_complete_window():
    current = ObservationSnapshot("peer", "instance-1", 0, ())
    ticket = OperationObserver(lambda: current).arm("peer", "instance-1", "target", "complete")
    current = ObservationSnapshot("peer", "instance-1", 2, (
        Observation("peer", "instance-1", "target", "complete", 2, {}),))
    with pytest.raises(ObservationError):
        ticket.poll()


def test_source_counter_regression_is_not_an_empty_poll():
    current = ObservationSnapshot("peer", "instance-1", 1, (
        Observation("peer", "instance-1", "old", "complete", 1, {}),))
    ticket = OperationObserver(lambda: current).arm("peer", "instance-1", "target", "complete")
    current = ObservationSnapshot("peer", "instance-1", 0, ())
    with pytest.raises(ObservationError):
        ticket.poll()
