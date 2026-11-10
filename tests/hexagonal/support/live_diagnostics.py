"""Real worker, entrypoint and protocol-peer subjects for diagnostics, readiness and the probe."""

import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

from tests.hexagonal.support.managed_workflow import json_lines

ROOT = Path(__file__).resolve().parents[3]
# The protocol peer speaks the Codex app-server dialect; these are its session and turn requests.
SESSION_START, TURN = "thread/start", "turn/start"


def shared_directory(parent, mode):
    """A directory with exactly this mode, whatever the process umask."""
    directory = Path(parent) / "location"
    directory.mkdir()
    directory.chmod(mode)
    return directory


def home_accepted(root):
    """Ask the product itself whether it would use a private home beneath ``root``."""
    from xorchestrate.instance.services import services
    from xorchestrate.model import OrchestrationError

    try:
        services({"XORCHESTRATE_HOME": str(Path(root) / "run" / "home")})
    except OrchestrationError:
        return False
    return True


def peer_session(*options, root=ROOT):
    """A configured session driven by the deterministic protocol peer."""
    return {"adapter": "codex", "options": {
        "command": [sys.executable, "-m", "scripts.acceptance.peers", "codex", *map(str, options)],
        "cwd": str(root), "application_tools": True}}


def native_configuration(path, sessions, *, capture=False):
    """An operator-style native configuration.

    Capture stays off to keep the subject small unless the export of captured
    telemetry is part of the claim; then the run's default local capture applies.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"sessions": sessions}
    if not capture:
        document["telemetry"] = {"enabled": False}
    path.write_text(json.dumps(document))
    return path


def observed_methods(trace):
    trace = Path(trace)
    return [json.loads(line)["method"] for line in trace.read_text().splitlines()] if trace.exists() else []


def worker(run_directory, *arguments, monkeypatch, timeout=30):
    """Run the scenario worker in-process, as its owned pane would, and read what it retained."""
    from scripts.acceptance.scenario import main

    monkeypatch.setenv("TMUX", "/tmp/unused-worker-context,1,0")
    monkeypatch.setenv("TMUX_PANE", "%0")
    run_directory = Path(run_directory)
    status = main(["--run-dir", str(run_directory), "--timeout", str(timeout), *map(str, arguments)])
    text = (run_directory / "scenario.json").read_text()
    diagnostics = run_directory / "diagnostics.json"
    return status, json.loads(text), text, json.loads(diagnostics.read_text()) if diagnostics.exists() else None


def run_testkit(*arguments, cache, timeout=120, umask=-1):
    """The public test-kit entrypoint as a separate process; returns it with its final JSON row."""
    env = dict(os.environ)
    env.setdefault("PYTHONPYCACHEPREFIX", str(cache))
    result = subprocess.run([sys.executable, "-m", "scripts.testkit", *map(str, arguments)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=timeout, umask=umask)
    rows = json_lines(result.stdout)
    return result, rows[-1] if rows else {}


def pane_text(run_directory):
    """What the owned pane showed, with rows wrapped at the pane width joined back together."""
    path = Path(run_directory) / "pane.txt"
    return "".join(path.read_text(encoding="utf-8").splitlines()) if path.exists() else ""


def receipts(directory):
    """Every scenario receipt beneath one run."""
    return [json.loads(path.read_text()) for path in sorted(Path(directory).rglob("scenario.json"))]


def archive_names(archive):
    with ZipFile(archive) as bundle:
        return bundle.namelist()


def archive_text(archive):
    """Everything one archive would hand to a reader."""
    with ZipFile(archive) as bundle:
        return "\n".join(bundle.read(name).decode("utf-8", "replace") for name in bundle.namelist())



def scripted_client(*replies):
    """A scenario client whose transport answers from a script; an exception in it is raised at that request."""
    from scripts.acceptance.scenario import Client

    client = Client.__new__(Client)
    client.budget = None
    client.requests = []
    pending = list(replies)

    def call(method, arguments):
        client.requests.append((method, arguments))
        reply = pending.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    client.call = call
    return client


def failed_effect(operation_id):
    """An application response for a failed effect that preserved its outcome under ``operation_id``."""
    return {"isError": True, "structuredContent": {"error": {
        "code": "operation_unknown", "message": "Subprocess returned a JSON-RPC error.",
        "details": {"operation_id": operation_id}}}}
