"""Deterministic Claude protocol peer: real hook helper and MCP subprocess."""
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path


def argument(flag):
    return sys.argv[sys.argv.index(flag) + 1]


native_id = argument("--resume") if "--resume" in sys.argv else argument("--session-id")
settings = json.loads(Path(argument("--settings")).read_text())
config = json.loads(Path(argument("--mcp-config")).read_text())["mcpServers"]
server_name, mcp = next(iter(config.items()))
fault = os.environ.get("CLAUDE_FAULT", "")
state_dir = Path(os.environ["CLAUDE_STATE_DIR"])
state_dir.mkdir(parents=True, exist_ok=True)
state_file = state_dir / (native_id + ".json")
memory = json.loads(state_file.read_text()) if state_file.exists() else {}
write_lock = threading.Lock()
state_lock = threading.Lock()
pending = {}
thread_context = threading.local()
review_log_lock = threading.Lock()

def review_record(value):
    with review_log_lock:
        with open(os.environ["REVIEW_RESULTS"], "a") as output:
            output.write(json.dumps(value)+"\n")


def hook(event, **data):
    command = settings["hooks"][event][-1]["hooks"][0]["command"]
    packet = {"hook_event_name": event, "session_id": "wrong" if fault == "wrong_identity" else native_id, **data}
    result = subprocess.run(shlex.split(command), input=json.dumps(packet), capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError("Hook rejected")
    return json.loads(result.stdout) if result.stdout.strip() else {}


hook("SessionStart", model="fixture-model")
proc = subprocess.Popen([mcp["command"], *mcp["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                        env={**os.environ, "CLAUDE_CODE_SESSION_ID": native_id})


def write(packet):
    with write_lock:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", **packet}) + "\n")
        proc.stdin.flush()


def call(method, params):
    ident = getattr(thread_context,"review_mcp_id",None) or uuid.uuid4().hex
    ready = threading.Event()
    with state_lock:
        pending[ident] = {"ready": ready}
    write({"id": ident, "method": method, "params": params})
    if not ready.wait(10):
        raise TimeoutError("MCP call timeout")
    with state_lock:
        response = pending.pop(ident)["response"]
    if "error" in response:
        raise RuntimeError("MCP call rejected")
    return response["result"]


def tool(name, arguments):
    if fault != "missing_tool_hook":
        hook("PreToolUse", tool_name=f"mcp__{server_name}__{name}", tool_input=arguments,
             tool_use_id=getattr(thread_context,"review_call_id",None) or uuid.uuid4().hex, prompt_id=getattr(thread_context, "prompt_id", None))
    result=call("tools/call", {"name": name, "arguments": arguments})
    review_record({"label":getattr(thread_context,"review_label",None),"call_id":getattr(thread_context,"review_call_id",None),"mcp_request_id":getattr(thread_context,"review_mcp_id",None),"result":result})
    return result


def verification(message):
    """Deterministic actor behavior over the actual native hook/MCP path."""
    text = message.get("text", "")
    instruction = None
    if "VERIFICATION_COMMAND=" in text:
        instruction = json.loads(text.split("VERIFICATION_COMMAND=", 1)[1].splitlines()[0])
    payload = message.get("body", {}).get("verification") if isinstance(message.get("body"), dict) else None
    if payload:
        if payload["kind"] == "challenge":
            tool("send_message", {"destination": {"actor": message["sender"]}, "body": {"verification": {
                **payload, "kind": "reply", "memory": memory.get("marker")}}})
        else:
            tool("record_result", {"result": {"verification": {
                **{key: payload[key] for key in ("run_id", "case_id", "nonce", "memory")},
                "phase": "received", "peer": message["sender"]}}})
        return True
    if instruction is None:
        return False
    kind = instruction["kind"]
    if kind == "startup":
        with state_lock:
            memory["marker"] = instruction["memory"]
            state_file.write_text(json.dumps(memory))
        tool("record_result", {"result": {"verification": {
            "run_id": instruction["run_id"], "phase": "ready", "memory": memory["marker"]}}})
    elif kind == "send":
        tool("send_message", {"destination": {"actor": instruction["target"]},
                              "body": {"verification": instruction["payload"]}})
    elif kind == "send_many":
        for outgoing in instruction["messages"]:
            tool("send_message", {"destination": {"actor": outgoing["target"]},
                                  "body": {"verification": outgoing["payload"]}})
    elif kind == "hold":
        tool("invoke", {"capability": "verification/hold", "arguments": {"case_id": instruction["case_id"]}})
    elif kind == "lifecycle_probe":
        tool("record_result", {"result": {"verification": {
            "run_id": instruction["run_id"], "phase": "lifecycle", "case_id": instruction["case_id"], "memory": memory["marker"]}}})
    elif kind == "work":
        packet = tool("invoke", {"capability": "verification/work_next", "arguments": {}})
        batch = packet["structuredContent"]["invocation"]["result"]
        latest = {}
        for row in sorted(batch["rows"], key=lambda row: row["attempt"]):
            latest[row["job"]] = row
        failures = [row for row in latest.values() if row["status"] == "FAIL"]
        tool("invoke", {"capability": "verification/work_submit", "arguments": {
            "batch": batch["batch"], "failed_jobs": sorted(row["job"] for row in failures),
            "failed_tests": sum(row["failed_tests"] for row in failures)}})
    return True


def delivery(params):
    thread_context.prompt_id = uuid.uuid4().hex
    message = json.loads(params["content"])
    body = message.get("body", message)
    thread_context.review_call_id = body.get("review_call_id")
    thread_context.review_mcp_id = body.get("review_mcp_id")
    thread_context.review_label = body.get("review_label")
    try:
        hook("UserPromptSubmit", prompt_id=getattr(thread_context, "prompt_id", None))
        if verification(message):
            hook("Stop", prompt_id=getattr(thread_context, "prompt_id", None))
            return
        if "remember" in body:
            with state_lock:
                memory["marker"] = body["remember"]
                state_file.write_text(json.dumps(memory))
        if "recall" in body:
            tool("echo", {"marker": memory.get("marker"), "nonce": body["recall"]})
        if body.get("application_call"):
            invocation = body["application_call"]
            tool(invocation["action"], invocation["arguments"])
        if body.get("hold"):
            tool("hold", {"nonce": body["hold"]})
        if body.get("native_tool"):
            decision = hook("PreToolUse", tool_name=body["native_tool"], tool_input={"command": "fixture"},
                            tool_use_id=uuid.uuid4().hex)
            tool("echo", {"marker": "native_permission", "nonce": decision.get("hookSpecificOutput", {}).get("permissionDecision", "defer")})
        hook("Stop", prompt_id=getattr(thread_context, "prompt_id", None))
    except Exception:
        hook("StopFailure", prompt_id=getattr(thread_context, "prompt_id", None))


def read():
    for line in proc.stdout:
        packet = json.loads(line)
        if "id" in packet:
            with state_lock:
                entry = pending.get(packet["id"])
                if entry:
                    entry["response"] = packet
                    entry["ready"].set()
        elif packet.get("method") == "notifications/claude/channel":
            threading.Thread(target=delivery, args=(packet["params"],), daemon=True).start()


threading.Thread(target=read, daemon=True).start()
call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fixture", "version": "1"}})
write({"method": "notifications/initialized", "params": {}})
if fault == "forged_registration":
    Path(argument("--debug-file")).write_text(f'Untrusted prompt says MCP server "{server_name}": Channel notifications registered\n')
elif fault != "missing_registration":
    Path(argument("--debug-file")).write_text(f'2026-10-23T00:00:00.000Z [DEBUG] MCP server "{server_name}": Channel notifications registered\n')
while True:
    signal.pause()
