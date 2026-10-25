#!/usr/bin/env python3
"""CLI and stdio MCP projection for bounded conductor lane reads."""

import sys

sys.dont_write_bytecode = True

from pathlib import Path  # noqa: E402

# The refusal wire comes from the shared module beside this file and
# lane_read.py; the script's own directory is already first on sys.path.
try:
    from instrument import Refusal, refuse
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

from lane_read import (  # noqa: E402
    DIGEST_RE,
    DISCOVERY_ID_RE,
    DISCOVERY_QUERIES,
    JOB_NAME_RE,
    canonical,
    check_result,
    decode_object,
    discovery_arguments,
    load_config,
    matches,
    strict_json,
)
from lane_read import (  # noqa: E402
    query as read_query,
)
from mcp_transport import (  # noqa: E402
    dispatch as dispatch_request,
)
from mcp_transport import (  # noqa: E402
    error as _error,
)
from mcp_transport import (  # noqa: E402
    result as _result,
)
from mcp_transport import (  # noqa: E402
    serve as serve_stdio,
)

INSTRUCTIONS = (
    "Read-only; evidence is not authority. Use brief before detail. "
    "No live readiness."
)


# Both transport projections use this coordinator; the read core accepts data only.
_ACQUISITIONS = {}


def query(config: dict, name: str, arguments: object) -> dict:
    if config["version"] != 3 or name not in DISCOVERY_QUERIES:
        return read_query(config, name, arguments)
    args = discovery_arguments(config, name, arguments)
    # Import only after schema validation; check/list and all legacy reads do
    # not open an anchor or load the acquisition adapter.
    from lane_refs import Acquisition

    key = id(config)
    try:
        if key not in _ACQUISITIONS:
            _ACQUISITIONS[key] = Acquisition(config)
        reading = _ACQUISITIONS[key].acquire(args["profile"])
    except OSError:
        reading = {"state": "unavailable", "reason": "unreadable", "sources": []}
    return read_query(config, name, args, reading)


def _schemas(config: dict[str, object]) -> list[dict[str, object]]:
    annotations = {"readOnlyHint": True, "destructiveHint": False}
    tools = [
        {
            "name": "lane_brief",
            "description": "Read the bounded lane brief before requesting detail.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": annotations,
        },
        {
            "name": "lane_record",
            "description": "Read one explicitly configured dispatch record summary.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "lane_alerts",
            "description": "Read a bounded monitor alert page with an explicit cursor.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "after": {"type": "integer", "minimum": 0, "default": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                },
                "additionalProperties": False,
            },
            "annotations": annotations,
        },
        {
            "name": "lane_usage",
            "description": "Read aggregate token usage from the configured rollout tail.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
            "annotations": annotations,
        },
    ]
    if config["version"] >= 2:
        tools.extend(
            [
                {
                    "name": "lane_findings",
                    "description": "Read a bounded page of recorded findings.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "cursor": {"type": "string"},
                        },
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                    "annotations": annotations,
                },
                {
                    "name": "lane_changes",
                    "description": "Compare record bytes with a revision checkpoint.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "checkpoint": {
                                "type": "object",
                                "additionalProperties": {"type": "string"},
                            }
                        },
                        "additionalProperties": False,
                    },
                    "annotations": annotations,
                },
                {
                    "name": "lane_jobs",
                    "description": "Read bounded structural cards from the job registry.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "additionalProperties": False,
                    },
                    "annotations": annotations,
                },
            ]
        )
    if config["version"] == 3:
        for name in DISCOVERY_QUERIES:
            properties = {"profile": {"type": "string", "pattern": "^[a-z][a-z0-9-]{0,31}$"},
                          "snapshot": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}
            fields = {"lane_start": ("cursor",), "lane_homes": ("concept", "cursor"),
                      "lane_handoffs": ("key", "cursor"), "lane_actions": ("name",)}[name]
            patterns = {"concept": "^[a-z][a-z0-9-]{0,63}$", "name": "^[a-z][a-z0-9-]{0,31}$",
                        "key": "^record/[0-9a-f]{64}$", "cursor": "^[0-9a-f]{64}:(0|[1-9][0-9]{0,2})$"}
            properties.update({field: {"type": "string", "pattern": patterns[field]} for field in fields})
            tools.append({"name": name, "description": "Read bounded profile discovery; evidence is not authority.",
                          "inputSchema": {"type": "object", "properties": properties,
                                          "required": ["profile"], "additionalProperties": False},
                          "annotations": annotations})
    return tools


