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
    assert calls[0][:3] == ("curl", "-LsSf", INSTALLER) and calls[0][3] == "-o"
    assert calls[1] == ("uv-installer",)
    assert list(sandbox.tmp.iterdir()) == []
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


@pytest.mark.parametrize(("short", "named"), [(True, "xo"), (False, "xorchestrate")])
def test_the_next_steps_name_the_short_command_when_the_release_provides_it(sandbox, short, named):
    sandbox.provide_uv(short=short)
    result = sandbox.run()
    assert result.returncode == 0, result.stderr
    assert f"  {named} --version   show the release" in result.stdout
    assert (sandbox.tool_bin / "xo").exists() is short
    assert "warning: another xo" not in result.stdout


def foreign_xo(sandbox, kind):
    """An ``xo`` in uv's executable directory that another program put there."""
    sandbox.tool_bin.mkdir(parents=True)
    xo = sandbox.tool_bin / "xo"
    if kind == "file":
        xo.write_text("#!/bin/sh\necho linter\n")
        xo.chmod(0o700)
    else:
        target = sandbox.root / "node" / "bin" / "xo"
        target.parent.mkdir(parents=True)
        target.write_text("#!/bin/sh\necho linter\n")
        xo.symlink_to(target)
    return xo


@pytest.mark.parametrize(("kind", "detail"), [("file", "it is not a link into"), ("link", "it links to")])
def test_a_foreign_xo_stops_the_script_before_uv_installs_anything(sandbox, kind, detail):
    sandbox.provide_uv()
    xo = foreign_xo(sandbox, kind)
    result = sandbox.run()
    assert result.returncode != 0
    assert f"{xo} already exists and is not part of the xorchestrate install ({detail}" in result.stderr
    assert "Move or remove it and re-run, or install with" in result.stderr and "--force xorchestrate" in result.stderr
    assert installs(sandbox.calls()) == []
    assert xo.read_text().endswith("echo linter\n") if kind == "file" else xo.is_symlink()


def test_an_xo_whose_owner_cannot_be_told_is_left_to_uv_and_not_named(sandbox):
    """Without readlink the script cannot tell a link's owner; uv decides, and the next steps use the long name."""
    (sandbox.bin / "readlink").unlink()
    sandbox.provide_uv(short=False)
    foreign_xo(sandbox, "link")
    result = sandbox.run()
    assert result.returncode == 0, result.stderr
    assert installs(sandbox.calls()) == [("tool", "install", "xorchestrate")]
    assert "  xorchestrate --version   show the release" in result.stdout


def test_an_xo_of_the_same_install_is_kept_on_a_rerun(sandbox):
    sandbox.provide_uv()
    assert sandbox.run().returncode == 0
    result = sandbox.run("--version", "1.2.3")
    assert result.returncode == 0, result.stderr
    assert installs(sandbox.calls())[-1] == ("tool", "install", "xorchestrate==1.2.3")


@pytest.mark.parametrize(("package", "other"), [("xorchestrate", "xork"), ("xork", "xorchestrate")])
def test_the_other_name_of_the_launcher_already_installed_stops_the_script(sandbox, package, other):
    sandbox.provide_uv()
    (sandbox.tool_dir / other / "bin").mkdir(parents=True)
    result = sandbox.run(env={"XORCHESTRATE_PACKAGE": package})
    assert result.returncode != 0
    assert f"{other} is already installed with uv tool install and provides the same commands as {package}" \
        in result.stderr
    assert f"uv tool uninstall {other}" in result.stderr
    assert installs(sandbox.calls()) == []


def test_the_alias_installs_and_runs_the_xorchestrate_command(sandbox):
    sandbox.provide_uv()
    result = sandbox.run("--version", "1.2.3", env={"XORCHESTRATE_PACKAGE": "xork"})
    assert result.returncode == 0, result.stderr
    assert installs(sandbox.calls()) == [("tool", "install", "xork==1.2.3")]
    assert "installed: {" in result.stdout and "  xo --version   show the release" in result.stdout
    assert (sandbox.tool_bin / "xorchestrate").resolve().parent == sandbox.tool_dir / "xork" / "bin"


