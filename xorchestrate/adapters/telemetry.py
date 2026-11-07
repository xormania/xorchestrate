"""Bridge Xorchestrate observations to the shared agent-telemetry collector.

Each call owns its donor repository connection. Native callbacks and concurrent
MCP workers never share a thread-bound SQLite connection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..model import OrchestrationError
from ..telemetry import canonical, protect

_OPTIONS = {"profile", "project_id", "dataset_id", "collector_id", "producer_id", "runtime_root",
            "database", "artifact_root", "export_root", "capture_lock_timeout_ms", "known_secrets",
            "artifact_threshold_bytes", "sources"}


def _configuration(message):
    return OrchestrationError(message, "telemetry_configuration")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise _configuration(f"Telemetry {name} must be nonempty text.")
    return value


def _contained(root, value, name):
    relative = Path(_text(value, name))
    if relative.is_absolute() or ".." in relative.parts:
        raise _configuration(f"Telemetry {name} must be relative to runtime_root.")
    path = root / relative
    for component in (path, *path.parents):
        if component.is_symlink():
            raise _configuration("Telemetry paths cannot traverse symbolic links.")
    if path.resolve() == root or not path.resolve().is_relative_to(root):
        raise _configuration("Telemetry path must be inside runtime_root.")
    return path


class UnavailableTelemetry:
    """A safe, visible failed optional construction without keeping source errors."""

    def __init__(self, reason="configuration_unavailable"):
        self.reason = reason

    def new_id(self):
        return uuid4().hex

    def record(self, *args, **kwargs):
        return {"status": "unavailable", "receipt": None, "reason": self.reason}

    def status(self):
        return {"status": "unavailable", "reason": self.reason, "coverage": "unknown", "sources": {},
                "publication": "unknown"}

    def query(self, **kwargs):
        return []

    def get_event(self, event_id, *, hydrate=True):
        return None

    def read_payload(self, *args, **kwargs):
        raise OrchestrationError("Telemetry capture is unavailable.", "telemetry_unavailable")

    def close(self):
        pass


class LocalTelemetry:
    """A configurable adapter; the shared package owns collection and durability."""

    def __init__(self, options):
        if not isinstance(options, dict) or options.keys() - _OPTIONS:
            raise _configuration("Unknown telemetry adapter option.")
        from agent_telemetry.application import Collector
        from agent_telemetry.artifacts import FileArtifactStore
        from agent_telemetry.domain import ArtifactRef, CaptureContext, Event
        from agent_telemetry.sqlite import SQLiteRepository

        self._collector_type, self._repository_type = Collector, SQLiteRepository
        self._context_type, self._event_type, self._artifact_type = CaptureContext, Event, ArtifactRef
        secrets = options.get("known_secrets", [])
        if not isinstance(secrets, list) or any(not isinstance(value, str) or not value for value in secrets):
            raise _configuration("Telemetry known_secrets must be explicit nonempty strings.")
        self._secrets = tuple(secrets)
        if "profile" in options:
            if options.keys() - {"profile", "known_secrets", "artifact_threshold_bytes", "sources", "export_root"}:
                raise _configuration("Telemetry profile and inline storage settings cannot be combined.")
            from agent_telemetry.config import load_config
            profile = load_config(options["profile"])
            self.root, self.path, self.artifact_root = profile.runtime_root, profile.database, profile.artifact_root
            self.project_id, self.dataset_id = profile.project_id, profile.dataset_id
            self.collector_id, self.producer_id = profile.collector_id, profile.producer_id
            self.timeout_ms, self.configuration_identity = profile.timeout_ms, profile.digest
            artifacts_enabled = profile.artifacts_enabled
        else:
            self.root = Path(_text(options.get("runtime_root"), "runtime_root")).absolute()
            for component in (self.root, *self.root.parents):
                if component.is_symlink():
                    raise _configuration("Telemetry runtime_root cannot traverse symbolic links.")
            self.root = self.root.resolve()
            self.path = _contained(self.root, options.get("database", "events.sqlite3"), "database")
            self.artifact_root = _contained(self.root, options.get("artifact_root", "artifacts"), "artifact_root")
            self.project_id = _text(options.get("project_id"), "project_id")
            self.dataset_id = _text(options.get("dataset_id"), "dataset_id")
            self.collector_id = _text(options.get("collector_id"), "collector_id")
            self.producer_id = _text(options.get("producer_id", "xorchestrate"), "producer_id")
            self.timeout_ms = options.get("capture_lock_timeout_ms", 250)
            if type(self.timeout_ms) is not int or not 0 <= self.timeout_ms <= 60000:
                raise _configuration("Telemetry lock wait must be between 0 and 60000 milliseconds.")
            material = {key: value for key, value in options.items() if key != "known_secrets"}
            self.configuration_identity = hashlib.sha256(canonical(material).encode()).hexdigest()
            artifacts_enabled = True
        if self.path == self.artifact_root or self.path.is_relative_to(self.artifact_root) or self.artifact_root.is_relative_to(self.path):
            raise _configuration("Telemetry database and artifacts cannot overlap.")
        self.export_root = _contained(self.root, options.get("export_root", "exports"), "export_root")
        self.artifact_threshold = options.get("artifact_threshold_bytes", 65536)
        if type(self.artifact_threshold) is not int or self.artifact_threshold < 1:
            raise _configuration("Telemetry artifact threshold must be a positive byte count.")
        sources = options.get("sources", ["application", "native", "capability", "evaluator", "process"])
        if not isinstance(sources, list) or any(not isinstance(name, str) or not name for name in sources):
            raise _configuration("Telemetry sources must be source names.")
        self._source_filter = set(sources) if "sources" in options else None
        self._sources = {name: {"configured": True, "observed": False, "coverage": "unknown"} for name in sources}
        self._lock = threading.Lock()
        self._failures = 0
        self._closed = False
        self.artifacts = FileArtifactStore(self.artifact_root) if artifacts_enabled else None
        repository = SQLiteRepository.initialize(self.path, dataset_id=self.dataset_id,
                                                collector_id=self.collector_id, timeout_ms=self.timeout_ms)
        repository.close()

    def new_id(self):
        return uuid4().hex

    def _health(self, observation):
        with self._lock:
            self._failures += 1

    def record(self, event_type, payload, context, *, event_id=None, basis="observed", completeness="complete",
               reason=None, cursor=None, links=()):
        repository = None
        try:
            if self._closed:
                return {"status": "unavailable", "receipt": None, "reason": "collector_closed"}
            protected, scope = protect(payload, self._secrets), protect(context, self._secrets)
            if not isinstance(scope, dict):
                raise ValueError("observation context must be an object")
            source = event_type.partition(".")[0]
            if self._source_filter is not None and source not in self._source_filter:
                return {"status": "disabled", "receipt": None, "reason": "source_disabled"}
            with self._lock:
                self._sources.setdefault(source, {"configured": False, "observed": False, "coverage": "unknown"})
            flat = {key: deepcopy(scope[key]) for key in ("actor", "action", "work_id", "session_id", "operation_id",
                    "attempt_id", "parent_attempt_id", "native_id", "incarnation", "native_call_id", "turn_id",
                    "driver", "harness", "source", "source_version", "configuration_id", "run_id", "candidate_id") if key in scope}
            for key, kind in (("work", "work_id"), ("session", "session_id"), ("operation", "operation_id")):
                if not flat.get(kind) and isinstance(scope.get(key), dict) and scope[key].get("id"):
                    flat[kind] = scope[key]["id"]
            backend = (scope.get("session") or {}).get("backend", {}) if isinstance(scope.get("session"), dict) else {}
            for key in ("native_id", "incarnation"):
                if not flat.get(key) and backend.get(key):
                    flat[key] = backend[key]
            text = canonical(protected)
            artifacts = ()
            payload_storage = None
            if self.artifacts is not None and len(text.encode()) > self.artifact_threshold:
                ref = self.artifacts.put(text.encode(), "application/json")
                artifacts = (ref,)
                payload_storage = asdict(ref)
                text = canonical({"_telemetry_payload": payload_storage})
            observation = self._event_type(
                event_id or self.new_id(), event_type, text,
                self._context_type(self.project_id, self.dataset_id, self.collector_id, self.producer_id,
                                   session_id=flat.get("session_id"), session_generation=flat.get("incarnation"),
                                   harness=flat.get("harness", flat.get("driver")), task_id=flat.get("work_id"),
                                   config_digest=self.configuration_identity, attribution="explicit"),
                basis=basis, completeness=completeness, reason=reason,
                source_json=canonical({"xorchestrate": flat, "payload_storage": payload_storage, "protection": {"policy": "xorchestrate-credentials-v1",
                                       "transformed": protected != payload or scope != context}}),
                artifacts=artifacts, links=tuple(links), redaction_policy="xorchestrate-credentials-v1",
            )
            repository = self._repository_type(self.path, timeout_ms=self.timeout_ms)
            result = self._collector_type(repository, self.artifacts, health=self._health).record(observation, cursor=cursor)
            response = asdict(result)
            if result.status in {"recorded", "duplicate"}:
                with self._lock:
                    self._sources[source].update(observed=True, coverage="observed", last_event_id=observation.event_id)
            return response
        except Exception as error:
            self._health({})
            return {"status": "unavailable", "receipt": None,
                    "reason": getattr(error, "code", "capture_error") if isinstance(getattr(error, "code", None), str) else "capture_error"}
        finally:
            if repository is not None:
                try:
                    repository.close()
                except Exception:
                    self._health({})

    def _view(self, row, *, hydrate=True):
        row = deepcopy(row)
        payload = json.loads(row["payload_json"])
        source = json.loads(row.get("source_json", "{}"))
        storage = source.get("payload_storage")
        if storage is not None:
            # Only collector-owned source metadata plus the event's exact declared
            # artifact reference can authorize indirection. An opaque caller key
            # named _telemetry_payload has no storage meaning.
            if storage not in row.get("artifacts", []):
                raise OrchestrationError("Telemetry payload reference is not bound to its event.", "artifact_integrity")
            if not isinstance(payload, dict) or payload.get("_telemetry_payload") != storage:
                raise OrchestrationError("Telemetry payload storage metadata differs.", "artifact_integrity")
            if hydrate:
                if self.artifacts is None:
                    raise OrchestrationError("Telemetry artifact is unavailable.", "artifact_integrity")
                payload = json.loads(self.artifacts.read(self._artifact_type(**storage)))
            else:
                payload = {"read_action": "telemetry_payload", "event_id": row["event_id"], **storage}
                row["payload_storage"] = storage
                row["payload_available"] = True
        row["payload"] = payload
        row["context"] = {**row["context"], **source.get("xorchestrate", {})}
        row["protection"] = source.get("protection", {})
        row.pop("payload_json", None)
        row.pop("source_json", None)
        row.pop("context_json", None)
        return row

    def view_observation(self, row):
        return self._view(row)

    def query(self, *, after=0, limit=100, event_type=None, work_id=None, session_id=None, operation_id=None):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise OrchestrationError("Invalid telemetry query bounds.", "invalid_query")
        repository = self._repository_type.inspect(self.path)
        result, cursor = [], after
        try:
            while len(result) < limit:
                rows = repository.query(project_id=self.project_id, event_type=event_type, after=cursor, limit=1000)
                if not rows:
                    break
                for row in rows:
                    cursor = row["sequence"]
                    item = self._view(row, hydrate=False)
                    if all(value is None or item["context"].get(key) == value for key, value in
                           (("work_id", work_id), ("session_id", session_id), ("operation_id", operation_id))):
                        result.append(item)
                        if len(result) == limit:
                            break
                if len(rows) < 1000:
                    break
            return result
        finally:
            repository.close()

    def get_event(self, event_id, *, hydrate=True):
        repository = self._repository_type.inspect(self.path)
        try:
            found = repository.db.execute("SELECT sequence FROM events WHERE event_id=? AND project_id=?",
                                          (event_id, self.project_id)).fetchone()
            if found is None:
                return None
            return self._view(repository.query(project_id=self.project_id, after=found[0] - 1, limit=1)[0], hydrate=hydrate)
        finally:
            repository.close()

    def resolve_scope(self, row):
        """Resolve a pre-creation request through its retained completed attempt.

        This join adds authority context only; it never invents a committed
        operation for an interrupted request or rewrites its immutable envelope.
        """
        context = row.get("context", {})
        attempt = context.get("attempt_id")
        if not attempt or row.get("event_type") != "application.requested":
            return None
        repository = self._repository_type.inspect(self.path)
        try:
            candidates = repository.db.execute(
                "SELECT observation FROM events WHERE project_id=? AND event_type=? "
                "AND json_extract(json_extract(observation,'$.source_json'),'$.xorchestrate.attempt_id')=? "
                "ORDER BY sequence", (self.project_id, "application.completed", attempt))
            for candidate in candidates:
                source = json.loads(json.loads(candidate[0])["source_json"]).get("xorchestrate", {})
                if source.get("actor") != context.get("actor"):
                    continue
                resolved = {key: source[key] for key in ("work_id", "session_id", "operation_id") if source.get(key)}
                if resolved:
                    return resolved
            return None
        finally:
            repository.close()

    def status(self):
        repository = self._repository_type.inspect(self.path)
        try:
            result = repository.status()
        finally:
            repository.close()
        with self._lock:
            result.update(status="closed" if self._closed else "ready", sources=deepcopy(self._sources),
                          capture_failures=self._failures, configuration_identity=self.configuration_identity)
        return result

    def read_payload(self, event_id, digest=None, offset=0, limit=65536):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1048576:
            raise OrchestrationError("Invalid telemetry payload range.", "invalid_query")
        row = self.get_event(event_id)
        if row is None:
            raise OrchestrationError("Telemetry event is unavailable.", "not_found")
        media_type = "application/json"
        if digest is None:
            data = canonical(row["payload"]).encode()
            digest = hashlib.sha256(data).hexdigest()
        else:
            ref = next((item for item in row.get("artifacts", []) if item["digest"] == digest), None)
            if ref is None or self.artifacts is None:
                raise OrchestrationError("The event does not reference this artifact.", "not_found")
            data = self.artifacts.read(self._artifact_type(**ref))
            media_type = ref["media_type"]
        # Range length is an upper bound. Keep text plus binary representation
        # inside the default MCP response frame; continuation retains all bytes.
        chunk = data[offset:offset + min(limit, 65536)]
        try:
            content = chunk.decode("utf-8")
        except UnicodeDecodeError as error:
            if error.start and error.reason == "unexpected end of data":
                chunk = chunk[:error.start]
                content = chunk.decode("utf-8")
            else:
                content = None
        return {"event_id": event_id, "digest": digest, "size_bytes": len(data), "offset": offset,
                "next_offset": offset + len(chunk) if offset + len(chunk) < len(data) else None,
                "content": content, "data_base64": base64.b64encode(chunk).decode(), "encoding": "utf-8" if content is not None else "base64",
                "media_type": media_type}

    def snapshot_source(self):
        return {"database_path": self.path, "artifact_store": self.artifacts, "artifact_root": self.artifact_root}

    def destination(self, value):
        path = Path(_text(value, "destination"))
        candidate = path if path.is_absolute() else self.export_root / path
        for component in (candidate, *candidate.parents):
            if component.is_symlink():
                raise _configuration("Telemetry destinations cannot traverse symbolic links.")
        candidate = candidate.resolve()
        if candidate == self.export_root or not candidate.is_relative_to(self.export_root):
            raise _configuration("Telemetry destination must be inside the configured export root.")
        return candidate

    def close(self):
        self._closed = True
