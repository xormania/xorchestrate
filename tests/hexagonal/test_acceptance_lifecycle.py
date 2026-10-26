"""Acceptance receipts and actual owned-tmux teardown, including failed runs."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.acceptance import lifecycle


def test_missing_tmux_fails_with_a_retained_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: None)
    report = lifecycle.supervise([sys.executable, "-c", "raise SystemExit(0)"], tmp_path)
    assert report["status"] == "failed"
    assert report["failure"] == "tmux_missing"
    assert json.loads((tmp_path / "report.json").read_text()) == report


def test_fixture_environment_does_not_inherit_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCEPTANCE_EXAMPLE_SECRET", "do-not-forward")
    monkeypatch.setenv("TMUX", "unrelated-server")
    monkeypatch.setenv("PYTHONPATH", "unrelated-imports")
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "external-bytecode"))
    fixture = lifecycle.environment("fixture", tmp_path)
    assert "ACCEPTANCE_EXAMPLE_SECRET" not in fixture
    assert "TMUX" not in fixture
    assert "PYTHONPATH" not in fixture
    assert fixture["PYTHONPYCACHEPREFIX"] == str(tmp_path / "external-bytecode")
    assert Path(fixture["HOME"]).is_relative_to(tmp_path)
    native = lifecycle.environment("native", tmp_path)
    assert native["ACCEPTANCE_EXAMPLE_SECRET"] == "do-not-forward"
    assert "TMUX" not in native


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
                               cwd=candidate, env=lifecycle.environment("fixture", tmp_path),
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert not list(candidate.rglob("*.pyc"))
    assert not (candidate / "__pycache__").exists()
    assert bool(list(cache.rglob("cache_probe*.pyc"))) == (policy == "external")


def test_reused_pid_is_never_signaled(monkeypatch):
    called = []
    monkeypatch.setattr(lifecycle, "processes", lambda: {100: (lifecycle.Identity(100, 20), 1, "S")})
    monkeypatch.setattr(lifecycle.os, "kill", lambda *args: called.append(args))
    monkeypatch.setattr(lifecycle.os, "pidfd_open", lambda *args: called.append(args))
    lifecycle.send_signal(lifecycle.Identity(100, 10), signal.SIGTERM)
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

    with lifecycle.subreaper():
        parent = subprocess.Popen([sys.executable, "-c", code])
        owned = None
        child = None
        try:
            wait_for(ready.exists)
            if already_spawned:
                spawn.touch()
                wait_for(child_pid.exists)
            tracker = lifecycle.OwnedProcesses()
            if not already_spawned:
                spawn.touch()
                wait_for(child_pid.exists)
                tracker.observe()
            child = lifecycle.processes()[int(child_pid.read_text())][0]
            assert lifecycle.processes()[child.pid][1] == parent.pid
            owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
            release.touch()
            parent.wait(timeout=5)
            assert lifecycle.processes()[child.pid][1] == os.getpid()

            survivors = tracker.observe()
            assert child not in tracker.known
            assert {identity.pid for identity in survivors} == {owned.pid}
            for identity in survivors:
                lifecycle.send_signal(identity, signal.SIGTERM)
            owned.wait(timeout=5)
            tracker.reap()
            assert tracker.observe() == []
            assert lifecycle.alive(child), "cleanup must leave the adopted unrelated descendant alive"
        finally:
            if parent.poll() is None:
                parent.terminate()
            parent.wait(timeout=5)
            if owned is not None:
                if owned.poll() is None:
                    owned.kill()
                owned.wait(timeout=5)
            if child is None and child_pid.exists():
                child = lifecycle.processes().get(int(child_pid.read_text()), (None,))[0]
            if child is not None:
                lifecycle.send_signal(child, signal.SIGKILL)
                wait_for(lambda: not lifecycle.alive(child))
                os.waitpid(child.pid, os.WNOHANG)


@pytest.mark.parametrize("already_spawned", [True, False], ids=["initial-tree", "later-observed-tree"])
def test_exclusion_follows_the_complete_tree_across_reparenting(monkeypatch, already_spawned):
    monkeypatch.setattr(lifecycle.os, "getpid", lambda: 1)
    parent = os.getpid()
    unrelated = lifecycle.Identity(100, 10)
    child = lifecycle.Identity(101, 11)
    grandchild = lifecycle.Identity(102, 12)
    owned = lifecycle.Identity(200, 20)
    # Reverse ancestry order requires a transitive closure rather than one pass.
    descendants = {102: (grandchild, 101, "S"), 101: (child, 100, "S")}
    snapshot = {**(descendants if already_spawned else {}), 100: (unrelated, parent, "S")}
    monkeypatch.setattr(lifecycle, "processes", lambda: snapshot)
    tracker = lifecycle.OwnedProcesses()
    if not already_spawned:
        snapshot = {**descendants, **snapshot}
        assert tracker.observe() == []
    snapshot = {102: (grandchild, 101, "S"), 101: (child, parent, "S"), 200: (owned, parent, "S")}
    assert tracker.observe() == [owned]
    snapshot = {102: (grandchild, parent, "S"), 200: (owned, parent, "S")}
    assert tracker.observe() == [owned]


def test_excluded_pid_reuse_does_not_exclude_a_new_owned_process(monkeypatch):
    monkeypatch.setattr(lifecycle.os, "getpid", lambda: 1)
    parent = os.getpid()
    old = lifecycle.Identity(100, 10)
    current = lifecycle.Identity(100, 20)
    snapshot = {100: (old, parent, "S")}
    monkeypatch.setattr(lifecycle, "processes", lambda: snapshot)
    tracker = lifecycle.OwnedProcesses()
    snapshot = {100: (current, parent, "S")}
    assert tracker.observe() == [current]


real_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is installed by the acceptance CI job")


@real_tmux
@pytest.mark.parametrize("receipt,exit_code,expected", [(True, 0, "passed"), (False, 0, "failed"), (True, 7, "failed")])
def test_pass_requires_worker_exit_and_scenario_receipt(tmp_path, receipt, exit_code, expected):
    code = ("import json,sys; from pathlib import Path; "
            + (f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}})); " if receipt else "")
            + f"sys.exit({exit_code})")
    report = lifecycle.supervise([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.5)
    assert report["status"] == expected, report
    assert report["worker_exit"] == exit_code
    assert report["cleanup"]["verified"], report
    assert report["cleanup"]["remaining_processes"] == 0
    assert (tmp_path / "pane.txt").exists()


@real_tmux
@pytest.mark.parametrize("exit_code", [0, 7])
def test_terminal_eof_waits_for_the_actual_worker_exit(tmp_path, exit_code):
    completed = tmp_path / "after-eof"
    code = ("import json,os,signal,time; from pathlib import Path; "
            f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}})); "
            "signal.signal(signal.SIGHUP,signal.SIG_IGN); os.close(0); os.close(1); os.close(2); time.sleep(0.3); "
            f"Path({str(completed)!r}).write_text('worker reached its own exit'); os._exit({exit_code})")
    report = lifecycle.supervise([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.5)
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
    report = lifecycle.supervise([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.5)
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
        report = lifecycle.supervise([sys.executable, "-c", code], tmp_path, timeout=2, grace=0.2)
        assert report["status"] == "failed"
        assert report["failure"] == "timeout", report
        assert report["cleanup"]["verified"], report
        assert report["cleanup"]["forced"]
        assert child_pid.exists(), report
        assert int(child_pid.read_text()) not in lifecycle.processes()
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
        report = lifecycle.supervise([sys.executable, "-c", code], tmp_path, timeout=10, grace=0.2)
        assert report["cleanup"]["verified"], report
        assert report["cleanup"]["forced"]
        assert int(child_pid.read_text()) not in lifecycle.processes()
        assert report["status"] == "failed"
        assert report["failure"] == "owned_process_leak"
        assert unrelated.poll() is None
        assert unrelated_socket.exists()
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
