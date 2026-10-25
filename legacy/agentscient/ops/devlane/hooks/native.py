"""Vendor wire translations for the existing shared hooks policy (A-218)."""
from __future__ import annotations

import json
import shlex
from pathlib import Path

HARNESSES = ("claude", "codex", "grok", "agy")
MAX_BYTES = 1024 * 1024
READ_TOOLS = {
    "Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "ToolSearch",
    "read_file", "read_many_files", "list_directory", "list_dir", "grep",
    "grep_search", "find_by_name", "view_file", "view_image", "search_web",
    "read_url_content", "list_resources", "read_resource", "list_mcp_resources",
    "list_mcp_resource_templates", "read_mcp_resource", "command_status",
    "TaskOutput", "TaskGet", "TaskList", "list_permissions", "manage_inbox",
}
SHELL_TOOLS = {"Bash", "bash", "exec_command", "shell_command", "shell", "run_terminal_command", "run_command"}
DELEGATION_TOOLS = {"Agent", "Task", "Workflow", "spawn_agent", "spawn_agents_on_csv", "invoke_subagent", "define_subagent", "spawn_subagent", "workflow", "browser_subagent", "delegate_task", "create_subagent"}


def loads(raw):
    if len(raw.encode() if isinstance(raw, str) else raw) > MAX_BYTES:
        raise ValueError("oversize native input")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate native field")
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def text(value, label, *, empty=False):
    if not isinstance(value, str) or "\0" in value or (not empty and not value.strip()):
        raise ValueError(f"invalid {label}")
    return value


def alias(data, first, second, default=None):
    if first in data and second in data and data[first] != data[second]:
        raise ValueError(f"conflicting {first}/{second}")
    return data.get(first, data.get(second, default))


def patch_items(body):
    """Decode complete apply_patch records; every target participates in policy."""
    lines = text(body, "patch").splitlines()
    if lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("incomplete patch delimiters")
    result = []
    current = None
    for line in lines[1:-1]:
        if line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ")):
            operation, path = line[4:].split(": ", 1)
            text(path, "patch path")
            current = {"file_path": path, "content": "", "operation": operation}
            result.append(current)
        elif line.startswith("*** Move to: "):
            if current is None or current["operation"] != "Update File":
                raise ValueError("move without update")
            path = text(line[len("*** Move to: "):], "move target")
            result.append({"file_path": path, "content": "", "operation": "Move to"})
        elif current is None or (line.startswith("***") and line != "*** End of File"):
            raise ValueError("unknown patch record")
        elif current["operation"] == "Delete File":
            raise ValueError("delete record with unexpected body")
        elif not line.startswith(("+", "-", " ", "@@")) and line != "*** End of File":
            raise ValueError("invalid patch hunk")
        elif line.startswith("+"):
            current["content"] += line[1:] + "\n"
    if not result:
        raise ValueError("empty patch")
    return result


def final_reading(harness, payload, home, cwd, session):
    """Translate installed native transcripts; absence is not an empty reply."""
    from urllib.parse import quote
    path = alias(payload, "transcript_path", "transcriptPath")
    if not path and harness == "grok":
        path = str(Path(home) / "sessions" / quote(cwd, safe="") / session / "chat_history.jsonl")
    target = Path(text(path, "native transcript")).resolve(strict=True)
    if not target.is_relative_to(Path(home).resolve()) or session not in target.parts:
        raise ValueError("transcript outside bound native session")
    if target.stat().st_size > 8 * MAX_BYTES:
        raise ValueError("native transcript exceeds bounded reading")
    rows = [loads(line) for line in target.read_bytes().splitlines() if line.strip()]
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("malformed native transcript")
    last = rows[-1]
    if harness == "agy":
        if last.get("type") != "PLANNER_RESPONSE" or last.get("source") != "MODEL" or last.get("status") != "DONE" or last.get("tool_calls"):
            raise ValueError("final native response not complete")
    elif last.get("type") != "assistant":
        raise ValueError("final native response absent")
    return text(last.get("content"), "final response", empty=True)


