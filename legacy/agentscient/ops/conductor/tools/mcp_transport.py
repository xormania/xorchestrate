"""Shared stdio framing and JSON-RPC envelope; domain policy stays in adapters.

See ../runtime-observation-contract.md. No source discovery, authority,
filesystem writes, network or domain imports belong at this boundary.
"""

import json

PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL = PROTOCOLS[-1]
MAX_LINE = 65_536


def error(identifier, code, message, data=None):
    value = {"code": code, "message": message}
    if data is not None:
        value["data"] = data
    return {"jsonrpc": "2.0", "id": identifier, "error": value}


def result(identifier, payload):
    return {"jsonrpc": "2.0", "id": identifier, "result": payload}


def dispatch(request, route, *, server, strict_initialize=False):
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return error(None, -32600, "Invalid Request")
    identifier = request.get("id")
    notification = "id" not in request
    method = request.get("method")
    if not isinstance(method, str):
        return error(identifier, -32600, "Invalid Request")
    params = request.get("params", {})
    if not isinstance(params, dict) or ("_meta" in params and not isinstance(params["_meta"], dict)):
        return None if notification else error(identifier, -32602, "Invalid params")
    params = dict(params)
    params.pop("_meta", None)
    if notification:
        return None
    if method == "initialize":
        if strict_initialize:
            if not set(params) <= {"protocolVersion", "capabilities", "clientInfo"}:
                return error(identifier, -32602, "Invalid params")
            if "protocolVersion" in params and not isinstance(params["protocolVersion"], str):
                return error(identifier, -32602, "Invalid params")
            if any(name in params and not isinstance(params[name], dict)
                   for name in ("capabilities", "clientInfo")):
                return error(identifier, -32602, "Invalid params")
        requested = params.get("protocolVersion")
        selected = requested if requested in PROTOCOLS else LATEST_PROTOCOL
        return result(identifier, {
            "protocolVersion": selected,
            "capabilities": server.get("capabilities", {"tools": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False}}),
            "serverInfo": server["serverInfo"],
            "instructions": server["instructions"],
        })
    if method == "ping":
        return result(identifier, {})
    return route(identifier, method, params)


def _reject_constant(value):
    raise ValueError(value)


def serve(source, dispatch_request, send):
    """Drain one bounded newline frame at a time; send owns serialization/bounds."""
    while True:
        line = source.readline(MAX_LINE + 1)
        if not line:
            return 0
        if len(line) > MAX_LINE:
            while line and not line.endswith(b"\n"):
                line = source.readline(MAX_LINE + 1)
            send(error(None, -32700, "Parse error"))
            continue
        try:
            request = json.loads(line, parse_constant=_reject_constant)
        except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError):
            send(error(None, -32700, "Parse error"))
            continue
        response = dispatch_request(request)
        if response is not None:
            send(response)
