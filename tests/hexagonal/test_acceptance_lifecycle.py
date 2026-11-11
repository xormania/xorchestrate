"""Acceptance receipts and actual owned terminal-host teardown, including failed runs."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.testkit.adapters import ownership
from scripts.testkit.adapters.supervised_run import supervise
from tests.hexagonal.support.terminal_host import MULTIPLEXER, owned_host, requires_host

UNREADY = {"adapter": "tmux", "options": {"binary": "/nonexistent/multiplexer"}}


def run_in_host(command, run_dir, selection=MULTIPLEXER, **options):
    """Supervise ``command`` in an owned host built as the acceptance entrypoint builds one."""
    with owned_host(selection) as selected:
        return supervise(command, run_dir, host=selected.host, host_report=selected.report(), **options)


def test_an_unready_terminal_host_fails_with_a_retained_receipt(tmp_path):
    report = run_in_host([sys.executable, "-c", "raise SystemExit(0)"], tmp_path, UNREADY)
    assert report["status"] == "failed"
    assert report["failure"] == "terminal_host_unavailable"
    assert report["terminal_host"]["selected"] == "tmux" and report["terminal_host"]["reason"], report
    assert json.loads((tmp_path / "report.json").read_text()) == report


def test_fixture_environment_does_not_inherit_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_EXAMPLE_SECRET", "do-not-forward")
    monkeypatch.setenv("TMUX", "unrelated-server")
    monkeypatch.setenv("PYTHONPATH", "unrelated-imports")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "external-bytecode"))
    fixture = ownership.environment("fixture", tmp_path)
    assert "ACCEPTANCE_EXAMPLE_SECRET" not in fixture
    assert "TMUX" not in fixture
    assert "PYTHONPATH" not in fixture
    assert fixture["PYTHONPYCACHEPREFIX"] == str(tmp_path / "external-bytecode")
    assert Path(fixture["HOME"]).is_relative_to(tmp_path)
    native = ownership.environment("native", tmp_path)
    assert native["ACCEPTANCE_EXAMPLE_SECRET"] == "do-not-forward"


@requires_host()
def test_the_worker_never_sees_the_controllers_multiplexer_markers(tmp_path, monkeypatch):
    """The host strips its own nesting markers, so a native worker started from inside one is not confused."""
    monkeypatch.setenv("TMUX", "/tmp/unrelated-server,1,0")
    monkeypatch.setenv("TMUX_PANE", "%9")
    seen = tmp_path / "seen.json"
    code = (f"import json,os; from pathlib import Path; Path({str(seen)!r}).write_text(json.dumps("
            "{k: os.environ.get(k) for k in ('TMUX_PANE', 'XORCHESTRATE_TERMINAL_HOST')})); "
            f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}}))")
    report = run_in_host([sys.executable, "-c", code], tmp_path, profile="native", timeout=10, grace=0.5)
    assert report["status"] == "passed", report
    observed = json.loads(seen.read_text())
    assert observed["TMUX_PANE"] != "%9", "the controller's pane marker reached the worker"
    assert observed["XORCHESTRATE_TERMINAL_HOST"] == "acceptance"


@pytest.mark.parametrize("policy", ["external", "disabled"])
def test_fixture_child_import_keeps_bytecode_outside_source(tmp_path, monkeypatch, policy):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "cache_probe.py").write_text("value = 1\n")
    cache = tmp_path / "external-bytecode"
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX" if policy == "external" else "PYTHONDONTWRITEBYTECODE",
                       str(cache) if policy == "external" else "1")
    completed = subprocess.run([sys.executable, "-c", "import cache_probe; assert cache_probe.value == 1"],
                               cwd=candidate, env=ownership.environment("fixture", tmp_path),
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert not list(candidate.rglob("*.pyc"))
    assert not (candidate / "__pycache__").exists()
    assert bool(list(cache.rglob("cache_probe*.pyc"))) == (policy == "external")


def test_reused_pid_is_never_signaled(monkeypatch):
    called = []
    monkeypatch.setattr(ownership, "processes", lambda: {100: (ownership.Identity(100, 20), 1, "S")})
    monkeypatch.setattr(ownership.os, "kill", lambda *args: called.append(args))
    monkeypatch.setattr(ownership.os, "pidfd_open", lambda *args: called.append(args))
    ownership.send_signal(ownership.Identity(100, 10), signal.SIGTERM)
    assert called == []


@pytest.mark.parametrize("already_spawned", [True, False], ids=["initial-descendant", "later-observed-descendant"])
def test_unrelated_descendant_remains_excluded_after_its_parent_exits(tmp_path, already_spawned):
    try:
        proc_pid = int(Path("/proc/self/stat").read_text().split(" ", 1)[0])
    except (OSError, ValueError):
        pytest.skip("real process ancestry requires readable Linux /proc process identities")
    if proc_pid != os.getpid():
        pytest.skip("host exposes /proc outside the subprocess PID namespace")
    ready = tmp_path / "ready"
    spawn = tmp_path / "spawn"
    child_pid = tmp_path / "child.pid"
    release = tmp_path / "release"
    code = f"""
