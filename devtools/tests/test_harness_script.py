"""Operator client maintenance never installs software during these tests."""

import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from devtools.scripts import harnesses


class Response(io.BytesIO):
    url = "https://storage.googleapis.com/antigravity-public/release"


class HarnessScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="harness tests ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = patch.dict(os.environ, {"HOME": str(self.root), "PATH": os.environ["PATH"]}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def call(self, *args):
        output = io.StringIO()
        with redirect_stdout(output), patch.object(harnesses.os, "geteuid", return_value=1000):
            result = harnesses.main(list(args))
        return result, output.getvalue()

    def test_offline_default_never_fetches_or_installs(self):
        with patch.object(harnesses, "executable_paths", return_value=[]), \
                patch.object(harnesses, "fetch") as fetch, \
                patch.object(harnesses, "install_native") as install:
            result, output = self.call()
        self.assertEqual(1, result)
        self.assertIn("freshness is unknown", output)
        self.assertEqual(4, output.count("installed=missing"))
        fetch.assert_not_called()
        install.assert_not_called()

    def test_update_preview_cannot_mutate_even_when_clients_missing(self):
        with patch.object(harnesses, "executable_paths", return_value=[]), \
                patch.object(harnesses, "update") as update, \
                patch.object(harnesses, "fetch") as fetch:
            result, output = self.call("update", "--only", "codex")
        self.assertEqual(0, result)
        self.assertIn("Preview", output)
        update.assert_not_called()
        fetch.assert_not_called()

    def test_root_update_is_refused_before_any_installation_checks(self):
        with patch.object(harnesses.os, "geteuid", return_value=0), \
                patch.object(harnesses, "executable_paths") as inventory, \
                redirect_stdout(io.StringIO()) as output:
            result = harnesses.main(["update", "--apply"])
        self.assertEqual(1, result)
        self.assertIn("without sudo", output.getvalue())
        inventory.assert_not_called()

    def test_online_mismatch_is_not_reported_current(self):
        with patch.object(harnesses, "executable_paths", return_value=[self.root / "codex"]), \
                patch.object(harnesses, "installation", return_value=("native", None)), \
                patch.object(harnesses, "installed_version", return_value="1.0.0"), \
                patch.object(harnesses, "latest_version", return_value="1.0.1"):
            result, output = self.call("check", "--online", "--only", "codex")
        self.assertEqual(1, result)
        self.assertIn("freshness is not verified", output)

    def test_failed_release_lookup_prevents_apply(self):
        with patch.object(harnesses, "executable_paths", return_value=[]), \
                patch.object(harnesses, "latest_version", side_effect=harnesses.MaintenanceError("offline")), \
                patch.object(harnesses, "update") as update:
            result, _ = self.call("update", "--apply", "--only", "codex")
        self.assertEqual(1, result)
        update.assert_not_called()

    def test_distinct_installations_block_mutation(self):
        with self.assertRaisesRegex(harnesses.MaintenanceError, "Multiple distinct"):
            harnesses.installation("codex", [self.root / "one", self.root / "two"])

    def test_npm_update_preserves_package_manager(self):
        with patch.object(harnesses, "run") as run, \
                patch.object(harnesses, "install_native") as native:
            harnesses.update("codex", "npm", self.root / "codex", self.root, "1.0.0", "1.0.1")
        run.assert_called_once_with(["npm", "install", "--global", "@openai/codex@latest"], timeout=600)
        native.assert_not_called()

    def test_homebrew_update_preserves_claude_channel(self):
        with patch.object(harnesses.shutil, "which", return_value="brew"), \
                patch.object(harnesses, "run") as run:
            harnesses.update("claude", "homebrew", self.root / "claude", "claude-code@latest", "1.0.0", "1.0.1")
        run.assert_called_once_with(["brew", "upgrade", "--cask", "claude-code@latest"], timeout=600)

    def test_read_only_package_or_custom_install_never_overwritten(self):
        with patch.object(harnesses.os, "access", return_value=False), \
                patch.object(harnesses, "run") as run:
            with self.assertRaisesRegex(harnesses.MaintenanceError, "not user-writable"):
                harnesses.update("claude", "npm", self.root / "claude", self.root, "1.0.0", "1.0.1")
        run.assert_not_called()
        with self.assertRaisesRegex(harnesses.MaintenanceError, "unknown"):
            harnesses.update("codex", "unknown", self.root / "codex", None, "1.0.0", "1.0.1")

    def test_checks_hide_untrusted_error_and_credential_text(self):
        with patch.object(harnesses, "executable_paths", side_effect=ValueError("SECRET-sentinel")):
            result, output = self.call("--only", "grok")
        self.assertEqual(1, result)
        self.assertNotIn("SECRET-sentinel", output)

    def test_public_versions_use_publisher_metadata_for_all_clients(self):
        responses = [b'{"version":"1.2.3"}', b'{"version":"2.3.4"}', b"0.3.0", b'{"version":"1.2.14"}']
        with patch.object(harnesses, "fetch", side_effect=responses) as fetch:
            versions = [harnesses.latest_version(name) for name in ("codex", "claude", "grok", "agy")]
        self.assertEqual(["1.2.3", "2.3.4", "0.3.0", "1.2.14"], versions)
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertIn("@openai%2fcodex", urls[0])
        self.assertIn("@anthropic-ai%2fclaude-code", urls[1])
        self.assertTrue(urls[2].startswith("https://x.ai/cli/"))
        self.assertTrue(urls[3].startswith(harnesses.AGY_RELEASES))

    def agy_payload(self, checksum=None):
        executable = self.root / "agy"
        executable.write_bytes(b"existing binary")
        payload = b"verified replacement binary"
        manifest = {"version": "1.2.14", "url": Response.url,
                    "sha512": checksum or hashlib.sha512(payload).hexdigest()}
        return executable, payload, manifest

    def test_agy_checksum_failure_keeps_existing_binary(self):
        executable, payload, manifest = self.agy_payload("0" * 128)
        with patch.object(harnesses, "agy_manifest", return_value=manifest), \
                patch.object(harnesses, "urlopen", return_value=Response(payload)), \
                patch.object(harnesses, "installed_version") as version:
            with self.assertRaisesRegex(harnesses.MaintenanceError, "checksum mismatch"):
                harnesses.update_agy(executable)
        self.assertEqual(b"existing binary", executable.read_bytes())
        version.assert_not_called()
        self.assertEqual([executable], list(self.root.iterdir()))

    def test_agy_verifies_payload_version_before_atomic_replace(self):
        executable, payload, manifest = self.agy_payload()
        with patch.object(harnesses, "agy_manifest", return_value=manifest), \
                patch.object(harnesses, "urlopen", return_value=Response(payload)), \
                patch.object(harnesses, "installed_version", return_value="1.2.14"):
            harnesses.update_agy(executable)
        self.assertEqual(payload, executable.read_bytes())
        self.assertEqual([executable], list(self.root.iterdir()))

    def test_agy_wrong_staged_version_or_symlink_preserves_install(self):
        executable, payload, manifest = self.agy_payload()
        with patch.object(harnesses, "agy_manifest", return_value=manifest), \
                patch.object(harnesses, "urlopen", return_value=Response(payload)), \
                patch.object(harnesses, "installed_version", return_value="0.0.1"):
            with self.assertRaisesRegex(harnesses.MaintenanceError, "Staged Agy version"):
                harnesses.update_agy(executable)
        self.assertEqual(b"existing binary", executable.read_bytes())
        alias = self.root / "alias"
        alias.symlink_to(executable)
        with self.assertRaisesRegex(harnesses.MaintenanceError, "ordinary native binary"):
            harnesses.update_agy(alias)

    def test_agy_archive_rejects_link_and_only_extracts_executable(self):
        executable, _, manifest = self.agy_payload()
        manifest["url"] += ".tar.gz"
        for malicious in (True, False):
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode="w:gz") as archive:
                member = tarfile.TarInfo("antigravity")
                member.size = 3
                if malicious:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../../outside"
                archive.addfile(member, None if malicious else io.BytesIO(b"new"))
                extra = tarfile.TarInfo("../../unwanted")
                extra.size = 4
                archive.addfile(extra, io.BytesIO(b"skip"))
            payload = data.getvalue()
            manifest["sha512"] = hashlib.sha512(payload).hexdigest()
            with patch.object(harnesses, "agy_manifest", return_value=manifest), \
                    patch.object(harnesses, "urlopen", return_value=Response(payload)), \
                    patch.object(harnesses, "installed_version", return_value="1.2.14"):
                if malicious:
                    with self.assertRaisesRegex(harnesses.MaintenanceError, "ordinary executable"):
                        harnesses.update_agy(executable)
                    self.assertEqual(b"existing binary", executable.read_bytes())
                else:
                    harnesses.update_agy(executable)
                    self.assertEqual(b"new", executable.read_bytes())
            self.assertEqual([executable], list(self.root.iterdir()))

    def test_npm_identity_is_verified_before_using_manager(self):
        root = self.root / "node_modules"
        package = root / "@openai/codex"
        package.mkdir(parents=True)
        (package / "package.json").write_text(json.dumps({"name": "wrong-package"}))
        with patch.object(harnesses.shutil, "which", return_value="npm"), \
                patch.object(harnesses, "run", return_value=str(root)):
            self.assertIsNone(harnesses.npm_directory("codex", package / "bin/codex.js"))

    def test_source_guard_preserves_errexit_and_caller(self):
        script = Path(harnesses.__file__).with_suffix(".sh")
        result = subprocess.run(
            ["bash", "-c", 'set -e; source "$1"; printf "alive:%s" "$-"', "test", str(script)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("alive:", result.stdout)
        self.assertIn("e", result.stdout.split("alive:")[1])


if __name__ == "__main__":
    unittest.main()
