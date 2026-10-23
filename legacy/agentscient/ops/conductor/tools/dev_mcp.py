#!/usr/bin/env python3
"""Small MCP entry for live development; existing services own all state.

    dev_mcp.py [--binding FILE] [--evidence-config FILE] [--serena-python FILE]
               [--project-config FILE] [--workspace PATH] check | serve

Only search, describe and invoke are exposed initially. Dispatch remains a
separate, explicitly requested adapter; this entry never launches workers.
"""

import re
import sys
from pathlib import Path

import code_work
import coordination_mcp as coordination
import mcp_transport
import project_work
from instrument import Refusal, refuse

sys.dont_write_bytecode = True

INSTRUCTIONS = (
    "Use capability_search, then capability_describe, then capability_invoke. "
    "Live communication between persistent harnesses is the default: find peers "
    "with coord_participants, send with coord_send, read coord_inbox, acknowledge "
    "and resolve separately. A queued message is not proof of delivery; inspect "
    "coord_routes and coord_attempts. Reuse a send key on retry. Dispatch is "
    "available separately only when the user explicitly requests it. Caller "
    "identity comes from the trusted session, never tool arguments."
)
SERVER_NAME = "dev-lane"
_TEXT = {"type": "string", "maxLength": 160}
_OFFSET = {"type": "integer", "minimum": 0, "maximum": 1_000_000}
LIVE_TOOLS = (
    coordination.Tool("coord_participants", "participants", False,
                      {"filter_role": _TEXT, "offset": _OFFSET}, (),
                      "Find peer participants for live harness communication.",
                      renames={"filter_role": "role"}),
    coordination.Tool("coord_routes", "routes", False, {"offset": _OFFSET}, (),
                      "Inspect live courier routes; an alive route is not a delivery receipt."),
    coordination.Tool("coord_attempts", "attempts", False,
                      {"message": _TEXT, "offset": _OFFSET}, (),
                      "Inspect native message submission attempts and delivery failures."),
)
LIVE_BY_NAME = {tool.name: tool for tool in LIVE_TOOLS}
COORD_NAMES = set(coordination.BY_NAME) | set(LIVE_BY_NAME)


def schema(name, description, properties, required=(), *, mutates=False):
    return {
        "name": name, "description": description,
        "inputSchema": {"type": "object", "properties": properties,
                        "required": list(required), "additionalProperties": False},
        "annotations": {"readOnlyHint": not mutates, "destructiveHint": mutates,
                        "idempotentHint": not mutates},
    }


TOOLS = [
    schema("capability_search", "Find a bounded page of capabilities; schemas are loaded on demand.",
           {"query": {"type": "string", "maxLength": 160},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            "offset": _OFFSET}),
    schema("capability_describe", "Inspect one capability's schema and availability.",
           {"capability": _TEXT}, ("capability",)),
    schema("capability_invoke", "Invoke a described capability; it may change durable state.",
           {"capability": _TEXT, "arguments": {"type": "object"}},
           ("capability", "arguments"), mutates=True),
]


def arguments_for(tool, arguments):
    """Validate the small discovery envelope; each service validates its own input."""
    if not isinstance(arguments, dict):
        refuse("arguments", "an arguments object", type(arguments).__name__,
               "pass the described object")
    spec = tool["inputSchema"]
    missing = set(spec["required"]) - set(arguments)
    unknown = set(arguments) - set(spec["properties"])
    if missing or unknown:
        refuse("arguments", "the described closed schema",
               f"missing {sorted(missing)}, unknown {sorted(unknown)}",
               "inspect the tool schema and correct the arguments")
    for name, value in arguments.items():
        rule = spec["properties"][name]
        wanted = rule["type"]
        valid = ((wanted == "string" and isinstance(value, str))
                 or (wanted == "object" and isinstance(value, dict))
                 or (wanted == "integer" and type(value) is int))
        if not valid:
            refuse("arguments", f"{name} as {wanted}", type(value).__name__,
                   "use the described type")
        if wanted == "integer" and not rule.get("minimum", 0) <= value <= rule.get("maximum", 1_000_000):
            refuse("arguments", f"{name} within its bounds", coordination.clip(value),
                   "use the described bounds or returned next_offset")
        if wanted == "string" and len(value) > rule.get("maxLength", 160):
            refuse("arguments", f"bounded {name}", "too long", "shorten the argument")
        if ("enum" in rule and value not in rule["enum"]) or (wanted == "string" and
                ("\x00" in value or (rule.get("pattern") and re.fullmatch(rule["pattern"], value) is None))):
            refuse("arguments", f"{name} matching the described schema", "invalid value", "correct the argument")
    return dict(arguments)


