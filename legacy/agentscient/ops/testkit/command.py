"""One command runner, one result shape, one timeout policy.

``run`` executes an argv without a shell, in a declared working
directory and environment, under a timeout whose expiry kills and
reaps the child and raises ``Timeout`` naming the argv. A ``Result``
reads its own refusal line into class, expected, found and needed, per
docs/wire.md: exit 2 by convention, stdout empty, exactly one non-empty
stderr line, the class token first, then expected, found, needed in
that order.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .world import PlantFailed

DEFAULT_TIMEOUT = 60.0
CLEANUP = 5.0
# docs/wire.md: the class is a lower-case token; a refusal that only
# says "invalid" (or arrives with an upper-case, underscored or empty
# token) makes the next actor guess.
CLASS_TOKEN = re.compile(r"[a-z][a-z0-9-]*")


class Timeout(AssertionError):
    """The command outlived its timeout; the child was killed and reaped."""


class NotOnTheWire(AssertionError):
    """The result is not a refusal in the wire's shape; the message says which rule."""


@dataclass(frozen=True)
class Refusal:
    cls: str
    expected: str
    found: str
    needed: str
    line: str


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    exit: int
    stdout: str
    stderr: str
    duration: float
    cwd: str

    @property
    def returncode(self) -> int:
        """The subprocess spelling, without a second result object."""
        return self.exit

    @property
    def lines(self) -> list[str]:
        return [ln for ln in self.stderr.splitlines() if ln.strip()]

    def refusal(self, *, exit: int = 2) -> Refusal:
        """The one refusal line, parsed; ``NotOnTheWire`` names the first
        rule the result breaks."""
        if self.exit != exit:
            raise NotOnTheWire(f"exit {exit} expected, found {self.exit}: {self.stderr!r}")
        if self.stdout != "":
            raise NotOnTheWire(f"stdout must be empty on a refusal, found {self.stdout!r}")
        lines = self.lines
        if len(lines) != 1:
            raise NotOnTheWire(f"one stderr line expected, found {len(lines)}: {self.stderr!r}")
        return parse_wire(lines[0])


def parse_wire(line: str) -> Refusal:
    if ":" not in line:
        raise NotOnTheWire(f"no class token before ':' in {line!r}")
    cls, rest = line.split(":", 1)
    if not CLASS_TOKEN.fullmatch(cls):
        raise NotOnTheWire(f"the class token must be one lower-case word of letters, digits and hyphens, found {cls!r}")
    lower = rest.lower()
    marks = [lower.find("expected"), lower.find("found"), lower.find("needed")]
    if -1 in marks:
        raise NotOnTheWire(f"expected, found and needed must each appear: {line!r}")
    if not marks[0] < marks[1] < marks[2]:
        raise NotOnTheWire(f"expected must precede found, found must precede needed: {line!r}")
    expected = rest[marks[0] + len("expected"):marks[1]].strip(" ;,")
    found = rest[marks[1] + len("found"):marks[2]].strip(" ;,")
    needed = rest[marks[2] + len("needed"):].strip(" ;,")
    for name, value in (("expected", expected), ("found", found), ("needed", needed)):
        if not value:
            raise NotOnTheWire(f"{name} must say something, found it empty: {line!r}")
    return Refusal(cls, expected, found, needed, line)


def _kill_session(child: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(child.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        child.kill()


def run(argv, *, cwd, env, stdin: str | None = None, timeout: float = DEFAULT_TIMEOUT,
        expect: int | None = None) -> Result:
    """Run ``argv`` and return its ``Result``. ``expect`` names the exit a
    fixture step must produce; any other exit is INVALID, never a verdict.

    The child leads its own session, so a timeout kills every descendant
    with it and the cleanup is bounded by ``CLEANUP``: a grandchild holding
    the pipes cannot keep ``Timeout`` from arriving."""
    argv = tuple(str(a) for a in argv)
    started = time.monotonic()
    try:
        child = subprocess.Popen(
            argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
    except FileNotFoundError as exc:
        # The world's PATH is its own bin: a tool the case neither planted
        # nor named is absent, and saying so beats a fabricated Result for
        # a process that never started (LD-72).
        raise PlantFailed(
            f"unplanted: expected {argv[0]} on the world's PATH; "
            f"found {argv[0]} absent from {env.get('PATH', '')!r}; "
            f'needed world.tools.plant("{argv[0]}") '
            f'or World(ambient_tools=("{argv[0]}",))'
        ) from exc
    try:
        out, err = child.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_session(child)
        try:
            child.communicate(timeout=CLEANUP)
        except subprocess.TimeoutExpired:
            for stream in (child.stdin, child.stdout, child.stderr):
                with contextlib.suppress(OSError):
                    stream.close()
            child.wait(timeout=CLEANUP)
        raise Timeout(f"{argv!r} in {cwd} outlived {timeout:g}s; killed and reaped") from exc
    result = Result(argv, child.returncode, out, err, time.monotonic() - started, str(Path(cwd)))
    if expect is not None and result.exit != expect:
        raise PlantFailed(
            f"INVALID: {argv!r} in {cwd} exited {result.exit} (wanted {expect})\n"
            f"stdout: {out}\nstderr: {err}"
        )
    return result
