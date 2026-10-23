"""Evaluator errors retain known causes without turning failures into judgments."""

import io
import json
import os
import socket
import unittest
import urllib.error
from unittest.mock import patch

from fixtures import advice as test_advice
from fixtures.advice import AdviceFixture
from fixtures.jev import JevTransportFixture

from devtools.failures import Failure, failure_info
from devtools.jev import MAX_RESPONSE, MODEL, Jev, JevUnavailable, checked_response


class ProviderFailureTests(JevTransportFixture, unittest.TestCase):

    def test_missing_credentials_transport_and_http_have_distinct_recovery_conditions(self):
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}), self.assertRaises(JevUnavailable) as caught:
            Jev().evaluate({}, self.questions)
        self.assertEqual({"category": "evaluation_unavailable", "reason": "credentials_unavailable",
                          "details": {"retryable": False}}, failure_info(caught.exception))
        self.assertIn("TYPESAFE_API_KEY", str(caught.exception))
        self.opener.open.assert_not_called()

        for error in (socket.timeout("private provider diagnostic"), urllib.error.URLError("private provider diagnostic")):
            with self.subTest(error=type(error).__name__):
                self.opener.open.side_effect = error
                with self.assertRaises(JevUnavailable) as caught:
                    Jev().evaluate({}, self.questions)
                self.assertEqual("transport_unavailable", failure_info(caught.exception)["reason"])
                self.assertTrue(failure_info(caught.exception)["details"]["retryable"])
                self.assertNotIn("private", str(caught.exception))

        for status, retryable in ((401, False), (302, False), (429, True), (529, True)):
            with self.subTest(status=status):
                self.opener.open.side_effect = urllib.error.HTTPError(
                    "url", status, "private provider diagnostic", {"Retry-After": "60"}, io.BytesIO(b"private body"))
                with self.assertRaises(JevUnavailable) as caught:
                    Jev().evaluate({}, self.questions)
                self.assertEqual({"category": "evaluation_unavailable", "reason": "provider_http_error",
                                  "details": {"status": status, "retryable": retryable}}, failure_info(caught.exception))
                self.assertNotIn("private", str(caught.exception))
                if not retryable:
                    self.assertIn("operator", str(caught.exception))
                    self.assertNotIn("try later", str(caught.exception))

    def test_invalid_json_oversize_and_invalid_assessments_are_never_unavailable(self):
        for body, validation, diagnostic in ((b"not JSON", "invalid_json", "invalid JSON"),
                                             (b"x" * (MAX_RESPONSE + 1), "response_budget_exceeded", "size limit")):
            with self.subTest(validation=validation):
                self.opener.open.return_value = self.response(body)
                with self.assertRaises(Failure) as caught:
                    Jev().evaluate({}, self.questions)
                self.assertNotIsInstance(caught.exception, JevUnavailable)
                self.assertIn(diagnostic, str(caught.exception))
                self.assertEqual({"category": "evaluation_invalid", "reason": "invalid_response",
                                  "details": {"validation": validation}}, failure_info(caught.exception))
        response = test_advice.answer({}, self.questions)
        for changes, validation in (({"model": "unexpected"}, "model_mismatch"),
                                    ({"answers": {}}, "incomplete_evaluation"), ({"usage": {}}, "usage"),
                                    ({"answers": {"q": {"type": "noul", "noul": 10**400}}}, "probability")):
            with self.subTest(validation=validation), self.assertRaises(Failure) as caught:
                checked_response(response | changes, self.questions, MODEL)
            self.assertNotIsInstance(caught.exception, JevUnavailable)
            self.assertEqual("evaluation_invalid", failure_info(caught.exception)["category"])
            self.assertEqual("invalid_response", failure_info(caught.exception)["reason"])
            self.assertEqual(validation, failure_info(caught.exception)["details"]["validation"])

        questions = {"q": {"type": "choice", "instructions": "Choose", "criteria": {"a": None, "b": None}}}
        for change in ({"choice": "invented"}, {"confidence": float("nan")},
                       {"probabilities": {"a": 0.2, "b": 0.2}}, {"probabilities": {"a": 1, "invented": 0}}):
            value = test_advice.answer({}, questions)
            value["answers"]["q"].update(change)
            with self.subTest(change=change), self.assertRaises(Failure) as caught:
                checked_response(value, questions, MODEL)
            self.assertEqual("evaluation_invalid", failure_info(caught.exception)["category"])
            self.assertEqual("invalid_response", failure_info(caught.exception)["reason"])

    def test_request_and_configuration_errors_never_reach_transport(self):
        cases = ((lambda: Jev().evaluate({}, self.questions, "latest"), "invalid_model"),
                 (lambda: Jev().evaluate({}, {}), "invalid_questions"),
                 (lambda: Jev().evaluate("x" * 30000, self.questions), "request_budget_exceeded"),
                 (lambda: Jev().evaluate({"invalid": float("nan")}, self.questions), "invalid_input"),
                 (lambda: Jev(connection={}), "invalid_provider_configuration"))
        for call, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(Failure) as caught:
                call()
            self.assertNotIsInstance(caught.exception, JevUnavailable)
            self.assertEqual("invalid_request", failure_info(caught.exception)["category"])
            self.assertEqual(reason, failure_info(caught.exception)["reason"])
            if reason == "request_budget_exceeded":
                self.assertIn("input budget", str(caught.exception))
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "invalid key"}), self.assertRaises(Failure) as caught:
            Jev().evaluate({}, self.questions)
        self.assertEqual("invalid_credential_configuration", failure_info(caught.exception)["reason"])
        self.opener.open.assert_not_called()

    def test_unclassified_adapter_value_error_keeps_its_original_identity(self):
        provider = Jev()
        error = ValueError("Adapter rejected an unknown condition")
        with patch.object(provider.adapter, "evaluate", side_effect=error), self.assertRaises(ValueError) as caught:
            provider.evaluate({}, self.questions)
        self.assertIs(error, caught.exception)
        self.assertEqual({"category": "operation_failed", "reason": "unclassified", "details": {}}, failure_info(error))


