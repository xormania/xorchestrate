"""Compatibility composition for application evidence normalization."""
from .adapters.native import default_decoders
from .evidence import normalize as _normalize


def normalize(raw, peer_sessions, provider_events, mode, *, decoders=None):
    return _normalize(raw, peer_sessions, provider_events, mode,
                      decoders=default_decoders() if decoders is None else decoders)
