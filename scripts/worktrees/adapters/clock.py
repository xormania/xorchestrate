"""Wall-clock time."""

from __future__ import annotations

import time


class SystemClock:
    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(max(seconds, 0.0))
