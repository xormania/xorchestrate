"""Exercise the reused command runner against actual child processes.

Adapted from Agentscient ops/testkit/tests/test_command.py at
456457b9a0a9eb1ff285ee2d3e40535b6f722cdb: declared context, partial output,
and bounded cleanup when a descendant holds the command's pipes.
"""

import json
import os
import pty
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.testkit.adapters.command import SubprocessRunner
from scripts.testkit.adapters.evidence import run_command, write_json
from scripts.testkit.adapters.tmux import alive, processes, send_signal, subreaper
from scripts.testkit.model import Cancelled, Timeout


def test_inherit_mode_preserves_real_terminal_descriptors_and_input(tmp_path):
    terminal_record = tmp_path / "terminal.json"
    result_record = tmp_path / "result.json"
    program = ("import json,os,sys; from pathlib import Path; "
               f"Path({str(terminal_record)!r}).write_text(json.dumps("
               "{'tty':[os.isatty(fd) for fd in (0,1,2)],'input':sys.stdin.readline()}))")
    helper = (
        "import json,os,sys; from pathlib import Path; "
        "from scripts.testkit.adapters.command import SubprocessRunner; "
        f"r=SubprocessRunner(terminal='inherit').run((sys.executable,'-c',{program!r}), "
        f"cwd={str(tmp_path)!r},env=dict(os.environ),timeout=3); "
        f"Path({str(result_record)!r}).write_text(json.dumps([r.exit,r.stdout,r.stderr,"
        "r.raw_stdout.hex(),r.raw_stderr.hex()]))"
    )
    master, slave = pty.openpty()
    try:
        os.write(master, b"terminal input\n")
        completed = subprocess.run((sys.executable, "-c", helper),
                                   cwd=Path(__file__).resolve().parents[2],
                                   stdin=slave, stdout=slave, stderr=slave, timeout=5)
        assert completed.returncode == 0
    finally:
        os.close(slave)
        os.close(master)
    assert json.loads(terminal_record.read_text()) == {
        "tty": [True, True, True], "input": "terminal input\n"}
    assert json.loads(result_record.read_text()) == [0, "", "", "", ""]


def test_inherited_terminal_retains_timeout_cleanup_without_fabricating_output(tmp_path):
    with pytest.raises(Timeout) as caught:
        SubprocessRunner(terminal="inherit").run(
            (sys.executable, "-c", "import time; time.sleep(20)"),
            cwd=tmp_path, env={}, timeout=0.1)
    assert caught.value.stdout == caught.value.stderr == ""
    assert caught.value.raw_stdout == caught.value.raw_stderr == b""


def test_terminal_policy_and_conflicting_input_are_rejected_before_execution(tmp_path):
    with pytest.raises(ValueError, match="capture or inherit"):
        SubprocessRunner(terminal="unknown")
    with pytest.raises(ValueError, match="input cannot also be supplied"):
        SubprocessRunner(terminal="inherit").run(
            ("must-not-run",), cwd=tmp_path, env={}, timeout=1, stdin="input")


def test_command_observes_exact_argv_environment_cwd_and_exit(tmp_path):
    program = ("import json,os,sys; print(json.dumps([sys.argv[1:],os.getcwd(),dict(os.environ)])); "
               "sys.stderr.write('diagnostic'); raise SystemExit(7)")
    argv = (sys.executable, "-c", program, "one argument; untouched")
    result = SubprocessRunner().run(argv, cwd=tmp_path, env={"DECLARED": "value"}, timeout=5)
    arguments, cwd, environment = json.loads(result.stdout)
    assert arguments == ["one argument; untouched"]
    assert cwd == str(tmp_path)
    assert environment.get("DECLARED") == "value"
    # Python itself can add LC_CTYPE at startup; operator variables are absent.
    assert set(environment) <= {"DECLARED", "LC_CTYPE"}
    assert result.argv == argv
    assert result.exit == result.returncode == 7
    assert result.stderr == "diagnostic"
    assert result.cwd == str(tmp_path)
    assert result.duration >= 0


def test_missing_executable_is_an_explicit_setup_failure(tmp_path):
    with pytest.raises(FileNotFoundError):
        SubprocessRunner().run((str(tmp_path / "absent"),), cwd=tmp_path, env={}, timeout=1)


@pytest.mark.parametrize("timed_out", [False, True], ids=["completed", "timeout"])
def test_raw_evidence_retains_invalid_utf8_bytes_without_reconstruction(tmp_path, timed_out):
    program = ("import os,time; os.write(1,b'out\\xff\\x00'); os.write(2,b'err\\xfe\\r\\n'); "
               + ("time.sleep(20)" if timed_out else ""))
    if timed_out:
        with pytest.raises(Timeout) as caught:
            SubprocessRunner().run((sys.executable, "-c", program), cwd=tmp_path, env={}, timeout=0.2)
        result = caught.value
    else:
        result = SubprocessRunner().run((sys.executable, "-c", program), cwd=tmp_path, env={}, timeout=2)
    assert result.raw_stdout == b"out\xff\x00"
    assert result.raw_stderr == b"err\xfe\r\n"
    assert result.stdout == "out\ufffd\x00"
    assert result.stderr == "err\ufffd\r\n"


