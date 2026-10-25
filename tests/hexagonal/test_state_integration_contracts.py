"""Active application consumers share the state contracts across both stores."""
import pytest

from tests.hexagonal.contracts.state import (
    application_consumes_changed_creation_guard,
    delivery_uncertainty_survives_restart_until_explicit_remedy,
    live_application_records_all_finite_lifecycles,
    retained_measurement_recovers_without_repeating_external_work,
)
from tests.hexagonal.support.state import LiveStateFixture


@pytest.fixture(params=["memory", "sqlite"])
def live_state(request, tmp_path):
    fixture = LiveStateFixture(request.param, tmp_path / "live-state.sqlite3")
    try:
        yield fixture
    finally:
        fixture.close()


@pytest.mark.parametrize("kind", ["session", "operation"])
def test_changed_model_controls_real_creation_and_has_reachable_remedy(live_state, kind):
    application_consumes_changed_creation_guard(live_state, kind)


def test_live_effects_produce_versioned_history_for_all_seven_finite_records(live_state):
    live_application_records_all_finite_lifecycles(live_state)


def test_unknown_delivery_is_retained_across_reconstruction_until_explicit_resolution(live_state):
    delivery_uncertainty_survives_restart_until_explicit_remedy(live_state)


def test_pending_measurement_recovers_retained_adapter_result_without_reexecution(live_state):
    retained_measurement_recovers_without_repeating_external_work(live_state)
