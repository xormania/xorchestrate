"""Synthetic agy stream-json frames from published 1.1.27 wire evidence.

Illustrations only: not executed vendor output and not a live session.
Numeric usage values are synthetic and are not an accounting oracle.
denied_actions item fields are not invented; the key may be present as
an empty list. Provider and native-session schemas are not invented.

Evidence: published JSON result keys conversation_id, status, response
(string), duration_seconds, num_turns, usage; conditional error (string)
and structured_output/json_schema (object). Stream selectors
event=init|step_update|result. Changelog 1.1.20: benign tool
errors/permission denials do not fail the overall run. Changelog 1.1.27:
JSON gained denied_actions.

Measured native result.status=CANCELED (status-only: no error string)
with denied_actions present and an empty response. Fixtures may use
that measured status; they must not invent further enums.

PROVISIONAL: the candidate _envelope contract is pending review.
"""

from __future__ import annotations

import json

from ops.testkit import World

# Distinct from the worker stamp placeholder so a copied ref cannot pass.
LAUNCHER_REF = "fc9ad2c015d47c85b405018521b57fa57e212d90"
CONVERSATION_ID = "00000000-0000-4000-8000-000000000001"
PLACEHOLDER_REF = "harness-placeholder"
# Measured native vendor status; not an invented enum.
MEASURED_CANCELED = "CANCELED"

NINE = (
    "job",
    "status",
    "verdict",
    "counts",
    "findings",
    "artifacts",
    "spend",
    "stamp",
    "note",
)

DECOY_LEAD_NOTE = "decoy-scan-lead"
DECOY_TRAIL_NOTE = "decoy-scan-trail"


def canonical_envelope(**over):
    env = {
        "job": "plan",
        "status": "ok",
        "verdict": "approve",
        "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
        "findings": [],
        "artifacts": {},
        "spend": {"harness": "agy", "total": 0, "out": 0, "runs": 0},
        "stamp": {
            "ref": PLACEHOLDER_REF,
            "started": None,
            "ended": None,
        },
        "note": "agy-terminal-ok",
    }
    env.update(over)
    return env


def finding(severity="p2", *, where="ops/devlane/dispatch/CONTRACT.md",
            claim="synthetic claim", reproduce="python3 -m unittest x"):
    return {
        "severity": severity,
        "where": where,
        "claim": claim,
        "reproduce": reproduce,
    }


def published_usage():
    return {
        "input_tokens": 10,
        "output_tokens": 3,
        "thinking_tokens": 1,
        "cache_read_tokens": 7,
        "total_tokens": 13,
    }


def published_result(response, *, status="SUCCESS", error=None, extra=None):
    if not isinstance(response, str):
        response = json.dumps(response)
    payload = {
        "conversation_id": CONVERSATION_ID,
        "status": status,
        "response": response,
        "duration_seconds": 1,
        "num_turns": 1,
        "usage": published_usage(),
    }
    if error is not None:
        payload["error"] = error
    if extra:
        payload.update(extra)
    return payload


def init_frame():
    return {
        "event": "init",
        "conversation_id": CONVERSATION_ID,
        "init": {
            "cwd": "/synthetic",
            "tools": [],
            "permission_mode": "request-review",
        },
    }


def step_update_frame(*, state="ACTIVE", step_type="agent_response",
                      text_delta="", extra=None):
    body = {
        "conversation_id": CONVERSATION_ID,
        "step_index": 1,
        "state": state,
        "step_type": step_type,
        "text_delta": text_delta,
    }
    if extra:
        body.update(extra)
    return {"event": "step_update", "step_update": body}


def result_frame(result):
    return {"event": "result", "result": result}


def drop_fields(payload, *keys):
    """Copy payload with named keys removed.

    Plants a missing required published field. Callers pass the
    published name (e.g. status); this does not invent enum values.
    """
    out = dict(payload)
    for key in keys:
        del out[key]
    return out


def valid_terminal_stream(nested, **published_kw):
    """One init frame and one result frame; no scan-shaped decoys."""
    return ndjson_bytes(
        init_frame(),
        result_frame(published_result(nested, **published_kw)),
    )


def decoy_envelope(note):
    return canonical_envelope(note=note)


def dumps(obj):
    return json.dumps(obj)


def ndjson_bytes(*objs):
    parts = [dumps(obj) for obj in objs]
    return ("".join(part + "\n" for part in parts)).encode("utf-8")


def plant_ndjson(world: World, target, *objs):
    """Plant framed fixture bytes through the world's proven plant."""
    return world.plant_bytes(target, ndjson_bytes(*objs))


def stream_with_decoys(*middle, lead=None, trail=None):
    lead = decoy_envelope(DECOY_LEAD_NOTE) if lead is None else lead
    trail = decoy_envelope(DECOY_TRAIL_NOTE) if trail is None else trail
    return ndjson_bytes(lead, *middle, trail)
