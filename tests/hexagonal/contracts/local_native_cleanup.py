"""Rejected collection bytes are disposable; source and accepted evidence are not."""


def collection_cleanup_contract(subject, case):
    observed = subject.collect(case)
    assert observed["canary_control_present"], "NATIVE_CLEANUP_CONTROL_MUST_BEGIN_WITH_REAL_CAPTURED_BINARY_CONTENT"
    assert observed["source_unchanged"], "NATIVE_REJECTION_CLEANUP_MUST_PRESERVE_SOURCE_DATABASES_AND_ARTIFACTS"
    assert observed["prior_export_unchanged"], "NATIVE_REJECTION_CLEANUP_MUST_PRESERVE_PRIOR_VALID_EXPORTS"
    assert observed["other_source_valid"], "NATIVE_REJECTION_CLEANUP_MUST_PRESERVE_OTHER_LEGITIMATE_CORPORA_AND_EXPORTS"
    assert observed["proof_unchanged"]
    assert observed["native_acceptance"] == "not_established_by_telemetry"
    if case == "healthy":
        assert observed["status"] == "verified" and observed["primary_status"] == "verified"
        assert observed["primary_valid"], "NATIVE_ACCEPTED_SOURCE_MUST_KEEP_VALID_BUNDLE_AND_RESTORED_BYTE_CLOSURE"
        assert observed["attempt_contains_canary"]
        return
    assert observed["status"] == "partial" and observed["primary_status"] == "unavailable"
    assert observed["primary_gap"]
    if case == "restored-identity":
        assert observed["restore_completed"] and observed["restore_fault_applied"], \
            "NATIVE_RESTORE_REJECTION_CONTROL_MUST_REACH_A_REAL_COMPLETED_RESTORE"
    assert observed["rejected_databases_absent"], "NATIVE_REJECTED_COPY_MUST_NOT_RETAIN_DATABASE_BYTES"
    assert observed["rejected_artifacts_absent"], "NATIVE_REJECTED_COPY_MUST_NOT_RETAIN_ARTIFACT_BYTES"
    assert not observed["attempt_contains_canary"], "NATIVE_REJECTED_COPY_BINARY_CONTENT_MUST_NOT_REMAIN_IN_EXPORT_OR_ARCHIVE"
    assert observed["rejected_markers_not_complete"]
