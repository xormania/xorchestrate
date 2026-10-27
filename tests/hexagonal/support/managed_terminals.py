"""Real PTY helper lifetime and bounded terminal-stream observations."""

import fcntl
import json
import os
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
from contextlib import contextmanager
from pathlib import Path

from xorchestrate.adapters import native_terminal

from .native import wait_for


@contextmanager
def native_terminal_process(directory, command):
    process = subprocess.Popen([
        sys.executable, str(Path(native_terminal.__file__).resolve()), "--directory", str(directory),
        "--native-id", "native-test-session", "--incarnation", "test-incarnation", "--", *command,
    ], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        wait_for(lambda: (directory / "terminal.json").exists() or process.poll() is not None)
        assert process.poll() is None, process.communicate(timeout=1)
        yield process, json.loads((directory / "terminal.json").read_text())
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)


def receive_until(connection, marker, timeout=3):
    deadline = time.monotonic() + timeout
    result = bytearray()
    while marker not in result and time.monotonic() < deadline:
        connection.settimeout(max(deadline - time.monotonic(), .01))
        data = connection.recv(65536)
        if not data:
            break
        result.extend(data)
    assert marker in result, bytes(result)
    return bytes(result)


def interactive_native_configuration(candidate, outside):
    """Adapt the existing Claude protocol peer with an operator-input gate.

    This is a declared deterministic command, never an installed vendor. Its
    real hook/MCP path keeps serving while its native tool waits for release.
    All fixture edits precede the candidate measurement.
    """
    source = Path(__file__).resolve().parents[1] / "fixtures"
    destination = candidate / "tests" / "hexagonal" / "fixtures"
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    peer = destination / "claude_native.py"
    text = peer.read_text()
    text = text.replace("def tool(name, arguments):\n", "def tool(name, arguments):\n"
                        "    operator_input.wait(30)\n"
                        "    while not Path(os.environ['OPERATOR_RELEASE']).exists():\n"
                        "        threading.Event().wait(.02)\n")
    text = text.replace("thread_context = threading.local()", "thread_context = threading.local()\n"
                        "operator_input = threading.Event()")
    text = text.replace("while True:\n    signal.pause()", "print('FIXTURE_NATIVE_PROMPT', flush=True)\n"
                        "operator_line = sys.stdin.readline()\n"
                        "print('FIXTURE_RECEIVED:' + operator_line.strip(), flush=True)\n"
                        "operator_input.set()\n"
                        "while True:\n    signal.pause()")
    peer.write_text(text)
    configuration = outside / "interactive-native.json"
    configuration.write_text(json.dumps({"sessions": {"interactive": {"adapter": "claude", "options": {
        "command": [sys.executable, str(peer)], "cwd": str(candidate), "application_tools": True,
        "terminal": "inherit", "startup_timeout": 10, "timeout": 5,
        "env": {"CLAUDE_STATE_DIR": str(outside / "native-state"), "OPERATOR_RELEASE": str(outside / "release")},
    }}}}))
    configuration.chmod(0o600)
    return configuration


@contextmanager
def public_attached_terminal(workflow, directory):
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 160, 0, 0))
    process = subprocess.Popen([str(workflow.root / "scripts/proof.sh"), "attach", str(directory)],
                               cwd=workflow.outside, env=workflow.env, stdin=slave, stdout=slave, stderr=slave,
                               start_new_session=True)
    os.close(slave)
    try:
        yield process, master
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        os.close(master)


def receive_terminal_until(descriptor, marker, timeout=20):
    deadline = time.monotonic() + timeout
    result = bytearray()
    while marker not in result and time.monotonic() < deadline:
        if not select.select([descriptor], [], [], max(deadline - time.monotonic(), .01))[0]:
            break
        try:
            data = os.read(descriptor, 65536)
        except OSError:
            break
        if not data:
            break
        result.extend(data)
    assert marker in result, bytes(result)
    return bytes(result)
