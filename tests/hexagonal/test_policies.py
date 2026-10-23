import json
import unittest
from unittest.mock import Mock

from xorchestrate.adapters.policies import ConfigurableRouting, RuleGuardrail
from xorchestrate.adapters.storage import MemoryStore
from xorchestrate.application import Application
from xorchestrate.model import Capability, OrchestrationError


class GuardrailTests(unittest.TestCase):
    def test_default_freedom_and_explicit_review_are_separate_from_assessment(self):
        self.assertTrue(RuleGuardrail().evaluate("new_action", {"assessment": {"outcome": "deny"}}).allowed)
        policy = RuleGuardrail({"rules": [{"effect": "review", "actions": ["invoke"],
                                          "capabilities": ["deploy/*"], "reason": "Owner reviews releases."}]})
        verdict = policy.evaluate("invoke", {"capability": {"name": "deploy/production"},
                                             "assessment": {"outcome": "approved"}})
        self.assertFalse(verdict.allowed)
        self.assertTrue(verdict.review)
        self.assertIn("releases", verdict.reason)
        self.assertTrue(policy.evaluate("invoke", {"capability": {"name": "test/unit"}}).allowed)

    def test_deny_cannot_be_masked_by_broad_allow_rule(self):
        policy = RuleGuardrail({"rules": [
            {"id": "allow", "effect": "allow", "actions": ["*"]},
            {"id": "deny", "effect": "deny", "actions": ["invoke"], "providers": ["cloud"],
             "roles": ["observer"], "where": {"arguments.mode": "write"}},
        ]})
        context = {"provider": "cloud", "actor_record": {"roles": ["observer"]}, "arguments": {"mode": "write"}}
        self.assertEqual(policy.evaluate("invoke", context).details, {"rule_ids": ["deny"]})
        self.assertFalse(policy.evaluate("invoke", context).allowed)
        context["arguments"]["mode"] = "read"
        self.assertTrue(policy.evaluate("invoke", context).allowed)

    def test_where_missing_and_boolean_number_are_not_equal(self):
        policy = RuleGuardrail({"rules": [{"effect": "deny", "where": {"arguments.value": True}}]})
        self.assertTrue(policy.evaluate("a", {}).allowed)
        self.assertTrue(policy.evaluate("a", {"arguments": {"value": 1}}).allowed)
        self.assertFalse(policy.evaluate("a", {"arguments": {"value": True}}).allowed)

    def test_where_matches_equivalent_numbers_inside_mixed_json_values(self):
        pairs = [
            (1, 1.0), (-1, -1.0), (0, -0.0), (1000, 1e3),
            (9007199254740992, 9007199254740992.0),
            ({"items": [1, {"count": 0, "enabled": True}, None], "label": "same"},
             {"label": "same", "items": [1.0, {"enabled": True, "count": -0.0}, None]}),
        ]
        for expected, actual in pairs:
            for expected, actual in ((expected, actual), (actual, expected)):
                with self.subTest(expected=expected, actual=actual):
                    policy = RuleGuardrail({"rules": [{"effect": "deny", "where": {"arguments.value": expected}}]})
                    self.assertFalse(policy.evaluate("a", {"arguments": {"value": actual}}).allowed)

    def test_where_preserves_json_types_structure_and_numeric_precision(self):
        pairs = [
            (True, 1), (True, 1.0), (False, 0), (False, -0.0), (1, "1"),
            (9007199254740993, 9007199254740992.0),
            ({"items": [1, {"enabled": True}]}, {"items": [1.0, {"enabled": 1}]}),
            ({"items": [1, 2]}, {"items": [2.0, 1.0]}),
            ({"items": [1]}, {"items": [1.0, 2]}),
            ({"items": [], "optional": None}, {"items": []}),
            ([1], {"0": 1}),
        ]
        for expected, actual in pairs:
            for expected, actual in ((expected, actual), (actual, expected)):
                with self.subTest(expected=expected, actual=actual):
                    policy = RuleGuardrail({"rules": [{"effect": "deny", "where": {"arguments.value": expected}}]})
                    self.assertTrue(policy.evaluate("a", {"arguments": {"value": actual}}).allowed)

    def test_numeric_spelling_cannot_bypass_a_denied_provider_effect(self):
        provider = Mock()
        provider.discover.return_value = [Capability("write", "Write a count", {
            "type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"],
        })]
        provider.invoke.return_value = {"written": 2}
        store = MemoryStore()
        self.addCleanup(store.close)
        identifiers = iter(range(100))
        app = Application(
            store=store, providers={"fixture": provider}, sessions={}, evaluators={},
            guardrail=RuleGuardrail({"rules": [{"actions": ["invoke"], "effect": "deny",
                                               "where": {"arguments.arguments.count": 1}}]}),
            routing=ConfigurableRouting(), clock=lambda: "now", identifiers=lambda: str(next(identifiers)),
        )
        for spelling in ("1", "1.0", "1e0"):
            with self.subTest(spelling=spelling), self.assertRaises(OrchestrationError) as caught:
                app.call("invoke", {"capability": "fixture/write", "arguments": {"count": json.loads(spelling)},
                                    "idempotency_key": spelling})
            self.assertEqual(caught.exception.code, "denied")
        provider.invoke.assert_not_called()
        app.call("invoke", {"capability": "fixture/write", "arguments": {"count": 2}, "idempotency_key": "allowed"})
        provider.invoke.assert_called_once()

    def test_rejects_misspelled_policy_and_copies_configuration(self):
        for options in ({"default": "maybe"}, {"rules": [{"effect": "deny", "actons": ["invoke"]}]},
                        {"rules": [{"effect": "deny", "actions": []}]},
                        {"rules": [{"id": "x", "effect": "deny"}, {"id": "x", "effect": "allow"}]}):
            with self.subTest(options=options), self.assertRaises(OrchestrationError):
                RuleGuardrail(options)
        options = {"rules": [{"effect": "deny", "actions": ["invoke"]}]}
        policy = RuleGuardrail(options)
        options["rules"].clear()
        self.assertFalse(policy.evaluate("invoke", {}).allowed)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.actors = [
            {"id": "alice", "roles": ["designer"]},
            {"id": "bob", "roles": ["author", "security"]},
            {"id": "casey", "roles": ["security"]},
        ]
        self.relations = [
            {"source": "alice", "target": "bob", "relationship": "consults"},
            {"source": "casey", "target": "alice", "relationship": "consults"},
            {"source": "alice", "target": "casey", "relationship": "unrelated"},
        ]
        self.routing = ConfigurableRouting()

    def test_direct_and_free_form_roles_union_deduplicates_stably(self):
        result = self.routing.recipients("alice", {"actors": ["casey", "alice"], "roles": ["security"]}, self.actors, [])
        self.assertEqual(result, ["alice", "bob", "casey"])
        self.assertEqual(self.routing.recipients("alice", {"roles": ["missing"]}, self.actors, []), [])

    def test_relationships_have_no_privileged_role_names(self):
        for direction, expected in (("outgoing", ["bob"]), ("incoming", ["casey"]), ("either", ["bob", "casey"])):
            with self.subTest(direction=direction):
                destination = {"relationship": {"name": "consults", "direction": direction}}
                self.assertEqual(self.routing.recipients("alice", destination, self.actors, self.relations), expected)

    def test_broadcast_is_explicit_and_configurable(self):
        self.assertEqual(self.routing.recipients("alice", {}, self.actors, []), [])
        self.assertEqual(self.routing.recipients("alice", {"all": True, "include_self": False}, self.actors, []), ["bob", "casey"])
        with self.assertRaises(OrchestrationError) as caught:
            ConfigurableRouting({"allow_broadcast": False}).recipients("alice", {"all": True}, self.actors, [])
        self.assertEqual(caught.exception.code, "forbidden")

    def test_invalid_destination_does_not_silently_become_broadcast(self):
        for destination in ({"actors": ["absent"]}, {"actor": "absent"}, {"actors": "bob"},
                            {"all": "false"}, {"everyone": True}, {"relationship": {"name": "x", "direction": "down"}}):
            with self.subTest(destination=destination), self.assertRaises(OrchestrationError):
                self.routing.recipients("alice", destination, self.actors, self.relations)


if __name__ == "__main__":
    unittest.main()
