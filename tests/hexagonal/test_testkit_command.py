"""Exercise the reused command runner against actual child processes.

Adapted from Agentscient ops/testkit/tests/test_command.py at
456457b9a0a9eb1ff285ee2d3e40535b6f722cdb: declared context, partial output,
and bounded cleanup when a descendant holds the command's pipes.
"""

import json
import os
import pty
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.testkit.adapters.command import SubprocessRunner
from scripts.testkit.model import Timeout


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
