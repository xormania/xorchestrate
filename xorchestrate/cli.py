"""Command line input adapter; JSON is the common boundary with MCP.

People use these commands directly. Agent harnesses use ``connect``, which
relays MCP between the harness's stdio and the project's long-running server.

Every command that runs the application (``describe``, ``call``, ``run``,
``serve``, ``connect``, ``up``, ``down``, ``status``) needs a set-up project:
it is found from the working directory, or named with ``--project PATH``, and
refused otherwise with a message naming ``xorchestrate init`` (D15). The
operator commands (``setup``, ``companion``, ``init``, ``approve``,
``doctor``) prepare one.
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
from .adapters.local_socket import InstanceLock, check_socket_directory, check_socket_path
from .adapters.storage_migrations import DATA_VERSION
from .adapters.unix import UnixClientTransport
from .composition import SUPPORTED_CONFIG_VERSIONS, build, describe, read_json
from .instance import operations
from .instance.doctor import doctor
from .instance.handoff import handoff
from .instance.home import Home
from .instance.instance import locate_project, prepare, require_companion, resolve_instance
from .instance.project import differing_files
from .instance.render import merged
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
PROJECT_COMMANDS = SERVER_COMMANDS | {"describe", "call", "run", "serve"}


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
    parser.add_argument("--project", help="A set-up project's repository or worktree (default: the working directory)")
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
    status = commands.add_parser("status", help="Report whether the server is running, what it holds and the overrides")
    status.add_argument("--socket", help=socket_help)
    setup = commands.add_parser("setup", help="First run: create or link the companion, then run doctor")
    setup.add_argument("path", nargs="?", help="A new or empty directory, or an existing companion clone")
    companion = commands.add_parser("companion", help="Create or register the companion repository")
    companion_commands = companion.add_subparsers(dest="companion_command", required=True)
    companion_commands.add_parser("init", help="Create the companion layout and register it").add_argument("path")
    companion_commands.add_parser("use", help="Register an existing companion clone").add_argument("path")
    init = commands.add_parser("init", help="Set up a project on this machine")
    init.add_argument("path", nargs="?", help="The project's repository (default: the working directory)")
    init.add_argument("--name", help="Project name for a new starter .xorchestrate/")
    init.add_argument("--approve", action="store_true", help="Also approve the tracked configuration (not the first time)")
    approve = commands.add_parser("approve", help="Approve the project's tracked .xorchestrate/ configuration")
    approve.add_argument("path", nargs="?", help="The project's repository (default: the working directory)")
    commands.add_parser("doctor", help="Check this machine and the current project")
    return parser


def _explicit_socket(args):
    if not args.socket:
        return None
    path = check_socket_path(args.socket)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Clients and the server alike refuse a directory another user could tamper with.
    return check_socket_directory(path)


def _forwarded(args, project_root):
    """Global options the detached server inherits from the client that starts it."""
    forwarded = ["--project", str(project_root)]
    for option in ("max_frame_bytes", "request_workers", "control_workers", "max_pending_requests"):
        forwarded += ["--" + option.replace("_", "-"), str(getattr(args, option))]
    return forwarded


def worktree_policy(tracked):
    """Refuse an MCP client whose worktree's tracked configuration is not the running one (D19)."""
    running = tracked.config_hash

    def policy(peer):
        attributes = peer.attributes if isinstance(peer.attributes, dict) else {}
        worktree = attributes.get("worktree")
        if not isinstance(worktree, dict) or not isinstance(worktree.get("root"), str):
            return "worktree_required", "Connect through `xorchestrate connect`, which reports its worktree."
        if worktree.get("config_hash") == running:
            return None
        files = worktree.get("config_files")
        changed = differing_files(tracked.digests, files if isinstance(files, dict) else {})
        return ("configuration_mismatch",
                f"The tracked configuration in {worktree['root']} differs from the running one "
                f"({', '.join(changed) or 'unknown files'}); approve it, then restart the server with "
                "`xorchestrate down` so the next connect runs it.",
                {"files": changed, "running": running, "worktree": worktree.get("config_hash")})

    return policy


