"""Arm-before-action observations at one explicitly named capture boundary.

Counters belong to this retained source window, never to wall time or a remote
occurrence clock. A ticket needs the complete post-arm prefix until its match;
eviction/restart cannot silently turn incomplete observation into readiness.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Lock


class ObservationError(AssertionError):
    """The supplied observations cannot establish the requested boundary."""


@dataclass(frozen=True)
class Observation:
    source: str
    incarnation: str
    operation: str
    boundary: str
    sequence: int
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ObservationSnapshot:
    source: str
    incarnation: str
    through: int
    observations: tuple[Observation, ...]
    first_sequence: int = 1


def _validate(snapshot, source, incarnation):
    if snapshot.source != source or snapshot.incarnation != incarnation:
        raise ObservationError("observation source/incarnation changed")
    first, through = snapshot.first_sequence, snapshot.through
    if type(first) is not int or type(through) is not int or first < 1 or through < first - 1:
        raise ObservationError("invalid observation cursor")
    sequences = [row.sequence for row in snapshot.observations]
    if any(type(value) is not int for value in sequences) or sequences != list(range(first, through + 1)):
        raise ObservationError("observation window has a gap or invalid cursor")
    return snapshot


class OperationObserver:
    def __init__(self, read):
        self.read = read

    def arm(self, source, incarnation, operation, boundary):
        snapshot = _validate(self.read(), source, incarnation)
        return ObservationTicket(self.read, source, incarnation, operation, boundary, snapshot.through)


class ObservationTicket:
    def __init__(self, read, source, incarnation, operation, boundary, cursor):
        self.read, self.source, self.incarnation = read, source, incarnation
        self.operation, self.boundary, self.cursor = operation, boundary, cursor
        self.matched = None

    def poll(self):
        if self.matched is not None:
            return self.matched
        snapshot = _validate(self.read(), self.source, self.incarnation)
        if snapshot.through < self.cursor:
            raise ObservationError("observation cursor regressed")
        if snapshot.first_sequence > self.cursor + 1:
            raise ObservationError("observation window lost an unread prefix")
        for row in snapshot.observations:
            if (row.sequence > self.cursor and row.source == self.source and row.incarnation == self.incarnation
                    and row.operation == self.operation and row.boundary == self.boundary):
                self.matched = deepcopy(row)
                return self.matched
        self.cursor = snapshot.through
        return None

    def wait(self, timeout=5, **options):
        # One shared classified waiting implementation; importing here avoids
        # coupling callback providers' construction to their waiting adapter.
        from tests.hexagonal.support.native import wait_for

        options.setdefault("message", f"{self.source}/{self.incarnation}: {self.operation} {self.boundary}")
        return wait_for(self.poll, timeout, **options)


class OperationLog:
    """A scoped, thread-safe fixture journal; optional eviction stays visible."""

    def __init__(self, source, incarnation, limit=None):
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("observation retention limit must be a positive integer")
        self.source, self.incarnation, self.limit = source, incarnation, limit
        self._lock, self._through, self._rows = Lock(), 0, []

    def record(self, operation, boundary, payload=None):
        with self._lock:
            self._through += 1
            row = Observation(self.source, self.incarnation, operation, boundary, self._through,
                              deepcopy(payload or {}))
            self._rows.append(row)
            if self.limit is not None:
                self._rows = self._rows[-self.limit:]
            return deepcopy(row)

    def snapshot(self):
        with self._lock:
            first = self._rows[0].sequence if self._rows else self._through + 1
            return ObservationSnapshot(self.source, self.incarnation, self._through,
                                       tuple(deepcopy(self._rows)), first)
