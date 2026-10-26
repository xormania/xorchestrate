"""Native work identities and effect lifecycles, independent of wire protocols.

Adapters hold their own lock while applying these transitions. Collection views
retain the old adapter vocabulary without maintaining another activity ledger;
future/process objects are payloads, never state-machine input or persisted state.
"""
from collections.abc import MutableMapping, MutableSet
from copy import deepcopy

from .model import OrchestrationError
from .state import MachineDefinition, StateEngine, Transition


def activity_definitions():
    return {
        "native_connection": MachineDefinition("native_connection", 1,
            frozenset({"starting", "connected", "disconnected", "closed"}), (
                Transition("create", frozenset({None}), "starting"),
                Transition("connect", frozenset({"starting", "connected"}), "connected"),
                Transition("disconnect", frozenset({"starting", "connected", "disconnected"}), "disconnected"),
                Transition("close", frozenset({"starting", "connected", "disconnected", "closed"}), "closed"),
            )),
        "native_work": MachineDefinition("native_work", 1,
            frozenset({"pending", "completed", "unknown"}), (
                Transition("start", frozenset({None, "pending"}), "pending"),
                Transition("start", frozenset({"completed"}), "completed"),
                Transition("finish", frozenset({"pending", "completed"}), "completed"),
                Transition("uncertain", frozenset({None, "pending", "completed", "unknown"}), "unknown"),
            )),
        "native_effect": MachineDefinition("native_effect", 1,
            frozenset({"pending", "completed", "failed", "unknown"}), (
                Transition("reserve", frozenset({None}), "pending"),
                Transition("succeed", frozenset({"pending"}), "completed"),
                Transition("fail", frozenset({"pending"}), "failed"),
                Transition("uncertain", frozenset({"pending"}), "unknown"),
                Transition("recover", frozenset({"pending"}), "unknown"),
            )),
    }


def activity_engine():
    return StateEngine(activity_definitions())


class NativeConnection:
    """Connection semantics; process handles and readiness signals stay outside."""

    def __init__(self, engine, identity):
        self.engine, self.identity = engine, identity
        self.state, self.version = None, 0
        self.transition("create")

    def transition(self, event):
        decision = self.engine.decide("native_connection", self.state, event,
            {"identity": self.identity, "version": self.version})
        self.state, self.version = decision.target, self.version + 1
        return self.state


class ActivityState:
    """One semantic work ledger; callers serialize it with their existing lock."""

    def __init__(self, engine=None, *, max_completed=1024):
        if type(max_completed) is not int or max_completed < 1:
            raise OrchestrationError("Completed work retention must be a positive integer.", "invalid_configuration")
        self.engine = engine if engine is not None else activity_engine()
        self.max_completed = max_completed
        self._items = {}
        self._anonymous_sequence = 0

    def transition(self, kind, identity, event):
        key = (kind, identity)
        before = self._items.get(key)
        context = {"kind": kind, "identity": identity,
                   "version": before["version"] if before else 0}
        decision = self.engine.decide("native_work", before["state"] if before else None, event, context)
        self._items[key] = {"state": decision.target, "version": context["version"] + 1}
        if decision.target == "completed":
            # Match the native adapters' existing bounded completion evidence.
            # Pending/uncertain identities are never forgotten to make room.
            completed = [item for item, row in self._items.items() if item[0] == kind and row["state"] == "completed"]
            for item in completed[:-self.max_completed]:
                del self._items[item]
        return decision.target

    def begin(self, kind, identity):
        return self.transition(kind, identity, "start")

    def finish(self, kind, identity):
        if (kind, identity) in self._items:
            return self.transition(kind, identity, "finish")
        return None

    def uncertain(self, kind="observation", identity="unclassified"):
        return self.transition(kind, identity, "uncertain")

    def active(self, kind):
        return tuple(identity for (group, identity), row in self._items.items()
                     if group == kind and row["state"] == "pending")

    @property
    def has_uncertainty(self):
        return any(row["state"] == "unknown" for row in self._items.values())

    @property
    def activity(self):
        if any(row["state"] == "pending" for row in self._items.values()):
            return "active"
        return "unknown" if self.has_uncertainty else "idle"

    def snapshot(self):
        return tuple({"kind": kind, "identity": identity, **deepcopy(row)}
                     for (kind, identity), row in self._items.items())

    def set_anonymous_count(self, count):
        if type(count) is not int or count < 0:
            raise OrchestrationError("Anonymous work count must be a nonnegative integer.", "invalid_request")
        active = list(self.active("anonymous_turn"))
        while len(active) < count:
            self._anonymous_sequence += 1
            identity = str(self._anonymous_sequence)
            self.begin("anonymous_turn", identity)
            active.append(identity)
        while len(active) > count:
            self.finish("anonymous_turn", active.pop())

    def set(self, kind, values=()):
        result = ActivitySet(self, kind)
        for value in values:
            result.add(value)
        return result

    def mapping(self, kind, values=None):
        result = ActivityMap(self, kind)
        for key, value in (values or {}).items():
            result[key] = value
        return result


class ActivitySet(MutableSet):
    """A mutable set view of currently pending identities in one work family."""

    def __init__(self, state, kind):
        self.state, self.kind = state, kind

    def __contains__(self, value):
        return value in self.state.active(self.kind)

    def __iter__(self):
        return iter(self.state.active(self.kind))

    def __len__(self):
        return len(self.state.active(self.kind))

    def add(self, value):
        self.state.begin(self.kind, value)

    def discard(self, value):
        if value in self:
            self.state.finish(self.kind, value)


class ActivityMap(MutableMapping):
    """Pending work view with non-semantic infrastructure payloads."""

    def __init__(self, state, kind):
        self.state, self.kind = state, kind
        self._payloads = {}

    def __getitem__(self, key):
        if key not in self.state.active(self.kind):
            raise KeyError(key)
        return self._payloads[key]

    def __setitem__(self, key, value):
        if self.state.begin(self.kind, key) == "pending":
            self._payloads[key] = value

    def __delitem__(self, key):
        if key not in self.state.active(self.kind):
            raise KeyError(key)
        self.state.finish(self.kind, key)
        del self._payloads[key]

    def __iter__(self):
        return iter(self.state.active(self.kind))

    def __len__(self):
        return len(self.state.active(self.kind))
