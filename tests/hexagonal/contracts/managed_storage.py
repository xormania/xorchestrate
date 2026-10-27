"""Managed evidence boundaries independent of the concrete file adapter."""

TERMINAL_PHASES = ("finished", "incomplete")

CONTROL_ACTIONS = ("stop", "collect", "close")

EXPORT_CASES = (
    {"name": "committed-report", "payload": {"status": "passed", "checks": ["one"]},
     "expected": {"status": "passed", "checks": ["one"]}},
    {"name": "credential-keys", "payload": {"status": "failed", "api_key": "private-value"},
     "expected": {"status": "failed", "api_key": "[redacted]"}},
    {"name": "credential-argv", "payload": {"command": ["tool", "--token", "private-value", "ok"]},
     "expected": {"command": ["tool", "--token", "[redacted]", "ok"]}},
)
