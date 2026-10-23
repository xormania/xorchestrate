"""Shared harness-test paths, reached from the repository root."""

import importlib.util
import sys
from pathlib import Path

from ops.testkit import World  # noqa: F401 - docs/tests.md import rule

HARNESS = Path(__file__).resolve().parents[1]


def load_sibling(name: str):
    """Expose a legacy top-level subject import without changing sys.path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HARNESS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


load_sibling("isolation")
