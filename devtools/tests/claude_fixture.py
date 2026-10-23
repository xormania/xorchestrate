#!/usr/bin/env python3
"""Claude hook/channel/transcript fixture. Never claims authenticated model use."""

import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from devtools.rpc import RPC


async def main():
    def option(name):
        return sys.argv[sys.argv.index(name) + 1]

    native_id = option("--session-id")
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    transcript = config / "projects/fixture" / (native_id + ".jsonl")
    transcript.parent.mkdir(parents=True, exist_ok=True)
    settings = json.loads(Path(option("--settings")).read_text())

    def hook(event, **fields):
        argv = shlex.split(settings["hooks"][event][0]["hooks"][0]["command"])
        payload = {"hook_event_name": event, "session_id": native_id, "transcript_path": str(transcript), **fields}
        subprocess.run(argv, input=json.dumps(payload), text=True, capture_output=True, check=True)

    hook("SessionStart", model="fixture-model")
    server = json.loads(Path(option("--mcp-config")).read_text())["mcpServers"]["agentscient_dev"]
    process = await asyncio.create_subprocess_exec(
        server["command"],
        *server["args"],
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    async def reject(method, params):
        raise ValueError("No client capability")

    tasks = set()
    active = set()

    async def tool(name, arguments):
        hook("PreToolUse", tool_name="mcp__agentscient_dev__" + name, tool_input=arguments)
        result = await rpc.request("tools/call", {"name": name, "arguments": arguments})
        assert not result.get("isError"), result
        hook("PostToolUse", tool_name="mcp__agentscient_dev__" + name, tool_response=result)
        return json.loads(result["content"][0]["text"])

    async def receive(params):
        text = params["content"]
        ident = int(re.search(r"Live message #(\d+)", text)[1])
        active.add(ident)
        with transcript.open("a") as stream:
            stream.write(json.dumps({"type": "user", "message": {"content": text}}) + "\n")
            stream.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "id": f"reply-{ident}",
                            "model": "fixture-model",
                            "content": "Received live direction",
                            "usage": {"input_tokens": 23},
                        },
                    }
                )
                + "\n"
            )
        current = await tool("onboard", {})
        readiness = current.get("readiness")
        if readiness and readiness["status"] == "starting":
            await tool("readiness_report", {"challenge": readiness["challenge"],
                                            "onboarding_revision": current["revision"]})
        diagnostic = current["context"].get("state") == "standby" or any(marker in text for marker in (
            "Readiness challenge.", "reporting for duty —", "unable to report ready —",
        ))
        if not diagnostic:
            await tool("work_report", {"text": f"Received message {ident}", "responding_to": f"message:{ident}"})
        gates = Path.cwd() / "fixture-turns"
        if not diagnostic and gates.is_dir():
            (gates / ("started-" + str(ident))).touch()
            async with asyncio.timeout(20):
                while not (gates / "release").exists():
                    await asyncio.sleep(0.01)
        active.discard(ident)
        if not active:
            hook("Stop")
        if not diagnostic and gates.is_dir():
            (gates / ("completed-" + str(ident))).touch()

    async def notification(method, params):
        if method == "notifications/claude/channel":
            task = asyncio.create_task(receive(params))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    rpc = RPC(process, reject, notification)
    await rpc.request(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude-fixture", "version": "1"}},
    )
    await rpc.notify("notifications/initialized")
    result = await rpc.request("tools/call", {"name": "onboard", "arguments": {}})
    onboard = json.loads(result["content"][0]["text"])
    assert all(skill["loaded"] for skill in onboard["skills"])
    Path(option("--debug-file")).write_text(
        '2026-09-28T00:00:00.000Z [DEBUG] MCP server "agentscient_dev": Channel notifications registered\n'
    )
    await rpc.dead.wait()


if __name__ == "__main__":
    asyncio.run(main())
