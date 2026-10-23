"""Load a task-app module by path, the way the other suites do.

The dev-lane apps are standalone scripts, not an installed package:
`ops/devlane/task/envelope.py` has no importable dotted name. Every suite
here loads it from its path, so a test file can be run on its own
(`python3 ops/devlane/task/tests/test_envelope.py`) without changing the
interpreter's import path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ops.testkit import World as World

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[2]


def _load(name, key):
    path = APP / f"{name}.py"
    spec = importlib.util.spec_from_file_location(key, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def load(name):
    """Import <app>/<name>.py under a task_ prefix and return it."""
    # Standalone task scripts use plain sibling imports. Prime those
    # names explicitly instead of making the application directory a
    # process-wide import path.
    dependencies = {
        "verify": ("envelope",),
        "run": ("envelope", "fileset", "verify"),
    }
    for sibling in dependencies.get(name, ()):
        if sibling not in sys.modules:
            if sibling == "verify" and "envelope" not in sys.modules:
                _load("envelope", "envelope")
            _load(sibling, sibling)
    return _load(name, f"task_{name}")
