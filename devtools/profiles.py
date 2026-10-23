"""Managed native restrictions and additive settings for the operator console."""

import hashlib
import json
import os
import shlex
import shutil
import sys
import tomllib
import uuid
from pathlib import Path

from .api import API, SERVER, TOOLS, validate
from .role_packages import HOOK_EVENTS, descriptor
from .role_packages import role as role_package
from .serena_provider import SERVER as SERENA_SERVER
from .serena_provider import TOOL as SERENA_TOOL
from .serena_provider import enabled as serena_enabled
from .serena_provider import server_config as serena_config

ENTRY = Path(__file__).resolve().parent / "run.py"
CODEX_FEATURES = {
    "hooks": True,
    "shell_tool": False,
    "unified_exec": False,
    "apply_patch_freeform": False,
    "js_repl": False,
    "code_mode": False,
    "plugins": False,
    "apps": False,
    "multi_agent": False,
}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data, indent=2) + "\n")
    path.chmod(0o600)


def toml(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(json.dumps(key) + " = " + toml(item) for key, item in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(map(toml, value)) + "]"
    return json.dumps(value)


def claude_command(config, private, instructions, catalogue, prompt, *, managed=True):
    """Keep worker restrictions out of the operator's native console."""
    native_id = str(uuid.uuid4())
    debug = private / "native-debug.log"
    debug.write_text("")
    debug.chmod(0o600)
    argv = [
        config["binary"],
        "--session-id", native_id,
        "--mcp-config", str(private / "mcp.json"),
        "--settings", str(private / "settings.json"),
        "--debug-file", str(debug),
        "--dangerously-load-development-channels", "server:" + SERVER,
        "--append-system-prompt", instructions,
    ]
    if managed:
        argv += [
            "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
            "--tools", "", "--disallowedTools", "Agent,Task",
            "--allowedTools", ",".join(
                "mcp__" + provider + "__" + row["name"]
                for provider, rows in catalogue.items() for row in rows
            ),
        ]
    for source, flag in (("model", "--model"), ("reasoning", "--effort")):
        if config.get(source):
            argv += [flag, config[source]]
    return [*argv, "--", prompt], native_id


def restore_login(private, original, harness):
    """Carry refreshed private credentials forward; a changed operator login wins."""
    source = original / "auth.json"
    identity = str(source.resolve())
    digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None
    cache = private.parent / (harness + "-login.json")
    previous = json.loads(cache.read_text()) if cache.is_file() else {}
    destination = private / "auth.json"
    destination.unlink(missing_ok=True)
    if previous.get("source_path") == identity and previous.get("source_sha256") == digest and "auth" in previous:
        destination.write_text(previous["auth"])
    elif source.is_file():
        shutil.copyfile(source, destination)
    if destination.exists():
        destination.chmod(0o600)
    write_json(private / "login-origin.json", {"source_path": identity, "source_sha256": digest, "harness": harness})


def codex_settings(original, config=None):
    """Select reusable native settings without treating a stale auth file as login.

    Codex keyring identities depend on CODEX_HOME. Its login CLI has no export
    operation, so a private generation cannot inherit that credential store.
    An operator can keep global keyring login and use a separate file-login home.
    """
    path = original / "config.toml"
    previous = tomllib.loads(path.read_text()) if path.is_file() else {}
    provider = (config or {}).get("provider") or previous.get("model_provider", "openai")
    provider_settings = previous.get("model_providers", {}).get(provider, {})
    requires_login = provider_settings.get("requires_openai_auth", provider == "openai")
    mode = previous.get("cli_auth_credentials_store", "file")
    if not isinstance(mode, str):
        raise ValueError("Codex cli_auth_credentials_store must name a native storage mode.")
    if not requires_login:
        # Independent providers supply their own native env/command credentials.
        # Never import an unrelated OpenAI account into that provider's session.
        mode = "ephemeral"
    elif mode in {"keyring", "auto"}:
        raise ValueError(
            "Private Codex profiles cannot reuse keyring/auto login storage. "
            "Use a dedicated CODEX_HOME with cli_auth_credentials_store=\"file\", "
            "run native codex login there, and launch with that CODEX_HOME. "
            "See devtools/OPERATIONS.md#native-login-storage."
        )
    if mode not in {"file", "ephemeral"}:
        raise ValueError("Unsupported Codex cli_auth_credentials_store setting.")
    keys = ("model", "model_provider", "model_providers", "model_reasoning_effort", "service_tier",
            "forced_login_method", "forced_chatgpt_workspace_id")
    return {**{key: previous[key] for key in keys if key in previous}, "cli_auth_credentials_store": mode}


def retain_login(directory, member, harness, generation):
    from .harnesses import get

    return get(harness).retain_login(directory, member, generation)


def _retain_login_builtin(directory, member, harness, generation):
    if harness not in {"codex", "grok"}:
        return
    private = Path(directory) / "profiles" / member / generation
    if (private / "auth.json").is_file() and (private / "login-origin.json").is_file():
        origin = json.loads((private / "login-origin.json").read_text())
        if "source_path" in origin:
            write_json(private.parent / (harness + "-login.json"),
                       {"source_path": origin["source_path"], "source_sha256": origin["source_sha256"],
                        "auth": (private / "auth.json").read_text()})


def permitted(harness, tool, arguments, *, catalogue=None):
    from .harnesses import get

    try:
        adapter = get(harness)
    except ValueError:
        return False
    return adapter.permitted(tool, arguments, catalogue=catalogue)


def _permitted_builtin(harness, tool, arguments, *, catalogue=None):
    if (not isinstance(harness, str) or harness not in {"codex", "claude", "grok"}
            or not isinstance(tool, str) or not isinstance(arguments, dict)):
        return False
    if tool in {
        "codex": {"update_plan", "request_user_input"},
        "claude": {"EndConversation", "StructuredOutput"},
        "grok": {"search_tool"},
    }.get(harness, set()):
        return True
    if harness == "grok" and tool == "use_tool":
        if set(arguments) != {"tool_name", "tool_input"}:
            return False
        tool, arguments = arguments["tool_name"], arguments["tool_input"]
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            return False
    catalogue = {SERVER: TOOLS, SERENA_SERVER: [SERENA_TOOL]} if catalogue is None else catalogue
    for provider, row in [(provider, row) for provider, rows in catalogue.items() for row in rows]:
        names = {f"mcp__{provider}__{row['name']}", f"{provider}__{row['name']}"}
        if harness == "codex":
            names.add(row["name"])
        if tool in names:
            try:
                validate(arguments, row["inputSchema"])
                return True
            except ValueError:
                return False
    return False


def tool_catalogue(directory, member, *, package_ceiling=False):
    """Current MCP authority, or the role's stable native launch allowance.

    Native CLI allowlists last for the session. The package ceiling permits
    tools that become available after ordinary binding/project transitions;
    MCP and hooks still check the live, narrower catalogue on every call.
    """
    api = API(directory, member)
    scope = api.control.snapshot()
    rows = api._tools(scope)
    role = api.commands.role(scope)
    if package_ceiling and role:
        declared = {name for group in role_package(role)["mcp"].values() for name in group}
        current = {row["name"]: row for row in rows}
        rows = [current.get(row["name"], row) for row in TOOLS if row["name"] in declared]
    result = {SERVER: rows}
    if serena_enabled(api.store, scope):
        result[SERENA_SERVER] = [SERENA_TOOL]
    return result


def prepare(directory, member, harness, config=None):
    from .harnesses import get

    api = API(directory, member)
    scope = api.control.snapshot()
    role = api.commands.role(scope)
    if role and harness not in role_package(role)["runtime"]["harnesses"]:
        raise ValueError("This role package does not permit the selected harness")
    if (config or {}).get("operator_console"):
        operator_console(directory, member)
    return get(harness).prepare(directory, member, config=config)


def operator_console(directory, member):
    """The console exception belongs only to the appointed external CO."""
    from .launch import team_config
    from .runtime import co_role

    api = API(directory, member)
    if team_config(directory).get("co") != member or api.store.member(member)["harness"] != "external":
        raise ValueError("Native operator controls require the appointed external CO.")
    co_role(api.control.snapshot())


def _prepare_builtin(directory, member, harness, *, adapter_name=None, config=None):
    """Isolate managed workers; preserve the operator's native configuration."""
    directory = Path(directory).resolve()
    from .store import Store

    store = Store(directory)
    operator = bool((config or {}).get("operator_console"))
    if operator:
        operator_console(directory, member)
    api = API(directory, member)
    scope = api.control.snapshot()
    role = api.commands.role(scope)
    package = role_package(role) if role else None
    if package and (adapter_name or harness) not in package["runtime"]["harnesses"]:
        raise ValueError("This role package does not permit the selected harness")
    if operator and harness == "codex":
        from .compat import require_codex_mcp_approval

        require_codex_mcp_approval(config["binary"])
    generation = store.member(member)["generation"]
    private = directory / "profiles" / member
    if generation:
        private /= generation
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    if package:
        if scope is None:
            from .standby import binding as standby_binding

            with store.connect() as db:
                scope = standby_binding(db, member)
        write_json(private / "role-package.json", {**descriptor(role), "binding_revision": scope["revision"] if scope else None})
    env = os.environ.copy()
    # Grok gets a private HOME below; the server still uses the operator's GitHub OAuth session.
    env.setdefault("GH_CONFIG_DIR", str(Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "gh"))
    cleared = ["CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "GROK_SESSION_ID", "AGENTSCIENT_CO_GENERATION"]
    if not operator:
        cleared += ["PYTHONPATH", "PYTHONSTARTUP", "CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE"]
    for key in cleared:
        env.pop(key, None)
    if operator:
        env["AGENTSCIENT_CO_GENERATION"] = private.name
    lease_args = ["--generation", generation] if generation else []
    hook = shlex.join(
        [sys.executable, str(ENTRY), "hook", "--state", str(directory), "--member", member, "--harness", adapter_name or harness,
         *lease_args]
    )
    groups = {
        name: [
            {
                **({"matcher": ".*"} if name.endswith("ToolUse") else {}),
                "hooks": [{"type": "command", "command": hook, "timeout": 10}],
            }
        ]
        for name in (package["hooks"]["events"] if package else HOOK_EVENTS)
        if not operator or name != "PreToolUse"
    }
    server = {"command": sys.executable,
              "args": [str(ENTRY), "mcp", "--state", str(directory), "--member", member, *lease_args]}
    if harness == "claude":
        from .control import Control
        from .store import Store

        server["args"].append("--channel")
        write_json(private / "settings.json", {"hooks": groups})
        servers = {SERVER: server}
        store = Store(directory)
        if serena_enabled(store, Control(store, member).snapshot()):
            servers[SERENA_SERVER] = serena_config(directory, member)
        write_json(private / "mcp.json", {"mcpServers": servers})
    elif harness == "codex":
        if operator:
            # Native home, settings layers, login storage and global permissions
            # belong to the operator. Approve our role-bound MCP surface by
            # default so native approval_policy=never does not deadlock writes.
            # Native per-tool overrides still take precedence over this default;
            # server-side role/work/generation checks remain authoritative.
            server["default_tools_approval_mode"] = "approve"
            write_json(private / "observer-hooks.json", groups)
            return private, env, server
        original = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        settings = codex_settings(original, config)
        if settings["cli_auth_credentials_store"] == "file":
            restore_login(private, original, harness)
        else:
            # Ephemeral native authentication must not restore or retain a file login.
            for name in ("auth.json", "login-origin.json"):
                (private / name).unlink(missing_ok=True)
        settings.update(features=CODEX_FEATURES, mcp_servers={}, web_search="disabled")
        (private / "config.toml").write_text(
            "\n".join(f"{key} = {toml(value)}" for key, value in settings.items()) + "\n"
        )
        write_json(private / "hooks.json", {"hooks": groups})
        env["CODEX_HOME"] = str(private)
    elif harness == "grok":
        if operator:
            write_json(private / "observer-hooks.json", groups)
            return private, env, server
        original = Path(os.environ.get("GROK_HOME", str(Path.home() / ".grok")))
        restore_login(private, original, harness)
        write_json(private / "hooks" / "agentscient-live.json", {"hooks": groups})
        # Native child sessions and workflows bypass the command service's
        # role/job bindings. Disable them in the private configuration and
        # override inherited feature switches before the ACP process starts.
        settings = {"mcp_servers": {}, "subagents": {"enabled": False},
                    "workflows": {"enabled": False}, "goal": {"enabled": False}}
        (private / "config.toml").write_text(
            "\n".join(f"{key} = {toml(value)}" for key, value in settings.items()) + "\n"
        )
        env.update(HOME=str(private), GROK_HOME=str(private), GROK_CONFIG_DIR=str(private),
                   GROK_SUBAGENTS="0", GROK_WORKFLOWS="0", GROK_GOAL="0")
    else:
        raise ValueError("Supported harnesses: codex, claude, grok")
    return private, env, server


def hook(directory, member, harness, payload, generation=None):
    from .sessions import Lease
    from .store import Store

    with Lease(Store(directory), member, generation).hold():
        return _hook(directory, member, harness, payload)


def _hook(directory, member, harness, payload):
    from .harnesses import get

    return get(harness).hook(directory, member, payload)


def _hook_builtin(directory, member, harness, payload, *, protocol):
    from .store import Store
    from .telemetry import Trace, context, observed

    store = Store(directory)
    trace = Trace(directory, member, component="hook", context=lambda: context(store, member))
    try:
        trace.record("native_hook", {"harness": harness, "payload": payload})
    finally:
        trace.close()
    observed(store, member, harness + "/hook", payload)
    if protocol == "claude" and payload.get("transcript_path"):
        path = Path(payload["transcript_path"]).expanduser().resolve()
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))).resolve() / "projects"
        native_id = payload.get("session_id")
        if path.is_relative_to(root) and path.name == str(native_id) + ".jsonl":
            store.event(member, "native_transcript", path=str(path), native_id=native_id)
        else:
            store.event(member, "transcript_rejected", reason="Path outside the native session transcript directory")
    event = payload.get("hook_event_name", payload.get("hookEventName", "PreToolUse"))
    event = {
        "pre_tool_use": "PreToolUse",
        "post_tool_use": "PostToolUse",
        "session_start": "SessionStart",
        "stop": "Stop",
    }.get(event, event)
    if event == "SessionStart" and isinstance(payload.get("session_id"), str):
        current = store.member(member)
        replace = current["native_id"] is None
        if current["detail"].get("transport") == "interactive/pty":
            try:
                operator_console(directory, member)
            except ValueError:
                pass  # A repaired managed seat can retain old console detail.
            else:
                # Native /clear and /resume can change the session inside this
                # console generation. Keep trace context attached to that session.
                replace = True
        if replace and current["generation"] is not None:
            store.session(member, generation=current["generation"], native_id=payload["session_id"])
    tool = payload.get("tool_name", payload.get("toolName"))
    arguments = payload.get("tool_input", payload.get("toolInput", {}))
    if event == "PreToolUse":
        allowed = permitted(harness, tool, arguments, catalogue=tool_catalogue(directory, member))
        if protocol == "claude":
            current = store.member(member)
            if current["desired"] == "running" and current["state"] == "idle":
                store.session(member, generation=current["generation"], state="active")
        store.event(member, "native_tool_allowed" if allowed else "native_tool_denied", tool=tool, harness=harness)
        if protocol == "grok":
            return {"decision": "allow" if allowed else "deny", "reason": "Use this team's MCP tools."}
        return {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "allow" if allowed else "deny",
                "permissionDecisionReason": "Use this team's MCP tools; native shell and filesystem tools are disabled.",
            }
        }
    store.event(member, "native_hook", event=event, native_id=payload.get("session_id"), tool=tool)
    if protocol == "claude" and event == "Stop":
        current = store.member(member)
        if current["desired"] == "running" and current["state"] == "active":
            store.session(member, generation=current["generation"], state="idle")
    return {}
