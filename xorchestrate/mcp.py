"""Stdio MCP input adapter. The host supplies one trusted principal per process."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .composition import read_json
from .model import OrchestrationError

PROTOCOL_VERSION = "2025-06-18"


def failure(error):
    return {"code": error.code, "message": str(error), "details": error.details}


class MCPServer:
    def __init__(self, application, actor="owner"):
        self.application = application
        self.actor = actor
        self.initialized = False

    def handle(self, request):
        if not isinstance(request, dict):
            return self._error(None, -32600, "Expected a JSON-RPC object.")
        identifier = request.get("id")
        if (request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str)
                or isinstance(identifier, bool) or (identifier is not None and not isinstance(identifier, (str, int)))):
            return self._error(None, -32600, "Invalid JSON-RPC request.")
        method = request["method"]
        if "id" not in request:
            return None
        params = request.get("params", {})
        if not isinstance(params, dict):
            return self._error(identifier, -32602, "Parameters must be an object.")
        try:
            if method == "initialize":
                if not isinstance(params.get("protocolVersion"), str):
                    return self._error(identifier, -32602, "protocolVersion is required.")
                self.initialized = True
                result = {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "xorchestrate", "version": __version__},
                    "instructions": "Coordinate work with configurable tools and policies. Tool evidence does not change policy authority.",
                }
            elif method == "ping":
                result = {}
            elif not self.initialized:
                return self._error(identifier, -32002, "Initialize the connection first.")
            elif method == "tools/list":
                result = {"tools": [
                    {"name": item.name, "description": item.description,
                     "inputSchema": item.input_schema, "annotations": item.annotations}
                    for item in self.application.describe()
                ]}
            elif method == "tools/call":
                if not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict):
                    return self._error(identifier, -32602, "Tool name and object arguments are required.")
                try:
                    value = self.application.call(params["name"], params.get("arguments", {}), actor=self.actor)
                    result = {"content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}],
                              "structuredContent": value, "isError": False}
                except OrchestrationError as exc:
                    value = {"error": failure(exc)}
                    result = {"content": [{"type": "text", "text": json.dumps(value, allow_nan=False)}],
                              "structuredContent": value, "isError": True}
            else:
                return self._error(identifier, -32601, "Method not found.")
            return {"jsonrpc": "2.0", "id": identifier, "result": result}
        except Exception:
            # Protocol replies must not leak credentials from adapter exceptions.
            return self._error(identifier, -32603, "Internal error; inspect retained operation state before retrying effects.")

    @staticmethod
    def _error(identifier, code, message):
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def frames(stream, maximum):
    """Read bounded newline-delimited UTF-8 frames, recovering after a bad frame."""
    while True:
        line = stream.readline(maximum + 1)
        if not line:
            return
        if len(line) > maximum:
            while line and not line.endswith(b"\n"):
                line = stream.readline(maximum + 1)
            yield None, OrchestrationError("Input frame exceeds configured limit.", "frame_too_large")
            continue
        if not line.strip():
            continue
        try:
            yield read_json(line.decode("utf-8")), None
        except (OrchestrationError, UnicodeError) as exc:
            error = exc if isinstance(exc, OrchestrationError) else OrchestrationError("Expected UTF-8 JSON.", "invalid_json")
            yield None, error


def serve(application, stdin, stdout, actor="owner", max_frame_bytes=1_048_576,
          request_workers=8, max_pending_requests=64, control_workers=2):
    """Serve independent RPC requests concurrently with bounded admission.

    Initialization, ping and discovery stay on the input path. Observation and
    lifecycle control have reserved workers, so held application calls cannot
    occupy their execution capacity. Each reply retains its request identifier;
    the CLI's uncorrelated ``run`` interface remains deliberately ordered.
    """
    if any(type(value) is not int or value < 1 for value in (request_workers, max_pending_requests, control_workers)):
        raise OrchestrationError("MCP concurrency limits must be positive integers.", "invalid_configuration")
    server = MCPServer(application, actor)
    write_lock = threading.Lock()
    state_lock = threading.Lock()
    pending = set()
    failures = []
    controls = {"poll_session", "stop_session", "detach_session", "get_operation", "list_sessions", "events"}
    limits = {False: threading.BoundedSemaphore(max_pending_requests), True: threading.BoundedSemaphore(max_pending_requests)}

    def write(response):
        if response is not None:
            with write_lock:
                stdout.write(json.dumps(response, allow_nan=False) + "\n")
                stdout.flush()

    def execute(request, key, control):
        try:
            write(server.handle(request))
        except Exception as exc:
            with state_lock:
                failures.append(exc)
        finally:
            with state_lock:
                pending.discard(key)
            limits[control].release()

    with ThreadPoolExecutor(max_workers=request_workers) as workers, ThreadPoolExecutor(max_workers=control_workers) as control_pool:
        for request, error in frames(stdin, max_frame_bytes):
            if error:
                write(server._error(None, -32700, str(error)))
                continue
            # Synchronous framing/initialization prevents races with pipelined
            # initialize + call. A notification never executes an application call.
            if not server.initialized or not (isinstance(request, dict) and request.get("method") == "tools/call" and "id" in request):
                write(server.handle(request))
                continue
            identifier = request.get("id")
            if type(identifier) not in (int, str) or request.get("jsonrpc") != "2.0":
                write(server.handle(request))
                continue
            params = request.get("params", {})
            control = isinstance(params, dict) and isinstance(params.get("name"), str) and params["name"] in controls
            key = (type(identifier), identifier)
            with state_lock:
                duplicate = key in pending
                if not duplicate:
                    admitted = limits[control].acquire(blocking=False)
                    if admitted:
                        pending.add(key)
            if duplicate:
                write(server._error(identifier, -32600, "Request identifier is already pending."))
            elif not admitted:
                write(server._error(identifier, -32001, "Concurrent request capacity exceeded; this request was not started."))
            else:
                (control_pool if control else workers).submit(execute, request, key, control)
    if failures:
        raise OrchestrationError("Cannot write MCP response.", "io_error") from failures[0]
