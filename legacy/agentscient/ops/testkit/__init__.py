"""The test kit: one world, one command runner, one fake registry, and
the case pattern on them. The contract is docs/tests.md.

Import from the repository root, the working directory every suite runs
in: ``from ops.testkit import World, run, refusal_wire``. There is no
other way in.
"""

from .cases import Mutant, RefusalCase, calibrate, refusal_wire, run_table, sidebyside
from .checks import Check, execute_checks, suite_checks
from .command import NotOnTheWire, Refusal, Result, Timeout, parse_wire, run
from .fakes import Tools
from .world import (
    Daemon,
    DaemonRefused,
    PlantFailed,
    Watch,
    World,
    compare,
    hermetic_env,
    make_executable,
    plant_bytes,
    plant_text,
    require,
)

__all__ = [
    "Check",
    "Daemon",
    "DaemonRefused",
    "Mutant",
    "NotOnTheWire",
    "PlantFailed",
    "Refusal",
    "RefusalCase",
    "Result",
    "Timeout",
    "Tools",
    "Watch",
    "World",
    "calibrate",
    "compare",
    "execute_checks",
    "hermetic_env",
    "make_executable",
    "parse_wire",
    "plant_bytes",
    "plant_text",
    "refusal_wire",
    "require",
    "run",
    "run_table",
    "sidebyside",
    "suite_checks",
]
