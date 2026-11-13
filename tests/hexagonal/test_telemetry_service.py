"""Current capture, source health and authority through public telemetry actions."""

import pytest

from tests.hexagonal.contracts.telemetry_service import (
    health_authority_change_contract,
    live_capture_contract,
    live_source_health_contract,
)
from tests.hexagonal.support.telemetry_service import ServiceFixture


@pytest.fixture
def telemetry_service(tmp_path):
    subject = ServiceFixture(tmp_path)
    try:
        yield subject
    finally:
        subject.close()


def test_queries_use_replaced_capture_and_disabled_state(telemetry_service):
    live_capture_contract(telemetry_service)


@pytest.mark.parametrize("kind", ["providers", "sessions", "evaluators"])
def test_status_uses_replaced_source_collections_and_bounded_health(telemetry_service, kind):
    live_source_health_contract(telemetry_service, kind)


def test_status_rejects_policy_changes_during_source_health_read(telemetry_service):
    health_authority_change_contract(telemetry_service)
