"""Private native PTY input/output crosses real processes, never MCP pipes."""

import json
import os
import shutil
import sys
from copy import deepcopy

import pytest

from scripts.testkit.adapters import terminals
from scripts.testkit.adapters.configuration import isolated_config
from tests.hexagonal.contracts.managed_terminals import IDENTITY_FAULTS
from tests.hexagonal.support.managed_terminals import (
    interactive_native_configuration,
    native_terminal_process,
    public_attached_terminal,
    receive_terminal_until,
    receive_until,
)
from tests.hexagonal.support.managed_workflow import PublicWorkflow, await_condition, isolated_candidate
from tests.hexagonal.support.native import native_drivers, wait_for
from xorchestrate.model import Capability

PROMPT = """import os,sys
assert sys.stdin.isatty() and sys.stdout.isatty()
with open('/dev/tty') as terminal:
    assert terminal.isatty()
print('NATIVE_PROMPT', flush=True)
for line in sys.stdin:
    print('RECEIVED:' + line.strip(), flush=True)
"""


@pytest.mark.skipif(os.name != "posix", reason="native terminal contract requires POSIX PTYs")
def test_native_prompt_and_input_have_controlling_terminal_and_leave_protocol_streams_clean(tmp_path):
    directory = tmp_path / "native" / "session"
    with native_terminal_process(directory, [sys.executable, "-u", "-c", PROMPT]) as (process, descriptor):
        assert descriptor["kind"] == "pty"
        assert descriptor["native_id"] == "native-test-session"
        assert descriptor["native_process"]["pid"] != process.pid
        assert terminals.discover(tmp_path)[0]["native_id"] == descriptor["native_id"]
        with terminals.connect(descriptor) as connection:
            receive_until(connection, b"NATIVE_PROMPT")
            connection.sendall(b"operator answer\n")
            receive_until(connection, b"RECEIVED:operator answer")
        process.terminate()
        process.wait(timeout=5)
        assert process.stdout.read() == b""
        assert process.stderr.read() == b""
        assert terminals.discover(tmp_path) == []
        receipt = json.loads((directory / "terminal-exit.json").read_text())
        assert receipt["shutdown_requested"] and not receipt["forced"]
        assert (directory / "terminal.log").stat().st_mode & 0o077 == 0
        assert not terminals._live_identity(descriptor["native_process"])


@pytest.mark.parametrize("fault", IDENTITY_FAULTS)
def test_wrong_owner_or_token_cannot_attach_to_an_owned_native_terminal(tmp_path, fault):
    with native_terminal_process(tmp_path / "terminal", [sys.executable, "-u", "-c", PROMPT]) as (_, original):
        descriptor = deepcopy(original)
        if fault == "token":
            descriptor["token"] = "wrong-token"
        elif fault in {"pid", "started", "uid"}:
            descriptor["owner"][fault] += 1_000_000
        else:
            descriptor["owner"][fault] = "different-boot"
        with pytest.raises((ValueError, OSError)):
            terminals.connect(descriptor)
        # Rejection must not consume or stop the legitimate terminal.
        with terminals.connect(original) as connection:
            receive_until(connection, b"NATIVE_PROMPT")


def test_detached_viewer_can_reconnect_without_restarting_native_process(tmp_path):
    with native_terminal_process(tmp_path / "terminal", [sys.executable, "-u", "-c", PROMPT]) as (_, descriptor):
        with terminals.connect(descriptor) as first:
            receive_until(first, b"NATIVE_PROMPT")
            first.sendall(b"first\n")
            receive_until(first, b"RECEIVED:first")

        def reattached():
            try:
                return terminals.connect(descriptor)
            except ValueError:
                return None

        second = wait_for(reattached)
        with second:
            second.sendall(b"second\n")
            receive_until(second, b"RECEIVED:second")
        assert terminals.validate(descriptor)


