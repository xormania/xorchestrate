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


def subject_observations_are_unambiguous(report, expected):
    """Every subject resolves its own cases and receipts through retained IDs."""
    observations = [item for item in report["evidence"] if item["kind"] == "test_observation"]
    assert len(observations) == len(expected), "ALL_SUBJECT_OBSERVATIONS_RETAINED"
    identifiers = [item["id"] for item in observations]
    assert len(set(identifiers)) == len(identifiers), "SUBJECT_OBSERVATION_IDS_ARE_UNIQUE"
    by_id = {item["id"]: item["content"] for item in observations}
    assert {row["subject_id"] for row in report["assessments"]} == set(expected)
    for assessment in report["assessments"]:
        references = set(assessment["evidence_ids"]) & set(by_id)
        assert len(references) == 1, "SUBJECT_RESOLVES_ONE_OBSERVATION_PACKET"
        identifier = next(iter(references))
        assert identifier in assessment["supporting_evidence_ids"], "OBSERVATION_SUPPORT_ID_RETAINED"
        packet = by_id[identifier]
        wanted = expected[assessment["subject_id"]]
        assert [case["id"] for case in packet["cases"]] == [wanted["case_id"]], "OBSERVATIONS_BELONG_TO_SUBJECT"
        case = packet["cases"][0]
        assert case["outcome"] == "failed"
        assert set(packet["receipts"]) == {case["receipt_id"]}
        receipt = packet["receipts"][case["receipt_id"]]
        assert receipt["reference"]["lane"] == wanted["lane"], "OBSERVATION_RECEIPT_LANE_RETAINED"
        assert receipt["report_sha256"] == wanted["report_sha256"]
        assert receipt["inventory_sha256"] == wanted["inventory_sha256"]
