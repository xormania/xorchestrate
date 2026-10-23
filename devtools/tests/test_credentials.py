"""Private file credentials, provider configuration and exact secret redaction."""

import io
import json
import os
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

from fixtures.credentials import CredentialFixture
from fixtures.jev import JevTransportFixture

from devtools import adapters
from devtools.configuration import defaults, resolve
from devtools.credentials import CredentialUnavailable, credential_values, read_credential, redact
from devtools.jev import Jev, JevUnavailable


class FixtureAdapter:
    def __init__(self, settings, connection):
        self.settings, self.connection = settings, connection

    def evaluate(self, state, questions, model):
        return {"model": model, "answers": {name: {"type": "noul", "noul": 0.75} for name in questions},
                "usage": {"input_tokens": 0, "output_tokens": 0}}


class CredentialTests(CredentialFixture, unittest.TestCase):

    def test_private_file_is_read_at_use_time_and_never_exported(self):
        before = dict(os.environ)
        self.assertEqual("private-file-key-value", read_credential(self.reference))
        replacement = self.root / "replacement"
        replacement.write_text("rotated-private-key\n")
        replacement.chmod(0o400)
        replacement.replace(self.path)
        self.assertEqual("rotated-private-key", read_credential(self.reference))
        self.assertEqual(before, dict(os.environ))

    def test_missing_credentials_are_distinct_from_bad_configuration(self):
        with patch.dict(os.environ, {"OPERATOR_JEV": ""}):
            with self.assertRaises(CredentialUnavailable):
                read_credential({"env": "OPERATOR_JEV"})
            self.assertEqual((), credential_values({"credential": {"env": "OPERATOR_JEV"}}))
        self.path.unlink()
        with self.assertRaises(CredentialUnavailable):
            read_credential(self.reference)
        for reference in ({}, {"env": "X", "file": str(self.path)}, {"key": "private-inline-key"}):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                read_credential(reference)

    def test_permissions_symlinks_devices_size_and_control_characters_are_rejected(self):
        self.path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private"):
            read_credential(self.reference)
        self.path.chmod(0o600)
        linked = self.root / "linked"
        linked.symlink_to(self.path)
        with self.assertRaises(ValueError):
            read_credential({"file": str(linked)})
        fifo = self.root / "fifo"
        os.mkfifo(fifo, mode=0o600)
        with self.assertRaisesRegex(ValueError, "regular"):
            read_credential({"file": str(fifo)})
        for value in (b"private with whitespace", b"private\0key", b"\xff", b"x" * 4097):
            self.path.write_bytes(value)
            with self.subTest(value=value[:20]), self.assertRaises(ValueError) as caught:
                read_credential(self.reference)
            self.assertNotIn("private with whitespace", str(caught.exception))

    def test_file_and_custom_env_keys_are_redacted_without_losing_structure(self):
        value = {"message": "Use private-file-key-value", "nested": ["private-file-key-value"],
                 "private-file-key-value": "Secret in an object key", "api_key": "Private field",
                 "json": '{"example":"private-file-key-value"}'}
        result = redact(value, {"credential": self.reference})
        self.assertEqual("Use [REDACTED]", result["message"])
        self.assertEqual("Secret in an object key", result["[REDACTED]"])
        self.assertEqual("[REDACTED]", result["api_key"])
        self.assertNotIn("private-file-key-value", json.dumps(result))
        with patch.dict(os.environ, {"OPERATOR_JEV": "custom-env-secret"}):
            self.assertEqual("[REDACTED]", redact("custom-env-secret", {"credential": {"env": "OPERATOR_JEV"}}))