def _resources(config: dict[str, object]) -> list[dict[str, str]]:
    rows = [
        ("lane://brief", "Lane brief", "Bounded lane summary without live readiness"),
        ("lane://alerts", "Lane alerts", "Default bounded monitor alert page"),
        ("lane://usage", "Lane usage", "Aggregate configured rollout usage"),
    ]
    rows.extend(
        (f"lane://record/{ident}", f"Lane record {ident}", "Configured dispatch record summary")
        for ident in config["job_ids"]
    )
    if config["version"] >= 2:
        rows.extend(
            (f"lane://findings/{ident}", f"Lane findings {ident}", "First recorded findings page")
            for ident in config["job_ids"]
        )
        rows.extend(
            [
                ("lane://changes", "Lane changes", "Stateless record revision comparison"),
                ("lane://jobs", "Lane jobs", "Bounded job registry cards"),
            ]
        )
    if config["version"] == 3:
        rows.extend((f"lane://{kind}/{profile}", f"Lane {kind} {profile}", "Bounded profile discovery")
                    for profile in config["profiles"] for kind in ("start", "homes", "handoffs", "actions"))
    return [
        {"uri": uri, "name": name, "description": description, "mimeType": "application/json"}
        for uri, name, description in rows
    ]


def _resource_query(config: dict[str, object], uri: object) -> dict[str, object]:
    if uri == "lane://brief":
        return query(config, "lane_brief", {})
    if uri == "lane://alerts":
        return query(config, "lane_alerts", {})
    if uri == "lane://usage":
        return query(config, "lane_usage", {})
    if isinstance(uri, str) and uri.startswith("lane://record/"):
        return query(config, "lane_record", {"id": uri.removeprefix("lane://record/")})
    if config["version"] >= 2:
        if isinstance(uri, str) and uri.startswith("lane://findings/"):
            return query(
                config,
                "lane_findings",
                {"id": uri.removeprefix("lane://findings/")},
            )
        if uri == "lane://changes":
            return query(config, "lane_changes", {})
        if uri == "lane://jobs":
            return query(config, "lane_jobs", {})
        if isinstance(uri, str) and uri.startswith("lane://jobs/"):
            return query(config, "lane_jobs", {"name": uri.removeprefix("lane://jobs/")})
    if config["version"] == 3 and isinstance(uri, str) and uri.startswith("lane://"):
        parts = uri[7:].split("/")
        if len(parts) >= 2 and matches(DISCOVERY_ID_RE, parts[1]) and parts[1] in config["profiles"]:
            kind, profile = parts[:2]
            args = {"profile": profile}
            if kind in ("start", "homes", "handoffs", "actions") and len(parts) == 2:
                return query(config, "lane_" + kind, args)
            if kind == "homes" and len(parts) == 3 and matches(JOB_NAME_RE, parts[2]):
                return query(config, "lane_homes", dict(args, concept=parts[2]))
            if kind == "actions" and len(parts) == 3 and matches(DISCOVERY_ID_RE, parts[2]):
                return query(config, "lane_actions", dict(args, name=parts[2]))
            if kind == "handoffs" and len(parts) == 4 and parts[2] == "record" and matches(DIGEST_RE, parts[3]):
                return query(config, "lane_handoffs", dict(args, key="record/" + parts[3]))
    refuse("arguments", "a listed lane resource URI", repr(uri), "select a listed resource")


