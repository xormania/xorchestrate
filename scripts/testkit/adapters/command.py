"""Bounded command adapter adapted from Agentscient's existing test-kit runner.

Source: uscient/agentscient@456457b9a0a9eb1ff285ee2d3e40535b6f722cdb,
ops/testkit/command.py. A command owns a process session; a timeout kills its
descendants and bounds pipe draining. Partial output is retained on Timeout.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time

from ..model import Result, Timeout

CLEANUP_TIMEOUT = 5.0


def _kill_session(child):
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(child.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        child.kill()


def _finish(child):
    """A detached descendant holding pipes cannot hold cleanup indefinitely."""
    try:
        return child.communicate(timeout=CLEANUP_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()
        child.wait(timeout=CLEANUP_TIMEOUT)
        return exc.stdout or b"", exc.stderr or b""


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


class SubprocessRunner:
    def __init__(self, *, terminal="capture"):
        if terminal not in {"capture", "inherit"}:
            raise ValueError("command terminal must be capture or inherit")
        self.terminal = terminal

    def run(self, argv, *, cwd, env, timeout, stdin=None):
        """Capture evidence or retain the operator's terminal descriptors.

        Inherited streams belong to the terminal, so returned output is empty.
        The caller records that capture was disabled. Both modes retain the same
        bounded command-session cleanup; inherited input cannot also be supplied
        as a string or byte buffer.
        """
        if self.terminal == "inherit" and stdin is not None:
            raise ValueError("inherited terminal input cannot also be supplied")
        argv = tuple(str(arg) for arg in argv)
        started = time.monotonic()
        stream = subprocess.PIPE if self.terminal == "capture" else None
        child = subprocess.Popen(argv, cwd=str(cwd), env=dict(env),
                                 stdin=stream, stdout=stream, stderr=stream,
                                 start_new_session=True)
        try:
            out, err = child.communicate(input=stdin.encode("utf-8") if isinstance(stdin, str) else stdin,
                                         timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _kill_session(child)
            out, err = _finish(child)
            out, err = out or b"", err or b""
            raise Timeout(argv, cwd=cwd, timeout=timeout, stdout=_text(out), stderr=_text(err),
                          raw_stdout=out, raw_stderr=err, duration=time.monotonic() - started) from exc
        except BaseException:
            _kill_session(child)
            _finish(child)
            raise
        out, err = out or b"", err or b""
        return Result(argv, child.returncode, _text(out), _text(err),
                      time.monotonic() - started, str(cwd), raw_stdout=out, raw_stderr=err)
