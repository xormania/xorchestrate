"""Best-effort, host-retained observations from the Serena process audit hook.

These records are diagnostics from an untrusted interpreter, not attestations.
The host supplies identity and request windows; no guest identity is accepted.
"""

import json
import threading
import time
from collections import deque

from .telemetry import Trace

PREFIX = b"AGENTSCIENT_PROCESS_AUDIT "
MAX_LINE = 65536
PROCESS_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty"}


def origin(stack):
    """Conservative diagnostic classification, never an authorization decision."""
    # Pinned SolidLSP's language-server launcher is checked below. Command names
    # alone must never hide a direct REPL invocation of php, git or a shell.
    backend = any(frame.get("file", "").replace("\\", "/").endswith("/solidlsp/util/subprocess_util.py")
                  and frame.get("function") == "do_popen" for frame in stack)
    if any("serena_repl" in frame.get("file", "").lower() and frame.get("file", "").startswith("<")
           for frame in stack):
        return "repl"
    if backend:
        return "backend"
    return "unknown"


class ProcessObservation:
    def __init__(self, directory, member, container, frame_context, gateway_connection=None):
        self.container = container
        self.gateway_connection = gateway_connection
        self.frame_context = dict(frame_context)
        self.windows = deque(maxlen=256)
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.thread = None
        self.capture_errors = 0
        self.trace = None
        try:
            self.trace = Trace(directory, member, component="serena-mcp")
        except Exception:
            # Missing audit evidence is reported by the operator reader. An
            # observation failure must not prevent ordinary source operations.
            self.capture_errors += 1
        self.status("starting")

    def record(self, kind, data, frame_context=None):
        try:
            if self.trace is not None:
                self.trace.record(kind, {**data, "container": self.container,
                                         "gateway_connection": self.gateway_connection},
                                  frame_context=frame_context or self.frame_context)
        except Exception:
            self.capture_errors += 1

    def status(self, status, **values):
        self.record("process_audit_status", {"status": status, **values})

    def begin(self, ident, frame_context, gateway_request_id=None):
        with self.lock:
            self.windows.append({"request_id": ident, "start": time.time_ns(), "end": None,
                                 "context": dict(frame_context), "gateway_request_id": gateway_request_id})
        self.record("process_audit_status", {"status": "call_started", "request_id": ident,
                                             "gateway_request_id": gateway_request_id}, frame_context)

    def finish(self, ident):
        with self.lock:
            window = next((row for row in reversed(self.windows) if row["request_id"] == ident), None)
            if window is not None:
                window["end"] = time.time_ns()
        self.record("process_audit_status", {"status": "call_finished", "request_id": ident,
                                             "gateway_request_id": window["gateway_request_id"] if window else None,
                                             "observer_ready": self.ready.is_set(),
                                             "capture_errors": self.capture_errors},
                    window["context"] if window else None)

    def correlation(self, event):
        with self.lock:
            for row in reversed(self.windows):
                if event.get("request_id") == row["request_id"]:
                    return row, "mcp_request"
            for row in reversed(self.windows):
                if row["start"] <= event["time_ns"] <= (row["end"] or time.time_ns()):
                    return row, "time_window"
        return None, "unattributed"

    def consume(self, line):
        """Consume only audit lines. Raw audit payloads never reach stderr logs."""
        if not line.startswith(PREFIX):
            return False
        try:
            event = json.loads(line[len(PREFIX):])
            if not isinstance(event, dict) or event.get("version") != 1:
                raise ValueError("Unsupported audit record")
            if type(event.get("time_ns")) is not int or type(event.get("pid")) is not int:
                raise ValueError("Audit record has no clock or process")
            dropped = event.get("dropped_before", 0)
            if type(dropped) is not int or dropped < 0:
                raise ValueError("Invalid loss count")
            if dropped:
                self.capture_errors += 1
                self.status("dropped", count=dropped)
            if event.get("kind") == "observer_ready":
                self.ready.set()
                self.status("ready", pid=event["pid"], evidence="best_effort_python_audit")
                return True
            if event.get("kind") != "process_attempt" or event.get("event") not in PROCESS_EVENTS:
                raise ValueError("Unsupported process event")
            stack = event.get("stack", [])
            if not isinstance(stack, list) or len(stack) > 32 or any(
                not isinstance(frame, dict) or not isinstance(frame.get("file"), str)
                or not isinstance(frame.get("function"), str) for frame in stack
            ):
                raise ValueError("Invalid process stack")
            row, correlation = self.correlation(event)
            # Whitelist fields; the interpreter cannot supply actor/job/role,
            # authorization, confirmed execution, or a trusted origin label.
            data = {key: event[key] for key in ("event", "time_ns", "monotonic_ns", "pid", "executable", "args", "cwd",
                                               "stack", "capture_truncated", "dropped_before") if key in event}
            data.update(evidence="attempt", origin=origin(stack), correlation=correlation)
            if row is not None:
                data["request_id"] = row["request_id"]
                data["gateway_request_id"] = row["gateway_request_id"]
            self.record("process_audit", data, row["context"] if row else None)
        except Exception:
            self.capture_errors += 1
            self.status("capture_error", reason="malformed_audit_record")
        return True

    def read(self, stream, log):
        try:
            while line := stream.readline(MAX_LINE + 1):
                if len(line) > MAX_LINE:
                    self.capture_errors += 1
                    self.status("capture_error", reason="oversized_stderr_record")
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_LINE + 1)
                    continue
                if not self.consume(line):
                    try:
                        log.write(line)
                        log.flush()
                    except Exception:
                        self.capture_errors += 1
        except Exception:
            self.capture_errors += 1
            self.status("capture_error", reason="stderr_reader_failed")
        finally:
            stream.close()

    def attach(self, stream, log):
        self.thread = threading.Thread(target=self.read, args=(stream, log), daemon=True)
        self.thread.start()

    def check_ready(self):
        if not self.ready.wait(0.2):
            self.status("unavailable", reason="observer_ready_not_received_rebuild_image")

    def close(self):
        if self.thread is not None:
            self.thread.join(timeout=1)
            if self.thread.is_alive():
                self.capture_errors += 1
                self.status("capture_error", reason="stderr_drain_incomplete")
        if not self.ready.is_set():
            self.status("unavailable", reason="observer_ready_not_received")
        self.status("closed", observer_ready=self.ready.is_set(), capture_errors=self.capture_errors)
        if self.trace is not None:
            self.trace.close()
