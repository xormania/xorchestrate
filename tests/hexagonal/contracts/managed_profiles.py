"""Declared profile variants independent of configuration implementations."""

INVALID_PROFILES = (
    ({"unknown_setting": True}, "Unknown profile fields"),
    ({"schema_version": 2}, "schema_version"),
    ({"timeout": 0}, "timeout"),
    ({"check_timeout": True}, "check_timeout"),
    ({"run_timeout": -1}, "run_timeout"),
    ({"shutdown_grace": float("inf")}, "shutdown_grace"),
    ({"drivers": ["same", "same"]}, "duplicate"),
    ({"families": "framework"}, "families"),
    ({"suite": "static", "families": ["framework"]}, "families"),
)
