"""Exit codes and the two failure kinds the engine must never conflate.

PLAN §4 fixes the map (loopflow's, unchanged).  The distinction that
matters is between a **refusal** — a mechanical precondition was not met,
nothing was recorded, the caller can fix it and retry — and an **integrity**
failure, where something did not re-hash to what was recorded.  An
integrity failure blocks, is never downgraded to a refusal, and is never
accompanied by a projection (PLAN §3.1).
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_INTEGRITY = 2
EXIT_CLAIMED = 3
EXIT_USAGE = 64

# Outside PLAN §4's map on purpose: a bug in wf is neither a refusal nor an
# evidence-integrity failure, and Python's default of 1 would file a crash
# under "refused". 70 is the BSD sysexits code for an internal software error.
EXIT_INTERNAL = 70


class WfError(Exception):
    """Base for everything the CLI turns into a documented exit code."""

    exit_code = EXIT_REFUSED
    kind = "error"

    def payload(self) -> dict:
        return {"error": self.kind, "message": str(self)}


class Refusal(WfError):
    """A mechanical precondition was not met.

    Validation is fail-fast: this names the **first** failed precondition,
    never all of them, so it must never be read as a complete account of
    what blocks a work order.  That is what ``exhaustiveness`` records on
    the attempt row.
    """

    exit_code = EXIT_REFUSED
    kind = "refusal"

    def __init__(self, precondition, *, expected=None, actual=None, needed=None, wo=None):
        self.precondition = precondition
        self.expected = expected
        self.actual = actual
        self.needed = needed
        self.wo = wo
        parts = [f"refused: {precondition}"]
        if expected is not None:
            parts.append(f"expected {expected}")
        if actual is not None:
            parts.append(f"actual {actual}")
        if needed is not None:
            parts.append(f"needed: {needed}")
        super().__init__(" — ".join(parts))

    def payload(self) -> dict:
        return {
            "error": "refusal",
            "wo": self.wo,
            "precondition": self.precondition,
            "expected": self.expected,
            "actual": self.actual,
            "needed": self.needed,
            "exhaustiveness": "first-failure",
            "message": str(self),
        }


class Integrity(WfError):
    """Recorded evidence does not re-hash to what it claims.

    Never downgraded, never accompanied by a projection.
    """

    exit_code = EXIT_INTEGRITY
    kind = "integrity"

    def __init__(self, problem, *, wo=None, seq=None, detail=None):
        self.problem = problem
        self.wo = wo
        self.seq = seq
        self.detail = detail
        where = wo or "?"
        if seq is not None:
            where = f"{where}#{seq}"
        message = f"integrity: {where}: {problem}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)

    def payload(self) -> dict:
        return {
            "error": "integrity",
            "wo": self.wo,
            "seq": self.seq,
            "problem": self.problem,
            "detail": self.detail,
            "message": str(self),
        }


class Claimed(WfError):
    """The work order is claimed by another agent (Slice 2 verbs)."""

    exit_code = EXIT_CLAIMED
    kind = "claimed"


class Usage(WfError):
    """The command line itself was wrong."""

    exit_code = EXIT_USAGE
    kind = "usage"
