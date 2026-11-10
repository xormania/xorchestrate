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
from importlib import metadata
from pathlib import Path

from . import __version__
from .adapters.local_socket import InstanceLock, check_socket_path
from .adapters.storage_migrations import DATA_VERSION
from .adapters.unix import UnixClientTransport
from .composition import SUPPORTED_CONFIG_VERSIONS, build, describe, load_config, read_json
from .mcp import MCPDispatcher, failure, frames, serve
from .model import OrchestrationError
from .server import (
    SERVER_LOCK_WAIT,
    ProjectServer,
    ServerFiles,
    default_socket_path,
    discover,
    ensure_server,
    open_client,
    relay,
    server_command,
    server_identity,
    wait_stopped,
)

SERVER_COMMANDS = {"connect", "up", "down", "status"}


def _write(value, stream):
    stream.write(json.dumps(value, allow_nan=False) + "\n")
    stream.flush()


def installed_commit(distribution=metadata.distribution):
    """The commit a VCS install resolved, from its PEP 610 ``direct_url.json``."""
    try:
        text = distribution("xorchestrate").read_text("direct_url.json")
        data = json.loads(text) if text else None
    except (metadata.PackageNotFoundError, OSError, UnicodeError, ValueError):
        return None
    vcs = data.get("vcs_info") if isinstance(data, dict) else None
    commit = vcs.get("commit_id") if isinstance(vcs, dict) else None
    return commit if isinstance(commit, str) and commit else None


def version_info(distribution=metadata.distribution):
    """Release identity and the format versions this release reads."""
    return {
        "version": __version__,
        "commit": installed_commit(distribution),
        "config_versions": sorted(SUPPORTED_CONFIG_VERSIONS),
        "data_version": DATA_VERSION,
    }


class _Version(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        _write(version_info(), sys.stdout)
        parser.exit()


def _parser():
    parser = argparse.ArgumentParser(description="Configurable agent orchestration")
    parser.add_argument("--version", action=_Version, help="Print release identity and supported versions as JSON")
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
    socket_help = "Server socket; by default the server chooses it and clients find it through its discovery record"
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


def _files(args):
    return ServerFiles.locate(server_identity(args.config))


def _explicit_socket(args):
    if not args.socket:
        return None
    path = check_socket_path(args.socket)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _forwarded(args):
    """Global options the detached server inherits from the client that starts it."""
    forwarded = []
    if args.config:
        forwarded += ["--config", str(Path(args.config).expanduser().resolve())]
    for option in ("max_frame_bytes", "request_workers", "control_workers", "max_pending_requests"):
        forwarded += ["--" + option.replace("_", "-"), str(getattr(args, option))]
    return forwarded


def _serve_unix(args):
    files = _files(args).ensure()
    lock = InstanceLock(files.lock)
    # Retry briefly: a client's lock probe may hold the lock for an instant.
    if not lock.acquire(wait=SERVER_LOCK_WAIT):
        record = files.read_record()
        raise OrchestrationError("A server is already running for this configuration.", "server_running",
                                 {"socket": record and record.get("socket")})
    try:
        path = _explicit_socket(args) or default_socket_path(files)
        config, base_dir = load_config(args.config)
        with build(config, base_dir) as runtime:
            dispatcher = MCPDispatcher(runtime.application, args.request_workers, args.max_pending_requests,
                                       args.control_workers)
            transport = None
            try:
                # The instance lock is held, so a socket left at the path with
                # nothing listening on it is stale.
                transport = UnixClientTransport(path, max_frame_bytes=args.max_frame_bytes, replace_stale=True)
                configured = str(Path(args.config).resolve()) if args.config else None
                server = ProjectServer(runtime.application, transport, dispatcher, max_connections=args.max_connections,
                                       details={"socket": str(path), "config": configured})
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGTERM, lambda *_: server.terminate())
                files.write_record(path, configured)
                _write({"serving": {"socket": str(path), "pid": os.getpid()}}, sys.stdout)
                server.serve_forever()
            finally:
                files.remove_record()
                if transport is not None:
                    transport.close()
                dispatcher.close()
    finally:
        lock.release()


def _server_command(args):
    files = _files(args)
    explicit = _explicit_socket(args)
    if args.command in {"connect", "up"}:
        purpose = "mcp" if args.command == "connect" else "control"
        actor = (args.connection_actor or args.actor) if args.command == "connect" else args.actor
        client, started = ensure_server(files, server_command(_forwarded(args), explicit), explicit_socket=explicit,
                                        actor=actor, purpose=purpose, timeout=args.start_timeout, cwd=os.getcwd())
        try:
            if args.command == "up":
                reply = client.request({"command": "status"}, args.start_timeout)
                _write({**reply, "started": started}, sys.stdout)
                return 0
            finished = relay(client, sys.stdin.fileno(), sys.stdout.buffer)
        finally:
            client.close()
        if not finished:
            _write({"error": {"code": "server_closed", "message": "The server closed the connection."}}, sys.stderr)
            return 1
        return 0
    target = discover(files, explicit)
    try:
        if target is None:
            raise FileNotFoundError("no server record")
        client = open_client(target, actor=args.actor, purpose="control")
    except OSError:
        _write({"running": False, "socket": None if target is None else str(target)}, sys.stdout)
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
        stopped = wait_stopped(files, args.timeout)
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
            _serve_unix(args)
            return 0
        config, base_dir = load_config(args.config)
        with build(config, base_dir) as runtime:
            app = runtime.application
            if args.command == "describe":
                _write({"operations": describe(app), "warnings": list(runtime.warnings)}, sys.stdout)
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
