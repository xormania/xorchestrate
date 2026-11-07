"""Observable contracts for selected, bounded public-process stories."""

STORIES = ("coordination", "effects-recovery", "session-lifecycle", "work-acceptance", "native-callback")
LIMITS = {"sessions": 2, "turns": 4, "tool_calls": 5000}
ASSERTIONS = {
    "coordination": {"coordination_and_acknowledgement", "coordination_survives_restart"},
    "effects-recovery": {"cli_and_mcp_effects_are_idempotent", "declared_guardrail_prevents_effect",
                         "uncertain_effect_is_not_reexecuted", "effects_survive_restart"},
    "session-lifecycle": {"bridge_session_lifecycle"},
    "work-acceptance": {"work_requirements_candidate_and_contribution", "measured_artifact_is_accepted",
                        "requirements_revision_invalidates_acceptance_and_replay",
                        "new_measurement_and_changed_artifact_survive_restart"},
    "native-callback": {"configured_native_application_callback", "native_callback_survives_restart"},
}


def completed_story(receipt, story):
    assert receipt["status"] == "passed", receipt
    assert receipt["story"]["id"] == story
    assert receipt["story"]["version"] == 1
    expected = receipt["story"]["expected_checks"]
    assert expected, "A story needs independently named assertions"
    assert ASSERTIONS[story] <= set(expected)
    actual = [row["name"] for row in receipt["checks"]]
    assert len(actual) == len(set(actual))
    assert set(expected) <= set(actual)
    assert all(row["status"] == "passed" for row in receipt["checks"])
    assert receipt["evidence_scope"] == "local_protocol_fixture"
    assert receipt["budget"]["scope"] == "controller_requests_and_harness_submissions"
    assert receipt["budget"]["limits"] == LIMITS
    assert all(0 <= receipt["budget"]["consumed"][key] <= value for key, value in LIMITS.items())


def rejected_before_dispatch(subject, action, arguments):
    before = dict(subject.budget.consumed)
    subject.reject(action, arguments)
    assert subject.dispatched == []
    assert subject.budget.consumed == before, "A refused request cannot consume an admitted-operation receipt"