@pytest.mark.parametrize("parent_exits", [False, True], ids=["running-parent", "exited-parent-held-pipes"])
def test_timeout_kills_descendants_retains_output_and_does_not_wait_for_held_pipes(tmp_path, parent_exits):
    marker = tmp_path / "descendant-survived"
    descendant = ("import pathlib,time; print('descendant-ready',flush=True); "
                  f"time.sleep(0.8); pathlib.Path({str(marker)!r}).touch(); time.sleep(20)")
    parent = ("import subprocess,sys,time; "
              f"subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
              "print('parent-ready',flush=True); sys.stderr.write('retained error\\n'); sys.stderr.flush(); "
              + ("" if parent_exits else "time.sleep(20)"))
    started = time.monotonic()
    with pytest.raises(Timeout) as caught:
        SubprocessRunner().run((sys.executable, "-c", parent), cwd=tmp_path,
                               env=dict(os.environ), timeout=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 3
    assert "parent-ready" in caught.value.stdout
    assert "descendant-ready" in caught.value.stdout
    assert "retained error" in caught.value.stderr
    assert caught.value.duration >= 0.3
    time.sleep(0.9)
    assert not marker.exists(), "descendant executed after the command timeout"


@pytest.mark.parametrize("trigger", ["timeout", "cancel"])
def test_graceful_shutdown_reaches_finalizer_and_retains_distinct_scope(tmp_path, trigger):
    ready, finalized = tmp_path / "ready", tmp_path / "finalized"
    program = (
        "import signal,time,sys; from pathlib import Path\n"
        f"def finish(*_): Path({str(finalized)!r}).write_text('saved'); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,finish)\n"
        f"Path({str(ready)!r}).touch()\n"
        "print('partial evidence',flush=True)\n"
        "while True: time.sleep(.02)\n"
    )
    cancellation = (lambda: "operator_stop" if ready.exists() else None) if trigger == "cancel" else None
    exception = Cancelled if trigger == "cancel" else Timeout
    with pytest.raises(exception) as caught:
        SubprocessRunner(cancellation=cancellation, shutdown_grace=1).run(
            (sys.executable, "-c", program), cwd=tmp_path, env={}, timeout=0.5)
    assert finalized.read_text() == "saved"
    assert "partial evidence" in caught.value.stdout
    assert caught.value.shutdown["forced"] is False
    assert caught.value.shutdown["scope"] == ("run" if trigger == "cancel" else "check")
    assert caught.value.shutdown["reason"] == ("operator_stop" if trigger == "cancel" else "check_timeout")


def test_stubborn_command_escalates_within_bound_and_preserves_timeout(tmp_path):
    program = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(20)"
    started = time.monotonic()
    with pytest.raises(Timeout) as caught:
        SubprocessRunner(shutdown_grace=0.1).run(
            (sys.executable, "-c", program), cwd=tmp_path, env={}, timeout=0.3)
    assert time.monotonic() - started < 2
    assert caught.value.shutdown["forced"] is True
    assert caught.value.shutdown["reason"] == "check_timeout"
    assert caught.value.shutdown["exit_status"] < 0


@pytest.mark.parametrize("trigger", ["timeout", "cancel"])
@pytest.mark.parametrize("shutdown_target", ["group", "owner"])
def test_owner_exit_does_not_hide_redirected_owned_group_survivor(tmp_path, trigger, shutdown_target):
    child_pid_file, ready = tmp_path / "child.pid", tmp_path / "ready"
    descendant = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(child_pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    owner = (
        "import subprocess,sys,time; from pathlib import Path\n"
        f"subprocess.Popen([sys.executable,'-c',{descendant!r}],stdin=subprocess.DEVNULL,"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"while not Path({str(child_pid_file)!r}).exists(): time.sleep(.01)\n"
        f"Path({str(ready)!r}).touch()\n"
        "time.sleep(60)\n"
    )
    unrelated = subprocess.Popen((sys.executable, "-c", "import time; time.sleep(60)"), start_new_session=True)
    child_pid = None
    try:
        with subreaper():
            try:
                cancellation = (lambda: "operator_stop" if ready.exists() else None) if trigger == "cancel" else None
                receipt = run_command((sys.executable, "-c", owner), tmp_path, tmp_path / "command",
                                      cancellation=cancellation, shutdown_grace=.1,
                                      shutdown_target=shutdown_target, timeout=.5)
                child_pid = int(child_pid_file.read_text())
                shutdown = receipt["shutdown"]
                assert receipt["status"] == ("cancelled" if trigger == "cancel" else "timeout")
                assert shutdown["reason"] == ("operator_stop" if trigger == "cancel" else "check_timeout")
                assert shutdown["scope"] == ("run" if trigger == "cancel" else "check")
                assert shutdown["forced"] is True
                assert shutdown["verified"] is True and shutdown["remaining_processes"] == []
                observed = processes().get(child_pid)
                assert observed is None or not alive(observed[0]), "redirected owned grandchild survived cleanup"
                assert unrelated.poll() is None
                assert json.loads((tmp_path / "command" / "command.json").read_text())["shutdown"] == shutdown
            finally:
                if child_pid is None and child_pid_file.exists():
                    child_pid = int(child_pid_file.read_text())
                if child_pid is not None:
                    observed = processes().get(child_pid)
                    if observed is not None:
                        send_signal(observed[0], signal.SIGKILL)
                    try:
                        os.waitpid(child_pid, 0)
                    except ChildProcessError:
                        pass
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_captured_supervisor_can_own_child_shutdown_without_duplicate_group_signals(tmp_path):
    count = tmp_path / "child-signals"
    child = (
        "import signal,time,sys; from pathlib import Path\n"
        "observed=[]\n"
        f"def finish(*_): observed.append(1); Path({str(count)!r}).write_text(str(len(observed))); time.sleep(.15); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,finish)\n"
        "while True: time.sleep(.01)\n"
    )
    owner = (
        "import signal,subprocess,sys,time\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child!r}])\n"
        "def finish(*_): time.sleep(.05); child.terminate(); child.wait(); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,finish)\n"
        "while True: time.sleep(.01)\n"
    )
    with pytest.raises(Timeout) as caught:
        SubprocessRunner(shutdown_target="owner", shutdown_grace=1).run(
            (sys.executable, "-c", owner), cwd=tmp_path, env={}, timeout=.5)
    assert count.read_text() == "1"
    assert caught.value.shutdown["forced"] is False


def test_recorded_cancellation_preserves_partial_bytes_and_final_receipt(tmp_path):
    ready = tmp_path / "ready"
    program = f"from pathlib import Path; import time; print('saved',flush=True); Path({str(ready)!r}).touch(); time.sleep(20)"
    directory = tmp_path / "command"
    receipt = run_command((sys.executable, "-c", program), tmp_path, directory,
                          cancellation=lambda: "run_deadline" if ready.exists() else None, timeout=5)
    assert receipt["status"] == "cancelled" and receipt["finished_at"]
    assert receipt["shutdown"]["reason"] == "run_deadline"
    assert receipt["shutdown"]["scope"] == "run"
    assert (directory / "stdout.log").read_bytes() == b"saved\n"
    assert json.loads((directory / "command.json").read_text()) == receipt


def test_command_interruption_keeps_receipt_and_reaches_child_finalizer(tmp_path):
    finalized = tmp_path / "finalized"
    directory = tmp_path / "receipt"
    program = (
        "import signal,time,sys; from pathlib import Path\n"
        f"def finish(*_): Path({str(finalized)!r}).touch(); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,finish)\n"
        "while True: time.sleep(.02)\n"
    )
    helper = (
        "import _thread,threading,sys\n"
        "from scripts.testkit.adapters.evidence import run_command\n"
        "timer=threading.Timer(.4,_thread.interrupt_main); timer.start()\n"
        "try:\n"
        f" run_command((sys.executable,'-c',{program!r}),{str(tmp_path)!r},{str(directory)!r},timeout=10)\n"
        "except KeyboardInterrupt: pass\n"
        "finally: timer.cancel()\n"
    )
    subprocess.run((sys.executable, "-c", helper), cwd=Path(__file__).resolve().parents[2], check=True, timeout=5)
    assert finalized.exists()
    receipt = json.loads((directory / "command.json").read_text())
    assert receipt["status"] == "interrupted" and receipt["error"] == "KeyboardInterrupt"
    assert receipt["finished_at"] and receipt["shutdown"]["forced"] is False


def test_atomic_writer_has_no_shared_temporary_file_under_concurrent_publications(tmp_path):
    path = tmp_path / "result.json"

    def publish(index):
        write_json(path, {"index": index, "payload": "x" * 10000})
        # Every visible generation is complete, regardless of writer ordering.
        assert len(json.loads(path.read_text())["payload"]) == 10000

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(publish, range(50)))
    assert json.loads(path.read_text())["index"] in range(50)
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.iterdir()) == [path]


def test_interrupted_atomic_publication_preserves_previous_committed_json(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    write_json(path, {"generation": 1})

    def fail_replace(source, destination):
        raise OSError("publication interrupted")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="publication interrupted"):
        write_json(path, {"generation": 2})
    assert json.loads(path.read_text()) == {"generation": 1}
    assert list(tmp_path.iterdir()) == [path]
