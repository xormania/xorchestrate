"""Current source authority and real storage boundaries in telemetry read paths."""

import pytest

from tests.hexagonal.contracts.telemetry_review import (
    artifact_usage_contract,
    bounded_usage_contract,
    byte_limited_status_contract,
    invocation_disclosure_contract,
    operation_disclosure_contract,
    sparse_filter_contract,
)
from tests.hexagonal.support.telemetry_review import ReviewFixture


@pytest.fixture
def review(tmp_path):
    subject = ReviewFixture(tmp_path)
    try:
        yield subject
    finally:
        subject.close()


@pytest.mark.parametrize("state", ["completed", "rejected", "missing-provider", "spoofed-operation"])
@pytest.mark.parametrize("selector", ["providers", "capabilities"])
@pytest.mark.parametrize("boundary", ["events", "payload"])
@pytest.mark.parametrize("storage", ["inline", "artifact"])
def test_requested_invocation_arguments_keep_current_source_authority(tmp_path, state, selector, boundary, storage):
    subject = ReviewFixture(tmp_path, artifact_threshold_bytes=512 if storage == "artifact" else 65536)
    try:
        invocation_disclosure_contract(subject, state, selector, boundary)
    finally:
        subject.close()


@pytest.mark.parametrize("boundary", ["events", "payload"])
@pytest.mark.parametrize("storage", ["inline", "artifact"])
def test_requested_invocation_arguments_keep_current_operation_reads(tmp_path, boundary, storage):
    subject = ReviewFixture(tmp_path, artifact_threshold_bytes=512 if storage == "artifact" else 65536)
    try:
        operation_disclosure_contract(subject, boundary)
    finally:
        subject.close()


def test_usage_reads_authorized_artifact_backed_observation(tmp_path):
    subject = ReviewFixture(tmp_path, artifact_threshold_bytes=512)
    try:
        artifact_usage_contract(subject)
    finally:
        subject.close()


@pytest.mark.parametrize("field", ["count", "latest", "source"])
def test_status_does_not_confuse_byte_limited_page_with_complete_dataset(review, field):
    byte_limited_status_contract(review, field)


@pytest.mark.parametrize("resource", ["work_id", "session_id", "operation_id"])
def test_sparse_resource_filter_obeys_raw_repository_scan_budget(review, resource):
    sparse_filter_contract(review, resource)


@pytest.mark.parametrize("explicit_budget", [False, True])
def test_large_retained_usage_obeys_view_budget_without_losing_raw_measurement(tmp_path, explicit_budget):
    subject = ReviewFixture(tmp_path, artifact_threshold_bytes=512)
    try:
        bounded_usage_contract(subject, explicit_budget)
    finally:
        subject.close()