class Entry:
    def __init__(self, binding=None, evidence=None, *, serena_python=None, workspace=None, project_config=None):
        self.binding = binding
        self.evidence = evidence
        self.code = code_work.CodeWork(serena_python, workspace)
        self.project = project_work.ProjectWork(project_config, workspace)
        self.catalogue = {row["name"]: row for row in coordination._schemas()}
        for tool in LIVE_TOOLS:
            self.catalogue[tool.name] = schema(tool.name, tool.summary, tool.properties, tool.required)
        for name, (description, properties, required) in code_work.CAPABILITIES.items():
            self.catalogue[name] = schema(name, description, properties, required, mutates=name != "code_status")
        for name, (description, properties, required, mutates) in self.project.capabilities.items():
            self.catalogue[name] = schema(name, description, properties, required, mutates=mutates)
        # The optional evidence adapter can be absent without blocking messages.
        import lane_mcp
        self.catalogue.update({row["name"]: row for row in lane_mcp._schemas(
            evidence if evidence is not None else {"version": 1})})

    def card(self, name):
        row = self.catalogue.get(name)
        if row is None:
            refuse("capability", "a discovered capability", coordination.clip(name),
                   "use capability_search; dispatch requires an explicit user request and its separate adapter")
        reason = None
        if name.startswith("lane_") and self.evidence is None:
            reason = "No evidence configuration; start with --evidence-config FILE."
        if name in code_work.CAPABILITIES and name != "code_status":
            reason = self.code.unavailable()
        if name in self.project.capabilities:
            reason = self.project.unavailable(name)
        if name in COORD_NAMES | set(code_work.CAPABILITIES) | set(self.project.capabilities) and self.binding is not None and name not in self.binding["capabilities"]:
            reason = "This launch binding does not permit the capability."
        return {"name": name, "description": row["description"],
                "mutates": not row["annotations"]["readOnlyHint"],
                "available": reason is None, "reason": reason}

    def search(self, arguments):
        query = arguments.get("query", "").lower()
        terms = re.findall(r"[a-z0-9]+", query)
        cards = [self.card(name) for name in sorted(self.catalogue)]
        matches = [card for card in cards if all(
            term in (card["name"] + " " + card["description"]).lower() for term in terms)]
        start, limit = arguments.get("offset", 0), arguments.get("limit", 5)
        page = matches[start:start + limit]
        return {"capabilities": page, "total": len(matches),
                "next_offset": start + limit if start + limit < len(matches) else None}

    def invoke(self, name, arguments):
        card = self.card(name)
        if not card["available"]:
            refuse("capability", "an available capability", name, card["reason"])
        if name in code_work.CAPABILITIES:
            checked = arguments_for(self.catalogue[name], arguments)
            result = self.code.call(self.binding, name, checked)
            if name == "code_status" and self.project.context_capability:
                result["development_context"] = {"capability": self.project.context_capability,
                                                 "instruction": "Load relevant project context before product edits."}
            return result
        if name in self.project.capabilities:
            checked = arguments_for(self.catalogue[name], arguments)
            return self.project.call(self.binding, name, checked)
        if name in coordination.BY_NAME:
            return coordination.call(self.binding, name, dict(arguments))
        if name in LIVE_BY_NAME:
            tool = LIVE_BY_NAME[name]
            coordination.check_capability(self.binding, tool)
            coordination.check_named_authority(tool, arguments)
            checked = arguments_for(self.catalogue[name], arguments)
            offset = checked.pop("offset", 0)
            conn, participant = coordination.resolve_binding(self.binding, tool)
            try:
                verb = coordination._verb(coordination._core(), tool.verb)
                payload = verb(conn, participant, **coordination.compose(verb, tool, participant, checked))
            finally:
                if conn is not None:
                    conn.close()
            rows = payload[tool.verb]
            return {**payload, tool.verb: rows[offset:offset + 20],
                    "next_offset": offset + 20 if offset + 20 < len(rows) else None}
        import lane_mcp
        return lane_mcp.query(self.evidence, name, arguments)

    def call(self, name, arguments):
        tool = next((row for row in TOOLS if row["name"] == name), None)
        if tool is None:
            refuse("arguments", "search, describe or invoke", coordination.clip(name),
                   "use a tool from tools/list")
        args = arguments_for(tool, arguments)
        if name == "capability_search":
            return self.search(args)
        capability = args["capability"]
        if name == "capability_describe":
            return {**self.card(capability), "tool": self.catalogue[capability]}
        return self.invoke(capability, args["arguments"])

    def route(self, identifier, method, params):
        # Reject oversized correlation ids before any write. A committed message
        # must not lose its identity to the shared sender's final size guard.
        if len(coordination.canonical(identifier).encode("utf-8")) > 200:
            return mcp_transport.error(None, -32602, "Request id exceeds 200 bytes")
        if method == "tools/list" and not params:
            return mcp_transport.result(identifier, {"tools": TOOLS})
        if method != "tools/call":
            return mcp_transport.error(identifier, -32602 if method == "tools/list" else -32601,
                                       "Invalid params" if method == "tools/list" else "Method not found")
        if (set(params) not in ({"name"}, {"name", "arguments"})
                or not isinstance(params.get("name"), str)
                or not isinstance(params.get("arguments", {}), dict)):
            return mcp_transport.error(identifier, -32602, "Invalid params")
        try:
            payload = self.call(params["name"], params.get("arguments", {}))
        except Refusal as exc:
            return coordination._refusal_result(identifier, exc)
        except Exception as exc:  # noqa: BLE001 — answer a protocol request without exposing internal data
            return mcp_transport.error(identifier, -32603, f"Internal error: {type(exc).__name__}")
        message = coordination._call_result(identifier, payload)
        if coordination.over_bound(message):
            reading = coordination.unavailable(payload)
            for key in ("source_event", "source_fingerprint", "provider_epoch", "cell_outcome", "verification_invalidated", "outcome", "freshness", "result"):
                if key in payload:
                    reading[key] = payload[key]
            message = coordination._call_result(identifier, reading)
        return message

    def dispatch(self, request):
        return mcp_transport.dispatch(request, self.route, strict_initialize=True, server={
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
            "capabilities": {"tools": {"listChanged": False}},
            "instructions": INSTRUCTIONS + (" " + code_work.INSTRUCTIONS if self.code.python else "")
                            + (" " + self.project.instructions if self.project.instructions else ""),
        })

    def check(self):
        return {"server": SERVER_NAME, "tools": [row["name"] for row in TOOLS],
                "tools_list_bytes": len(coordination.canonical({"tools": TOOLS}).encode("utf-8")),
                "capabilities": len(self.catalogue), "response_bound": coordination.RESPONSE_BYTES,
                "communication": "live", "dispatch": "separate; explicit user request only",
                "binding": "config" if self.binding is not None else "session",
                "evidence": "configured" if self.evidence is not None else "unavailable",
                "serena": self.code.unavailable() or "configured; validate with a real prod-lane code call",
                "project": {"unavailable_reason": self.project.provider.unavailable(self.project.workspace, "")},
                "native_harness_restrictions": "not established by this adapter"}


