"""Optional operational signals, independent of the durable evidence corpus.

The narrow content mapping deliberately excludes prompts, results, credentials,
authority and baggage. OTLP/HTTP JSON follows OTLP 1.11.1; the instrumentation
mapping is Xorchestrate's own versioned mapping, not unstable GenAI conventions.
Protocol: https://opentelemetry.io/docs/specs/otlp/
Examples: https://github.com/open-telemetry/opentelemetry-proto/tree/main/examples
"""

import hashlib
import json
import re
import time
import uuid
from copy import deepcopy
from math import isfinite
from threading import Lock
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAPPING_VERSION = "xorchestrate-operational-v1"
OTLP_VERSION = "1.11.1"
_LABELS = ("boundary", "outcome", "component", "source")
_CORRELATION = ("event_id", "run_id", "session_id", "operation_id", "attempt_id", "parent_attempt_id")
_OUTCOMES = frozenset(("success", "succeeded", "complete", "completed", "recorded", "duplicate",
                       "failure", "failed", "error", "unavailable", "deferred", "rejected",
                       "unknown", "interrupted", "started", "partial", "suppressed", "ok"))
_BOUNDARIES = frozenset(("application", "provider", "native", "evaluator", "capture", "query", "export", "artifact"))
_ACTIONS = frozenset(("request", "result", "failure", "success", "duration", "count", "bytes", "backlog",
                      "operation", "started", "completed", "status", "session", "read", "write", "snapshot",
                      "response", "validation"))


class LocalOperationalSink:
    """An explicitly selected, process-local sink suitable for diagnostics."""

    def __init__(self, *, capacity=1000):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("invalid local signal capacity")
        self.capacity = capacity
        self.events = []
        self.dropped = 0
        self._lock = Lock()

    def emit(self, event):
        with self._lock:
            if len(self.events) == self.capacity:
                self.events.pop(0)
                self.dropped += 1
            self.events.append(deepcopy(event))


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _safe_signal(event):
    # Values used as labels are bounded vocabularies. Caller-chosen identities
    # belong in log/span links and never expand metric label cardinality.
    kind = event.get("kind", "log")
    if kind not in ("log", "metric", "span"):
        raise ValueError("unknown operational signal kind")
    boundary = event.get("boundary", "application")
    if boundary not in _BOUNDARIES:
        boundary = "application"
    outcome = event.get("outcome", event.get("status", "unknown"))
    if outcome not in _OUTCOMES:
        outcome = "unknown"
    result = {"kind": kind, "boundary": boundary, "outcome": outcome,
              "name": "xorchestrate." + boundary, "mapping_version": MAPPING_VERSION,
              "time_unix_nano": time.time_ns()}
    proposed_name = event.get("name", "")
    if isinstance(proposed_name, str):
        parts = proposed_name.removeprefix("xorchestrate.").split(".")
        if len(parts) == 2 and parts[0] == boundary and parts[1] in _ACTIONS:
            result["name"] += "." + parts[1]
    for key in ("duration_ms", "value", "bytes", "backlog"):
        value = event.get(key)
        if _number(value) and value >= 0:
            result[key] = value
    if kind == "metric":
        # The finite boundary vocabulary defines metric names as well as labels.
        measurement = event.get("measurement", proposed_name.split(".")[-1] if isinstance(proposed_name, str) else "count")
        if measurement not in ("count", "duration", "bytes", "backlog", "failures"):
            measurement = "count"
        default_unit = {"duration": "ms", "bytes": "By"}.get(measurement, "1")
        unit = event.get("unit", default_unit)
        allowed_units = ("ms", "s") if measurement == "duration" else (default_unit,)
        if unit not in allowed_units:
            raise ValueError("metric unit does not match its measurement")
        if measurement == "duration" and "value" not in event and unit != "ms":
            raise ValueError("duration_ms quantity requires milliseconds")
        result["unit"] = unit
        result["measurement"] = measurement
        result["name"] = "xorchestrate." + boundary + "." + measurement
        value_key = {"duration": "duration_ms", "bytes": "bytes", "backlog": "backlog"}.get(measurement)
        measured = event.get("value", event.get(value_key)) if value_key else event.get("value", 1)
        if not _number(measured) or measured < 0:
            raise ValueError("metric quantity is unavailable or invalid")
        result["value"] = measured
    else:
        context = event.get("context", {})
        if not isinstance(context, dict):
            context = {}
        for key in _CORRELATION:
            value = event.get(key, context.get(key))
            if isinstance(value, str) and len(value) <= 256:
                result[key] = value
        for key, length in (("trace_id", 32), ("span_id", 16), ("parent_span_id", 16)):
            value = event.get(key)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{" + str(length) + "}", value) and int(value, 16):
                result[key] = value.lower()
    return result


