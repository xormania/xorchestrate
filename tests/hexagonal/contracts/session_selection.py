"""Session selection guarantees at the delivery consumer and policy boundaries."""


def latest_created_session_receives_delivery(fixture, *, later_stamp, reconstruct):
    earlier = fixture.start("earlier")
    # A later creation must still be found when its witness is on another page.
    fixture.emit({"type": "fixture.unrelated", "at": fixture.now}, repetitions=1001)
    fixture.now = later_stamp
    later = fixture.start("later")
    assert earlier["id"] > later["id"], "FIXTURE_IDS_OPPOSE_CREATION_ORDER"
    assert earlier["created_at"] >= later["created_at"], "FIXTURE_UTC_DOES_NOT_ORDER_CREATION"
    if reconstruct:
        fixture.reconstruct()
    result = fixture.dispatch()
    assert result["attempt"]["session_id"] == later["id"], "DELIVERY_USES_SESSION_CREATION_ORDER"
    assert result["attempt"]["status"] == "submitted"
    assert fixture.driver.calls[-1][1]["incarnation"] == later["backend"]["incarnation"]
    # Starting again after reconstruction must extend the durable ordering.
    fixture.now = "2026-11-12T10:00:00Z"
    newest = fixture.start("newest")
    assert fixture.dispatch()["attempt"]["session_id"] == newest["id"], "SESSION_ORDER_CONTINUES_AFTER_RECONSTRUCTION"


def candidate_order_comes_from_matching_creation_witness(fixture, selection_factory):
    earlier = fixture.start("earlier")
    later = fixture.start("later")
    witnessed = fixture.creation_sequences()
    assert witnessed[earlier["id"]] < witnessed[later["id"]]
    fixture.emit({"type": "work.created", "record_id": earlier["id"], "at": fixture.now})
    fixture.emit({"type": "session.created", "record_id": "unrelated-session", "at": fixture.now})
    fixture.reconstruct()
    policy = selection_factory(earlier["id"])
    fixture.app.session_selection = policy
    result = fixture.dispatch()
    assert result["attempt"]["session_id"] == earlier["id"], "CUSTOM_SESSION_SELECTION_REMAINS_EFFECTIVE"
    assert {row["id"]: row.get("creation_sequence") for row in policy.candidates} == witnessed, \
        "CANDIDATE_SEQUENCE_MATCHES_ITS_SESSION_CREATED_EVENT"


def selection_is_independent_of_candidate_enumeration(policy, candidates, expected):
    assert policy.select({}, "bob", candidates) == expected, "SESSION_SELECTION_ORDER"
    assert policy.select({}, "bob", list(reversed(candidates))) == expected, "SESSION_SELECTION_ORDER"


def legacy_record_metadata_does_not_claim_creation_order(fixture, selection_factory, *, mixed):
    witnessed = fixture.start("witnessed")
    fixture.legacy_session(witnessed["id"], "z-legacy-earlier", "2026-11-12T13:00:00Z", 999999)
    fixture.legacy_session(witnessed["id"], "a-legacy-later", "2026-11-12T14:00:00Z", 888888)
    if not mixed:
        fixture.stop(witnessed["id"])
    fixture.reconstruct()
    expected = witnessed["id"] if mixed else "a-legacy-later"
    assert fixture.dispatch()["attempt"]["session_id"] == expected, "LEGACY_SELECTION_USES_ONLY_WITNESSED_ORDER"
    policy = selection_factory(expected)
    fixture.app.session_selection = policy
    fixture.dispatch()
    legacy = [row for row in policy.candidates if row["id"] != witnessed["id"]]
    assert len(legacy) == 2
    assert all(row.get("creation_sequence") is None for row in legacy), "RECORD_METADATA_IS_NOT_CREATION_WITNESS"
