"""Command line input adapter; JSON is the common boundary with MCP.

People use these commands directly. Agent harnesses use ``connect``, which
relays MCP between the harness's stdio and the long-running server.
"""
import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path

from .adapters.local_socket import InstanceLock, check_socket_path
from .adapters.unix import UnixClientTransport
from .composition import build, describe, load_config, read_json
from .mcp import MCPDispatcher, failure, frames, serve
from .model import OrchestrationError
from .server import (
    ProjectServer,
    companion_paths,
    default_socket_path,
    ensure_server,
    open_client,
    relay,
    server_command,
    wait_stopped,
)

SERVER_COMMANDS = {"connect", "up", "down", "status"}


def _write(value, stream):
    stream.write(json.dumps(value, allow_nan=False) + "\n")
    stream.flush()


def _parser():
    parser = argparse.ArgumentParser(description="Configurable agent orchestration")
    parser.add_argument("--config", help="Explicit trusted JSON configuration file")
    parser.add_argument("--actor", default="owner", help="Principal for this local process")
    parser.add_argument("--max-frame-bytes", type=int, default=1_048_576)
    parser.add_argument("--request-workers", type=int, default=8, help="Concurrent MCP effect workers")
    parser.add_argument("--control-workers", type=int, default=2, help="Reserved MCP observation/control workers")
    parser.add_argument("--max-pending-requests", type=int, default=64, help="MCP admission bound per worker pool")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("describe", help="List application operations and input schemas")
    call = commands.add_parser("call", help="Run one operation; read JSON from stdin by default")
    call.add_argument("action")
    inputs = call.add_mutually_exclusive_group()
    inputs.add_argument("--json", dest="json_input")
    inputs.add_argument("--input", help="JSON argument file")
    socket_help = "Server socket; default derived from the resolved --config under a per-user runtime directory"
    serve_command = commands.add_parser("serve", help="Serve MCP over stdin/stdout until EOF, or as a shared server")
    serve_command.add_argument("--transport", choices=("stdio", "unix"), default="stdio",
                               help="stdio: one client on this process's streams; unix: the shared server")
    serve_command.add_argument("--socket", help=socket_help)
    serve_command.add_argument("--max-connections", type=int, default=64, help="Concurrent clients of a unix server")
    commands.add_parser("run", help="Process JSON lines {action, arguments} until EOF; retain live sessions")
    connect = commands.add_parser("connect", help="Agent harness MCP entry: relay stdio to the server, starting it if needed")
    connect.add_argument("--socket", help=socket_help)
    connect.add_argument("--actor", dest="connection_actor", help="Actor for this connection (default: --actor)")
    connect.add_argument("--start-timeout", type=float, default=30.0, help="Seconds to wait for a server to accept")
    up = commands.add_parser("up", help="Start the server if it is not running and report its status")
    up.add_argument("--socket", help=socket_help)
    up.add_argument("--start-timeout", type=float, default=30.0, help="Seconds to wait for a server to accept")
    down = commands.add_parser("down", help="Drain in-flight requests and stop the server")
    down.add_argument("--socket", help=socket_help)
    down.add_argument("--detach", action="store_true", help="Stop even while sessions are live")
    down.add_argument("--timeout", type=float, default=60.0, help="Seconds to wait for the drain and exit")
    status = commands.add_parser("status", help="Report whether the server is running and what it holds")
    status.add_argument("--socket", help=socket_help)
    return parser


def _socket_path(args):
    if args.socket:
        path = check_socket_path(args.socket)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path
    return default_socket_path(args.config)


def _forwarded(args):
    """Global options the detached server inherits from the client that starts it."""
    forwarded = []
    if args.config:
        forwarded += ["--config", str(Path(args.config).expanduser().resolve())]
    for option in ("max_frame_bytes", "request_workers", "control_workers", "max_pending_requests"):
        forwarded += ["--" + option.replace("_", "-"), str(getattr(args, option))]
    return forwarded


def _serve_unix(args, path):
    lock = InstanceLock(companion_paths(path)["instance_lock"])
    if not lock.acquire():
        raise OrchestrationError("A server is already running for this socket.", "server_running", {"socket": str(path)})
    try:
        config, base_dir = load_config(args.config)
        with build(config, base_dir) as runtime:
            dispatcher = MCPDispatcher(runtime.application, args.request_workers, args.max_pending_requests,
                                       args.control_workers)
            try:
                # The instance lock is held, so any socket left at the path is stale.
                transport = UnixClientTransport(path, max_frame_bytes=args.max_frame_bytes, replace_stale=True)
                server = ProjectServer(runtime.application, transport, dispatcher, max_connections=args.max_connections,
                                       details={"socket": str(path), "config": str(Path(args.config).resolve()) if args.config else None})
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGTERM, lambda *_: server.terminate())
                _write({"serving": {"socket": str(path), "pid": os.getpid()}}, sys.stdout)
                server.serve_forever()
            finally:
                dispatcher.close()
    finally:
        lock.release()


