"""Provider composition reuses established telemetry disclosure and range contracts."""

import pytest

from tests.hexagonal.contracts.telemetry import (
    bounded_event_page_contract,
    bounded_payload_contract,
    changing_authorization_contract,
    disclosure_contract,
    raw_usage_contract,
)
from tests.hexagonal.contracts.telemetry_provider import (
    fresh_query_contract,
    protected_bundle_contract,
    provider_discovery_contract,
    report_provenance_contract,
    report_resource_authority_contract,
    report_session_scope_contract,
    restored_bundle_contract,
    shared_capture_contract,
)
from tests.hexagonal.support.telemetry_provider import ProviderFixture, without_capture
from xorchestrate.model import OrchestrationError


@pytest.fixture
def provider(tmp_path):
    subject = ProviderFixture(tmp_path)
    try:
        yield subject
    finally:
        subject.close()


@pytest.mark.parametrize("provider_name", ["telemetry", "observations"])
def test_telemetry_is_discovered_under_the_configured_provider_name(tmp_path, provider_name):
    subject = ProviderFixture(tmp_path, provider_name=provider_name)
    try:
        provider_discovery_contract(subject)
    finally:
        subject.close()


def test_provider_queries_share_capture_and_do_not_recursively_copy_results(provider):
    shared_capture_contract(provider)


@pytest.mark.parametrize("boundary", ["events", "payload"])
def test_provider_queries_keep_nested_resource_authority(provider, boundary):
    disclosure_contract(provider, boundary)


@pytest.mark.parametrize("boundary", ["events", "payload"])
def test_provider_queries_reject_context_changes_during_reads(provider, boundary):
    changing_authorization_contract(provider, boundary)


def test_provider_payload_ranges_reconstruct_protected_content(provider):
    bounded_payload_contract(provider)


def test_provider_event_pages_bound_bytes_and_reference_complete_payload(provider):
    bounded_event_page_contract(provider, True)


def test_provider_usage_preserves_raw_counter_uncertainty(provider):
    raw_usage_contract(provider)


@pytest.mark.parametrize("action", ["capabilities", "invoke"])
def test_provider_queries_require_normal_discovery_and_invocation_permission(provider, action):
    provider.capture("fixture.allowed", {"value": "protected"}, event_id="normal-authorization")
    provider.deny([action])
    assert provider.refusal("events") == "denied"


def test_provider_queries_are_fresh_even_with_a_repeated_key(provider):
    fresh_query_contract(provider)


def test_provider_preserves_global_telemetry_read_authorization_even_for_empty_sources(provider):
    provider.deny(["telemetry_events"])
    assert provider.refusal("events") == "denied", "TELEMETRY_PROVIDER_MUST_APPLY_SERVICE_ENTRY_AUTHORITY"


def test_report_records_caller_provenance_without_copying_payload_to_state(provider):
    report_provenance_contract(provider)


def test_report_authorizes_its_nested_resource_scope(provider):
    report_resource_authority_contract(provider)


def test_report_uses_the_resolved_session_scope_instead_of_conflicting_caller_work(provider):
    report_session_scope_contract(provider)


@pytest.mark.parametrize("field,value", [
    ("actor", "forged-runtime"), ("event_type", "application.completed"),
    ("basis", "observed"), ("operation_id", "forged-operation"),
    ("native_id", "forged-native"), ("event_id", "forged-event"),
])
def test_report_does_not_accept_runtime_provenance_fields(provider, field, value):
    arguments = {"name": "report", "payload": {"value": 1}, field: value}
    assert provider.refusal("report", arguments, actor="owner", key="forged-report") == "invalid_arguments"
    assert not provider.events(event_type="telemetry.reported")


def test_provider_snapshot_verify_and_restore_authorize_the_complete_bundle(provider):
    protected_bundle_contract(provider)


def test_provider_restore_reuses_the_verified_bundle_and_fences_repeats(provider):
    restored_bundle_contract(provider)


def test_provider_disabling_leaves_automatic_capture_available(tmp_path):
    subject = ProviderFixture(tmp_path, provider_enabled=False)
    try:
        work = subject.create_work("Capture remains available")
        assert subject.app.call("telemetry_events", {"work_id": work})["events"]
        assert subject.refusal("events") == "not_found"
    finally:
        subject.close()


def test_provider_can_report_disabled_capture_without_constructing_a_collector(tmp_path):
    subject = without_capture(tmp_path)
    try:
        provider_discovery_contract(subject)
        assert subject.status()["status"] == "disabled"
        assert subject.refusal("events", actor="owner") == "telemetry_unavailable"
    finally:
        subject.close()


def test_provider_can_expose_an_explicit_operation_subset(tmp_path):
    subject = ProviderFixture(tmp_path, provider_options={"operations": ["status", "events"]})
    try:
        assert {row["name"] for row in subject.descriptors()} == {"telemetry/status", "telemetry/events"}
        assert subject.status()["status"] == "ready"
        assert subject.refusal("report", {"name": "hidden", "payload": {}}, key="hidden") == "not_found"
    finally:
        subject.close()


@pytest.mark.parametrize("options", [
    {"unknown": True}, {"operations": ["status", "unknown"]},
    {"operations": ["status", "status"]}, {"operations": []},
])
def test_provider_rejects_invalid_options(tmp_path, options):
    subject = ProviderFixture(tmp_path, provider_options=options)
    try:
        with pytest.raises(OrchestrationError) as error:
            subject.descriptors()
        assert error.value.code in {"invalid_configuration", "configuration_error"}
    finally:
        subject.close()
