"""Storage and operational sink ports used by the local telemetry adapter.

Composition selects the implementations by name (``telemetry_corpus``,
``telemetry_artifacts`` and ``telemetry_operational_sink``); the collector never
names a concrete store. A factory receives its validated options object and the
collector's ``CollectorScope``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .domain import ArtifactRef, CaptureResult, Event


@dataclass(frozen=True)
class CollectorScope:
    """Identity and root shared by the corpus and artifact adapters of one collector."""

    runtime_root: Path
    dataset_id: str
    collector_id: str


class TelemetryCorpus(Protocol):
    """One connection to an append-only observation corpus.

    Reads never initialize absent storage. Query rows carry the immutable
    observation plus ``sequence``, ``digest``, ``captured_at``, ``context_json``
    and ``unresolved_links``.
    """

    def append(self, event: Event, captured_at: str, cursor=None) -> CaptureResult: ...
    def query(self, *, project_id=None, event_type=None, after=0, limit=100) -> list[dict]: ...
    def get(self, event_id: str, *, project_id: str) -> dict | None: ...
    def find_by_source(self, *, project_id: str, event_type: str, path: tuple[str, ...],
                       value: str) -> list[dict]:
        """Observations whose source metadata has ``value`` at ``path``, in sequence order."""
        ...
    def status(self) -> dict: ...
    def close(self) -> None: ...


class TelemetryCorpusStorage(Protocol):
    """A selected corpus adapter; every connection is owned by one caller.

    ``locations`` lists the filesystem paths the adapter owns (empty when it
    owns none), so the collector can refuse overlapping storage; an adapter
    that omits it is refused. ``database_path`` is the SQLite database that
    snapshot bundles copy, or None when the adapter cannot be bundled.
    ``inspect(location)`` opens a read-only connection, optionally to a
    preserved copy in this adapter's format.
    """

    locations: tuple[Path, ...]
    database_path: Path | None

    def initialize(self) -> None: ...
    def open(self) -> TelemetryCorpus: ...
    def inspect(self, location=None) -> TelemetryCorpus: ...


class TelemetryArtifacts(Protocol):
    """Content-addressed artifact storage installed before observations refer to it.

    ``locations`` lists the filesystem paths the adapter owns (empty when none).
    """

    locations: tuple[Path, ...]

    def put(self, data: bytes, media_type="application/octet-stream") -> ArtifactRef: ...
    def read(self, ref: ArtifactRef) -> bytes: ...
    def verify(self, ref: ArtifactRef) -> bool: ...


class OperationalSink(Protocol):
    """Optional protected operational signals, independent of corpus durability.

    The collector's bounded dispatch owns delivery to ``emit``. Factories and
    sinks bound their own construction and emission work; this interface does
    not supply a background-resource or shutdown lifecycle.
    """

    def emit(self, signal: dict) -> None: ...


CorpusFactory = Callable[[dict, CollectorScope], TelemetryCorpusStorage]
ArtifactsFactory = Callable[[dict, CollectorScope], TelemetryArtifacts]
OperationalSinkFactory = Callable[[dict, CollectorScope], OperationalSink]
