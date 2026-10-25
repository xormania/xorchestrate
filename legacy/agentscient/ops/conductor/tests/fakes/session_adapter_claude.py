# testkit: exempt fixture program planted as the claude session adapter
"""A logging wrapper around the real claude session adapter.

Staged beside a copy of sessions.py in place of session_adapters/claude.py.
It delegates every method to the real adapter and appends one JSON line
per observe_identity and probe call, so a row can see the supervisor went
through the adapter boundary. The stage substitutes two anchors, REAL (the
real adapter's path) and LOG (the call log), each written once below.
origin: 20260923T003033Z-review-codex-c99cc9#6
"""
from __future__ import annotations

import importlib.util
import json

_SPEC = importlib.util.spec_from_file_location("session_adapter_claude_real", "@@REAL@@")
_REAL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_REAL)
_LOG = "@@LOG@@"


def _log(entry: dict) -> None:
    with open(_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


class Adapter(_REAL.Adapter):
    def observe_identity(self, store, launch_id):
        found = super().observe_identity(store, launch_id)
        _log({"method": "observe_identity", "launch_id": launch_id, "found": found})
        return found

    def probe(self, executable):
        reading = super().probe(executable)
        _log({"method": "probe", "executable": str(executable), "reading": reading})
        return reading
