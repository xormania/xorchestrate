"""Installed native harness adapters selected through the shared operator manifest.

Manifest harness entries name zero-argument factories. Instances declare vendor
and supports_provider and implement prepare(directory, member, config=None), permitted(tool,
arguments, catalogue=...), hook(directory, member, payload), run(directory,
member, config, generation), command(directory, member, config, private, server),
channel(private), retain_login(directory, member, generation), and
readiness(binary, probe, config=None). config supplies the selected runtime snapshot.
The validated operator_console launch mode preserves native operator controls;
ordinary managed profiles continue to enforce their role's native restrictions.
Adapters may declare supports_initial_prompt = True to accept the command-only
config["initial_prompt"] as actual initial user input in an interactive CO.
Without that declaration a requested brief fails before session allocation.
prepare returns (private_path, environment, MCP_config);
command returns (argv, native_session_id). run owns one persistent session;
the common launcher retains generation, work binding and cleanup ownership.
"""

from __future__ import annotations

import asyncio

from .adapters import factory


def get(name):
    adapter = factory("harness", name)()
    methods = ("prepare", "permitted", "hook", "run", "command", "channel", "retain_login", "readiness")
    if (any(not callable(getattr(adapter, method, None)) for method in methods)
            or not isinstance(getattr(adapter, "vendor", None), str) or not adapter.vendor
            or type(getattr(adapter, "supports_provider", None)) is not bool):
        raise ValueError("Harness adapters must implement profile, permission, hook, session and console capabilities.")
    # The configured name is identity; its adapter owns the underlying protocol.
    adapter.name = name
    return adapter


class BuiltinHarness:
    protocol = None
    supports_provider = False
    supports_initial_prompt = True

    def prepare(self, directory, member, config=None):
        from .profiles import _prepare_builtin

        return _prepare_builtin(directory, member, self.protocol, adapter_name=self.name, config=config)

    def permitted(self, tool, arguments, *, catalogue=None):
        from .profiles import _permitted_builtin

        return _permitted_builtin(self.protocol, tool, arguments, catalogue=catalogue)

    def hook(self, directory, member, payload):
        from .profiles import _hook_builtin

        return _hook_builtin(directory, member, self.name, payload, protocol=self.protocol)

    def retain_login(self, directory, member, generation):
        from .profiles import _retain_login_builtin

        return _retain_login_builtin(directory, member, self.protocol, generation)

    def command(self, directory, member, config, private, server):
        from .console import _command_builtin

        return _command_builtin(directory, member, config, private, server, protocol=self.protocol)

    def channel(self, private):
        return None

    def readiness(self, binary, probe, config=None):
        return {"protocol": self.readiness_message}

    def run(self, directory, member, config, generation):
        from .native import Native

        return asyncio.run(Native(directory, member, config, generation).run())


class CodexHarness(BuiltinHarness):
    protocol = "codex"
    vendor = "openai"
    supports_provider = True

    def readiness(self, binary, probe, config=None):
        import os
        import tomllib
        from pathlib import Path

        from .compat import codex_capability, require_codex_mcp_approval
        from .profiles import codex_settings

        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        if (config or {}).get("operator_console"):
            # The operator TUI retains its native home and credential store. It
            # does not depend on the managed app-server protocol or file-login
            # import used by isolated workers.
            path = home / "config.toml"
            settings = tomllib.loads(path.read_text()) if path.is_file() else {}
            mode = settings.get("cli_auth_credentials_store", "file")
            if not isinstance(mode, str) or mode not in {"file", "ephemeral", "keyring", "auto"}:
                raise ValueError("Unsupported Codex cli_auth_credentials_store setting.")
            approval = require_codex_mcp_approval(binary, probe)
            return {"protocol": "Native TUI authentication and session creation are checked on actual startup.",
                    "login_storage": mode, "mcp_approval": approval}
        settings = codex_settings(home, config)
        capability = codex_capability(binary)
        if capability["status"] != "supported":
            raise ValueError("Installed Codex must support TurnStartParams.toolOutput; update before launch.")
        return {"protocol": capability, "login_storage": settings["cli_auth_credentials_store"]}


class GrokHarness(BuiltinHarness):
    protocol = "grok"
    vendor = "xai"
    readiness_message = "ACP authentication and session creation are checked on actual startup."

    def readiness(self, binary, probe, config=None):
        if (config or {}).get("operator_console"):
            return {"protocol": "Native TUI authentication and session creation are checked on actual startup."}
        return super().readiness(binary, probe, config)


class ClaudeHarness(BuiltinHarness):
    protocol = "claude"
    vendor = "anthropic"
    readiness_message = "Channel registration is checked on actual startup."

    def channel(self, private):
        from .compat import ClaudeChannelLog

        return ClaudeChannelLog(private / "native-debug.log")

    def run(self, directory, member, config, generation):
        from .native import claude

        return claude(directory, member, config, generation)
