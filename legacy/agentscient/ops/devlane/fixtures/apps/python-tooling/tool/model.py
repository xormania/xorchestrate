"""Data models used by the fixture service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Interval:
    """A half-open interval whose end is excluded."""

    start: int
    end: int

    def contains(self, value: int) -> bool:
        return self.start <= value <= self.end

    def values(self) -> range:
        """Return the clean half-open range neighbour."""
        return range(self.start, self.end)
