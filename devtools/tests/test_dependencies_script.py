import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools.scripts import dependencies


class DependenciesScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dependencies-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "path with spaces" / "toolchains"
        self.config = dependencies.manifest()

    def complete(self, stdout="", code=0):
        return subprocess.CompletedProcess([], code, stdout, "")

    def installer(self, argv, *, env, **kwargs):
        self.assertEqual("install", argv[-1])
        self.assertTrue(argv[-2].endswith(".github/actions/php-runtime/toolchain.py"))
        self.assertEqual("Linux", env["RUNNER_OS"])
        self.assertEqual("X64", env["RUNNER_ARCH"])
        target = Path(env["RUNNER_TEMP"]) / "prod-php.test"
        target.mkdir()
        for name in ("php", "composer", "composer.phar"):
            (target / name).write_text("owned fixture " + name)
            (target / name).chmod(0o700)
        Path(env["GITHUB_PATH"]).write_text(str(target) + "\n")
        return ""

    def install_fixture(self):
        with patch.dict(os.environ, {"GH_TOKEN": "sentinel-secret"}, clear=True), \
             patch.object(dependencies, "required", side_effect=self.installer):
            return dependencies.toolchain_install(self.state, self.config)

    def test_preview_never_executes_tools_or_creates_state(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch.object(dependencies, "command") as run:
            self.assertEqual(0, dependencies.main(["preview", "--toolchains", str(self.state)]))
        run.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertIn("--php", output.getvalue())
        self.assertIn("owned", output.getvalue().lower())

    def test_root_install_refuses_before_any_system_mutation(self):
        with patch.object(dependencies.os, "geteuid", return_value=0), \
             patch.object(dependencies.platform, "system", return_value="Linux"), \
             patch.object(dependencies.platform, "machine", return_value="x86_64"), \
             patch.object(dependencies, "apt_install") as install:
            with self.assertRaisesRegex(ValueError, "normal user"):
                dependencies.main(["install", "--toolchains", str(self.state)])
        install.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_owned_install_captures_token_and_publishes_persistent_runtime(self):
        output = io.StringIO()
        captured = {}

        def install(argv, **kwargs):
            captured.update(kwargs)
            return self.installer(argv, **kwargs)

        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output), \
             patch.object(dependencies, "command", return_value=self.complete("sentinel-secret\n")) as auth, \
             patch.object(dependencies, "required", side_effect=install):
            destination = dependencies.toolchain_install(self.state, self.config)
        auth.assert_called_once_with(["gh", "auth", "token", "--hostname", "github.com"])
        self.assertEqual("sentinel-secret", captured["env"]["GH_TOKEN"])
        self.assertNotIn("sentinel-secret", output.getvalue())
        self.assertTrue(dependencies.toolchain_check(destination, self.config))
        self.assertEqual(0o700, self.state.stat().st_mode & 0o777)
        self.assertFalse(list(self.state.glob(".install-*")))
        self.assertEqual(self.config["tag"], destination.name)

    def test_failed_installer_does_not_publish_partial_toolchain_or_leak_token(self):
        with patch.dict(os.environ, {"GH_TOKEN": "sentinel-secret"}, clear=True), \
             patch.object(dependencies, "command", return_value=self.complete("", 1)):
            with self.assertRaisesRegex(ValueError, "failed.*exit 1") as caught:
                dependencies.toolchain_install(self.state, self.config)
        self.assertNotIn("sentinel-secret", str(caught.exception))
        self.assertFalse((self.state / self.config["tag"]).exists())
        self.assertFalse(list(self.state.glob(".install-*")))

    def test_existing_corrupt_runtime_is_preserved_without_reinstall(self):
        destination = self.install_fixture()
        (destination / "php").write_text("corrupted bytes")
        with patch.object(dependencies, "required") as run:
            with self.assertRaisesRegex(ValueError, "Preserve it"):
                dependencies.toolchain_install(self.state, self.config)
        run.assert_not_called()
        self.assertEqual("corrupted bytes", (destination / "php").read_text())

    def test_published_runtime_is_reused_and_symlink_alias_is_rejected(self):
        destination = self.install_fixture()
        before = (destination / "receipt.json").read_bytes()
        with patch.object(dependencies, "required") as run:
            self.assertEqual(destination, dependencies.toolchain_install(self.state, self.config))
        run.assert_not_called()
        self.assertEqual(before, (destination / "receipt.json").read_bytes())
        alias = self.root / "alias"
        alias.symlink_to(destination, target_is_directory=True)
        self.assertFalse(dependencies.toolchain_check(alias, self.config))

    def test_cached_candidate_newer_and_missing_packages_fail_check(self):
        def probe(argv):
            if argv[0] == "dpkg-query":
                return self.complete("install ok installed\t1.0") if argv[-1] != "tmux" else self.complete("", 1)
            if argv[0] == "dpkg":
                return self.complete()
            return self.complete("  Installed: 1.0\n  Candidate: 2.0\n")

        output = io.StringIO()
        with patch.object(dependencies.shutil, "which", return_value="/fake/binary"), \
             patch.object(dependencies, "command", side_effect=probe), contextlib.redirect_stdout(output):
            self.assertFalse(dependencies.packages_check())
        self.assertIn("local APT metadata", output.getvalue())
        self.assertIn("FAIL tmux: installed=missing", output.getvalue())
        self.assertIn("installed=1.0, candidate=2.0", output.getvalue())

    def test_unknown_candidate_is_not_pass_and_newer_installed_is_not_outdated(self):
        def probe(argv):
            if argv[0] == "dpkg-query":
                return self.complete("install ok installed\t3.0")
            if argv[0] == "dpkg":
                return self.complete("", 1)
            return self.complete("" if argv[-1] == "tmux" else "  Candidate: 2.0\n")

        output = io.StringIO()
        with patch.object(dependencies.shutil, "which", return_value="/fake/binary"), \
             patch.object(dependencies, "command", side_effect=probe), contextlib.redirect_stdout(output):
            self.assertFalse(dependencies.packages_check())
        self.assertIn("NOT VERIFIED tmux", output.getvalue())
        self.assertIn("PASS git: installed=3.0, candidate=2.0", output.getvalue())

    def test_apt_updates_only_named_prerequisites_and_uses_terminal_sudo(self):
        with patch.object(dependencies.platform, "freedesktop_os_release", return_value={"ID": "ubuntu"}), \
             patch.object(dependencies.os, "geteuid", return_value=1000), \
             patch.object(dependencies.shutil, "which", return_value="/fake/binary"), \
             patch.object(dependencies, "required") as run:
            dependencies.apt_install()
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(["sudo", "apt-get", "update"], commands[0])
        self.assertEqual(["sudo", "apt-get", "install", "--no-install-recommends", "-y", *dependencies.PACKAGES], commands[1])
        self.assertTrue(all(call.kwargs["interactive"] for call in run.call_args_list))
        self.assertNotIn("php", commands[1])
        self.assertNotIn("docker.io", commands[1])

    def test_check_does_not_build_or_replace_docker_and_reports_wsl(self):
        output = io.StringIO()
        with patch.object(dependencies.shutil, "which", return_value="/fake/docker"), \
             patch.object(dependencies.platform, "release", return_value="6.6-microsoft-standard-WSL2"), \
             patch.object(dependencies, "command", return_value=self.complete("", 1)) as run, \
             contextlib.redirect_stdout(output):
            self.assertFalse(dependencies.docker_check(build=False))
        self.assertEqual(1, run.call_count)
        self.assertIn("WSL Integration", output.getvalue())

    def test_composer_install_detects_lock_mutation_and_does_not_run_update(self):
        project = self.root / "project with spaces"
        project.mkdir()
        (project / "composer.json").write_text("{}")
        (project / "composer.lock").write_text("{}")

        def composer(argv, **kwargs):
            self.assertEqual("install", argv[1])
            self.assertEqual(project, kwargs["cwd"])
            self.assertTrue(kwargs["env"]["PATH"].startswith(str(self.state) + os.pathsep))
            (project / "composer.lock").write_text('{"changed":true}')
            return self.complete()

        with patch.object(dependencies, "command", side_effect=composer):
            with self.assertRaisesRegex(ValueError, "lockfile changed"):
                dependencies.project_check(project, self.state, install=True)

    def test_autoload_alone_does_not_pass_missing_stale_or_wrong_reference_packages(self):
        project = self.root / "fixture-project"
        (project / "vendor/composer").mkdir(parents=True)
        (project / "vendor/autoload.php").write_text("<?php")
        package = {"name": "example/service", "version": "dev-main", "source": {"reference": "first"}}
        (project / "composer.lock").write_text(json.dumps({"packages": [package]}))
        inventory = project / "vendor/composer/installed.json"
        for value in ([], [{**package, "version": "v1.0.0"}], [{**package, "source": {"reference": "other"}}]):
            inventory.write_text(json.dumps({"packages": value}))
            self.assertFalse(dependencies.locked_packages_match(project))
        inventory.write_text(json.dumps({"packages": [package]}))
        self.assertTrue(dependencies.locked_packages_match(project))

    def test_project_checks_cannot_be_redirected_or_ignore_the_php_platform(self):
        project = self.root / "project"
        (project / "vendor/composer").mkdir(parents=True)
        (project / "vendor/autoload.php").write_text("<?php")
        (project / "composer.json").write_text("{}")
        (project / "composer.lock").write_text('{"packages": []}')
        (project / "vendor/composer/installed.json").write_text('{"packages": []}')
        inherited = {"COMPOSER": "elsewhere.json", "COMPOSER_VENDOR_DIR": "elsewhere",
                     "COMPOSER_IGNORE_PLATFORM_REQS": "1", "COMPOSER_IGNORE_PLATFORM_REQ": "php", "COMPOSER_NO_DEV": "1"}

        def check(argv, **kwargs):
            for key in inherited:
                self.assertNotIn(key, kwargs["env"])
            self.assertEqual("1", kwargs["env"]["COMPOSER_DISABLE_NETWORK"])
            self.assertIn("--no-plugins", argv)
            self.assertIn("--no-scripts", argv)
            return self.complete("", 1 if argv[1] == "validate" else 0)

        with patch.dict(os.environ, inherited), patch.object(dependencies, "command", side_effect=check):
            self.assertFalse(dependencies.project_check(project, self.state, install=False))

    def test_sourcing_preserves_caller_options_even_with_errexit(self):
        script = dependencies.ROOT / "devtools/scripts/dependencies.sh"
        result = subprocess.run(["bash", "-e", "-c", 'before=$-; source "$1"; [[ $- == "$before" ]]; printf "survived\\n"', "bash", str(script)],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("survived", result.stdout)


if __name__ == "__main__":
    unittest.main()
