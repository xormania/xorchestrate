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


def dispatch_pressure(value):
    """Expose bounded pipeline health without corpus membership or identities."""
    if not isinstance(value, dict):
        return {"status": "unavailable", "coverage": "unknown"}
    result = {key: value[key] for key in ("pending", "pending_bytes", "capacity", "max_bytes", "rejected",
              "failed", "source_failed") if type(value.get(key)) is int and value[key] >= 0}
    if type(value.get("closed")) is bool:
        result["closed"] = value["closed"]
    if value.get("coverage") in {"gap", "pending", "observed", "unobserved", "unknown"}:
        result["coverage"] = value["coverage"]
    if value.get("status") in {"disabled", "unavailable"}:
        result["status"] = value["status"]
    return result


class TelemetryViews:
    """Read through explicit current-authorization and projection callbacks."""

    def __init__(self, sink, store, context, authorize, verify, projector, authority=None):
        self.sink = sink
        self.store = store
        self.context = context
        self.authorize = authorize
        self.verify = verify
        self.projector = projector
        self.authority = authority if authority is not None else (lambda: None)

    def _configured(self):
        if self.sink is None:
            raise OrchestrationError("Telemetry capture is not configured.", "telemetry_unavailable")
        return self.sink

    def inspect(self, row, actor, action, *, resolve_scope=None, load_observation=None):
        row = deepcopy(row)
        scope = row.get("context", {})
        if not any(scope.get(key) for key in ("work_id", "session_id", "operation_id")):
            resolve = resolve_scope if resolve_scope is not None else getattr(self.sink, "resolve_scope", None)
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
        application_envelope = row.get("event_type") in {
            "application.requested", "application.completed", "application.failed"}
        execution = payload
        if application_envelope and row.get("payload_storage"):
            # A descriptor does not carry the typed invocation's source scope.
            # Authorize the outer resource first, then inspect the retained
            # application envelope without publishing its hydrated content.
            load = load_observation if load_observation is not None else self.sink.get_event
            original = load(row["event_id"])
            if original is None:
                raise OrchestrationError("Telemetry source changed during inspection.", "conflict")
            execution = original.get("payload")
        if isinstance(execution, dict) and isinstance(execution.get("arguments"), dict):
            if application_envelope:
                target = self.projector._execution(actor, execution, {}, scope.get("actor", actor))
            else:
                target = self.context(actor, execution["arguments"])
            self.authorize(action, target)
            # The outer row may already carry work scope, so the derived
            # target needs the same per-resource read checks as the row.
            for key, read_action in (("work", "get_work"), ("operation", "get_operation"),
                                     ("session", "native_observations")):
                if target.get(key):
                    self.authorize(read_action, target)
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
        return self._event_page(arguments, actor, action)[0]

    def _event_page(self, arguments, actor, action, *, scan_limit=10000):
        """Return a byte-bounded page with private raw-scan accounting."""
        sink = self._configured()
        limit = arguments.get("limit", 100)
        budget = arguments.get("max_bytes", 262144)
        cursor = arguments.get("after", 0)
        visible, contexts = [], []
        scanned = 0
        cutoff = arguments.get("cutoff")
        if cutoff is None:
            cutoff = sink.status().get("max_sequence", 0) or 0
        filters = {key: arguments[key] for key in ("event_type", "work_id", "session_id", "operation_id", "run_id", "attempt_id") if key in arguments}
        complete = cursor >= cutoff

        def page(items, position):
            return {"events": items, "next_after": position, "cutoff": cutoff,
                    "scan_limited": scanned >= scan_limit and not complete,
                    "complete": complete, "projection_version": 1}

        page_full = False
        while len(visible) < limit and scanned < scan_limit and not page_full and not complete:
            batch_limit = min(1000, scan_limit - scanned)
            scanner = getattr(sink, "scan", None)
            if callable(scanner):
                batch = scanner(after=cursor, limit=batch_limit, **filters)
                rows = batch["events"]
                batch_cursor, examined, exhausted = batch["next_after"], batch["scanned"], batch["exhausted"]
            else:
                rows = sink.query(after=cursor, limit=batch_limit, **filters)
                batch_cursor = rows[-1]["sequence"] if rows else cursor
                examined, exhausted = len(rows), len(rows) < batch_limit
            if not 0 <= examined <= batch_limit or (not exhausted and batch_cursor <= cursor):
                raise OrchestrationError("Telemetry source did not provide bounded scan progress.", "invalid_query")
            scanned += examined
            consumed = True
            for index, row in enumerate(rows):
                if row["sequence"] > cutoff:
                    cursor, complete, consumed = cutoff, True, False
                    break
                previous = cursor
                cursor = row["sequence"]
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
                    page_full, consumed = True, False
                    break
                visible.append(item)
                contexts.extend(basis)
                if len(visible) == limit:
                    consumed = index == len(rows) - 1
                    break
            if consumed:
                cursor = min(max(cursor, batch_cursor), cutoff)
                complete = exhausted or cursor >= cutoff
        self._verify(contexts)
        return page(visible, cursor), scanned, contexts

    def status(self, actor):
        sink = self._configured()
        state = sink.status()
        # Dataset-wide counts and cursor positions can reveal denied membership.
        result = {key: deepcopy(state[key]) for key in ("status", "reason", "schema_version",
                  "capture_failures", "publication", "configuration_identity") if key in state}
        sources = {name: {"configured": True, "observed": False, "coverage": "unknown"}
                   for name, source in state.get("sources", {}).items() if source.get("configured")}
        count, cursor, scanned, last_capture, complete = 0, 0, 0, None, False
        contexts = []
        cutoff = state.get("max_sequence", 0) or 0
        while not complete and scanned < 10000:
            page, examined, basis = self._event_page({"after": cursor, "cutoff": cutoff, "limit": 1000},
                                                   actor, "telemetry_status", scan_limit=10000 - scanned)
            scanned += examined
            contexts.extend(basis)
            for row in page["events"]:
                count += 1
                last_capture = row.get("captured_at")
                name = row["event_type"].partition(".")[0]
                source = sources.setdefault(name, {"configured": False})
                source.update(observed=True, coverage="observed", last_event_id=row["event_id"])
            complete = page["complete"]
            if page["next_after"] <= cursor and not complete:
                break
            cursor = page["next_after"]
        self._verify(contexts)
        result["sources"] = sources
        if isinstance(state.get("operational"), dict):
            operational = state["operational"]
            result["operational"] = {"configured": True, "corpus_coverage": "independent",
                                     "status": operational.get("status", "unknown"),
                                     "dispatch": dispatch_pressure(operational.get("dispatch")),
                                     "export_failures": operational.get("failures", 0)}
        result.update(event_count=count, visible_event_count=count, coverage="observed" if count else "unknown",
                      last_capture=last_capture, count_complete=complete,
                      last_capture_complete=complete, source_coverage_complete=complete)
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
        from .telemetry_views import usage_summary

        events = self._analysis_page(arguments, actor, "telemetry_usage")
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
        self._verify(events["_contexts"])
        normalized = usage_summary(events["events"])
        # Retained observations are exact payload reads; an analysis response is
        # a bounded projection with explicit source references when content is
        # too large to inline. The legacy raw sample view remains available.
        normalized.update(samples=samples, next_after=events["next_after"], cutoff=events["cutoff"],
                          scan_limited=events["scan_limited"], complete=events["complete"],
                          normalization=normalized["normalization_version"],
                          cost=None, cost_status="unknown", totals_status="normalized" if normalized["source_event_ids"] else "unknown",
                          population={"basis": "authorized returned page", "after": arguments.get("after", 0),
                                      "complete": events["complete"]})
        return self._bounded_analysis(normalized, arguments.get("max_bytes", 262144))

    def _analysis_page(self, arguments, actor, action):
        """Hydrate authorized descriptors, then authorize their typed closure."""
        sink = self._configured()
        page = self.events({**arguments, "limit": min(arguments.get("limit", 1000), 1000)}, actor, action)
        hydrated, contexts = [], []
        for row in page["events"]:
            if row.get("payload_storage") or row.get("payload_available"):
                original = sink.get_event(row["event_id"])
                if original is None:
                    raise OrchestrationError("Telemetry source changed during analysis.", "conflict")
                row = original
            row, basis = self.inspect(row, actor, action)
            contexts.extend(basis)
            hydrated.append(row)
        self._verify(contexts)
        return {**page, "events": hydrated, "_contexts": contexts}

    def _revalidate_analysis_union(self, pages, actor, action):
        """Reauthorize every returned row after all population reads finish."""
        def validate():
            contexts = [context for page in pages for context in page["_contexts"]]
            for page in pages:
                for row in page["events"]:
                    current, basis = self.inspect(row, actor, action)
                    if current.get("payload") != row.get("payload"):
                        # A smaller projection would silently change the cohort
                        # after its measures and denominators were calculated.
                        raise OrchestrationError("Comparison authority changed during the population reads.", "denied")
                    contexts.extend(basis)
            self._verify(contexts)

        self._consistent_authorization(validate)

    def _consistent_authorization(self, validate):
        """Use one stable configured policy revision for the complete closure."""
        for _ in range(2):
            before = self.authority()
            validate()
            if self.authority() == before:
                return
        raise OrchestrationError("Telemetry authority changed during closure authorization.", "conflict")

    @staticmethod
    def _bounded_analysis(result, budget):
        if len(canonical(result).encode()) <= budget:
            return result
        result = deepcopy(result)
        raw = result.pop("raw_observations", [])
        result["raw_observations_omitted"] = True
        result["source_references"] = [{"event_id": row["event_id"], "read_action": "telemetry_payload"} for row in raw]
        if len(canonical(result).encode()) > budget:
            result["samples"] = [{"event_id": item["event_id"], "sequence": item["sequence"],
                                   "measurements_omitted": True, "read_action": "telemetry_payload"}
                                  for item in result.get("samples", [])]
        if len(canonical(result).encode()) > budget:
            raise OrchestrationError("Telemetry analysis exceeds the selected budget; select a smaller page.", "invalid_query")
        return result

    def comparison(self, arguments, actor):
        from .telemetry_views import compare_runs

        shared = {key: arguments[key] for key in ("after", "cutoff", "limit", "max_bytes") if key in arguments}
        populations = []
        pages = []
        for side in ("baseline", "candidate"):
            selected = {key: arguments[side + "_" + key] for key in ("run_id", "work_id") if side + "_" + key in arguments}
            if not selected:
                raise OrchestrationError("Each comparison population needs an explicit work or run selector.", "invalid_arguments")
            page = self._analysis_page({**shared, **selected}, actor, "telemetry_compare")
            populations.append(page["events"])
            pages.append(page)
        declared = {key: arguments[key] for key in ("event_types", "unit", "passed_outcomes", "failed_outcomes") if key in arguments}
        result = compare_runs(*populations, **declared)
        result.update(next_after={"baseline": pages[0]["next_after"], "candidate": pages[1]["next_after"]},
                      cutoff={"baseline": pages[0]["cutoff"], "candidate": pages[1]["cutoff"]},
                      population_scope="authorized returned pages")
        bounded = self._bounded_analysis(result, arguments.get("max_bytes", 262144))
        self._revalidate_analysis_union(pages, actor, "telemetry_compare")
        return bounded

    def bundle_authorizer(self, actor, action, source):
        """Bind authorization to actual source bytes and current typed records."""
        sink = self._configured()
        rows, proofs = {}, {}

        def resolve(row):
            return sink.resolve_scope(row, database_path=source["database_path"])

        def authorize(row):
            if row.get("context", {}).get("project_id") != sink.project_id:
                raise OrchestrationError("The bundle belongs to another configured project.", "denied")
            envelope = sink.view_observation(row, hydrate=False)
            _, contexts = self.inspect(envelope, actor, action, resolve_scope=resolve,
                                       load_observation=lambda _: sink.view_observation(
                                           row, artifact_store=source["artifact_store"]))
            self._verify(contexts)
            original = sink.view_observation(row, artifact_store=source["artifact_store"])
            visible, nested = self.inspect(original, actor, action, resolve_scope=resolve)
            if visible.get("payload") != original.get("payload"):
                raise OrchestrationError("The complete bundle closure is not visible.", "denied")
            self._verify([*contexts, *nested])
            rows[row["event_id"]] = deepcopy(row)
            for proof in (*contexts, *nested):
                proofs.setdefault(hashlib.sha256(canonical(proof).encode()).digest(), proof)
            return True

        def finalize():
            def validate():
                # Retain the entire observed closure, rather than only the last
                # row's decision. A later policy change invalidates earlier rows.
                for row in list(rows.values()):
                    authorize(row)
                self._verify(list(proofs.values()))

            self._consistent_authorization(validate)

        authorize.finalize = finalize
        return authorize
