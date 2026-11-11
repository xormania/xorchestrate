"""Independent expectations for advisory diagnoses over executed evidence."""


def post_test_advice_is_bound(report, expected, original_result):
    assert report["stage"] == "post_test" and report["advisory"] is True, "POST_TEST_ADVICE_REMAINS_ADVISORY"
    assert report["deterministic_status"] == original_result["status"], "TRIAGE_CANNOT_CLEAR_REQUIRED_FAILURE"
    assert report["candidate"] == {
        "source_sha": expected["source_sha"], "tested_sha": expected["checkout"]["commit"],
        "tree": expected["checkout"]["tree"],
    }, "POST_TEST_CANDIDATE_BOUND"
    evidence_ids = {item["id"] for item in report["evidence"]}
    for row in report["assessments"]:
        assert set(row["evidence_ids"]) <= evidence_ids, "TRIAGE_REFERENCES_SUPPLIED_EVIDENCE"
        assert set(row["supporting_evidence_ids"]) <= set(row["evidence_ids"]), "TRIAGE_SUPPORT_REFERENCES_BOUND"


def provider_did_not_run(report, provider):
    assert report["status"] != "complete", "UNBOUND_TRIAGE_STAYS_INCONCLUSIVE"
    assert provider.calls == [], "UNBOUND_TRIAGE_MUST_NOT_CALL_PROVIDER"
    assert report["assessments"] == [], "UNBOUND_TRIAGE_MUST_NOT_CLAIM_DEFECT"
