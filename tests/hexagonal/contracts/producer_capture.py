"""Observable producer-capture contracts, independent of transport fixtures."""

import json


def events_of(events, event_type):
    return [event for event in events if event.get("event_type") == event_type]


def retained_bytes_contract(event, content):
    payload = event["payload"]
    assert payload["encoding"] == "base64", "captured bytes need an explicit encoding"
    assert payload["captured_bytes"] == len(content), "retained length must describe retained bytes"
    assert payload["range_start"] >= 0
    assert payload["range_end"] >= payload["range_start"]
    return content


def require_event(events, event_type):
    matches = events_of(events, event_type)
    assert matches, f"missing observed {event_type} at the producer boundary"
    return matches[-1]


def assert_attempt_links(events, event_types):
    selected = [require_event(events, event_type) for event_type in event_types]
    identities = [event["context"].get("attempt_id") for event in selected]
    assert all(isinstance(value, str) and value for value in identities), "every observed attempt has identity"
    assert len(set(identities)) == 1, "one exchange must retain its original attempt identity"
    return identities[0]


def assert_protected_contract(events, secret, retained_contents):
    assert secret not in json.dumps(events), "credential escaped into an observation envelope"
    for content in retained_contents:
        assert secret.encode() not in content, "credential escaped into encoded retained bytes"


def assert_validation(events, status, code=None):
    event = require_event(events, "evaluator.validation")
    assert event["payload"]["status"] == status
    if code is not None:
        assert event["payload"]["code"] == code
    return event
