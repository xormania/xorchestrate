"""Replay ledger for native tool calls, shared by the native session drivers.

Drivers keep their own wire handling and native-observation matching. This ledger
only deduplicates a call identity by its argument fingerprint and retains each
call's ``native_effect`` lifecycle: a completed call replays its result, a
recorded failure is raised again, and an unsettled call is refused. Callers
serialize access with their existing session lock.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy

from ..model import OrchestrationError, public_error_message
from ..outcomes import effect_outcome
from .process import encode_json

DEFAULT_LIMIT = 10000


def call_fingerprint(action, arguments) -> str:
    return hashlib.sha256(encode_json([action, arguments], sort_keys=True)).hexdigest()


class NativeCallLedger:
    def __init__(self, engine, limit=DEFAULT_LIMIT):
        self.engine, self.limit = engine, limit
        self._rows = {}

    def __contains__(self, key):
        return key in self._rows

    def __len__(self):
        return len(self._rows)

    def get(self, key):
        return self._rows.get(key)

    def values(self):
        return self._rows.values()

    def _transition(self, key, event):
        row = self._rows.get(key)
        return self.engine.decide("native_effect", row["state"] if row else None, event,
                                  {"kind": "callback", "identity": key}).target

    def reserve(self, key, fingerprint, /, **fields):
        """Reserve a new call and return ``None``, or return a detached completed row to replay."""
        prior = self._rows.get(key)
        if prior is None:
            if len(self._rows) >= self.limit:
                raise OrchestrationError("Native call replay ledger is full.", "session_capacity")
            self._rows[key] = {"fingerprint": fingerprint, "state": self._transition(key, "reserve"), **deepcopy(fields)}
            return None
        if prior["fingerprint"] != fingerprint:
            raise OrchestrationError("Native call ID was reused with different arguments.", "native_call_conflict")
        if prior["state"] == "completed":
            return deepcopy(prior)
        if "error" in prior:
            error = prior["error"]
            raise OrchestrationError(error["message"], error["code"], deepcopy(error["details"]))
        raise OrchestrationError("The native call is already in progress.", "call_in_progress",
                                 {"outcome": prior.get("effect_outcome", "unknown")})

    def complete(self, key, result, /, **fields):
        """Retain a returned result before settlement, so a refused settlement cannot lose it."""
        self._rows[key].update({"result": deepcopy(result), "effect_outcome": "completed", **deepcopy(fields)})

    def succeed(self, key):
        self._rows[key]["state"] = self._transition(key, "succeed")

    def fail(self, key, error):
        """Record a failure: definite ones fail, others stay uncertain. Returns the recorded error."""
        outcome = effect_outcome(error)
        row = self._rows[key]
        try:
            row["state"] = self._transition(key, "fail" if outcome in {"not_started", "failed"} else "uncertain")
        except Exception as recording_error:
            # The refusal fact is retained even when its transition is refused.
            row["recording_error"] = getattr(recording_error, "code", type(recording_error).__name__)
        row["effect_outcome"] = outcome
        # A replay re-raises this row as a public OrchestrationError, so only an
        # expected adapter error contributes its code, message and details.
        public = isinstance(error, OrchestrationError)
        row["error"] = {"code": error.code if public else "callback_failed", "message": public_error_message(error),
                        "details": {**(deepcopy(error.details) if public else {}), "outcome": outcome}}
        return deepcopy(row["error"])
