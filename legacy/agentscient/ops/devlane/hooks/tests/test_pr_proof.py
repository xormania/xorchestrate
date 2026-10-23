"""pr-proof: a PR is marked ready or merged only against proof the system
can verify at that boundary.

Owner, 2026-09-12: "proof needs to be part of the system - it has to
generated and verified at boundaries"; "it has to be impossible to not do
the work - impossible"; "you can't take any agents word on anything - you
need proof, evidence". Measured that day: PR #141 was merged before its
checks registered (pitfall 22) and three capabilities were recorded as
done with no reading that any dispatch consumed them.

From 2026-09-16 the ready flip also crosses the `audit` row, declared
beside this one in the registry and planted with its faults in
test_pr_audit.py; this file's fixture satisfies it so these rows keep
measuring the proof section, the corpus at the head and the ci run.

The row: `gh pr ready N` and `gh pr merge N` refuse class `pr-proof`
(a deny: the remedy, the proof, is the agent's own; owner, 2026-09-12:
"i don't want asks unless they're critical") unless the PR body carries a `## Proof` section naming corpus row ids
(`P-...`) that all exist in `ops/conductor/handoff/probes/` at the PR
head, the head is a fetched object here, and a `ci` run on that head has
completed with success (the run executed those rows). No grant escape.
`gh pr view` and every other gh verb are untouched.

One producer (Claude Fable 5.1) under hand grant 159 wrote these tests
and the row; the second producer is absent by the owner's word.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

import support

from ops.testkit import World, run

HOOK = Path(__file__).resolve().parents[1] / "claude" / "matrix.py"
CORPUS = "ops/conductor/handoff/probes/probes-2026-09-12-fixture.jsonl"
TIMEOUT = 60


class PrProofTests(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="pr-proof-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self.hook = self._subject()
        self.repo = self.world.cwd
        self.world.tools.plant("cue", exit=0)
        self._plant_gh(fail=False, pr={"body": "", "headRefOid": "0" * 40,
                                      "headRefName": "lane/fixture",
                                      "isDraft": True}, runs=[])
        self.env = self.world.env()
        self.assertEqual(
            Path(shutil.which("gh", path=self.env["PATH"])).resolve(),
            (self.world.bin / "gh").resolve(),
            "INVALID: fake gh not first on PATH",
        )
        self.world.git("init", "-q", "-b", "dev")
        self.world.plant_text(
            self.repo / "ops" / "lane.conf",
            "LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\nSCOPE_CAP='1024'\n"
            f"MODEL_CODEX_SECURITY_AUDIT='{support.AUDIT_MODEL}'\n",
        )
        self.world.plant_text(
            self.repo / CORPUS,
            json.dumps({"id": "P-FIX-1", "kind": "serve", "lane": "git",
                        "question": "q", "serve": "git log -1",
                        "expect": "x"}) + "\n",
        )
        self.world.plant_text(self.repo / support.FINDINGS_TOOL, support.findings_vocabulary())
        self.world.git("add", "-A")
        self.world.git("commit", "-q", "-m", "fixture")
        audited = self.world.git("rev-parse", "HEAD").stdout.strip()
        # The same command crosses the audit row (class `audit`), so the world
        # carries a security audit closed at the head with its findings
        # disposed; test_pr_audit.py is where that row's faults are planted.
        self.world.plant_text(
            self.repo / support.DISPATCH_RECORDS / f"{support.AUDIT_RECORD_ID}.json",
            json.dumps(support.security_audit_record(audited=audited), indent=1),
        )
        self.world.plant_text(
            self.repo / support.FINDINGS_EXPORT,
            support.findings_export(*support.audit_disposed()),
        )
        self.world.git("add", "-A")
        self.world.git("commit", "-q", "-m", "records: the audit and its dispositions")
        self.head = self.world.git("rev-parse", "HEAD").stdout.strip()

    def _subject(self):
        staged = self.world.stage(HOOK, siblings=("miss.py", "command_shape.py"))
        # Kit gap: stage copies siblings beside the subject, not the parent
        # data file matrix.py loads as ../boundaries.json.
        src = HOOK.parent.parent / "boundaries.json"
        self.world.plant_bytes(staged.parent.parent / "boundaries.json", src.read_bytes())
        self.world.plant_bytes(staged.parent.parent / "guidance.py", (HOOK.parent.parent / "guidance.py").read_bytes())
        return staged

    def _plant_gh(self, *, fail, pr=None, runs=None):
        if fail:
            self.world.tools.plant(
                "gh", stderr="gh: authentication failed\n", exit=1,
            )
            return
        self.world.tools.plant(
            "gh",
            stderr="fake gh: unexpected\n",
            exit=2,
            responses=[
                {"match": ["pr", "view"], "stdout": json.dumps(pr),
                 "stderr": "", "exit": 0},
                {"match": ["run", "list"], "stdout": json.dumps(runs),
                 "stderr": "", "exit": 0},
            ],
        )

    def plant(self, *, body, head=None, runs=None):
        head = head or self.head
        pr = {"body": body, "headRefOid": head, "headRefName": "lane/fixture",
              "isDraft": True}
        if runs is None:
            runs = [{"name": "ci", "workflowName": "ci", "status": "completed",
                     "conclusion": "success", "headSha": head}]
        self._plant_gh(fail=False, pr=pr, runs=runs)

    def run_bash(self, command, env=None):
        payload = json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": command},
                              "cwd": str(self.repo)})
        return run(
            [sys.executable, str(self.hook)],
            cwd=str(self.repo),
            env=env or self.env,
            stdin=payload,
            timeout=TIMEOUT,
        )

    def _call_text(self):
        return "\n".join(
            " ".join(c["argv"]) for c in self.world.tools.calls("gh")
        )

    def refusal(self, proc):
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertTrue(proc.stdout.strip(), "hook let the call through")
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        # pr-proof is a hard row with no grant escape; it denies, because
        # the proof is the agent's to add (owner, 2026-09-12)
        self.assertNotIn("permissionDecision", out)
        reason = out["additionalContext"].removeprefix("Diagnostic (advisory): ")
        self.assertTrue(reason.startswith("[matrix:pr-proof] pr-proof: expected "), reason)
        self.assertIn("; found ", reason)
        self.assertIn("; needed ", reason)
        return reason

    def allowed(self, proc):
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "", proc.stdout)

    GOOD = ("## What\n\nx\n\n## Proof\n\nRows at the head: P-FIX-1.\n"
            f"Security audit: {support.AUDIT_RECORD_ID}.\n"
            + support.proof_findings(*support.audit_disposed())
            + "\n## Why\n\ny\n")

    def test_ready_and_merge_pass_with_proof_rows_at_a_green_head(self):
        self.plant(body=self.GOOD)
        self.allowed(self.run_bash("gh pr ready 12"))
        self.allowed(self.run_bash("gh pr merge 12 --merge"))
        calls = self._call_text()
        self.assertIn("pr view 12", calls)
        self.assertIn("run list", calls)
        self.assertIn(self.head, calls)

    def test_no_proof_section_refuses(self):
        self.plant(body="## What\n\nx\n\n## Readings\n\nsee P-FIX-1\n")
        reason = self.refusal(self.run_bash("gh pr ready 12"))
        self.assertIn("Proof", reason)

    def test_proof_section_without_ids_refuses(self):
        self.plant(body="## Proof\n\nwe tested it by hand\n")
        self.refusal(self.run_bash("gh pr ready 12"))

    def test_an_id_absent_from_the_corpus_at_the_head_is_named(self):
        self.plant(body="## Proof\n\nP-FIX-1 and P-FIX-NOPE\n")
        reason = self.refusal(self.run_bash("gh pr merge 12 --merge"))
        self.assertIn("P-FIX-NOPE", reason)
        self.assertNotIn("P-FIX-1,", reason)

    def test_a_bare_family_prefix_in_prose_is_not_an_id(self):
        # origin: #278, refused at the flip twice on 2026-09-23 ("found absent:
        # P-278"): prose saying "all seven P-278 rows PASS" was read as a row id.
        # The same shape refused #271 and #274. An id is P-<family>-<leaf>; a bare
        # family, followed by a space, a full stop, an asterisk or the line's end,
        # names no row.
        body = self.GOOD.replace(
            "Rows at the head: P-FIX-1.\n",
            "Rows at the head: P-FIX-1. All the P-FIX rows PASS; P-FIX* are the fixture's.\n"
            "The family is P-FIX.\nP-FIX\n")
        self.plant(body=body)
        self.allowed(self.run_bash("gh pr ready 12"))
        self.allowed(self.run_bash("gh pr merge 12 --merge"))

    def test_a_proof_naming_only_a_bare_family_names_no_row(self):
        # The clean neighbour of the row above: with no full id beside it, a bare
        # family is still no proof.
        self.plant(body="## Proof\n\nall the P-FIX rows PASS\n")
        reason = self.refusal(self.run_bash("gh pr ready 12"))
        self.assertNotIn("found absent", reason)

    def test_an_id_runs_to_its_last_word_character(self):
        # origin: A-539 (record 20260923T044640Z-review-codex-5f987b#1): with no right
        # boundary, "P-FIX-1\u00e9" and "P-FIX-1-" were read as the row P-FIX-1 and the
        # flip passed. The whole token is the id, so each is named absent.
        for token in ("P-FIX-1\u00e9", "P-FIX-1-", "P-FIX\u00e9-1"):
            with self.subTest(token=token):
                self.plant(body=self.GOOD.replace("P-FIX-1.", token + "."))
                reason = self.refusal(self.run_bash("gh pr ready 12"))
                self.assertIn("found absent: " + token, reason)

    def test_an_id_whose_leaf_ends_in_a_hyphen_is_absent_even_as_a_corpus_row(self):
        # origin: A-544 (record 20260923T054308Z-review-codex-ec5e36#2): the corpus
        # readers take any string id, so a row named "P-FIX-1-" made that token
        # present and the flip passed. A leaf ends in a word character; a token
        # that does not is never a row, whatever the corpus carries.
        corpus = self.repo / CORPUS
        for token in ("P-FIX-1-", "P-FIX-1--"):
            with self.subTest(token=token):
                self.world.plant_text(corpus, corpus.read_text(encoding="utf-8") + json.dumps(
                    {"id": token, "kind": "serve", "lane": "git", "question": "q",
                     "serve": "git log -1", "expect": "x"}) + "\n")
                self.world.git("add", "-A")
                self.world.git("commit", "-q", "-m", "fixture: a row id ending in a hyphen")
                self.head = self.world.git("rev-parse", "HEAD").stdout.strip()
                self.plant(body=self.GOOD.replace("P-FIX-1.", token + "."))
                for command in ("gh pr ready 12", "gh pr merge 12 --merge"):
                    reason = self.refusal(self.run_bash(command))
                    self.assertIn("found absent: " + token, reason)
        # Clean neighbour: the full id beside it still passes.
        self.plant(body=self.GOOD)
        self.allowed(self.run_bash("gh pr ready 12"))

    def test_a_head_not_fetched_here_refuses_with_the_fetch(self):
        self.plant(body=self.GOOD, head="0" * 40)
        reason = self.refusal(self.run_bash("gh pr ready 12"))
        self.assertIn("git fetch", reason)

    def test_no_completed_run_on_the_head_refuses(self):
        self.plant(body=self.GOOD, runs=[{"name": "ci", "workflowName": "ci",
                                          "status": "in_progress", "conclusion": None,
                                          "headSha": self.head}])
        reason = self.refusal(self.run_bash("gh pr ready 12"))
        self.assertIn("run", reason)
        self.plant(body=self.GOOD, runs=[])
        self.refusal(self.run_bash("gh pr ready 12"))

    def test_a_failed_run_on_the_head_refuses(self):
        self.plant(body=self.GOOD, runs=[{"name": "ci", "workflowName": "ci",
                                          "status": "completed", "conclusion": "failure",
                                          "headSha": self.head}])
        reason = self.refusal(self.run_bash("gh pr merge 12 --merge"))
        self.assertIn("failure", reason)

    def test_a_run_on_another_sha_does_not_count(self):
        self.plant(body=self.GOOD, runs=[{"name": "ci", "workflowName": "ci",
                                          "status": "completed", "conclusion": "success",
                                          "headSha": "f" * 40}])
        self.refusal(self.run_bash("gh pr ready 12"))

    def test_gh_failing_is_could_not_look_not_a_pass(self):
        self.plant(body=self.GOOD)
        self._plant_gh(fail=True)
        self.refusal(self.run_bash("gh pr ready 12"))

    def test_a_grant_is_no_escape(self):
        self.plant(body="## What\n\nno proof\n")
        self.refusal(self.run_bash("HAND_GRANT=1 gh pr ready 12"))

    def test_other_gh_verbs_are_untouched(self):
        self.plant(body="## What\n\nno proof\n")
        for command in ("gh pr view 12", "gh pr checks 12", "gh pr list",
                        "gh run list --branch dev", "gh pr create --draft --fill"):
            proc = self.run_bash(command)
            self.assertNotIn("pr-proof", proc.stdout, command)
        self.assertNotIn("pr view", self._call_text())

    def test_the_pr_number_may_come_after_flags(self):
        self.plant(body=self.GOOD)
        self.allowed(self.run_bash("gh pr merge --merge --subject 'x' 12"))
        self.plant(body="## What\n\nno proof\n")
        self.refusal(self.run_bash("gh pr merge --merge 12"))

    def test_a_selected_repository_is_read_for_its_proof(self):
        """Codex review of PR #158 (2026-09-12): `-R`/`--repo` selects another
        repository; the proof is read there, never from this repository's
        PR of the same number."""
        self.plant(body=self.GOOD)
        for command, selected in (("gh pr merge 12 --repo owner/other --merge", "owner/other"),
                                  ("gh pr ready -R owner/other 12", "owner/other"),
                                  ("gh pr ready 12 --repo=owner/third", "owner/third")):
            self.world.plant_text(self.world.tools.log_path("gh"), "")
            self.allowed(self.run_bash(command))
            calls = self._call_text().splitlines()
            view = [c for c in calls if c.startswith("pr view 12")]
            runs = [c for c in calls if c.startswith("run list")]
            self.assertEqual(len(view), 1, calls)
            self.assertEqual(len(runs), 1, calls)
            self.assertIn(f"-R {selected}", view[0], command)
            self.assertIn(f"-R {selected}", runs[0], command)

    def test_no_selected_repository_selects_none(self):
        self.plant(body=self.GOOD)
        self.allowed(self.run_bash("gh pr ready 12"))
        self.assertNotIn("-R", self._call_text())



if __name__ == "__main__":
    unittest.main()