class OperationalExporter:
    """Fail-open operational export; sampling has no reference to corpus writes.

    This adapter has no collector/repository dependency. The caller records its
    durable observation independently before optionally emitting signals here.
    The default sink is bounded local memory; no network endpoint is inferred.
    """

    def __init__(self, sink=None, sample=None):
        self.sink = sink if sink is not None else LocalOperationalSink()
        self.sample = sample if sample is not None else (lambda event: True)
        self.exported = self.sampled_out = self.failures = 0
        self._lock = Lock()

    def record(self, event):
        try:
            signal = _safe_signal(deepcopy(event))
            if not self.sample(deepcopy(signal)):
                with self._lock:
                    self.sampled_out += 1
                return {"status": "sampled_out", "mapping_version": MAPPING_VERSION}
            self.sink.emit(signal)
            with self._lock:
                self.exported += 1
            return {"status": "exported", "mapping_version": MAPPING_VERSION}
        except Exception:
            with self._lock:
                self.failures += 1
            return {"status": "unavailable", "reason": "operational_export_failed",
                    "mapping_version": MAPPING_VERSION}

    def status(self):
        with self._lock:
            return {"exported": self.exported, "sampled_out": self.sampled_out, "failures": self.failures,
                    "mapping_version": MAPPING_VERSION,
                    "corpus_coverage": "independent", "sink": type(self.sink).__name__}


class ObservationSignals:
    """Correlate observed request/terminal boundaries into operational spans.

    Duration is the local monotonic interval between callbacks, including local
    observation overhead. It is not provider-reported latency. Missing starts
    stay missing. Pending correlation is bounded and never contains source text.
    This process-local interpretation does not change or sample corpus writes.
    """

    def __init__(self, exporter, *, clock=None, capacity=1000):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("invalid operational correlation capacity")
        self.exporter = exporter
        self.clock = clock if clock is not None else time.monotonic
        self.capacity = capacity
        self._pending = {}
        self._lock = Lock()
        self.missing_starts = self.evicted = 0

    def observe(self, event_type, context, *, receipt=None, capture_status="unknown", capture_duration_ms=None,
                observed_monotonic=None):
        """Emit safe operational metadata after an independent capture attempt."""
        try:
            return self._observe(event_type, context, receipt, capture_status, capture_duration_ms, observed_monotonic)
        except Exception:
            # Even malformed source metadata or a clock failure is optional.
            return {"status": "unavailable", "reason": "operational_correlation_failed"}

    def _observe(self, event_type, context, receipt, capture_status, capture_duration_ms, observed_monotonic):
        # Capture the observation's own boundary before any sink can wait. A
        # queued deployment passes the original callback time explicitly.
        now = observed_monotonic if _number(observed_monotonic) else self.clock()
        source, _, phase = event_type.partition(".")
        boundary = {"application": "application", "native": "native", "capability": "provider",
                    "evaluator": "evaluator", "process": "provider"}.get(source, "capture")
        receipt = receipt or {}
        signal = {"boundary": boundary, "event_id": receipt.get("event_id"), "context": context,
                  "outcome": "failed" if phase in ("failed", "error") else
                             "completed" if phase == "completed" else
                             "started" if phase in ("request", "requested") else "unknown"}
        self.exporter.record({**signal, "kind": "log", "name": boundary + "." +
                              (phase if phase in _ACTIONS else "status")})
        self.exporter.record({"kind": "metric", "boundary": "capture", "name": "capture.count",
                              "outcome": capture_status, "unit": "1", "value": 1})
        if _number(capture_duration_ms) and capture_duration_ms >= 0:
            self.exporter.record({"kind": "metric", "boundary": "capture", "measurement": "duration",
                                  "outcome": capture_status, "unit": "ms", "value": capture_duration_ms})
        if capture_status == "duplicate":
            return {"status": "observed", "span": "duplicate_observation"}
        attempt = context.get("attempt_id")
        if not attempt:
            return {"status": "observed", "span": "unattributed"}
        key = (source, attempt, context.get("native_call_id"))
        started = phase in ("request", "requested")
        ended = phase in ("completed", "failed", "response", "result", "error")
        with self._lock:
            if started:
                # The first observed start is the boundary. Repeated snapshots
                # cannot silently move the start forward.
                if key not in self._pending:
                    if len(self._pending) == self.capacity:
                        self._pending.pop(next(iter(self._pending)))
                        self.evicted += 1
                    trace_basis = context.get("parent_attempt_id") or attempt
                    self._pending[key] = (now, hashlib.sha256(str(trace_basis).encode()).hexdigest()[:32],
                                          hashlib.sha256(repr(key).encode()).hexdigest()[:16])
                return {"status": "observed", "span": "pending"}
            begin = self._pending.pop(key, None) if ended else None
            if ended and begin is None:
                self.missing_starts += 1
        if begin is not None:
            duration = max(0, now - begin[0]) * 1000
            self.exporter.record({**signal, "kind": "span", "name": boundary + ".request",
                                  "trace_id": begin[1], "span_id": begin[2], "duration_ms": duration})
            self.exporter.record({"kind": "metric", "boundary": boundary, "measurement": "duration",
                                  "outcome": signal["outcome"], "unit": "ms", "value": duration})
            return {"status": "observed", "span": "completed", "duration_basis": "local_callback_interval"}
        return {"status": "observed", "span": "missing_start" if ended else "not_terminal"}

    def status(self):
        with self._lock:
            correlation = {"pending": len(self._pending), "missing_starts": self.missing_starts,
                           "evicted": self.evicted, "duration_basis": "local_callback_interval"}
        return {**self.exporter.status(), "correlation": correlation}


