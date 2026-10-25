"""Deterministic peers for real process acceptance, never a model substitute."""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

SCHEMA = {"type": "object", "properties": {"nonce": {"type": "string"}}, "required": ["nonce"], "additionalProperties": False}


def emit(packet):
    print(json.dumps(packet, allow_nan=False), flush=True)


def effect(marker, name, arguments):
    with marker.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"name": name, "nonce": arguments["nonce"]}) + "\n")
    return {"nonce": arguments["nonce"], "peer": name}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("cli", "mcp", "bridge", "codex"))
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--text-only", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "cli":
        request = json.load(sys.stdin)
        emit(effect(args.marker, "cli", request["arguments"]))
        return 0
    native_id = "acceptance-" + uuid4().hex
    events = []
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        method, params = request["method"], request.get("params", {})
        result = {}
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "acceptance-peer", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": name, "description": "Local acceptance effect", "inputSchema": SCHEMA}
                                for name in ("echo", "uncertain")]}
        elif method == "tools/call":
            name = params["name"]
            value = effect(args.marker, name, params["arguments"])
            if name == "uncertain":
                return 0  # Effect happened, response did not: never safe to replay.
            result = {"content": [{"type": "text", "text": json.dumps(value)}], "structuredContent": value}
        elif method == "session/start":
            result = {"session_id": native_id}
        elif method == "session/send":
            events.append({"kind": "message", "message": params["message"]})
            result = {"accepted": True}
        elif method == "session/poll":
            result = {"events": events}
            events = []
        elif method == "session/stop":
            emit({"jsonrpc": "2.0", "id": request["id"], "result": {"stopped": True}})
            return 0
        elif method in ("thread/start", "thread/resume"):
            result = {"thread": {"id": native_id}}
        elif method == "turn/start":
            if args.text_only:
                emit({"method": "item/agentMessage/delta", "params": {"threadId": native_id, "delta": "Task completed."}})
                emit({"id": request["id"], "result": {"turn": {"id": "acceptance-turn", "status": "completed"}}})
                continue
            instruction = json.loads(params["input"][0]["text"].split("\n", 1)[1])
            emit({"id": "native-callback", "method": "item/tool/call", "params": {
                "threadId": native_id, "callId": "receipt-" + instruction["arguments"]["result"]["nonce"],
                "tool": instruction["tool"], "arguments": instruction["arguments"],
            }})
            response = json.loads(sys.stdin.readline())
            if not response.get("result", {}).get("success"):
                return 2
            result = {"turn": {"id": "acceptance-turn", "status": "completed"}}
        else:
            emit({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unknown fixture method"}})
            continue
        emit({**({"jsonrpc": "2.0"} if args.mode != "codex" else {}), "id": request["id"], "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
