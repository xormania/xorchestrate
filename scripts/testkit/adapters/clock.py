"""Wall-clock effects for the injected test-kit clock port."""

import time


class SystemClock:
    def monotonic(self):
        return time.monotonic()
