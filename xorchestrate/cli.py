"""Command line input adapter; JSON is the common boundary with MCP.

People use these commands directly. Agent harnesses use ``connect``, which
relays MCP between the harness's stdio and the project's long-running server.

Every command that runs the application (``describe``, ``call``, ``run``,
``serve``, ``connect``, ``up``, ``down``, ``status``, ``telemetry``) needs a
set-up project: it is found from the working directory, or named with
``--project PATH``, and refused otherwise with a message naming
``xorchestrate init`` (D15). The operator commands (``setup``, ``companion``,
``init``, ``approve``, ``doctor``, ``upgrade``, ``migrate-config``) prepare,
upgrade or migrate one and run with the installed release.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from . import __version__, installation
from .adapters.local_socket import InstanceLock, check_socket_directory, check_socket_path
from .adapters.storage_migrations import DATA_VERSION
from .adapters.unix import UnixClientTransport
from .composition import RELEASE, SUPPORTED_CONFIG_VERSIONS, build, describe, read_json
from .instance import operations, snapshots
from .instance.doctor import doctor
from .instance.handoff import HANDOFF_VARIABLE, handoff
from .instance.home import Home
from .instance.instance import (
    approval_refusal,
    installed_release,
    locate_project,
    prepare,
    require_companion,
    resolve_instance,
    upgrade_refusal,
)
from .instance.migrate_config import migrate_config
from .instance.project import differing_files
from .instance.render import merged
from .instance.upgrade import upgrade, upgrade_step
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
PROJECT_COMMANDS = SERVER_COMMANDS | {"describe", "call", "run", "serve", "telemetry"}
# Only talk to the running server's socket, so they run in this launcher, never through a handoff:
# whichever release the server runs, and while an upgrade to a newly pinned release is pending.
LAUNCHER_COMMANDS = {"down", "status"}


def _write(value, stream):
    stream.write(json.dumps(value, allow_nan=False) + "\n")
    stream.flush()


def installed_commit(distribution=None):
    """The commit a VCS install resolved, from its PEP 610 ``direct_url.json``."""
    return installation.classify_source(installation.direct_url(distribution))[2]


def version_info(distribution=None, prefix=None):
    """Release identity, install source and the format versions this release reads."""
    installed = installation.installation(distribution, prefix)
    return {
        "version": __version__,
        "commit": installed.commit,
        "install_source": installed.reported_source,
        "upgrade_command": installation.upgrade_command(installed),
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
    parser.add_argument("--skip-snapshots", action="store_true",
                        help="Do not take the telemetry snapshots the configuration selects for this render or "
                             "upgrade (recorded as skipped)")
    parser.add_argument("--max-frame-bytes", type=int, default=1_048_576)
    parser.add_argument("--request-workers", type=int, default=8, help="Concurrent MCP effect workers")
    parser.add_argument("--control-workers", type=int, default=2, help="Reserved MCP observation/control workers")
    parser.add_argument("--max-pending-requests", type=int, default=64, help="MCP admission bound per worker pool")
    # upgrade-step is internal: it is left out of the listed commands.
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
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
    project_help = ("Use it for one project only (its repository; default: the working directory), recorded in that "
                    "project's instance; without it, it becomes this machine's default")
    for name, text in (("init", "Create a companion at PATH and use it"), ("use", "Use an existing companion")):
        command = companion_commands.add_parser(name, help=text)
        command.add_argument("path")
        command.add_argument("--project", dest="companion_project", nargs="?", const=".", help=project_help)
    init = commands.add_parser("init", help="Set up a project on this machine")
    init.add_argument("path", nargs="?", help="The project's repository (default: the working directory)")
    init.add_argument("--name", help="Project name for a new starter .xorchestrate/")
    init.add_argument("--approve", action="store_true", help="Also approve the tracked configuration (not the first time)")
    init.add_argument("--relocate", action="store_true",
                      help="Re-point this project's instance to this repository after the old one was moved")
    approve = commands.add_parser("approve", help="Approve the project's tracked .xorchestrate/ configuration")
    approve.add_argument("path", nargs="?", help="The project's repository (default: the working directory)")
    commands.add_parser("doctor", help="Check this machine and the current project")
    telemetry = commands.add_parser("telemetry", help="Snapshot the project's telemetry or archive a snapshot")
    telemetry_commands = telemetry.add_subparsers(dest="telemetry_command", required=True)
    snapshot = telemetry_commands.add_parser("snapshot", help="Take a manual snapshot bundle of the telemetry corpus")
    snapshot.add_argument("--destination", help="Bundle name inside the export root (default: manual-<UTC time>)")
    snapshot.add_argument("--archive", action="store_true", help="Also archive it into the companion")
    archive = telemetry_commands.add_parser("archive", help="Copy a complete, verified bundle into the companion")
    archive.add_argument("bundle", help="Bundle name (or path) inside the telemetry export root")
    upgrade_command = commands.add_parser("upgrade", help="Upgrade this machine's instance to the pinned release")
    upgrade_command.add_argument("--to", help="Expected target release; must equal the approved pin unless --plan-only")
    upgrade_command.add_argument("--plan-only", action="store_true",
                                 help="Build the target toolchain and print its dry-run plan; change nothing")
    upgrade_command.add_argument("--detach", action="store_true", help="Stop the server even while sessions are live")
    upgrade_command.add_argument("--timeout", type=float, default=60.0, help="Seconds to wait for the server to stop")
    migrate = commands.add_parser("migrate-config", help="Rewrite a configuration to the current format version")
    migrate.add_argument("file", nargs="?", help="Configuration file (default: the project's tracked config.json)")
    migrate.add_argument("--write", action="store_true", help="Rewrite the file (default: show the diff only)")
    step = commands.add_parser("upgrade-step")
    step.add_argument("step", choices=("check", "migrate"))
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
        home = Home.locate()
        return merged(tracked, require_companion(home, resolve_instance(home, tracked)))[1]
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
        # The server starts in the instance directory, never in the worktree, so
        # nothing in the repository can shadow the package (see server_command).
        client, started = ensure_server(files, server_command(_forwarded(args, root), explicit),
                                        explicit_socket=explicit, actor=actor, purpose=purpose,
                                        attributes=attributes, timeout=args.start_timeout, cwd=str(instance.data_dir))
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


class ServerControl:
    """How ``upgrade`` stops, holds and restarts a project's server."""

    def __init__(self, instance_id, project_root, timeout):
        self.files = ServerFiles.locate(server_identity(instance_id))
        self.project_root, self.timeout = project_root, timeout

    def stop(self, detach):
        target = discover(self.files)
        try:
            if target is None:
                raise FileNotFoundError("no server record")
            client = open_client(target, actor="owner", purpose="control")
        except OSError:
            return {"was_running": False}
        try:
            reply = client.request({"command": "down", "detach": detach}, self.timeout)
        except TimeoutError as exc:
            raise OrchestrationError("The server is still draining in-flight requests.", "drain_timeout") from exc
        finally:
            client.close()
        if "error" in reply:
            error = reply["error"]
            raise OrchestrationError(error.get("message", "The server refused to stop."), error.get("code", "refused"),
                                     error.get("details") or {})
        if not wait_stopped(self.files, self.timeout):
            raise OrchestrationError("The server did not stop in time.", "drain_timeout")
        return {"was_running": True, "detached_sessions": reply.get("detached_sessions", [])}

    @contextmanager
    def hold(self):
        lock = InstanceLock(self.files.ensure().lock)
        if not lock.acquire(wait=SERVER_LOCK_WAIT):
            raise OrchestrationError("A server for this project started during the upgrade.", "server_running")
        try:
            yield
        finally:
            lock.release()

    def start(self):
        """``up`` through the launcher, which now hands off to the upgraded release."""
        result = subprocess.run([sys.executable, "-P", "-m", "xorchestrate", "--project", str(self.project_root), "up"],
                                capture_output=True, text=True, timeout=120, check=False)
        lines = result.stdout.strip().splitlines()
        try:
            return json.loads(lines[-1]) if result.returncode == 0 and lines else {"error": result.stderr[-2000:]}
        except ValueError:
            return {"error": result.stdout[-2000:]}


