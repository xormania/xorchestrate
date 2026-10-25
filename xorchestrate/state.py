"""Declared state transitions shared by application and adapter lifecycles.

The declaration/guard/move/history boundary follows the recovered Agentscient
engine. Persistence, identity, authority and workflow policy remain supplied by
the current application: this module neither opens a store nor performs I/O.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType

from .model import JSON, Conflict, OrchestrationError, Record
from .ports import Transaction

Guard = Callable[[JSON], bool]


def _definition_error(message: str) -> OrchestrationError:
    return OrchestrationError(message, "invalid_state_definition")


def _name(value, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise _definition_error(f"{label} must be a nonempty string.")


@dataclass(frozen=True)
class Transition:
    """A named event from declared sources to one target, optionally guarded.

    ``None`` is the source of a record's creation. Guards inspect a detached JSON
    context and return a boolean; they must not perform effects or modify shared
    state. Several guarded alternatives are permitted, but exactly one must be
    eligible for a decision. There is no first-match priority or implicit fallback.
    """

    event: str
    sources: frozenset[str | None]
    target: str
    guard: Guard | None = None

    def __post_init__(self):
        _name(self.event, "Transition event")
        _name(self.target, "Transition target")
        if isinstance(self.sources, (str, bytes)):
            raise _definition_error("Transition sources must be a nonempty collection of states.")
        try:
            sources = frozenset(self.sources)
        except (TypeError, ValueError) as error:
            raise _definition_error("Transition sources must be a nonempty collection of states.") from error
        if not sources or any(value is not None and (not isinstance(value, str) or not value) for value in sources):
            raise _definition_error("Transition sources must contain nonempty state names or the creation source None.")
        if self.guard is not None and not callable(self.guard):
            raise _definition_error("A transition guard must be callable.")
        object.__setattr__(self, "sources", sources)


@dataclass(frozen=True)
class MachineDefinition:
    """An immutable contract supplied by the component owning a lifecycle.

    Version is a positive contract revision, not a record's concurrency version.
    Definition replacement/migration is a composition responsibility; the engine
    does not silently alter existing definitions or authoritative records.
    """

    name: str
    version: int
    states: frozenset[str]
    transitions: tuple[Transition, ...]
    state_field: str = "status"

    def __post_init__(self):
        _name(self.name, "Machine name")
        _name(self.state_field, "State field")
        if type(self.version) is not int or self.version < 1:
            raise _definition_error("A machine definition version must be a positive integer.")
        if isinstance(self.states, (str, bytes)) or isinstance(self.transitions, (str, bytes)):
            raise _definition_error("States and transitions must be nonempty collections.")
        try:
            states = frozenset(self.states)
            transitions = tuple(self.transitions)
        except (TypeError, ValueError) as error:
            raise _definition_error("States and transitions must be nonempty collections.") from error
        if not states or any(not isinstance(value, str) or not value for value in states):
            raise _definition_error("Machine states must be nonempty strings.")
        if not transitions or any(not isinstance(value, Transition) for value in transitions):
            raise _definition_error("A machine must declare Transition values.")
        for position, transition in enumerate(transitions):
            if transition.target not in states or not transition.sources <= states | {None}:
                raise _definition_error("Every transition endpoint must be a declared state.")
            for prior in transitions[:position]:
                if prior.event == transition.event and prior.sources & transition.sources and (
                    prior.guard is None or transition.guard is None or prior.guard is transition.guard
                ):
                    raise _definition_error("Overlapping transitions need distinct guards; unguarded ambiguity is invalid.")
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "transitions", transitions)


@dataclass(frozen=True)
class TransitionDecision:
    machine: str
    definition_version: int
    event: str
    source: str | None
    target: str


def _json_snapshot(value, label: str) -> JSON:
    """Detach JSON without coercing tuples, non-string keys or nonfinite values."""
    if not isinstance(value, dict):
        raise OrchestrationError(f"{label} must be a JSON object.", "invalid_state_input")
    try:
        # JSON serialization rejects cycles and nonfinite numbers before any
        # authoritative mutation. Walk the original to reject its coercions.
        json.dumps(value, allow_nan=False)
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                if any(not isinstance(key, str) for key in item):
                    raise ValueError("Non-string JSON key")
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif type(item) not in (str, int, float, bool, type(None)):
                raise ValueError("Non-JSON value")
        return deepcopy(value)
    except (TypeError, ValueError, RecursionError) as error:
        raise OrchestrationError(f"{label} must contain valid JSON values.", "invalid_state_input") from error


class StateEngine:
    """Evaluate immutable definitions and apply decisions in a caller transaction.

    ``decide`` is a read-only policy evaluation. ``apply`` reads the current
    record under the caller's transaction, validates its exact version, and
    writes state plus required transition history together. Its full replacement
    data and guard inputs are detached. External effects run outside this module.
    """

    def __init__(self, definitions: Mapping[str, MachineDefinition]):
        if not isinstance(definitions, Mapping):
            raise _definition_error("State definitions must be a mapping of machine names to definitions.")
        copied = dict(definitions)
        if any(not isinstance(value, MachineDefinition) or key != value.name for key, value in copied.items()):
            raise _definition_error("Each registry key must equal its machine definition's name.")
        self._definitions = MappingProxyType(copied)

    @property
    def definitions(self) -> Mapping[str, MachineDefinition]:
        return self._definitions

    def _definition(self, machine_name: str) -> MachineDefinition:
        if not isinstance(machine_name, str) or machine_name not in self._definitions:
            raise OrchestrationError("Unknown state machine.", "unknown_state_machine")
        return self._definitions[machine_name]

    def decide(self, machine_name: str, current_state: str | None, event: str,
               context: JSON | None = None) -> TransitionDecision:
        definition = self._definition(machine_name)
        if current_state is not None and (
            not isinstance(current_state, str) or current_state not in definition.states
        ):
            raise OrchestrationError("The current state is not declared by this machine.", "invalid_state")
        if not isinstance(event, str) or not event:
            raise OrchestrationError("A transition event must be a nonempty string.", "invalid_transition")
        supplied = _json_snapshot({} if context is None else context, "Transition context")
        candidates = [rule for rule in definition.transitions if rule.event == event and current_state in rule.sources]
        if not candidates:
            raise OrchestrationError("This event is not allowed from the current state.", "invalid_transition", {
                "machine": machine_name, "event": event, "state": current_state,
            })
        eligible = []
        for rule in candidates:
            try:
                permitted = True if rule.guard is None else rule.guard(deepcopy(supplied))
            except OrchestrationError:
                raise
            except Exception as error:
                raise OrchestrationError("State transition guard could not be evaluated.", "state_guard_error") from error
            if type(permitted) is not bool:
                raise OrchestrationError("A state guard must return a boolean.", "invalid_guard_result")
            if permitted:
                eligible.append(rule)
        if not eligible:
            raise OrchestrationError("State transition prerequisites are not satisfied.", "state_guard_rejected", {
                "machine": machine_name, "event": event, "state": current_state,
            })
        if len(eligible) != 1:
            raise OrchestrationError("Multiple state transitions are eligible for this event.", "ambiguous_transition", {
                "machine": machine_name, "event": event, "state": current_state,
            })
        return TransitionDecision(machine_name, definition.version, event, current_state, eligible[0].target)

    def apply(self, tx: Transaction, kind: str, key: str, event: str, data: JSON,
              expected_version: int | None = None, context: JSON | None = None, *,
              machine_name: str | None = None) -> Record:
        """Apply one full replacement and retain its transition in ``tx``.

        The guard context reserves ``current`` and ``proposed`` for authoritative
        old/new record data. Other caller context is available to policy; only
        explicit string ``actor``/``at`` provenance enters transition history.
        A failure after attempting a write makes the transaction rollback-only,
        even when an outer caller catches that failure inside its transaction.
        """
        definition = self._definition(kind if machine_name is None else machine_name)
        if not isinstance(kind, str) or not kind or not isinstance(key, str) or not key:
            raise OrchestrationError("State subject kind and key must be nonempty strings.", "invalid_state_input")
        if expected_version is not None and (type(expected_version) is not int or expected_version < 1):
            raise OrchestrationError("Expected state version must be a positive integer.", "invalid_state_input")
        proposed = _json_snapshot(data, "Proposed state")
        supplied = _json_snapshot({} if context is None else context, "Transition context")
        current = tx.get(kind, key)
        if (expected_version is None and current is not None) or (
            expected_version is not None and (current is None or current.version != expected_version)
        ):
            raise Conflict()
        source = None if current is None else current.data.get(definition.state_field)
        if current is not None and source is None:
            raise OrchestrationError("The stored record has no declared state.", "invalid_state")
        guard_context = {**supplied, "current": None if current is None else deepcopy(current.data), "proposed": proposed}
        decision = self.decide(definition.name, source, event, guard_context)
        if proposed.get(definition.state_field) != decision.target:
            raise OrchestrationError("Proposed state does not match the declared transition target.", "invalid_state_target")
        abort = getattr(tx, "abort", None)
        if not callable(abort):
            raise OrchestrationError("State transitions require rollback-only transaction support.", "invalid_state_transaction")
        history = {
            "type": "state.transition", "machine": decision.machine, "definition_version": decision.definition_version,
            "subject": {"kind": kind, "key": key}, "event": decision.event, "source": decision.source,
            "target": decision.target, "previous_version": expected_version,
            "record_version": 1 if expected_version is None else expected_version + 1,
        }
        history.update({name: supplied[name] for name in ("actor", "at") if isinstance(supplied.get(name), str)})
        try:
            record = tx.put(kind, key, proposed, expected_version)
            tx.emit(history)
        except BaseException:
            abort()
            raise
        return record