def test_the_alias_cannot_be_installed_from_git(sandbox):
    sandbox.provide_uv()
    result = sandbox.run("--git", "v1.2.3", env={"XORCHESTRATE_PACKAGE": "xork"})
    assert result.returncode != 0 and "--git installs xorchestrate from the repository" in result.stderr
    assert sandbox.calls() == []


def test_another_xo_earlier_on_path_is_reported(sandbox):
    sandbox.provide_uv()
    linter = sandbox.root / "node"
    linter.mkdir()
    (linter / "xo").write_text("#!/bin/sh\necho linter\n")
    (linter / "xo").chmod(0o700)
    result = sandbox.run(env={"PATH": f"{linter}:{sandbox.bin}:{sandbox.tool_bin}"})
    assert result.returncode == 0, result.stderr
    assert f"warning: another xo comes first on your PATH ({linter / 'xo'}); use xorchestrate" in result.stdout
    assert "is not on your PATH" not in result.stdout


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


def test_a_failed_download_stops_before_running_an_installer(sandbox):
    sandbox.provide_curl(download_fails=True)
    result = sandbox.run(env={"UV_INSTALLER_URL": INSTALLER})
    assert result.returncode != 0 and "could not download the uv installer from " + INSTALLER in result.stderr
    assert [call[0] for call in sandbox.calls()] == ["curl"]
    assert list(sandbox.tmp.iterdir()) == []


def test_uv_under_cargo_home_is_found(sandbox):
    cargo = sandbox.root / "cargo"
    (cargo / "bin").mkdir(parents=True)
    (sandbox.bin / "curl").unlink(missing_ok=True)
    sandbox.provide_uv()
    (sandbox.bin / "uv").rename(cargo / "bin" / "uv")
    result = sandbox.run(env={"CARGO_HOME": str(cargo)})
    assert result.returncode == 0, result.stderr
    assert "using uv at " + str(cargo / "bin" / "uv") in result.stdout


@pytest.mark.parametrize(("reported", "accepted"), [
    ("0.4.22", False), ("0.3.9", False), ("0.4.23", True), ("0.5.0", True), ("1.0.0", True), ("0.13.7", True)])
def test_an_old_uv_is_refused_naming_the_minimum_version(sandbox, reported, accepted):
    sandbox.provide_uv()
    result = sandbox.run(env={"FAKE_UV_VERSION": reported})
    if accepted:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0
        assert f"uv {reported} at {sandbox.bin / 'uv'} is too old; uv 0.4.23 or newer is required" in result.stderr
        assert installs(sandbox.calls()) == []


def test_a_launcher_that_does_not_run_fails_the_script(sandbox):
    sandbox.provide_uv()
    sandbox.tool_bin.mkdir(parents=True)
    (sandbox.tool_bin / "xorchestrate").write_text("#!/bin/sh\nexit 3\n")
    (sandbox.tool_bin / "xorchestrate").chmod(0o700)
    result = sandbox.run()
    assert result.returncode != 0 and "does not run" in result.stderr and "next steps" not in result.stdout


def test_a_truncated_download_runs_nothing(sandbox, tmp_path):
    sandbox.provide_uv()
    text = SCRIPT.read_text()
    truncated = tmp_path / "truncated.sh"
    truncated.write_text(text[:text.index('    say "installing $requirement')])
    result = subprocess.run([str(sandbox.bin / "sh"), str(truncated)], env={"PATH": str(sandbox.bin),
                            "HOME": str(sandbox.home)}, capture_output=True, text=True, timeout=60, check=False)
    assert result.stdout == "" and sandbox.calls() == []


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