def main():
    try:
        args = sys.argv[1:]
        options = {}
        while args and args[0].startswith("--"):
            if len(args) < 2 or args[0] not in ("--binding", "--evidence-config", "--serena-python", "--workspace", "--project-config") or args[0] in options:
                refuse("arguments", "[--binding FILE] [--evidence-config FILE] [--serena-python FILE] [--project-config FILE] [--workspace PATH] check | serve",
                       coordination.clip(args), "use each declared option once")
            options[args[0]] = Path(args[1])
            args = args[2:]
        if args not in (["check"], ["serve"]):
            refuse("arguments", "check or serve", coordination.clip(args), "select a declared verb")
        project_config = options.get("--project-config")
        project_caps = project_work.ProjectWork(project_config).capabilities
        binding = (coordination.load_config(options["--binding"], catalogue=COORD_NAMES | set(code_work.CAPABILITIES) | set(project_caps))
                   if "--binding" in options else None)
        evidence = None
        if "--evidence-config" in options:
            import lane_mcp
            evidence = lane_mcp.load_config(options["--evidence-config"])
        entry = Entry(binding, evidence, serena_python=options.get("--serena-python"), workspace=options.get("--workspace"), project_config=project_config)
        if args == ["check"]:
            print(coordination.canonical(entry.check()))
            return 0
        try:
            return mcp_transport.serve(sys.stdin.buffer, entry.dispatch, coordination._send)
        finally:
            entry.code.close()
    except Refusal as exc:
        print(exc.line(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
