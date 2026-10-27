"""Bounded command adapter adapted from Agentscient's existing test-kit runner.

Source: uscient/agentscient@456457b9a0a9eb1ff285ee2d3e40535b6f722cdb,
ops/testkit/command.py. A command owns a process session; a timeout kills its
descendants and bounds pipe draining. Partial output is retained on Timeout.
"""

from __future__ import annotations

import contextlib
import math
import os
import signal
import subprocess
import time

from ..model import Cancelled, Result, Timeout

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
    def __init__(self, *, terminal="capture", cancellation=None, shutdown_grace=10.0, shutdown_target=None):
        if terminal not in {"capture", "inherit"}:
            raise ValueError("command terminal must be capture or inherit")
        self.terminal = terminal
        if (isinstance(shutdown_grace, bool) or not isinstance(shutdown_grace, (float, int))
                or not math.isfinite(shutdown_grace) or shutdown_grace < 0):
            raise ValueError("shutdown grace must be finite and nonnegative")
        self.cancellation = cancellation
        self.shutdown_grace = shutdown_grace
        self.shutdown_target = shutdown_target or ("owner" if terminal == "inherit" else "group")
        if self.shutdown_target not in {"owner", "group"}:
            raise ValueError("shutdown target must be owner or group")

    def _shutdown(self, child, *, reason, scope):
        """Signal the command owner first when it owns an interactive finalizer.

        Ordinary commands own a single session; their group receives TERM.
        Supervised check wrappers coordinate nested tmux/native cleanup, so
        only the wrapper receives TERM before a bounded group escalation.
        The session was acquired by this Popen and is never recovered from an
        unverified saved PID. Its group identity remains allocated while any
        member survives.
        """
        receipt = {"reason": str(reason), "scope": scope, "forced": False,
                   "grace_seconds": self.shutdown_grace, "errors": []}
        out, err = b"", b""
        try:
            with contextlib.suppress(ProcessLookupError):
                if self.shutdown_target == "owner":
                    child.terminate()
                else:
                    os.killpg(child.pid, signal.SIGTERM)
            out, err = child.communicate(timeout=self.shutdown_grace)
        except subprocess.TimeoutExpired:
            receipt["forced"] = True
            _kill_session(child)
            try:
                out, err = _finish(child)
            except (OSError, subprocess.TimeoutExpired) as error:
                receipt["errors"].append({"type": type(error).__name__, "phase": "forced_drain"})
                out, err = getattr(error, "stdout", b""), getattr(error, "stderr", b"")
        except OSError as error:
            receipt["errors"].append({"type": type(error).__name__, "phase": "graceful_shutdown"})
            receipt["forced"] = True
            _kill_session(child)
            try:
                out, err = _finish(child)
            except (OSError, subprocess.TimeoutExpired) as error:
                receipt["errors"].append({"type": type(error).__name__, "phase": "forced_drain"})
                out, err = getattr(error, "stdout", b""), getattr(error, "stderr", b"")
        receipt["exit_status"] = child.returncode
        receipt["verified"] = child.returncode is not None and not receipt["errors"]
        self.last_shutdown = receipt
        self.last_output = (out or b"", err or b"")
        return out or b"", err or b"", receipt

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
        reason = self.cancellation() if self.cancellation is not None else None
        if reason:
            raise Cancelled(argv, cwd=cwd, reason=reason, launched=False,
                            shutdown={"reason": str(reason), "scope": "run", "forced": False})
        stream = subprocess.PIPE if self.terminal == "capture" else None
        child = subprocess.Popen(argv, cwd=str(cwd), env=dict(env),
                                 stdin=stream, stdout=stream, stderr=stream,
                                 start_new_session=True)
        pending_input = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        try:
            while True:
                reason = self.cancellation() if self.cancellation is not None else None
                remaining = started + timeout - time.monotonic()
                if reason or remaining <= 0:
                    out, err, shutdown = self._shutdown(
                        child, reason=reason or "check_timeout", scope="run" if reason else "check")
                    values = {"cwd": cwd, "stdout": _text(out), "stderr": _text(err),
                              "raw_stdout": out, "raw_stderr": err,
                              "duration": time.monotonic() - started, "shutdown": shutdown}
                    if reason:
                        raise Cancelled(argv, reason=reason, **values)
                    raise Timeout(argv, timeout=timeout, **values)
                try:
                    out, err = child.communicate(input=pending_input, timeout=min(0.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending_input = None
        except (Timeout, Cancelled):
            raise
        except BaseException:
            # KeyboardInterrupt/SystemExit still traverse graceful cleanup. The
            # evidence adapter retains the initiating exception and shutdown.
            try:
                self.last_shutdown = self._shutdown(child, reason="interrupted", scope="run")[2]
            except BaseException:
                _kill_session(child)
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    _finish(child)
            raise
        out, err = out or b"", err or b""
        return Result(argv, child.returncode, _text(out), _text(err),
                      time.monotonic() - started, str(cwd), raw_stdout=out, raw_stderr=err)
