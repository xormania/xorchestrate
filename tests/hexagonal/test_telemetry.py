"""Run the shared telemetry contract against the active composition root."""

import pytest

from tests.hexagonal.contracts.telemetry import (
    absent_scope_contract,
    bounded_event_page_contract,
    bounded_payload_contract,
    changing_authorization_contract,
    composed_native_capture_contract,
    composed_semantic_capture_contract,
    concurrent_context_contract,
    configuration_state_contract,
    denied_scan_continuation_contract,
    disclosure_contract,
    fixed_cutoff_pagination_contract,
    forged_artifact_reference_contract,
    opaque_artifact_marker_contract,
    opaque_payload_contract,
    optional_capture_noninterference_contract,
    precreation_request_disclosure_contract,
    raw_usage_contract,
    rich_payload_policy_contract,
    source_manifest_disclosure_contract,
    stable_receipt_contract,
    unresolved_request_disclosure_contract,
)
from tests.hexagonal.support.telemetry import Fixture, native_fixture


@pytest.fixture
def telemetry(tmp_path):
    fixture = Fixture(tmp_path)
    try:
        yield fixture
    finally:
        fixture.close()


def test_composed_semantic_capture_retains_rich_work_content(telemetry):
    composed_semantic_capture_contract(telemetry)


def test_rich_payload_protects_credentials_and_preserves_unknown_zero_empty(telemetry):
    rich_payload_policy_contract(telemetry)


def test_reopened_capture_preserves_receipt_and_rejects_conflicting_identity(telemetry):
    stable_receipt_contract(telemetry)


def test_bounded_payload_reads_reconstruct_exact_retained_content(telemetry):
    bounded_payload_contract(telemetry)


@pytest.mark.parametrize("boundary", ["events", "payload"])
def test_nested_work_cannot_bypass_current_read_authorization(telemetry, boundary):
    disclosure_contract(telemetry, boundary)


def test_opaque_caller_payload_is_not_reinterpreted_as_owned_records(telemetry):
    opaque_payload_contract(telemetry)


def test_raw_usage_preserves_zero_unknown_and_unpriced_quantities(telemetry):
    raw_usage_contract(telemetry)


@pytest.mark.parametrize("boundary", ["events", "payload"])
def test_authorization_changes_during_read_reject_stale_projection(telemetry, boundary):
    changing_authorization_contract(telemetry, boundary)


@pytest.mark.parametrize("mode", ["disabled", "throws", "deferred", "unavailable"])
@pytest.mark.parametrize("outcome", ["return", "error", "exit"])
def test_optional_capture_preserves_action_outcome_and_single_effect(telemetry, mode, outcome):
    optional_capture_noninterference_contract(telemetry, mode, outcome)


def test_overlapping_calls_keep_immutable_work_actor_and_attempt_context(telemetry):
    concurrent_context_contract(telemetry)


def test_native_adapter_and_semantic_callback_reach_composed_collector(tmp_path):
    subject = native_fixture(tmp_path)
    try:
        composed_native_capture_contract(subject)
    finally:
        subject.close()


def test_disabled_invalid_and_enabled_collector_states_are_distinct(telemetry):
    configuration_state_contract(telemetry)


def test_fixed_cutoff_pages_exclude_concurrent_appends(telemetry):
    fixed_cutoff_pagination_contract(telemetry)


def test_denied_prefix_scan_keeps_continuation_progress(telemetry):
    denied_scan_continuation_contract(telemetry)


@pytest.mark.parametrize("kind", ["work", "session", "operation"])
@pytest.mark.parametrize("lifecycle", ["missing", "deleted"])
def test_absent_scope_does_not_become_public_content(telemetry, kind, lifecycle):
    absent_scope_contract(telemetry, kind, lifecycle)


def test_request_emitted_before_work_creation_obeys_current_work_authorization(telemetry):
    precreation_request_disclosure_contract(telemetry)


def test_early_rejected_request_does_not_default_to_global_disclosure(telemetry):
    unresolved_request_disclosure_contract(telemetry)


def test_source_manifest_only_reports_authorized_observed_membership(telemetry):
    source_manifest_disclosure_contract(telemetry)


@pytest.mark.parametrize("explicit_budget", [False, True])
def test_event_page_byte_bound_keeps_large_payload_exactly_retrievable(telemetry, explicit_budget):
    bounded_event_page_contract(telemetry, explicit_budget)


@pytest.mark.parametrize("scenario", [opaque_artifact_marker_contract, forged_artifact_reference_contract],
    ids=["literal-marker", "forged-reference"])
def test_opaque_marker_cannot_select_internal_artifact_storage(tmp_path, scenario):
    subject = Fixture(tmp_path, local_options={"artifact_threshold_bytes": 512})
    try:
        scenario(subject)
    finally:
        subject.close()
