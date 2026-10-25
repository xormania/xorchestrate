"""One state-machine behavioral contract over both supported storage adapters."""
import pytest

from tests.hexagonal.contracts.state import (
    contention_has_one_winner_and_no_loser_history,
    history_failure_cannot_publish_state,
    legal_progress_and_guard_remedy,
    reconstruction_retains_state_versions_and_history,
    separate_subjects_remain_separate,
    stale_versions_include_same_state_and_aba,
    target_mismatch_and_duplicate_creation_are_atomic,
)
from tests.hexagonal.support.state import StoredStateFixture


@pytest.fixture(params=["memory", "sqlite"])
def state_fixture(request, tmp_path):
    fixture = StoredStateFixture(request.param, tmp_path / "state.sqlite3")
    try:
        yield fixture
    finally:
        fixture.close()


def test_legal_progress_guard_refusal_and_reachable_remedy(state_fixture):
    legal_progress_and_guard_remedy(state_fixture)


def test_same_state_changes_and_aba_reject_stale_versions(state_fixture):
    stale_versions_include_same_state_and_aba(state_fixture)


def test_declared_target_and_creation_identity_cannot_be_bypassed(state_fixture):
    target_mismatch_and_duplicate_creation_are_atomic(state_fixture)


@pytest.mark.parametrize("catch_inside", [False, True], ids=["propagated", "caught-by-caller"])
@pytest.mark.parametrize("after_append", [False, True], ids=["before-history-write", "after-history-write"])
def test_state_and_history_remain_atomic_on_adapter_failure(state_fixture, catch_inside, after_append):
    history_failure_cannot_publish_state(state_fixture, catch_inside=catch_inside, after_append=after_append)


def test_reconstructed_engine_and_persistent_store_keep_committed_state(state_fixture):
    reconstruction_retains_state_versions_and_history(state_fixture)


def test_simultaneous_competitors_publish_exactly_one_transition(state_fixture):
    contention_has_one_winner_and_no_loser_history(state_fixture)


def test_subject_identity_scopes_state_and_history(state_fixture):
    separate_subjects_remain_separate(state_fixture)
