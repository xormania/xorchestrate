"""Bridge frame ownership after short writes, uncertain writes and late replies."""

import pytest


def uncertain_write_closes_stream(subject, failure, *, already_pending):
    pending = subject.pending_request() if already_pending else None
    before = subject.wire
    subject.arm_failure(failure)
    with pytest.raises(subject.error_type) as unknown:
        subject.request("poisoned")
    assert unknown.value.details.get("outcome") == "unknown", "BRIDGE_WRITE_OUTCOME_STAYS_UNKNOWN"
    assert subject.wire[len(before):].startswith(b'{"id"'), "controlled fault must follow actual partial frame bytes"
    assert not subject.wire.endswith(b"\n"), "controlled failure must leave an unfinished frame"
    assert subject.closed, "BRIDGE_UNCERTAIN_WRITE_CLOSES_STREAM"
    poisoned_wire = subject.wire
    with pytest.raises(subject.error_type) as refused:
        subject.request("after-poisoned-frame")
    assert refused.value.details.get("outcome") == "not_started", "BRIDGE_CLOSED_WRITE_IS_NOT_STARTED"
    assert subject.wire == poisoned_wire, "BRIDGE_CLOSED_WRITE_NEVER_APPENDS_FRAME"
    if pending is not None:
        with pytest.raises(subject.error_type) as abandoned:
            pending.result(1)
        assert abandoned.value.code == "bridge_unavailable", "BRIDGE_POISONED_WRITE_RELEASES_PENDING_CALLER"
        assert abandoned.value.details.get("outcome") == "unknown", "BRIDGE_PENDING_OUTCOME_STAYS_UNKNOWN"


def complete_short_writes_preserve_stream(subject):
    expected = {"received": True}
    assert subject.complete_request("short-writes", expected) == expected
    assert subject.connection.send_calls > 1, "healthy control must use multiple short writes"
    assert not subject.closed, "BRIDGE_COMPLETE_SHORT_WRITES_STAY_USABLE"
    assert subject.complete_request("next", {"next": True}) == {"next": True}


def rejected_frame_preserves_stream(subject):
    with pytest.raises(subject.error_type) as oversized:
        subject.request("oversized", {"body": "x" * 1024})
    assert oversized.value.code == "bridge_protocol"
    assert oversized.value.details.get("outcome") == "not_started"
    assert subject.wire == b"", "BRIDGE_OVERSIZE_REFUSAL_WRITES_NOTHING"
    assert not subject.closed, "BRIDGE_PREWRITE_REFUSAL_PRESERVES_STREAM"
    assert subject.complete_request("small", {"small": True}) == {"small": True}


def response_timeout_preserves_stream(subject):
    with pytest.raises(subject.error_type) as unanswered:
        subject.request("slow-response")
    assert unanswered.value.code == "bridge_timeout"
    assert unanswered.value.details.get("outcome") == "unknown"
    assert subject.wire.endswith(b"\n"), "response-timeout control must have written a complete frame"
    assert not subject.closed, "BRIDGE_RESPONSE_TIMEOUT_PRESERVES_STREAM"
    subject.complete_late_request({"late": True})
    assert subject.complete_request("control", {"control": True}) == {"control": True}
