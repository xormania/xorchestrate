"""Trusted configuration for the replaceable state engine and its definitions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from ..lifecycles import default_definitions
from ..model import OrchestrationError
from ..state import MachineDefinition, StateEngine, Transition
from .policies import RuleGuardrail


def _error(message):
    return OrchestrationError(message, "configuration_error")


def _condition(where):
    if not isinstance(where, dict):
        raise _error("A transition's where condition must be an object.")
    policy = RuleGuardrail({"default": "deny", "rules": [{"effect": "allow", "where": where}]})
    return lambda context: policy.evaluate("transition", context).allowed


def _definition(name, specification):
    if not isinstance(specification, dict) or specification.keys() - {"version", "states", "transitions", "state_field"}:
        raise _error("State definitions accept version, states, transitions and state_field.")
    states, transitions = specification.get("states"), specification.get("transitions")
    if not isinstance(states, list) or not isinstance(transitions, list):
        raise _error("A state definition requires states and transitions lists.")
    declared = []
    for transition in transitions:
        if not isinstance(transition, dict) or transition.keys() - {"event", "sources", "target", "where"}:
            raise _error("A transition accepts event, sources, target and where.")
        if not isinstance(transition.get("sources"), list):
            raise _error("Transition sources must be a list; null denotes creation.")
        declared.append(Transition(
            event=transition.get("event"), sources=transition["sources"], target=transition.get("target"),
            guard=_condition(transition["where"]) if "where" in transition else None,
        ))
    return MachineDefinition(name, specification.get("version", 1), states, declared,
                             state_field=specification.get("state_field", "status"))


def _guarded(original, policy, action):
    def guard(context):
        return (original is None or original(context)) and policy.evaluate(action, context).allowed
    return guard


class ConfiguredStateEngine(StateEngine):
    """Default lifecycle definitions plus trusted overrides and guard policies.

    options.definitions adds/replaces declarations by name. options.guards maps
    a machine name to ordinary RuleGuardrail options; actions are machine.event.
    Original definition guards and configured policy both have to permit a move.
    A custom module:factory may instead supply another engine implementation.
    """

    def __init__(self, options=None):
        options = {} if options is None else deepcopy(options)
        if not isinstance(options, dict) or options.keys() - {"definitions", "guards"}:
            raise _error("State engine options accept definitions and guards.")
        definitions = dict(default_definitions())
        overrides, policies = options.get("definitions", {}), options.get("guards", {})
        if not isinstance(overrides, dict) or not isinstance(policies, dict):
            raise _error("State definitions and guards must be maps.")
        for name, specification in overrides.items():
            definitions[name] = _definition(name, specification)
        for name, specification in policies.items():
            if name not in definitions:
                raise _error("A state guard names an unavailable definition.")
            if not isinstance(specification, dict):
                raise _error("A state guard policy must be an object.")
            policy = RuleGuardrail(specification)
            definition = definitions[name]
            definitions[name] = replace(definition, transitions=tuple(
                replace(transition, guard=_guarded(transition.guard, policy, f"{name}.{transition.event}"))
                for transition in definition.transitions
            ))
        super().__init__(definitions)