import subprocess, sys, time
from pathlib import Path
Path({str(ready)!r}).touch()
while not Path({str(spawn)!r}).exists():
    time.sleep(0.01)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
pid_path = Path({str(child_pid)!r})
pid_path.with_suffix('.tmp').write_text(str(child.pid))
pid_path.with_suffix('.tmp').replace(pid_path)
while not Path({str(release)!r}).exists():
    time.sleep(0.01)
"""

    def wait_for(predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert predicate()

    with ownership.subreaper():
        parent = subprocess.Popen([sys.executable, "-c", code])
        owned = None
        child = None
        try:
            wait_for(ready.exists)
            if already_spawned:
                spawn.touch()
                wait_for(child_pid.exists)
            tracker = ownership.OwnedProcesses()
            if not already_spawned:
                spawn.touch()
                wait_for(child_pid.exists)
                tracker.observe()
            child = ownership.processes()[int(child_pid.read_text())][0]
            assert ownership.processes()[child.pid][1] == parent.pid
            owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
            release.touch()
            parent.wait(timeout=5)
            assert ownership.processes()[child.pid][1] == os.getpid()

            survivors = tracker.observe()
            assert child not in tracker.known
            assert {identity.pid for identity in survivors} == {owned.pid}
            for identity in survivors:
                ownership.send_signal(identity, signal.SIGTERM)
            owned.wait(timeout=5)
            tracker.reap()
            assert tracker.observe() == []
            assert ownership.alive(child), "cleanup must leave the adopted unrelated descendant alive"
        finally:
            if parent.poll() is None:
                parent.terminate()
            parent.wait(timeout=5)
            if owned is not None:
                if owned.poll() is None:
                    owned.kill()
                owned.wait(timeout=5)
            if child is None and child_pid.exists():
                child = ownership.processes().get(int(child_pid.read_text()), (None,))[0]
            if child is not None:
                ownership.send_signal(child, signal.SIGKILL)
                wait_for(lambda: not ownership.alive(child))
                os.waitpid(child.pid, os.WNOHANG)


@pytest.mark.parametrize("already_spawned", [True, False], ids=["initial-tree", "later-observed-tree"])
def test_exclusion_follows_the_complete_tree_across_reparenting(monkeypatch, already_spawned):
    monkeypatch.setattr(ownership.os, "getpid", lambda: 1)
    parent = os.getpid()
    unrelated = ownership.Identity(100, 10)
    child = ownership.Identity(101, 11)
    grandchild = ownership.Identity(102, 12)
    owned = ownership.Identity(200, 20)
    # Reverse ancestry order requires a transitive closure rather than one pass.
    descendants = {102: (grandchild, 101, "S"), 101: (child, 100, "S")}
    snapshot = {**(descendants if already_spawned else {}), 100: (unrelated, parent, "S")}
    monkeypatch.setattr(ownership, "processes", lambda: snapshot)
    tracker = ownership.OwnedProcesses()
    if not already_spawned:
        snapshot = {**descendants, **snapshot}
        assert tracker.observe() == []
    snapshot = {102: (grandchild, 101, "S"), 101: (child, parent, "S"), 200: (owned, parent, "S")}
    assert tracker.observe() == [owned]
    snapshot = {102: (grandchild, parent, "S"), 200: (owned, parent, "S")}
    assert tracker.observe() == [owned]


def test_excluded_pid_reuse_does_not_exclude_a_new_owned_process(monkeypatch):
    monkeypatch.setattr(ownership.os, "getpid", lambda: 1)
    parent = os.getpid()
    old = ownership.Identity(100, 10)
    current = ownership.Identity(100, 20)
    snapshot = {100: (old, parent, "S")}
    monkeypatch.setattr(ownership, "processes", lambda: snapshot)
    tracker = ownership.OwnedProcesses()
    snapshot = {100: (current, parent, "S")}
    assert tracker.observe() == [current]


real_tmux = requires_host()


@real_tmux
@pytest.mark.parametrize("receipt,exit_code,expected", [(True, 0, "passed"), (False, 0, "failed"), (True, 7, "failed")])
def test_pass_requires_worker_exit_and_scenario_receipt(tmp_path, receipt, exit_code, expected):
    code = ("import json,sys; from pathlib import Path; "
            + (f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}})); " if receipt else "")
            + f"sys.exit({exit_code})")
    report = run_in_host([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.5)
    assert report["status"] == expected, report
    assert report["worker_exit"] == exit_code
    assert report["cleanup"]["verified"], report
    assert report["cleanup"]["remaining_processes"] == 0
    assert (tmp_path / "pane.txt").exists()
    descriptor = json.loads((tmp_path / "terminal.json").read_text())
    assert descriptor["schema_version"] == 2 and descriptor["kind"] == "terminal_host", descriptor
    assert descriptor["attach"]["argv"] and descriptor["receipt"], descriptor


@real_tmux
@pytest.mark.parametrize("exit_code", [0, 7])
def test_terminal_eof_waits_for_the_actual_worker_exit(tmp_path, exit_code):
    completed = tmp_path / "after-eof"
    code = ("import json,os,signal,time; from pathlib import Path; "
            f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}})); "
            "signal.signal(signal.SIGHUP,signal.SIG_IGN); os.close(0); os.close(1); os.close(2); time.sleep(0.3); "
            f"Path({str(completed)!r}).write_text('worker reached its own exit'); os._exit({exit_code})")
    report = run_in_host([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.5)
    assert completed.exists(), report
    assert report["worker_exit"] == exit_code, report
    assert report["status"] == ("passed" if exit_code == 0 else "failed"), report
    assert report["cleanup"]["verified"], report
    assert not report["cleanup"]["forced"], report


@real_tmux
def test_worker_signal_is_a_final_failed_exit(tmp_path):
    code = ("import json,os,signal; from pathlib import Path; "
            f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}})); "
            "os.kill(os.getpid(),signal.SIGTERM)")
    report = run_in_host([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.5)
    assert report["status"] == "failed", report
    assert report["failure"] == "worker_signaled", report
    assert report["worker_exit"] == -signal.SIGTERM, report
    assert report["cleanup"]["verified"], report


@real_tmux
def test_timeout_reaps_detached_descendant_without_touching_unrelated_process(tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    child_pid = tmp_path / "child.pid"
    code = ("import subprocess,sys,time,signal; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
            "p=subprocess.Popen([sys.executable,'-c','import time,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'],start_new_session=True); "
            f"Path({str(child_pid)!r}).write_text(str(p.pid)); time.sleep(60)")
    try:
        report = run_in_host([sys.executable, "-c", code], tmp_path, timeout=2, grace=0.2)
        assert report["status"] == "failed"
        assert report["failure"] == "timeout", report
        assert report["cleanup"]["verified"], report
        assert report["cleanup"]["forced"]
        assert child_pid.exists(), report
        assert int(child_pid.read_text()) not in ownership.processes()
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


@real_tmux
def test_fast_orphan_cannot_escape_cleanup(tmp_path):
    unrelated_socket = tmp_path / "unrelated.sock"
    unrelated = subprocess.Popen([shutil.which("tmux"), "-D", "-S", str(unrelated_socket), "-f", "/dev/null"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child_pid = tmp_path / "orphan.pid"
    code = ("import subprocess,sys,json; from pathlib import Path; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
            f"Path({str(child_pid)!r}).write_text(str(p.pid)); "
            f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}}))")
    try:
        deadline = time.monotonic() + 3
        while not unrelated_socket.exists() and unrelated.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert unrelated_socket.exists()
        report = run_in_host([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.2)
        assert report["cleanup"]["verified"], report
        assert report["cleanup"]["forced"]
        assert int(child_pid.read_text()) not in ownership.processes()
        assert report["status"] == "failed"
        assert report["failure"] == "owned_process_leak"
        assert unrelated.poll() is None
        assert unrelated_socket.exists()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
