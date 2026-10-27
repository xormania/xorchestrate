"""Public managed workflow outcomes, independent of process implementation."""

REPEAT_PROPERTIES = ("suite", "mode", "timeout", "check_timeout", "drivers", "families")
TERMINAL_PHASES = ("finished", "incomplete")
CONTROL_SCOPES = ("stop", "run-deadline")
