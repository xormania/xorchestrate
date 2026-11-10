"""Independent local native-readiness contracts, without vendor inference claims."""

import pytest

from scripts.testkit.operator_composition import prepare_run, repeat_settings
from tests.hexagonal.contracts.local_native_collection import index_population_contract, native_collection_contract
from tests.hexagonal.contracts.local_native_readiness import (
    default_capture_contract,
    explicit_disabled_capture_contract,
    isolated_capture_contract,
    project_language_contract,
    project_override_contract,
    public_setup_contract,
    repeat_capture_contract,
    repeat_snapshot_integrity_contract,
    saved_verify_interpreter_contract,
    selected_capture_sqlite_contract,
    selected_config_install_contract,
    selected_sqlite_contract,
)
from tests.hexagonal.support.local_native_collection import NativeCollection
from tests.hexagonal.support.local_native_readiness import LocalNativeReadiness


@pytest.fixture
def readiness(tmp_path):
    return LocalNativeReadiness(tmp_path)


def test_new_native_setup_enables_durable_capture_without_source_filter(readiness):
    default_capture_contract(readiness)


def test_setup_preserves_explicit_disabled_capture_and_operator_configuration(readiness):
    explicit_disabled_capture_contract(readiness)


@pytest.mark.parametrize("enabled", [True, False])
def test_doctor_requires_corpus_sqlite_only_for_selected_enabled_capture(readiness, enabled):
    selected_capture_sqlite_contract(readiness, enabled)


@pytest.mark.parametrize("language", ["rust", "python", "php"])
def test_doctor_checks_only_selected_project_language_and_native_clients(readiness, language):
    project_language_contract(readiness, language)


def test_missing_selected_project_prerequisite_is_explicit_without_launch(readiness):
    project_language_contract(readiness, "rust", missing="cargo")


@pytest.mark.parametrize("configured", ["absent", "local-default", "inline", "disabled", "custom"])
def test_managed_native_capture_is_per_run_and_preserves_explicit_policy(readiness, configured):
    isolated_capture_contract(readiness, configured)


def test_explicit_project_requirements_replace_language_defaults(readiness):
    project_override_contract(readiness)


@pytest.mark.parametrize("mode", ["incompatible", "compatible"])
def test_selected_interpreter_reports_sqlite_compatibility(readiness, mode):
    selected_sqlite_contract(readiness, mode)


@pytest.mark.parametrize("case", ["healthy", "disabled", "missing", "corrupt", "lineage", "external"])
def test_managed_collection_preserves_corpus_and_exposes_selected_source_gaps(tmp_path, case):
    subject = NativeCollection(tmp_path, case)
    try:
        native_collection_contract(subject, case)
    finally:
        subject.close()


def test_public_setup_accepts_selected_native_project_without_changing_existing_operator_settings(readiness):
    public_setup_contract(readiness)


@pytest.mark.parametrize("enabled", [True, False])
def test_setup_installs_one_dependency_set_without_rewriting_selected_config(readiness, enabled):
    selected_config_install_contract(readiness, enabled)


def test_public_verify_uses_saved_run_interpreter_through_real_bootstrap(readiness):
    saved_verify_interpreter_contract(readiness)


@pytest.mark.parametrize("mutation", ["empty", "subset"])
def test_public_verify_rejects_omitted_saved_index_entries_with_archive_intact(tmp_path, mutation):
    subject = NativeCollection(tmp_path, "healthy")
    try:
        index_population_contract(subject, mutation)
    finally:
        subject.close()


@pytest.mark.parametrize("policy", ["absent", "local-default", "shared-root", "custom", "disabled"])
def test_native_repeat_uses_fresh_capture_and_preserves_operator_policy_through_saved_inputs(readiness, policy):
    repeat_capture_contract(readiness, policy, prepare=prepare_run, repeat=repeat_settings)


def test_native_repeat_rejects_a_changed_saved_operator_input_snapshot(readiness):
    repeat_snapshot_integrity_contract(readiness, prepare=prepare_run, repeat=repeat_settings)

