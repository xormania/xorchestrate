"""Stdio MCP input adapter. The host supplies one trusted principal per process."""
import json

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


def serve(application, stdin, stdout, actor="owner", max_frame_bytes=1_048_576):
    server = MCPServer(application, actor)
    for request, error in frames(stdin, max_frame_bytes):
        response = server._error(None, -32700, str(error)) if error else server.handle(request)
        if response is not None:
            stdout.write(json.dumps(response, allow_nan=False) + "\n")
            stdout.flush()
