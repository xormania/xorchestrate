"""Correlate delivered guidance with observed calls, without claiming causation.

These advisory events neither authorize calls nor decide whether work is ready.
The snapshot is taken before execution because successful acceptance can release
the caller's binding and change the work whose failure prompted the guidance.
"""

from __future__ import annotations

import json

from .store import canonical


def capture_recovery(store, member, action, arguments):
    """Return matching current guidance before a normally authorized call runs."""
    try:
        # Diagnostics imports services which use this module at their call boundary.
        from .diagnostics import Diagnostics

        diagnostics = Diagnostics(store, member)
        scope = diagnostics.current()
        if not scope:
            return []
        with store.connect() as db:
            rows = list(db.execute(
                "SELECT id,data FROM events WHERE actor=? AND work_order=? AND job IS ? "
                "AND kind='skill_guidance_delivered' ORDER BY id DESC LIMIT 100",
                (member, scope["work_order"], scope["job"]),
            ))
        if not rows:
            return []
        settings = diagnostics.settings(scope)
        identity = diagnostics.identity(scope)
        if identity is None or not settings["features"]["guidance"]:
            return []
        call = {"tool": action, "arguments": arguments}
        normalized = canonical(call)
        candidates, seen = [], set()
        for row in rows:
            data = json.loads(row["data"])
            if (data.get("context_identity") != identity or
                    data.get("configuration_revision") != settings["revision"]):
                continue
            original = canonical(data.get("failed_call")) == normalized
            suggested = [item for item in data.get("actions", []) if isinstance(item, dict) and
                         canonical({"tool": item.get("tool"), "arguments": item.get("arguments")}) == normalized]
            if not original and not suggested:
                continue
            signature = canonical([data.get("operation"), data.get("failed_call"),
                                   data.get("failure"), suggested])
            if signature in seen:
                continue
            seen.add(signature)
            candidates.append({
                "guidance_event": f"event:{row['id']}",
                "originating_operation": data.get("operation"),
                "work_order": scope["work_order"], "job": scope["job"],
                "context_identity": identity, "configuration_revision": settings["revision"],
                "action": action, "arguments": json.loads(canonical(arguments)),
                "original_call": original, "suggested_actions": suggested,
            })
        return candidates if diagnostics.unchanged(scope, identity, settings) else []
    except Exception:
        # Correlation and telemetry must never replace the actual tool outcome.
        return []


def _outcome(result, error=None):
    from .diagnostics import failed

    if error is not None or failed(result):
        return "failed"
    if not isinstance(result, dict) or not result:
        return "unknown"
    if result.get("state") in {"queued", "running"}:
        return "pending"
    if result.get("state") in {"uncertain", "unknown"}:
        return "unknown"
    # A worker receipt is not the underlying call's outcome. Inspect that result
    # even when the worker itself reached its completed state.
    if result.get("state") == "completed":
        return _outcome(result.get("result"))
    for key in ("result", "validation"):
        if isinstance(result.get(key), dict):
            nested = _outcome(result[key])
            if nested in {"unknown", "pending"}:
                return nested
    return "succeeded"


def _measurements(candidate, result):
    """Report the suggested check's measured facts, not completion readiness."""
    if candidate["action"] != "work_check" or not isinstance(result, dict):
        return []
    while isinstance(result.get("result"), dict):
        result = result["result"]
    result = result.get("validation", result)
    if not isinstance(result, dict) or not isinstance(result.get("checks"), list):
        return []
    observed = []
    for suggestion in candidate["suggested_actions"]:
        condition = suggestion.get("condition", {})
        condition = condition if isinstance(condition, dict) else {}
        check = condition.get("check") or candidate["arguments"].get("check")
        if not check:
            continue
        for measured in result["checks"]:
            if not isinstance(measured, dict) or measured.get("check") != check:
                continue
            revision, expected = result.get("revision"), condition.get("revision")
            changes = result.get("uncommitted")
            observed.append({
                "check": check, "passed": type(measured.get("exit_code")) is int and measured["exit_code"] == 0,
                "evidence": measured.get("evidence"), "revision": revision,
                "expected_revision": expected,
                "revision_matches": revision == expected if revision and expected else None,
                "workspace_clean": not changes if isinstance(changes, list) else None,
            })
    return observed


def record_recovery(store, member, candidates, *, operation, result=None, error=None):
    """Retain an observation; only a successful original retry clears its condition.

Suggested measurements retain their own evidence and source validity separately.
Their success is not proof that the original completion condition is satisfied,
or that the delivered guidance caused a subsequent successful call.
"""
    try:
        outcome = _outcome(result, error)
        for candidate in candidates:
            matches = (["original_call"] if candidate["original_call"] else [])
            if candidate["suggested_actions"]:
                matches.append("suggested_action")
            data = {
                key: candidate[key] for key in (
                    "guidance_event", "originating_operation", "work_order", "job",
                    "context_identity", "configuration_revision", "action", "arguments",
                )
            }
            data.update(
                subsequent_operation=operation, matches=matches, outcome=outcome,
                condition_resolved=(outcome == "succeeded") if candidate["original_call"] and
                outcome in {"succeeded", "failed"} else None,
            )
            measurement = _measurements(candidate, result) if outcome in {"succeeded", "failed"} else []
            if measurement:
                data["measurements"] = measurement
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                duplicate = db.execute(
                    "SELECT 1 FROM events WHERE actor=? AND work_order=? AND job IS ? "
                    "AND kind='recovery_action_observed' "
                    "AND json_extract(data,'$.guidance_event')=? "
                    "AND json_extract(data,'$.subsequent_operation') IS ? "
                    "AND json_extract(data,'$.outcome')=? LIMIT 1",
                    (member, candidate["work_order"], candidate["job"], candidate["guidance_event"],
                     operation, outcome),
                ).fetchone()
                if not duplicate:
                    store._event(db, member, "recovery_action_observed", data)
    except Exception:
        pass
