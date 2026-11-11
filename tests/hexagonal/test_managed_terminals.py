"""Private native PTY input/output crosses real processes, never MCP pipes."""

import json
import os
import sys
from copy import deepcopy
from pathlib import Path

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
from tests.hexagonal.support.terminal_host import requires_host
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


@requires_host()
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


def _descriptor(directory, endpoint, **fields):
    from scripts.testkit.adapters.ownership import processes

    me = processes()[os.getpid()][0]
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    descriptor = {"schema_version": 2, "kind": "terminal_host", "purpose": "acceptance worker", "terminal": "acceptance",
                  "owner": {"pid": me.pid, "started": me.started, "boot_id": boot, "uid": os.getuid()},
                  "receipt": {"opaque": True}, "interactive": True,
                  "host": {"selection": {"adapter": "tmux"},
                           "placement": {"name": "acceptance", "location": str(directory / "terminal-host"),
                                         "socket": str(endpoint / "host.sock")}}, **fields}
    path = directory / "terminal.json"
    path.write_text(json.dumps(descriptor))
    path.chmod(0o600)
    return {**descriptor, "descriptor_path": str(path)}


@pytest.fixture
def endpoint():
    import shutil as files
    import tempfile

    path = Path(tempfile.mkdtemp(prefix="xa-"))
    yield path
    files.rmtree(path, ignore_errors=True)


def test_host_descriptors_are_schema_two_and_older_ones_are_refused_with_a_reason(tmp_path, endpoint):
    current = tmp_path / "current"
    current.mkdir(mode=0o700)
    descriptor = _descriptor(current, endpoint)
    assert terminals.validate(descriptor) and terminals.refusal(descriptor) is None
    assert [item["terminal"] for item in terminals.discover(current)] == ["acceptance"]
    assert not terminals.validate({**descriptor, "owner": {**descriptor["owner"], "started": 1}}), "a dead run"
    old = tmp_path / "old"
    old.mkdir(mode=0o700)
    legacy = _descriptor(old, endpoint, schema_version=1, kind="tmux", socket="/tmp/x.sock", pane="acceptance:0.0")
    assert terminals.discover(old) == []
    with pytest.raises(ValueError, match="older test kit"):
        terminals.attach(legacy)
    with pytest.raises(ValueError, match="expected 2"):
        terminals.attach({**descriptor, "schema_version": 1})


def test_a_planted_descriptor_is_neither_offered_nor_run(tmp_path, endpoint):
    """Run directories are writable by the launched harness: nothing a descriptor says is executed."""
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    marker = tmp_path / "executed"
    planted = _descriptor(run, endpoint, attach={"argv": ["touch", str(marker)], "text": "planted"})
    calls = []

    def host_for(selection, placement):
        calls.append((selection, placement))
        return None

    with pytest.raises(ValueError):
        terminals.attach(planted, host_for=host_for)
    assert not marker.exists(), "an argv read from a descriptor was executed"
    assert calls and calls[0][1]["location"] == str(run / "terminal-host"), "attach asks the host, never the file"
    for placement in ({"name": "acceptance", "location": str(tmp_path / "elsewhere"), "socket": str(endpoint / "host.sock")},
                      {"name": "acceptance", "location": str(run / "terminal-host"), "socket": str(tmp_path / "host.sock")},
                      {"name": "acceptance", "location": str(run / "terminal-host"), "socket": "/tmp/host.sock"}):
        moved = _descriptor(run, endpoint, host={"selection": None, "placement": placement})
        assert not terminals.validate(moved) and terminals.discover(run) == [], placement
        with pytest.raises(ValueError):
            terminals.attach(moved, host_for=host_for)
    assert len(calls) == 1, "a placement outside the run's own location never builds a host"


@pytest.mark.parametrize("planted", ["factory", "options"])
def test_a_rewritten_host_selection_is_refused_before_anything_is_imported_or_run(tmp_path, endpoint, monkeypatch,
                                                                               planted):
    """The harness can rewrite the run's files: attach and ownerless close take only a built-in host name from them."""
    import shutil as files
    import tempfile

    from scripts.testkit import operator_cli
    from scripts.testkit.operator_composition import ENDPOINT_PREFIX, close_without_supervisor

    marker = tmp_path / "imported"
    package = tmp_path / "planted"
    package.mkdir()
    (package / "planted_host.py").write_text(f"open({str(marker)!r}, 'w').write('imported')\nmake = lambda options: None\n")
    monkeypatch.syspath_prepend(str(package))
    binary = tmp_path / "binary.sh"
    binary.write_text(f"#!/bin/sh\ntouch {marker}\n")
    binary.chmod(0o700)
    selection = ({"adapter": "planted_host:make"} if planted == "factory"
                 else {"adapter": "tmux", "options": {"binary": str(binary)}})
    run = tmp_path / "run"
    run.mkdir(mode=0o700)
    descriptor = _descriptor(run, endpoint)
    descriptor["host"]["selection"] = selection
    with pytest.raises(ValueError):
        terminals.attach(descriptor, host_for=operator_cli._attach_host)
    assert not marker.exists() and "planted_host" not in sys.modules, "attach built a host the file chose"

    closing = tmp_path / "closing"
    closing.mkdir(mode=0o700)
    closing_endpoint = Path(tempfile.mkdtemp(prefix=ENDPOINT_PREFIX))
    try:
        (closing / "plan.json").write_text(json.dumps({"terminal_host": selection,
                                                       "terminal_endpoint": str(closing_endpoint)}))
        result = close_without_supervisor(closing, {})
    finally:
        files.rmtree(closing_endpoint, ignore_errors=True)
    assert not marker.exists() and "planted_host" not in sys.modules, "ownerless close built a host the file chose"
    if planted == "factory":
        assert not result["cleanup"]["verified"] and result["cleanup"].get("errors"), result
