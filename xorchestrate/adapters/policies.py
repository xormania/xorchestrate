"""Replaceable declarations for guardrails and organizational message routing.

These adapters supply useful defaults, not domain requirements. Installed policy
implementations can replace either adapter through the composition root.
"""

from __future__ import annotations

import copy
import fnmatch
import json

from ..model import JSON, OrchestrationError, Verdict


def _error(message: str) -> OrchestrationError:
    return OrchestrationError(message, "invalid_configuration")


def _strings(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(v, str) or not v for v in value):
        raise _error(f"{name} must be a nonempty list of strings.")
    return value


def _same_json_value(left: object, right: object) -> bool:
    """Compare JSON values without conflating booleans and numbers.

    JSON has one numeric type: 1 and 1.0 describe the same value. Compare
    numbers directly rather than coercing them to floats and losing precision.
    """
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same_json_value(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same_json_value(a, b) for a, b in zip(left, right))
    return left == right


class RuleGuardrail:
    """Allow by default; combine matching rules as deny, then review, then allow.

    Selectors within a rule intersect; entries within a selector are alternatives.
    actions/actors/providers/capabilities/roles use case-sensitive shell patterns.
    where matches exact JSON values at dotted context paths. Policy is trusted
    configuration, and is never read from tool responses or assessment outcomes.
    """

    _selectors = {"actions", "actors", "providers", "capabilities", "roles"}
    _effects = {"allow", "deny", "review"}

    def __init__(self, options: JSON | None = None):
        options = {} if options is None else options
        if not isinstance(options, dict) or options.keys() - {"default", "rules", "reason"}:
            raise _error("Guardrail options accept default, rules and reason.")
        self.default = options.get("default", "allow")
        if not isinstance(self.default, str) or self.default not in self._effects:
            raise _error("Guardrail default must be allow, deny or review.")
        self.reason = options.get("reason", "")
        if not isinstance(self.reason, str):
            raise _error("Guardrail reason must be text.")
        rules = options.get("rules", [])
        if not isinstance(rules, list):
            raise _error("Guardrail rules must be a list.")
        self.rules = []
        identifiers = set()
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict) or rule.keys() - self._selectors - {"id", "effect", "where", "reason"}:
                raise _error("A guardrail rule contains an unknown field.")
            if not isinstance(rule.get("effect"), str) or rule["effect"] not in self._effects:
                raise _error("Every guardrail rule needs effect allow, deny or review.")
            identifier = rule.get("id", str(index))
            if not isinstance(identifier, str) or not identifier or identifier in identifiers:
                raise _error("Guardrail rule identifiers must be unique, nonempty strings.")
            identifiers.add(identifier)
            for selector in self._selectors & rule.keys():
                _strings(rule[selector], selector)
            where = rule.get("where", {})
            if not isinstance(where, dict) or any(not isinstance(k, str) or not k or ".." in k or k.startswith(".") or k.endswith(".") for k in where):
                raise _error("Guardrail where must map dotted context paths to JSON values.")
            try:
                json.dumps(where, allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                raise _error("Guardrail where requires finite JSON values.") from None
            if not isinstance(rule.get("reason", ""), str):
                raise _error("Guardrail rule reasons must be text.")
            self.rules.append({**copy.deepcopy(rule), "id": identifier})

    @staticmethod
    def _matches(rule: JSON, action: str, context: JSON) -> bool:
        capability = context.get("capability", {})
        if isinstance(capability, dict):
            capability = capability.get("id", capability.get("name", ""))
        actor = context.get("actor_record") or {}
        values = {
            "actions": [action], "actors": [context.get("actor", "")],
            "providers": [context.get("provider", "")], "capabilities": [capability],
            "roles": actor.get("roles", []),
        }
        for selector, candidates in values.items():
            if selector in rule and not any(isinstance(candidate, str) and fnmatch.fnmatchcase(candidate, pattern)
                                           for candidate in candidates for pattern in rule[selector]):
                return False
        missing = object()
        for path, expected in rule.get("where", {}).items():
            actual = context
            for part in path.split("."):
                actual = actual.get(part, missing) if isinstance(actual, dict) else missing
            if actual is missing or not _same_json_value(actual, expected):
                return False
        return True

    def evaluate(self, action: str, context: JSON) -> Verdict:
        matching = [rule for rule in self.rules if self._matches(rule, action, context)]
        priority = {"deny": 3, "review": 2, "allow": 1}
        effect = max((rule["effect"] for rule in matching), key=priority.get) if matching else self.default
        deciding = [rule for rule in matching if rule["effect"] == effect]
        reason = "; ".join(rule["reason"] for rule in deciding if rule.get("reason")) or self.reason
        return Verdict(effect == "allow", reason, effect == "review", {"rule_ids": [rule["id"] for rule in deciding]})


class ConfigurableRouting:
    """Resolve explicit people, free-form roles and free-form graph relationships.

    Selections are combined as a union and returned in actor-list order. No role
    is privileged and no organizational hierarchy is required.
    """

    def __init__(self, options: JSON | None = None):
        options = {} if options is None else options
        if not isinstance(options, dict) or options.keys() - {"include_self", "allow_broadcast"}:
            raise _error("Routing options accept include_self and allow_broadcast.")
        if any(type(value) is not bool for value in options.values()):
            raise _error("Routing options must be booleans.")
        self.include_self = options.get("include_self", True)
        self.allow_broadcast = options.get("allow_broadcast", True)

    def recipients(self, sender: str, destination: JSON, actors: list[JSON], relations: list[JSON]) -> list[str]:
        if not isinstance(destination, dict) or destination.keys() - {"actor", "actors", "role", "roles", "relationship", "all", "include_self"}:
            raise OrchestrationError("Destination contains an unknown routing field.")
        for field in ("all", "include_self"):
            if field in destination and type(destination[field]) is not bool:
                raise OrchestrationError(f"Destination {field} must be a boolean.")
        known = {actor["id"] for actor in actors}
        selected: set[str] = set()
        for singular, plural in (("actor", "actors"), ("role", "roles")):
            values = destination.get(plural, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise OrchestrationError(f"Destination {plural} must be a list of nonempty strings.")
            values = list(values)
            if singular in destination:
                if not isinstance(destination[singular], str) or not destination[singular]:
                    raise OrchestrationError(f"Destination {singular} must be a nonempty string.")
                values.append(destination[singular])
            if singular == "actor":
                unknown = set(values) - known
                if unknown:
                    raise OrchestrationError("A destination actor does not exist.", "not_found", {"actors": sorted(unknown)})
                selected.update(values)
            else:
                selected.update(actor["id"] for actor in actors if set(actor.get("roles", [])) & set(values))
        if destination.get("all"):
            if not self.allow_broadcast:
                raise OrchestrationError("Broadcast routing is disabled by configuration.", "forbidden")
            selected.update(known)
        if "relationship" in destination:
            edge = destination["relationship"]
            if not isinstance(edge, dict) or edge.keys() - {"name", "direction", "from"}:
                raise OrchestrationError("Relationship routing accepts name, direction and from.")
            name, origin, direction = edge.get("name"), edge.get("from", sender), edge.get("direction", "outgoing")
            if not isinstance(name, str) or not name or not isinstance(origin, str) or not origin:
                raise OrchestrationError("Relationship name and origin must be nonempty strings.")
            if not isinstance(direction, str) or direction not in {"outgoing", "incoming", "either"}:
                raise OrchestrationError("Relationship direction must be outgoing, incoming or either.")
            for relation in relations:
                if relation["relationship"] != name:
                    continue
                if direction in {"outgoing", "either"} and relation["source"] == origin:
                    selected.add(relation["target"])
                if direction in {"incoming", "either"} and relation["target"] == origin:
                    selected.add(relation["source"])
        if not destination.get("include_self", self.include_self):
            selected.discard(sender)
        return [actor["id"] for actor in actors if actor["id"] in selected]