def normalize(harness, payload, *, event=None, cwd=None, home=None):
    """Return canonical events; classification and permission stay in enforcement."""
    if harness not in HARNESSES or not isinstance(payload, dict):
        raise ValueError("unknown harness or malformed native object")
    if payload.get("toolInputTruncated") or payload.get("truncated"):
        raise ValueError("truncated native input")
    if harness == "grok" and "hookEventName" in payload:
        names = {"pre_tool_use": "PreToolUse", "post_tool_use": "PostToolUse", "session_start": "SessionStart", "stop": "Stop"}
        payload = {**payload, "hookEventName": names.get(payload["hookEventName"], payload["hookEventName"])}
    claimed_event = alias(payload, "hook_event_name", "hookEventName")
    if event and claimed_event and event != claimed_event:
        raise ValueError("conflicting native lifecycle event")
    event = event or claimed_event or "PreToolUse"
    if event not in {"PreToolUse", "PostToolUse", "Stop", "SessionStart", "PreInvocation", "PostInvocation"}:
        raise ValueError("unsupported lifecycle event")
    if harness == "agy":
        call = payload.get("toolCall", {})
        if not isinstance(call, dict):
            raise ValueError("malformed toolCall")
        tool, args = call.get("name", ""), call.get("args", {})
        paths = payload.get("workspacePaths", [])
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError("invalid workspace paths")
        directory = cwd or (args.get("Cwd") if isinstance(args, dict) else None) or (paths[0] if len(paths) == 1 else None)
        session = payload.get("conversationId")
        transcript = payload.get("transcriptPath")
    else:
        tool = alias(payload, "tool_name", "toolName", "")
        args = alias(payload, "tool_input", "toolInput", {})
        directory = payload.get("cwd") or cwd
        session = alias(payload, "session_id", "sessionId", "")
        transcript = alias(payload, "transcript_path", "transcriptPath")
    directory = text(directory, "working directory")
    if not Path(directory).is_absolute():
        raise ValueError("relative working directory")
    if cwd and not Path(directory).resolve().is_relative_to(Path(cwd).resolve()):
        raise ValueError("native cwd outside bound workspace")
    base = {"hook_event_name": event, "cwd": directory, "session_id": session or "",
            "native_tool": tool, "native_harness": harness}
    if transcript:
        base["transcript_path"] = text(transcript, "transcript")
    for first, second in (("last_assistant_message", "lastAssistantMessage"), ("tool_response", "toolResponse"), ("stop_hook_active", "stopHookActive")):
        value = alias(payload, first, second)
        if value is not None:
            base[first] = value
    for key in ("source", "stop_hook_active", "last_assistant_message", "tool_response"):
        if key in payload:
            base[key] = payload[key]
    if event == "Stop" and "last_assistant_message" not in base and home and harness in {"agy", "grok"}:
        base["last_assistant_message"] = final_reading(harness, payload, home, directory, session)
    if event in {"Stop", "SessionStart", "PreInvocation", "PostInvocation"}:
        return [base]
    if not isinstance(args, dict):
        raise ValueError("tool arguments must be an object")
    text(tool, "tool name")
    if tool == "Result":
        raise ValueError("reserved canonical tool name")
    def item(name, inp):
        return {**base, "tool_name": name, "tool_input": inp}
    if tool in SHELL_TOOLS:
        command = alias(args, "command", "cmd")
        if harness == "agy":
            command = args.get("CommandLine")
            directory = text(args.get("Cwd", directory), "command cwd")
        elif args.get("workdir"):
            directory = text(args["workdir"], "command cwd")
        base["cwd"] = directory
        if not Path(directory).is_absolute():
            raise ValueError("relative shell cwd")
        if cwd and not Path(directory).resolve().is_relative_to(Path(cwd).resolve()):
            raise ValueError("shell cwd outside bound workspace")
        return [item("Bash", {**args, "command": text(command, "command")})]
    if tool == "apply_patch":
        body = alias(args, "command", "patch", args.get("input"))
        return [item("Write" if x["operation"] == "Add File" else "Edit", x) for x in patch_items(body)]
    if tool in {"MultiEdit", "multi_replace_file_content"}:
        path = args.get("file_path", args.get("TargetFile"))
        edits = args.get("edits", args.get("ReplacementChunks"))
        if not isinstance(edits, list) or not edits:
            raise ValueError("missing edit batch")
        out = []
        for edit in edits:
            if not isinstance(edit, dict):
                raise ValueError("invalid batch item")
            target = text(edit.get("file_path", path), "batch target")
            replacement = text(edit.get("new_string", edit.get("ReplacementContent")), "replacement", empty=True)
            out.append(item("Edit", {"file_path": target, "new_string": replacement,
                                     "old_string": edit.get("old_string", edit.get("TargetContent", ""))}))
        return out
    if tool in {"Write", "Edit", "write_file", "create_file", "search_replace", "replace", "edit_file", "write_to_file", "replace_file_content", "NotebookEdit", "notebook_edit"}:
        path = args.get("file_path", args.get("target_file", args.get("path", args.get("TargetFile", args.get("notebook_path")))))
        writing = tool in {"Write", "write_file", "create_file", "write_to_file"}
        content = args.get("content", args.get("CodeContent")) if writing else args.get("new_string", args.get("new_source", args.get("ReplacementContent", args.get("replacement"))))
        return [item("Write" if writing else "Edit", {**args, "file_path": text(path, "file target"),
                     "content" if writing else "new_string": text(content, "file content", empty=True)})]
    if tool in DELEGATION_TOOLS:
        return [item("Agent", args)]
    if tool in {"write_stdin", "send_command_input", "manage_task"}:
        if tool == "manage_task":
            action = args.get("Action")
            if action in {"list", "status", "kill"}:
                return [item("Read" if action != "kill" else "Cancel", args)]
            if action != "send_input":
                raise ValueError("unknown task action")
            value = args.get("Input")
        else:
            value = args.get("chars", args.get("Input", args.get("input")))
        return [item("Continuation" if text(value, "continuation", empty=True) else "Read", args)]
    if tool.startswith("mcp__"):
        fields = tool.split("__", 2)
        if len(fields) != 3:
            raise ValueError("malformed MCP tool")
        return [item("MCP", {"server": fields[1], "name": fields[2], "arguments": args})]
    if harness == "claude" and tool == "StructuredOutput":
        # The job's envelope, returned to the harness; launch._envelope reads it (LD-136).
        return [item("Result", args)]
    if tool == "call_mcp_tool":
        return [item("MCP", {"server": args.get("ServerName"), "name": args.get("ToolName"), "arguments": args.get("Arguments")})]
    return [item("Read" if tool in READ_TOOLS else tool, args)]



