"""Client driver attaching application sessions to an operator-owned bridge host.

Stopping a bridged session detaches its connection; it never terminates the
host's native conversation. See ``bridge_host`` for the host side.
"""
from __future__ import annotations

import socket
import threading
import uuid
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from .bridge_transport import Peer, bridge_error, copy_json, fifo_connect, json_digest


class BridgeSessionDriver:
    """Attach to a trusted operator bridge; stopping means detaching, never kill."""

    def __init__(self, options):
        self.options = copy_json(options)
        endpoint = self.options.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise bridge_error("A configured bridge endpoint is required.", "invalid_configuration")
        self.endpoint = str(Path(endpoint).expanduser().absolute())
        self.transport = self.options.get("transport", "unix")
        if self.transport not in {"unix", "fifo"}:
            raise bridge_error("Bridge transport must be unix or fifo.", "invalid_configuration")
        self._instance = uuid.uuid4().hex
        self._sessions = {}
        self._capabilities = ()
        self._callback = None
        self._application_replay = None
        self._observer = None
        self._lock = threading.RLock()
        self._closed = False

    def bind_application_tools(self, capabilities, callback):
        if self._sessions:
            raise bridge_error("Bind application tools before attaching.", "conflict")
        self._capabilities, self._callback = tuple(capabilities), callback

    def bind_application_replay(self, callback):
        if self._sessions:
            raise bridge_error("Bind replay validation before attaching.", "conflict")
        if not callable(callback):
            raise bridge_error("Application replay validator must be callable.", "invalid_configuration")
        self._application_replay = callback

    def bind_observer(self, observer):
        if self._sessions:
            raise bridge_error("Bind observations before attaching.", "conflict")
        self._observer = observer

    def _incoming(self, local, method, params):
        if method == "observation":
            observation = params.get("observation", {})
            receipt = local.get("receipt")
            if receipt is None or observation.get("native_id") != receipt["native_id"] or observation.get("incarnation") != receipt["incarnation"] or observation.get("host_id") != receipt["host_id"]:
                raise bridge_error("Observation does not match this bridge attachment.", "session_identity")
            self._emit_observation(local, observation)
            return {"retained": True}
        if method != "application_tool" or self._callback is None:
            raise bridge_error("No application callback is bound.", "unsupported_operation")
        context = params.get("context", {})
        receipt = local.get("receipt")
        if receipt is None or context.get("native_id") != receipt["native_id"] or context.get("incarnation") != receipt["incarnation"] or context.get("host_id") != receipt["host_id"]:
            raise bridge_error("Callback does not match this bridge attachment.", "session_identity")
        callback = self._application_replay if params.get("replay_only") else self._callback
        if callback is None:
            raise bridge_error("Application replay validation is unavailable.", "stale_native_result", "unknown")
        return callback(params["action"], copy_json(params["arguments"]), copy_json(context), params.get("idempotency_key"))

    def _emit_observation(self, local, observation):
        with self._lock:
            if observation["sequence"] in local["observed"]:
                return
            if self._observer:
                self._observer(deepcopy(local["context"]), deepcopy(observation))
            local["observed"].add(observation["sequence"])

    def _attach(self, specification, source=None):
        specification = copy_json(specification)
        if not isinstance(specification, dict) or set(specification) - {"context"}:
            raise bridge_error("Bridge attachment accepts trusted application context only; configure the host to select native settings.", "invalid_arguments")
        if self._closed:
            raise bridge_error("Bridge driver is closed.")
        if source is not None and (source.get("bridge_endpoint") != self.endpoint or source.get("bridge_transport") != self.transport):
            raise bridge_error("Resume source belongs to another configured endpoint.", "session_identity")
        connection = None
        try:
            if self.transport == "fifo":
                connection = fifo_connect(self.endpoint)
            else:
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(self.options.get("timeout", 30))
                connection.connect(self.endpoint)
                connection.settimeout(None)
        except OSError as exc:
            if connection is not None:
                connection.close()
            raise bridge_error("The configured bridge endpoint is unavailable.") from exc
        local = {"context": copy_json(specification.get("context", {})), "cursor": 0, "observed": set()}
        peer = Peer(connection, lambda method, params: self._incoming(local, method, params), self.options)
        try:
            receipt = peer.request("attach", {"context": local["context"], "source": source,
                "catalog_identity": json_digest([asdict(c) for c in self._capabilities])})
            if not isinstance(receipt, dict) or not receipt.get("native_id") or not receipt.get("incarnation") or receipt.get("ownership") != "external":
                raise bridge_error("Host returned an invalid attachment receipt.", "bridge_protocol")
            handle = uuid.uuid4().hex
            receipt.update({"instance": self._instance, "handle": handle, "bridge_endpoint": self.endpoint, "bridge_transport": self.transport})
            local.update({"receipt": deepcopy(receipt), "peer": peer})
            with self._lock:
                self._sessions[handle] = local
            peer.request("activate", {"incarnation": receipt["incarnation"]})
            return receipt
        except Exception:
            peer.close()
            raise

    def start(self, specification):
        return self.attach(specification)

    def attach(self, specification):
        return self._attach(specification)

    def resume(self, source_backend, specification):
        return self._attach(specification, copy_json(source_backend))

    def reconnect(self, source_backend, specification):
        return self.resume(source_backend, specification)

    def _get(self, session):
        with self._lock:
            local = self._sessions.get(session.get("handle"))
            if local is None or session != local["receipt"] or session.get("instance") != self._instance:
                raise bridge_error("Backend receipt does not identify this bridge connection.", "session_unavailable")
            return local

    def _call(self, session, method, **params):
        local = self._get(session)
        result = local["peer"].request(method, {"incarnation": session["incarnation"], "operation_id": uuid.uuid4().hex, **params})
        if method in {"send", "deliver"} and isinstance(result, dict) and "incarnation" in result:
            if result.get("native_id") != session["native_id"]:
                raise bridge_error("Native submission changed conversation identity.", "session_identity", "unknown")
            result = {**result, "native_incarnation": result["incarnation"], "incarnation": session["incarnation"], "host_id": session["host_id"]}
        return result

    def send(self, session, message):
        return self._call(session, "send", message=copy_json(message))

    def deliver(self, session, message):
        return self._call(session, "deliver", message=copy_json(message))

    def poll(self, session):
        local = self._get(session)
        native = self._call(session, "poll")
        observed = self._call(session, "events", after=local["cursor"])
        events = observed["events"]
        if events and events[0]["sequence"] != local["cursor"] + 1:
            raise bridge_error("Bridge observation history has a retention gap.", "evidence_gap", "unknown")
        for observation in events:
            # Historical old-lease events remain inspectable but are never
            # forwarded to the new application as trusted current observations.
            if observation.get("incarnation") == session["incarnation"] and self._observer:
                self._emit_observation(local, observation)
        local["cursor"] = observed["last_sequence"]
        return [*native, *({"kind": "bridge_observation", "observation": event} for event in events)]

    def inspect(self, session):
        return self._call(session, "inspect")

    def detach(self, session):
        local = self._get(session)
        try:
            return self._call(session, "detach")
        finally:
            local["peer"].close()
            with self._lock:
                self._sessions.pop(session["handle"], None)

    def stop(self, session):
        return self.detach(session)

    def close(self):
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for local in sessions:
            # Socket close cannot terminate or stop the host-owned native process.
            local["peer"].close()
