"""Composed telemetry behavior across real input and provider transports."""

import pytest

from tests.hexagonal.contracts.telemetry_workflow import (
    blocked_export_contract,
    changed_authority_contract,
    comparison_authority_contract,
    comparison_changed_authority_contract,
    denied_bundle_contract,
    mixed_bundle_changed_authority_contract,
    mixed_read_changed_authority_contract,
    normalized_provider_usage_contract,
    public_episode_contract,
)
from tests.hexagonal.support.telemetry_workflow import Episode


def test_public_cli_mcp_episode_survives_verified_export_and_restore(tmp_path):
    subject = Episode(tmp_path)
    try:
        public_episode_contract(subject)
    finally:
        subject.close()


@pytest.mark.parametrize("operation", ["telemetry_snapshot", "telemetry_verify", "telemetry_restore"])
def test_public_bundle_operations_apply_current_whole_closure_authority(tmp_path, operation):
    subject = Episode(tmp_path, direct=True)
    try:
        denied_bundle_contract(subject, operation)
    finally:
        subject.close()


@pytest.mark.parametrize("operation", ["telemetry_snapshot", "telemetry_verify", "telemetry_restore"])
def test_public_bundle_operations_revalidate_policy_after_artifact_work(tmp_path, operation):
    subject = Episode(tmp_path, direct=True)
    try:
        changed_authority_contract(subject, operation)
    finally:
        subject.close()


def test_actual_provider_usage_reaches_public_normalization(tmp_path):
    subject = Episode(tmp_path, direct=True)
    try:
        normalized_provider_usage_contract(subject)
    finally:
        subject.close()


def test_public_comparison_keeps_reported_cohorts_and_current_authority(tmp_path):
    subject = Episode(tmp_path, direct=True)
    try:
        comparison_authority_contract(subject)
    finally:
        subject.close()


def test_blocked_operational_transport_does_not_hold_action_or_corpus(tmp_path):
    subject = Episode(tmp_path, direct=True)
    try:
        blocked_export_contract(subject)
    finally:
        subject.close()


def test_public_comparison_revalidates_union_after_candidate_query(tmp_path):
    subject = Episode(tmp_path, direct=True)
    try:
        comparison_changed_authority_contract(subject)
    finally:
        subject.close()


@pytest.mark.parametrize("operation", ["telemetry_snapshot", "telemetry_verify", "telemetry_restore"])
def test_public_bundle_revalidates_earlier_work_after_final_later_work_check(tmp_path, operation):
    subject = Episode(tmp_path, direct=True)
    try:
        mixed_bundle_changed_authority_contract(subject, operation)
    finally:
        subject.close()


@pytest.mark.parametrize("operation", ["telemetry_events", "telemetry_status", "telemetry_usage", "telemetry_payload"])
def test_public_read_rejects_mixed_work_authority_changes_during_final_checks(tmp_path, operation):
    subject = Episode(tmp_path, direct=True)
    try:
        mixed_read_changed_authority_contract(subject, operation)
    finally:
        subject.close()
