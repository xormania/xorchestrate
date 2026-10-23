"""Exercise retained release integrity, real isolated installation and publishing."""

import copy
import hashlib
import importlib.util
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "toolchain", Path(__file__).resolve().parents[1] / "actions/php-runtime/toolchain.py",
)
toolchain = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(toolchain)


class ToolchainTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = copy.deepcopy(toolchain.manifest())
        self.config["php"]["version"] = "8.5.11"
        self.config["composer"]["version"] = "2.10.3"
        php = self.root / "php"
        php.write_text(
            '#!/bin/sh\ncase "$1" in\n'
            '  --version) echo "PHP fixture";;\n'
            '  -r) printf "8.5.11";;\n'
            '  *) cat "$1";;\nesac\n',
        )
        php.chmod(0o755)
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as output:
            output.add(php, arcname="php")
        self.data = {"php": archive.getvalue(), "composer": b"Composer version 2.10.3 fixture\n"}
        for key in self.data:
            self.config[key]["sha256"] = hashlib.sha256(self.data[key]).hexdigest()
        self.paths = toolchain.cache_paths(self.config, self.root / "tools")
        self.release = {
            "id": 123, "draft": False, "html_url": "https://github.com/fixture/release",
            "assets": [{"id": index, "name": self.config[key]["asset"], "state": "uploaded"}
                       for index, key in enumerate(self.data, 1)],
        }
        self.env = {
            "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "RUNNER_TEMP": str(self.root),
            "RUNNER_TOOL_CACHE": str(self.root / "tools"), "GITHUB_PATH": str(self.root / "github-path"),
            "GITHUB_REPOSITORY": self.config["repository"], "GITHUB_SHA": "a" * 40,
        }
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, self.env).start()

    def response(self, url, **kwargs):
        key = "php" if url.endswith("/1") or url == self.config["php"]["source"] else "composer"
        return io.BytesIO(self.data[key])

    def test_cold_warm_and_corrupt_cache_install_with_the_same_isolated_php(self):
        with patch.object(toolchain, "get_release", return_value=self.release) as metadata, \
                patch.object(toolchain, "request", side_effect=self.response) as request:
            toolchain.install(self.config, self.paths)
            self.assertEqual(request.call_count, 2)
            toolchain.install(self.config, self.paths)
            self.assertEqual(metadata.call_count, 1)  # Warm install needs no network.
            self.assertEqual(request.call_count, 2)
            for calls, component in enumerate(("php", "composer"), 3):
                with self.subTest(corrupt=component):
                    self.paths[component].write_bytes(b"corrupt")
                    toolchain.install(self.config, self.paths)
                    self.assertEqual(request.call_count, calls)
                    self.assertTrue(toolchain.verified(self.paths[component], self.config[component]["sha256"]))
        runtimes = Path(self.env["GITHUB_PATH"]).read_text().splitlines()
        self.assertEqual(len(set(runtimes)), 4)
        for runtime in runtimes:
            # No PHP in PATH: the Composer wrapper must select its sibling binary.
            env = {**os.environ, "PATH": "/usr/bin:/bin"}
            result = subprocess.check_output([runtime + "/composer", "--version"], env=env)
            self.assertEqual(result, self.data["composer"])

    def test_bad_or_interrupted_transfer_is_not_published(self):
        path = self.paths["php"]
        for failure in (None, OSError("interrupted transfer")):
            with self.subTest(failure=failure), patch.object(
                toolchain, "request", return_value=io.BytesIO(b"changed upstream bytes"), side_effect=failure,
            ):
                with self.assertRaises((ValueError, OSError)):
                    toolchain.download("https://fixture.invalid/archive", path, self.config["php"]["sha256"])
                self.assertFalse(path.exists())
                self.assertEqual(list(path.parent.glob(".download.*")), [])

    def test_draft_missing_assets_and_wrong_platform_fail_before_execution(self):
        for release in ({**self.release, "draft": True}, {**self.release, "assets": []}):
            with self.subTest(release=release), patch.object(toolchain, "get_release", return_value=release):
                with self.assertRaises((ValueError, KeyError)):
                    toolchain.install(self.config, self.paths)
                self.assertFalse(Path(self.env["GITHUB_PATH"]).exists())
        with patch.dict(os.environ, {"RUNNER_ARCH": "ARM64"}), patch.object(toolchain, "get_release") as lookup:
            with self.assertRaisesRegex(ValueError, "Linux/X64"):
                toolchain.install(self.config, self.paths)
            lookup.assert_not_called()

    def test_version_mismatch_fails(self):
        self.config["php"]["version"] = "0.0.0"
        with patch.object(toolchain, "get_release", return_value=self.release), \
                patch.object(toolchain, "request", side_effect=self.response):
            with self.assertRaisesRegex(ValueError, "PHP version"):
                toolchain.install(self.config, self.paths)

    def test_published_release_is_verified_without_mutation(self):
        with patch.object(toolchain, "get_release", return_value=self.release), \
                patch.object(toolchain, "request", side_effect=self.response) as request, \
                patch.object(toolchain, "api") as api:
            toolchain.publish(self.config, self.paths)
            self.assertEqual(request.call_count, 2)
            api.assert_not_called()

    def test_existing_draft_resumes_missing_assets_then_verifies_before_publish(self):
        draft = {**self.release, "draft": True}
        incomplete = {**draft, "assets": draft["assets"][:1]}
        events = []

        def api(url, **kwargs):
            method = kwargs.get("method", "GET")
            events.append(method)
            if method == "PATCH":
                self.assertEqual(events[-3:], ["verify", "verify", "PATCH"])
                return self.release
            return draft

        def response(url, **kwargs):
            if "/assets/" in url:
                events.append("verify")
            return self.response(url, **kwargs)

        with patch.object(toolchain, "get_release", return_value=incomplete), \
                patch.object(toolchain, "api", side_effect=api), \
                patch.object(toolchain, "request", side_effect=response):
            toolchain.publish(self.config, self.paths)
        self.assertEqual(events, ["POST", "GET", "verify", "verify", "PATCH"])

    def test_draft_lookup_pages_when_the_published_tag_endpoint_returns_404(self):
        unrelated = {"tag_name": "other", "draft": False}
        draft = {**self.release, "draft": True, "tag_name": self.config["tag"]}
        with patch.object(toolchain, "api", side_effect=[[unrelated] * 100, [draft]]) as api:
            self.assertEqual(toolchain.get_draft(self.config), draft)
            self.assertTrue(api.call_args.args[0].endswith("page=2"))
        missing = urllib.error.HTTPError("https://api.github.com/fixture", 404, "missing", {}, None)
        with patch.object(toolchain, "get_release", side_effect=missing), \
                patch.object(toolchain, "get_draft", return_value=draft), \
                patch.object(toolchain, "api", side_effect=[draft, self.release]) as api, \
                patch.object(toolchain, "request", side_effect=self.response):
            toolchain.publish(self.config, self.paths)
            self.assertEqual([c.kwargs.get("method", "GET") for c in api.call_args_list], ["GET", "PATCH"])

    def test_bad_release_readback_never_publishes(self):
        with patch.object(toolchain, "get_release", return_value={**self.release, "draft": True}), \
                patch.object(toolchain, "api", return_value={**self.release, "draft": True}) as api, \
                patch.object(toolchain, "request", return_value=io.BytesIO(b"bad")):
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                toolchain.publish(self.config, self.paths)
            self.assertTrue(all(call.kwargs.get("method", "GET") == "GET" for call in api.call_args_list))

    def test_bad_source_never_creates_a_release(self):
        missing = urllib.error.HTTPError("https://api.github.com/fixture", 404, "missing", {}, None)
        with patch.object(toolchain, "get_release", side_effect=missing), \
                patch.object(toolchain, "get_draft", return_value=None), \
                patch.object(toolchain, "request", return_value=io.BytesIO(b"new bytes")), \
                patch.object(toolchain, "api") as api:
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                toolchain.publish(self.config, self.paths)
            api.assert_not_called()

    def test_authorization_is_api_only_and_removed_on_redirect(self):
        with patch.dict(os.environ, {"GH_TOKEN": "fixture-token"}), \
                patch.object(urllib.request, "urlopen") as opened:
            toolchain.request("https://api.github.com/repos/example/releases/assets/1", binary=True)
            original = opened.call_args.args[0]
            self.assertEqual(original.get_header("Authorization"), "Bearer fixture-token")
            redirected = urllib.request.HTTPRedirectHandler().redirect_request(
                original, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/file",
            )
            self.assertIsNone(redirected.get_header("Authorization"))
            toolchain.request("https://getcomposer.org/download/2.10.3/composer.phar", binary=True)
            self.assertIsNone(opened.call_args.args[0].get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
