"""Independent expectations for MCP sessions reached through any client transport.

The conversation is written as JSON-RPC data and its expected replies are
derived from the MCP envelope rules and the declared application operations,
not from the server implementation.
"""

PROTOCOL = "2025-06-18"
FRAME_LIMIT = 4096


def rpc(identifier, method, params=None):
    return {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params if params is not None else {}}


def initialize(identifier=1):
    return rpc(identifier, "initialize", {"protocolVersion": PROTOCOL, "clientInfo": {"name": "contract", "version": "1"},
                                          "capabilities": {}})


INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def conversation(goal):
    """Ordered steps: ``(frame, expectation)``; a ``None`` expectation means no reply is due.

    Frames are either JSON objects or raw bytes (for framing faults).
    """
    return [
        (rpc(1, "tools/list"), ("error", -32002)),
        (b"not json", ("error", -32700)),
        (b"x" * (FRAME_LIMIT + 10), ("error", -32700)),
        (initialize(2), ("initialized", None)),
        (INITIALIZED, None),
        (rpc(3, "tools/list"), ("tools", {"create_work", "get_work", "list_work"})),
        (rpc(4, "tools/call", {"name": "create_work", "arguments": {"goal": goal}, "actor": "forged"}), ("work", goal)),
        (rpc(5, "tools/call", {"name": "create_work", "arguments": {"goal": goal, "actor": "forged"}}),
         ("tool_error", "invalid_arguments")),
        (rpc(6, "missing/method"), ("error", -32601)),
        (rpc(7, "tools/call", {"name": "get_work", "arguments": []}), ("error", -32602)),
        (rpc(8, "ping"), ("result", {})),
    ]


def check_reply(reply, frame, expectation, actor):
    """Assert one reply against its expectation; return the created work, if any."""
    kind, value = expectation
    if isinstance(frame, dict):
        assert reply.get("id") == frame["id"], (reply, frame)
    else:
        assert reply.get("id") is None, reply
    assert reply.get("jsonrpc") == "2.0", reply
    if kind == "error":
        assert reply["error"]["code"] == value, reply
        return None
    assert "error" not in reply, reply
    result = reply["result"]
    if kind == "initialized":
        assert result["protocolVersion"] == PROTOCOL and result["serverInfo"]["name"] == "xorchestrate", result
    elif kind == "tools":
        names = {tool["name"] for tool in result["tools"]}
        assert value <= names, names
        assert all(isinstance(tool["inputSchema"], dict) for tool in result["tools"])
    elif kind == "work":
        assert result["isError"] is False, result
        work = result["structuredContent"]["work"]
        assert work["goal"] == value and work["created_by"] == actor, work
        return work
    elif kind == "tool_error":
        assert result["isError"] is True and result["structuredContent"]["error"]["code"] == value, result
    else:
        assert result == value, result
    return None


def created_by(reply):
    """The actor recorded on a ``create_work`` reply."""
    return reply["result"]["structuredContent"]["work"]["created_by"]


def run_conversation(channel, actor, goal="Coordinate through the shared server"):
    """Drive the conversation over any channel with ``send`` and ``read``; return created work."""
    created = []
    for frame, expectation in conversation(goal):
        channel.send(frame)
        if expectation is not None:
            work = check_reply(channel.read(), frame, expectation, actor)
            if work is not None:
                created.append(work)
    assert len(created) == 1
    return created[0]
