"""Acceptance receipts and actual owned-tmux teardown, including failed runs."""

import json
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
    fixture = lifecycle.environment("fixture", tmp_path)
    assert "ACCEPTANCE_EXAMPLE_SECRET" not in fixture
    assert "TMUX" not in fixture
    assert "PYTHONPATH" not in fixture
    assert Path(fixture["HOME"]).is_relative_to(tmp_path)
    native = lifecycle.environment("native", tmp_path)
    assert native["ACCEPTANCE_EXAMPLE_SECRET"] == "do-not-forward"
    assert "TMUX" not in native


def test_reused_pid_is_never_signaled(monkeypatch):
    called = []
    monkeypatch.setattr(lifecycle, "processes", lambda: {100: (lifecycle.Identity(100, 20), 1, "S")})
    monkeypatch.setattr(lifecycle.os, "kill", lambda *args: called.append(args))
    monkeypatch.setattr(lifecycle.os, "pidfd_open", lambda *args: called.append(args))
    lifecycle.send_signal(lifecycle.Identity(100, 10), signal.SIGTERM)
    assert called == []


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
    code = ("import json,os,time; from pathlib import Path; "
            f"Path({str(tmp_path / 'scenario.json')!r}).write_text(json.dumps({{'status':'passed'}})); "
            "os.close(0); os.close(1); os.close(2); time.sleep(0.3); "
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
