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
from scripts.testkit.adapters.managed_process import ManagedWorker
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
    assert observed["XORCHESTRATE_TERMINAL_HOST"] is not None, "TESTKIT_WORKER_KNOWS_ITS_TERMINAL_HOST"
    assert observed["XORCHESTRATE_TERMINAL_HOST"] == "acceptance", observed


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
    assert descriptor["receipt"] and "placement" in descriptor["host"], descriptor
    assert "attach" not in descriptor, "nothing executable is recorded"


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


@real_tmux
def test_a_timed_out_scenario_reports_its_interruption_in_the_capture_and_cleans_up(tmp_path):
    """On timeout the worker is interrupted first, so its own FAILED line is captured before the terminal closes."""
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "scripts.acceptance.scenario", "--run-dir", str(tmp_path), "--profile", "fixture",
               "--timeout", "120"]
    report = run_in_host(command, tmp_path, timeout=1.0, grace=8, cwd=root)
    assert report["failure"] == "timeout", report
    assert report["cleanup"]["verified"], report["cleanup"]
    # Whether the scenario's own runtime cleanup succeeds depends on the phase it was interrupted in (as before
    # the terminal host); the ordering itself is proven deterministically below.
    capture = (tmp_path / "pane.txt").read_text()
    assert any(line.startswith("FAILED") and "interrupted" in line for line in capture.splitlines()), capture[-2000:]
    assert "interrupted" in (tmp_path / "terminal.log").read_text(errors="replace"), "the log keeps the full output"


@real_tmux
def test_a_timed_out_worker_alone_is_asked_to_stop_and_its_last_words_are_captured(tmp_path):
    """Timeout stops the worker first (its children untouched, for it to stop in order), then captures its output."""
    marker = tmp_path / "child.signalled"
    code = ("import os,signal,subprocess,sys,time; from pathlib import Path\n"
            f"marker = {str(marker)!r}\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import signal,sys,time\\n'\n"
            "    f'signal.signal(signal.SIGTERM, lambda *_: (open({marker!r}, \"w\").close(), sys.exit(0)))\\n'\n"
            "    f'signal.signal(signal.SIGINT, lambda *_: (open({marker!r}, \"w\").close(), sys.exit(0)))\\n'\n"
            "    f'open({marker!r} + \".ready\", \"w\").close()\\ntime.sleep(60)\\n'])\n"
            "while not os.path.exists(marker + '.ready'): time.sleep(0.01)\n"
            "def stop(*_):\n"
            "    time.sleep(0.3)\n"
            "    print(f'FAILED worker: interrupted child-signalled={os.path.exists(marker)}', flush=True)\n"
            "    child.kill(); child.wait(); sys.exit(1)\n"
            "for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(number, stop)\n"
            "print('working', flush=True); time.sleep(60)\n")
    report = run_in_host([sys.executable, "-c", code], tmp_path, timeout=2, grace=5)
    assert report["failure"] == "timeout" and report["cleanup"]["verified"], report
    pane = (tmp_path / "pane.txt").read_text()
    assert "child-signalled=True" not in pane, "TESTKIT_TIMEOUT_STOPS_THE_WORKER_ALONE_FIRST"
    assert "FAILED worker: interrupted child-signalled=False" in pane, pane[-500:]


def test_an_unready_default_host_is_a_setup_error_never_a_silent_fallback(tmp_path, monkeypatch):
    """The test kit takes the product default's host without its fallback; another host only when selected."""
    import xorchestrate.composition as product
    from scripts.acceptance import __main__ as acceptance_cli
    from scripts.testkit import composition as testkit
    from scripts.testkit.adapters import terminal_selection

    monkeypatch.setattr(product, "DEFAULT_TERMINAL_HOST", {**UNREADY, "fallback": {"adapter": "detached-process"}})
    assert "fallback" not in terminal_selection.parse(None), "TESTKIT_DEFAULT_HOST_HAS_NO_FALLBACK"
    check = testkit.plan(directory=tmp_path / "plan", suite="acceptance", mode="protocol-fixture")[0]
    row = testkit.readiness(check, root=Path(__file__).resolve().parents[2], mode="protocol-fixture")
    item = next(item for item in row["readiness"] if item["name"] == "terminal_host")
    assert row["status"] != "passed" and item["passed"] is False and item["adapter"] is None, row
    assert acceptance_cli.main(["--output", str(tmp_path / "runs"), "--timeout", "5"]) == 1
    [report] = (tmp_path / "runs").rglob("report.json")
    assert json.loads(report.read_text())["failure"] == "terminal_host_unavailable", "the CI lane cannot pass on it"
    explicit = terminal_selection.parse('{"adapter": "detached-process"}')
    assert explicit == {"adapter": "detached-process"}, "an explicitly selected host is used as selected"


DETACHED = {"adapter": "detached-process"}
ENDINGS = {"exit-7": ("exit 7", 7, 7), "signal": ("kill -TERM $$", -signal.SIGTERM, -1)}


class SlowToAnswer:
    """The host, with its first answer about its terminals returning late: the worker ends meanwhile."""

    def __init__(self, host):
        self.host, self.answered = host, False

    def terminals(self):
        found = self.host.terminals()
        if not self.answered:
            self.answered = True
            time.sleep(1)
        return found

    def __getattr__(self, name):
        return getattr(self.host, name)


@pytest.mark.parametrize("ending", sorted(ENDINGS))
@pytest.mark.parametrize("caller", ["supervisor", "managed"])
def test_the_host_alone_reaps_what_it_spawned_so_a_failed_worker_is_never_reported_as_zero(tmp_path, caller, ending):
    """The worker writes a passing scenario receipt, then fails while the kit is between looks at the host."""
    command, supervised_exit, managed_exit = ENDINGS[ending]
    script = tmp_path / "worker.sh"
    script.write_text("#!/bin/sh\n"
                      f"printf '{{\"status\": \"passed\"}}' > {tmp_path / 'scenario.json'}\n"
                      f'[ "$3" = _dashboard ] && exec sleep 30\nsleep 0.5\n{command}\n')
    script.chmod(0o700)
    if caller == "supervisor":
        with owned_host(DETACHED) as selected:
            report = supervise([str(script)], tmp_path, host=SlowToAnswer(selected.host),
                               host_report=selected.report(), timeout=10, grace=0.5)
        assert report["worker_exit"] == supervised_exit, report
        assert report["status"] == "failed", f"a passed receipt from a worker that then fails is not accepted: {report}"
        assert report["cleanup"]["verified"], report
        return
    with owned_host(DETACHED, name="managed") as selected, ownership.subreaper():
        worker = ManagedWorker(tmp_path, host=selected.host, root=tmp_path, interpreter=str(script), grace=0.5)
        try:
            worker.start()
            deadline, status = time.monotonic() + 10, None
            while status is None and time.monotonic() < deadline:
                status = worker.poll()
                time.sleep(0.05)
            assert status == managed_exit, f"the worker's own ending, never a stolen 0: {status}"
            assert worker.cleanup_work()["verified"]
        finally:
            worker.close()
