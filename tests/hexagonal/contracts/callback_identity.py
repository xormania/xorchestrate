"""Callback joins expressed through the shared independent identity contract.

The action, phase and JSON-type dimensions are distinct from vendor encoding.
Their expected rejections remain literals from the identity requirement; a
support adapter translates semantic record/claim names into raw storage rows.
"""

from .identity import ClaimChange, IdentityScenario

INVALID_IDENTITIES = (("wrong", "contradictory"), ("null", None), ("boolean", True),
                      ("number", 1), ("array", []), ("missing", None))


CALLBACK_JOIN_SCENARIOS = (
    *tuple(IdentityScenario(f"{phase}-{action}-{claim}-{label}", "exchange",
                            (ClaimChange(phase + "/" + action, claim, value, absent=label == "missing"),),
                            False, "callback_identity_mismatch")
           for action in ("send_message", "record_result", "invoke")
           for phase in ("requested", "completed")
           for claim in ("native_session", "incarnation")
           for label, value in INVALID_IDENTITIES),
    *tuple(IdentityScenario(f"{phase}-application-session-{label}", "exchange",
                            (ClaimChange(phase + "/send_message", "application_session", value, absent=label == "missing"),),
                            False, "malformed_raw_evidence" if label == "missing" else "callback_identity_mismatch")
           for phase in ("requested", "completed")
           for label, value in (("other-qualified", "qualified-other"), ("unrecognized", "unrecognized-app"),
                                ("null", None), ("missing", None))),
    *tuple(IdentityScenario(f"{side}-nested-session-{label}", "exchange",
                            (ClaimChange(side, "nested_session", value),), False,
                            "native_call_missing_or_misbound" if side == "native" else "callback_identity_mismatch")
           for side in ("native", "requested/send_message", "completed/send_message")
           for label, value in (("wrong", "other-app"), ("null", None), ("boolean", False))),
    *tuple(IdentityScenario(f"durable-session-{claim}-{label}", "exchange",
                            (ClaimChange("session", claim, value),), False, "callback_session_binding")
           for claim in ("native_session", "incarnation", "actor")
           for label, value in (("wrong", "contradictory"), ("null", None), ("boolean", True))),
    # The old value matrix repeated this identical deletion three times. One
    # scenario owns that fault; invalid field values above remain independent.
    IdentityScenario("durable-session-missing", "exchange", (ClaimChange("session", "record", absent=True),),
                     False, "callback_session_binding"),
    *tuple(IdentityScenario(f"{phase}-actor-{label}", "exchange",
                            (ClaimChange(phase + "/send_message", "actor", value),), False, "callback_actor_binding")
           for phase in ("requested", "completed")
           for label, value in (("wrong", "other-actor"), ("null", None), ("boolean", False))),
    *tuple(IdentityScenario(f"{phase}-call-id-{label}", "exchange",
                            (ClaimChange(phase + "/send_message", "call_id", value),), False, "callback_call_binding")
           for phase in ("requested", "completed")
           for label, value in (("null", None), ("boolean", False), ("number", 1), ("array", []))),
    IdentityScenario("request-missing", "exchange", (ClaimChange("requested/send_message", "record", absent=True),),
                     False, "callback_request_missing_or_misbound"),
)


CALLBACK_LIFECYCLE_SCENARIOS = (
    *tuple(IdentityScenario(f"{side}-nested-session-coherent", "lifecycle",
                            (ClaimChange(side, "nested_session", "qualified-session"),))
           for side in ("native", "callback")),
    *tuple(IdentityScenario(f"{side}-nested-session-{label}", "lifecycle",
                            (ClaimChange(side, "nested_session", value),), False, "lifecycle_retained_observation_binding")
           for side in ("native", "callback")
           for label, value in (("wrong", "other-app"), ("null", None), ("boolean", False))),
    *tuple(IdentityScenario(f"{side}-actor-coherent", "lifecycle",
                            (ClaimChange(side, "actor", "qualified-actor"),))
           for side in ("native", "callback", "session", "evidence", "evidence-source")),
    *tuple(IdentityScenario(f"{side}-actor-{label}", "lifecycle",
                            (ClaimChange(side, "actor", value),), False, "lifecycle_actor_binding")
           for side in ("native", "callback", "session", "evidence", "evidence-source")
           for label, value in (("wrong", "other-actor"), ("null", None), ("boolean", False))),
)
