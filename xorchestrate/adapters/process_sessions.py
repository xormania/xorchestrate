"""Owned stdio process sessions with distinct conversation, connection and activity identities.

Shared live-session state and process ownership for native drivers (see
``codex`` and ``acp``) plus the generic JSON-RPC driver. Externally managed
conversations use the persistent bridge adapter. A saved backend receipt is not
a live connection. Submission results never establish native receipt, completion
or work acceptance.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..activity import ActivityState, NativeConnection
from ..model import JSON, OrchestrationError
from ..outcomes import before_effect
from ..schema import check_schema, validate
from .jsonrpc import JsonLineProcess
from .native_calls import DEFAULT_LIMIT, NativeCallLedger
from .process import command_options, encode_json, positive_integer
from .session_base import OwnedSessionDriver, json_object, native_identifier
from .telemetry_capture import capture_origin, observe_telemetry


@dataclass
class LiveSession:
    context: JSON
    native_id: str | None = None
    process: JsonLineProcess | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)
    incarnation: str = field(default_factory=lambda: uuid.uuid4().hex)
    activity: str = "idle"
    settings: JSON = field(default_factory=lambda: {"requested": {}, "resolved": {}, "observed": {}})
    events: list[JSON] = field(default_factory=list)
    calls: NativeCallLedger | None = None
    activity_model: ActivityState = field(default_factory=ActivityState)
    pending: Any = field(init=False)
    connection: NativeConnection = field(init=False)
    preidentity: list[JSON] = field(default_factory=list)
    completed: deque = field(default_factory=lambda: deque(maxlen=1024))
    seen_turns: deque = field(default_factory=lambda: deque(maxlen=1024))
    active_callbacks: Any = field(init=False)
    submissions: Any = field(init=False)
    unbound_completions: set = field(default_factory=set)

    def __post_init__(self):
        self.connection = NativeConnection(self.activity_model.engine, self.incarnation)
        self.pending = self.activity_model.mapping("prompt")
        self.active_callbacks = self.activity_model.set("callback")
        self.submissions = self.activity_model.set("submission")
        if self.calls is None:
            self.calls = NativeCallLedger(self.activity_model.engine)


class ProcessSessions(OwnedSessionDriver):
    protocol = "jsonrpc"
    jsonrpc = True
    resumable = False
    contract_version = "native-session-v1"
    application_callback_methods = ()

    def __init__(self, options: JSON):
        self.options = json_object(options, "Session options", "invalid_configuration")
        command_options(self.options)  # Validate configuration before the first effect.
        self.max_sessions = positive_integer(self.options, "max_sessions", 64)
        self.max_native_calls = positive_integer(self.options, "max_native_calls", DEFAULT_LIMIT)
        self.max_pending_messages = positive_integer(self.options, "max_pending_messages", 1024)
        if not isinstance(self.options.get("allow_configuration_change_on_resume", False), bool):
            raise OrchestrationError("allow_configuration_change_on_resume must be boolean.", "invalid_configuration")
        super().__init__()
        self.configuration_snapshot_id = hashlib.sha256(encode_json(self.options, sort_keys=True)).hexdigest()
        operational = {"timeout", "turn_timeout", "callback_timeout", "callback_workers", "shutdown_timeout",
                       "gateway_timeout", "gateway_workers", "observation_timeout", "allow_configuration_change_on_resume", "telemetry_drain_timeout"}
        material = {key: value for key, value in self.options.items() if key not in operational and not key.startswith("max_")}
        self.configuration_id = hashlib.sha256(encode_json({"protocol": self.protocol,
            "contract_version": self.contract_version, "options": material}, sort_keys=True)).hexdigest()

    def _observation(self, live: LiveSession, kind: str, **fields) -> JSON:
        event = {"kind": kind, "source": self.protocol, "native_id": live.native_id,
                 "incarnation": live.incarnation, "at": datetime.now(timezone.utc).isoformat(), **deepcopy(fields)}
        observe_telemetry(self._telemetry_observer, "native." + kind, event,
                          {**live.context, "native_id": live.native_id, "incarnation": live.incarnation},
                          secrets=self._telemetry_credentials())
        if self._observer and live.native_id is not None:
            self._observer(deepcopy(live.context), deepcopy(event))
        return event

    def _notification(self, live: LiveSession, method: str, params: JSON) -> None:
        params = json_object(params, "Native notification", "provider_protocol")
        if live.native_id is None:
            if len(live.preidentity) >= self.options.get("max_pending_messages", 1024):
                raise OrchestrationError("Too many notifications before native identity.", "provider_protocol")
            live.preidentity.append({"method": method, "params": params})
            return
        self._verify_identity(live, params)
        self._observation(live, "native_notification", method=method, payload=params)

    def _callback_context(self, live: LiveSession, action: str, call_id: str | None) -> JSON:
        return {**json_object(live.context, "Context"), "native_id": live.native_id,
                "incarnation": live.incarnation, "call_id": call_id, "action": action}

    def _verify_identity(self, live: LiveSession, params: JSON) -> None:
        for name in ("threadId", "sessionId", "session_id"):
            observed = params.get(name)
            if observed is not None and observed != live.native_id:
                raise OrchestrationError("Native session identity does not match the owned connection.", "session_identity")

    def _verified_request(self, live: LiveSession, params: Any) -> JSON:
        params = json_object(params, "Native request", "provider_protocol")
        if live.connection.state == "closed":
            raise OrchestrationError("Native callback belongs to a closed connection.", "session_unavailable")
        self._verify_identity(live, params)
        if live.native_id is None:
            raise OrchestrationError("The native session has not established its identity.", "session_identity")
        return params

    def _request(self, live: LiveSession, method: str, params: Any) -> JSON:
        """Reject native requests that the protocol adapter does not translate."""
        with before_effect():
            self._verified_request(live, params)
            raise OrchestrationError("This native request is not supported.", "unsupported_method")

    def _new(self, specification: JSON) -> LiveSession:
        with self._lock:
            if self._closed:
                raise OrchestrationError("Session driver is closed.", "session_unavailable", {"outcome": "not_started"})
            if len(self._sessions) + self._starting >= self.max_sessions:
                raise OrchestrationError("Configured concurrent session limit reached.", "session_capacity", {"outcome": "not_started"})
            self._starting += 1
        try:
            context = json_object(specification.get("context", {}), "Session context")
            live = LiveSession(context=context, activity_model=ActivityState(self._state_engine),
                                calls=NativeCallLedger(self._state_engine, self.max_native_calls))
            live.process = JsonLineProcess(self.options, lambda method, params: self._request(live, method, params), jsonrpc=self.jsonrpc,
                                           notification_handler=lambda method, params: self._notification(live, method, params),
                                           telemetry_observer=self._telemetry_observer, telemetry_context=capture_origin({**context, "incarnation": live.incarnation}),
                                           application_callback_methods=self.application_callback_methods if self._application_callback is not None else ())
            return live
        except BaseException:
            with self._lock:
                self._starting -= 1
            raise

    def _publish(self, live: LiveSession) -> JSON:
        handle = uuid.uuid4().hex
        queued, live.preidentity = live.preidentity, []
        for packet in queued:
            self._notification(live, packet["method"], packet["params"])
        with self._lock:
            if self._closed:
                raise OrchestrationError("Session driver closed while starting the session.", "session_unavailable")
            live.connection.transition("connect")
            self._sessions[handle] = live
        receipt = {"instance": self._instance, "handle": handle, "native_id": live.native_id,
                   "protocol": self.protocol, "resumable": self.resumable, "incarnation": live.incarnation,
                   "ownership": "owned", "connection_state": "connected", "activity": live.activity,
                   "configuration_id": self.configuration_id, "configuration_snapshot_id": self.configuration_snapshot_id,
                   "settings": deepcopy(live.settings)}
        self._observation(live, "session_started", settings=live.settings, configuration_id=self.configuration_id,
                          ownership="owned", connection_state="connected")
        return receipt

    def _start(self, specification: JSON, handshake: Callable) -> JSON:
        specification = json_object(specification, "Session specification")
        live = self._new(specification)
        try:
            handshake(live, specification)
            return self._publish(live)
        except BaseException:
            live.connection.transition("close")
            live.process.close()
            if hasattr(live, "gateway"):
                live.gateway.close()
            with self._lock:
                for key, current in list(self._sessions.items()):
                    if current is live:
                        self._sessions.pop(key)
            raise
        finally:
            with self._lock:
                self._starting -= 1

    def _get(self, session: JSON) -> LiveSession:
        live = self._owned(session)
        if live.connection.state == "closed":
            raise OrchestrationError("Session handle does not identify an open connection.", "session_unavailable", {"outcome": "not_started"})
        return live

    def _notifications(self, live: LiveSession) -> list[JSON]:
        result = live.process.poll_notifications()
        try:
            for packet in result:
                self._verify_identity(live, json_object(packet.get("params", {}), "Native notification", "provider_protocol"))
        except OrchestrationError:
            live.process.close()
            raise
        return result

    def poll(self, session: JSON) -> list[JSON]:
        live = self._get(session)
        result = self._notifications(live)
        with live.lock:
            extra, live.events = live.events, []
        return [*result, *extra]

    def status(self, session: JSON) -> JSON:
        live = self._get(session)
        with live.lock:
            if live.process.process.poll() is not None:
                live.connection.transition("disconnect")
        return {"native_id": live.native_id, "incarnation": live.incarnation,
                "connection_state": live.connection.state,
                "activity": live.activity, "settings": deepcopy(live.settings), "ownership": "owned"}

    def reconnect(self, source: JSON, specification: JSON) -> JSON:
        resume = getattr(self, "resume", None)
        if not callable(resume):
            raise OrchestrationError("This adapter does not support reconnect.", "unsupported_operation", {"outcome": "not_started"})
        return resume(source, specification)

    def deliver(self, session: JSON, message: JSON) -> JSON:
        return self.send(session, message)

    def stop(self, session: JSON) -> JSON:
        live = self._get(session)
        live.connection.transition("close")
        live.process.close()
        with self._lock:
            self._sessions.pop(session["handle"], None)
        self._observation(live, "session_disconnected", ownership="owned")
        return {"stopped": True, "native_id": live.native_id, "incarnation": live.incarnation}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for live in sessions:
            live.connection.transition("close")
            live.process.close()
            self._observation(live, "session_disconnected", ownership="owned")
        self._close_telemetry()


class JSONRPCSessionDriver(ProcessSessions):
    """Bridge contract: start -> session_id; send -> object; poll -> events.

    The configured executable reads JSON-RPC lines from stdin. Method names are
    configurable through ``methods``. ``initialize`` optionally supplies a
    method and params. No command, environment, or executable comes from a
    session specification. The entire specification is passed as ordinary data.
    """

    def __init__(self, options: JSON):
        super().__init__(options)
        methods = json_object(self.options.get("methods", {}), "Session methods", "invalid_configuration")
        self.methods = {operation: methods.get(operation, f"session/{operation}") for operation in ("start", "send", "poll", "stop")}
        if any(not isinstance(value, str) or not value for value in self.methods.values()):
            raise OrchestrationError("Session methods must be nonempty strings.", "invalid_configuration")
        self.initialize = self.options.get("initialize")
        if self.initialize is not None:
            self.initialize = json_object(self.initialize, "Session initialization", "invalid_configuration")
            if not isinstance(self.initialize.get("method"), str) or not self.initialize["method"]:
                raise OrchestrationError("Session initialization needs a method.", "invalid_configuration")
            json_object(self.initialize.get("params", {}), "Session initialization params", "invalid_configuration")

    def start(self, specification: JSON) -> JSON:
        def handshake(live, spec):
            if self.initialize is not None:
                live.process.request(self.initialize["method"], self.initialize.get("params", {}))
            result = json_object(live.process.request(self.methods["start"], spec), "Session start result", "provider_protocol")
            live.native_id = native_identifier(result.get("session_id"))
        return self._start(specification, handshake)

    def send(self, session: JSON, message: JSON) -> JSON:
        message = json_object(message, "Session message")
        live = self._get(session)
        with live.lock:
            return json_object(live.process.request(self.methods["send"], {"session_id": live.native_id, "message": message}), "Session send result", "provider_protocol")

    def poll(self, session: JSON) -> list[JSON]:
        live = self._get(session)
        with live.lock:
            result = json_object(live.process.request(self.methods["poll"], {"session_id": live.native_id}), "Session poll result", "provider_protocol")
            events = result.get("events")
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
                raise OrchestrationError("Session poll result must contain an events array of objects.", "provider_protocol", {"outcome": "unknown"})
            return [*self._notifications(live), *events]

    def stop(self, session: JSON) -> JSON:
        live = self._get(session)
        with live.lock:
            try:
                result = json_object(live.process.request(self.methods["stop"], {"session_id": live.native_id}), "Session stop result", "provider_protocol")
            finally:
                super().stop(session)
            return {"stopped": True, "native_id": live.native_id, "result": result}


class NativeSessions(ProcessSessions):
    """Native start parameters require a delegation from trusted composition."""

    def __init__(self, options: JSON):
        super().__init__(options)
        self.request_parameters = self.options.get("request_parameters", {"type": "object", "additionalProperties": False})
        check_schema(self.request_parameters)

    def _prepare_start(self, specification: JSON, reserved: tuple[str, ...]) -> tuple[JSON, JSON]:
        specification = json_object(specification, "Session specification")
        if "resume" in specification:
            raise OrchestrationError("Resume requires an authorized source session, not a caller-supplied native ID.", details={"outcome": "not_started"})
        requested = json_object(specification.get("parameters", {}), "Native session parameters")
        try:
            validate(requested, self.request_parameters)
        except OrchestrationError as exc:
            exc.details["outcome"] = "not_started"
            raise
        if any(name in requested for name in reserved):
            raise OrchestrationError("Native session identity and dynamic tools are owned by the configured driver.", details={"outcome": "not_started"})
        return specification, requested

    def start(self, specification: JSON) -> JSON:
        return self._launch(specification, None)

    def resume(self, session: JSON, specification: JSON) -> JSON:
        """Resume a backend selected and authorized by the application."""
        session = json_object(session, "Resume source")
        if not self.resumable:
            raise OrchestrationError("Session resumption is not enabled for this driver.", "unsupported_operation", {"outcome": "not_started"})
        native_id = session.get("native_id")
        if session.get("protocol") != self.protocol or not isinstance(native_id, str) or not native_id or len(native_id) > 4096:
            raise OrchestrationError("Resume source does not identify a native session for this protocol.", "session_identity", {"outcome": "not_started"})
        specification = json_object(specification, "Session specification")
        previous = session.get("configuration_id")
        if previous and previous != self.configuration_id and not self.options.get("allow_configuration_change_on_resume", False):
            raise OrchestrationError("Configured driver changed since the measured session; explicit trusted resume policy is required.",
                                     "configuration_changed", {"outcome": "not_started", "previous": previous, "current": self.configuration_id})
        if "parameters" not in specification and session.get("settings", {}).get("requested"):
            specification["parameters"] = deepcopy(session["settings"]["requested"])
        result = self._launch(specification, native_id)
        if session.get("instance") == self._instance:
            try:
                self._get(session)
            except OrchestrationError:
                pass  # A previous detach/stop already released this connection.
            else:
                self.stop(session)
                result["replaced_connection"] = {"incarnation": session.get("incarnation"), "state": "closed"}
        if previous and previous != self.configuration_id:
            result["configuration_change"] = {"from": previous, "to": self.configuration_id,
                                               "authorized_by": "trusted_configuration"}
        return result


