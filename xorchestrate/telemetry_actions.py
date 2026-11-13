"""One operation catalog shared by telemetry capabilities and legacy actions."""

from .model import action_spec

STRING = {"type": "string", "minLength": 1}

_TELEMETRY_QUERY = {
    "after": {"type": "integer", "minimum": 0},
    "cutoff": {"type": "integer", "minimum": 0},
    "max_bytes": {"type": "integer", "minimum": 256, "maximum": 1048576},
    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    "event_type": STRING, "work_id": STRING, "session_id": STRING, "operation_id": STRING,
    "run_id": STRING, "attempt_id": STRING,
}
TELEMETRY_OPERATIONS = {
    "status": action_spec("Inspect optional capture health and authorized source coverage.", {}, read=True),
    "events": action_spec("Read bounded authorized observations with source provenance.", _TELEMETRY_QUERY, read=True),
    "usage": action_spec("Normalize identified usage while retaining raw quantities and unknown prices.", _TELEMETRY_QUERY, read=True),
    "compare": action_spec("Compare explicitly selected reported result populations with denominators and evidence limits.", {
        **{key: value for key, value in _TELEMETRY_QUERY.items() if key in {"after", "cutoff", "limit", "max_bytes"}},
        "baseline_run_id": STRING, "candidate_run_id": STRING, "baseline_work_id": STRING, "candidate_work_id": STRING,
        "event_types": {"type": "array", "minItems": 1, "items": STRING}, "unit": STRING,
        "passed_outcomes": {"type": "array", "items": STRING}, "failed_outcomes": {"type": "array", "items": STRING},
    }, read=True),
    "payload": action_spec("Read a bounded payload belonging to an authorized observation.", {
        "event_id": STRING, "digest": STRING, "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1048576},
    }, ("event_id",), read=True),
    "snapshot": action_spec("Export a verified snapshot after authorizing the complete evidence closure.",
        {"destination": STRING}, ("destination",)),
    "verify": action_spec("Verify a local bundle under current authority for its complete evidence closure.",
        {"destination": STRING}, ("destination",), read=True),
    "restore": action_spec("Restore an authorized verified bundle into a fresh configured destination.",
        {"bundle": STRING, "destination": STRING}, ("bundle", "destination")),
    "report": action_spec("Record a caller-reported observation, separate from runtime evidence.", {
        "name": {"type": "string", "minLength": 1, "maxLength": 128},
        "payload": {"type": "object"}, "work_id": STRING, "session_id": STRING,
    }, ("name", "payload")),
}
