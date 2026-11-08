"""Managed collection preserves exact corpus bytes and explicit source gaps."""


def native_collection_contract(subject, case):
    receipt, unchanged, proof_unchanged = subject.collect()
    telemetry = receipt.get("telemetry")
    assert isinstance(telemetry, dict), "MANAGED_COLLECTION_MUST_ACCOUNT_FOR_DECLARED_TELEMETRY_SOURCES"
    assert telemetry["native_acceptance"] == "not_established_by_telemetry"
    assert unchanged, "COLLECTING_TELEMETRY_MUST_NOT_REWRITE_SOURCE_CORPUS"
    assert proof_unchanged, "BYTE_CLOSURE_VERIFICATION_MUST_NOT_ESTABLISH_NATIVE_OR_PRODUCT_ACCEPTANCE"
    entries = telemetry["entries"]
    assert entries and any(entry["source"] == str(subject.descriptor) for entry in entries)
    if case == "healthy":
        assert telemetry["status"] == "verified"
        entry = next(entry for entry in entries if entry["source"] == str(subject.descriptor))
        assert entry["status"] == "verified"
        assert entry.get("source_completion") == "unknown", "VERIFIED_BYTES_MUST_NOT_INVENT_SOURCE_COMPLETENESS"
        assert subject.independent_bundle(entry, telemetry)
        accepted, stdout, rejected, detail = subject.public_verify_then_corrupt(entry)
        assert accepted == 0 and stdout.strip(), "PUBLIC_VERIFY_MUST_CHECK_SAVED_COLLECTION_TELEMETRY"
        assert rejected != 0 and detail.strip(), "PUBLIC_VERIFY_MUST_REJECT_A_CORRUPTED_CAPTURED_OBJECT"
    elif case == "disabled":
        assert telemetry["status"] == "disabled"
        assert all(entry["status"] == "disabled" for entry in entries)
        assert any(entry.get("reason") for entry in entries)
    else:
        assert telemetry["status"] == "partial", "UNAVAILABLE_OR_MISBOUND_CAPTURE_MUST_NOT_BE_LABELLED_VERIFIED"
        assert any(entry["status"] == "unavailable" and entry.get("reason") for entry in entries)
        assert telemetry["gaps"], "SOURCE_FAILURE_MUST_REMAIN_AN_EXPLICIT_COLLECTION_GAP"


def index_population_contract(subject, mutation):
    before, after, population, archive_unchanged = subject.verify_omitted_population(mutation)
    assert before == 0, "POPULATION_CONTROL_MUST_BEGIN_WITH_A_REAL_VERIFIED_COLLECTION"
    assert population >= (2 if mutation == "subset" else 1)
    assert archive_unchanged, "POPULATION_CONTROL_MUST_KEEP_ARCHIVED_INDEX_AND_BUNDLE_BYTES_INTACT"
    assert after != 0, "PUBLIC_VERIFY_MUST_REJECT_OMITTED_ARCHIVED_INDEX_POPULATION"

