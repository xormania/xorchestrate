"""Compose repository diagnostics; harness adapters contain no predicates."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[2]
TOOLS = SOURCE / "ops/conductor/tools"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Paths are chosen from this installed source, never from the caller's cwd.
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(HERE / "claude"))
import instrument  # noqa: E402

native = load("shared_hook_native", HERE / "native.py")
matrix = load("shared_hook_matrix", HERE / "claude/matrix.py")
engine = load("shared_hook_engine", HERE / "claude/engine.py")
engine.matrix = matrix
unsafe = load("shared_hook_unsafe", HERE / "claude/unsafe-command.py")
context = load("shared_hook_context", HERE / "claude/context-precheck.py")



def decision(outcome="allow", reason="", context=""):
    return {"outcome": outcome, "reason": reason, "context": context}


def refuse(found, needed="repair the shared guard binding; inspect co_doc.py"):
    return decision("deny", f"hook-input: expected complete verified enforcement; found {found}; needed {needed}")


def converted(value):
    if value is None:
        return decision()
    if not isinstance(value, dict):
        raise ValueError("malformed evaluator response")
    if value.get("decision") == "block":
        return decision("deny", native.text(value.get("reason"), "refusal reason"))
    spec = value.get("hookSpecificOutput")
    if not isinstance(spec, dict):
        raise ValueError("missing evaluator output")
    outcome = spec.get("permissionDecision")
    if outcome in {"deny", "ask"}:
        return decision(outcome, native.text(spec.get("permissionDecisionReason"), "refusal reason"))
    if "additionalContext" in spec:
        return decision(context=native.text(spec["additionalContext"], "context", empty=True))
    raise ValueError("unknown evaluator decision")


@contextmanager
def working_directory(path):
    previous = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(previous)


def mcp_allowed(inp, servers):
    """Use existing catalogues/schemas; domain mutations still authorize themselves."""
    server, name, args = inp.get("server"), inp.get("name"), inp.get("arguments")
    if server not in servers or not isinstance(args, dict):
        return False
    if server == "coordination":
        owner = load("shared_coordination_surface", TOOLS / "coordination_mcp.py")
        tool = owner.BY_NAME.get(name)
        if tool is None:
            return False
        owner.check_arguments(tool, args)
        return True
    if server in {"agentscient-lane", "agentscient_lane"}:
        owner = load("shared_lane_surface", TOOLS / "lane_mcp.py")
        # Names/schema are derived from the existing owner, not duplicated here.
        rows = owner._schemas({"version": 3})
        return any(row["name"] == name and row.get("annotations", {}).get("readOnlyHint") is True for row in rows)
    return False


def inspect(payload, *, servers=()):
    """One complete decision. Guard failures cannot be confused with predicate None."""
    token = None
    try:
        if not isinstance(payload, dict):
            raise ValueError("native object absent")
        event = payload.get("hook_event_name")
        if event not in engine.EVENTS:
            raise ValueError("unsupported lifecycle coverage")
        cwd = native.text(payload.get("cwd"), "cwd")
        if not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError("unavailable working directory")
        # Source config/registry are authority. Payload and environment cannot
        # replace them with a worker-controlled checkout's policy.
        conf = instrument._laneconf().parse_file(SOURCE / "ops/lane.conf")
        token = matrix.GUARD_CONTEXT.set({"conf": conf, "state": str(instrument.state_root()), "instrument": instrument})
        registry = native.loads((HERE / "boundaries.json").read_bytes())
        rows = registry.get("rows")
        if registry.get("schema") != "agentscient.boundaries/2" or not isinstance(rows, list) or not rows:
            raise ValueError("invalid installed registry")
        if event in {"PreToolUse", "PostToolUse"}:
            tool, inp = payload.get("tool_name"), payload.get("tool_input")
            if not isinstance(inp, dict):
                raise ValueError("invalid tool arguments")
            if tool == "Bash":
                native.text(inp.get("command"), "command")
                if inp.get("tty") or inp.get("RunPersistent") or inp.get("env"):
                    return refuse("unbound interactive shell or environment", "use a finite guarded command")
            elif tool in {"Write", "Edit", "MultiEdit"}:
                native.text(inp.get("file_path"), "edit path")
                if not any(isinstance(inp.get(key), str) for key in ("content", "new_string")):
                    raise ValueError("missing edit content")
            elif tool == "MCP":
                if not mcp_allowed(inp, servers):
                    return refuse("unbound MCP operation", "bind the existing server catalogue and effective core authority")
            elif tool == "Continuation":
                return refuse("unbound continuation input", "use a finite guarded command; input is not a fresh shell action")
            elif tool not in {"Read", "Cancel", "Agent", "Task", "Workflow", "Result"}:
                return refuse("unknown native tool", "declare and verify its translation in the shared guard")
            if event == "PostToolUse" and any(row.get("engine") and row.get("event") == event for row in rows) and "tool_response" not in payload:
                return refuse("unavailable post-tool result", "supply the verified native result reading")
        elif event == "Stop" and not isinstance(payload.get("last_assistant_message"), str):
            return refuse("unavailable final response", "supply a verified native transcript reading")
        with working_directory(cwd):
            if event == "PreToolUse":
                if tool == "Bash":
                    out = converted(unsafe.decide(payload))
                    if out["outcome"] != "allow":
                        return out
                if tool in {"Bash", "Write", "Edit", "MultiEdit"}:
                    out = converted(matrix.decide(payload))
                    if out["outcome"] != "allow":
                        return out
            out = converted(engine.decide(payload, rows=rows)[0])
            if out["outcome"] != "allow":
                return out
            if event == "PreToolUse" and tool == "Bash":
                checked = converted(context.decide(payload, strict=True))
                if checked["outcome"] != "allow":
                    return checked
            return out
    except Exception as exc:  # noqa: BLE001 - unavailable input becomes an advisory finding
        return refuse(type(exc).__name__ + ": " + str(exc)[:240])
    finally:
        if token is not None:
            matrix.GUARD_CONTEXT.reset(token)


def evaluate(payload, *, servers=()):
    """Diagnostics guide work; a failed diagnostic never becomes a work gate."""
    finding = inspect(payload, servers=servers)
    return decision(context=finding.get("reason") or finding.get("context", ""))


def mcp_only_decision(harness, payload):
    """Selected profile's tool boundary, separate from advisory repo diagnostics.

    Inspect the original call before normalizing shell/patch payloads. Grok's
    discovery wrapper is read-only; its execution wrapper must name this entry.
    All unknown native project tools stay closed, including future additions.
    """
    import dev_mcp

    tool = native.alias(payload, "tool_name", "toolName")
    arguments = native.alias(payload, "tool_input", "toolInput", {})
    if not isinstance(tool, str) or not isinstance(arguments, dict):
        raise ValueError("MCP-only profile requires a named tool with object arguments")
    controls = {"claude": {"EndConversation", "StructuredOutput"},
                "codex": {"update_plan", "request_user_input"}, "grok": {"search_tool"}}
    if tool in controls.get(harness, ()):
        return decision()
    if harness == "grok" and tool == "use_tool":
        if set(arguments) != {"tool_name", "tool_input"}:
            raise ValueError("MCP-only use_tool requires one target and its arguments")
        tool, arguments = arguments["tool_name"], arguments["tool_input"]
    prefixes = ["mcp__coordination__"]
    if harness == "grok":
        prefixes.append("coordination__")
    for spec in dev_mcp.TOOLS:
        if tool in [prefix + spec["name"] for prefix in prefixes]:
            dev_mcp.arguments_for(spec, arguments)
            return decision()
    return decision("deny", "MCP-only profile: use the development entry's capability_search, "
                    "capability_describe and capability_invoke for project operations.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=native.HARNESSES, required=True)
    parser.add_argument("--event", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--mcp-only", action="store_true")
    args = parser.parse_args(argv)
    result = refuse("unavailable guard")
    try:
        profiles = load("shared_hook_profiles", HERE / "profiles.py")
        binding = profiles.read_binding(args.binding)
        profiles.validate(binding)
        if args.mcp_only != binding.get("mcp_only", False):
            raise ValueError("MCP-only selection differs from its pinned helper")
        if binding["harness"] != args.harness:
            raise ValueError("harness binding mismatch")
        raw = native.loads(sys.stdin.buffer.read(native.MAX_BYTES + 1))
        if args.mcp_only and args.event == "PreToolUse":
            result = mcp_only_decision(args.harness, raw)
            if result["outcome"] == "allow":
                profiles.initialize(binding, raw, instrument=instrument)
            print(json.dumps(native.respond(args.harness, result, args.event)))
            return 0
        profiles.initialize(binding, raw, instrument=instrument)
        items = native.normalize(args.harness, raw, event=args.event, cwd=binding["cwd"], home=binding["home"])
        rewritten = None
        if args.harness == "codex" and args.event == "PreToolUse" and len(items) == 1 and items[0].get("tool_name") == "Bash":
            rewritten = native.bind_command(items[0]["tool_input"]["command"], binding["cwd"])
            items[0]["cwd"] = binding["cwd"]
        findings = []
        for payload in items:
            finding = evaluate(payload, servers=binding.get("servers", ()))
            if finding.get("context"):
                findings.append(finding["context"])
        result = decision(context="\n".join(dict.fromkeys(findings)))
        if rewritten:
            result["updated_input"] = {"command": rewritten}
    except Exception as exc:  # noqa: BLE001 - native hooks must answer even on a broken binding
        result = refuse(type(exc).__name__ + ": " + str(exc)[:240])
    result["context"] = result.get("context") or result.get("reason", "")
    result["advisory"] = not (args.mcp_only and args.event == "PreToolUse")
    print(json.dumps(native.respond(args.harness, result, args.event)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
