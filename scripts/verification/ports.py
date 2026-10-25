"""Replaceable observation decoding; no transport, storage or verdict dependency."""
from typing import Protocol


class NativeObservationDecoder(Protocol):
    """Translate one retained native envelope to facts, never passing verdicts.

    Facts use event native_tool/native_discovery/native_result, activity, or native_turn.
    A native_result preserves a native return envelope; only recognized application
    actions permit typed effect interpretation. Its optional
    arguments preserve native pre-enrichment input and result preserves the return.
    A native_turn has phase submitted/start/end, turn_id and anonymous. Explicit
    aliases, when applicable, are retained in turn_claims; the core reconciles
    them. The application evidence projection supplies qualified identity and
    untouched raw provenance to each fact. Unknown records produce no facts.
    """
    origin: str

    def decode(self, record: dict, mode: str | None) -> list[dict]: ...
