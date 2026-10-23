"""Credential tests never use real accounts, credential stores or API requests."""

import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "credentials.py"
SPEC = importlib.util.spec_from_file_location("manual_credentials_script", SCRIPT)
credentials = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(credentials)
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
SENTINEL = "credential-secret-never-print"


def jwt(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp, "email": SENTINEL}).encode()).decode().rstrip("=")
    return "header." + payload + ".signature"


class CredentialScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="credential-check-tests-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(self.root), "PATH": os.environ["PATH"]}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        binaries = patch.object(credentials.shutil, "which", return_value="/fake/client")
        binaries.start()
        self.addCleanup(binaries.stop)

    def document(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def check(self, service, online=False, configuration=None):
        row = credentials.check(service, online=online, repos=["uscient/agentscient"], now=NOW, days=7,
                                configuration=configuration)
        self.assertNotIn(SENTINEL, json.dumps(row))
        return row

    def test_offline_inventory_never_launches_clients_or_network(self):
        self.document(".codex/auth.json", {"tokens": {"access_token": jwt((NOW + timedelta(days=2)).timestamp())}})
        with patch.object(credentials, "native", side_effect=AssertionError("native ran")), \
             patch.object(credentials, "request_json", side_effect=AssertionError("network ran")):
            row = self.check("codex")
        self.assertEqual((row["stored"], row["status"], row["expiry"]), ("present", "incomplete", "soon"))
        self.assertEqual(row["expires_at"], "2026-10-04T00:00:00Z")

    def test_expired_access_does_not_claim_subscription_expired(self):
        self.document(".claude/.credentials.json", {"claudeAiOauth": {
            "accessToken": SENTINEL, "refreshToken": SENTINEL, "expiresAt": (NOW.timestamp() - 60) * 1000,
        }})
        with patch.object(credentials, "native", return_value=SENTINEL):
            row = self.check("claude", online=True)
        self.assertEqual(row["expiry"], "expired")
        self.assertEqual(row["status"], "incomplete")
        self.assertIn("refresh may still work", row["expiry_note"])

    def test_bad_credential_files_are_redacted_and_not_followed(self):
        path = self.document(".grok/auth.json", {"access_token": SENTINEL})
        for broken in ("permissions", "malformed", "symlink"):
            with self.subTest(broken=broken):
                if broken == "permissions":
                    path.chmod(0o644)
                elif broken == "malformed":
                    path.chmod(0o600)
                    path.write_text(SENTINEL)
                else:
                    path.unlink()
                    path.symlink_to(self.document("outside.json", {"access_token": SENTINEL}))
                self.assertEqual(self.check("grok")["status"], "failed")

    def test_opaque_and_invalid_jwt_expiry_stays_unknown(self):
        for value in (SENTINEL, "x.!.y", jwt("not-a-date"), jwt(True), jwt(1e100)):
            with self.subTest(value=value[:8]):
                self.assertIsNone(credentials.jwt_expiry(value))
        self.assertEqual(credentials.expiry_report(None, NOW, 7)["expires_at"], None)

    def test_codex_subscription_does_not_probe_unrelated_env_api_key(self):
        os.environ["OPENAI_API_KEY"] = SENTINEL
        self.document(".codex/auth.json", {"auth_mode": "chatgpt", "tokens": {
            "access_token": jwt((NOW + timedelta(days=9)).timestamp()),
        }})
        with patch.object(credentials, "native", return_value=SENTINEL), \
             patch.object(credentials, "request_json") as api:
            row = self.check("codex", online=True)
        api.assert_not_called()
        self.assertEqual(row["status"], "incomplete")
        self.assertIn("no supported", row["live"])

    def test_codex_provider_override_and_inactive_token_do_not_mislead(self):
        self.document(".codex/auth.json", {"auth_mode": "apikey", "OPENAI_API_KEY": SENTINEL,
                                             "tokens": {"access_token": jwt(NOW.timestamp() - 60)}})
        with patch.object(credentials, "native", return_value=""), \
             patch.object(credentials, "request_json", return_value={"data": []}) as api:
            row = self.check("codex", online=True)
            self.assertEqual(row["status"], "verified")
            self.assertIsNone(row["expires_at"])
            api.reset_mock()
            (self.root / ".codex/config.toml").write_text('[model_providers.openai]\nbase_url="https://proxy.invalid/v1"\n')
            self.assertEqual(self.check("codex", online=True)["status"], "incomplete")
            api.assert_not_called()

    def test_malformed_settings_and_api_shapes_are_reported_as_failures(self):
        root = self.root / ".codex"
        root.mkdir()
        (root / "config.toml").write_text("cli_auth_credentials_store=[]\n")
        self.assertEqual(self.check("codex")["status"], "failed")
        with patch.object(credentials, "native", side_effect=["", 'HTTP/2 200\n\n{"id":42}',
                                                              '{"full_name":{"unexpected":"object"}}']):
            self.assertEqual(self.check("github", online=True)["status"], "failed")

    def test_api_key_checks_distinguish_authentication_from_native_selection(self):
        os.environ["ANTHROPIC_API_KEY"] = SENTINEL
        with patch.object(credentials, "native", return_value=""), \
             patch.object(credentials, "request_json", return_value={"data": []}) as api:
            row = self.check("claude", online=True)
        self.assertEqual(api.call_args.args[0], "https://api.anthropic.com/v1/models?limit=1")
        self.assertEqual(api.call_args.args[1]["x-api-key"], SENTINEL)
        self.assertEqual(row["status"], "incomplete")
        self.assertIn("selection unverified", row["live"])

    def test_grok_key_block_and_agy_provider_selection(self):
        os.environ.update(XAI_API_KEY=SENTINEL, GEMINI_API_KEY=SENTINEL)
        with patch.object(credentials, "request_json", return_value={
                "api_key_disabled": True, "api_key_blocked": False, "team_blocked": False}):
            self.assertEqual(self.check("grok", online=True)["status"], "failed")
        with patch.object(credentials, "request_json", return_value={"models": []}) as api:
            self.assertEqual(self.check("agy", online=True)["status"], "incomplete")
            api.assert_not_called()
            self.document(".gemini/antigravity-cli/settings.json", {"modelProvider": "gemini"})
            self.assertEqual(self.check("agy", online=True)["status"], "verified")

    def test_github_header_expiry_repo_reads_and_no_identity_leak(self):
        output = "HTTP/2.0 200 OK\r\nGitHub-Authentication-Token-Expiration: 2026-10-05 00:00:00 UTC\r\n\r\n"
        output += json.dumps({"id": 42, "login": SENTINEL})
        with patch.object(credentials, "native", side_effect=[SENTINEL, output, json.dumps({
                "full_name": "uscient/agentscient", "permissions": {"push": True}, "owner": SENTINEL})]):
            row = self.check("github", online=True)
        self.assertEqual(row["status"], "verified")
        self.assertEqual(row["expiry"], "soon")
        self.assertTrue(row["repositories"][0]["push"])
        with patch.object(credentials, "native", side_effect=["", "HTTP/2 200\n\n{\"id\":42}", json.dumps({
                "full_name": "uscient/agentscient"})]):
            row = self.check("github", online=True)
        self.assertIsNone(row["expires_at"])
        self.assertIsNone(row["repositories"][0]["push"])

    def test_native_and_http_failure_details_are_never_echoed(self):
        with patch.object(credentials.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, SENTINEL)):
            row = self.check("github", online=True)
        self.assertEqual(row["status"], "failed")
        opener = unittest.mock.Mock()
        opener.open.side_effect = HTTPError("https://api.x.ai/" + SENTINEL, 401, SENTINEL, {}, None)
        with patch.object(credentials, "build_opener", return_value=opener):
            with self.assertRaisesRegex(credentials.CheckError, "HTTP 401") as caught:
                credentials.request_json("https://api.x.ai/v1/api-key", {})
        self.assertNotIn(SENTINEL, str(caught.exception))
        self.assertIsNone(credentials.NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com"))

    def test_json_report_exit_code_and_source_guard(self):
        os.environ["GEMINI_API_KEY"] = SENTINEL
        self.document(".gemini/antigravity-cli/settings.json", {"modelProvider": "gemini"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = credentials.main(["--service", "agy", "--json"])
        self.assertEqual(code, 2)
        self.assertNotIn(SENTINEL, output.getvalue())
        self.assertFalse(json.loads(output.getvalue())["online"])
        source = subprocess.run(["bash", "-c", 'set -eu; source "$1"; printf "alive:%s" "$-"',
                                 "bash", str(SCRIPT.with_suffix(".sh"))], text=True, capture_output=True)
        self.assertEqual(source.returncode, 0)
        self.assertIn("alive:", source.stdout)
        self.assertIn("e", source.stdout.rsplit("alive:", 1)[1])

    def test_jev_offline_uses_default_overlay_and_does_not_need_native_tools(self):
        secret = self.root / ".config/agentscient/secrets/typesafe.key"
        secret.parent.mkdir(parents=True)
        secret.write_text(SENTINEL + "\n")
        secret.chmod(0o600)
        self.document(".config/agentscient/configuration.json", {
            "version": 1, "project": {"connection": {"credential": {"file": "secrets/typesafe.key"}}},
        })
        from devtools.jev import Jev
        with patch.object(credentials.shutil, "which", side_effect=AssertionError("native lookup")), \
             patch.object(credentials, "native", side_effect=AssertionError("native ran")), \
             patch.object(Jev, "evaluate", side_effect=AssertionError("network ran")):
            row = self.check("jev")
        self.assertEqual((row["stored"], row["status"], row["expires_at"]), ("present", "incomplete", None))
        self.assertEqual(row["source"], "private credential file")
        self.assertEqual(row["configurations_checked"], 1)

    def test_jev_default_environment_and_wrapper_work_from_unrelated_cwd(self):
        os.environ["TYPESAFE_API_KEY"] = SENTINEL
        for mode in ("direct", "wrapper"):
            with self.subTest(mode=mode):
                if mode == "direct":
                    row = self.check("jev")
                else:
                    # Isolated Python must import this checkout, never this cwd or PYTHONPATH.
                    (self.root / "devtools").mkdir()
                    (self.root / "devtools/__init__.py").write_text("raise RuntimeError('untrusted cwd import')\n")
                    result = subprocess.run(["bash", str(SCRIPT.with_suffix(".sh")), "--service", "jev", "--json"],
                                            cwd=self.root, env={**os.environ, "PYTHONPATH": str(self.root)},
                                            text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertNotIn(SENTINEL, result.stdout + result.stderr)
                    row = json.loads(result.stdout)["credentials"][0]
                self.assertEqual((row["source"], row["stored"]), ("environment variable", "present"))

    def test_jev_probes_each_effective_project_role_variant_once(self):
        os.environ.update(PROJECT_JEV=SENTINEL, ROLE_JEV=SENTINEL)
        overlay = self.document("private/overlay.json", {"version": 1,
            "project": {"connection": {"endpoint": "https://project.invalid/evaluate", "credential": {"env": "PROJECT_JEV"}}},
            "roles": {
                "coder": {"connection": {"endpoint": "https://role.invalid/evaluate", "credential": {"env": "ROLE_JEV"}}},
                "audit": {"model": "jev-1.14.0"},
                "research": {"transport": {"timeout": 3}},
                "docs": {},
            }})
        from devtools.jev import Jev
        called = []
        def evaluate(instance, state, questions, model=None):
            called.append((instance.connection, instance.settings, model, state, questions))
            return {"provider_echo": SENTINEL}
        with patch.object(Jev, "evaluate", evaluate), \
             patch.object(credentials.shutil, "which", side_effect=AssertionError("native lookup")):
            row = self.check("jev", online=True, configuration=overlay)
        self.assertEqual((row["status"], row["configurations_checked"]), ("verified", 4))
        self.assertEqual(len(called), 4)
        self.assertEqual({call[0]["credential"]["env"] for call in called}, {"PROJECT_JEV", "ROLE_JEV"})
        self.assertIn("https://role.invalid/evaluate", {call[0]["endpoint"] for call in called})
        self.assertIn("jev-1.14.0", {call[2] for call in called})
        self.assertIn(3, {call[1]["timeout"] for call in called})
        self.assertTrue(all(call[3] == {"value": 2} for call in called))

    def test_jev_real_transport_validates_response_without_printing_secrets(self):
        os.environ["TYPESAFE_API_KEY"] = SENTINEL
        from devtools import jev
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        valid = {"model": jev.MODEL, "answers": {"check": {"type": "noul", "noul": 1}},
                 "usage": {"input_tokens": 1, "output_tokens": 1}, "echo": SENTINEL}
        for body, expected in ((valid, "verified"), ({"echo": SENTINEL}, "failed")):
            with self.subTest(expected=expected):
                response.read.return_value = json.dumps(body).encode()
                with patch.object(jev.urllib.request, "build_opener", return_value=opener):
                    row = self.check("jev", online=True)
                self.assertEqual(row["status"], expected)
                request = opener.open.call_args.args[0]
                self.assertEqual(request.get_header("Authorization"), "Bearer " + SENTINEL)
                self.assertNotIn(SENTINEL.encode(), request.data)
        opener.open.side_effect = HTTPError("https://typesafe.invalid/" + SENTINEL, 401, SENTINEL, {}, None)
        with patch.object(jev.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(self.check("jev", online=True)["status"], "failed")

    def test_jev_failure_continues_other_variants_and_redacts_provider_exception(self):
        os.environ["TYPESAFE_API_KEY"] = SENTINEL
        overlay = self.document("overlay.json", {"version": 1, "roles": {"coder": {"model": "jev-1.14.0"}}})
        from devtools.jev import Jev
        output = io.StringIO()
        with patch.object(Jev, "evaluate", side_effect=[RuntimeError(SENTINEL), {"echo": SENTINEL}]) as evaluate, \
             contextlib.redirect_stdout(output):
            code = credentials.main(["--service", "jev", "--online", "--configuration", str(overlay), "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(evaluate.call_count, 2)
        self.assertNotIn(SENTINEL, output.getvalue())
        row = json.loads(output.getvalue())["credentials"][0]
        self.assertEqual([result["status"] for result in row["checks"]], ["failed", "verified"])

    def test_jev_missing_bad_configuration_or_credentials_cannot_pass(self):
        from devtools.jev import Jev
        for configuration in (self.root / "missing.json", self.document("malformed.json", {"version": 1, SENTINEL: 1}), None):
            with self.subTest(configuration=configuration), patch.object(Jev, "evaluate") as evaluate:
                self.assertEqual(self.check("jev", online=True, configuration=configuration)["status"], "failed")
                evaluate.assert_not_called()
        secret = self.root / "key"
        secret.write_text(SENTINEL)
        secret.chmod(0o644)
        overlay = self.document("file-overlay.json", {"version": 1, "project": {"connection": {"credential": {"file": str(secret)}}}})
        with patch.object(Jev, "evaluate") as evaluate:
            self.assertEqual(self.check("jev", online=True, configuration=overlay)["status"], "failed")
            evaluate.assert_not_called()

    def test_jev_is_in_default_service_selection(self):
        output = io.StringIO()
        with patch.object(credentials, "check", return_value={"status": "verified"}) as check, \
             contextlib.redirect_stdout(output):
            self.assertEqual(credentials.main(["--json"]), 0)
        self.assertIn("jev", [call.args[0] for call in check.call_args_list])


if __name__ == "__main__":
    unittest.main()