def _dispatch(config, request):
    return dispatch_request(
        request, lambda identifier, method, params: _route(config, identifier, method, params),
        server={
            "serverInfo": {"name": "agentscient-lane", "version": "1"},
            "instructions": INSTRUCTIONS,
        }, strict_initialize=False,
    )


def _route(config, identifier, method, params):
    if method == "tools/list":
        return _result(identifier, {"tools": _schemas(config)})
    if method == "resources/list":
        return _result(identifier, {"resources": _resources(config)})
    if method == "resources/templates/list" and config["version"] >= 2:
        return _result(
            identifier,
            {
                "resourceTemplates": ([
                    {
                        "uriTemplate": "lane://jobs/{name}",
                        "name": "Lane job card",
                        "mimeType": "application/json",
                    }
                ] + ([{"uriTemplate": uri, "name": "Lane discovery detail", "mimeType": "application/json"}
                      for uri in ("lane://homes/{profile}/{concept}", "lane://handoffs/{profile}/record/{digest}",
                                  "lane://actions/{profile}/{name}")] if config["version"] == 3 else []))
            },
        )
    if method == "tools/call":
        if set(params) not in ({"name"}, {"name", "arguments"}) or not isinstance(
            params.get("name"), str
        ):
            return _error(identifier, -32602, "Invalid params")
        arguments = params.get("arguments", {})
        try:
            payload = query(config, params["name"], arguments)
        except Refusal as exc:
            return _result(
                identifier,
                {"content": [{"type": "text", "text": exc.line()}], "isError": True},
            )
        text = canonical(payload)
        return _result(
            identifier,
            {
                "content": [{"type": "text", "text": text}],
                "structuredContent": payload,
                "isError": False,
            },
        )
    if method == "resources/read":
        if set(params) != {"uri"}:
            return _error(identifier, -32602, "Invalid params")
        try:
            payload = _resource_query(config, params["uri"])
        except Refusal:
            return _error(identifier, -32602, "Invalid params")
        return _result(
            identifier,
            {
                "contents": [
                    {
                        "uri": params["uri"],
                        "mimeType": "application/json",
                        "text": canonical(payload),
                    }
                ]
            },
        )
    return _error(identifier, -32601, "Method not found")


def _send(message: dict[str, object]) -> None:
    sys.stdout.write(canonical(message) + "\n")
    sys.stdout.flush()


def serve(config):
    return serve_stdio(sys.stdin.buffer, lambda request: _dispatch(config, request), _send)


def _cli(argv: list[str]) -> tuple[Path, str, list[str]]:
    if len(argv) < 3 or argv[0] != "--config":
        refuse(
            "arguments",
            "--config FILE followed by check, serve, or query",
            repr(argv),
            "use the declared command shape",
        )
    return Path(argv[1]), argv[2], argv[3:]


def main() -> int:
    try:
        config_path, verb, rest = _cli(sys.argv[1:])
        config = load_config(config_path)
        if verb == "check" and rest in ([], ["."]):
            print(canonical(check_result(config)))
            return 0
        if verb == "serve" and not rest:
            return serve(config)
        if verb == "query" and len(rest) in {1, 2}:
            if config['version'] == 3 and rest[0] in DISCOVERY_QUERIES:
                try:
                    arguments = strict_json(rest[1].encode('utf-8')) if len(rest) == 2 else {}
                except (ValueError, UnicodeError, RecursionError):
                    refuse('arguments', f'closed arguments for {rest[0]}', 'invalid', 'use the declared query schema')
            else:
                arguments = decode_object(rest[1]) if len(rest) == 2 else {}
            print(canonical(query(config, rest[0], arguments)))
            return 0
        refuse(
            "arguments",
            "check, serve, or query TOOL [JSON]",
            repr([verb, *rest]),
            "use the declared verb and arguments",
        )
    except Refusal as exc:
        print(exc.line(), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
