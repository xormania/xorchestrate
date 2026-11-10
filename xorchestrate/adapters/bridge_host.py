"""A local, operator-owned native session host with replaceable client leases.

The Unix socket is a local trust boundary (0600, same-user peer check). It does
not attach to arbitrary PIDs. The host owns its native driver; attached clients
own only a connection. A signed host receipt permits reconnecting the same
conversation after a client restart without accepting caller-created identities.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import stat
import tempfile
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from ..activity import activity_engine
from ..model import OrchestrationError, canonical_digest, public_error
from ..outcomes import before_effect, effect_outcome
from ..ports import ApplicationToolSessionDriver, SessionDriver
from .bridge_transport import FifoListener, Peer, bridge_error, copy_json, positive_option
from .local_socket import OwnerSocketListener
from .process import decode_json


def _call_binding(native_context):
    return ("native_call", json.dumps([native_context.get("native_id"), native_context.get("incarnation"),
        native_context.get("call_id")], separators=(",", ":")))


class SessionBridgeHost:
    """Keep one configured native conversation alive across client attachments."""

    def __init__(self, driver: SessionDriver, endpoint, specification=None, capabilities=(), options=None, *, state_engine=None):
        self.driver = driver
        self.state_engine = state_engine if state_engine is not None else activity_engine()
        bind_state = getattr(driver, "bind_state_engine", None)
        if callable(bind_state):
            bind_state(self.state_engine)
        self.endpoint = Path(endpoint).expanduser().absolute()
        self.options = copy_json(options or {})
        state_path = self.options.get("state_path")
        self.state_path = Path(state_path).expanduser().absolute() if state_path else None
        self.transport = self.options.get("transport", "unix")
        if self.transport not in {"unix", "fifo"}:
            raise bridge_error("Bridge transport must be unix or fifo.", "invalid_configuration")
        self.max_events = positive_option(self.options, "max_events", 10000)
        self.max_connections = positive_option(self.options, "max_connections", 64)
        self.host_id = uuid.uuid4().hex
        self._secret = os.urandom(32)
        self._lock = threading.RLock()
        self._peers = set()
        self._active = None
        self._active_ready = False
        self._lease = None
        self._context = {}
        self._events = []
        self._sequence = 0
        self._effects = {}
        self._bindings = {}
        self._effect_context = threading.local()
        self._closed = False
        self._outgoing = queue.Queue(maxsize=self.max_events)
        self.capabilities = tuple(capabilities)
        self.catalog_identity = canonical_digest([asdict(c) for c in self.capabilities])
        if self.capabilities:
            if not callable(getattr(driver, "bind_application_tools", None)):
                raise bridge_error("Native driver cannot expose configured application tools.", "invalid_configuration")
            tools = cast(ApplicationToolSessionDriver, driver)
            tools.bind_application_tools(list(self.capabilities), self._callback)
            bind_replay = getattr(tools, "bind_application_replay", None)
            if callable(bind_replay):
                bind_replay(self._replay_callback)
        observer = getattr(driver, "bind_observer", None)
        if callable(observer):
            observer(self._observe)
        saved = self._load_state()
        if saved:
            resume = getattr(driver, "resume", None)
            if not callable(resume):
                raise bridge_error("Configured native driver cannot recover the saved host conversation.", "unsupported_operation")
            self.host_id = saved["host_id"]
            self._secret = bytes.fromhex(saved["secret"])
            self._effects = saved["effects"]
            for key, effect in tuple(self._effects.items()):
                if effect["status"] == "pending":
                    self._transition_effect(key, "recover", {
                        "recovery_reason": "host_restarted_before_effect_outcome"})
            self._events = saved["events"]
            self._sequence = saved["sequence"]
            self._bindings = {(field, value): lease for field, value, lease in saved["bindings"]}
            self.backend = resume(deepcopy(saved["backend"]), copy_json(specification or {}))
            if self.backend.get("native_id") != saved["backend"].get("native_id"):
                driver.close()
                raise bridge_error("Recovered native conversation identity changed.", "session_identity", "unknown")
        else:
            self.backend = driver.start(copy_json(specification or {}))
        self.configuration_id = self.backend["configuration_id"]
        self._socket = None
        try:
            self.endpoint.parent.mkdir(parents=True, exist_ok=True)
            # Never unlink an operator's existing socket or unrelated file.
            if self.transport == "fifo":
                self._socket = FifoListener(self.endpoint)
            else:
                self._socket = OwnerSocketListener(self.endpoint, 16)
            self._save_state()
        except Exception:
            if self._socket is not None:
                self._socket.close()
            driver.close()
            raise
        self.thread = threading.Thread(target=self._serve, daemon=True, name="session-bridge-host")
        self.thread.start()
        self.observer_thread = threading.Thread(target=self._forward_observations, daemon=True, name="session-bridge-observer")
        self.observer_thread.start()

    def _load_state(self):
        if self.state_path is None:
            return None
        try:
            metadata = self.state_path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise bridge_error("Host state must be an owner-only regular file.", "bridge_permissions")
        if metadata.st_size > 64 * 1024 * 1024:
            raise bridge_error("Host state exceeds the configured safety bound.", "bridge_capacity")
        fd = os.open(self.state_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            return decode_json(source.read())

    def _transition_effect(self, key, event, changes):
        """Decide before replacing the one effect row in the host snapshot."""
        previous = self._effects.get(key)
        proposed = {**deepcopy(previous or {}), **deepcopy(changes)}
        decision = self.state_engine.decide("native_effect", previous["status"] if previous else None,
            event, {"identity": key, "current": previous, "proposed": proposed})
        proposed["status"] = decision.target
        proposed["state_version"] = (previous or {}).get("state_version", 0) + 1
        proposed["state_history"] = [*(previous or {}).get("state_history", []), {
            "event": event, "source": decision.source, "target": decision.target,
            "definition_version": decision.definition_version, "version": proposed["state_version"],
        }]
        self._effects[key] = proposed
        return proposed

    def _save_state(self):
        if self.state_path is None or not hasattr(self, "backend"):
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {"host_id": self.host_id, "secret": self._secret.hex(), "backend": self.backend,
            "effects": self._effects, "events": self._events, "sequence": self._sequence,
            "bindings": [[field, value, lease] for (field, value), lease in self._bindings.items()]}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.state_path.parent, prefix=".bridge-state-", delete=False) as target:
                temporary = Path(target.name)
                os.fchmod(target.fileno(), 0o600)
                target.write(json.dumps(state, allow_nan=False, separators=(",", ":")).encode())
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.state_path)
            directory = os.open(self.state_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _serve(self):
        while not self._closed:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                break
            with self._lock:
                self._peers = {item for item in self._peers if not item.closed}
                if len(self._peers) >= self.max_connections:
                    connection.close()
                    continue
            holder = {}
            ready = threading.Event()

            def handle(method, params, holder=holder, ready=ready):
                ready.wait()
                return self._request(holder["peer"], method, params)

            peer = Peer(connection, handle, self.options)
            holder["peer"] = peer
            with self._lock:
                self._peers = {item for item in self._peers if not item.closed}
                self._peers.add(peer)
            ready.set()

    def _token(self, source=None):
        value = {"host_id": self.host_id, "native_id": self.backend["native_id"], "configuration_id": self.configuration_id}
        if source is not None:
            value = {key: source.get(key) for key in value}
        return hmac.new(self._secret, canonical_digest(value).encode(), hashlib.sha256).hexdigest()

    def _receipt(self):
        return {"host_id": self.host_id, "native_id": self.backend["native_id"], "protocol": "bridge",
            "native_protocol": self.backend.get("protocol"), "native_incarnation": self.backend.get("incarnation"),
            "incarnation": self._lease, "ownership": "external", "connection_state": "connected", "resumable": True,
            "configuration_id": self.configuration_id, "settings": deepcopy(self.backend.get("settings", {"requested": {}, "resolved": {}, "observed": {}})),
            "resume_token": self._token(), "catalog_identity": self.catalog_identity}

    def _check(self, peer, params, *, outcome="not_started"):
        if peer is not self._active or params.get("incarnation") != self._lease:
            raise bridge_error("The bridge connection was detached or replaced.", "stale_connection", outcome)

    @contextmanager
    def _settlement(self, peer, params):
        """Retain native knowledge, then fence every result/error leaving the host."""
        with self._lock:
            try:
                yield
            finally:
                # Settlement may itself fail or carry a completed native result.
                # The current owner retains that evidence; a replaced connection
                # gets no result and cannot infer permission to repeat the effect.
                self._check(peer, params, outcome="unknown")

    def _request(self, peer, method, params):
        if not isinstance(params, dict):
            raise bridge_error("Bridge parameters must be an object.", "bridge_protocol")
        with self._lock:
            if method == "attach":
                if params.get("catalog_identity") != self.catalog_identity:
                    raise bridge_error("Attached application tool catalog differs from the host catalog.", "configuration_changed")
                source = params.get("source")
                if source is not None:
                    if not isinstance(source, dict) or source.get("host_id") != self.host_id or source.get("native_id") != self.backend["native_id"] or not hmac.compare_digest(str(source.get("resume_token", "")), self._token(source)):
                        raise bridge_error("The source receipt does not identify this host conversation.", "session_identity")
                    if source.get("configuration_id") != self.configuration_id:
                        raise bridge_error("The source configuration no longer matches this host.", "configuration_changed")
                context = params.get("context", {})
                if not isinstance(context, dict):
                    raise bridge_error("Attach context must be an object.", "bridge_protocol")
                self._active, self._lease = peer, uuid.uuid4().hex
                self._active_ready = False
                self._context = copy_json(context)
                return self._receipt()
            self._check(peer, params)
            lease = self._lease
            if method == "activate":
                self._active_ready = True
                return {"active": True}
            if method == "detach":
                self._active = None
                return {"detached": True, "terminated": False, "native_id": self.backend["native_id"], "incarnation": lease}
            if method == "events":
                after = params.get("after", 0)
                if type(after) is not int or after < 0:
                    raise bridge_error("Observation cursor must be nonnegative.", "bridge_protocol")
                return {"events": deepcopy([event for event in self._events if event["sequence"] > after]), "last_sequence": self._sequence}
            if method == "inspect":
                return {"receipt": self._receipt(), "effects": deepcopy(self._effects)}
            if method not in {"send", "deliver", "poll"}:
                raise bridge_error("Unsupported bridge operation.", "unsupported_operation")
            key = params.get("operation_id")
            if not isinstance(key, str) or not key:
                raise bridge_error("A bridge operation identity is required.", "bridge_protocol")
            identity = canonical_digest({"method": method, "message": params.get("message"), "incarnation": lease})
            existing = self._effects.get(key)
            if existing:
                if existing["identity"] != identity:
                    raise bridge_error("Bridge operation identity was reused for another effect.", "conflict")
                if existing["status"] == "completed":
                    return deepcopy(existing["result"])
                if existing.get("effect_outcome", existing.get("error_details", {}).get("outcome")) == "completed":
                    details = {"outcome": "completed"}
                    if "result" in existing:
                        details["result"] = deepcopy(existing["result"])
                    raise OrchestrationError("The native effect completed but host settlement is unresolved.",
                        "completed_unapplied", details)
                if existing["status"] == "failed" or existing.get("effect_outcome") in {"not_started", "failed"}:
                    raise OrchestrationError(existing.get("error_message", "The native effect was refused."),
                        existing.get("error", "effect_failed"),
                        {**existing.get("error_details", {}), "outcome": "failed"})
                raise bridge_error("The bridge effect remains unresolved.", "operation_unresolved", "unknown")
            if len(self._effects) >= self.max_events:
                raise bridge_error("Host effect retention limit reached; inspect and rotate the host.", "bridge_capacity")
            with before_effect():
                message = copy_json(params.get("message", {}))
                if not isinstance(message, dict):
                    raise bridge_error("Session message must be an object.", "bridge_protocol")
                function = getattr(self.driver, method, None)
                if not callable(function):
                    raise bridge_error("Native driver does not implement this operation.", "unsupported_operation")
                self._transition_effect(key, "reserve", {"identity": identity, "incarnation": lease})
                if message.get("attempt_id"):
                    self._bindings[("attempt_id", message["attempt_id"])] = lease
                self._save_state()
        try:
            self._effect_context.lease = lease
            if method == "poll":
                result = function(deepcopy(self.backend))
            else:
                result = function(deepcopy(self.backend), message)
        except Exception as exc:
            with self._settlement(peer, params):
                knowledge = effect_outcome(exc)
                event = "fail" if knowledge in {"not_started", "failed"} else "uncertain"
                # This evidence is persisted, returned by inspect and replayed as a
                # public error, so arbitrary exception attributes stay private.
                diagnostic = public_error(exc)
                evidence = {"error": diagnostic["code"], "effect_outcome": knowledge,
                    "error_message": diagnostic["message"], "error_details": diagnostic["details"]}
                if knowledge == "completed" and "result" in evidence["error_details"]:
                    evidence["result"] = deepcopy(evidence["error_details"]["result"])
                # Observed effect knowledge is evidence, not a forced lifecycle
                # transition. A configured terminal guard may refuse settlement.
                self._effects[key].update(evidence)
                try:
                    self._save_state()
                    self._transition_effect(key, event, {})
                    self._save_state()
                except Exception as settlement:
                    raise OrchestrationError("Native outcome could not be settled by the host.", "effect_unapplied", {
                        "outcome": knowledge, "cause": public_error(settlement)["code"],
                        "native_error": evidence["error"],
                    }) from settlement
            raise
        finally:
            self._effect_context.lease = None
        with self._settlement(peer, params):
            # Retain the returned result independently of whether the configured
            # lifecycle can settle it. Never force a guard-denied transition.
            self._effects[key].update({"result": deepcopy(result), "effect_outcome": "completed"})
            try:
                self._save_state()
                self._transition_effect(key, "succeed", {})
                if isinstance(result, dict):
                    for field in ("turn_id", "prompt_id", "submission_id", "call_id"):
                        if result.get(field):
                            self._bindings[(field, result[field])] = lease
                self._save_state()
            except Exception as settlement:
                self._effects[key]["recording_error"] = getattr(settlement, "code", type(settlement).__name__)
                try:
                    self._save_state()
                except Exception:
                    pass  # The original returned result is also carried outward.
                raise OrchestrationError("Native effect completed but host settlement could not be retained.",
                    "completed_unapplied", {"outcome": "completed", "result": deepcopy(result),
                        "cause": getattr(settlement, "code", type(settlement).__name__)}) from settlement
        return result

    def _replay_callback(self, action, arguments, native_context, idempotency_key=None):
        return self._callback(action, arguments, native_context, idempotency_key, replay_only=True)

    def _callback(self, action, arguments, native_context, idempotency_key=None, *, replay_only=False):
        with self._lock:
            peer, lease = self._active, self._lease
            if peer is None or peer.closed or not self._active_ready:
                raise bridge_error("No application is attached to receive native tool calls.", "application_detached", "unknown")
            call_id = native_context.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise bridge_error("Native callback lacks a call identity.", "missing_call_identity", "unknown")
            if (native_context.get("native_id") != self.backend["native_id"]
                    or native_context.get("incarnation") != self.backend.get("incarnation")):
                raise bridge_error("Native callback belongs to another conversation incarnation.", "session_identity", "unknown")
            call_key = _call_binding(native_context)
            # Observation is the first authority binding, not dispatch time. A
            # reconnect may replace actor/work as well as the connection. Never
            # lend that replacement authority to a call already seen elsewhere.
            bound_lease = self._bindings.get(call_key, getattr(self._effect_context, "lease", None) or lease)
            if bound_lease != lease:
                raise bridge_error("Native callback belongs to a detached or replaced application connection.", "stale_connection", "unknown")
            context = {**deepcopy(self._context), "native_id": self.backend["native_id"], "incarnation": lease,
                "native_incarnation": native_context.get("incarnation"), "host_id": self.host_id,
                "call_id": call_id, "action": action}
            if call_id:
                self._bindings.setdefault(call_key, lease)
                self._save_state()
        try:
            result = peer.request("application_tool", {"action": action, "arguments": arguments, "context": context,
                "idempotency_key": idempotency_key, "replay_only": replay_only})
        except OrchestrationError as error:
            # A callback rejected before application execution says nothing
            # about other native effects in the enclosing send/delivery.
            if error.details.get("outcome") in {"not_started", "failed"}:
                raise OrchestrationError(str(error), error.code, {**error.details, "outcome": "unknown",
                    "callback_outcome": error.details["outcome"]}) from error
            raise
        with self._lock:
            if peer is not self._active or lease != self._lease:
                raise bridge_error("Native callback completed after its application connection was replaced.", "stale_connection", "unknown")
        return result

    def _observe(self, native_context, observation):
        with self._lock:
            lease = getattr(self._effect_context, "lease", None)
            identities = {}
            # A native call has its own identity. Reusing a turn/prompt label
            # must not bind a fresh call to an obsolete attachment; the host
            # dispatches that call through the currently attached application.
            fields = ("call_id",) if observation.get("call_id") else ("attempt_id", "turn_id", "prompt_id", "submission_id")
            for field in fields:
                if observation.get(field):
                    identities[field] = observation[field]
                    key = _call_binding(observation) if field == "call_id" else (field, observation[field])
                    lease = lease or self._bindings.get(key)
            payload = observation.get("payload", {})
            if not observation.get("call_id") and isinstance(payload, dict) and payload.get("turnId"):
                identities["turn_id"] = payload["turnId"]
                lease = lease or self._bindings.get(("turn_id", payload["turnId"]))
            message = observation.get("message", {})
            if not observation.get("call_id") and isinstance(message, dict) and message.get("attempt_id"):
                identities["attempt_id"] = message["attempt_id"]
                lease = lease or self._bindings.get(("attempt_id", message["attempt_id"]))
            result = observation.get("result", {})
            if not observation.get("call_id") and isinstance(result, dict) and isinstance(result.get("turn"), dict) and result["turn"].get("id"):
                identities["turn_id"] = result["turn"]["id"]
                lease = lease or self._bindings.get(("turn_id", identities["turn_id"]))
            # Tool observations arrive before their callback; bind them once,
            # including an unbound observation made before any attachment.
            # Completion without a known turn stays
            # unbound rather than becoming evidence for a replacement lease.
            if lease is None and observation.get("kind") in {"native_tool", "native_tool_call"}:
                call_key = _call_binding(observation)
                lease = self._bindings.get(call_key, self._lease)
                if observation.get("call_id"):
                    self._bindings.setdefault(call_key, lease)
            conflict = hasattr(self, "backend") and (
                observation.get("native_id") != self.backend.get("native_id")
                or observation.get("incarnation") != self.backend.get("incarnation"))
            # An asynchronous native tool may be the first lease-qualified
            # observation of its enclosing turn. Preserve that relation so its
            # later completion can be forwarded. A fresh call never transfers
            # an already-owned turn, and a stale call cannot bind a new turn.
            if (not conflict and lease and lease == self._lease
                    and observation.get("kind") in {"native_tool", "native_tool_call"}
                    and observation.get("call_id")
                    and self._bindings.get(_call_binding(observation)) == lease):
                for field in ("turn_id", "prompt_id"):
                    turn = observation.get(field)
                    if isinstance(turn, str) and turn:
                        self._bindings.setdefault((field, turn), lease)
            if lease:
                for field, value in identities.items():
                    key = _call_binding(observation) if field == "call_id" else (field, value)
                    previous = self._bindings.get(key)
                    if key in self._bindings and previous != lease:
                        conflict = True
                    else:
                        self._bindings[key] = lease
            if conflict:
                lease = None
            self._sequence += 1
            event = {**deepcopy(observation), "native_incarnation": observation.get("incarnation"),
                "incarnation": lease, "host_id": self.host_id, "sequence": self._sequence,
                "bridge_bound": lease is not None, "bridge_identity_conflict": conflict,
                "at": observation.get("at", datetime.now(timezone.utc).isoformat())}
            self._events.append(event)
            recovered = []
            if observation.get("kind") == "native_submission" and lease:
                for older in self._events:
                    if older.get("bridge_bound") or older.get("bridge_identity_conflict") or older.get("native_incarnation") != event.get("native_incarnation"):
                        continue
                    matches = any(older.get(field) == value for field, value in identities.items() if field in {"turn_id", "prompt_id", "submission_id"})
                    if matches:
                        older.update({"incarnation": lease, "bridge_bound": True, "bridge_binding_reason": "matched_native_submission"})
                        recovered.append(deepcopy(older))
            if len(self._events) > self.max_events:
                # A gap is explicit and cannot be mistaken for complete audit.
                self._events.pop(0)
            self._save_state()
            if self._active is not None and self._active_ready and lease == self._lease:
                try:
                    for item in [*recovered, event]:
                        self._outgoing.put_nowait((self._active, lease, deepcopy(self._context), deepcopy(item)))
                except queue.Full:
                    # Events are retained for explicit collection. Queue pressure
                    # does not turn an observation into successful delivery.
                    pass

    def _forward_observations(self):
        while not self._closed:
            try:
                peer, lease, context, event = self._outgoing.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                current = peer is self._active and lease == self._lease
            if current:
                try:
                    peer.request("observation", {"context": context, "observation": event})
                except OrchestrationError:
                    pass  # Still retained; poll exposes the original observation.

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._socket.shutdown()
        if threading.current_thread() is not self.thread:
            self.thread.join()
        self._socket.close()
        with self._lock:
            peers = list(self._peers)
            self._active = None
        for peer in peers:
            peer.close()
        self.driver.close()


