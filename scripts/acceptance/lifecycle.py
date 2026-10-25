"""Compatibility module for the shared owned-terminal adapter.

Aliasing the module preserves existing instrumentation of its process probes;
the implementation and its mutable module state have exactly one owner.
"""

import sys

from scripts.testkit.adapters import tmux

sys.modules[__name__] = tmux