def _server_command(args):
    path = _socket_path(args)
    if args.command == "connect":
        actor = args.connection_actor or args.actor
        client, _ = ensure_server(path, server_command(_forwarded(args), path), actor=actor,
                                  timeout=args.start_timeout, cwd=os.getcwd())
        try:
            finished = relay(client, sys.stdin.fileno(), sys.stdout.buffer)
        finally:
            client.close()
        if not finished:
            _write({"error": {"code": "server_closed", "message": "The server closed the connection."}}, sys.stderr)
            return 1
        return 0
    if args.command == "up":
        client, started = ensure_server(path, server_command(_forwarded(args), path), actor=args.actor,
                                        purpose="control", timeout=args.start_timeout, cwd=os.getcwd())
        try:
            reply = client.request({"command": "status"}, args.start_timeout)
        finally:
            client.close()
        _write({**reply, "started": started}, sys.stdout)
        return 0
    try:
        client = open_client(path, actor=args.actor, purpose="control")
    except (OSError, OrchestrationError):
        _write({"running": False, "socket": str(path)}, sys.stdout)
        return 3 if args.command == "status" else 0
    try:
        if args.command == "status":
            reply = client.request({"command": "status"}, 30)
        else:
            reply = client.request({"command": "down", "detach": args.detach}, args.timeout)
    except TimeoutError:
        _write({"error": {"code": "drain_timeout", "message":
            "The server is still draining in-flight requests; it stops when they finish."}}, sys.stderr)
        return 2
    finally:
        client.close()
    if "error" in reply:
        _write({"error": reply["error"]}, sys.stderr)
        return 2
    if args.command == "down":
        stopped = wait_stopped(path, args.timeout)
        reply = {**reply, "stopped": stopped}
    _write(reply, sys.stdout)
    return 0 if reply.get("stopped", True) else 2


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if min(args.max_frame_bytes, args.request_workers, args.control_workers, args.max_pending_requests) < 1 or not args.actor:
        parser.error("Frame/concurrency bounds must be positive and actor must be nonempty.")
    if args.command == "serve" and args.max_connections < 1:
        parser.error("--max-connections must be positive.")
    if args.command == "connect" and args.connection_actor == "":
        parser.error("--actor must be nonempty.")
    try:
        if args.command in SERVER_COMMANDS:
            return _server_command(args)
        if args.command == "serve" and args.transport == "unix":
            _serve_unix(args, _socket_path(args))
            return 0
        config, base_dir = load_config(args.config)
        with build(config, base_dir) as runtime:
            app = runtime.application
            if args.command == "describe":
                _write({"operations": describe(app)}, sys.stdout)
            elif args.command == "serve":
                serve(app, sys.stdin.buffer, sys.stdout, args.actor, args.max_frame_bytes,
                      args.request_workers, args.max_pending_requests, args.control_workers)
            elif args.command == "run":
                for request, error in frames(sys.stdin.buffer, args.max_frame_bytes):
                    try:
                        if error:
                            raise error
                        if not isinstance(request, dict) or not isinstance(request.get("action"), str):
                            raise OrchestrationError("Expected {action, arguments}.")
                        result = app.call(request["action"], request.get("arguments", {}), actor=args.actor)
                        _write({"result": result}, sys.stdout)
                    except OrchestrationError as exc:
                        _write({"error": failure(exc)}, sys.stdout)
                    except Exception:
                        _write({"error": {"code": "internal_error", "message":
                            "Operation failed; inspect retained operation state before retrying effects."}}, sys.stdout)
            else:
                if args.json_input is not None:
                    text = args.json_input
                elif args.input:
                    with Path(args.input).open(encoding="utf-8") as source:
                        text = source.read(args.max_frame_bytes + 1)
                else:
                    text = "{}" if sys.stdin.isatty() else sys.stdin.read(args.max_frame_bytes + 1)
                if len(text.encode("utf-8")) > args.max_frame_bytes:
                    raise OrchestrationError("Input exceeds configured limit.", "frame_too_large")
                _write(app.call(args.action, read_json(text or "{}"), actor=args.actor), sys.stdout)
        return 0
    except OrchestrationError as exc:
        _write({"error": failure(exc)}, sys.stderr)
        return 2
    except (OSError, UnicodeError):
        _write({"error": {"code": "io_error", "message": "Unable to read or write the requested resource."}}, sys.stderr)
        return 2
    except Exception:
        _write({"error": {"code": "internal_error", "message": "Operation failed; inspect retained operation state before retrying effects."}}, sys.stderr)
        return 1
