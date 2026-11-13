"""Isolated candidates and real public-shell/process workflow subjects."""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from scripts.testkit.adapters.ownership import Identity, alive, processes, send_signal

SOURCE = Path(__file__).resolve().parents[3]


def isolated_candidate(destination):
    destination.mkdir()
    for name in ("scripts", "xorchestrate", "contracts", "docs", ".github"):
        shutil.copytree(SOURCE / name, destination / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "ruff.toml", "AGENTS.md", "README.md"):
        shutil.copy2(SOURCE / name, destination / name)
    for argv in (("git", "init", "-q"), ("git", "add", "."),
                 ("git", "-c", "user.name=workflow-fixture", "-c", "user.email=workflow@example.invalid",
                  "commit", "-qm", "Isolated managed workflow candidate")):
        subprocess.run(argv, cwd=destination, check=True, capture_output=True)
    return destination


def json_lines(output):
    result = []
    for line in output.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def owner_running(owner):
    """Whether the run's recorded owner process still runs, judged by its identity (pid and start time).

    ``owner_alive`` turns false as soon as the owner record is released; the supervisor exits a few
    milliseconds later. A test that must leave no process behind waits for this instead.
    """
    if not isinstance(owner, dict):
        return False
    try:
        return alive(Identity(owner["pid"], owner["started"]))
    except (KeyError, TypeError):
        return False


def await_owner_exit(read_owner, *, timeout=20):
    """Wait, on the shared classified budget, until the owner that ``read_owner()`` returns has exited."""
    from tests.hexagonal.support.native import wait_for

    return wait_for(lambda: not owner_running(read_owner()), timeout, message="the run's supervisor did not exit")


def await_condition(predicate, *, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.05)
    raise TimeoutError("Expected managed workflow transition was not observed")


class PublicWorkflow:
    def __init__(self, candidate, outside):
        self.root, self.outside = candidate, outside
        self.outside.mkdir(parents=True, exist_ok=True)
        self.env = {**os.environ, "PROOF_PYTHON": sys.executable,
                    "PYTHONPYCACHEPREFIX": str(outside / "cache")}
        self.runs, self.frozen = [], []

    def command(self, *arguments, timeout=90):
        result = subprocess.run([str(self.root / "scripts/proof.sh"), *map(str, arguments)],
                                cwd=self.outside, env=self.env, capture_output=True, text=True, timeout=timeout)
        rows = json_lines(result.stdout)
        return result, rows[-1] if rows else {}

    def start(self, *, suite="multi-harness", mode="fixture", timeout=5, source=None, **overrides):
        argv = ["start", "--detach", "--suite", suite, "--mode", mode, "--timeout", str(timeout),
                "--shutdown-grace", "1", "--output", str(self.outside / "runs")]
        if source is not None:
            argv += ["--from", str(source)]
        for name, value in overrides.items():
            argv += ["--" + name.replace("_", "-"), str(value)]
        result, response = self.command(*argv)
        if result.returncode or not response.get("directory"):
            raise AssertionError(f"Public start failed: {result.stdout}\n{result.stderr}")
        directory = Path(response["directory"])
        self.runs.append(directory)
        return directory, response

    def state(self, directory):
        return json.loads((directory / "state.json").read_text())

    def freeze(self, directory):
        # The run's state names no process (the terminal host exposes none); find the worker by its command line.
        from tests.hexagonal.support.host_processes import arguments_containing

        found = [pid for pid, command in arguments_containing(str(directory)) if " _worker " in f" {command} "]
        assert len(found) == 1, found
        identity = processes()[found[0]][0]
        send_signal(identity, signal.SIGSTOP)
        self.frozen.append(identity)
        return identity

    def resume(self):
        for identity in self.frozen:
            send_signal(identity, signal.SIGCONT)
        self.frozen.clear()

    def close(self):
        self.resume()
        for directory in self.runs:
            with contextlib.suppress(Exception):
                state = self.state(directory)
                if state["phase"] not in {"finished", "incomplete"}:
                    self.command("stop", directory)
                    self.command("wait", directory, timeout=40)
                self.command("close", directory, timeout=20)
                await_owner_exit(lambda: self.state(directory).get("owner"), timeout=15)


@contextlib.contextmanager
def foreign_terminal():
    binary = shutil.which("tmux")
    if binary is None:
        raise FileNotFoundError("Real tmux is required for managed workflow tests")
    with tempfile.TemporaryDirectory(prefix="mw-", dir="/tmp") as directory:
        socket = str(Path(directory) / "foreign.sock")
        base = [binary, "-S", socket, "-f", os.devnull]
        subprocess.run([*base, "new-session", "-d", "-s", "proof-unrelated", "--", sys.executable,
                        "-c", "import time; time.sleep(120)"], check=True, capture_output=True)
        try:
            yield lambda: subprocess.run([*base, "-N", "has-session", "-t", "=proof-unrelated"],
                                         capture_output=True, timeout=3).returncode == 0
        finally:
            subprocess.run([*base, "-N", "kill-server"], capture_output=True, timeout=3)
