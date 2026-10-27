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


class _OwnedGroup:
    """Observe one acquired group and fence every signal by process identity."""

    def __init__(self, child):
        # Lazy reuse avoids command -> tmux -> evidence -> command imports at
        # module initialization. These are the existing PID/start/pidfd fences.
        from .tmux import processes

        self.pid = child.pid
        self.known = set()
        self.unknown = set()
        owner = processes().get(child.pid)
        if owner is not None:
            self.known.add(owner[0])
        self.observe()

    def observe(self):
        from .tmux import processes

        snapshot = processes()
        members = set()
        for identity, _, _ in snapshot.values():
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if os.getpgid(identity.pid) == self.pid:
                    members.add(identity)
        # A known current member (including an unreaped owner) anchors group
        # continuity. Never adopt a possibly reused saved group ID by itself.
        if self.known & members:
            self.known.update(members)
        self.unknown = {identity for identity in members - self.known
                        if snapshot[identity.pid][2] != "Z"}
        return {identity for identity in self.known
                if snapshot.get(identity.pid, (None,))[0] == identity and snapshot[identity.pid][2] != "Z"}

    def signal(self, number, *, owner_only=False):
        from .tmux import send_signal

        for identity in self.observe():
            if not owner_only or identity.pid == self.pid:
                send_signal(identity, number)


def _kill_session(child, group=None):
    if group is not None:
        group.signal(signal.SIGKILL)
    else:
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

    def _shutdown(self, child, *, reason, scope, group):
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
        deadline = time.monotonic() + self.shutdown_grace
        drain_pending = False
        try:
            group.signal(signal.SIGTERM, owner_only=self.shutdown_target == "owner")
            out, err = child.communicate(timeout=self.shutdown_grace)
        except subprocess.TimeoutExpired:
            drain_pending = True
        except OSError as error:
            receipt["errors"].append({"type": type(error).__name__, "phase": "graceful_shutdown"})
            drain_pending = True
        # communicate() proves the owner's exit and pipe closure, not the
        # group's exit: a grandchild can redirect stdio and ignore TERM.
        remaining = group.observe()
        while remaining and time.monotonic() < deadline:
            time.sleep(min(.02, max(0, deadline - time.monotonic())))
            remaining = group.observe()
        if remaining or drain_pending:
            receipt["forced"] = True
            _kill_session(child, group)
        if drain_pending:
            try:
                out, err = _finish(child)
            except (OSError, subprocess.TimeoutExpired) as error:
                receipt["errors"].append({"type": type(error).__name__, "phase": "forced_drain"})
                out, err = getattr(error, "stdout", b""), getattr(error, "stderr", b"")
        verification_deadline = time.monotonic() + min(CLEANUP_TIMEOUT, max(.1, self.shutdown_grace))
        remaining = group.observe()
        while remaining and time.monotonic() < verification_deadline:
            receipt["forced"] = True
            group.signal(signal.SIGKILL)
            time.sleep(.01)
            remaining = group.observe()
        if group.unknown:
            receipt["errors"].append({"type": "OwnershipUnknown", "phase": "group_verification"})
        if not group.known:
            receipt["errors"].append({"type": "OwnershipUnavailable", "phase": "group_verification"})
        receipt["remaining_processes"] = [{"pid": item.pid, "started": item.started}
                                            for item in sorted(remaining, key=lambda item: item.pid)]
        receipt["exit_status"] = child.returncode
        receipt["verified"] = child.returncode is not None and not remaining and not receipt["errors"]
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
        group = _OwnedGroup(child)
        pending_input = stdin.encode("utf-8") if isinstance(stdin, str) else stdin
        try:
            while True:
                reason = self.cancellation() if self.cancellation is not None else None
                remaining = started + timeout - time.monotonic()
                if reason or remaining <= 0:
                    out, err, shutdown = self._shutdown(
                        child, reason=reason or "check_timeout", scope="run" if reason else "check", group=group)
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
                    group.observe()
        except (Timeout, Cancelled):
            raise
        except BaseException:
            # KeyboardInterrupt/SystemExit still traverse graceful cleanup. The
            # evidence adapter retains the initiating exception and shutdown.
            try:
                self.last_shutdown = self._shutdown(child, reason="interrupted", scope="run", group=group)[2]
            except BaseException:
                _kill_session(child, group)
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    _finish(child)
            raise
        out, err = out or b"", err or b""
        return Result(argv, child.returncode, _text(out), _text(err),
                      time.monotonic() - started, str(cwd), raw_stdout=out, raw_stderr=err)
