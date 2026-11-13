"""Composition roots: identical behavioral contracts across native adapters."""
import pytest

from tests.hexagonal.contracts.completion import (
    accepted_before_start,
    final_idle_claims_reconcile,
    overlapping_callbacks_and_tails,
    released_turns_remain_observable,
)
from tests.hexagonal.contracts.operation_observation import (
    each_release_reaches_only_the_next_held_boundary,
    witnessed_native_schedule,
)
from tests.hexagonal.support.completion_runtime import (
    CheckerCompletionSubject,
    ClaudePeer,
    CodexPeer,
    GrokPeer,
    composed_completion,
)


@pytest.mark.parametrize("peer", [CodexPeer, ClaudePeer, GrokPeer], ids=["codex", "claude", "grok"])
@pytest.mark.parametrize("store,transport", [("memory", "direct"), ("sqlite", "direct"), ("memory", "fifo")])
def test_overlapping_callbacks_and_native_tails(peer, store, transport, tmp_path):
    with composed_completion(peer, tmp_path, store, transport) as fixture:
        overlapping_callbacks_and_tails(fixture, CheckerCompletionSubject())


@pytest.mark.parametrize("peer", [CodexPeer, GrokPeer], ids=["codex", "grok"])
@pytest.mark.parametrize("store,transport", [("memory", "direct"), ("sqlite", "direct"), ("memory", "fifo")])
def test_accepted_submission_before_first_native_start(peer, store, transport, tmp_path):
    with composed_completion(peer, tmp_path, store, transport) as fixture:
        accepted_before_start(fixture, CheckerCompletionSubject())


@pytest.mark.parametrize("peer", [CodexPeer, ClaudePeer, GrokPeer], ids=["codex", "claude", "grok"])
@pytest.mark.parametrize("transport", ["direct", "fifo"])
def test_released_native_turns_remain_observable(peer, transport, tmp_path):
    with composed_completion(peer, tmp_path, transport=transport) as fixture:
        released_turns_remain_observable(fixture)


@pytest.mark.parametrize("peer", [CodexPeer, ClaudePeer, GrokPeer], ids=["codex", "claude", "grok"])
def test_final_idle_identity_claims_reconcile(peer, tmp_path):
    with composed_completion(peer, tmp_path) as fixture:
        final_idle_claims_reconcile(fixture, CheckerCompletionSubject())


@pytest.mark.parametrize("peer", [CodexPeer, ClaudePeer, GrokPeer], ids=["codex", "claude", "grok"])
@pytest.mark.parametrize("store,transport", [("memory", "direct"), ("sqlite", "direct"), ("memory", "fifo")])
def test_operation_bound_schedule_has_activation_and_exact_native_completion(peer, store, transport, tmp_path):
    with composed_completion(peer, tmp_path, store, transport) as fixture:
        witnessed_native_schedule(fixture)


def test_reusing_operation_label_cannot_borrow_earlier_activation(tmp_path):
    with composed_completion(CodexPeer, tmp_path) as fixture:
        fixture.submit("same-operation", tail=True)
        fixture.release_tail("same-operation")
        with pytest.raises(ValueError, match="operation"):
            fixture.submit("same-operation", tail=True)


@pytest.mark.parametrize("peer", [CodexPeer, ClaudePeer, GrokPeer], ids=["codex", "claude", "grok"])
@pytest.mark.parametrize("store,transport", [("memory", "direct"), ("sqlite", "direct"), ("memory", "fifo")])
def test_start_callback_and_tail_controls_compose_on_one_operation(peer, store, transport, tmp_path):
    with composed_completion(peer, tmp_path, store, transport) as fixture:
        each_release_reaches_only_the_next_held_boundary(fixture)
