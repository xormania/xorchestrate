"""Canonical JSON, digests, and the canonical spelling of a timestamp.

Canonical form is *defined* (PLAN §2) as byte-identical to Python's

    json.dumps(obj, sort_keys=True, separators=(",", ":"))

so a chain written here verifies in any language that can reproduce those
bytes.  Everything hashed anywhere in ``wf`` goes through :func:`dumpb`.

``allow_nan`` is off: ``NaN``/``Infinity`` are not JSON, so a conforming
parser in another language could not read them back.  For every value that
is JSON at all the bytes are identical to the definition above.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json

# The `prev` of the first event in a chain: 64 zeroes, the width of a sha256.
ZERO_DIGEST = "0" * 64

# RFC3339 UTC, exactly six fractional digits, literal Z.  The time is hashed
# as a string, so it must have exactly one spelling.
TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _reject_constant(name: str) -> None:
    raise ValueError(f"canonical: {name} is not JSON")


def dumps(obj) -> str:
    """The canonical text form of ``obj``."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def dumpb(obj) -> bytes:
    """The canonical bytes of ``obj`` — what gets hashed and stored."""
    return dumps(obj).encode("utf-8")


def loads(raw):
    """Decode canonical (or any) JSON bytes/str, refusing non-JSON constants."""
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    return json.loads(raw, parse_constant=_reject_constant)


def sha256_hex(data: bytes) -> str:
    """Lowercase hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def digest(obj) -> str:
    """Lowercase hex sha256 of the canonical bytes of ``obj``."""
    return sha256_hex(dumpb(obj))


def is_canonical(raw) -> bool:
    """True when ``raw`` is already the canonical encoding of what it decodes to.

    A document that merely *means* the same thing is not the same record: key
    order, whitespace, escaping, and number spelling are all part of what was
    hashed.
    """
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        value = loads(raw)
    except (ValueError, UnicodeDecodeError):
        return False
    try:
        return dumpb(value) == raw
    except (ValueError, TypeError):
        return False


def format_time(when: _dt.datetime) -> str:
    """The canonical wire spelling of an instant."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.astimezone(_dt.timezone.utc).strftime(TIME_FORMAT)


def parse_time(raw: str) -> _dt.datetime:
    """Parse a canonical wire time.

    UTC must be spelled with a trailing ``Z``: the value is hashed as a
    string, so an equivalent offset such as ``+00:00`` would be a second
    spelling of the same instant and therefore a second hash.
    """
    if not isinstance(raw, str) or not raw.endswith("Z"):
        raise ValueError(f"canonical: time {raw!r} is not UTC spelled with a trailing Z")
    parsed = _dt.datetime.strptime(raw, TIME_FORMAT)  # noqa: DTZ007 —
    # the guard above refuses anything not spelled with a trailing Z
    # and tzinfo is attached on the next line.
    return parsed.replace(tzinfo=_dt.timezone.utc)


def now() -> str:
    """The current instant in canonical wire form."""
    return format_time(_dt.datetime.now(_dt.timezone.utc))
