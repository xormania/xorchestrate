"""Render repository diagnostics without making a permission decision.

CI owns acceptance. Native harness permissions and worker isolation remain
owned by their runtimes; a diagnostic neither grants nor denies permission.
"""

from __future__ import annotations


def message(value):
    if not isinstance(value, dict):
        return ""
    output = value.get("hookSpecificOutput", {})
    return str(output.get("additionalContext") or output.get("permissionDecisionReason")
               or value.get("reason") or value.get("context") or "")


def respond(value, event="PreToolUse"):
    text = message(value)
    if not text:
        return {}
    text = "Diagnostic (advisory): " + text
    if event == "Stop":
        return {"systemMessage": text}
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
