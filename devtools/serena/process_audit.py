"""Best-effort CPython process-attempt observation; never an execution policy.

The Linux container writes bounded records to a separately opened nonblocking
stderr descriptor. The gateway owns correlation, redaction and durable telemetry.
Guest Python shares this interpreter and can interfere with this observer: these
events are diagnostic evidence, not a complete or tamper-proof security audit.
"""

import json
import os
import subprocess
import sys
import time

PREFIX = b"AGENTSCIENT_PROCESS_AUDIT "
MAX_RECORD_BYTES = 4096
EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty"})
PROBE = "agentscient.process_observer.probe"
# Capture stdlib code identities before guest code runs. Matching only the
# immediate launcher frames keeps unrelated direct spawn calls observable.
_POSIX_SPAWN_CODE = getattr(getattr(subprocess.Popen, "_posix_spawn", None), "__code__", None)
_EXECUTE_CHILD_CODE = subprocess.Popen._execute_child.__code__


class ProcessObserver:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.dropped = 0
        self.installed = False

    @staticmethod
    def text(value, limit):
        # Do not invoke guest objects' repr/str/path conversion from an audit hook.
        # Omit complete long fields: a sliced secret may evade host redaction.
        if type(value) is bytes:
            return ("[omitted: over limit]", True) if len(value) > limit else (value.decode("utf-8", errors="replace"), False)
        if type(value) is str:
            return ("[omitted: over limit]", True) if len(value) > limit else (value, False)
        if value is None:
            return None, False
        return "<non-string>", True

    def stack(self):
        rows = []
        truncated = False
        frame = sys._getframe(2)
        while frame is not None and len(rows) < 8:
            filename, filename_cut = self.text(frame.f_code.co_filename, 160)
            function, function_cut = self.text(frame.f_code.co_name, 80)
            truncated = truncated or filename_cut or function_cut
            rows.append({
                "file": filename, "function": function,
                "line": frame.f_lineno,
            })
            frame = frame.f_back
        return rows, truncated or frame is not None

    @staticmethod
    def request_id():
        # Best effort only: Serena's worker threads need not retain this context.
        # This is the request ContextVar used by the pinned MCP SDK 1.28.1.
        module = sys.modules.get("mcp.server.lowlevel.server")
        variable = getattr(module, "request_ctx", None)
        context = variable.get(None) if variable is not None else None
        ident = getattr(context, "request_id", None)
        return ident if type(ident) is int and ident.bit_length() <= 64 or type(ident) is str and len(ident) <= 128 else None

    def emit(self, kind, **details):
        row = {
            "version": 1, "kind": kind, "time_ns": time.time_ns(),
            "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(),
            "dropped_before": self.dropped, "capture_truncated": False, **details,
        }
        while True:
            payload = b"\n" + PREFIX + json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
            if len(payload) <= MAX_RECORD_BYTES:
                break
            row["capture_truncated"] = True
            if row.get("stack"):
                row["stack"].pop()
            elif row.get("args"):
                row["args"].pop()
            else:
                row.update(executable=None, cwd=None)
        try:
            written = os.write(self.descriptor, payload)
            if written != len(payload):
                self.dropped += 1
            else:
                self.dropped = 0
        except (OSError, ValueError):
            self.dropped += 1

    def audit(self, event, values):
        if self.descriptor is None:
            return
        if event == PROBE:
            self.installed = True
            return
        if event not in EVENTS:
            return
        try:
            if event == "os.posix_spawn":
                frame = sys._getframe(1)
                if (frame.f_code is _POSIX_SPAWN_CODE and frame.f_back is not None
                        and frame.f_back.f_code is _EXECUTE_CHILD_CODE):
                    # Popen already emitted this launch attempt. Direct calls
                    # to os.posix_spawn must still produce their own record.
                    return
            executable, arguments, cwd = None, (), None
            if event == "subprocess.Popen":
                executable, arguments, cwd = values[:3]
            elif event in {"os.exec", "os.posix_spawn"}:
                executable, arguments = values[:2]
            elif event == "os.spawn":
                executable, arguments = values[1:3]
            elif event == "os.system":
                arguments = (values[0],)
            if type(arguments) not in (tuple, list):
                arguments = (arguments,)
            args = [self.text(value, 160) for value in arguments[:8]]
            executable, executable_cut = self.text(executable, 256)
            cwd, cwd_cut = self.text(cwd, 256)
            stack, stack_cut = self.stack()
            details = {
                "event": event, "evidence": "attempt", "executable": executable,
                "args": [value for value, _ in args], "cwd": cwd, "stack": stack,
                "capture_truncated": executable_cut or cwd_cut or stack_cut or len(arguments) > 8
                or any(cut for _, cut in args),
            }
            try:
                ident = self.request_id()
                if ident is not None:
                    details["request_id"] = ident
            except Exception:
                pass  # Request context is optional; keep the process evidence.
            self.emit("process_attempt", **details)
        except BaseException:
            # Observation must never reject an otherwise permitted operation.
            self.dropped += 1


def install():
    """Install inside Serena's interpreter, or continue with coverage unavailable."""
    descriptor = None
    observer = None
    try:
        # Reopen rather than dup: O_NONBLOCK must not change Serena's stderr flags.
        descriptor = os.open("/proc/self/fd/2", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        observer = ProcessObserver(descriptor)
        sys.addaudithook(observer.audit)
        sys.audit(PROBE)
        if not observer.installed:
            observer.descriptor = None
            os.close(descriptor)
            return None
        observer.emit("observer_ready")
        return observer
    except BaseException:
        if observer is not None:
            observer.descriptor = None
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return None
