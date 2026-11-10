"""Data version of durable state and the explicit registry of migration steps.

``DATA_VERSION`` is the record shape this release reads and writes. A step keyed
by source version ``N`` transforms a store from ``N`` to ``N + 1`` inside one
store transaction, using only the transaction's ``list``, ``get``, ``put``,
``delete`` and ``emit`` operations so record versions advance normally. It
returns the number of records it changed per record kind. The store adapter runs
the chain, stamps the new version and retains a ``data_migrated`` event in the
same transaction; a step never stamps versions itself.

Version 1 is the schema written before data versioning existed, so the registry
is empty. Stores are migrated only when explicitly requested; normal opening
never migrates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .. import __version__
from ..model import OrchestrationError

DATA_VERSION = 1
RELEASE = f"v{__version__}"

MigrationStep = Callable[..., Mapping[str, int]]


@dataclass(frozen=True)
class MigrationRegistry:
    """Ordered ``N -> N + 1`` steps ending at ``current``."""

    current: int
    steps: Mapping[int, MigrationStep] = field(default_factory=dict)

    def __post_init__(self):
        if type(self.current) is not int or self.current < 1:
            raise ValueError("The current data version must be a positive integer.")
        steps = dict(self.steps)
        for source, step in steps.items():
            if type(source) is not int or not 1 <= source < self.current or not callable(step):
                raise ValueError("Migration steps are keyed by a source version below the current version.")
        if steps and set(steps) != set(range(min(steps), self.current)):
            raise ValueError("Migration steps must form a contiguous chain to the current version.")
        object.__setattr__(self, "steps", MappingProxyType(steps))

    def oldest(self) -> int:
        """The oldest stored version this registry can migrate from."""
        return min(self.steps, default=self.current)

    def chain(self, stored: int) -> list[tuple[int, MigrationStep]]:
        """Steps from ``stored`` to ``current`` in order; empty when current."""
        if stored >= self.current:
            return []
        if stored < self.oldest():
            raise OrchestrationError(
                "No migration chain reaches the current data version from the stored version.",
                "data_migration_unavailable",
                {"stored": stored, "current": self.current, "oldest_migratable": self.oldest(), "release": RELEASE},
            )
        return [(source, self.steps[source]) for source in range(stored, self.current)]


REGISTRY = MigrationRegistry(DATA_VERSION, {})