class ConfiguredTransportTests(JevTransportFixture, unittest.TestCase):
    def setUp(self):
        CredentialFixture.setUp(self)
        self.policy = defaults()
        self.connection = self.policy["connection"] | {"endpoint": "https://jev.example.test/v1/systemone",
                                                       "credential": self.reference}
        self.questions = {"proof": {"type": "noul", "instructions": "Assess private-file-key-value"}}
        self.transport()
        self.success = self.response({"model": self.policy["model"], "answers": {"proof": {"type": "noul", "noul": 0.9}},
                                      "usage": {"input_tokens": 5, "output_tokens": 1}})
        self.opener.open.return_value = self.success

    def test_configured_endpoint_file_rotation_and_redacted_payload(self):
        provider = Jev(connection=self.connection)
        provider.evaluate({"source": "private-file-key-value"}, self.questions)
        request = self.opener.open.call_args.args[0]
        self.assertEqual(self.connection["endpoint"], request.full_url)
        self.assertEqual("Bearer private-file-key-value", request.get_header("Authorization"))
        self.assertNotIn(b"private-file-key-value", request.data)
        self.path.write_text("rotated-key-value\n")
        provider.evaluate({"source": "rotated-key-value"}, self.questions)
        request = self.opener.open.call_args.args[0]
        self.assertEqual("Bearer rotated-key-value", request.get_header("Authorization"))
        self.assertNotIn(b"rotated-key-value", request.data)

    def test_secret_in_question_or_choice_identifier_cannot_reach_the_provider(self):
        self.path.write_text("fixture_secret_identifier\n")
        questions = [
            {"fixture_secret_identifier": {"type": "noul", "instructions": "Evaluate"}},
            {"q": {"type": "choice", "instructions": "Choose", "criteria": {
                "fixture_secret_identifier": "A", "ordinary": "B"}}},
        ]
        for value in questions:
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                Jev(connection=self.connection).evaluate({}, value)
            self.assertNotIn("fixture_secret_identifier", str(caught.exception))
        self.opener.open.assert_not_called()

    def test_configured_retries_and_file_credential_failure_classification(self):
        failure = urllib.error.HTTPError("url", 503, "private response", {}, io.BytesIO(b"private body"))
        self.opener.open.side_effect = [failure, self.success]
        settings = self.policy["transport"] | {"retry_statuses": [503]}
        with patch("devtools.jev.time.sleep") as sleep:
            Jev(settings, self.connection).evaluate({}, self.questions)
            sleep.assert_called_once()
        self.path.chmod(0o644)
        with self.assertRaises(ValueError) as caught:
            Jev(connection=self.connection).evaluate({}, self.questions)
        self.assertNotIsInstance(caught.exception, JevUnavailable)
        self.path.unlink()
        with self.assertRaises(JevUnavailable):
            Jev(connection=self.connection).evaluate({}, self.questions)

    def test_connection_inspection_never_reads_credential(self):
        self.path.unlink()
        Jev(connection=self.connection)
        self.opener.open.assert_not_called()

    def test_manifest_registers_an_installed_adapter_without_dispatch_changes(self):
        manifest = self.root / "adapters.json"
        document = json.loads(adapters.MANIFEST.read_text())
        document["adapters"]["evaluation"]["fixture"] = __name__ + ":FixtureAdapter"
        manifest.write_text(json.dumps(document))
        (self.root / "team.json").write_text(json.dumps({"project": {"configuration_file": "operator.json"}}))
        (self.root / "operator.json").write_text(json.dumps({"version": 1, "project": {
            "connection": {"adapter": "fixture"}, "model": "fixture-model-v2"}}))
        with patch.object(adapters, "MANIFEST", manifest):
            settings = resolve(SimpleNamespace(directory=self.root))
            result = Jev(settings["transport"], settings["connection"]).evaluate({}, self.questions, settings["model"])
        self.assertEqual("fixture-model-v2", result["model"])
        self.assertEqual(0.75, result["answers"]["proof"]["noul"])
        self.opener.open.assert_not_called()

    def test_bad_or_uninstalled_manifest_entries_never_fall_back(self):
        manifest = self.root / "adapters.json"
        with patch.object(adapters, "MANIFEST", manifest):
            with self.assertRaisesRegex(ValueError, "readable JSON"):
                adapters.names("evaluation")
            for value in ('{"version":1,"version":1,"adapters":{}}',
                          '{"version":1,"adapters":{"evaluation":{"fixture":"types:SimpleNamespace",'
                          '"fixture":"types:SimpleNamespace"}}}',
                          '{"version":NaN,"adapters":{}}'):
                manifest.write_text(value)
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "readable JSON"):
                    adapters.names("evaluation")
            manifest.write_text(json.dumps({"version": 1, "adapters": {"evaluation": {"wrong": "not an import"}}}))
            with self.assertRaisesRegex(ValueError, "module:attribute"):
                adapters.names("evaluation")
            manifest.write_text(json.dumps({"version": 1, "adapters": {"evaluation": {
                "missing": "missing_agentscient_adapter:Factory", "not_callable": "devtools.adapters:NAME"}}}))
            with self.assertRaisesRegex(ValueError, "not installed"):
                adapters.factory("evaluation", "missing")
            with self.assertRaisesRegex(ValueError, "callable"):
                adapters.factory("evaluation", "not_callable")
            with self.assertRaisesRegex(ValueError, "not registered"):
                adapters.factory("evaluation", "typesafe")
        self.opener.open.assert_not_called()
