import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from xorchestrate.adapters.evaluation import ToolEvaluator, TypeSafeEvaluator, checked_assessment
from xorchestrate.model import Capability, OrchestrationError


class FixtureProvider:
    def __init__(self, result):
        self.result, self.requests, self.closed = result, [], False
        self.discoveries = 0

    def discover(self):
        self.discoveries += 1
        return [Capability("reason", "Assess the supplied question.")]

    def invoke(self, name, arguments, context):
        self.requests.append((name, arguments, context))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        self.closed = True


class ToolEvaluatorTests(unittest.TestCase):
    def test_direct_and_mcp_assessments_preserve_missing_confidence_and_evidence(self):
        assessment = {"outcome": "uncertain", "rationale": "The observable evidence is incomplete."}
        for result in (assessment, {"structuredContent": assessment},
                       {"content": [{"type": "text", "text": json.dumps(assessment)}]}):
            with self.subTest(result=result):
                provider = FixtureProvider(result)
                evaluator = ToolEvaluator({"provider": "local", "capability": "reason", "arguments": {"model": "selected"}}, {"local": provider})
                answer = evaluator.assess("Does this satisfy the requested stack?", {"evidence": ["repo tool output"]})
                self.assertEqual(answer.outcome, "uncertain")
                self.assertIsNone(answer.confidence)
                self.assertEqual(answer.evidence, ())
                self.assertEqual(provider.requests[0][1]["model"], "selected")
                self.assertEqual(provider.requests[0][1]["context"], {"evidence": ["repo tool output"]})
                self.assertEqual(provider.discoveries, 1)
                evaluator.close()
                self.assertFalse(provider.closed)

    def test_errors_remain_distinct_without_exposing_provider_message(self):
        for result, code in (({"outcome": "pass", "confidence": True}, "evaluation_invalid"),
                             ({"outcome": "pass", "confidence": float("nan")}, "evaluation_invalid"),
                             ({"content": [{"type": "text", "text": "probably fine"}]}, "evaluation_invalid"),
                             ({"isError": True}, "evaluation_unavailable"),
                             (OrchestrationError("secret provider diagnostic", "capability_failed"), "evaluation_unavailable")):
            provider = FixtureProvider(result)
            with self.subTest(result=result), self.assertRaises(OrchestrationError) as caught:
                ToolEvaluator({"provider": "p", "capability": "reason"}, {"p": provider}).assess("question", {})
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("secret", str(caught.exception))

    def test_evidence_is_a_detached_json_snapshot(self):
        original = {"outcome": "ready", "confidence": 0.9, "evidence": [{"source": "tool", "status": "pass"}]}
        answer = checked_assessment(original)
        original["evidence"][0]["status"] = "changed"
        self.assertEqual(answer.evidence[0]["status"], "pass")
        with self.assertRaises(OrchestrationError):
            checked_assessment({"outcome": "ready", "evidence": [{"bad": float("inf")}]})


class TypeSafeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.response = {"model": "operator-selected-model", "answers": {"assessment": {
            "type": "choice", "choice": "uncertain", "confidence": 0.6,
            "probabilities": {"ready": 0.4, "uncertain": 0.6}}}, "usage": {"input_tokens": 3, "output_tokens": 2}}
        self.status, self.extra_headers = 200, {}
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                test.requests.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(test.status)
                for name, value in test.extra_headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(test.response if isinstance(test.response, bytes) else json.dumps(test.response).encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.options = {"endpoint": f"http://127.0.0.1:{self.server.server_port}/assess", "allow_http": True,
                        "model": "operator-selected-model", "criteria": {"ready": "Enough evidence", "uncertain": "More inference needed"},
                        "credential_env": "XORCHESTRATE_TEST_ASSESSMENT_KEY"}
        self.environment = patch.dict(os.environ, {"XORCHESTRATE_TEST_ASSESSMENT_KEY": "fixture-key-private"})
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_real_http_wire_and_configured_model_rubric(self):
        answer = TypeSafeEvaluator(self.options).assess("Evaluate the available evidence", {"tool": {"source": "mcp"}})
        self.assertEqual(answer.outcome, "uncertain")
        self.assertEqual(answer.confidence, 0.6)
        self.assertEqual(answer.evidence, ())
        headers, payload = self.requests[0]
        self.assertEqual(headers["Authorization"], "Bearer fixture-key-private")
        self.assertEqual(payload["model"], "operator-selected-model")
        self.assertEqual(payload["questions"]["assessment"]["criteria"], self.options["criteria"])
        self.assertNotIn("fixture-key-private", json.dumps(answer.details))

    def test_redirect_http_error_and_malformed_response_are_distinct(self):
        self.status = 302
        self.extra_headers = {"Location": "/elsewhere"}
        with self.assertRaises(OrchestrationError) as caught:
            TypeSafeEvaluator(self.options).assess("question", {})
        self.assertEqual(caught.exception.code, "evaluation_unavailable")
        self.assertEqual(len(self.requests), 1)
        self.status, self.extra_headers, self.response = 200, {}, b"bad json"
        with self.assertRaises(OrchestrationError) as caught:
            TypeSafeEvaluator(self.options).assess("question", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")

    def test_unknown_outcome_and_budgets_reject_without_recommendation(self):
        self.response["answers"]["assessment"]["choice"] = "invented"
        with self.assertRaises(OrchestrationError) as caught:
            TypeSafeEvaluator(self.options).assess("question", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")
        with self.assertRaises(OrchestrationError):
            TypeSafeEvaluator({**self.options, "max_request_bytes": 10}).assess("question", {})
        self.assertEqual(len(self.requests), 1)
        with self.assertRaises(OrchestrationError) as caught:
            TypeSafeEvaluator({**self.options, "max_response_bytes": 10}).assess("question", {})
        self.assertEqual(caught.exception.code, "evaluation_invalid")

    def test_credentials_never_become_inputs_outputs_or_error_messages(self):
        with self.assertRaises(OrchestrationError):
            TypeSafeEvaluator(self.options).assess("question", {"accidental": "fixture-key-private"})
        self.assertEqual(self.requests, [])
        self.response = {"model": "fixture-key-private"}
        with self.assertRaises(OrchestrationError) as caught:
            TypeSafeEvaluator(self.options).assess("question", {})
        self.assertNotIn("fixture-key-private", str(caught.exception))
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(OrchestrationError) as caught:
            TypeSafeEvaluator(self.options).assess("question", {})
        self.assertEqual(caught.exception.code, "evaluation_unavailable")
        self.assertEqual(len(self.requests), 1)


if __name__ == "__main__":
    unittest.main()