def test_managed_configuration_routes_inherited_native_terminal_without_mutating_source(tmp_path, monkeypatch):
    run = tmp_path / "run"
    config = {"sessions": {"selected": {"adapter": "claude", "options": {
        "command": ["native-client"], "terminal": "inherit", "pass_env": ["HOME"], "application_tools": True,
    }}}}
    original = deepcopy(config)
    monkeypatch.setenv("XORCHESTRATE_PROOF_DIRECTORY", str(run))
    derived = isolated_config(config, tmp_path, run / "checks")
    terminal = derived["sessions"]["selected"]["options"]["terminal"]
    assert terminal["adapter"] == "pty" and terminal["directory"] == str(run / "checks" / "native-terminals")
    assert derived["sessions"]["selected"]["options"]["pass_env"] == ["HOME"]
    assert config == original


def test_existing_claude_launcher_keeps_hook_and_mcp_callbacks_when_using_owned_pty(tmp_path):
    calls, observed = [], []
    with native_drivers() as create:
        driver = create("claude", state=tmp_path / "native-state", terminal={
            "adapter": "pty", "directory": str(tmp_path / "terminals"), "purpose": "fixture native prompt"})
        driver.bind_observer(lambda context, event: observed.append(event))
        driver.bind_application_tools([Capability("echo", "Echo", {"type": "object"})],
                                      lambda action, arguments, context, key: calls.append((action, arguments, context)) or {})
        session = driver.start({})
        descriptor = terminals.discover(tmp_path)[0]
        assert descriptor["native_id"] == session["native_id"]
        assert descriptor["incarnation"] == session["incarnation"]
        driver.send(session, {"remember": "retained native memory"})
        wait_for(lambda: any(item["kind"] == "turn_completed" for item in observed))
        driver.send(session, {"recall": "callback nonce"})
        wait_for(lambda: calls)
        assert calls[0][0:2] == ("echo", {"marker": "retained native memory", "nonce": "callback nonce"})
        assert calls[0][2]["native_id"] == session["native_id"]
        driver.stop(session)
        assert terminals.discover(tmp_path) == []


@pytest.mark.skipif(shutil.which("tmux") is None, reason="public managed terminal witness requires real tmux")
def test_public_attach_reaches_nested_native_prompt_and_detaches_without_stopping_it(tmp_path):
    candidate = isolated_candidate(tmp_path / "candidate with spaces")
    workflow = PublicWorkflow(candidate, tmp_path / "operator with spaces")
    configuration = interactive_native_configuration(candidate, workflow.outside)
    try:
        directory, _ = workflow.start(suite="acceptance", mode="native", timeout=30, driver="interactive",
                                      config=configuration, shutdown_grace=5)
        with public_attached_terminal(workflow, directory) as (viewer, terminal):
            receive_terminal_until(terminal, b"FIXTURE_NATIVE_PROMPT")
            descriptor = [item for item in terminals.discover(directory) if item["kind"] == "pty"][0]
            native_process = descriptor["native_process"]
            os.write(terminal, b"human operator answer\n")
            receive_terminal_until(terminal, b"FIXTURE_RECEIVED:human operator answer")
            os.write(terminal, b"\x1d")
            receive_terminal_until(terminal, b"Dashboard:")
            assert terminals.validate(descriptor)
            assert terminals.discover(directory)[0]["native_process"] == native_process
            os.write(terminal, b"q\n")
            assert viewer.wait(timeout=10) == 0
        assert terminals.validate(descriptor), "leaving the public viewer must leave native work running"
        stopped, requested = workflow.command("stop", directory)
        assert stopped.returncode == 0 and requested["status"] == "requested"
        waited, result = workflow.command("wait", directory, timeout=45)
        assert waited.returncode != 0 and result["reason"] == "operator_stop"
        assert result["cleanup"]["verified"] and not result["cleanup"]["forced"]
        await_condition(lambda: not terminals.validate(descriptor))
        receipts = list((directory / "checks").rglob("scenario.json"))
        assert len(receipts) == 1
        scenario = json.loads(receipts[0].read_text())
        assert scenario["error"]["code"] == "interrupted"
        assert "cleanup_error" not in scenario
        assert all(row.get("failure", {}).get("code") not in {"rpc_response", "response_eof", "response_frame_limit"}
                   for row in scenario["checks"])
        closed, _ = workflow.command("close", directory)
        assert closed.returncode == 0
    finally:
        workflow.close()