def _serve_unix(args, prepared):
    files = ServerFiles.locate(server_identity(prepared.instance.instance_id)).ensure()
    lock = InstanceLock(files.lock)
    # Retry briefly: a client's lock probe may hold the lock for an instant.
    if not lock.acquire(wait=SERVER_LOCK_WAIT):
        record = files.read_record()
        raise OrchestrationError("A server is already running for this project.", "server_running",
                                 {"socket": record and record.get("socket")})
    try:
        path = _explicit_socket(args) or default_socket_path(files)
        with build(prepared.config, project=prepared.context) as runtime:
            dispatcher = MCPDispatcher(runtime.application, args.request_workers, args.max_pending_requests,
                                       args.control_workers)
            transport = None
            try:
                # The instance lock is held, so a socket left at the path with
                # nothing listening on it is stale.
                transport = UnixClientTransport(path, max_frame_bytes=args.max_frame_bytes, replace_stale=True)
                rendered = str(prepared.rendered.path)
                details = {"socket": str(path), "config": rendered, "project": prepared.tracked.name,
                           "instance_id": prepared.instance.instance_id, "config_hash": prepared.tracked.config_hash,
                           "started_worktree": prepared.tracked.worktree.root.as_posix()}
                server = ProjectServer(runtime.application, transport, dispatcher, max_connections=args.max_connections,
                                       details=details, peer_policy=worktree_policy(prepared.tracked))
                if threading.current_thread() is threading.main_thread():
                    signal.signal(signal.SIGTERM, lambda *_: server.terminate())
                files.write_record(path, rendered)
                _write({"serving": {"socket": str(path), "pid": os.getpid()}}, sys.stdout)
                server.serve_forever()
            finally:
                files.remove_record()
                if transport is not None:
                    transport.close()
                dispatcher.close()
    finally:
        lock.release()


def _overrides(tracked):
    try:
        return merged(tracked, require_companion(Home.locate()))[1]
    except OrchestrationError:
        return None


def _server_command(args, tracked):
    instance = resolve_instance(Home.locate(), tracked)
    files = ServerFiles.locate(server_identity(instance.instance_id))
    explicit = _explicit_socket(args)
    if args.command in {"connect", "up"}:
        purpose = "mcp" if args.command == "connect" else "control"
        actor = (args.connection_actor or args.actor) if args.command == "connect" else args.actor
        attributes = {"worktree": {**tracked.identity(), "config_files": tracked.digests}} if purpose == "mcp" else None
        root = tracked.worktree.root
        client, started = ensure_server(files, server_command(_forwarded(args, root), explicit),
                                        explicit_socket=explicit, actor=actor, purpose=purpose,
                                        attributes=attributes, timeout=args.start_timeout, cwd=str(root))
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
        reply = {"running": False, "socket": None if target is None else str(target)}
        if args.command == "status":
            reply["overrides"] = _overrides(tracked)
        _write(reply, sys.stdout)
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
    else:
        reply = {**reply, "overrides": _overrides(tracked)}
    _write(reply, sys.stdout)
    return 0 if reply.get("stopped", True) else 2


def _operator_command(args):
    if args.command == "setup":
        report = operations.setup(args.path)
    elif args.command == "companion":
        command = operations.companion_init if args.companion_command == "init" else operations.companion_use
        report = command(args.path)
    elif args.command == "init":
        report = operations.init(args.path or args.project, name=args.name, approve=args.approve)
    elif args.command == "approve":
        report = operations.approve(args.path or args.project)
    else:
        report = doctor(args.project)
        _write(report, sys.stdout)
        return 0 if report["ok"] else 1
    _write(report, sys.stdout)
    return 0


def _application_command(args, prepared):
    with build(prepared.config, project=prepared.context) as runtime:
        app = runtime.application
        if args.command == "describe":
            _write({"operations": describe(app), "warnings": list(runtime.warnings)}, sys.stdout)
        elif args.command == "serve":
            serve(app, sys.stdin.buffer, sys.stdout, args.actor, args.max_frame_bytes,
                  args.request_workers, args.max_pending_requests, args.control_workers, prepared.worktree)
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


def main(argv=None):
    parser = _parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if min(args.max_frame_bytes, args.request_workers, args.control_workers, args.max_pending_requests) < 1 or not args.actor:
        parser.error("Frame/concurrency bounds must be positive and actor must be nonempty.")
    if args.command == "serve" and args.max_connections < 1:
        parser.error("--max-connections must be positive.")
    if args.command == "connect" and args.connection_actor == "":
        parser.error("--actor must be nonempty.")
    try:
        if args.command not in PROJECT_COMMANDS:
            return _operator_command(args)
        # No project, no run (D15): the refusal names `xorchestrate init`.
        tracked = locate_project(args.project)
        # The pinned release runs the command; this returns only when it is this release.
        handoff(Home.locate(), tracked.release, argv)
        if args.command in SERVER_COMMANDS:
            return _server_command(args, tracked)
        prepared = prepare(tracked.worktree.root)
        if args.command == "serve" and args.transport == "unix":
            _serve_unix(args, prepared)
            return 0
        return _application_command(args, prepared)
    except OrchestrationError as exc:
        _write({"error": failure(exc)}, sys.stderr)
        return 2
    except (OSError, UnicodeError):
        _write({"error": {"code": "io_error", "message": "Unable to read or write the requested resource."}}, sys.stderr)
        return 2
    except Exception:
        _write({"error": {"code": "internal_error", "message": "Operation failed; inspect retained operation state before retrying effects."}}, sys.stderr)
        return 1
