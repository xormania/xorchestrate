"""The Dispatcher role lever: who may take it, and what the seam holds.

Contract: the `dispatcher` row of `ops/devlane/hooks/boundaries.json`,
whose evidence is this machine's `holder_note`. The subject is
`ops/conductor/tools/dispatcher.py`, run as a subprocess under a scratch
XDG_STATE_HOME with a staged lane conf, the way test_machine.py runs the
engine.

Two findings of the security audit 20260916T122524Z-review-codex-05a9f5
at aea65bb9 are the origin of every row below:

  #2 (p1) the hand-over checks the agent identity, not the session, so
     two sessions of one model take the role from each other. Measured
     before the fix: `set` from session-a then `set` from session-b, both
     under one WF_AGENT_OVERRIDE, both exit 0.
  #10 (p2) the seam validates only non-emptiness, so a multiline note
     with a secret in it is stored and echoed by `show`.

Declared gap: the lapsed branch of the hand-over. The role's ttl is
twelve hours and `--ttl` takes whole minutes, so no row here can wait one
out, and reaching in to move `held_since` would make this file a second
author of the engine's rows (docs/verified-couplings.md, shape). The
branch is one line, `if machine.lapsed(row): return`, and it is named
here rather than covered.

One producer (Claude Opus 5 (1M context)) under hand grant 265; the
second producer is absent by the owner's word (AGENTS.md, dev-lane
changes by hand).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
LEVER = REPO / "ops" / "conductor" / "tools" / "dispatcher.py"
ONE_AGENT = "One Model <noreply@example.test>"
A, B, C = "session-a-0001", "session-b-0002", "session-c-0003"
CAP = 60
ORIGIN = "20260916T122524Z-review-codex-05a9f5"


class TheRoleLever(unittest.TestCase):
    def setUp(self):
        self.assertTrue(LEVER.is_file(), f"INVALID: the lever is absent: {LEVER}")
        self.world = World(prefix="dispatcher-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")
        self.lever = self.world.stage(
            LEVER, siblings=("instrument.py", "laneconf.py", "machine.py")
        )

    def cli(self, *argv, agent=ONE_AGENT, sid=None):
        extra = {"LANE_CONF": str(self.conf), "WF_AGENT": agent}
        if sid is not None:
            extra["CLAUDE_CODE_SESSION_ID"] = sid
        return run([sys.executable, str(self.lever), *argv],
                   cwd=self.world.cwd, env=self.world.env(extra), timeout=CAP)

    def refusal(self, proc, klass):
        self.assertEqual(proc.exit, 2, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout, "", "a refusal keeps stdout empty")
        lines = [line for line in proc.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, proc.stderr)
        self.assertRegex(lines[0], rf"^{klass}: expected .+; found .+; needed .+$")
        return lines[0]

    def done(self, proc):
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return proc.stdout

    # -- the seam holds a session id and nothing else (#10) ----------------

    def test_a_note_that_is_not_a_session_id_refuses(self):
        with self.subTest(id="seam-shape", origin=ORIGIN):
            reason = self.refusal(
                self.cli("set", "not-a-session\ncredential=TOP-SECRET",
                         "--why", "demo", sid=A),
                "session",
            )
            self.assertNotIn("TOP-SECRET", reason,
                             "the refusal echoes the value it refused")
            self.assertIn("no Dispatcher named", self.done(self.cli("show", sid=A)))

    def test_a_note_carrying_a_space_refuses(self):
        with self.subTest(id="seam-space", origin=ORIGIN):
            self.refusal(self.cli("set", "session a", "--why", "demo", sid=A), "session")

    def test_a_session_id_is_taken(self):
        """The clean neighbour: the shape a harness actually gives."""
        with self.subTest(id="seam-clean", origin=ORIGIN):
            self.assertIn(A, self.done(self.cli("set", A, "--why", "today", sid=A)))
            self.assertIn(A, self.done(self.cli("show", sid=A)))

    # -- only the holding session hands the role on (#2) -------------------

    def test_a_peer_of_the_same_identity_cannot_take_a_live_role(self):
        with self.subTest(id="hand-session", origin=ORIGIN):
            self.done(self.cli("set", A, "--why", "today", sid=A))
            reason = self.refusal(
                self.cli("set", B, "--why", "mine now", sid=B), "session")
            self.assertIn(A, reason)
            self.assertIn(B, reason)
            self.assertIn(A, self.done(self.cli("show", sid=A)))

    def test_a_peer_of_the_same_identity_cannot_clear_a_live_role(self):
        with self.subTest(id="clear-session", origin=ORIGIN):
            self.done(self.cli("set", A, "--why", "today", sid=A))
            reason = self.refusal(self.cli("clear", "--why", "not mine"), "session")
            self.assertIn(A, reason)
            self.assertIn(A, self.done(self.cli("show", sid=A)))

    def test_the_holder_hands_the_role_on(self):
        """The clean neighbour: a hand-over from inside the holding session."""
        with self.subTest(id="hand-clean", origin=ORIGIN):
            self.done(self.cli("set", A, "--why", "today", sid=A))
            self.assertIn(C, self.done(self.cli("set", C, "--why", "over to c", sid=A)))
            self.assertIn(C, self.done(self.cli("show", sid=A)))

    def test_the_holder_clears_the_role(self):
        """The clean neighbour of the clear half."""
        with self.subTest(id="clear-clean", origin=ORIGIN):
            self.done(self.cli("set", A, "--why", "today", sid=A))
            self.assertIn("no Dispatcher named",
                          self.done(self.cli("clear", "--why", "done", sid=A)))

    def test_a_session_the_harness_did_not_name_cannot_take_a_live_role(self):
        with self.subTest(id="hand-unnamed", origin=ORIGIN):
            self.done(self.cli("set", A, "--why", "today", sid=A))
            reason = self.refusal(self.cli("set", B, "--why", "mine now"), "session")
            self.assertIn(A, reason)

    def test_the_first_set_needs_no_holder(self):
        """The bootstrap: an unset role is taken by anyone, as the machine
        table says, and only the hand-over is narrowed."""
        with self.subTest(id="bootstrap", origin=ORIGIN):
            self.assertIn(B, self.done(self.cli("set", B, "--why", "first", sid=B)))


if __name__ == "__main__":
    unittest.main()