def _attributes(values):
    result = []
    for key, value in values.items():
        if isinstance(value, bool):
            encoded = {"boolValue": value}
        elif isinstance(value, int):
            encoded = {"intValue": str(value)}
        elif isinstance(value, float):
            encoded = {"doubleValue": value}
        else:
            encoded = {"stringValue": str(value)}
        result.append({"key": key, "value": encoded})
    return result


class OtlpOperationalSink:
    """Explicit OTLP/HTTP JSON transport with bounded network wait and no retry.

    ``transport(url, payload_bytes, timeout)`` may replace HTTP for tests or a
    deployment-specific authenticated transport. It returns response JSON bytes
    or a decoded mapping. Partial rejection is reported as export failure.
    """

    def __init__(self, endpoint, *, transport=None, timeout=1.0):
        parts = urlsplit(endpoint)
        if parts.scheme not in ("https", "http") or not parts.netloc or parts.query or parts.fragment or parts.username:
            raise ValueError("invalid explicit OTLP endpoint")
        if not _number(timeout) or not 0 < timeout <= 60:
            raise ValueError("invalid OTLP timeout")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.transport = transport if transport is not None else self._http

    @staticmethod
    def _http(url, payload, timeout):
        request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=timeout) as response:
            # Responses cannot force unbounded memory use.
            return response.read(65537)

    def emit(self, event):
        # Reapply the content policy even if a caller bypassed OperationalExporter.
        signal = _safe_signal(event)
        timestamp = str(signal["time_unix_nano"])
        resource = {"attributes": _attributes({"service.name": "xorchestrate"})}
        scope = {"name": "xorchestrate.telemetry", "version": MAPPING_VERSION}
        attributes = {key: signal[key] for key in _LABELS if key in signal}
        kind = signal["kind"]
        if kind == "span":
            duration = signal.get("duration_ms", 0)
            span = {"traceId": signal.get("trace_id", uuid.uuid4().hex),
                    "spanId": signal.get("span_id", uuid.uuid4().hex[:16]), "name": signal["name"],
                    "kind": 1, "startTimeUnixNano": str(max(0, int(timestamp) - int(duration * 1_000_000))),
                    "endTimeUnixNano": timestamp,
                    "attributes": _attributes({**attributes, **{key: signal[key] for key in _CORRELATION if key in signal}}),
                    "status": {"code": 2 if signal["outcome"] in ("failed", "failure", "error") else 0}}
            if "parent_span_id" in signal:
                span["parentSpanId"] = signal["parent_span_id"]
            body = {"resourceSpans": [{"resource": resource, "scopeSpans": [{"scope": scope, "spans": [span]}]}]}
            suffix = "traces"
        elif kind == "metric":
            measurement = signal["measurement"]
            value = signal["value"]
            point = {"timeUnixNano": timestamp, "attributes": _attributes(attributes), "asDouble": value}
            metric = {"name": signal["name"], "unit": signal["unit"],
                      "description": "Xorchestrate boundary observation; " + measurement}
            if measurement in ("count", "failures", "bytes"):
                metric["sum"] = {"aggregationTemporality": 1, "isMonotonic": True,
                                 "dataPoints": [{**point, "startTimeUnixNano": timestamp}]}
            elif measurement == "duration":
                metric["histogram"] = {"aggregationTemporality": 1, "dataPoints": [{
                    "timeUnixNano": timestamp, "startTimeUnixNano": timestamp,
                    "attributes": _attributes(attributes), "count": "1", "sum": value,
                    "min": value, "max": value, "bucketCounts": ["1"], "explicitBounds": []}]}
            else:
                metric["gauge"] = {"dataPoints": [point]}
            body = {"resourceMetrics": [{"resource": resource, "scopeMetrics": [{"scope": scope, "metrics": [metric]}]}]}
            suffix = "metrics"
        else:
            record = {"timeUnixNano": timestamp, "severityNumber": 9,
                      "body": {"stringValue": signal["name"]},
                      "attributes": _attributes({**attributes, **{key: signal[key] for key in _CORRELATION if key in signal}})}
            for local, wire in (("trace_id", "traceId"), ("span_id", "spanId")):
                if local in signal:
                    record[wire] = signal[local]
            body = {"resourceLogs": [{"resource": resource, "scopeLogs": [{"scope": scope, "logRecords": [record]}]}]}
            suffix = "logs"
        response = self.transport(self.endpoint + "/v1/" + suffix,
                                  json.dumps(body, separators=(",", ":"), allow_nan=False).encode(), self.timeout)
        if isinstance(response, bytes):
            if len(response) > 65536:
                raise ValueError("OTLP response exceeds limit")
            response = json.loads(response or b"{}")
        if not isinstance(response, dict):
            raise ValueError("invalid OTLP response")
        partial = response.get("partialSuccess", {})
        if partial and (partial.get("errorMessage") or any(int(value) for key, value in partial.items() if key.startswith("rejected"))):
            raise ValueError("OTLP collector rejected operational signals")
