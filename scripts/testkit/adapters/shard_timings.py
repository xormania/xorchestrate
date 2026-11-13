"""Read, freeze and verify the timing input shared by shard execution and qualification."""

from __future__ import annotations

import json
from pathlib import Path

from ..sharding import TIMINGS, validate_timings
from .evidence import digest


def timing_source(root, path=None, *, external=False):
    root = Path(root).resolve()
    source = Path(path).expanduser() if path is not None else root / TIMINGS
    source = source.resolve() if source.is_absolute() else (root / source).resolve()
    if external and path is not None and (source == root or root in source.parents):
        raise ValueError("--timings must be outside the measured candidate")
    return source


def read_timings(root, path=None):
    """Parse and identify one byte sequence, avoiding separate reads for weights and identity."""
    source = timing_source(root, path)
    if not source.is_file():
        raise ValueError("Test timings must be a regular file")
    raw = source.read_bytes()
    default, weights = validate_timings(json.loads(raw))
    return raw, default, weights, digest(raw)


def verify_timings(root, path, sha256):
    """Reject a snapshot changed since its owner captured the model."""
    raw, default, weights, observed = read_timings(root, path)
    if observed != sha256:
        raise ValueError("Test timings changed after the shard model was frozen")
    return raw, default, weights, observed


def freeze_timings(root, directory, path=None):
    """Keep the selected model outside the checkout, without rewriting its bytes."""
    source = timing_source(root, path, external=True)
    raw, _, _, sha256 = read_timings(root, source)
    target = Path(directory) / "timings.json"
    with target.open("xb") as stream:
        stream.write(raw)
    target.chmod(0o400)
    return {"path": str(target), "sha256": sha256}
