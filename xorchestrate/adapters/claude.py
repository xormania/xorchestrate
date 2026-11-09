"""Claude Code sessions using the native MCP channel and independent hooks.

Adapted from the native mechanism in xormania/multi-harness-proof at
 a944f31b7527807d000da985c546e8b5bc9ff476 (mhproof/adapters.py, mcp.py,
 evidence.py). No historical tool allowlist or organization policy is inherited.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import re
import shlex
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..activity import ActivityState, NativeConnection, activity_engine
from ..model import OrchestrationError
from ..outcomes import before_effect, effect_outcome
from ..schema import check_schema, validate
from .jsonrpc import (
    TelemetryDispatch,
    capture_origin,
    close_process,
    command_options,
    decode_json,
    encode_json,
    observe_telemetry,
    observe_wire,
    positive_integer,
    positive_number,
    telemetry_dispatch,
    telemetry_secrets,
)
from .native_gateway import LocalGateway


def _object(value, name):
    if not isinstance(value, dict):
        raise OrchestrationError(f"{name} must be an object.", details={"outcome": "not_started"})
    return decode_json(encode_json(value))


def _digest(value):
    return hashlib.sha256(encode_json(value, sort_keys=True)).hexdigest()


@dataclass
class _ClaudeLive:
    context: dict
    native_id: str
    incarnation: str = field(default_factory=lambda: uuid.uuid4().hex)
    handle: str = field(default_factory=lambda: uuid.uuid4().hex)
    lock: threading.RLock = field(default_factory=threading.RLock)
    hook_ready: threading.Event = field(default_factory=threading.Event)
    mcp_ready: threading.Event = field(default_factory=threading.Event)
    stopped: threading.Event = field(default_factory=threading.Event)
    events: deque = field(default_factory=deque)
    calls: dict = field(default_factory=dict)
    pending_calls: deque = field(default_factory=deque)
    mcp_calls: dict = field(default_factory=dict)
    deliveries: dict = field(default_factory=dict)
    channel: queue.Queue = field(default_factory=queue.Queue)
    process: object = None
    gateway: object = None
    settings: dict = field(default_factory=dict)
    activity: str = "unknown"
    failure: object = None
    channel_ready: bool = False
    diagnostics: dict = field(default_factory=dict)
    activity_model: ActivityState = field(default_factory=ActivityState)
    active_turns: set = field(init=False)
    completed_turns: set = field(default_factory=set)
    active_tools: set = field(init=False)
    connection: NativeConnection = field(init=False)
    transcript: dict = field(default_factory=lambda: {"path": None, "cursor": 0, "identity": None,
                                                     "source_id": None, "modified": None,
                                                     "hasher": None, "verification": None})
    transcript_lock: threading.RLock = field(default_factory=threading.RLock)
    # Native tool-use names seen in this session's transcript, by call ID.
    transcript_tools: dict = field(default_factory=dict)
    telemetry_context: dict = field(default_factory=dict)

    def __post_init__(self):
        self.connection = NativeConnection(self.activity_model.engine, self.incarnation)
        self.active_turns = self.activity_model.set("turn")
        self.active_tools = self.activity_model.set("callback")


class ClaudeSessionDriver:
    protocol = "claude"
    resumable = True

    def __init__(self, options):
        self.options = _object(options, "Claude options")
        command_options(self.options)
        self.timeout = positive_number(self.options, "timeout", 30)
        self.startup_timeout = positive_number(self.options, "startup_timeout", 120)
        self.max_sessions = positive_integer(self.options, "max_sessions", 64)
        self.max_events = positive_integer(self.options, "max_events", 10000)
        self.max_calls = positive_integer(self.options, "max_calls", 10000)
        self.max_deliveries = positive_integer(self.options, "max_deliveries", 1024)
        self.max_transcript_bytes = positive_integer(self.options, "max_transcript_bytes", 4 * 1024 * 1024)
        self.max_transcript_frame = positive_integer(self.options, "max_transcript_frame_bytes", 1024 * 1024)
        self.request_parameters = self.options.get("request_parameters", {"type": "object", "additionalProperties": False})
        check_schema(self.request_parameters)
        self.launch = _object(self.options.get("launch", {}), "Claude launch settings")
        if set(self.launch) - {"model", "effort", "permission_mode", "tools", "allowed_tools", "disallowed_tools", "system_prompt", "initial_prompt"}:
            raise OrchestrationError("Unknown Claude launch setting.", "invalid_configuration")
        self.permissions = _object(self.options.get("permissions", {}), "Claude permissions")
        if any(value not in {"allow", "deny", "ask", "defer"} for value in self.permissions.values()):
            raise OrchestrationError("Claude native permissions must be allow, deny, ask or defer.", "invalid_configuration")
        self.server_name = self.options.get("server_name", "xorchestrate")
        if not isinstance(self.server_name, str) or not self.server_name.isidentifier():
            raise OrchestrationError("Claude MCP server_name must be an identifier.", "invalid_configuration")
        self.terminal = self.options.get("terminal", "inherit")
        if isinstance(self.terminal, dict):
            if (set(self.terminal) - {"adapter", "directory", "purpose"}
                    or self.terminal.get("adapter") != "pty"
                    or not isinstance(self.terminal.get("directory"), str)
                    or not self.terminal["directory"]
                    or not isinstance(self.terminal.get("purpose", "native prompt"), str)):
                raise OrchestrationError("Claude PTY terminal needs an adapter and private directory.", "invalid_configuration")
            if os.name != "posix":
                raise OrchestrationError("Claude PTY terminal requires POSIX.", "invalid_configuration")
        elif self.terminal not in {"inherit", "discard"}:
            raise OrchestrationError("Claude terminal must be inherit, discard or a PTY adapter.", "invalid_configuration")
        # Operational time/queue bounds do not change the selected native configuration.
        material = {key: value for key, value in self.options.items() if key not in {
            "timeout", "startup_timeout", "max_sessions", "max_events", "max_calls", "max_deliveries", "gateway_timeout", "gateway_workers", "terminal", "evidence_directory",
            "telemetry_drain_timeout", "max_telemetry_pending", "max_telemetry_bytes", "max_transcript_bytes", "max_transcript_frame_bytes"}}
        self.configuration_id = _digest({"adapter": "claude-channel-v1", "options": material})
        self._instance = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._sessions = {}
        self._starting = 0
        self._closed = False
        self._observer = None
        self._telemetry_observer = None
        self._callback = None
        self._application_replay = None
        self._capabilities = {}
        self._state_engine = activity_engine()

    def bind_state_engine(self, engine):
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind state handling before launching sessions.", "conflict")
            self._state_engine = engine

    def bind_observer(self, observer):
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind observers before launching sessions.", "conflict")
            self._observer = observer

    def bind_telemetry(self, observer):
        if observer is not None and not callable(observer):
            raise OrchestrationError("Telemetry observer must be callable.", "invalid_configuration")
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind telemetry before launching sessions.", "conflict")
            self._telemetry_observer = telemetry_dispatch(observer, self.options)

    def telemetry_health(self):
        observer = self._telemetry_observer
        return observer.health() if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def drain_telemetry(self, timeout=None):
        observer = self._telemetry_observer
        return observer.drain(timeout) if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def bind_application_tools(self, capabilities, callback):
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind application tools before launching sessions.", "conflict")
            self._capabilities = {item.name: deepcopy(item) for item in capabilities}
            self._callback = callback

    def bind_application_replay(self, callback):
        if not callable(callback):
            raise OrchestrationError("Application replay validator must be callable.", "invalid_configuration")
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind replay validation before starting sessions.", "conflict")
            self._application_replay = callback

    def _activity(self, live):
        """Called under live.lock; a completion only retires its own work."""
        live.activity = live.activity_model.activity
        return live.activity

    def _emit(self, live, kind, source, **fields):
        event = {"kind": kind, "at": datetime.now(timezone.utc).isoformat(), "source": source,
                 "native_id": live.native_id, "incarnation": live.incarnation, **deepcopy(fields)}
        self._capture(live, "native." + kind, event)
        with live.lock:
            if len(live.events) >= self.max_events:
                live.failure = OrchestrationError("Claude observation bound reached; evidence may not be dropped.", "observation_overflow")
                raise live.failure
            live.events.append(event)
        if self._observer:
            self._observer(deepcopy(live.context), deepcopy(event))
        return event

    def _capture(self, live, event_type, payload, **metadata):
        origin = live.telemetry_context or {**live.context, "native_id": live.native_id, "incarnation": live.incarnation}
        return observe_telemetry(self._telemetry_observer, event_type, payload, origin,
                                 secrets=telemetry_secrets(self.options) if self._telemetry_observer is not None else (), **metadata)

    def _transcript_status(self, live, status, reason=None, **fields):
        self._capture(live, "native.transcript_source", {"status": status, "cursor": live.transcript["cursor"],
                      "source_id": live.transcript["source_id"], "source_version": "claude-transcript-jsonl-v1",
                      "continuity": live.transcript.get("continuity", "initial_source"),
                      "native_id": live.native_id, "incarnation": live.incarnation, "reason": reason, **fields})
        if isinstance(self._telemetry_observer, TelemetryDispatch):
            self._telemetry_observer.drain()

    def _capture_transcript(self, live, selected=None):
        """Tail only this hook-selected session, adapting the proof capture donor.

        The cursor crosses complete protected records only after observer
        acknowledgment. A partial final record remains available for the next
        hook/poll. No directory discovery or historical launcher is involved.
        """
        if self._telemetry_observer is None:
            return
        try:
            with live.transcript_lock:
                source = live.transcript
                if selected is not None:
                    path = Path(selected) if isinstance(selected, str) else None
                    if (path is None or not path.is_absolute() or path.name != live.native_id + ".jsonl"
                            or source["path"] is not None and path != source["path"]):
                        self._transcript_status(live, "rejected", "session_path_mismatch")
                        return
                    source["path"] = path
                if source["path"] is None:
                    self._transcript_status(live, "unavailable", "transcript_path_not_exposed")
                    return
                descriptor = os.open(source["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        self._transcript_status(live, "rejected", "unsafe_source_file")
                        return
                    identity = (info.st_dev, info.st_ino)
                    modified = (info.st_mtime_ns, info.st_ctime_ns)
                    remaining = self.max_transcript_bytes

                    def new_generation(reason):
                        if source["identity"] is not None:
                            self._transcript_status(live, "gap", reason, previous_cursor=source["cursor"])
                        # Continuity fingerprints are ephemeral, keyed and never
                        # published or persisted as credential-derived metadata.
                        empty = hmac.new(os.urandom(32), digestmod=hashlib.sha256)
                        source.update(identity=identity, cursor=0, source_id=uuid.uuid4().hex,
                                      hasher=empty.copy(), empty_hasher=empty, verification=None,
                                      recheck_required=False, tail_pending=False, continuity="initial_source")

                    if source["identity"] != identity or info.st_size < source["cursor"]:
                        new_generation("source_rotated_or_truncated")
                    elif (source["modified"] != modified or source.get("recheck_required")) and source["cursor"] and not source.get("tail_pending"):
                        verification = source["verification"]
                        if verification is not None and verification["modified"] != modified:
                            if info.st_size > verification["size"]:
                                # Appends beyond the frozen committed cursor do
                                # not erase progress. This is not an atomic file
                                # snapshot: recheck the whole prefix again after
                                # allowing a bounded tail pass to make progress.
                                verification.update(modified=modified, size=info.st_size, concurrent_growth=True)
                                source["continuity"] = "unconfirmed_during_growth"
                            else:
                                verification = None
                        if verification is None:
                            # A fresh keyed empty state is retained alongside
                            # the committed rolling state for bounded rechecks.
                            empty = source["empty_hasher"].copy()
                            verification = {"modified": modified, "size": info.st_size, "offset": 0,
                                            "hasher": empty, "concurrent_growth": False}
                            source["verification"] = verification
                        stream.seek(verification["offset"])
                        while verification["offset"] < source["cursor"] and remaining:
                            block = stream.read(min(65536, source["cursor"] - verification["offset"], remaining))
                            if not block:
                                new_generation("consumed_prefix_unavailable")
                                break
                            verification["hasher"].update(block)
                            verification["offset"] += len(block)
                            remaining -= len(block)
                        if source["cursor"]:
                            current = os.fstat(stream.fileno())
                            current_modified = (current.st_mtime_ns, current.st_ctime_ns)
                            if current_modified != modified:
                                if current.st_size > info.st_size:
                                    modified = current_modified
                                    verification.update(modified=modified, size=current.st_size, concurrent_growth=True)
                                    source["continuity"] = "unconfirmed_during_growth"
                                else:
                                    source["verification"] = None
                                    self._transcript_status(live, "partial", "source_changed_during_verification")
                                    return
                            if verification["offset"] < source["cursor"]:
                                self._transcript_status(live, "partial", "prefix_verification_pending",
                                                        verified_prefix_bytes=verification["offset"])
                                return
                            if not hmac.compare_digest(verification["hasher"].digest(), source["hasher"].digest()):
                                new_generation("consumed_prefix_changed")
                            else:
                                source["recheck_required"] = verification["concurrent_growth"]
                                source["tail_pending"] = verification["concurrent_growth"]
                                source["continuity"] = "unconfirmed_during_growth" if verification["concurrent_growth"] else "verified_prefix"
                                if verification["concurrent_growth"]:
                                    self._transcript_status(live, "partial", "continuity_unconfirmed_during_growth")
                            source["verification"] = None
                    source["modified"] = modified
                    stream.seek(source["cursor"])
                    while remaining:
                        offset = stream.tell()
                        line = stream.readline(min(self.max_transcript_frame + 1, remaining))
                        if not line:
                            source["tail_pending"] = False
                            self._transcript_status(live, "captured", source_bytes=info.st_size)
                            return
                        remaining -= len(line)
                        if not line.endswith(b"\n") or len(line) > self.max_transcript_frame:
                            # Ownership fields may follow private content in an
                            # unfinished JSON object. Even an unbound current
                            # driver may be reading historical application tool
                            # results. Retain the range until it can be classified.
                            self._transcript_privacy_gap(live, line, offset, info.st_size, "incomplete",
                                reason="transcript_fragment_requires_classification",
                                event_type="native.transcript_tail", acknowledge=False)
                            self._transcript_status(live, "partial", "partial_tail" if offset + len(line) >= info.st_size else "read_bound",
                                                    observed_range_start=offset, observed_range_end=offset + len(line), source_bytes=info.st_size)
                            return
                        parser_status, entry = "valid", None
                        try:
                            entry = decode_json(line)
                        except OrchestrationError:
                            parser_status = "invalid"
                        if isinstance(entry, dict):
                            if any(entry.get(key) is not None and entry[key] != live.native_id for key in ("sessionId", "session_id")):
                                self._transcript_status(live, "rejected", "record_session_mismatch")
                                return
                        elif parser_status == "valid":
                            parser_status = "invalid"
                        origin = live.telemetry_context or {**live.context, "native_id": live.native_id, "incarnation": live.incarnation}
                        secrets = telemetry_secrets(self.options)
                        event_id = "claude-transcript:" + source["source_id"] + ":" + str(offset)
                        withheld = self._withheld_tool_results(live, entry) if parser_status == "valid" else None
                        if parser_status != "valid":
                            accepted = self._transcript_privacy_gap(live, line, offset, info.st_size, parser_status,
                                reason="transcript_record_requires_classification", event_id=event_id)
                        elif withheld:
                            # Application tool replies embed resources with their
                            # own current authorization. Typed application
                            # observations retain them; this outer-session copy
                            # keeps only an explicit, non-reversible gap.
                            accepted = self._transcript_privacy_gap(live, line, offset, info.st_size, parser_status,
                                reason="callback_response_requires_typed_observation", tool_results=withheld,
                                event_id=event_id)
                        else:
                            accepted = observe_wire(self._telemetry_observer, "native.transcript", line, origin, secrets=secrets,
                                                    range_start=offset, source_id=source["source_id"], source_version="claude-transcript-jsonl-v1",
                                                    parser_version="xorchestrate-json-v1", parser_status=parser_status, entry=entry,
                                                    continuity=source.get("continuity", "initial_source"),
                                                    native_id=live.native_id, incarnation=live.incarnation, event_id=event_id, acknowledge=True)
                        if not accepted:
                            self._transcript_status(live, "partial", "capture_unavailable")
                            return
                        source["cursor"] = offset + len(line)
                        source["hasher"].update(line)
                        source["tail_pending"] = False
                        if isinstance(self._telemetry_observer, TelemetryDispatch):
                            self._telemetry_observer.release(event_id)
                        if isinstance(entry, dict):
                            message = entry.get("message") if isinstance(entry.get("message"), dict) else entry
                            usage = message.get("usage")
                            if isinstance(usage, dict):
                                message_id = message.get("id")
                                observe_telemetry(self._telemetry_observer, "native.usage", {"usage": usage,
                                                  "counter_semantics": "overwrite" if isinstance(message_id, str) and message_id else "unknown",
                                                  "usage_stream_id": live.native_id + ":" + message_id if isinstance(message_id, str) and message_id else None,
                                                  "source_id": source["source_id"], "source_version": "claude-transcript-jsonl-v1",
                                                  "source_event_id": event_id}, origin, secrets=secrets, event_id=event_id + ":usage")
                    self._transcript_status(live, "partial", "read_bound", source_bytes=info.st_size)
        except FileNotFoundError:
            self._transcript_status(live, "unavailable", "source_missing")
        except (OSError, ValueError, TypeError):
            self._transcript_status(live, "unavailable", "source_unreadable")
        except BaseException:
            self._transcript_status(live, "unavailable", "capture_failed")

    def _transcript_privacy_gap(self, live, line, offset, total_bytes, parser_status, *, reason,
                                event_type="native.transcript", event_id=None, tool_results=None, acknowledge=True):
        """Retain observed positions without a reversible unclassified record."""
        source = live.transcript
        payload = {"representation": "suppressed", "captured_bytes": 0, "source_bytes": len(line),
                   "total_bytes": total_bytes, "range_start": offset, "range_end": offset + len(line),
                   "source_id": source["source_id"], "source_version": "claude-transcript-jsonl-v1",
                   "parser_version": "xorchestrate-json-v1", "parser_status": parser_status,
                   "continuity": source.get("continuity", "initial_source"),
                   "native_id": live.native_id, "incarnation": live.incarnation,
                   "provenance": "unclassified-transcript-record-v1"}
        if event_type == "native.transcript_tail":
            payload["cursor"] = source["cursor"]
        if tool_results:
            payload.update(direction="callback_response", tool_results=tool_results,
                           provenance="application-capable-callback-v1")
        origin = live.telemetry_context or {**live.context, "native_id": live.native_id, "incarnation": live.incarnation}
        return observe_telemetry(self._telemetry_observer, event_type, payload, origin,
                                 secrets=telemetry_secrets(self.options), completeness="suppressed", reason=reason,
                                 event_id=event_id, acknowledge=acknowledge)

    def _withheld_tool_results(self, live, entry):
        """Return tool results this outer-session record must not retain.

        Results of application tools, and results whose originating tool use
        was never observed, cannot be attributed to a non-application tool.
        """
        if not isinstance(entry, dict):
            return []
        message = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        content = message.get("content")
        blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
        prefix = "mcp__" + self.server_name + "__"
        for block in blocks:
            if (block.get("type") == "tool_use" and isinstance(block.get("id"), str)
                    and isinstance(block.get("name"), str) and len(live.transcript_tools) < self.max_calls):
                live.transcript_tools.setdefault(block["id"], block["name"])
        withheld = []
        results = [block for block in blocks if block.get("type") == "tool_result"]
        if "toolUseResult" in entry and not results:
            results = [{"tool_use_id": None}]
        for block in results:
            call_id = block.get("tool_use_id")
            with live.lock:
                hooked = live.calls.get(call_id) if isinstance(call_id, str) else None
            tool = live.transcript_tools.get(call_id) if isinstance(call_id, str) else None
            if hooked is not None:
                action = hooked.get("action")
            elif tool is not None:
                action = tool[len(prefix):] if tool.startswith(prefix) else None
            else:
                withheld.append({"call_id": call_id if isinstance(call_id, str) else None, "classification": "unattributed"})
                continue
            if action in self._capabilities:
                withheld.append({"call_id": call_id, "action": action, "classification": "application_tool"})
        return withheld

    def _schedule_transcript(self, live, selected=None):
        if isinstance(self._telemetry_observer, TelemetryDispatch):
            self._telemetry_observer.schedule(lambda: self._capture_transcript(live, selected))

    def _verify_native(self, live, native_id):
        if native_id != live.native_id:
            live.failure = OrchestrationError("Claude native identity changed or is missing.", "session_identity")
            self._emit(live, "identity_error", "claude/hook", observed_native_id=native_id)
            raise live.failure

    def _hook(self, live, packet):
        # State transitions and their timestamped observations are ordered
        # together; another hook cannot publish a later state in between them.
        with live.lock:
            return self._hook_locked(live, packet)

    def _hook_locked(self, live, packet):
        self._capture(live, "native.hook", {"native_id": live.native_id, "incarnation": live.incarnation, "packet": packet})
        packet = _object(packet, "Native hook")
        self._verify_native(live, packet.get("session_id"))
        self._schedule_transcript(live, packet.get("transcript_path"))
        name = packet.get("hook_event_name")
        source = "claude/" + str(name)
        self._emit(live, "native_session", source)
        if name == "SessionStart":
            with live.lock:
                if isinstance(packet.get("model"), str):
                    live.settings["observed"]["model"] = packet["model"]
                self._activity(live)
            self._emit(live, "settings", source, settings=live.settings)
            live.hook_ready.set()
        elif name == "PreToolUse":
            tool, call_id, arguments = packet.get("tool_name"), packet.get("tool_use_id"), packet.get("tool_input")
            if not isinstance(tool, str) or not isinstance(call_id, str) or not call_id or not isinstance(arguments, dict):
                raise OrchestrationError("Native tool hook lacks tool, call ID or arguments.", "native_evidence_missing")
            prefix = "mcp__" + self.server_name + "__"
            action = tool[len(prefix):] if tool.startswith(prefix) else None
            fingerprint = _digest([tool, arguments])
            decision = self.permissions.get(tool, self.permissions.get("*", "defer"))
            if action in self._capabilities:
                decision = self.permissions.get("application_tools", decision)
            with live.lock:
                previous = live.calls.get(call_id)
                if previous and previous["fingerprint"] != fingerprint:
                    live.failure = OrchestrationError("Native call ID reused with different arguments.", "native_call_conflict")
                    raise live.failure
                if previous:
                    decision = previous.get("decision", "defer")
                    return {} if decision == "defer" else {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": decision, "permissionDecisionReason": "Configured Xorchestrate native tool policy"}}
                if not previous:
                    if len(live.calls) >= self.max_calls:
                        raise OrchestrationError("Claude native call bound reached.", "observation_overflow")
                    live.calls[call_id] = {"fingerprint": fingerprint, "action": action, "arguments": deepcopy(arguments), "used": False, "decision": decision}
                    if action in self._capabilities:
                        live.pending_calls.append(call_id)
                        if decision != "deny":
                            live.active_tools.add(call_id)
                    prompt = packet.get("prompt_id")
                    if isinstance(prompt, str) and prompt:
                        live.active_turns.add(prompt)
                    elif (action not in self._capabilities and not live.active_turns
                          and not live.activity_model.active("anonymous_turn")):
                        live.activity_model.begin_anonymous("anonymous_turn")
                self._activity(live)
            self._emit(live, "native_tool", source, call_id=call_id, action=action, tool=tool,
                       arguments=arguments, turn_id=packet.get("prompt_id"))
            self._emit(live, "native_permission", source, call_id=call_id, tool=tool, decision=decision)
            if decision != "defer":
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                              "permissionDecisionReason": "Configured Xorchestrate native tool policy"}}
        elif name in {"Stop", "StopFailure"}:
            with live.lock:
                prompt = packet.get("prompt_id")
                if isinstance(prompt, str) and prompt:
                    if prompt in live.active_turns:
                        live.active_turns.remove(prompt)
                        live.completed_turns.add(prompt)
                    elif prompt not in live.completed_turns:
                        live.activity_model.uncertain()
                elif anonymous := live.activity_model.active("anonymous_turn"):
                    # Concurrent anonymous completions cannot be deduplicated.
                    # Retire at most one, retaining uncertainty if identity was
                    # insufficient to prove that all observed work completed.
                    if len(anonymous) > 1:
                        live.activity_model.uncertain()
                    live.activity_model.finish("anonymous_turn", anonymous[-1])
                else:
                    live.activity_model.uncertain()
                if name == "StopFailure":
                    live.activity_model.uncertain()
                self._activity(live)
            self._emit(live, "turn_completed" if name == "Stop" else "turn_failed", source,
                       turn_id=packet.get("prompt_id"), status="completed" if name == "Stop" else "failed",
                       activity=live.activity)
        elif name == "UserPromptSubmit":
            with live.lock:
                anonymous = live.activity_model.active("anonymous_turn")
                if len(live.active_turns) + len(live.completed_turns) + len(anonymous) >= self.max_calls:
                    raise OrchestrationError("Claude turn observation bound reached.", "observation_overflow")
                prompt = packet.get("prompt_id")
                if isinstance(prompt, str) and prompt:
                    if prompt not in live.completed_turns:
                        live.active_turns.add(prompt)
                else:
                    live.activity_model.begin_anonymous("anonymous_turn")
                self._activity(live)
            self._emit(live, "turn_started", source, turn_id=packet.get("prompt_id"))
        self._emit(live, "activity", source, state=live.activity)
        return {}

    def _tool(self, live, payload):
        with before_effect():
            name, arguments = payload.get("name"), payload.get("arguments", {})
            if name not in self._capabilities or self._callback is None:
                raise OrchestrationError("Unknown application tool.", "unknown_action")
            arguments = _object(arguments, "Native tool arguments")
            # Composition validates after injecting trusted work/idempotency context.
            mcp_id = payload.get("mcp_request_id")
            if isinstance(mcp_id, bool) or not isinstance(mcp_id, (str, int)):
                raise OrchestrationError("MCP invocation requires a request ID.", "native_evidence_missing")
            mcp_key = json.dumps([payload.get("mcp_connection_id", "direct-gateway"), mcp_id])
            fingerprint = _digest([name, arguments])
            replay = None
            with live.lock:
                previous = live.mcp_calls.get(mcp_key)
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise OrchestrationError("MCP request ID reused with different arguments.", "native_call_conflict")
                    if previous.get("state") == "failed":
                        error = previous["error"]
                        raise OrchestrationError(error["message"], error["code"], error["details"])
                    if previous.get("state") != "completed":
                        raise OrchestrationError("Native callback outcome is unresolved.", "operation_unresolved",
                            {"outcome": previous.get("effect_outcome", "unknown")})
                    replay = deepcopy(previous)
                matched = previous["call_id"] if previous else next((key for key in live.pending_calls if not live.calls[key]["used"]
                                and live.calls[key]["action"] == name and live.calls[key]["arguments"] == arguments), None)
                if matched is None:
                    raise OrchestrationError("Application call has no matching native hook.", "native_evidence_missing")
                if live.calls[matched].get("decision") == "deny":
                    raise OrchestrationError("Configured native tool policy denied this call.", "denied")
                if not previous:
                    target = self._state_engine.decide("native_effect", None, "reserve",
                        {"kind": "callback", "identity": mcp_key}).target
                    live.calls[matched]["used"] = True
                    live.pending_calls.remove(matched)
                    live.mcp_calls[mcp_key] = {"fingerprint": fingerprint, "call_id": matched, "state": target}
            trusted = {**deepcopy(live.context), "native_id": live.native_id, "incarnation": live.incarnation,
                       "call_id": matched, "action": name, "native_observation": "claude/PreToolUse"}
            if replay is not None:
                if self._application_replay:
                    return self._application_replay(name, deepcopy(arguments), trusted,
                        json.dumps([live.native_id, live.incarnation, matched]))
                return replay["result"]
        returned = False
        try:
            result = self._callback(name, deepcopy(arguments), trusted, json.dumps([live.native_id, live.incarnation, matched]))
            returned = True
            with live.lock:
                live.mcp_calls[mcp_key].update({"result": deepcopy(result), "effect_outcome": "completed"})
            self._emit(live, "tool_result", "claude/mcp", call_id=matched, action=name, arguments=arguments)
            with live.lock:
                live.mcp_calls[mcp_key]["state"] = self._state_engine.decide("native_effect", "pending", "succeed",
                    {"kind": "callback", "identity": mcp_key}).target
                live.active_tools.discard(matched)
                self._activity(live)
        except Exception as error:
            if returned:
                error = OrchestrationError("Callback completed but local settlement was refused.", "operation_unresolved",
                    {"outcome": "completed", "result": deepcopy(result), "cause": getattr(error, "code", type(error).__name__)})
            with live.lock:
                live.active_tools.discard(matched)
                if effect_outcome(error) not in {"not_started", "failed"}:
                    live.activity_model.uncertain("callback", matched)
                try:
                    live.mcp_calls[mcp_key]["state"] = self._state_engine.decide("native_effect", "pending",
                        "fail" if effect_outcome(error) in {"not_started", "failed"} else "uncertain",
                        {"kind": "callback", "identity": mcp_key}).target
                except Exception as recording_error:
                    live.mcp_calls[mcp_key]["recording_error"] = getattr(recording_error, "code", type(recording_error).__name__)
                live.mcp_calls[mcp_key]["effect_outcome"] = effect_outcome(error)
                live.mcp_calls[mcp_key]["error"] = {"code": getattr(error, "code", "callback_failed"),
                    "message": str(error), "details": {**deepcopy(getattr(error, "details", {})), "outcome": effect_outcome(error)}}
                self._activity(live)
            raise error
        return result

    def _gateway(self, live, action, payload):
        if live.stopped.is_set():
            raise OrchestrationError("Claude connection was replaced or stopped.", "session_unavailable")
        if action == "hook":
            return self._hook(live, payload)
        if live.failure:
            raise live.failure
        if action == "tools/list":
            return {"tools": [{"name": item.name, "description": item.description, "inputSchema": item.input_schema}
                              for item in self._capabilities.values()]}
        if action == "tools/call":
            return self._tool(live, payload)
        if action == "initialized":
            if payload.get("native_id") is not None:
                self._verify_native(live, payload["native_id"])
                self._emit(live, "native_session", "claude/mcp-environment")
            live.mcp_ready.set()
            self._emit(live, "mcp_initialized", "claude/mcp", channel=payload.get("channel"))
            return {}
        if action == "channel/next":
            try:
                return live.channel.get(timeout=0.25)
            except queue.Empty:
                return None
        if action == "channel/sent":
            with live.lock:
                delivery = live.deliveries.get(payload.get("delivery_id"))
            if not delivery:
                raise OrchestrationError("Unknown channel delivery.", "not_found")
            self._emit(live, "submitted", "claude/channel", delivery_id=payload["delivery_id"],
                       message=delivery["message"], acceptance="notification_written")
            delivery["written"].set()
            return {}
        raise OrchestrationError("Unsupported gateway operation.", "unsupported_operation")

    def _receipt(self, live):
        return {"instance": self._instance, "handle": live.handle, "native_id": live.native_id,
                "protocol": self.protocol, "resumable": True, "incarnation": live.incarnation,
                "ownership": "owned", "connection_state": "connected", "activity": live.activity,
                "settings": deepcopy(live.settings), "configuration_id": self.configuration_id,
                "diagnostics": deepcopy(live.diagnostics)}

    def _launch(self, specification, source=None):
        specification = _object(specification, "Claude specification")
        if "resume" in specification:
            raise OrchestrationError("Resume requires an adapter-issued source receipt.", details={"outcome": "not_started"})
        retained = source.get("settings", {}).get("requested", {}) if source else {}
        requested = _object(specification.get("parameters", retained), "Claude parameters")
        validate(requested, self.request_parameters)
        if set(requested) - set(self.launch) - {"model", "effort", "system_prompt", "initial_prompt"}:
            raise OrchestrationError("Undelegated Claude launch parameter.", details={"outcome": "not_started"})
        resolved = {**requested, **deepcopy(self.launch)}
        for key in ("model", "effort", "permission_mode", "system_prompt", "initial_prompt"):
            if key in resolved and not isinstance(resolved[key], str):
                raise OrchestrationError("Claude launch string is invalid.", details={"outcome": "not_started"})
        with self._lock:
            if self._closed or len(self._sessions) + self._starting >= self.max_sessions:
                raise OrchestrationError("Claude session capacity unavailable.", "session_capacity", {"outcome": "not_started"})
            self._starting += 1
        live = _ClaudeLive(_object(specification.get("context", {}), "Trusted session context"), source["native_id"] if source else str(uuid.uuid4()),
                           activity_model=ActivityState(self._state_engine))
        live.telemetry_context = capture_origin({**live.context, "native_id": live.native_id, "incarnation": live.incarnation})
        live.settings = {"requested": requested, "resolved": resolved,
                         "observed": {"model": None, "effort": None}, "precedence": "trusted_configuration"}
        if source and source.get("configuration_id") != self.configuration_id:
            if not self.options.get("allow_configuration_change", False):
                with self._lock:
                    self._starting -= 1
                raise OrchestrationError("Claude configuration changed since source session.", "configuration_changed", {"outcome": "not_started"})
            live.settings["configuration_change"] = {"from": source.get("configuration_id"), "to": self.configuration_id}
        try:
            live.gateway = LocalGateway(lambda action, payload: self._gateway(live, action, payload), self.options)
            directory = Path(live.gateway.directory.name)
            retained = self.options.get("evidence_directory")
            if retained is not None:
                directory = Path(retained).expanduser().resolve() / live.incarnation
                directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            live.diagnostics = {"directory": str(directory), "retained": retained is not None}
            mcp_config = directory / "mcp.json"
            settings_file = directory / "settings.json"
            debug_file = directory / "claude-debug.log"
            live.diagnostics["debug_file"] = str(debug_file)
            mcp_config.write_text(json.dumps({"mcpServers": {self.server_name: live.gateway.mcp_spec(channel=True)}}))
            hook_command = shlex.join([sys.executable, str(Path(__file__).with_name("native_gateway.py")), "hook", str(live.gateway.descriptor_path)])
            native_settings = deepcopy(self.options.get("settings", {}))
            hooks = native_settings.setdefault("hooks", {})
            for event in ("SessionStart", "PreToolUse", "UserPromptSubmit", "Stop", "StopFailure"):
                hooks.setdefault(event, []).append({"hooks": [{"type": "command", "command": hook_command}]})
            settings_file.write_text(json.dumps(native_settings))
            os.chmod(mcp_config, 0o600)
            os.chmod(settings_file, 0o600)
            command = command_options(self.options)
            argv = command["args"] + (["--resume", live.native_id] if source else ["--session-id", live.native_id])
            argv += ["--strict-mcp-config", "--mcp-config", str(mcp_config), "--settings", str(settings_file),
                     "--debug-file", str(debug_file), "--dangerously-load-development-channels", "server:" + self.server_name]
            for setting, flag in (("model", "--model"), ("effort", "--effort"), ("permission_mode", "--permission-mode"),
                                  ("system_prompt", "--append-system-prompt")):
                if setting in resolved:
                    argv += [flag, resolved[setting]]
            for setting, flag in (("tools", "--tools"), ("allowed_tools", "--allowedTools"), ("disallowed_tools", "--disallowedTools")):
                if setting in resolved:
                    values = resolved[setting]
                    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                        raise OrchestrationError("Claude tool settings must be string lists.", "invalid_configuration")
                    argv += [flag, ",".join(values)]
            argv += ["--", resolved.get("initial_prompt", "Await instructions from the configured Xorchestrate channel.")]
            command["args"] = argv
            if isinstance(self.terminal, dict):
                terminal_directory = Path(self.terminal["directory"]).expanduser().resolve() / live.incarnation
                command["args"] = [sys.executable, str(Path(__file__).with_name("native_terminal.py")),
                                   "--directory", str(terminal_directory), "--native-id", live.native_id,
                                   "--incarnation", live.incarnation, "--purpose", self.terminal.get("purpose", "native prompt"),
                                   "--", *argv]
                live.diagnostics["terminal_descriptor"] = str(terminal_directory / "terminal.json")
            io = {} if self.terminal == "inherit" else {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            live.process = subprocess.Popen(**command, **io)
            with self._lock:
                self._sessions[live.handle] = live
            deadline = time.monotonic() + self.startup_timeout
            marker = re.compile(rb'(?m)^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z \[DEBUG\] MCP server "'
                                + re.escape(self.server_name.encode()) + rb'": Channel notifications registered\r?$')
            while time.monotonic() < deadline:
                if live.failure:
                    raise live.failure
                if live.process.poll() is not None:
                    raise OrchestrationError("Claude exited before native channel readiness.", "session_unavailable")
                if debug_file.exists():
                    # Native debug readiness is a compatibility observation, never receipt proof.
                    with debug_file.open("rb") as stream:
                        data = stream.read(1024 * 1024)
                    if marker.search(data):
                        live.channel_ready = True
                if live.hook_ready.is_set() and live.mcp_ready.is_set() and live.channel_ready:
                    live.connection.transition("connect")
                    self._emit(live, "channel_ready", "claude/debug-log", server=self.server_name)
                    self._emit(live, "connected", "claude/driver", ownership="owned")
                    return self._receipt(live)
                time.sleep(0.02)
            raise OrchestrationError("Claude channel/hook readiness was not independently observed before timeout.", "session_start_timeout")
        except BaseException:
            live.connection.transition("close")
            if live.process:
                self._close_process(live.process)
            if live.gateway:
                live.gateway.close()
            with self._lock:
                self._sessions.pop(live.handle, None)
            raise
        finally:
            with self._lock:
                self._starting -= 1

    def start(self, specification):
        return self._launch(specification)

    def resume(self, source_backend, specification):
        source_backend = _object(source_backend, "Claude resume source")
        if source_backend.get("protocol") != self.protocol or not isinstance(source_backend.get("native_id"), str) or not source_backend["native_id"]:
            raise OrchestrationError("Invalid Claude resume source.", "session_identity", {"outcome": "not_started"})
        replacement = self._launch(specification, source_backend)
        with self._lock:
            owned_source = source_backend.get("instance") == self._instance and source_backend.get("handle") in self._sessions
        if owned_source:
            self.stop(source_backend)
        return replacement

    def _get(self, session):
        session = _object(session, "Claude session handle")
        with self._lock:
            live = self._sessions.get(session.get("handle"))
        if (not live or session.get("instance") != self._instance or session.get("native_id") != live.native_id
                or session.get("incarnation") != live.incarnation or session.get("protocol") != self.protocol):
            raise OrchestrationError("Claude connection receipt is stale or invalid.", "session_unavailable", {"outcome": "not_started"})
        if live.failure:
            raise live.failure
        if live.stopped.is_set() or live.process.poll() is not None:
            raise OrchestrationError("Claude connection is no longer live.", "session_unavailable", {"outcome": "not_started"})
        return live

    def send(self, session, message):
        live = self._get(session)
        message = _object(message, "Claude message")
        delivery_id = uuid.uuid4().hex
        with live.lock:
            if len(live.deliveries) >= self.max_deliveries:
                raise OrchestrationError("Claude channel delivery bound reached.", "delivery_capacity", {"outcome": "not_started"})
            written = threading.Event()
            live.deliveries[delivery_id] = {"message": message, "written": written}
        live.channel.put({"delivery_id": delivery_id, "params": {"content": json.dumps(message, allow_nan=False),
                          "meta": {"message_id": str(message.get("message_id", delivery_id)),
                                   "sender_id": str(message.get("sender", "controller"))}}})
        if not written.wait(self.timeout):
            raise OrchestrationError("Channel submission outcome is unknown; do not replay as a fresh delivery.", "provider_timeout", {"outcome": "unknown", "delivery_id": delivery_id})
        with live.lock:
            live.deliveries.pop(delivery_id, None)
        return {"submitted": True, "delivery_id": delivery_id, "native_id": live.native_id,
                "incarnation": live.incarnation, "transport": "claude/channel", "acceptance": "notification_written",
                "activity": live.activity}

    deliver = send

    def poll(self, session):
        live = self._get(session)
        self._schedule_transcript(live)
        with live.lock:
            result = list(live.events)
            live.events.clear()
        return result

    def status(self, session):
        with self._lock:
            live = self._sessions.get(session.get("handle"))
        if (not live or session.get("instance") != self._instance or session.get("incarnation") != live.incarnation
                or session.get("native_id") != live.native_id or session.get("protocol") != self.protocol):
            raise OrchestrationError("Claude connection receipt is stale or invalid.", "session_unavailable", {"outcome": "not_started"})
        self._schedule_transcript(live)
        with live.lock:
            if live.process.poll() is not None and live.connection.state != "closed":
                live.connection.transition("disconnect")
            connection = live.connection.state
        return {"native_id": live.native_id, "incarnation": live.incarnation, "ownership": "owned",
                "connection_state": connection, "activity": "unknown" if live.failure else live.activity,
                "settings": deepcopy(live.settings)}

    def detach(self, session):
        # Owned stdin/channel children cannot survive their adapter. The external
        # bridge's detach contract keeps its native host alive instead.
        return {**self.stop(session), "detached": True, "connection_state": "disconnected"}

    def reconnect(self, source_backend, specification):
        return self.resume(source_backend, specification)

    def stop(self, session):
        # Stop remains available when observation failure made normal calls unusable.
        with self._lock:
            live = self._sessions.get(session.get("handle"))
        if (not live or session.get("instance") != self._instance or session.get("incarnation") != live.incarnation
                or session.get("native_id") != live.native_id or session.get("protocol") != self.protocol):
            raise OrchestrationError("Claude connection receipt is stale or invalid.", "session_unavailable", {"outcome": "not_started"})
        with live.lock:
            live.connection.transition("close")
            live.stopped.set()
        self._close_process(live.process)
        self._schedule_transcript(live)
        live.gateway.close()
        with self._lock:
            self._sessions.pop(live.handle, None)
        return {"stopped": True, "native_id": live.native_id, "incarnation": live.incarnation, "ownership": "owned", "connection_state": "closed"}

    def _close_process(self, process):
        if isinstance(self.terminal, dict) and process.poll() is None:
            # The owned helper forwards termination to its PTY child and reaps
            # it before exiting. Give that bounded chain time to complete.
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            close_process(process, grace=3)
        else:
            close_process(process)

    def close(self):
        with self._lock:
            self._closed = True
            sessions = [self._receipt(live) for live in self._sessions.values()]
        for session in sessions:
            self.stop(session)
        if isinstance(self._telemetry_observer, TelemetryDispatch):
            self._telemetry_observer.close()
