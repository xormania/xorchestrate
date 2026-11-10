"""Bridge Xorchestrate observations to the in-tree telemetry collector.

Corpus and artifact storage are selected adapters. Each call owns its corpus
connection; native callbacks and concurrent MCP workers never share a
thread-bound connection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..model import OrchestrationError
from ..telemetry import canonical, protect
from .telemetry_capture import TelemetryDispatch
from .telemetry_collector import config
from .telemetry_collector.application import Collector
from .telemetry_collector.artifacts import FileArtifactStore
from .telemetry_collector.domain import ArtifactRef, CaptureContext, Event, TelemetryError
from .telemetry_collector.ports import CollectorScope

_OPTIONS = {"project_id", "dataset_id", "collector_id", "producer_id", "runtime_root", "corpus", "artifacts",
            "database", "artifact_root", "export_root", "capture_lock_timeout_ms", "known_secrets",
            "artifact_threshold_bytes", "sources", "operational", "snapshot_timeout"}
# Shorthand options for the default storage adapters, kept for existing configurations.
_CORPUS_SHORTHAND = {"database": "database", "capture_lock_timeout_ms": "capture_lock_timeout_ms"}
_ARTIFACTS_SHORTHAND = {"artifact_root": "root"}


_DETAIL_LIMIT = 200


def _bounded(value):
    return None if value is None else str(value)[:_DETAIL_LIMIT]


def _configuration(message, cause="invalid_option", detail=None):
    """A configuration refusal with a bounded cause that status can report.

    ``detail`` holds option or adapter names or a fixed validation message,
    never option values, so it cannot carry configured secrets.
    """
    return OrchestrationError(message, "telemetry_configuration",
                              {"cause": cause, "detail": _bounded(message if detail is None else detail)})


def construction_cause(error):
    """Reduce a telemetry construction failure to a bounded, non-sensitive cause.

    Configuration refusals keep their cause and detail; collector errors keep
    their code (for example ``sqlite_version``); anything else reports only
    the exception type.
    """
    current = error
    while current is not None:
        if isinstance(current, OrchestrationError) and current.code == "telemetry_configuration":
            return {"code": current.details.get("cause", "invalid_option"),
                    "detail": _bounded(current.details.get("detail"))}
        if isinstance(current, TelemetryError):
            return {"code": current.code, "detail": None}
        current = current.__cause__
    root = error
    while root.__cause__ is not None:
        root = root.__cause__
    if root is error and isinstance(error, OrchestrationError):
        return {"code": error.code, "detail": _bounded(str(error))}
    return {"code": "construction_failed", "detail": type(root).__name__}


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise _configuration(f"Telemetry {name} must be nonempty text.")
    return value


def _storage(options, name, shorthand, default):
    """Return one nested storage selection, accepting the default adapter's shorthand."""
    present = {key: options[key] for key in shorthand if key in options}
    if name in options:
        if present:
            raise _configuration(f"Telemetry {name} selection and its shorthand options cannot be combined.")
        return config.selection(options[name], name)
    return default, {target: present[key] for key, target in shorthand.items() if key in present}, True


class UnavailableTelemetry:
    """A safe, visible failed construction without keeping source errors.

    For example, a configuration error or a Python whose SQLite lacks the WAL fix.
    """

    project_id = None

    def __init__(self, reason="configuration_unavailable", cause=None):
        self.reason = reason
        self.cause = cause

    def new_id(self):
        return uuid4().hex

    def record(self, *args, **kwargs):
        return {"status": "unavailable", "receipt": None, "reason": self.reason}

    def status(self):
        result = {"status": "unavailable", "reason": self.reason, "coverage": "unknown", "sources": {},
                  "publication": "unknown"}
        if self.cause is not None:
            result["cause"] = dict(self.cause)
        return result

    def scan(self, *, after=0, limit=100, **filters):
        return {"events": [], "next_after": after, "scanned": 0, "exhausted": True}

    def get_event(self, event_id, *, hydrate=True):
        return None

    def resolve_scope(self, row, *, database_path=None):
        return None

    def read_payload(self, *args, **kwargs):
        raise OrchestrationError("Telemetry capture is unavailable.", "telemetry_unavailable")

    def snapshot(self, *args, **kwargs):
        raise OrchestrationError("Telemetry capture is unavailable.", "telemetry_unavailable")

    verify = restore = view_observation = snapshot
    snapshot_source = bundle_source = snapshot

    def close(self):
        pass


