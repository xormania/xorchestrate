"""MCP input adapter: one protocol session per client connection.

Connections come from a ``ClientTransport`` (``stdio`` for one launching
process, ``unix`` for the shared project server). Every session binds the actor
its connection asserted; all sessions share one application and one set of
bounded worker pools, so admission limits apply per server, not per connection.
"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .adapters.stdio import StdioTransport, read_line_frame
from .composition import read_json
from .model import OrchestrationError, public_error
from .worktrees import worktree_identity

PROTOCOL_VERSION = "2025-06-18"
CONTROL_TOOLS = frozenset({"poll_session", "stop_session", "detach_session", "get_operation", "list_sessions", "events"})


def failure(error):
    return public_error(error)


class MCPServer:
    """Protocol state of one MCP session; the actor is fixed by its connection.

    ``peer`` is the connection's ``PeerIdentity``. Its ``worktree`` attribute
    (root, name, branch, configuration hash) is bound to every call of the
    session, like the actor; other asserted attributes stay with the session.
    """

    def __init__(self, application, actor="owner", peer=None):
        self.application = application
        self.actor = actor
        self.peer = peer
        attributes = getattr(peer, "attributes", None)
        self.worktree = worktree_identity(attributes.get("worktree")) if isinstance(attributes, dict) else None
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
                    value = self._call_tool(params["name"], params.get("arguments", {}))
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

    def _call_tool(self, name, arguments):
        if self.worktree is None:
            return self.application.call(name, arguments, actor=self.actor)
        return self.application.call(name, arguments, actor=self.actor, worktree=self.worktree)

    @staticmethod
    def _error(identifier, code, message):
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def frames(stream, maximum):
    """Read bounded newline-delimited UTF-8 frames, recovering after a bad frame."""
    while True:
        try:
            line = read_line_frame(stream, maximum)
        except OrchestrationError as error:
            yield None, error
            continue
        if line is None:
            return
        yield _decode(line)


def _decode(frame):
    try:
        return read_json(frame.decode("utf-8")), None
    except (OrchestrationError, UnicodeError) as exc:
        error = exc if isinstance(exc, OrchestrationError) else OrchestrationError("Expected UTF-8 JSON.", "invalid_json")
        return None, error


class MCPDispatcher:
    """Bounded execution shared by every MCP session of one server.

    Initialization, ping and discovery stay on each connection's input path.
    Observation and lifecycle control have reserved workers, so held
    application calls cannot occupy their execution capacity. Admission is
    counted across all connections. Each reply keeps its request identifier
    and goes to the connection that sent the request.
    """

    def __init__(self, application, request_workers=8, max_pending_requests=64, control_workers=2):
        if any(type(value) is not int or value < 1 for value in (request_workers, max_pending_requests, control_workers)):
            raise OrchestrationError("MCP concurrency limits must be positive integers.", "invalid_configuration")
        self.application = application
        self._pools = {False: ThreadPoolExecutor(max_workers=request_workers, thread_name_prefix="mcp-request"),
                       True: ThreadPoolExecutor(max_workers=control_workers, thread_name_prefix="mcp-control")}
        self._limits = {False: threading.BoundedSemaphore(max_pending_requests),
                        True: threading.BoundedSemaphore(max_pending_requests)}
        self._state = threading.Condition()
        self._in_flight = 0
        self._draining = False
        self._stopped = False

    @property
    def in_flight(self):
        with self._state:
            return self._in_flight

    @property
    def draining(self):
        with self._state:
            return self._draining

    def begin_drain(self):
        """Refuse new effect calls on every connection.

        Calls already admitted continue, and the reserved observation/control
        tools (``CONTROL_TOOLS``) stay available so clients can watch, stop or
        detach their sessions while the server drains.
        """
        with self._state:
            self._draining = True

    def end_drain(self):
        with self._state:
            self._draining = False

    def stop_admission(self):
        """Refuse every new tool call, including observation and control."""
        with self._state:
            self._draining = True
            self._stopped = True

    def wait_idle(self, timeout=None):
        """Wait until no admitted tool call is executing. Return whether that happened."""
        with self._state:
            return self._state.wait_for(lambda: self._in_flight == 0, timeout)

    def close(self):
        for pool in self._pools.values():
            pool.shutdown(wait=True)

    def _finish(self, control):
        self._limits[control].release()
        with self._state:
            self._in_flight -= 1
            self._state.notify_all()

    def _run_inline(self, server, request, control):
        with self._state:
            refused = self._stopped or (self._draining and not control)
            if not refused:
                self._in_flight += 1
        if refused:
            return server._error(None, -32003, "Server is shutting down; this request was not started.")
        try:
            return server.handle(request)
        finally:
            with self._state:
                self._in_flight -= 1
                self._state.notify_all()

    def serve_connection(self, connection):
        """Run one MCP session until its client finishes, then wait for its admitted calls.

        Returns the failures of asynchronous replies. A failure writing a
        synchronous reply propagates to the caller.
        """
        server = MCPServer(self.application, connection.peer.actor, connection.peer)
        local = threading.Condition()
        pending = set()
        failures = []

        def write(response):
            if response is not None:
                connection.write_frame(json.dumps(response, allow_nan=False).encode())

        def execute(request, key, control):
            try:
                write(server.handle(request))
            except Exception as exc:
                with local:
                    failures.append(exc)
            finally:
                with local:
                    pending.discard(key)
                    local.notify_all()
                self._finish(control)

        try:
            while True:
                try:
                    frame = connection.read_frame()
                except OrchestrationError as error:
                    write(server._error(None, -32700, str(error)))
                    continue
                if frame is None:
                    break
                request, error = _decode(frame)
                if error:
                    write(server._error(None, -32700, str(error)))
                    continue
                # Synchronous framing/initialization prevents races with pipelined
                # initialize + call. A notification never executes an application call.
                if not server.initialized or not (isinstance(request, dict) and request.get("method") == "tools/call" and "id" in request):
                    write(server.handle(request))
                    continue
                identifier = request.get("id")
                params = request.get("params", {})
                control = isinstance(params, dict) and isinstance(params.get("name"), str) and params["name"] in CONTROL_TOOLS
                if identifier is None and request.get("jsonrpc") == "2.0":
                    # A null identifier cannot be tracked as pending, so the call
                    # runs inline as before, but it still obeys drain/stop
                    # admission and is counted in flight.
                    write(self._run_inline(server, request, control))
                    continue
                if type(identifier) not in (int, str) or request.get("jsonrpc") != "2.0":
                    write(server.handle(request))  # invalid: answered without executing
                    continue
                key = (type(identifier), identifier)
                admitted = draining = False
                with local:
                    duplicate = key in pending
                    if not duplicate:
                        with self._state:
                            draining = self._stopped or (self._draining and not control)
                            if not draining:
                                admitted = self._limits[control].acquire(blocking=False)
                                if admitted:
                                    self._in_flight += 1
                        if admitted:
                            pending.add(key)
                if duplicate:
                    write(server._error(identifier, -32600, "Request identifier is already pending."))
                elif draining:
                    write(server._error(identifier, -32003, "Server is shutting down; this request was not started."))
                elif not admitted:
                    write(server._error(identifier, -32001, "Concurrent request capacity exceeded; this request was not started."))
                else:
                    try:
                        self._pools[control].submit(execute, request, key, control)
                    except RuntimeError:  # pool already shut down
                        # Not started: give back its admission, count and identifier.
                        with local:
                            pending.discard(key)
                            local.notify_all()
                        self._finish(control)
                        write(server._error(identifier, -32003, "Server is shutting down; this request was not started."))
        finally:
            with local:
                local.wait_for(lambda: not pending)
        return list(failures)


def serve(application, stdin, stdout, actor="owner", max_frame_bytes=1_048_576,
          request_workers=8, max_pending_requests=64, control_workers=2, worktree=None):
    """Serve one MCP session over the given streams until end of input.

    Independent requests run concurrently with bounded admission; the CLI's
    uncorrelated ``run`` interface remains deliberately ordered. ``worktree``
    is the launching process's worktree identity, bound like the actor.
    """
    dispatcher = MCPDispatcher(application, request_workers, max_pending_requests, control_workers)
    attributes = {"worktree": worktree} if worktree is not None else None
    try:
        failures = dispatcher.serve_connection(
            StdioTransport(stdin, stdout, actor, max_frame_bytes, attributes).accept())
    finally:
        dispatcher.close()
    if failures:
        raise OrchestrationError("Cannot write MCP response.", "io_error") from failures[0]
