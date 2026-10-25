#!/usr/bin/env python3
"""Offline fixture for Grok's native user-scoped MCP add/remove commands.

Matches the explicit scope/transport CLI and persisted stdio fields in upstream
Grok CLI's MCP command handlers. It does not emulate plugin discovery or claim
authenticated native acceptance. Serialization is independent of the launcher.

Source: Grok CLI at 2bdd1d6a6369de0e8c68132ea4539e9abd9e14a81,
crates/codegen/xai-grok-pager/src/mcp_cmd.rs.
Reference: https://docs.x.ai/build/features/mcp-servers
"""

import json
import os
import sys
import tomllib
from pathlib import Path


def write_config(path, settings):
    """Serialize the TOML values exercised by native settings fixtures."""
    def value(item):
        if isinstance(item, dict):
            return "{" + ", ".join(json.dumps(key) + " = " + value(child) for key, child in item.items()) + "}"
        if isinstance(item, list):
            return "[" + ", ".join(value(child) for child in item) + "]"
        return json.dumps(item)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(key) + " = " + value(item) + "\n" for key, item in settings.items()))


def main(arguments=None):
    args = sys.argv[1:] if arguments is None else arguments
    assert args[:1] == ["mcp"], args
    action, name = args[1:3]
    if action == "add":
        assert args[3:8] == ["--scope", "user", "--transport", "stdio", "--"], args
        assert len(args) >= 9, args
    else:
        assert action == "remove" and args[3:] == ["--scope", "user"], args
    failure = os.environ.get("AGENTSCIENT_GROK_MCP_FAIL")
    if failure == action:
        print("Native MCP command fixture failed", file=sys.stderr)
        return 1
    home = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok")))
    path = home / "config.toml"
    settings = tomllib.loads(path.read_text()) if path.is_file() else {}
    servers = settings.setdefault("mcp_servers", {})
    if action == "add":
        servers[name] = {"command": args[8], "args": args[9:], "enabled": True}
        if failure == "add-foreign-replacement":
            servers[name] = {"command": "operator-replacement", "args": ["--keep"], "enabled": True}
    else:
        if name not in servers:
            print("Server not configured", file=sys.stderr)
            return 1
        del servers[name]
        if not servers:
            del settings["mcp_servers"]
    write_config(path, settings)
    if failure == action + "-after-write":
        print("Native MCP command failed after saving", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
