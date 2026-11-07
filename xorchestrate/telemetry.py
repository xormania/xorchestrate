"""Observation protection and authorized views, independent of storage/exporters.

Capture is optional. Application correctness evidence keeps its existing owner.
No source payload or captured instruction is interpreted as configuration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy

from .model import OrchestrationError

_CREDENTIALS = frozenset({
    "authorization", "password", "passwd", "secret", "apikey", "accesstoken",
    "refreshtoken", "idtoken", "credential", "credentials", "cookie",
    "setcookie", "clientsecret", "token", "privatekey",
})
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_TEXT_CREDENTIAL = re.compile(
    r'''(?ix)(["']?(?:authorization|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key)["']?\s*[:=]\s*)(["'])(.*?)(\2)'''
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def protect(value, secrets=()):
    """Protect explicit credential fields and supplied values, retaining safe text.

    This is a versioned, bounded-source treatment, not a claim to detect every
    secret in arbitrary text. Producers supply only configured credential values.
    Unchanged serialized JSON retains its original whitespace and encoding text.
    """
    secrets = tuple(sorted({item for item in secrets if isinstance(item, str) and item}, key=len, reverse=True))

    def text(source):
        result = source
        try:
            parsed = json.loads(source)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            cleaned = walk(parsed)
            if cleaned != parsed:
                result = canonical(cleaned)
        for secret in secrets:
            result = result.replace(secret, "[REDACTED]")
        result = _BEARER.sub("Bearer [REDACTED]", result)
        return _TEXT_CREDENTIAL.sub(lambda match: match[1] + match[2] + "[REDACTED]" + match[4], result)

    def walk(item):
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                normalized = key.lower().replace("_", "").replace("-", "")
                result[text(key)] = "[REDACTED]" if normalized in _CREDENTIALS else walk(child)
            return result
        if isinstance(item, (tuple, list)):
            return [walk(child) for child in item]
        return text(item) if isinstance(item, str) else item

    return walk(deepcopy(value))


def record_observation(sink, event_type, payload, context, **metadata):
    """Never convert an optional capture failure into a different action outcome."""
    if sink is None:
        return {"status": "disabled", "receipt": None, "reason": "not_configured"}
    try:
        result = sink.record(event_type, deepcopy(payload), deepcopy(context), **metadata)
        return result if isinstance(result, dict) else {"status": "unavailable", "reason": "invalid_capture_result"}
    except Exception:
        return {"status": "unavailable", "receipt": None, "reason": "capture_error"}


class TelemetryViews:
    """Read through explicit current-authorization and projection callbacks."""

    def __init__(self, sink, store, context, authorize, verify, projector):
        self.sink = sink
        self.store = store
        self.context = context
        self.authorize = authorize
        self.verify = verify
        self.projector = projector

    def _configured(self):
        if self.sink is None:
            raise OrchestrationError("Telemetry capture is not configured.", "telemetry_unavailable")
        return self.sink

    def inspect(self, row, actor, action):
        row = deepcopy(row)
        scope = row.get("context", {})
        if not any(scope.get(key) for key in ("work_id", "session_id", "operation_id")):
            resolve = getattr(self.sink, "resolve_scope", None)
            resolved = resolve(row) if callable(resolve) else None
            if resolved:
                scope = {**scope, **resolved}
                row["context"] = scope
            elif scope.get("actor") != actor:
                raise OrchestrationError("Unresolved observations are private to their originating actor.", "denied")
        arguments = {key: scope[key] for key in ("work_id", "session_id", "operation_id") if scope.get(key)}
        current = self.context(actor, arguments)
        contexts = [current]
        self.authorize(action, current)
        for key, read_action in (("work_id", "get_work"), ("operation_id", "get_operation"),
                                 ("session_id", "native_observations")):
            if arguments.get(key):
                self.authorize(read_action, current)
        payload = row.get("payload")
        if isinstance(payload, dict) and isinstance(payload.get("arguments"), dict):
            target = self.context(actor, payload["arguments"])
            self.authorize(action, target)
            if target.get("work"):
                self.authorize("get_work", target)
            contexts.append(target)
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            payload["result"], nested = self.projector.project_result(action, actor, payload.get("action"), payload["result"], {})
            contexts.extend(nested)
        return row, contexts

    def _verify(self, contexts):
        with self.store.transaction() as tx:
            for context in contexts:
                self.verify(tx, context)

    @staticmethod
    def _descriptor(row):
        visible = deepcopy(row)
        stored = row.get("payload_storage")
        if stored is None:
            content = canonical(row.get("payload")).encode("utf-8")
            stored = {"digest": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
                      "media_type": "application/json"}
        visible["payload"] = {"read_action": "telemetry_payload", "event_id": row["event_id"],
                              **deepcopy(stored)}
        visible["payload_available"] = True
        return visible

    def events(self, arguments, actor, action="telemetry_events"):
        sink = self._configured()
        limit = arguments.get("limit", 100)
        budget = arguments.get("max_bytes", 262144)
        cursor = arguments.get("after", 0)
        visible, contexts = [], []
        scanned = 0
        cutoff = arguments.get("cutoff")
        if cutoff is None:
            cutoff = sink.status().get("max_sequence", 0) or 0
        filters = {key: arguments[key] for key in ("event_type", "work_id", "session_id", "operation_id") if key in arguments}

        def page(items, position):
            return {"events": items, "next_after": position, "cutoff": cutoff,
                    "scan_limited": scanned >= 10000 and position < cutoff, "projection_version": 1}

        page_full = False
        while len(visible) < limit and scanned < 10000 and not page_full:
            rows = sink.query(after=cursor, limit=min(1000, 10000 - scanned), **filters)
            if not rows or rows[0]["sequence"] > cutoff:
                break
            reached_cutoff = False
            for row in rows:
                if row["sequence"] > cutoff:
                    reached_cutoff = True
                    break
                previous = cursor
                cursor = row["sequence"]
                scanned += 1
                try:
                    item, basis = self.inspect(row, actor, action)
                except OrchestrationError as error:
                    if error.code in {"denied", "review_required", "not_found"}:
                        continue
                    raise
                if item.get("payload_storage") or len(canonical(page([item], cursor)).encode()) > budget:
                    item = self._descriptor(item)
                if len(canonical(page([*visible, item], cursor)).encode()) > budget:
                    if not visible:
                        # Identity/envelope metadata itself cannot fit this chosen
                        # budget. Fail explicitly rather than truncate identity.
                        raise OrchestrationError("Telemetry event envelope exceeds the selected page budget.", "invalid_query")
                    cursor = previous
                    scanned -= 1
                    page_full = True
                    break
                visible.append(item)
                contexts.extend(basis)
                if len(visible) == limit:
                    break
            if reached_cutoff or cursor >= cutoff or len(rows) < 1000:
                break
        self._verify(contexts)
        return page(visible, cursor)

    def status(self, actor):
        sink = self._configured()
        observed = self.events({"limit": 1000}, actor, "telemetry_status")["events"]
        state = sink.status()
        # Dataset-wide counts and cursor positions can reveal denied membership.
        result = {key: deepcopy(state[key]) for key in ("status", "reason", "schema_version",
                  "capture_failures", "publication", "configuration_identity") if key in state}
        sources = {name: {"configured": True, "observed": False, "coverage": "unknown"}
                   for name, source in state.get("sources", {}).items() if source.get("configured")}
        for row in observed:
            name = row["event_type"].partition(".")[0]
            source = sources.setdefault(name, {"configured": False})
            source.update(observed=True, coverage="observed", last_event_id=row["event_id"])
        result["sources"] = sources
        result.update(event_count=len(observed), visible_event_count=len(observed), coverage="observed" if observed else "unknown",
                      last_capture=observed[-1].get("captured_at") if observed else None,
                      count_complete=len(observed) < 1000)
        return result

    def payload(self, arguments, actor):
        sink = self._configured()
        envelope = sink.get_event(arguments["event_id"], hydrate=False)
        if envelope is None:
            raise OrchestrationError("Telemetry payload is unavailable.", "not_found")
        _, envelope_contexts = self.inspect(envelope, actor, "telemetry_payload")
        self._verify(envelope_contexts)
        original = sink.get_event(arguments["event_id"])
        visible, contexts = self.inspect(original, actor, "telemetry_payload")
        contexts.extend(envelope_contexts)
        projected = visible.get("payload") != original.get("payload")
        if arguments.get("digest"):
            if projected:
                raise OrchestrationError("The original payload is not fully visible.", "denied")
            result = sink.read_payload(**arguments)
        else:
            data = canonical(visible.get("payload")).encode("utf-8")
            offset, limit = arguments.get("offset", 0), min(arguments.get("limit", 65536), 65536)
            part = data[offset:offset + limit]
            try:
                content = part.decode("utf-8")
            except UnicodeDecodeError as error:
                if error.start and error.reason == "unexpected end of data":
                    part = part[:error.start]
                    content = part.decode("utf-8")
                else:
                    content = None
            result = {"event_id": arguments["event_id"], "content": content,
                      "data_base64": base64.b64encode(part).decode(),
                      "offset": offset, "next_offset": offset + len(part) if offset + len(part) < len(data) else None,
                      "size_bytes": len(data), "digest": hashlib.sha256(data).hexdigest(),
                      "encoding": "utf-8" if content is not None else "base64",
                      "projection_version": 1, "projected": projected}
        self._verify(contexts)
        return result

    def usage(self, arguments, actor):
        events = self.events({**arguments, "limit": min(arguments.get("limit", 1000), 1000)}, actor, "telemetry_usage")
        measurements = []

        def visit(value, path):
            if isinstance(value, dict):
                for key, child in value.items():
                    if "token" in key.lower() or key in {"usage", "cost", "costUsdTicks"}:
                        measurements.append({"path": path + [key], "value": deepcopy(child)})
                    elif isinstance(child, (dict, list)):
                        visit(child, path + [key])
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    visit(child, path + [index])

        samples = []
        for row in events["events"]:
            measurements = []
            visit(row.get("payload"), [])
            if measurements:
                samples.append({"event_id": row["event_id"], "sequence": row["sequence"],
                                "context": row["context"], "measurements": measurements})
        return {"samples": samples, "next_after": events["next_after"], "normalization": "raw-v1",
                "cost": None, "cost_status": "unknown", "totals_status": "not_normalized"}
