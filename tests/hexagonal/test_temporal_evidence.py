"""Retained ordering, not civil timestamps or capture arrival, proves sequence."""
from copy import deepcopy

import pytest

from scripts.verification.checker import VerificationFailure, exchange_evidence, verify
from tests.hexagonal.contracts.temporal_evidence import (
    EXCHANGE_VARIANTS,
    WALL_TIME_VARIANTS,
    assert_named_rejection,
    assert_qualified,
)
from tests.hexagonal.support.application import STORAGE_ADAPTERS
from tests.hexagonal.support.completion_evidence import completion_input
from tests.hexagonal.support.identity_evidence import CODEX, GROK, receipt_factory
from tests.hexagonal.support.raw_evidence import raw_exchange
from tests.hexagonal.support.temporal_evidence import (
    StoreOrder,
    composed_exchange,
    distort_wall_time,
    reverse_capture_lists,
)
from tests.hexagonal.support.verification_evidence import proof_chain


@pytest.mark.parametrize("kind", EXCHANGE_VARIANTS)
@pytest.mark.parametrize("wall_time", WALL_TIME_VARIANTS)
@pytest.mark.parametrize("reordered", [False, True])
def test_exchange_order_survives_clock_changes_and_capture_reordering(kind, wall_time, reordered):
    inputs = raw_exchange(kind)
    assert_qualified(verify(*inputs))
    distort_wall_time(inputs[0], wall_time)
    if reordered:
        reverse_capture_lists(inputs[0])
    assert_qualified(verify(*inputs))


@pytest.mark.parametrize("peer", ["codex", "claude", "grok"])
@pytest.mark.parametrize("wall_time", WALL_TIME_VARIANTS)
def test_native_turn_completion_uses_observed_order_when_timestamps_change(peer, wall_time):
    inputs, add = completion_input(peer)
    add("native_completion", activity="idle")
    assert_qualified(verify(*inputs))
    distort_wall_time(inputs[0], wall_time)
    reverse_capture_lists(inputs[0])
    assert_qualified(verify(*inputs))


@pytest.mark.parametrize("fault,diagnostic", [
    ("missing-envelope", "ordering_evidence_missing"),
    ("unknown-version", "ordering_evidence_version"),
    ("missing-message-creation", "record_order_missing"),
])
def test_required_ordering_evidence_cannot_be_replaced_by_plausible_timestamps(fault, diagnostic):
    inputs = raw_exchange()
    assert_qualified(verify(*inputs))
    raw = inputs[0]["raw_records"]
    if fault == "missing-envelope":
        del raw["ordering"]
    elif fault == "unknown-version":
        raw["ordering"]["version"] = 999
    else:
        order = StoreOrder(raw)
        order.events.remove(order.creation("message", "challenge"))
    assert_named_rejection(verify(*inputs), diagnostic)


@pytest.mark.parametrize("kind", ["busy", "work_exchange"])
def test_provider_report_requires_its_own_matching_store_witness(kind):
    inputs = raw_exchange(kind)
    assert_qualified(verify(*inputs))
    order = StoreOrder(inputs[0]["raw_records"])
    order.events.remove(order.marker(inputs[0]["provider_events"][0]))
    assert_named_rejection(verify(*inputs), "provider_order_missing")


def test_normalized_order_from_different_domains_cannot_be_compared():
    events, sessions, case = proof_chain()
    assert exchange_evidence(events, case, "nonce", "private", sessions)
    next(event for event in events if event["event"] == "queued")["order"]["source"] = "another-capture"
    with pytest.raises(VerificationFailure, match="^ordering_domain_mismatch$"):
        exchange_evidence(events, case, "nonce", "private", sessions)


@pytest.mark.parametrize("peer", ["codex", "claude", "grok"])
def test_later_active_observation_vetoes_idle_even_after_wall_clock_rollback(peer):
    inputs, add = completion_input(peer)
    add("native_completion", activity="idle")
    assert_qualified(verify(*inputs))
    add("activity", state="active", at="2026-10-23T00:00:00+00:00")
    assert_named_rejection(verify(*inputs), "native_completion_missing")


@pytest.mark.parametrize("wall_time", WALL_TIME_VARIANTS)
def test_product_receipt_uses_independent_creation_order(wall_time):
    subject = receipt_factory(CODEX, GROK)("exchange")
    assert subject.evaluate().accepted
    distort_wall_time(subject.raw, wall_time)
    result = subject.evaluate()
    assert result.accepted, ("PRODUCT_EXCHANGE_IGNORES_DIAGNOSTIC_TIME", result.detail)


@pytest.mark.parametrize("fault,diagnostic", [("missing", "exchange_order_missing"), ("reversed", "exchange_event_order")])
def test_product_receipt_cannot_use_wall_time_to_replace_missing_or_false_order(fault, diagnostic):
    subject = receipt_factory(CODEX, GROK)("exchange")
    assert subject.evaluate().accepted
    order = StoreOrder(subject.raw)
    challenge, reply = order.creation("message", "challenge"), order.creation("message", "reply")
    if fault == "missing":
        order.events.remove(reply)
    else:
        order.place(reply, challenge)
    result = subject.evaluate()
    assert not result.accepted, ("PRODUCT_EXCHANGE_REQUIRES_CREATION_ORDER", result.detail)
    assert diagnostic in result.diagnostics, result.detail


@pytest.mark.parametrize("implementation", STORAGE_ADAPTERS)
def test_actual_store_and_provider_capture_qualify_without_wall_clock_order(implementation, tmp_path):
    with composed_exchange(tmp_path, implementation) as captured:
        assert_qualified(verify(*captured))
        for wall_time in WALL_TIME_VARIANTS:
            inputs = deepcopy(captured)
            distort_wall_time(inputs[0], wall_time)
            reverse_capture_lists(inputs[0])
            assert_qualified(verify(*inputs))
