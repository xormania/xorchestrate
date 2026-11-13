"""Classified observation budgets with injected elapsed time and waiting effects."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .ports import Clock

CLASSIFICATIONS = frozenset({"harness_allowance", "product_guarantee", "fault_control"})
T = TypeVar("T")


def _positive_finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        valid = math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a positive finite number")


@dataclass(frozen=True)
class WaitBudget:
    """Only harness allowances expand with a configured timeout scale.

    Classification protects the duration from scaling; it does not turn a
    cooperative observation wait into a product latency assertion.
    """

    timeout: float
    classification: str
    scale: float = 1.0

    def __post_init__(self):
        if not isinstance(self.classification, str) or self.classification not in CLASSIFICATIONS:
            raise ValueError("Unknown wait budget classification")
        _positive_finite(self.timeout, "timeout")
        _positive_finite(self.scale, "timeout scale")
        try:
            effective = self.effective_timeout
        except OverflowError as error:
            raise ValueError("effective timeout must be a positive finite number") from error
        _positive_finite(effective, "effective timeout")

    @property
    def effective_scale(self):
        return self.scale if self.classification == "harness_allowance" else 1.0

    @property
    def effective_timeout(self):
        return self.timeout * self.effective_scale


class WaitTimeout(AssertionError):
    """An observation allowance expired; retain the actual consumed budget."""

    def __init__(self, message, *, budget, elapsed, attempts, retries):
        self.details = {
            "classification": budget.classification,
            "requested_timeout": budget.timeout,
            "effective_timeout": budget.effective_timeout,
            "configured_scale": budget.scale,
            "scale": budget.effective_scale,
            "elapsed": elapsed,
            "attempts": attempts,
            "retries": retries,
        }
        super().__init__(f"{message} ({budget.classification}: requested={budget.timeout:g}s, "
                         f"effective={budget.effective_timeout:g}s, scale={budget.effective_scale:g}, "
                         f"elapsed={elapsed:g}s, attempts={attempts}, retries={retries})")


def bounded_wait(predicate: Callable[[], T], budget: WaitBudget, *, clock: Clock,
                 sleep: Callable[[float], None], poll_interval: float = .01,
                 message: str = "Expected observation did not arrive",
                 retryable: Callable[[Exception], bool] | None = None) -> T:
    """Poll while one elapsed budget permits another attempt.

    A predicate already running is not preempted, and its truthy result is
    returned even if it finishes after the deadline. False observations and
    explicitly retryable errors consume the same budget. Sleeps are clamped to
    the remaining allowance, although a real scheduler can wake later.

    Injecting a clock/sleeper controls this loop only. It does not virtualize
    external I/O, interrupt blocked predicates, or provide a real-time watchdog.
    """
    _positive_finite(poll_interval, "poll interval")
    started = clock.monotonic()
    deadline = started + budget.effective_timeout
    if not math.isfinite(deadline):
        raise ValueError("wait deadline must be finite")
    attempts = retries = 0
    now = clock.monotonic()
    while now < deadline:
        attempts += 1
        try:
            result = predicate()
            if result:
                return result
        except Exception as error:
            if retryable is None or not retryable(error):
                raise
            retries += 1
        now = clock.monotonic()
        remaining = deadline - now
        if remaining <= 0:
            break
        sleep(min(poll_interval, remaining))
        now = clock.monotonic()
    raise WaitTimeout(message, budget=budget, elapsed=now - started, attempts=attempts, retries=retries)
