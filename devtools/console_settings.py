"""Temporary Grok CO registration through the native MCP configuration CLI.

Loose Grok plugins start disabled. Use the supported MCP add/remove operations
instead of treating discovery as activation; keep user settings in native hands.
"""

import copy
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tomllib
from contextlib import contextmanager
from pathlib import Path

GENERATION_ENV = "AGENTSCIENT_CO_GENERATION"


def _identity(path):
    value = path.lstat()
    return value.st_dev, value.st_ino


def _server(path, name):
    values = tomllib.loads(path.read_text()) if path.exists() else {}
    return values.get("mcp_servers", {}).get(name)


def _native(binary, arguments, workspace, env):
    result = subprocess.run([binary, "mcp", *arguments], cwd=workspace, env=env,
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if result.returncode:
        # Native errors may quote configuration values; do not echo credentials.
        raise ValueError(f"Grok MCP {arguments[0]} failed with exit status {result.returncode}.")


@contextmanager
def grok_settings(binary, private, server, workspace, env):
    """Register only this console; preserve unrelated and operator-edited settings.

    Grok performs locked read/modify/write of its user configuration. Hooks and the
    MCP connection require the inherited generation marker, so other sessions cannot
    attach to this CO. No plugin, native tool policy, or project settings are changed.
    """
    from .api import SERVER

    private = Path(private).resolve()
    generation = private.name
    if not re.fullmatch(r"[0-9a-f]{32}", generation) or env.get(GENERATION_ENV) != generation:
        raise ValueError("The Grok console requires its current native session generation.")
    home = Path(env.get("GROK_HOME", str(Path(env.get("HOME", str(Path.home()))) / ".grok"))).expanduser()
    if not home.is_absolute():
        home = Path(workspace) / home
    config = home / "config.toml"
    name = SERVER + "_" + generation
    if _server(config, name) is not None:
        raise ValueError("The Grok console connection already exists; refusing to replace it.")
    gate = [sys.executable, str(Path(__file__).resolve()), generation]
    connection = {"command": sys.executable,
                  "args": [*gate[1:], server["command"], *server.get("args", [])], "enabled": True}
    hooks = copy.deepcopy(json.loads((private / "observer-hooks.json").read_text()))
    if "PreToolUse" in hooks:
        raise ValueError("Operator hooks must observe without imposing tool permissions.")
    for groups in hooks.values():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = shlex.join([*gate, *shlex.split(handler["command"])])
    data = (json.dumps({"hooks": hooks}, indent=2) + "\n").encode()
    created, owned_file, registered, attempted = [], None, None, False
    try:
        for directory in (home, home / "hooks"):
            try:
                directory.mkdir(mode=0o700, parents=True)
            except FileExistsError:
                if not directory.is_dir():
                    raise
            else:
                created.append((directory, _identity(directory)))
        path = home / "hooks" / ("agentscient-co-" + generation + ".json")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        owned_file = (path, _identity(path))
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
        attempted = True
        _native(binary, ["add", name, "--scope", "user", "--transport", "stdio", "--",
                         connection["command"], *connection["args"]], workspace, env)
        observed = _server(config, name)
        if (not isinstance(observed, dict) or observed.get("command") != connection["command"]
                or observed.get("args") != connection["args"] or observed.get("enabled") is False):
            raise ValueError("Grok did not register the requested CO connection.")
        registered = observed
        yield name
    finally:
        try:
            if attempted:
                try:
                    current = _server(config, name)
                except (OSError, ValueError):
                    current = None  # Preserve an unreadable/operator-edited configuration.
                if current == (registered or connection):
                    _native(binary, ["remove", name, "--scope", "user"], workspace, env)
        finally:
            if owned_file:
                path, identity = owned_file
                try:
                    if (_identity(path) == identity and stat.S_ISREG(path.lstat().st_mode)
                            and path.read_bytes() == data):
                        path.unlink()
                except OSError:
                    pass
            for directory, identity in reversed(created):
                try:
                    if _identity(directory) == identity:
                        directory.rmdir()
                except OSError:
                    pass  # Remove only our still-empty directories.


def main():
    """Native MCP and hook processes inherit only their own CO launch marker."""
    if len(sys.argv) >= 3 and os.environ.get(GENERATION_ENV) == sys.argv[1]:
        os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
