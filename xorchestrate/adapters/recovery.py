"""Built-in recovery policies: which interrupted sessions to recover automatically after a hard restart."""

from __future__ import annotations

from ..model import OrchestrationError


def _no_options(options, name):
    if options:
        raise OrchestrationError(f"The {name} recovery policy takes no options.", "invalid_configuration")


class ReconnectRecovery:
    """Recover every interrupted session whose driver can reconnect (the default)."""

    def __init__(self, options=None):
        _no_options(options, "reconnect")

    def select(self, interrupted):
        return [session.session_id for session in interrupted if session.can_reconnect]


class ManualRecovery:
    """Recover nothing automatically: detect and report, and leave recover to an explicit call."""

    def __init__(self, options=None):
        _no_options(options, "manual")

    def select(self, interrupted):
        return []
