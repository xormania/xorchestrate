"""Default live orchestration lifecycle definitions, replaceable by composition.

These describe the active application's records. They introduce no conductor,
assignment workflow, role hierarchy, or completion policy. Work/contribution
labels remain caller-defined and acceptance remains independently evaluated.
"""

from .state import MachineDefinition, Transition

DURABLE_LIFECYCLES = frozenset({
    "operation", "session", "invocation", "delivery", "delivery_attempt",
    "native_call", "measurement",
})


def _edge(event, sources, target):
    return Transition(event, frozenset(sources), target)


def _same(event, states):
    return tuple(_edge(event, {state}, state) for state in sorted(states))


def _definition(name, states, transitions):
    return MachineDefinition(name, 1, frozenset(states), tuple(transitions))


def default_definitions():
    """A fresh definition registry for all active durable and native machines."""
    operation = {"pending", "completed", "failed", "unknown", "completed_unapplied"}
    session = {"starting", "active", "failed", "unknown", "stopped", "detached", "disconnected", "superseded"}
    closed = {"stopped", "detached", "disconnected", "superseded"}
    outcome = {"pending", "completed", "failed", "unknown"}
    delivery = {"queued", "pending", "submitted", "failed", "unknown"}
    attempt = {"pending", "submitted", "failed", "unknown", "reconciled"}
    definitions = {
        "operation": _definition("operation", operation, (
            _edge("create", {None}, "pending"),
            _edge("complete", {"pending"}, "completed"),
            _edge("fail", {"pending"}, "failed"),
            _edge("uncertain", {"pending"}, "unknown"),
            _edge("retain_success", {"pending", "unknown"}, "completed_unapplied"),
            *_same("retain_late", operation),
            _edge("resolve_completed", {"pending", "unknown", "completed_unapplied"}, "completed"),
            _edge("resolve_failed", {"pending", "unknown", "completed_unapplied"}, "failed"),
        )),
        "session": _definition("session", session, (
            _edge("create", {None}, "starting"),
            _edge("reserve", {"active"}, "active"),
            _edge("activate", {"starting", "unknown"}, "active"),
            _edge("send", {"active", "unknown"}, "active"),
            *_same("poll", session),
            _edge("stop", session, "stopped"),
            _edge("detach", session, "detached"),
            _edge("replace", session, "superseded"),
            _edge("disconnect", {"active", "unknown"}, "disconnected"),
            *_same("release_closed", closed),
            _edge("uncertain", {"starting", "active", "unknown"}, "unknown"),
            *_same("uncertain", closed),
            _edge("start_failed", {"starting", "unknown"}, "failed"),
            *_same("start_failed", closed),
            _edge("effect_failed", {"active", "unknown"}, "active"),
            *_same("effect_failed", closed),
        )),
        "invocation": _definition("invocation", outcome, (
            _edge("create", {None}, "pending"),
            _edge("complete", {"pending", "unknown"}, "completed"),
            _edge("fail", {"pending", "unknown"}, "failed"),
            _edge("uncertain", {"pending"}, "unknown"),
        )),
        "delivery": _definition("delivery", delivery, (
            _edge("create", {None}, "queued"),
            _edge("reserve", {"queued"}, "pending"),
            _edge("submit", {"pending"}, "submitted"),
            _edge("fail", {"pending"}, "failed"),
            _edge("uncertain", {"pending"}, "unknown"),
            _edge("retry", {"failed"}, "queued"),
            _edge("resolve_submitted", {"pending", "unknown"}, "submitted"),
            _edge("resolve_failed", {"pending", "unknown"}, "failed"),
        )),
        "delivery_attempt": _definition("delivery_attempt", attempt, (
            _edge("create", {None}, "pending"),
            _edge("submit", {"pending"}, "submitted"),
            _edge("fail", {"pending"}, "failed"),
            _edge("uncertain", {"pending"}, "unknown"),
            _edge("reconcile", {"pending", "unknown"}, "reconciled"),
            *_same("retain_late", attempt),
        )),
        "native_call": _definition("native_call", outcome, (
            _edge("create", {None}, "pending"),
            # A concurrently reconciled successful call remains the same fact.
            _edge("complete", {"pending", "completed"}, "completed"),
            _edge("fail", {"pending"}, "failed"),
            _edge("uncertain", {"pending"}, "unknown"),
            _edge("resolve", {"pending", "unknown"}, "completed"),
            *_same("retain_late", outcome),
        )),
        "measurement": _definition("measurement", outcome, (
            _edge("create", {None}, "pending"),
            _edge("complete", {"pending", "completed"}, "completed"),
            _edge("fail", {"pending"}, "failed"),
            _edge("uncertain", {"pending"}, "unknown"),
            _edge("recover", {"pending", "unknown"}, "completed"),
        )),
    }
    from .activity import activity_definitions

    definitions.update(activity_definitions())
    return definitions
