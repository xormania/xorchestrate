import unittest

from xorchestrate.adapters.policies import ConfigurableRouting, RuleGuardrail
from xorchestrate.model import OrchestrationError


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
