"""Lifecycle shared by session drivers that own their native processes.

Owned connections cannot outlive their driver. Bindings are fixed before the
first session starts, and only a receipt issued by this driver instance
selects a live session.
"""
from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from ..activity import activity_engine
from ..model import JSON, Capability, OrchestrationError
from .process import decode_json, encode_json
from .telemetry_capture import TelemetrySource


def json_object(value: Any, name: str, code: str = "invalid_request") -> JSON:
    if not isinstance(value, dict):
        raise OrchestrationError(f"{name} must be a JSON object.", code, {"outcome": "unknown" if code == "provider_protocol" else "not_started"})
    # Do not retain caller-owned mutable data or non-JSON values.
    return decode_json(encode_json(value))


def native_identifier(value: Any, name: str = "session ID") -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise OrchestrationError(f"The session provider returned an invalid {name}.", "provider_protocol", {"outcome": "unknown"})
    return value


def application_tools(capabilities, callback) -> dict[str, Capability]:
    """Validate an application tool binding and detach its descriptors."""
    if not callable(callback) or not isinstance(capabilities, list) or any(not isinstance(item, Capability) for item in capabilities):
        raise OrchestrationError("Application tools require capability descriptors and a callback.", "invalid_configuration")
    return {item.name: deepcopy(item) for item in capabilities}


class OwnedSessionDriver(TelemetrySource):
    protocol: str

    def __init__(self):
        self._instance = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._sessions: dict = {}
        self._starting = 0
        self._closed = False
        self._observer = None
        self._application_tools: dict[str, Capability] = {}
        self._application_callback = None
        self._application_replay = None
        self._state_engine = activity_engine()

    @contextmanager
    def _before_start(self, binding: str):
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError(f"Bind {binding} before starting sessions.", "conflict")
            yield

    def _telemetry_binding(self):
        return self._before_start("telemetry")

    def bind_observer(self, observer) -> None:
        if not callable(observer):
            raise OrchestrationError("Session observer must be callable.", "invalid_configuration")
        with self._before_start("observers"):
            self._observer = observer

    def bind_state_engine(self, engine) -> None:
        """Select transition behavior before any native resource is created."""
        with self._before_start("state management"):
            self._state_engine = engine

    def bind_application_replay(self, callback) -> None:
        """Bind a read-only replay validator, separate from effectful dispatch."""
        if not callable(callback):
            raise OrchestrationError("Application replay validator must be callable.", "invalid_configuration")
        with self._before_start("replay validation"):
            self._application_replay = callback

    def _owned(self, session):
        """Return the live session named by this driver's own current receipt."""
        session = json_object(session, "Session handle")
        with self._lock:
            handle = session.get("handle")
            live = self._sessions.get(handle) if isinstance(handle, str) else None
        if (live is None or session.get("instance") != self._instance or session.get("protocol") != self.protocol
                or session.get("native_id") != live.native_id or session.get("incarnation") != live.incarnation):
            raise OrchestrationError("Session receipt is stale or not owned by this driver instance; explicitly resume its native ID if supported.",
                                     "session_unavailable", {"outcome": "not_started"})
        return live

    def _activity(self, live):
        """Under live.lock: aggregate turns, in-flight submissions and callbacks."""
        live.activity = live.activity_model.activity
        return live.activity

    def detach(self, session: JSON) -> JSON:
        # An owned pipe or channel cannot outlive its driver. End that connection
        # while retaining the resumable conversation receipt. External bridge
        # detach is deliberately different and does not terminate the native process.
        return {**self.stop(session), "detached": True, "ownership": "owned", "connection_state": "disconnected"}


class ApplicationToolDriver(OwnedSessionDriver):
    """An owned driver that exposes application actions as native tools."""

    def bind_application_tools(self, capabilities: list[Capability], callback) -> None:
        tools = application_tools(capabilities, callback)
        with self._before_start("application tools"):
            self._application_tools = tools
            self._application_callback = callback
