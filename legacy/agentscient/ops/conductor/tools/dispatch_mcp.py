#!/usr/bin/env python3
"""The Dispatcher's four verbs as MCP tools, over stdio.

Contract: ops/conductor/sessions-lever.md, "Slice 3 — Dispatcher
integration", the verbs; the tool shape and the error result are
`coordination-mcp-lever.md`'s, reused from `coordination_mcp.py` and
never copied. The work is `dispatch_actions.py`'s: each tool calls one
of its `VERBS`, so the rows the tools write are the rows the CLI writes.

    dispatch_mcp.py check | serve | call TOOL [JSON]

`coord_launch` and `coord_collect` mutate and bind the caller from its
session, as `coordination_mcp.py` does; the `role` and `holder` checks
are the verbs' own. `coord_status` and `coord_wait` are reads and bind
nobody. The standalone `serve` needs no registration line in
`coordination_mcp.py`; `TOOLS` is exported for the one that joins them.
"""

import sys

sys.dont_write_bytecode = True

import json  # noqa: E402
import os  # noqa: E402

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import coordination
    import coordination_mcp as base
    import dispatch_actions
    import dispatch_intents as intents
    import mcp_transport as transport
    from instrument import Refusal, refuse, session
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py, coordination_mcp.py and dispatch_actions.py beside "
        "this tool; found absent; needed restore them beside ops/conductor/tools/dispatch_mcp.py",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

SERVER_NAME = "dispatch"
INSTRUCTIONS = (
    "The Dispatcher's launch facade: coord_launch and coord_collect change durable "
    "state and act only for the session holding the dispatcher role and machine; "
    "coord_status and coord_wait are reads. Every refusal carries expected, found "
    "and needed."
)
_TEXT = {"type": "string"}
_SECONDS = {"type": "integer", "minimum": 1}

TOOLS = (
    base.Tool("coord_launch", "launch", True,
              {"assignment": _TEXT, "job": _TEXT, "harness": _TEXT, "branch": _TEXT,
               "scope_ref": _TEXT, "input": _TEXT, "key": _TEXT},
              ("assignment", "job", "harness", "branch", "scope_ref", "input", "key"),
              "Launch one job for a held assignment: the intent is reserved, then the "
              "wrapper runs; the same key and ask answers the same intent."),
    base.Tool("coord_status", "status", False, {"intent": _TEXT}, ("intent",),
              "An intent's phase, record, job state and closed reading."),
    base.Tool("coord_wait", "wait", False,
              {"intent": _TEXT, "timeout": _SECONDS, "interval": _SECONDS}, ("intent",),
              "Block until the launcher reads the job closed, or the timeout; a job "
              "that did not close clean is a reading, not an error."),
    base.Tool("coord_collect", "collect", True, {"intent": _TEXT, "verify": _TEXT},
              ("intent", "verify"),
              "Collect a launched job with a named verification from "
              "verifications.json, and close the intent with the reading."),
)
BY_NAME = {tool.name: tool for tool in TOOLS}


def call(name, arguments) -> dict:
    tool = BY_NAME.get(name)
    if tool is None:
        refuse("arguments", "a tool this server carries", base.clip(name),
               f"choose one of {sorted(BY_NAME)}")
    base.check_named_authority(tool, arguments)
    checked = dict(base.check_arguments(tool, arguments))
    verb = dispatch_actions.VERBS[tool.verb]
    if "input" in checked:
        checked["inputs"] = [checked.pop("input")]
    if tool.mutates and not session(""):
        refuse("session", "a session id in this server's environment", "unset",
               "run under a harness that sets CLAUDE_CODE_SESSION_ID")
    conn = intents.writer() if tool.mutates else intents.reader()
    try:
        if not tool.mutates:
            return verb(conn, None, **checked)
        participant = coordination.bound_participant(conn)
        return verb(conn, participant, agent=participant["agent"], **checked)
    finally:
        if conn is not None:
            conn.close()


def _schemas() -> list:
    return [{
        "name": tool.name,
        "description": tool.summary,
        "inputSchema": {"type": "object", "properties": dict(tool.properties),
                        "required": list(tool.required), "additionalProperties": False},
        "annotations": {"readOnlyHint": not tool.mutates, "destructiveHint": False,
                        "idempotentHint": not tool.mutates},
    } for tool in TOOLS]


def _dispatch(request) -> dict | None:
    return transport.dispatch(request, _route, server={
        "serverInfo": {"name": SERVER_NAME, "version": "1"},
        "instructions": INSTRUCTIONS,
        "capabilities": {"tools": {"listChanged": False}},
    })


def _route(identifier, method, params):
    if method == "tools/list":
        if params:
            return base._error(identifier, -32602, "Invalid params")
        return base._result(identifier, {"tools": _schemas()})
    if method == "tools/call":
        if set(params) not in ({"name"}, {"name", "arguments"}) or not isinstance(
                params.get("name"), str):
            return base._error(identifier, -32602, "Invalid params")
        if "arguments" in params and not isinstance(params["arguments"], dict):
            return base._error(identifier, -32602, "Invalid params")
        try:
            payload = call(params["name"], params.get("arguments", {}))
        except Refusal as refusal:
            return base._refusal_result(identifier, refusal)
        except Exception as error:  # noqa: BLE001 - a server answers, always
            return base._error(identifier, -32603, f"Internal error: {type(error).__name__}")
        message = base._call_result(identifier, payload)
        if base.over_bound(message):
            message = base._call_result(identifier, base.unavailable(payload))
        return message
    return base._error(identifier, -32601, "Method not found")


def serve() -> int:
    return transport.serve(sys.stdin.buffer, _dispatch, base._send)


def main() -> int:
    rest = sys.argv[1:]
    try:
        if rest == ["check"]:
            print(base.canonical({
                "server": SERVER_NAME,
                "tools": [tool.name for tool in TOOLS],
                "mutations": [tool.name for tool in TOOLS if tool.mutates],
                "binding": "session" if session("") else "none",
            }))
            return 0
        if rest == ["serve"]:
            return serve()
        if rest[:1] == ["call"] and len(rest) in (2, 3):
            arguments = {}
            if len(rest) == 3:
                try:
                    arguments = json.loads(rest[2], parse_constant=transport._reject_constant)
                except (ValueError, UnicodeError, RecursionError):
                    refuse("arguments", "a JSON object of arguments", "unparseable",
                           "pass the declared arguments as JSON")
            payload = call(rest[1], arguments)
            print(base.canonical(payload))
            return 0
        refuse("arguments", "check, serve, or call TOOL [JSON]", repr(rest) if rest else "no verb",
               "use the declared verb and arguments")
    except Refusal as refusal:
        print(refusal.line(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
