"""Command line input adapter; JSON is the common boundary with MCP."""
import argparse
import json
import sys
from pathlib import Path

from .composition import build, describe, load_config, read_json
from .mcp import failure, frames, serve
from .model import OrchestrationError


def _write(value, stream):
    stream.write(json.dumps(value, allow_nan=False) + "\n")
    stream.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Configurable agent orchestration")
    parser.add_argument("--config", help="Explicit trusted JSON configuration file")
    parser.add_argument("--actor", default="owner", help="Principal for this local process")
    parser.add_argument("--max-frame-bytes", type=int, default=1_048_576)
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
    if args.max_frame_bytes < 1 or not args.actor:
        parser.error("Frame size must be positive and actor must be nonempty.")
    try:
        config, base_dir = load_config(args.config)
        with build(config, base_dir) as runtime:
            app = runtime.application
            if args.command == "describe":
                _write({"operations": describe(app)}, sys.stdout)
            elif args.command == "serve":
                serve(app, sys.stdin.buffer, sys.stdout, args.actor, args.max_frame_bytes)
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
