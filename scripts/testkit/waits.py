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


def _nonnegative_finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a nonnegative finite number")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a nonnegative finite number")


@dataclass(frozen=True)
class Deadline:
    """A live local boundary, shared by nested blocking operations.

    Zero remaining means no allowance, never an unbounded timeout or a new
    minimum allowance. A child shares this clock and cannot extend its parent.
    This value controls supplied blocking timeouts, not arbitrary code or the
    operating system's scheduling latency; it is not portable across boots.
    """

    expires: float
    clock: Clock

    def __post_init__(self):
        try:
            finite = not isinstance(self.expires, bool) and math.isfinite(self.expires)
        except (TypeError, OverflowError):
            finite = False
        if not finite:
            raise ValueError("deadline must be finite")

    @classmethod
    def after(cls, seconds, *, clock):
        _nonnegative_finite(seconds, "duration")
        return cls(clock.monotonic() + seconds, clock)

    def remaining(self, limit=None):
        if limit is not None:
            _nonnegative_finite(limit, "remaining limit")
        left = max(0.0, self.expires - self.clock.monotonic())
        return left if limit is None else min(left, limit)

    def child(self, timeout):
        child = self.after(timeout, clock=self.clock)
        return Deadline(min(self.expires, child.expires), self.clock)


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

    def start(self, clock, *, parent=None):
        if parent is not None:
            if parent.clock is not clock:
                raise ValueError("nested deadlines must share the same clock")
            return parent.child(self.effective_timeout)
        return Deadline.after(self.effective_timeout, clock=clock)


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
                 retryable: Callable[[Exception], bool] | None = None,
                 deadline: Deadline | None = None) -> T:
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
    running = budget.start(clock, parent=deadline)
    attempts = retries = 0
    now = clock.monotonic()
    while now < running.expires:
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
        remaining = running.expires - now
        if remaining <= 0:
            break
        sleep(min(poll_interval, remaining))
        now = clock.monotonic()
    raise WaitTimeout(message, budget=budget, elapsed=now - started, attempts=attempts, retries=retries)
