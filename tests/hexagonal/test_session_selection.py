"""Durable session creation order, legacy compatibility and replaceable policy."""

import pytest

from tests.hexagonal.contracts import session_selection as contract
from tests.hexagonal.support.application import STORAGE_ADAPTERS
from tests.hexagonal.support.delivery import CapturingSessionSelection, SessionSelectionFixture
from xorchestrate.adapters.delivery import LatestSessionSelection


@pytest.fixture(params=STORAGE_ADAPTERS)
def selection(request, tmp_path):
    fixture = SessionSelectionFixture(request.param, tmp_path / "selection.sqlite3")
    try:
        yield fixture
    finally:
        fixture.close()


@pytest.mark.parametrize("later_stamp", ["2026-11-12T11:00:00Z", "2026-11-12T12:00:00Z"], ids=["rollback", "equal"])
@pytest.mark.parametrize("reconstruct", [False, True], ids=["live", "reconstructed"])
def test_delivery_uses_creation_order_across_wall_clock_and_history_pages(selection, later_stamp, reconstruct):
    contract.latest_created_session_receives_delivery(selection, later_stamp=later_stamp, reconstruct=reconstruct)


def test_candidate_creation_sequence_is_store_witness_and_custom_policy_is_used(selection):
    contract.candidate_order_comes_from_matching_creation_witness(selection, CapturingSessionSelection)


@pytest.mark.parametrize("mixed", [False, True], ids=["all-legacy", "mixed"])
def test_legacy_record_metadata_does_not_claim_creation_order(selection, mixed):
    contract.legacy_record_metadata_does_not_claim_creation_order(selection, CapturingSessionSelection, mixed=mixed)


def test_policy_uses_creation_sequence_before_utc_and_identifier():
    earlier = {"id": "z-earlier", "driver": "agent", "created_at": "2026-11-12T12:00:00Z", "creation_sequence": 12}
    later = {"id": "a-later", "driver": "agent", "created_at": "2026-11-12T11:00:00Z", "creation_sequence": 25}
    contract.selection_is_independent_of_candidate_enumeration(LatestSessionSelection(), [earlier, later], "a-later")


@pytest.mark.parametrize("legacy_sequence", [None, 0, -1, True, 1.5, "9"])
def test_policy_prefers_witnessed_session_to_legacy_or_invalid_order(legacy_sequence):
    legacy = {"id": "z-legacy", "driver": "agent", "created_at": "2026-11-12T12:00:00Z"}
    if legacy_sequence is not None:
        legacy["creation_sequence"] = legacy_sequence
    witnessed = {"id": "a-witnessed", "driver": "agent", "created_at": "2026-11-12T11:00:00Z", "creation_sequence": 1}
    contract.selection_is_independent_of_candidate_enumeration(LatestSessionSelection(), [legacy, witnessed], "a-witnessed")


def test_policy_keeps_driver_constraint_and_legacy_timestamp_fallback():
    older = {"id": "z-old", "driver": "agent", "created_at": "2026-11-12T11:00:00Z"}
    newer = {"id": "a-new", "driver": "agent", "created_at": "2026-11-12T12:00:00Z"}
    other = {"id": "other", "driver": "other", "created_at": "2026-11-12T13:00:00Z", "creation_sequence": 100}
    policy = LatestSessionSelection({"driver": "agent"})
    contract.selection_is_independent_of_candidate_enumeration(policy, [older, newer, other], "a-new")
    assert policy.select({}, "bob", [other]) is None
