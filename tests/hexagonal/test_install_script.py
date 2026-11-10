"""The install script provides uv, installs the launcher with uv tool install, and never uses sudo."""

import shutil
import subprocess

import pytest

from tests.hexagonal.support.install_script import SCRIPT, InstallSandbox

INSTALLER = "https://installer.invalid/uv.sh"


@pytest.fixture
def sandbox(tmp_path):
    return InstallSandbox(tmp_path)


def installs(calls):
    return [call[1:] for call in calls if call[:3] == ("uv", "tool", "install")]


def test_missing_uv_is_installed_with_the_official_installer_then_the_launcher(sandbox):
    sandbox.provide_curl()
    result = sandbox.run(env={"UV_INSTALLER_URL": INSTALLER})
    assert result.returncode == 0, result.stderr
    calls = sandbox.calls()
    assert calls[0] == ("curl", "-LsSf", INSTALLER)
    assert calls[1] == ("uv-installer",)
    assert installs(calls) == [("tool", "install", "xorchestrate")]
    assert "uv was not found; installing it for this user from " + INSTALLER in result.stdout
    assert "installed uv at " + str(sandbox.home / ".local/bin/uv") in result.stdout
    assert '"install_source": "index"' in result.stdout
    assert "next steps:" in result.stdout and "is not on your PATH" in result.stdout
    assert not [call for call in calls if call[0] == "sudo"]


def test_rerunning_keeps_the_existing_uv_and_launcher(sandbox):
    sandbox.provide_curl()
    first = sandbox.run()
    second = sandbox.run()
    assert (first.returncode, second.returncode) == (0, 0), second.stderr
    calls = sandbox.calls()
    assert [call[0] for call in calls].count("curl") == 1
    assert installs(calls) == [("tool", "install", "xorchestrate")] * 2
    assert "using uv at " + str(sandbox.home / ".local/bin/uv") in second.stdout
    assert "already installed" in second.stdout
    assert not [call for call in calls if call[0] == "sudo"]


@pytest.mark.parametrize(("arguments", "env", "expected"), [
    ((), {}, ("tool", "install", "xorchestrate")),
    (("--version", "1.2.3"), {}, ("tool", "install", "xorchestrate==1.2.3")),
    (("--version=1.2.3-rc.1",), {}, ("tool", "install", "xorchestrate==1.2.3-rc.1")),
    (("--git", "v1.2.3"), {}, ("tool", "install", "git+https://github.com/xormania/xorchestrate@v1.2.3")),
    (("--git", "main"), {"XORCHESTRATE_REPOSITORY": "https://git.invalid/fork"},
     ("tool", "install", "git+https://git.invalid/fork@main")),
    (("--version", "2.0.0"), {"XORCHESTRATE_PACKAGE": "xorchestrate-pro",
                              "XORCHESTRATE_INDEX_URL": "https://index.invalid/simple"},
     ("tool", "install", "--default-index", "https://index.invalid/simple", "xorchestrate-pro==2.0.0")),
])
def test_uv_on_path_installs_the_selected_source(sandbox, arguments, env, expected):
    sandbox.provide_uv()
    sandbox.provide_curl()
    result = sandbox.run(*arguments, env=env)
    assert result.returncode == 0, result.stderr
    assert "using uv at " + str(sandbox.bin / "uv") in result.stdout
    assert "installed: {" in result.stdout
    calls = sandbox.calls()
    assert installs(calls) == [expected]
    assert not [call for call in calls if call[0] in {"curl", "sudo"}]


@pytest.mark.parametrize(("arguments", "message"), [
    (("--version", "1.0.0", "--git", "v1.0.0"), "use either --version or --git, not both"),
    (("--git",), "--git needs a tag, branch or commit"),
    (("--force",), "unknown option: --force"),
])
def test_invalid_options_change_nothing(sandbox, arguments, message):
    sandbox.provide_uv()
    result = sandbox.run(*arguments)
    assert result.returncode != 0 and message in result.stderr
    assert sandbox.calls() == []


def test_without_uv_or_a_downloader_it_stops_with_guidance(sandbox):
    result = sandbox.run()
    assert result.returncode != 0
    assert "neither curl nor wget is available" in result.stderr
    assert sandbox.calls() == []


def test_a_failed_uv_installation_is_reported(sandbox):
    sandbox.provide_curl(installs_uv=False)
    result = sandbox.run()
    assert result.returncode != 0 and "uv was installed but cannot be found" in result.stderr
    assert installs(sandbox.calls()) == []


def test_a_failed_launcher_installation_fails_the_script(sandbox):
    sandbox.provide_uv(fail_install=True)
    result = sandbox.run()
    assert result.returncode != 0 and "next steps" not in result.stdout
    assert installs(sandbox.calls()) == [("tool", "install", "xorchestrate")]


def test_help_describes_the_options_without_installing(sandbox):
    result = sandbox.run("--help")
    assert result.returncode == 0 and "--version X.Y.Z | --git REF" in result.stdout
    assert sandbox.calls() == []


def test_the_script_is_posix_sh_and_shellcheck_clean():
    assert SCRIPT.read_text().startswith("#!/bin/sh\n")
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is unavailable; GitHub-hosted Ubuntu runners provide it")
    result = subprocess.run(["shellcheck", "--shell=sh", str(SCRIPT)], capture_output=True, text=True,
                            timeout=60, check=False)
    assert result.returncode == 0, result.stdout
