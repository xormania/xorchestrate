"""Deterministic subprocess protocol peer; no model or vendor service is used."""

import json
import os
import sys
import time
import uuid
from pathlib import Path

from controlled_gates import wait_gate

mode = sys.argv[1]
protocol = "codex" if mode.startswith("codex") else "acp" if mode.startswith("acp") else "jsonrpc"
native_id = "fixture-" + uuid.uuid4().hex
start_params = {}
events = []
authentication = None
LEDGER = os.environ.get("XORCHESTRATE_FIXTURE_LEDGER")
"""Optional: a file the peer appends each delivered turn to, as an independent record of what it received."""
GATES = os.environ.get("XORCHESTRATE_FIXTURE_GATES")
"""Optional: a directory of controlled gates; a stage holds only while ``<stage>.armed`` exists there."""


def record_delivery(text):
    if LEDGER:
        with open(LEDGER, "a", encoding="utf-8") as ledger:
            ledger.write(json.dumps({"native_id": native_id, "text": text}) + "\n")
            ledger.flush()
            os.fsync(ledger.fileno())


def hold(stage):
    if GATES and (Path(GATES) / (stage + ".armed")).exists():
        wait_gate({stage + "_gate": str(Path(GATES) / (stage + ".gate")),
                   stage + "_entered": str(Path(GATES) / (stage + ".entered"))}, stage)


def write(**fields):
    if protocol != "codex":
        fields = {"jsonrpc": "2.0", **fields}
    print(json.dumps(fields), flush=True)


def tool(text):
    method = "item/tool/call" if protocol == "codex" else "session/request_permission"
    params = {"threadId" if protocol == "codex" else "sessionId": "alien" if text == "wrong_tool" else native_id,
              "tool": "echo", "arguments": {"value": "from-native"}, "callId": "call-1"}
    if text == "record":
        params.update(tool="record_result", arguments={"result": "from-native"}, callId="call-record")
    write(id="native-1", method=method, params=params)
    answer = json.loads(sys.stdin.readline())
    return answer


for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    params = request.get("params", {})
    result = {}
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
    elif method == "authenticate":
        authentication = params
    elif method in ("session/start", "custom/start", "thread/start", "thread/resume", "session/new", "session/load"):
        start_params = params
        if mode == "bad_id":
            result = {"session_id": None}
        elif mode == "flood":
            print("x" * 100000, flush=True)
            continue
        else:
            if method in ("thread/resume", "session/load"):
                native_id = params.get("threadId", params.get("sessionId"))
                hold("resume")
                if mode == "codex_mismatch":
                    native_id = "not-the-resumed-thread"
            result = {"thread": {"id": native_id}} if protocol == "codex" else {"sessionId": native_id} if protocol == "acp" else {"session_id": native_id}
    elif method in ("session/send", "turn/start", "session/prompt"):
        message = params.get("message", {})
        text = message.get("text", "") if protocol == "jsonrpc" else params.get("input", params.get("prompt"))[0]["text"]
        record_delivery(text)
        if text == "hang":
            time.sleep(5)
        elif text == "exit":
            sys.exit(0)
        result = {"accepted": True, "native_id": native_id, "start": start_params, "message": message,
                  "environment": os.environ.get("SESSION_TEST"), "authentication": authentication}
        if text in ("tool", "wrong_tool", "record"):
            result["tool_response"] = tool(text)
        if protocol == "codex":
            write(method="item/agentMessage/delta", params={"threadId": "alien" if text == "wrong_identity" else native_id, "delta": "echo:" + text})
            result["turn"] = {"id": "turn-1", "status": "inProgress"}
        elif protocol == "acp":
            write(method="session/update", params={"sessionId": native_id, "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "echo:" + text}}})
            result["stopReason"] = "end_turn"
        else:
            events.append({"kind": "message", "message": message})
    elif method == "session/poll":
        result = {"events": events}
        events = []
    elif method == "session/stop":
        write(id=request["id"], result={"stopped": True})
        break
    else:
        write(id=request["id"], error={"code": -32601, "message": "Unsupported fixture request"})
        continue
    write(id=request["id"], result=result)
