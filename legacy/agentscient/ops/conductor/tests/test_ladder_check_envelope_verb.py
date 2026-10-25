"""Hermetic envelope refusal for the driver, items 3 and 7.

Written from contract items 3 and 7 of
`.dev/ladder-inputs/scope-item31-20260911.md`. The tool is the subject
and is not read here.

Item 7: every new refusal is one stderr line, exit 2, stdout empty,
with one hermetic invocation in the registry for each component that
gains a refusal. Item 3: a malformed field is class envelope naming
the field, at the check-tests stop. The registry row is how the gate
exercises class envelope without a ladder run.
"""

from __future__ import annotations

import sys
import unittest

from test_instrument_conventions import (
    REGISTRY_DIR,
    read_registry,
)
from test_ladder_lever import (
    RUN_CAP,
    _LadderFixture,
)

from ops.testkit import run


def _envelope_rows():
    rows, failures = read_registry(REGISTRY_DIR)
    if failures:
        raise AssertionError(f"malformed registry files: {failures}")
    return [
        row for row in rows
        if row.tool == "ladder.py" and row.refusal == "envelope"
    ]


class EnvelopeRefusalRegistry(unittest.TestCase):
    """Item 7 — ladder.py carries a hermetic class-envelope row."""

    def test_ladder_registry_has_a_hermetic_envelope_row(self):
        rows = _envelope_rows()
        mutant = [("ladder.py", ("run",), "arguments", False)]
        self.assertNotIn(
            ("ladder.py", (), "envelope", False),
            mutant,
        )
        self.assertTrue(
            rows,
            "item 7: ladder.py gains class envelope and must carry a "
            "hermetic registry row for it; found none",
        )
        for row in rows:
            self.assertFalse(
                row.identity,
                f"item 7: the envelope row is hermetic; found {row}",
            )


class EnvelopeRefusalSpeaksTheWire(_LadderFixture):
    """Item 7 — the registered invocation speaks the envelope refusal."""

    def assert_envelope_wire(self, proc) -> str:
        err = proc.stderr or ""
        self.assertEqual(proc.exit, 2, err or proc.stdout)
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        line = lines[0]
        head = line.split(":", 1)[0].strip().lower().replace("_", "-")
        self.assertEqual(head, "envelope", line)
        self.assertEqual(
            (proc.stdout or "").strip(),
            "",
            "refusal text belongs on stderr, not stdout",
        )
        lower = line.lower()
        for word in ("expected", "found", "needed"):
            self.assertIn(word, lower, line)
        self.assertLess(lower.index("expected"), lower.index("found"), line)
        self.assertLess(lower.index("found"), lower.index("needed"), line)
        return line

    def test_the_registered_envelope_invocation_refuses_on_empty_stdin(self):
        rows = _envelope_rows()
        self.assertTrue(
            rows,
            "item 7: ladder.py must register a hermetic envelope invocation",
        )
        row = rows[0]
        proc = run(
            [sys.executable, str(self.staged), *row.argv],
            cwd=str(self.invoke_cwd),
            env=self.env(),
            stdin="",
            timeout=RUN_CAP,
        )
        self.assert_envelope_wire(proc)


if __name__ == "__main__":
    unittest.main()