def bind_command(command, cwd):
    """Bind the origin omitted from Codex's native hook wire (M-247)."""
    return "cd -- " + shlex.quote(text(cwd, "bound cwd")) + " && {\n" + text(command, "command") + "\n}"


def respond(harness, decision, event):
    """Render advisory hook output, retaining the legacy wire for internal callers."""
    if decision.get("advisory"):
        try:
            from .guidance import respond as advice
        except ImportError:
            from guidance import respond as advice
        output = advice({"context": decision.get("context", "")}, event)
        if harness == "agy":
            return {"decision": "allow", "reason": decision.get("context", "")}
        if event == "PreToolUse" and "updated_input" in decision:
            output.setdefault("hookSpecificOutput", {"hookEventName": event})["updatedInput"] = decision["updated_input"]
        return output
    if harness not in HARNESSES or decision.get("outcome") not in {"allow", "deny", "ask"}:
        raise ValueError("invalid policy decision")
    allowed = decision["outcome"] == "allow"
    reason = decision.get("reason", "")
    if decision["outcome"] == "ask":
        reason += " Owner decision pending; this adapter cannot supply the owner's approval."
    if event == "Stop" and not allowed and reason.startswith("hook-input:") and harness != "agy":
        return {"continue": False, "stopReason": reason}
    if harness == "agy":
        if event == "PreToolUse":
            return {"decision": "allow" if allowed else "deny", "reason": reason}
        if event == "Stop":
            return {"decision": "allow" if allowed else "continue", "reason": reason}
        if event == "PostInvocation" and not allowed:
            return {"terminationBehavior": "terminate"}
        return {}
    if event == "PreToolUse":
        output = {"hookEventName": event, "permissionDecision": "allow" if allowed else "deny", "permissionDecisionReason": reason}
        if allowed and "updated_input" in decision:
            output["updatedInput"] = decision["updated_input"]
        return {"hookSpecificOutput": output}
    if not allowed:
        return {"decision": "block", "reason": reason}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": decision.get("context", "")}}
