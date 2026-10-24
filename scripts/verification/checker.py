"""Verification composition and stable public API.

The behavioral predicates live in core; protocol decoding is selected here via
replaceable ports. A separate checkout isolates execution, not the correctness
oracle: expectations must still come from requirements and observed evidence.
"""
from . import core
from . import normalize as normalizer
from .adapters.native import default_decoders
from .evidence import decode_observations
from .model import VerificationFailure as VerificationFailure
from .model import canonical as canonical
from .model import digest as digest
from .model import require as require

PEERS = ("codex", "claude", "grok")
ORIGINS = {peer: decoder.origin for peer, decoder in default_decoders().items()}
identity_matches = core.identity_matches
arguments_match = core.arguments_match
expected_work = core.expected_work
signature = core.signature


def _decoders(decoders):
    return default_decoders() if decoders is None else decoders


def _context(decoders=None, *, callbacks=True):
    selected = _decoders(decoders)
    return core.VerificationContext({peer: decoder.origin for peer, decoder in selected.items()},
        callback_matcher=match_callback if callbacks and decoders is None else None,
        identity_matcher=identity_matches)


def native_inventory(peers=PEERS):
    return core.native_inventory(peers)


def default_criteria(brief_sha256):
    return {"version": 1, "owner_basis_sha256": brief_sha256,
            "requirements": [*(f"H{i}" for i in range(1, 9)), *(f"B{i}" for i in range(1, 6)), "A1", "A2"],
            "required_cases": native_inventory()}


def native_call(events, peer, tool, arguments, sessions, call_id=None, *, decoders=None):
    return core.native_call(events, peer, tool, arguments, sessions, call_id, _context(decoders))


def match_callback(events, event, sessions, *, decoders=None):
    return core.match_callback(events, event, sessions, _context(decoders, callbacks=False))


def exchange_evidence(events, case, nonce, memory, sessions, *, decoders=None):
    return core.exchange_evidence(events, case, nonce, memory, sessions, _context(decoders))


def tool_audit(events, sessions, *, decoders=None):
    return core.tool_audit(events, sessions, _context(decoders))


def _native_facts(raw, sessions, mode, decoders):
    return decode_observations(raw.get("observations", []), sessions,
        {row["id"]: row for row in raw.get("sessions", [])}, mode, decoders)


def _prepare(run, cases, decoders):
    result = {**run, "native_facts": _native_facts(run.get("raw_records", {}), run["sessions"], run.get("mode"), decoders),
              "lifecycle_native_facts": {}}
    for case in cases:
        if case.get("kind") != "lifecycle":
            continue
        peer, operation = case["peer"], case["operation"]
        if operation == "resume":
            raw = run.get("raw_records", {})
        else:
            records = next((item["records"] for item in run.get("lifecycle_records", []) if item.get("peer") == peer), {})
            raw = {"observations": records.get("native_observation", []), "sessions": records.get("session", [])}
        lifecycle = next((event for event in [*run.get("events", []), *run.get("lifecycle_events", [])]
                          if event.get("event") == "lifecycle" and event.get("peer") == peer
                          and event.get("operation") == operation), {})
        identities = {peer: lifecycle.get("after", run["sessions"].get(peer, {}))}
        result["lifecycle_native_facts"][(peer, operation)] = _native_facts(raw, identities, run.get("mode"), decoders)
    return result


def lifecycle_evidence(case, run, lifecycle, *, decoders=None):
    sessions = run.get("sessions", {case["peer"]: lifecycle.get("after", {})})
    prepared = _prepare({**run, "sessions": sessions, "lifecycle_events": [lifecycle]}, [{**case, "kind": "lifecycle"}], _decoders(decoders))
    return core.lifecycle_evidence(case, prepared, lifecycle, _context(decoders))


def completed_native_turns(raw, sessions, peer, latest, *, decoders=None):
    return core.completed_native_turns(_native_facts(raw, sessions, None, _decoders(decoders)), sessions, peer, latest)


def completion_evidence(own, run, peer, *, decoders=None):
    return core.completion_evidence(own, _prepare(run, [], _decoders(decoders)), peer)


def check_case(case, run, fixture, *, decoders=None):
    return core.check_case(case, _prepare(run, [case], _decoders(decoders)), fixture, _context(decoders))


def verify(run, criteria, expected_bindings, fixture, *, decoders=None):
    selected = _decoders(decoders)
    normalization_error = None
    try:
        if "raw_records" in run:
            arguments = (run["raw_records"], run["sessions"], run.get("provider_events", []), run.get("mode"))
            events = normalizer.normalize(*arguments) if decoders is None else normalizer.normalize(*arguments, decoders=selected)
            for event in run.get("lifecycle_events", []):
                events.append({**event, "seq": len(events) + 1})
            run = {**run, "events": events}
        run = _prepare(run, criteria["required_cases"], selected)
    except VerificationFailure as error:
        normalization_error = error.assertion
        run = {**run, "events": []}
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        normalization_error = "malformed_raw_evidence"
        run = {**run, "events": []}
    return core.verify(run, criteria, expected_bindings, fixture, _context(decoders), normalization_error)