class LocalTelemetry:
    """A configurable adapter; the collector owns validation, protection and durability.

    ``resolve(component, adapter)`` is supplied by composition and returns the
    factory selected for ``corpus`` or ``artifacts``; this adapter never names a
    concrete store.
    """

    def __init__(self, options, *, resolve):
        if not isinstance(options, dict):
            raise _configuration("Telemetry options must be an object.")
        if options.keys() - _OPTIONS:
            raise _configuration("Unknown telemetry adapter option.", "unknown_option",
                                 ", ".join(sorted(str(key) for key in options.keys() - _OPTIONS)))
        try:
            self._configure(options, resolve)
        except TelemetryError as error:
            if error.code != "invalid_config":
                raise
            raise _configuration(str(error)) from error

    @staticmethod
    def _adapter(resolve, component, name, options, scope):
        """Construct one selected storage adapter, refusing unknown or unloadable names."""
        try:
            factory = resolve(component, name)
        except OrchestrationError as error:
            raise _configuration(f"Unknown telemetry {component} adapter.", "unknown_adapter",
                                 f"{component}: {name}") from error
        except (ImportError, AttributeError, ValueError) as error:
            raise _configuration(f"Telemetry {component} adapter cannot be loaded.", "adapter_unavailable",
                                 f"{component}: {name}") from error
        return factory(options, scope)

    def _configure(self, options, resolve):
        secrets = options.get("known_secrets", [])
        if not isinstance(secrets, list) or any(not isinstance(value, str) or not value for value in secrets):
            raise _configuration("Telemetry known_secrets must be explicit nonempty strings.")
        self._secrets = tuple(secrets)
        self.root = config.runtime_root(options.get("runtime_root"))
        self.project_id = config.text(options.get("project_id"), "project_id")
        self.dataset_id = config.text(options.get("dataset_id"), "dataset_id")
        self.collector_id = config.text(options.get("collector_id"), "collector_id")
        self.producer_id = config.text(options.get("producer_id", "xorchestrate"), "producer_id")
        material = {key: value for key, value in options.items() if key != "known_secrets"}
        self.configuration_identity = hashlib.sha256(canonical(material).encode()).hexdigest()
        scope = CollectorScope(self.root, self.dataset_id, self.collector_id)
        adapter, corpus_options, enabled = _storage(options, "corpus", _CORPUS_SHORTHAND, "sqlite")
        if not enabled:
            raise _configuration("Telemetry requires a corpus; disable telemetry itself instead.")
        self.corpus = self._adapter(resolve, "corpus", adapter, corpus_options, scope)
        adapter, artifact_options, enabled = _storage(options, "artifacts", _ARTIFACTS_SHORTHAND, "filesystem")
        # A disabled selection is still validated, so a typo cannot hide behind it.
        artifacts = self._adapter(resolve, "artifacts", adapter, artifact_options, scope)
        self.artifacts = artifacts if enabled else None
        owned = [*self._locations(self.corpus, "corpus"),
                 *(self._locations(self.artifacts, "artifacts") if enabled else ())]
        if any(config.overlapping(first, second) for index, first in enumerate(owned) for second in owned[index + 1:]):
            raise _configuration("Telemetry database and artifacts cannot overlap.")
        # Snapshot bundles are SQLite/file closures; a corpus without a database path does not offer them.
        if not hasattr(self.corpus, "database_path"):
            raise _configuration("Telemetry corpus adapter does not declare database_path.", "invalid_adapter",
                                 "corpus: database_path")
        self.path = self.corpus.database_path
        if self.path is not None and not isinstance(self.path, Path):
            raise _configuration("Telemetry corpus database_path must be a path or null.", "invalid_adapter",
                                 "corpus: database_path")
        self.artifact_root = getattr(self.artifacts, "root", None)
        self.export_root = config.contained(self.root, options.get("export_root", "exports"), "export_root")
        self.snapshot_timeout = options.get("snapshot_timeout", 5.0)
        if (isinstance(self.snapshot_timeout, bool) or not isinstance(self.snapshot_timeout, (int, float))
                or not 0 < self.snapshot_timeout <= 3600):
            raise _configuration("Telemetry snapshot deadline must be between 0 and 3600 seconds.")
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
        self._operational = self._operational_dispatch = None
        self._operational_issue = None
        self.corpus.initialize()
        if options.get("operational") is not None:
            try:
                self._configure_operational(options["operational"])
            except Exception:
                # Optional transport configuration has a separate health state;
                # it cannot disable the independently configured local corpus.
                self._operational = self._operational_dispatch = None
                self._operational_issue = "configuration_unavailable"

    @staticmethod
    def _locations(adapter, component):
        locations = getattr(adapter, "locations", None)
        if not isinstance(locations, tuple) or not all(isinstance(item, Path) for item in locations):
            raise _configuration(f"Telemetry {component} adapter must declare its locations.", "invalid_adapter",
                                 f"{component}: locations")
        return locations

    def new_id(self):
        return uuid4().hex

    def _health(self, observation):
        with self._lock:
            self._failures += 1

    def _configure_operational(self, options):
        if (not isinstance(options, dict) or options.keys() - {"adapter", "endpoint", "timeout", "capacity",
                "trace_sample_rate", "max_pending", "max_bytes", "drain_timeout"}):
            raise _configuration("Invalid operational telemetry settings.")
        from .telemetry_export import LocalOperationalSink, ObservationSignals, OperationalExporter, OtlpOperationalSink

        rate = options.get("trace_sample_rate", 1.0)
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            raise _configuration("Invalid operational trace sampling rate.")
        adapter = options.get("adapter", "local")
        if adapter == "local":
            sink = LocalOperationalSink(capacity=options.get("capacity", 1000))
        elif adapter == "otlp":
            sink = OtlpOperationalSink(options.get("endpoint", ""), timeout=options.get("timeout", 1.0))
        else:
            raise _configuration("Unknown operational telemetry adapter.")

        def sampled(signal):
            if signal.get("kind") != "span":
                return True
            identity = signal.get("event_id") or ""
            return int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16) / 2**32 < rate

        self._operational = ObservationSignals(OperationalExporter(sink, sample=sampled))
        def observe(item):
            result = self._operational.observe(**item)
            return {"status": "recorded" if result.get("status") == "observed" else "unavailable",
                    "receipt": None, "boundary": "operational_signals"}

        self._operational_dispatch = TelemetryDispatch(observe, {
            "max_telemetry_pending": options.get("max_pending", 64),
            "max_telemetry_bytes": options.get("max_bytes", 8 * 1024 * 1024),
            "telemetry_drain_timeout": options.get("drain_timeout", 0.05),
        })

    def _operational_observation(self, event_type, context, response, duration, observed_monotonic):
        if self._operational_dispatch is None:
            return
        try:
            # Queue only operational links; source payloads never enter this
            # asynchronous transport or its pending-memory budget.
            scope = protect(context, self._secrets)
            scope = {key: scope[key] for key in ("attempt_id", "parent_attempt_id", "native_call_id", "operation_id",
                     "session_id", "run_id") if isinstance(scope.get(key), str)}
            self._operational_dispatch({"event_type": event_type, "context": scope,
                "receipt": response.get("receipt"), "capture_status": response.get("status", "unknown"),
                "capture_duration_ms": duration, "observed_monotonic": observed_monotonic})
        except Exception:
            # Operational queue failure cannot alter the collector's receipt.
            pass

    def record(self, event_type, payload, context, *, event_id=None, basis="observed", completeness="complete",
               reason=None, cursor=None, links=()):
        repository = None
        started = time.monotonic()
        response = {"status": "unavailable", "receipt": None}
        try:
            if self._closed:
                response = {"status": "unavailable", "receipt": None, "reason": "collector_closed"}
                return response
            protected, scope = protect(payload, self._secrets), protect(context, self._secrets)
            if not isinstance(scope, dict):
                raise ValueError("observation context must be an object")
            source = event_type.partition(".")[0]
            if self._source_filter is not None and source not in self._source_filter:
                response = {"status": "disabled", "receipt": None, "reason": "source_disabled"}
                return response
            with self._lock:
                self._sources.setdefault(source, {"configured": False, "observed": False, "coverage": "unknown"})
            flat = {key: deepcopy(scope[key]) for key in ("actor", "action", "work_id", "session_id", "operation_id",
                    "attempt_id", "parent_attempt_id", "native_id", "incarnation", "native_call_id", "turn_id",
                    "driver", "harness", "source", "source_version", "configuration_id", "run_id", "candidate_id",
                    "worktree") if key in scope}
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
            observation = Event(
                event_id or self.new_id(), event_type, text,
                CaptureContext(self.project_id, self.dataset_id, self.collector_id, self.producer_id,
                                   session_id=flat.get("session_id"), session_generation=flat.get("incarnation"),
                                   harness=flat.get("harness", flat.get("driver")), task_id=flat.get("work_id"),
                                   config_digest=self.configuration_identity, attribution="explicit"),
                basis=basis, completeness=completeness, reason=reason,
                source_json=canonical({"xorchestrate": flat, "payload_storage": payload_storage, "protection": {"policy": "xorchestrate-credentials-v1",
                                       "transformed": protected != payload or scope != context}}),
                artifacts=artifacts, links=tuple(links), redaction_policy="xorchestrate-credentials-v1",
            )
            repository = self.corpus.open()
            result = Collector(repository, self.artifacts, health=self._health).record(observation, cursor=cursor)
            response = asdict(result)
            if result.status in {"recorded", "duplicate"}:
                with self._lock:
                    self._sources[source].update(observed=True, coverage="observed", last_event_id=observation.event_id)
            return response
        except Exception as error:
            self._health({})
            response = {"status": "unavailable", "receipt": None,
                    "reason": getattr(error, "code", "capture_error") if isinstance(getattr(error, "code", None), str) else "capture_error"}
            return response
        finally:
            if repository is not None:
                try:
                    repository.close()
                except Exception:
                    self._health({})
            self._operational_observation(event_type, context, response, (time.monotonic() - started) * 1000, started)

    def _view(self, row, *, hydrate=True, artifact_store=None):
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
                store = artifact_store if artifact_store is not None else self.artifacts
                if store is None:
                    raise OrchestrationError("Telemetry artifact is unavailable.", "artifact_integrity")
                payload = json.loads(store.read(ArtifactRef(**storage)))
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

    def view_observation(self, row, *, hydrate=True, artifact_store=None):
        return self._view(row, hydrate=hydrate, artifact_store=artifact_store)

    def scan(self, *, after=0, limit=100, event_type=None, work_id=None, session_id=None, operation_id=None,
             run_id=None, attempt_id=None):
        """Filter one bounded repository batch, retaining its raw continuation.

        Resource matches can be sparse. An empty matching batch is distinct
        from exhausting the repository, and must not hide an unbounded scan
        inside the application's read budget.
        """
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise OrchestrationError("Invalid telemetry query bounds.", "invalid_query")
        repository = self.corpus.inspect()
        try:
            rows = repository.query(project_id=self.project_id, event_type=event_type, after=after, limit=limit)
            result = []
            for row in rows:
                item = self._view(row, hydrate=False)
                if all(value is None or item["context"].get(key) == value for key, value in
                       (("work_id", work_id), ("session_id", session_id), ("operation_id", operation_id),
                        ("run_id", run_id), ("attempt_id", attempt_id))):
                    result.append(item)
            return {"events": result, "next_after": rows[-1]["sequence"] if rows else after,
                    "scanned": len(rows), "exhausted": len(rows) < limit}
        finally:
            repository.close()

    def get_event(self, event_id, *, hydrate=True):
        repository = self.corpus.inspect()
        try:
            found = repository.get(event_id, project_id=self.project_id)
            return None if found is None else self._view(found, hydrate=hydrate)
        finally:
            repository.close()

    def resolve_scope(self, row, *, database_path=None):
        """Resolve a pre-creation request through its retained completed attempt.

        This join adds authority context only; it never invents a committed
        operation for an interrupted request or rewrites its immutable envelope.
        """
        context = row.get("context", {})
        attempt = context.get("attempt_id")
        if not attempt or row.get("event_type") != "application.requested":
            return None
        repository = self.corpus.inspect(database_path)
        try:
            candidates = repository.find_by_source(project_id=self.project_id, event_type="application.completed",
                                                   path=("xorchestrate", "attempt_id"), value=attempt)
            for candidate in candidates:
                source = json.loads(candidate["source_json"]).get("xorchestrate", {})
                if source.get("actor") != context.get("actor"):
                    continue
                resolved = {key: source[key] for key in ("work_id", "session_id", "operation_id") if source.get(key)}
                if resolved:
                    return resolved
            return None
        finally:
            repository.close()

    def status(self):
        repository = self.corpus.inspect()
        try:
            result = repository.status()
        finally:
            repository.close()
        with self._lock:
            result.update(status="closed" if self._closed else "ready", sources=deepcopy(self._sources),
                          capture_failures=self._failures, configuration_identity=self.configuration_identity)
        if self._operational is not None:
            result["operational"] = {**self._operational.status(), "status": "ready",
                                     "dispatch": self._operational_dispatch.health()}
        elif self._operational_issue is not None:
            result["operational"] = {"status": "unavailable", "reason": self._operational_issue,
                                     "corpus_coverage": "independent"}
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
            data = self.artifacts.read(ArtifactRef(**ref))
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
        self._bundles()
        return {"database_path": self.path, "artifact_store": self.artifacts, "artifact_root": self.artifact_root}

    def _bundles(self):
        if self.path is None:
            raise OrchestrationError("The selected telemetry corpus adapter does not support snapshot bundles.",
                                     "telemetry_unsupported")

    def destination(self, value):
        self._bundles()
        path = Path(_text(value, "destination"))
        candidate = path if path.is_absolute() else self.export_root / path
        for component in (candidate, *candidate.parents):
            if component.is_symlink():
                raise _configuration("Telemetry destinations cannot traverse symbolic links.")
        candidate = candidate.resolve()
        if candidate == self.export_root or not candidate.is_relative_to(self.export_root):
            raise _configuration("Telemetry destination must be inside the configured export root.")
        return candidate

    def snapshot(self, destination, *, authorize=None, receipt=False):
        from .telemetry_snapshot import SnapshotError, bundle_receipt, create_snapshot, invalidate_bundle
        try:
            path = self.destination(destination)
            manifest = create_snapshot(self.path, self.artifacts, path,
                                       authorize=authorize, backup_timeout=self.snapshot_timeout)
            if not receipt:
                return manifest
            try:
                return bundle_receipt(path, manifest, authorize=authorize)
            except BaseException:
                invalidate_bundle(path)
                raise
        except PermissionError as error:
            raise OrchestrationError("The complete bundle closure is not authorized.", "denied") from error
        except (SnapshotError, OSError) as error:
            raise OrchestrationError("Telemetry snapshot could not be completed and verified.", "telemetry_integrity") from error

    def verify(self, destination, *, authorize=None, receipt=False):
        from .telemetry_snapshot import SnapshotError, bundle_receipt, verify_bundle
        try:
            path = self.destination(destination)
            manifest = verify_bundle(path, authorize=authorize)
            return bundle_receipt(path, manifest, authorize=authorize) if receipt else manifest
        except PermissionError as error:
            raise OrchestrationError("The complete bundle closure is not authorized.", "denied") from error
        except (SnapshotError, OSError) as error:
            raise OrchestrationError("Telemetry bundle verification failed.", "telemetry_integrity") from error

    def restore(self, bundle, destination, *, authorize=None, receipt=False):
        from .telemetry_snapshot import SnapshotError, bundle_receipt, invalidate_bundle, restore_bundle
        try:
            path = self.destination(destination)
            restored = restore_bundle(self.destination(bundle), path, authorize=authorize)
            if not receipt:
                return restored
            try:
                compact = bundle_receipt(path, restored["manifest"], authorize=authorize)
                return {**compact, "database_path": restored["database_path"], "artifact_root": restored["artifact_root"]}
            except BaseException:
                invalidate_bundle(path)
                raise
        except PermissionError as error:
            raise OrchestrationError("The complete bundle closure is not authorized.", "denied") from error
        except (SnapshotError, OSError) as error:
            raise OrchestrationError("Telemetry bundle restore could not be completed and verified.", "telemetry_integrity") from error

    def bundle_source(self, destination):
        path = self.destination(destination)
        return {"database_path": path / "telemetry.sqlite3", "artifact_store": FileArtifactStore(path / "artifacts")}

    def close(self):
        self._closed = True
        if self._operational_dispatch is not None:
            self._operational_dispatch.close()
