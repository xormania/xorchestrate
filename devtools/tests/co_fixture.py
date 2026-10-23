#!/usr/bin/env python3
"""Native TUI fixture: validates operator controls and exercises real CO MCP."""

import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.122.0")
    raise SystemExit(0)
if args[:1] == ["mcp"]:
    from grok_mcp_fixture import main

    raise SystemExit(main(args))
exchange = Path(os.environ["AGENTSCIENT_CO_FIXTURE"])
expected = json.loads(exchange.read_text())
assert Path.cwd() == Path(expected["workspace"])
if "--mcp-config" in args:
    harness = "claude"
    servers = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text())["mcpServers"]
    assert not {"--tools", "--strict-mcp-config", "--disable-slash-commands", "--setting-sources"} & set(args)
    Path(args[args.index("--debug-file") + 1]).write_text(
        '2026-09-28T01:00:00.000Z [DEBUG] MCP server "agentscient_dev": Channel notifications registered\n'
    )
elif "--rules" in args:
    harness = "grok"
    assert Path(os.environ["GROK_HOME"]) == Path(os.environ["HOME"]) / ".grok"
    generation = os.environ["AGENTSCIENT_CO_GENERATION"]
    native_home = Path(os.environ["GROK_HOME"])
    configured = tomllib.loads((native_home / "config.toml").read_text())["mcp_servers"]
    name = "agentscient_dev_" + generation
    servers = {name: configured[name]}
    hooks = json.loads((native_home / "hooks" / ("agentscient-co-" + generation + ".json")).read_text())
    assert "PreToolUse" not in hooks["hooks"]
    assert not (Path.cwd() / ".grok/plugins").exists()
    assert "--no-leader" in args
    assert not {"--tools", "--no-subagents", "--no-memory", "--disable-web-search"} & set(args)
else:
    harness = "codex"
    options = {}
    for index, arg in enumerate(args):
        if arg == "-c":
            value = tomllib.loads(args[index + 1])
            for key, item in value.items():
                if isinstance(item, dict):
                    options.setdefault(key, {}).update(item)
                else:
                    options[key] = item
    servers = options["mcp_servers"]
    assert not {"sandbox_mode", "approval_policy", "features", "developer_instructions"} & options.keys()
    assert Path(os.environ["CODEX_HOME"]) == Path(os.environ["HOME"]) / ".codex"
    assert options["model"] == "selected-model" and options["model_reasoning_effort"] == "high"

assert len(servers) == 1
server = next(iter(servers.values()))
assert "--generation" in server["args"] and "--operator" not in server["args"]
process = subprocess.Popen(
    [server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
)
ident = 0
notifications = []


def call(method, params=None):
    global ident
    ident += 1
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}) + "\n")
    process.stdin.flush()
    while True:
        packet = json.loads(process.stdout.readline())
        if packet.get("id") == ident:
            assert "error" not in packet, packet
            return packet["result"]
        if "method" in packet:
            notifications.append(packet)


def tool(name, arguments):
    result = call("tools/call", {"name": name, "arguments": arguments})
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


try:
    call("initialize", {"clientInfo": {"name": harness + "-tui-fixture", "version": "1"}})
    process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    process.stdin.flush()
    onboard = tool("onboard", {})
    assert onboard["role"] == expected["role"]
    assert onboard["package"]["behavior"] == "co"
    assert all(skill["loaded"] for skill in onboard["skills"])
    context = tool("work_context", {})
    assert expected["predecessor"] in json.dumps(context)
    tool("direct", expected["goal"])
    exchange.with_suffix(".directed").touch()
    deadline = time.monotonic() + 10
    while True:
        context = tool("work_context", {})
        report = next((row for row in context["incoming"] if expected["incoming"] in row["text"]), None)
        pushed = any(row["method"] == "notifications/claude/channel" and
                     expected["incoming"] in row["params"]["content"] for row in notifications)
        if report and (harness != "claude" or pushed):
            break
        assert time.monotonic() < deadline, "Current XO report did not reach the CO through its configured transport"
        time.sleep(0.02)
    tool("work_report", {"text": "CO recovered using " + harness, "kind": "progress",
                         "responding_to": report["reference"]})
    assert (harness == "claude") == pushed
    # Separate PTY writes deliberately split a credential before one complete line.
    secret = os.environ["TEST_API_KEY"]
    os.write(1, ("Live CO response " + harness + " " + secret[:7]).encode())
    os.write(1, (secret[7:] + "\n").encode())
finally:
    process.stdin.close()
    process.wait(timeout=5)
