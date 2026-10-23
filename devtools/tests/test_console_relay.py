"""Exercise the CO terminal transport without a vendor CLI or account."""

import errno
import json
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import tty
import unittest
from pathlib import Path

from devtools import processes
from devtools.console import relay

MODULE = "devtools.tests.test_console_relay"
ROOT = Path(__file__).resolve().parents[2]


def wait_for_file(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("Fixture acknowledgment did not arrive: " + path.name)
        time.sleep(0.01)


def native_fixture(report, mode):
    # Raw mode prevents the PTY echo from masquerading as received commands.
    tty.setraw(sys.stdin.fileno())
    if mode == "stubborn-descendant":
        subprocess.Popen([sys.executable, "-m", MODULE, "--descendant-fixture", str(report)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_for_file(report.with_suffix(".descendant"))
    if mode == "stubborn-native":
        signal.signal(signal.SIGTERM, lambda *_: report.with_suffix(".term").touch())
    print("NATIVE READY", flush=True)
    pending = b""
    while True:
        pending += os.read(sys.stdin.fileno(), 4096)
        while b"\n" in pending:
            command, pending = pending.split(b"\n", 1)
            print("RECEIVED " + json.dumps(command.decode()), flush=True)
            if command == b"/quit":
                # Include an unterminated final line to check the exit drain.
                print("NATIVE EXIT", end="", flush=True)
                return


def descendant_fixture(report):
    # Session-leader exit can send SIGHUP before the relay requests shutdown.
    # Survive it so this fixture always exercises TERM-to-KILL escalation.
    signal.signal(signal.SIGHUP, lambda *_: report.with_suffix(".hup").touch())
    signal.signal(signal.SIGTERM, lambda *_: report.with_suffix(".term").touch())
    ready = report.with_suffix(".descendant")
    temporary = ready.with_suffix(".tmp")
    temporary.write_text(json.dumps(processes.identity(os.getpid())))
    temporary.replace(ready)
    while True:
        signal.pause()


def relay_fixture(report, mode):
    terminal_output = []

    class Trace:
        def record(self, kind, data):
            if kind == "process_started":
                report.with_suffix(".pid").write_text(str(data["pid"]))
            if (mode == "trace-start-failure" and kind == "process_started"
                    or mode == "trace-end-failure" and kind == "terminal_output"
                    and "NATIVE EXIT" in data["text"]):
                raise OSError("Fixture telemetry unavailable")
            if kind == "terminal_output":
                terminal_output.append(data["text"])

    signals = (signal.SIGWINCH, signal.SIGTERM, signal.SIGHUP)
    before = [signal.getsignal(sig) for sig in signals]
    code, error = None, None
    try:
        code = relay([sys.executable, "-m", MODULE, "--native-fixture", str(report), mode],
                     os.environ.copy(), ROOT, Trace(), lambda: True)
    except OSError as caught:
        error = str(caught)
    report.write_text(json.dumps({
        "exit_code": code,
        "error": error,
        "handlers_restored": before == [signal.getsignal(sig) for sig in signals],
        "terminal_output": "".join(terminal_output),
    }))


class ConsoleRelayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.report = Path(temporary.name) / "result.json"

    def start(self, mode="normal", **streams):
        process = subprocess.Popen(
            [sys.executable, "-m", MODULE, "--relay-fixture", str(self.report), mode],
            cwd=ROOT, **streams,
        )
        self.addCleanup(self.stop, process)
        return process

    def stop(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                pid_file = self.report.with_suffix(".pid")
                if pid_file.exists():
                    try:
                        os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.kill()
                process.wait(timeout=3)
        # Regressions may let a native child outlive the relay. Own that cleanup
        # independently of whether the driver itself has already exited.
        pid_file = self.report.with_suffix(".pid")
        if pid_file.exists():
            try:
                os.killpg(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    def read_until(self, fd, marker):
        output = b""
        deadline = time.monotonic() + 10
        while marker not in output and time.monotonic() < deadline:
            if not select.select([fd], [], [], 0.1)[0]:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            output += chunk
        self.assertIn(marker, output, output.decode(errors="replace"))
        return output

    def result(self, process):
        self.assertEqual(0, process.wait(timeout=10))
        result = json.loads(self.report.read_text())
        self.assertTrue(result["handlers_restored"])
        pid = int(self.report.with_suffix(".pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        return result

    def test_native_commands_and_quit_pass_through_and_restore_terminal(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        original = termios.tcgetattr(slave)
        process = self.start(stdin=slave, stdout=slave, stderr=slave)
        self.read_until(master, b"NATIVE READY")
        commands = ["/model alternate-model", "/effort high", "\x03", "\x04", "/quit"]
        os.write(master, ("\n".join(commands) + "\n").encode())
        output = self.read_until(master, b"NATIVE EXIT").decode()
        received = [json.loads(line.removeprefix("RECEIVED "))
                    for line in output.splitlines() if line.startswith("RECEIVED ")]
        self.assertEqual(commands, received)
        result = self.result(process)
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(result["terminal_output"].endswith("NATIVE EXIT"))
        for command in commands:
            self.assertIn("RECEIVED " + json.dumps(command), result["terminal_output"])
        self.assertEqual(original, termios.tcgetattr(slave))

    def test_stdin_eof_terminates_native_child_and_returns(self):
        process = self.start(stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.read_until(process.stdout.fileno(), b"NATIVE READY")
        process.stdin.close()
        self.assertEqual(-signal.SIGTERM, self.result(process)["exit_code"])

    def test_stdin_eof_escalates_when_native_child_ignores_termination(self):
        process = self.start("stubborn-native", stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.read_until(process.stdout.fileno(), b"NATIVE READY")
        process.stdin.close()
        self.assertEqual(-signal.SIGKILL, self.result(process)["exit_code"])
        self.assertTrue(self.report.with_suffix(".term").exists())

    def test_quit_terminates_stubborn_descendant_after_native_parent_exits(self):
        process = self.start("stubborn-descendant", stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.read_until(process.stdout.fileno(), b"NATIVE READY")
        descendant = json.loads(self.report.with_suffix(".descendant").read_text())
        self.addCleanup(processes.signal_owned, [descendant], signal.SIGKILL)
        processes.signal_owned([descendant], signal.SIGHUP)
        wait_for_file(self.report.with_suffix(".hup"))
        self.assertTrue(processes.alive([descendant]), "Fixture must survive native terminal hangup")
        process.stdin.write(b"/quit\n")
        process.stdin.flush()
        self.read_until(process.stdout.fileno(), b"NATIVE EXIT")
        self.assertEqual(0, self.result(process)["exit_code"])
        self.assertTrue(self.report.with_suffix(".term").exists())
        self.assertFalse(processes.alive([descendant]), "Native descendant escaped console cleanup")

    def test_telemetry_failure_reaps_child_and_restores_terminal(self):
        for mode in ("trace-start-failure", "trace-end-failure"):
            with self.subTest(mode=mode):
                master, slave = pty.openpty()
                self.addCleanup(os.close, master)
                self.addCleanup(os.close, slave)
                original = termios.tcgetattr(slave)
                process = self.start(mode, stdin=slave, stdout=slave, stderr=slave)
                if mode == "trace-end-failure":
                    self.read_until(master, b"NATIVE READY")
                    os.write(master, b"/quit\n")
                self.assertEqual("Fixture telemetry unavailable", self.result(process)["error"])
                self.assertEqual(original, termios.tcgetattr(slave))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--native-fixture":
        native_fixture(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "--relay-fixture":
        relay_fixture(Path(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "--descendant-fixture":
        descendant_fixture(Path(sys.argv[2]))
    else:
        unittest.main()
