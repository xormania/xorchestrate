"""Independent operational outcomes around an authoritative proof result."""

FINALIZATION_CASES = (
    ("passed", True, False, "completed", None, "passed"),
    ("failed", True, False, "completed", None, "failed"),
    ("incomplete", True, False, "completed", None, "failed"),
    ("passed", False, False, "completed", None, "failed"),
    ("passed", True, True, "completed", None, "failed"),
    ("passed", True, False, "error", None, "failed"),
    ("passed", True, False, "partial", None, "failed"),
    ("passed", True, False, "completed", "operator_stop", "failed"),
)
