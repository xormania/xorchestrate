"""Independent real callback security controls for producer capture."""

import pytest

from tests.hexagonal.contracts.telemetry_callback_review import (
    callback_disclosure_contract,
    explicit_callback_gap_contract,
    generic_callback_capture_contract,
    opaque_provider_contract,
)
from tests.hexagonal.support.telemetry_callback_review import CallbackFixture, generic_callback


@pytest.mark.parametrize("resource", ["work", "provider"])
@pytest.mark.parametrize("boundary", ["events", "payload", "digest"])
def test_real_callback_result_obeys_current_nested_resource_authority(tmp_path, resource, boundary):
    fixture = CallbackFixture(tmp_path, artifact=boundary != "events")
    try:
        callback_disclosure_contract(fixture, resource, boundary)
    finally:
        fixture.close()


def test_app_callback_wire_suppression_retains_explicit_durable_gap_and_source_byte_count(tmp_path):
    fixture = CallbackFixture(tmp_path)
    try:
        explicit_callback_gap_contract(fixture)
    finally:
        fixture.close()


def test_generic_unregistered_callback_keeps_opaque_wire_capture(tmp_path):
    generic_callback_capture_contract(generic_callback(tmp_path))


@pytest.mark.parametrize("boundary", ["events", "payload"])
def test_real_callback_preserves_opaque_provider_control_and_protocol_reply(tmp_path, boundary):
    fixture = CallbackFixture(tmp_path, artifact=boundary == "payload")
    try:
        opaque_provider_contract(fixture, boundary)
    finally:
        fixture.close()

