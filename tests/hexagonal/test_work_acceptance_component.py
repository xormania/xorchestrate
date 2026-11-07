"""Acceptance workflow contracts through an explicit standalone composition."""
import pytest

from tests.hexagonal.contracts.state import retained_measurement_recovers_without_repeating_external_work
from tests.hexagonal.contracts.work_acceptance_component import (
    authorized_invocation_contract,
    live_configuration_contract,
)
from tests.hexagonal.support.work_acceptance_component import Fixture


@pytest.fixture(params=["memory", "sqlite"])
def acceptance(request, tmp_path):
    fixture = Fixture(request.param, tmp_path / "acceptance-component.sqlite")
    yield fixture
    fixture.close()


def test_measurement_uses_authorized_invocation_and_preserves_request_identity(acceptance):
    authorized_invocation_contract(acceptance)


def test_measurement_recovers_without_repeating_completed_external_work(acceptance):
    retained_measurement_recovers_without_repeating_external_work(acceptance)


def test_live_policy_and_provider_dependencies_preserve_currentness(acceptance):
    live_configuration_contract(acceptance)
