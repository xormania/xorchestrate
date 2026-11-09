"""Optional, protected telemetry capture shared by source adapters.

Capture is independent of protocol effects and required evidence callbacks:
admission is never a durable receipt and a failed observer cannot replace the
effect that produced an observation.
"""

from __future__ import annotations

import base64
import json
import queue
import re
import threading
import time
import uuid
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import nullcontext
from copy import deepcopy

from ..model import OrchestrationError
from ..telemetry import protect
from .process import command_options, encode_json, positive_integer, positive_number

_CREDENTIAL_NAME = re.compile(r"(?:api.?key|authorization|password|credential|private.?key|access.?token|refresh.?token|(?:^|_)token$|secret)", re.I)
_INCOMPLETE_CREDENTIAL = re.compile(
    r'''(?ix)(["']?(?:authorization|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key)["']?\s*[:=]\s*)(["'])([^"']*)\Z''')


class TelemetryDispatch:
    """One ordered optional worker; admission is never a durable receipt.

    Count and byte pressure are bounded independently. Health is read directly,
    so a broken collector is not needed to report its own failures. Transcript
    readers may request bounded acknowledgment and retry a stable event ID.
    """

    def __init__(self, observer, options=None):
        options = options or {}
        self.observer = observer
        self.capacity = positive_integer(options, "max_telemetry_pending", 64)
        self.maximum = positive_integer(options, "max_telemetry_bytes", 8 * 1024 * 1024)
        self.drain_timeout = positive_number(options, "telemetry_drain_timeout", 0.05)
        self._condition = threading.Condition()
        self._queue = queue.Queue(maxsize=self.capacity + 1)
        self._pending = 0
        self._bytes = 0
        self._closed = False
        self._receipts = {}
        self._counts = {"admitted": 0, "completed": 0, "failed": 0, "rejected": 0,
                        "source_recorded": 0, "source_failed": 0}
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    def _submit(self, event, retain_ack=False):
        identifier = event.get("event_id") if retain_ack else None
        with self._condition:
            if identifier in self._receipts:
                return self._receipts[identifier]
            try:
                size = len(encode_json(event))
            except BaseException:
                self._counts["rejected"] += 1
                return None
            if self._closed or self._pending >= self.capacity or self._bytes + size > self.maximum:
                self._counts["rejected"] += 1
                return None
            if retain_ack and len(self._receipts) >= self.capacity:
                self._counts["rejected"] += 1
                return None
            future = Future()
            if identifier is not None:
                self._receipts[identifier] = future
            self._pending += 1
            self._bytes += size
            self._counts["admitted"] += 1
            self._queue.put_nowait((event, future, size))
            return future

    def __call__(self, event):
        if threading.current_thread() is self._thread:
            return self._nested(event)
        future = self._submit(event)
        return {"status": "deferred" if future is not None else "unavailable", "receipt": None,
                "reason": "queued" if future is not None else "dispatch_pressure_or_closed"}

    def capture(self, event):
        if threading.current_thread() is self._thread:
            return self._nested(event)
        future = self._submit(event, retain_ack=True)
        if future is None:
            return {"status": "unavailable", "reason": "dispatch_pressure_or_closed"}
        try:
            return future.result(timeout=self.drain_timeout)
        except FutureTimeout:
            return {"status": "deferred", "reason": "awaiting_capture_ack"}

    def release(self, event_id):
        with self._condition:
            self._receipts.pop(event_id, None)

    def _nested(self, event):
        try:
            result = self.observer(event)
            if result is None:
                result = {"status": "recorded", "receipt": None, "boundary": "observer_return"}
        except BaseException:
            result = {"status": "unavailable", "reason": "observer_failed"}
        with self._condition:
            success = isinstance(result, dict) and result.get("status") in {"recorded", "duplicate"}
            self._counts["source_recorded" if success else "source_failed"] += 1
        return result

    def schedule(self, callback):
        """Run bounded source ingestion on the optional worker, off protocol IO."""
        with self._condition:
            if self._closed or self._pending >= self.capacity:
                self._counts["rejected"] += 1
                return False
            self._pending += 1
            self._counts["admitted"] += 1
            self._queue.put_nowait((callback, Future(), 0))
            return True

    def _work(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            event, future, size = item
            try:
                result = event() if callable(event) else self.observer(event)
                if result is None:
                    result = {"status": "recorded", "receipt": None, "boundary": "observer_return"}
            except BaseException:
                result = {"status": "unavailable", "reason": "observer_failed"}
            with self._condition:
                success = isinstance(result, dict) and result.get("status") in {"recorded", "duplicate"}
                self._counts["completed" if success else "failed"] += 1
                identifier = event.get("event_id") if isinstance(event, dict) else None
                if not success and self._receipts.get(identifier) is future:
                    self._receipts.pop(identifier, None)
                future.set_result(result)
                self._pending -= 1
                self._bytes -= size
                self._condition.notify_all()

    def drain(self, timeout=None):
        if threading.current_thread() is self._thread:
            return self.health()
        duration = self.drain_timeout if timeout is None else positive_number({"timeout": timeout}, "timeout", self.drain_timeout)
        deadline = time.monotonic() + duration
        with self._condition:
            while self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self.health()

    def health(self):
        with self._condition:
            return {**self._counts, "pending": self._pending, "pending_bytes": self._bytes,
                    "capacity": self.capacity, "max_bytes": self.maximum, "closed": self._closed,
                    "coverage": "gap" if self._counts["rejected"] or self._counts["failed"] or self._counts["source_failed"] else "pending" if self._pending else "observed" if self._counts["admitted"] else "unobserved"}

    def close(self):
        with self._condition:
            if not self._closed:
                self._closed = True
                self._queue.put_nowait(None)
        return self.drain()


def telemetry_dispatch(observer, options=None):
    return observer if observer is None or isinstance(observer, TelemetryDispatch) else TelemetryDispatch(observer, options)


def capture_origin(value=None) -> dict:
    """Detach origin data; transport attempts never use mutable current selection."""
    try:
        context = deepcopy(value) if isinstance(value, dict) else {}
        if context.get("attempt_id"):
            context["parent_attempt_id"] = context["attempt_id"]
        context["attempt_id"] = uuid.uuid4().hex
        return context
    except BaseException:
        return {"attempt_id": uuid.uuid4().hex}


def telemetry_secrets(options: dict) -> tuple[str, ...]:
    """Inspect only credentials explicitly made available to this source process."""
    try:
        environment = command_options(options)["env"]
        return tuple(value for key, value in environment.items() if value and _CREDENTIAL_NAME.search(key))
    except BaseException:
        return ()


def observe_telemetry(observer, event_type, payload, context, *, secrets=(), completeness="complete", reason=None, event_id=None, acknowledge=False):
    """Optional capture is independent of protocol and required evidence callbacks.

    Protection is shared with the pure telemetry contract. A failed protector has
    no unchecked fallback; a failed optional observer cannot replace the effect.
    """
    if observer is None:
        return False
    try:
        event = {"event_type": event_type, "payload": payload, "context": context,
                 "basis": "observed", "completeness": completeness}
        if reason is not None:
            event["reason"] = reason
        if event_id is not None:
            event["event_id"] = event_id
        protected = protect(deepcopy(event), secrets=secrets)
        result = observer.capture(protected) if acknowledge and isinstance(observer, TelemetryDispatch) else observer(protected)
        return result is None or isinstance(result, dict) and result.get("status") in {"recorded", "duplicate"}
    except BaseException:
        return False


def observe_wire(observer, event_type, data, context, *, secrets=(), complete=True,
                 total_bytes=None, reason=None, range_start=0, event_id=None, acknowledge=False, **fields):
    """Retain protected bytes and explicitly distinguish source/retained ranges.

    The reversible decode keeps safe binary diagnostics intact. Credential text
    is filtered before base64 encoding, including when the surrounding frame is
    malformed. A protector failure yields a safe suppression envelope.
    """
    if observer is None:
        return
    try:
        text = data.decode("utf-8", "surrogateescape")
        if not complete:
            # A retained prefix can stop inside a configured credential. Full
            # value replacement alone cannot protect that incomplete suffix.
            for secret in secrets:
                if not isinstance(secret, str) or not secret:
                    continue
                variants = (secret, json.dumps(secret, ensure_ascii=True)[1:-1])
                for variant in variants:
                    for size in range(min(len(text), len(variant) - 1), 0, -1):
                        if text.endswith(variant[:size]):
                            text = text[:-size] + "[REDACTED]"
                            break
        try:
            json.loads(text)
        except (ValueError, RecursionError):
            text = _INCOMPLETE_CREDENTIAL.sub(lambda match: match[1] + match[2] + "[REDACTED]", text)
        protected = protect(text, secrets=secrets).encode("utf-8", "surrogateescape")
        payload = {**fields, "content": base64.b64encode(protected).decode("ascii"), "encoding": "base64",
                   "captured_bytes": len(protected), "range_start": range_start, "range_end": range_start + len(data),
                   "total_bytes": total_bytes, "protection": "unchanged" if data == protected else "redacted",
                   "representation": "protected_bytes", "source_bytes": len(data)}
        return observe_telemetry(observer, event_type, payload, context, secrets=secrets,
                                 completeness="complete" if complete else "partial", reason=reason, event_id=event_id, acknowledge=acknowledge)
    except BaseException:
        observe_telemetry(observer, event_type, {"representation": "suppressed", "captured_bytes": 0}, context,
                          secrets=secrets, completeness="suppressed", reason="credential_filter_unavailable")


class StderrCapture:
    """Drain independently even after the bounded retained prefix is full."""

    def __init__(self, process, maximum):
        self.process, self.maximum = process, maximum
        self.data = bytearray()
        self.total = 0
        self.eof = False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while True:
                block = self.process.stderr.read(65536)
                if not block:
                    self.eof = True
                    return
                self.total += len(block)
                self.data.extend(block[:max(0, self.maximum - len(self.data))])
        except (OSError, ValueError):
            pass

    def emit(self, observer, context, secrets):
        self.thread.join(timeout=1)
        complete = self.eof and self.total == len(self.data)
        observe_wire(observer, "process.stderr", bytes(self.data), context, secrets=secrets,
                     complete=complete, total_bytes=self.total if self.eof else None,
                     reason=None if complete else "retention_limit" if self.total > len(self.data) else "interrupted",
                     channel="stderr")


class TelemetrySource:
    """Optional telemetry owned by one source adapter.

    ``_telemetry_binding`` lets an adapter refuse a late binding; dispatch
    bounds come from the adapter's ``options`` when it has them.
    """

    _telemetry_observer = None

    def _telemetry_binding(self):
        return nullcontext()

    def bind_telemetry(self, observer):
        if observer is not None and not callable(observer):
            raise OrchestrationError("Telemetry observer must be callable.", "invalid_configuration")
        with self._telemetry_binding():
            previous = self._telemetry_observer
            self._telemetry_observer = telemetry_dispatch(observer, getattr(self, "options", None))
        if isinstance(previous, TelemetryDispatch) and previous is not self._telemetry_observer:
            previous.close()

    def telemetry_health(self):
        observer = self._telemetry_observer
        return observer.health() if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def drain_telemetry(self, timeout=None):
        observer = self._telemetry_observer
        return observer.drain(timeout) if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def _telemetry_credentials(self) -> tuple[str, ...]:
        """Credentials treated in this source's captures; none without an observer."""
        return telemetry_secrets(self.options) if self._telemetry_observer is not None else ()

    def _close_telemetry(self):
        if isinstance(self._telemetry_observer, TelemetryDispatch):
            self._telemetry_observer.close()
