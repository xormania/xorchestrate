"""Deterministic checksum helper for fixture records."""


def checksum(value: str) -> int:
    return sum(value.encode("utf-8")) % 256
