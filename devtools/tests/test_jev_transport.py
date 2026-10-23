"""Provider wire and retry contracts; service retention is tested separately."""

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from fixtures.advice import answer
from fixtures.jev import JevTransportFixture

from devtools.configuration import defaults as load_policy
from devtools.jev import MODEL, Jev, checked_response


class JevTransportTests(JevTransportFixture, unittest.TestCase):


    def test_official_endpoint_auth_bounded_io_and_typed_response(self):
        self.opener.open.return_value = self.response(answer({}, self.questions))
        result = Jev().evaluate({"text": "source"}, self.questions)
        request = self.opener.open.call_args.args[0]
        self.assertEqual("https://api.typesafe.ai/v1/systemone", request.full_url)
        self.assertEqual("Bearer test-secret-never-log", request.get_header("Authorization"))
        self.assertEqual(8, self.opener.open.call_args.kwargs["timeout"])
        self.assertEqual(0.8, result["answers"]["q"]["noul"])

    def test_documented_score_wire_format_and_configured_timeout(self):
        questions = {"frustration": {"type": "score", "instructions": "How frustrated?",
                                     "criteria": ["Calm", "Frustrated", "Very angry"]}}
        value = {"model": MODEL, "answers": {"frustration": {
            "type": "score", "score": 1.05, "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
            "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05}, "confidence": 0.92}},
            "usage": {"input_tokens": 304, "output_tokens": 18}}
        self.opener.open.return_value = self.response(value)
        settings = load_policy()["transport"] | {"timeout": 3}
        response = Jev(settings).evaluate("Help! My payouts have been failing for 3 days.", questions)
        self.assertEqual(value, response)
        self.assertEqual(3, self.opener.open.call_args.kwargs["timeout"])
        wire = json.loads(self.opener.open.call_args.args[0].data)
        self.assertEqual({"state", "model", "questions"}, wire.keys())
        value["answers"]["frustration"].pop("legend")
        with self.assertRaisesRegex(ValueError, "legend"):
            checked_response(value, questions, MODEL)

    def test_rate_limit_retry_is_bounded_and_error_body_never_leaks(self):
        limited = urllib.error.HTTPError("url", 429, "secret", {"Retry-After": "0.5"}, io.BytesIO(b"secret input"))
        self.opener.open.side_effect = [limited, self.response(answer({}, self.questions))]
        with patch("devtools.jev.time.sleep") as sleep:
            Jev().evaluate({}, self.questions)
            sleep.assert_called_once_with(0.5)
        self.opener.open.side_effect = urllib.error.HTTPError("url", 529, "secret", {"Retry-After": "60"}, io.BytesIO(b"secret"))
        with patch("devtools.jev.time.sleep") as sleep, self.assertRaisesRegex(ValueError, "HTTP 529") as caught:
            Jev().evaluate({}, self.questions)
        sleep.assert_not_called()
        self.assertNotIn("secret", str(caught.exception))
