"""Command line input adapter; JSON is the common boundary with MCP."""
import argparse
import json
import sys
from importlib import metadata
from pathlib import Path

from . import __version__
from .adapters.storage_migrations import DATA_VERSION
from .composition import SUPPORTED_CONFIG_VERSIONS, build, describe, load_config, read_json
from .mcp import failure, frames, serve
from .model import OrchestrationError


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


def main(argv=None):
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
    commands.add_parser("serve", help="Serve MCP over stdin/stdout until EOF")
    commands.add_parser("run", help="Process JSON lines {action, arguments} until EOF; retain live sessions")
    args = parser.parse_args(argv)
    if min(args.max_frame_bytes, args.request_workers, args.control_workers, args.max_pending_requests) < 1 or not args.actor:
        parser.error("Frame/concurrency bounds must be positive and actor must be nonempty.")
    try:
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
