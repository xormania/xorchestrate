"""Claude Code session transcript capture for hook-selected JSONL sources.

The reader tails only the transcript file a native hook selected for one live
session. It reads driver bounds and the bound telemetry observer at call time;
hook-driven scheduling stays with the session driver.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import stat
import uuid
from pathlib import Path

from ..model import OrchestrationError
from .process import decode_json
from .telemetry_capture import TelemetryDispatch, observe_telemetry, observe_wire


class ClaudeTranscriptReader:
    """Protected transcript capture for a Claude session driver's live sessions."""

    def __init__(self, driver):
        self.driver = driver

    def status(self, live, status, reason=None, **fields):
        self.driver._capture(live, "native.transcript_source", {"status": status, "cursor": live.transcript["cursor"],
                             "source_id": live.transcript["source_id"], "source_version": "claude-transcript-jsonl-v1",
                             "continuity": live.transcript.get("continuity", "initial_source"),
                             "native_id": live.native_id, "incarnation": live.incarnation, "reason": reason, **fields})
        if isinstance(self.driver._telemetry_observer, TelemetryDispatch):
            self.driver._telemetry_observer.drain()

    def capture(self, live, selected=None):
        """Tail only this hook-selected session, adapting the proof capture donor.

        The cursor crosses complete protected records only after observer
        acknowledgment. A partial final record remains available for the next
        hook/poll. No directory discovery or historical launcher is involved.
        """
        if self.driver._telemetry_observer is None:
            return
        try:
            with live.transcript_lock:
                source = live.transcript
                if selected is not None:
                    path = Path(selected) if isinstance(selected, str) else None
                    if (path is None or not path.is_absolute() or path.name != live.native_id + ".jsonl"
                            or source["path"] is not None and path != source["path"]):
                        self.status(live, "rejected", "session_path_mismatch")
                        return
                    source["path"] = path
                if source["path"] is None:
                    self.status(live, "unavailable", "transcript_path_not_exposed")
                    return
                descriptor = os.open(source["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        self.status(live, "rejected", "unsafe_source_file")
                        return
                    identity = (info.st_dev, info.st_ino)
                    modified = (info.st_mtime_ns, info.st_ctime_ns)
                    remaining = self.driver.max_transcript_bytes

                    def new_generation(reason):
                        if source["identity"] is not None:
                            self.status(live, "gap", reason, previous_cursor=source["cursor"])
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
                                    self.status(live, "partial", "source_changed_during_verification")
                                    return
                            if verification["offset"] < source["cursor"]:
                                self.status(live, "partial", "prefix_verification_pending",
                                            verified_prefix_bytes=verification["offset"])
                                return
                            if not hmac.compare_digest(verification["hasher"].digest(), source["hasher"].digest()):
                                new_generation("consumed_prefix_changed")
                            else:
                                source["recheck_required"] = verification["concurrent_growth"]
                                source["tail_pending"] = verification["concurrent_growth"]
                                source["continuity"] = "unconfirmed_during_growth" if verification["concurrent_growth"] else "verified_prefix"
                                if verification["concurrent_growth"]:
                                    self.status(live, "partial", "continuity_unconfirmed_during_growth")
                            source["verification"] = None
                    source["modified"] = modified
                    stream.seek(source["cursor"])
                    while remaining:
                        offset = stream.tell()
                        line = stream.readline(min(self.driver.max_transcript_frame + 1, remaining))
                        if not line:
                            source["tail_pending"] = False
                            self.status(live, "captured", source_bytes=info.st_size)
                            return
                        remaining -= len(line)
                        if not line.endswith(b"\n") or len(line) > self.driver.max_transcript_frame:
                            # Ownership fields may follow private content in an
                            # unfinished JSON object. Even an unbound current
                            # driver may be reading historical application tool
                            # results. Retain the range until it can be classified.
                            self._privacy_gap(live, line, offset, info.st_size, "incomplete",
                                reason="transcript_fragment_requires_classification",
                                event_type="native.transcript_tail", acknowledge=False)
                            self.status(live, "partial", "partial_tail" if offset + len(line) >= info.st_size else "read_bound",
                                        observed_range_start=offset, observed_range_end=offset + len(line), source_bytes=info.st_size)
                            return
                        parser_status, entry = "valid", None
                        try:
                            entry = decode_json(line)
                        except OrchestrationError:
                            parser_status = "invalid"
                        if isinstance(entry, dict):
                            if any(entry.get(key) is not None and entry[key] != live.native_id for key in ("sessionId", "session_id")):
                                self.status(live, "rejected", "record_session_mismatch")
                                return
                        elif parser_status == "valid":
                            parser_status = "invalid"
                        origin = live.telemetry_context or {**live.context, "native_id": live.native_id, "incarnation": live.incarnation}
                        secrets = self.driver._telemetry_credentials()
                        event_id = "claude-transcript:" + source["source_id"] + ":" + str(offset)
                        withheld = self._withheld_tool_results(live, entry) if parser_status == "valid" else None
                        if parser_status != "valid":
                            accepted = self._privacy_gap(live, line, offset, info.st_size, parser_status,
                                reason="transcript_record_requires_classification", event_id=event_id)
                        elif withheld:
                            # Application tool replies embed resources with their
                            # own current authorization. Typed application
                            # observations retain them; this outer-session copy
                            # keeps only an explicit, non-reversible gap.
                            accepted = self._privacy_gap(live, line, offset, info.st_size, parser_status,
                                reason="callback_response_requires_typed_observation", tool_results=withheld,
                                event_id=event_id)
                        else:
                            accepted = observe_wire(self.driver._telemetry_observer, "native.transcript", line, origin, secrets=secrets,
                                                    range_start=offset, source_id=source["source_id"], source_version="claude-transcript-jsonl-v1",
                                                    parser_version="xorchestrate-json-v1", parser_status=parser_status, entry=entry,
                                                    continuity=source.get("continuity", "initial_source"),
                                                    native_id=live.native_id, incarnation=live.incarnation, event_id=event_id, acknowledge=True)
                        if not accepted:
                            self.status(live, "partial", "capture_unavailable")
                            return
                        source["cursor"] = offset + len(line)
                        source["hasher"].update(line)
                        source["tail_pending"] = False
                        if isinstance(self.driver._telemetry_observer, TelemetryDispatch):
                            self.driver._telemetry_observer.release(event_id)
                        if isinstance(entry, dict):
                            message = entry.get("message") if isinstance(entry.get("message"), dict) else entry
                            usage = message.get("usage")
                            if isinstance(usage, dict):
                                message_id = message.get("id")
                                observe_telemetry(self.driver._telemetry_observer, "native.usage", {"usage": usage,
                                                  "counter_semantics": "overwrite" if isinstance(message_id, str) and message_id else "unknown",
                                                  "usage_stream_id": live.native_id + ":" + message_id if isinstance(message_id, str) and message_id else None,
                                                  "source_id": source["source_id"], "source_version": "claude-transcript-jsonl-v1",
                                                  "source_event_id": event_id}, origin, secrets=secrets, event_id=event_id + ":usage")
                    self.status(live, "partial", "read_bound", source_bytes=info.st_size)
        except FileNotFoundError:
            self.status(live, "unavailable", "source_missing")
        except (OSError, ValueError, TypeError):
            self.status(live, "unavailable", "source_unreadable")
        except BaseException:
            self.status(live, "unavailable", "capture_failed")

    def _privacy_gap(self, live, line, offset, total_bytes, parser_status, *, reason,
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
        return observe_telemetry(self.driver._telemetry_observer, event_type, payload, origin,
                                 secrets=self.driver._telemetry_credentials(), completeness="suppressed", reason=reason,
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
        prefix = "mcp__" + self.driver.server_name + "__"
        for block in blocks:
            if (block.get("type") == "tool_use" and isinstance(block.get("id"), str)
                    and isinstance(block.get("name"), str) and len(live.transcript_tools) < self.driver.max_native_calls):
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
            if action in self.driver._application_tools:
                withheld.append({"call_id": call_id, "action": action, "classification": "application_tool"})
        return withheld