class AdviceFailureTests(unittest.TestCase):
    setUp = AdviceFixture.setUp
    configure = AdviceFixture.configure
    assess = AdviceFixture.assess

    def test_retained_failures_preserve_causes_without_fabricating_assessments(self):
        self.configure({"decisions": {"failure_mode": "block"}})
        cases = ((JevUnavailable("Credentials absent", reason="credentials_unavailable"), "unavailable"),
                 (Failure("Provider returned invalid data", category="evaluation_invalid", reason="invalid_response"), "invalid_response"),
                 (Failure("Input budget exceeded", category="invalid_request", reason="request_budget_exceeded"), "invalid_request"),
                 (ValueError("Jev is unavailable, but this adapter did not classify its cause"), "failed"))
        for index, (error, outcome) in enumerate(cases, start=1):
            with self.subTest(outcome=outcome):
                self.provider.side_effect = error
                with self.assertRaises(ValueError) as caught:
                    self.assess()
                self.assertIs(error, caught.exception)
                events = self.store.events(kind="jev_evaluation")["events"]
                self.assertEqual(index, len(events))
                data = events[-1]["data"]
                self.assertEqual(outcome, data["outcome"])
                self.assertEqual(failure_info(error), data["failure"])
                self.assertEqual(str(error), data["error"])
                self.assertNotIn("answers", data)
                self.assertNotIn("evidence", data)
                self.assertEqual([], self.api.artifacts.list(query="jev-")["artifacts"])
        self.assertEqual("prepared", self.api.call("work_inspect", {})["workspace"])

    def test_original_provider_error_is_preserved_but_retained_credentials_are_redacted(self):
        self.configure({"decisions": {"failure_mode": "block"}})
        error = ValueError("Adapter failed with test-secret-value-12345")
        self.provider.side_effect = error
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-secret-value-12345"}), self.assertRaises(ValueError) as caught:
            self.assess()
        self.assertIs(error, caught.exception)
        event = self.store.events(kind="jev_evaluation")["events"][-1]
        self.assertEqual("failed", event["data"]["outcome"])
        self.assertIn("[REDACTED]", event["data"]["error"])
        self.assertNotIn("test-secret-value-12345", json.dumps(event))
