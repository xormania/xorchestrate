"""The acceptance gate: what an acceptance must rest on (AC-10).

Contract: `ops/conductor/coordination-roles.md` §The acceptance gate
(package E). The subject is `ops/conductor/tools/coordination.py`. The
world these rows run in is the one the brief's rows carry,
`test_coordination_brief.CoordinationWorld`: its own HOME, its own XDG
state, its own store, and nothing of the operator's.

Written by hand under grant 272 (owner, 2026-09-16: "hand grant ok'd" at
the session's start and "get it done" today, relayed by the Lead session
agentscient-11), one producer (Claude Opus 5, 1M context), the second
producer absent by the owner's word (AGENTS.md §Ground rules). The one
command that reads this file is `python3 -m unittest discover -s
ops/conductor/tests -p test_coordination_accept.py`: red while
`core_accept` checks only role, generation and producer, read again
after the gate is written, both readings quoted in the commit.
"""

from __future__ import annotations

import unittest

from test_coordination_brief import BRANCH, CHECKER, LEAD, WORKER, CoordinationWorld

from ops.testkit import RefusalCase, refusal_wire

REQUIREMENT = "AC-10"


class _Gate(CoordinationWorld):
    """A candidate carried to verified, with the evidence each row wants."""

    def carried(self, *, observe=(), requirement=REQUIREMENT) -> str:
        self.lock()
        self.bind_two()
        self.ok("bind", "--roles", "worker", session=CHECKER)
        self.ok("grant", "PT-3", "--roles", "verifier", session=LEAD)
        opened = self.ok("draft", "the gate's trial row", "--outcome",
                         "an unsupported acceptance refuses",
                         "--requirement", requirement,
                         "--revision", "a1b2c3d", session=LEAD)
        aid = opened.split()[-1]
        self.ok("offer", aid, "--to", "PT-2", "--generation", self.gen(aid), session=LEAD)
        self.ok("claim", aid, "--branch", BRANCH, "--generation", self.gen(aid),
                session=WORKER)
        for applicability in observe:
            argv = [aid, "--observation", f"the suite ran ({applicability})",
                    "--revision", "a1b2c3d", "--generation", self.gen(aid)]
            if applicability == "stale":
                argv.append("--stale")
            self.ok("observe", *argv, session=WORKER)
        self.ok("submit", aid, "--evidence", "a1b2c3d", "--revision", "a1b2c3d",
                "--generation", self.gen(aid), session=WORKER)
        self.ok("verify", aid, "--candidate", "a1b2c3d", "--evidence",
                "20260916T010203Z-job-v", "--generation", self.gen(aid),
                session=CHECKER)
        return aid


class TheGate(_Gate):
    def test_an_acceptance_with_no_examination_refuses(self):
        """A requirement nobody examined is the gap AC-10 names."""
        aid = self.carried()
        proc = self.run_tool("accept", aid, "--evidence", "a1b2c3d",
                             "--generation", self.gen(aid), session=LEAD)
        refusal_wire(
            RefusalCase("no-examination", ("accept",), "evidence",
                        named=(REQUIREMENT, "a1b2c3d")),
            proc,
        )
        self.assertIn("verified", self.ok("show", aid, session=LEAD))

    def test_an_acceptance_over_evidence_gone_stale_refuses(self):
        """One current reading does not cover a reading that went stale
        against the same candidate."""
        aid = self.carried(observe=("current", "stale"))
        proc = self.run_tool("accept", aid, "--evidence", "a1b2c3d",
                             "--generation", self.gen(aid), session=LEAD)
        refusal_wire(
            RefusalCase("stale-evidence", ("accept",), "evidence", named=("stale",)),
            proc,
        )

    def test_a_current_examination_accepts(self):
        aid = self.carried(observe=("current",))
        self.ok("accept", aid, "--evidence", "a1b2c3d", "--generation", self.gen(aid),
                session=LEAD)
        self.assertIn("accepted", self.ok("show", aid, session=LEAD))

    def test_the_stale_evidence_of_an_earlier_candidate_does_not_block(self):
        """`reopen` stales what was read against the old revision. That
        is history, not a hole in the new candidate's evidence."""
        aid = self.carried(observe=("current",))
        self.ok("reopen", aid, "--revision", "b2c3d4e", "--why", "the candidate moved",
                "--generation", self.gen(aid), session=LEAD)
        self.ok("observe", aid, "--observation", "the suite ran again",
                "--revision", "b2c3d4e", "--generation", self.gen(aid), session=WORKER)
        self.ok("submit", aid, "--evidence", "b2c3d4e", "--revision", "b2c3d4e",
                "--generation", self.gen(aid), session=WORKER)
        self.ok("verify", aid, "--candidate", "b2c3d4e", "--evidence",
                "20260916T010204Z-job-v", "--generation", self.gen(aid), session=CHECKER)
        self.ok("accept", aid, "--evidence", "b2c3d4e", "--generation", self.gen(aid),
                session=LEAD)
        self.assertIn("accepted", self.ok("show", aid, session=LEAD))


class TheRecordedException(_Gate):
    def test_an_exception_carries_the_acceptance_and_reads_back_as_one(self):
        aid = self.carried(observe=("current", "stale"))
        words = "the reviewer is unavailable; the risk is written in #248"
        out = self.ok("accept", aid, "--evidence", "a1b2c3d",
                      "--generation", self.gen(aid), "--exception", words,
                      session=LEAD)
        self.assertIn("exception", out)
        shown = self.ok("show", aid, session=LEAD)
        self.assertIn("accepted", shown)
        self.assertIn(words, shown)

    def test_an_exception_without_words_refuses_class_arguments(self):
        aid = self.carried(observe=("current", "stale"))
        proc = self.run_tool("accept", aid, "--evidence", "a1b2c3d",
                             "--generation", self.gen(aid), "--exception", "   ",
                             session=LEAD)
        refusal_wire(
            RefusalCase("wordless-exception", ("accept", "--exception"), "arguments",
                        named=("words",)),
            proc,
        )


class Authority(_Gate):
    def test_a_caller_without_the_lead_role_reads_role_before_evidence(self):
        """Authority first: a participant that may not accept at all
        learns that, not what the evidence lacks."""
        aid = self.carried()
        proc = self.run_tool("accept", aid, "--evidence", "a1b2c3d",
                             "--generation", self.gen(aid), session=WORKER)
        refusal_wire(
            RefusalCase("worker-accepts", ("accept",), "role", named=("lead",)),
            proc,
        )


if __name__ == "__main__":
    unittest.main()
