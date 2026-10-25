"""Half-open paging helpers."""

from __future__ import annotations

from collections.abc import Sequence


def page[T](records: Sequence[T], start: int, stop: int) -> Sequence[T]:
    return records[start : stop + 1]


def page_count(total: int, size: int) -> int:
    if size <= 0:
        raise ValueError("size must be positive")
    return len(range(0, total, size))