def _upgrade_command(args):
    tracked = locate_project(args.project)
    instance = resolve_instance(Home.locate(), tracked)
    control = ServerControl(instance.instance_id, tracked.worktree.root, args.timeout)
    return upgrade(args.project, to=args.to, plan_only=args.plan_only, detach=args.detach, server=control,
                   skip_snapshots=args.skip_snapshots)


def _operator_command(args):
    if args.command == "upgrade":
        report = _upgrade_command(args)
    elif args.command == "upgrade-step":
        report = upgrade_step(args.step, args.project)
    elif args.command == "migrate-config":
        report = migrate_config(args.file, write=args.write, path=args.project)
    elif args.command == "setup":
        report = operations.setup(args.path)
    elif args.command == "companion":
        command = operations.companion_init if args.companion_command == "init" else operations.companion_use
        report = command(args.path, project=args.companion_project)
    elif args.command == "init":
        report = operations.init(args.path or args.project, name=args.name, approve=args.approve,
                                 relocate_instance=args.relocate)
    elif args.command == "approve":
        report = operations.approve(args.path or args.project)
    else:
        report = doctor(args.project)
        _write(report, sys.stdout)
        return 0 if report["ok"] else 1
    _write(report, sys.stdout)
    return 0


def _telemetry_command(args, prepared):
    common = {"companion": prepared.companion, "instance": prepared.instance, "tracked": prepared.tracked}
    if args.telemetry_command == "snapshot":
        report = snapshots.snapshot(prepared.config, prepared.context, [snapshots.MANUAL_SNAPSHOT],
                                    destination=args.destination, archive=True if args.archive else None, **common)
    else:
        report = snapshots.archive_bundle(args.bundle, config=prepared.config, context=prepared.context, **common)
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
        home = Home.locate()
        # The pinned release is read from project.json, so it is trusted only
        # once the project is set up here and that file is approved.
        instance = resolve_instance(home, tracked)
        if args.command in LAUNCHER_COMMANDS:
            os.environ.pop(HANDOFF_VARIABLE, None)
            return _server_command(args, tracked)
        release = tracked.release
        if release != RELEASE or installed_release(instance, tracked) != release:
            for refusal in (approval_refusal(require_companion(home, instance), tracked),
                            upgrade_refusal(instance, tracked)):
                if refusal is not None:
                    raise refusal
        # The pinned release runs the command; this returns only when it is this release.
        handoff(home, release, argv)
        os.environ.pop(HANDOFF_VARIABLE, None)
        if args.command in SERVER_COMMANDS:
            return _server_command(args, tracked)
        prepared = prepare(tracked.worktree.root, skip_snapshots=args.skip_snapshots)
        if args.command == "serve" and args.transport == "unix":
            _serve_unix(args, prepared)
            return 0
        if args.command == "telemetry":
            return _telemetry_command(args, prepared)
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
