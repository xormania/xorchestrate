---
name: tdd
description: The honest red, as commands - collect, fail on an assertion at the base sha, pass at the head with the same command, tests sealed before code, a fix re-audited before merge. Use before any code lands against tests, by hand or when reading a ladder's stages. The law is ops/process/tdd.md; under a control plan, control.py runs it.
---

# tdd — the red is pasted, never inferred

The law is `ops/process/tdd.md`: tests by a separate producer, a fixture
that reaches the behaviour, a RED that fails an assertion, a review by a
third producer, code against sealed tests, a GREEN with the same
command. Under a control plan `control.py` runs that sequence and this
page is not needed. This page is what a session does when there is no
plan: a hand repair, a ladder record it is reading, a worker's landed
commit it is about to push.

Why it exists, measured: on 2026-09-10 early failures were taken as RED
without checking the assertions (lost-work AAR); a shadowed unittest
method's crash was taken as RED, and CHANGES was treated as approval
(manual-enforcement AAR); on 2026-09-11 a frozen assertion its own
fixture made impossible passed three stages (pitfall 19), and a fix
commit merged three minutes after landing with its re-audit never run,
carrying a hole the audit had just described (7b7979b, fixed in #133).
The findings ledger holds 169 check-tests findings, 46 of them P1: the
first draft of a test set is weak, every time.

## The steps, each with its output pasted

1. **Tests first, by someone else.** The test commit precedes the code
   commit and its trailer names a producer other than the implementer:

   ```sh
   git log --format='%h %ad %s | %(trailers:key=Co-Authored-By,valueonly)' --date=short -- <test file>
   ```

2. **Collect.** Zero tests, an import error or a skip is not a red:

   ```sh
   python3 -m unittest discover -s <tests dir> -p '<test_file.py>' -v 2>&1 | tail -3
   ```

   Paste the `Ran N tests` line; N is above zero.

3. **RED at the base.** A scratch worktree at the base sha, only the
   test files copied in, the same discover command:

   ```sh
   git worktree add <scratch> <base sha>
   git show <head>:<test path> > <scratch>/<test path>
   python3 -m unittest discover -s <scratch>/<tests dir> -p '<test_file.py>' 2>&1 | awk '/^FAIL:|^ERROR:|AssertionError/'
   ```

   Paste those lines. A `FAIL:` with an `AssertionError` is a red. An
   `ERROR:` is a fixture or import defect and is not a red; it goes back
   to the test author.

4. **Satisfiable.** For each failing test, one line: what the fixture
   creates, what the assertion forbids, and that they are not the same
   thing. Pitfall 19 is the fixture planting the directory the assertion
   forbids; nothing before the coder can see it, so this line is the
   only place it gets caught.

5. **Code, tests untouched.** Before pushing:

   ```sh
   git diff <tests commit> HEAD -- <test files>
   ```

   Empty. A test edited to reach green is a new tests commit by the test
   author, reviewed again, not a fix.

6. **GREEN at the head.** The same discover arguments as step 3; paste
   `Ran N tests` and `OK`. Then `ruff check <changed files>` (pitfall 21:
   a worker's named command runs tests, not ruff, and CI runs both).

7. **Review before merge.** Two reviewers; a CHANGES verdict is a stop,
   ruled on by a producer who wrote neither the code nor the finding.
   A fix commit gets its own re-audit, and the merge waits for it. The
   ladder's `security-re` and `pr` stages are that wait; a PR opened by
   hand does not skip them.

8. **In the commit.** The red lines from step 3 and the green line from
   step 6, with the base sha, so the commit carries its own evidence.

## What this page never accepts

- A non-zero exit as a red. Only a pasted assertion failure is one.
- "The suite is green" from memory, from a worker's note, or from a
  previous run. Exit 0 is evidence, never authority.
- A merge on an open CHANGES, or a fix merged before its re-audit.
- Tests and code from the same producer. Where the owner orders a hand
  fix, the commit and the record say the second producer is absent.

Record the use: `python3 ops/conductor/tools/skills.py log-use tdd "<what was measured>"`
with `WF_AGENT` set.
