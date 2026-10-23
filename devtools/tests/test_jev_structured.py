"""Structured TypeSafe rubrics retain their boundaries through MCP and transport."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures import advice as test_advice
from fixtures.advice import AdviceFixture

from devtools.advice import clean_questions
from devtools.configuration import defaults as load
from devtools.configuration import validate
from devtools.jev import MODEL, Jev, checked_response
from devtools.jev_config import validate_entry, validate_question, validate_questions


def rubric():
    return {
        "proof": {"type": "noul", "instructions": {"question": "Does evidence cover the goal?", "required": ["receipt", "failure recovery"]},
                  "criteria": {"true": {"all": ["observed current behavior", "required boundary cases"]},
                               "false": ["missing evidence", "only a producer claim"]}},
        "scope": {"type": "choice", "instructions": ["Classify the responsibility", "Use its current job"],
                  "criteria": {"required": {"covers": ["objective", "done_when"], "optional": False},
                               "separate": ["useful improvement", "outside the assigned responsibility"]}},
        "coverage": {"type": "score", "instructions": None,
                     "criteria": [None, {"observed": ["one boundary"], "count": 1}, ["all boundaries", "current proof"]]},
    }


class StructuredQuestionTests(unittest.TestCase):
    def test_policy_and_wire_format_accept_documented_entry_shapes(self):
        questions = rubric()
        self.assertIs(questions, validate_questions(questions))
        policy = load()
        policy["scope"]["questions"] = questions
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "rubric.json"
            path.write_text(json.dumps(policy))
            loaded = validate(json.loads(path.read_text()))
        self.assertEqual(questions, loaded["scope"]["questions"])
        response = test_advice.answer({}, questions)
        self.assertEqual(response, checked_response(response, questions, MODEL))
        response["answers"]["coverage"]["legend"]["1"] = "Flattened text"
        with self.assertRaisesRegex(ValueError, "legend"):
            checked_response(response, questions, MODEL)

    def test_nonfinite_deep_oversized_and_non_json_entries_are_rejected(self):
        deep = "leaf"
        for _ in range(10):
            deep = {"nested": deep}
        for value in (True, 1, 0.9, {"nested": float("nan")}, {"nested": float("inf")},
                      {"nested": object()}, {1: "not a JSON key"}, "x" * 6001,
                      ["x"] * 513, deep, {"nested": "\0"}, {"nested": "\ud800"}):
            with self.subTest(value=type(value).__name__), self.assertRaises(ValueError):
                validate_entry(value)
        for value in (
            {"type": "noul", "instructions": "Is it done?", "criteria": {"true": "Yes"}},
            {"type": "noul", "instructions": "Is it done?", "unexpected": True},
            {"type": "score", "instructions": "Coverage?", "criteria": ["One"]},
            {"type": "choice", "instructions": "Scope?", "criteria": {"x": False}},
            {"type": [], "instructions": None},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_question(value)
        with self.assertRaises(ValueError):
            validate_questions({"wrong-name": rubric()["proof"]})

    def test_question_identifiers_survive_but_structured_credentials_do_not(self):
        questions = {"api_key": {"type": "choice", "instructions": {"password": "private-value", "task": "Inspect handling"},
                                 "criteria": {"token": {"authorization": "Bearer private-value", "example": "Bearer abcdefghijk"},
                                              "password": ["Check secret-value-123"]}}}
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "secret-value-123"}):
            sanitized = clean_questions(questions)
        self.assertEqual({"api_key"}, sanitized.keys())
        self.assertEqual({"token", "password"}, sanitized["api_key"]["criteria"].keys())
        self.assertEqual("[REDACTED]", sanitized["api_key"]["instructions"]["password"])
        self.assertNotIn("private-value", json.dumps(sanitized))
        self.assertNotIn("abcdefghijk", json.dumps(sanitized))
        self.assertNotIn("secret-value-123", json.dumps(sanitized))
        validate_questions(sanitized)

    def test_malformed_questions_cannot_reach_transport(self):
        with patch("devtools.jev.urllib.request.build_opener") as opener:
            with self.assertRaises(ValueError):
                Jev().evaluate({}, {"q": {"type": "noul", "instructions": {"invalid": float("nan")}}})
        opener.assert_not_called()

    def test_transport_keeps_structured_wire_request_and_score_legend(self):
        questions = rubric()
        response = test_advice.answer({}, questions)
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "fixture-key-123"}), \
                patch("devtools.jev.urllib.request.build_opener") as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            result = Jev().evaluate({"proof": "Current observed receipt behavior"}, questions)
            payload = json.loads(factory.return_value.open.call_args.args[0].data)
        self.assertEqual(questions, payload["questions"])
        self.assertEqual(response, result)


class StructuredAdviceTests(unittest.TestCase):
    setUp = AdviceFixture.setUp
    configure = AdviceFixture.configure
    assess = AdviceFixture.assess

    def test_mcp_preserves_structured_instructions_and_criteria_in_retained_requests(self):
        questions = [{"name": name, **question} for name, question in rubric().items()]
        first = self.assess(questions=questions)
        second = self.assess(questions=questions)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(1, len(self.requests))
        _, wire, _ = self.requests[0]
        self.assertEqual(rubric()["proof"]["instructions"], wire["s0_q0"]["instructions"]["question"])
        self.assertEqual(rubric()["proof"]["criteria"], wire["s0_q0"]["criteria"])
        self.assertEqual(rubric()["scope"]["criteria"], wire["s0_q1"]["criteria"])
        self.assertEqual(rubric()["coverage"]["criteria"], wire["s0_q2"]["criteria"])
        retained = self.api.call("work_context", {"reference": first["evidence"]})
        self.assertEqual(wire, json.loads(retained["content"])["request"]["questions"])
        self.assertEqual({"proof", "scope", "coverage"}, first["results"][0]["answers"].keys())

    def test_structured_scope_policy_and_legacy_level_alias_work_together(self):
        policy = load()
        policy["scope"]["questions"] = rubric()
        self.configure({"scope": policy["scope"]})
        self.api.call("work_assess", {"proposal": "Prove receipt recovery"})
        self.assertEqual(rubric(), self.requests[-1][1])
        levels = [None, {"covers": ["recovery", "normal receipt"]}]
        self.assess(questions=[{"name": "coverage", "type": "score", "instructions": ["Assess coverage"], "levels": levels}])
        self.assertEqual(levels, self.requests[-1][1]["s0_q0"]["criteria"])

    def test_conflicting_aliases_wrong_shapes_and_credentials_are_handled_before_provider(self):
        for question in (
            {"name": "q", "type": "choice", "instructions": "Select", "options": ["a", "b"], "criteria": {"a": None, "b": None}},
            {"name": "q", "instructions": "Complete?", "criteria": ["true", "false"]},
            {"name": "q", "instructions": {"nested": float("nan")}},
            {"name": "q", "instructions": False},
        ):
            with self.subTest(question=question), self.assertRaises(ValueError):
                self.assess(questions=[question])
        self.assertEqual([], self.requests)
        self.assess(questions=[{"name": "password", "instructions": {"task": "Inspect storage", "password": "unregistered-secret"}}])
        self.assertNotIn("unregistered-secret", json.dumps(self.requests))
