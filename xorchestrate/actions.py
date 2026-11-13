"""The public application action catalogue: names, descriptions and closed input schemas.

Workflow components contribute their own actions; this module assembles the one
catalogue that dispatch validates against and adapters describe.
"""
from copy import deepcopy

from .acceptance import ACCEPTANCE_ACTIONS
from .delivery import DELIVERY_ACTIONS
from .model import action_spec

STRING = {"type": "string", "minLength": 1}
OBJECT = {"type": "object"}
VERSION = {"type": "integer", "minimum": 1}
# Actions that create a session record bound to an adapter start receipt.
SESSION_CREATIONS = frozenset({"start_session", "attach_session", "reconnect_session"})

ACTIONS = {
    "create_work": action_spec("Create work from a goal and caller-supplied context.", {
        "goal": STRING, "title": STRING, "inputs": OBJECT, "context": OBJECT, "metadata": OBJECT,
    }, ("goal",)),
    "get_work": action_spec("Read work and its assignments, evidence, and sessions.", {"work_id": STRING}, ("work_id",), read=True),
    "list_work": action_spec("List work, optionally filtered by status.", {"status": STRING}, read=True),
    "update_work": action_spec("Update work with an explicit record version.", {
        "work_id": STRING, "expected_version": VERSION,
        "changes": {"type": "object", "minProperties": 1, "additionalProperties": False, "properties": {
            "goal": STRING, "title": STRING, "status": STRING, "inputs": OBJECT, "context": OBJECT, "metadata": OBJECT,
        }},
    }, ("work_id", "expected_version", "changes")),
    "add_actor": action_spec("Register an actor with caller-defined roles.", {
        "actor_id": STRING, "name": STRING, "roles": {"type": "array", "items": STRING, "uniqueItems": True}, "metadata": OBJECT,
    }, ("actor_id",)),
    "update_actor": action_spec("Update an actor without prescribing a role hierarchy.", {
        "actor_id": STRING, "expected_version": VERSION,
        "changes": {"type": "object", "minProperties": 1, "additionalProperties": False, "properties": {
            "name": STRING, "roles": {"type": "array", "items": STRING, "uniqueItems": True}, "metadata": OBJECT,
        }},
    }, ("actor_id", "expected_version", "changes")),
    "list_actors": action_spec("List registered actors.", {}, read=True),
    "assign": action_spec("Assign an actor to work in an optional caller-defined role.", {
        "work_id": STRING, "actor_id": STRING, "role": {"type": "string"}, "metadata": OBJECT,
    }, ("work_id", "actor_id")),
    "remove_assignment": action_spec("Remove an assignment at its observed version.", {
        "assignment_id": STRING, "expected_version": VERSION,
    }, ("assignment_id", "expected_version")),
    "relate": action_spec("Record a named, directed relationship between actors.", {
        "source": STRING, "target": STRING, "relationship": STRING, "metadata": OBJECT,
    }, ("source", "target", "relationship")),
    "remove_relation": action_spec("Remove a relationship at its observed version.", {
        "relation_id": STRING, "expected_version": VERSION,
    }, ("relation_id", "expected_version")),
    "send_message": action_spec("Route and retain a message using the configured routing policy.", {
        "destination": OBJECT, "body": {}, "work_id": STRING, "thread_id": STRING,
    }, ("destination", "body")),
    "inbox": action_spec("Read a recipient's retained messages.", {
        "recipient": STRING, "include_acknowledged": {"type": "boolean"}, "work_id": STRING,
    }, read=True),
    "acknowledge": action_spec("Acknowledge a message delivered to the caller.", {"message_id": STRING}, ("message_id",)),
    "start_session": action_spec("Start a configured session driver, once per explicit idempotency key.", {
        "driver": STRING, "specification": OBJECT, "idempotency_key": STRING, "work_id": STRING, "actor_id": STRING,
        "resume_session_id": STRING,
    }, ("driver", "specification", "idempotency_key")),
    "send_session": action_spec("Send a message through a session driver, once per key.", {
        "session_id": STRING, "message": OBJECT, "idempotency_key": STRING,
    }, ("session_id", "message", "idempotency_key")),
    "poll_session": action_spec("Collect session output, retaining the result for replay of this key.", {
        "session_id": STRING, "idempotency_key": STRING,
    }, ("session_id", "idempotency_key")),
    "stop_session": action_spec("Stop a session, once per key.", {
        "session_id": STRING, "idempotency_key": STRING,
    }, ("session_id", "idempotency_key")),
    "list_sessions": action_spec("List sessions, optionally for one work item.", {"work_id": STRING}, read=True),
    "recover": action_spec("Recover sessions interrupted by a hard restart: reconnect those that can, mark the rest "
                           "disconnected; report each old session's replacement.",
                           {"session_ids": {"type": "array", "items": STRING}}),
    "capabilities": action_spec("Discover provider capabilities using provider/name addresses.", {"provider": STRING}, read=True),
    "invoke": action_spec("Invoke a discovered capability using trusted execution policy; effects require an idempotency key.", {
        "capability": STRING, "arguments": OBJECT, "idempotency_key": STRING, "work_id": STRING,
    }, ("capability", "arguments")),
    "assess": action_spec("Obtain an assessment, escalating uncertainty using configured evaluators.", {
        "question": STRING, "work_id": STRING, "context": OBJECT,
    }, ("question",)),
    "record_result": action_spec("Retain caller-supplied work evidence without changing policy.", {
        "work_id": STRING, "result": {}, "label": STRING, "metadata": OBJECT,
    }, ("work_id", "result")),
    "events": action_spec("Read the event stream after a sequence number.", {
        "after": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    }, read=True),
    "get_operation": action_spec("Inspect an external operation and any preserved outcome.", {"operation_id": STRING}, ("operation_id",), read=True),
    "resolve_operation": action_spec("Record a verified outcome of an interrupted effect without executing it again.", {
        "operation_id": STRING, "expected_version": VERSION,
        "outcome": {"enum": ["completed", "failed"]}, "result": {}, "reason": STRING,
    }, ("operation_id", "expected_version", "outcome", "reason")),
    **ACCEPTANCE_ACTIONS,
    **DELIVERY_ACTIONS,
}
_TELEMETRY_QUERY = {
    "after": {"type": "integer", "minimum": 0},
    "cutoff": {"type": "integer", "minimum": 0},
    "max_bytes": {"type": "integer", "minimum": 256, "maximum": 1048576},
    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    "event_type": STRING, "work_id": STRING, "session_id": STRING, "operation_id": STRING,
    "run_id": STRING, "attempt_id": STRING,
}
ACTIONS.update({
    "telemetry_status": action_spec("Inspect optional capture health and authorized source coverage.", {}, read=True),
    "telemetry_events": action_spec("Read bounded authorized observations with source provenance.", _TELEMETRY_QUERY, read=True),
    "telemetry_usage": action_spec("Normalize identified usage while retaining raw quantities and unknown prices.", _TELEMETRY_QUERY, read=True),
    "telemetry_compare": action_spec("Compare explicitly selected reported result populations with denominators and evidence limits.", {
        **{key: value for key, value in _TELEMETRY_QUERY.items() if key in {"after", "cutoff", "limit", "max_bytes"}},
        "baseline_run_id": STRING, "candidate_run_id": STRING, "baseline_work_id": STRING, "candidate_work_id": STRING,
        "event_types": {"type": "array", "minItems": 1, "items": STRING}, "unit": STRING,
        "passed_outcomes": {"type": "array", "items": STRING}, "failed_outcomes": {"type": "array", "items": STRING},
    }, read=True),
    "telemetry_payload": action_spec("Read a bounded payload belonging to an authorized observation.", {
        "event_id": STRING, "digest": STRING, "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1048576},
    }, ("event_id",), read=True),
    "telemetry_snapshot": action_spec("Export a verified snapshot after authorizing the complete evidence closure.",
        {"destination": STRING}, ("destination",)),
    "telemetry_verify": action_spec("Verify a local bundle under current authority for its complete evidence closure.",
        {"destination": STRING}, ("destination",), read=True),
    "telemetry_restore": action_spec("Restore an authorized verified bundle into a fresh configured destination.",
        {"bundle": STRING, "destination": STRING}, ("bundle", "destination")),
})
ACTIONS.update({name: deepcopy(ACTIONS["start_session"]) for name in ("attach_session", "reconnect_session")})
ACTIONS["detach_session"] = deepcopy(ACTIONS["stop_session"])
