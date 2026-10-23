---
name: pr-overview
description: A fresh overview of every open pull request in this repository and the one thing each is waiting on. Use when asked what PRs are open, what is blocking a merge, which PR to work on next, or for a status sweep before merging.
---

Run it; do not summarise from memory:

```sh
python3 .claude/skills/pr-overview/pr_overview.py
python3 .claude/skills/pr-overview/pr_overview.py --json      # for scripts
python3 .claude/skills/pr-overview/pr_overview.py --selftest  # the verdicts
```

Present what it printed, with the head SHA it printed beside each PR. A
PR's state moves whenever anyone pushes, reviews, or resolves a thread,
so an overview from earlier in the session is a recollection — say so if
you repeat one instead of re-running.

## What it will not do, and why each refusal exists

**It never reads `statusCheckRollup`.** That field is append-only: a
re-run appends beside the attempt it superseded, so asking whether any
entry failed answers *"did anything ever fail here"* while reading
exactly like *"is it red now"*. It uses `gh pr checks`, which collapses
to the latest attempt per name.

**It reports review AGE, not just presence.** A review is pinned to the
commit it read. `stale` means the head moved after it — which is either
a review to re-request or a review that was *answered*, and the tool
cannot tell those apart. Check the replies before deciding.

**An empty-bodied review is not a verdict.** Answering an inline comment
creates a review record of its own, so a PR whose threads were all
answered accumulates empty reviews authored by whoever answered them.
Taking the latest of those reported one PR here as freshly reviewed by
its own author, turning "the review predates the head" into "ready to
merge".

**No `gh`, an unreadable PR list, or every PR being unreadable exits
non-zero.** It prints nothing rather than an overview it could not read,
and "none open" is printed in words rather than left as silence. A checks
checks or review API error isolated to one PR is shown as that PR's `cannot
tell` row; the other readable rows remain useful. The `gh pr checks` no-checks diagnostic is a
real empty result, not an API failure.

**One passing check is not all of them.** `gh pr checks` lists what has
reported, and a nonempty all-pass list reads exactly like "CI is green"
while the matrix jobs are still registering — one PR here was read as
ready on a single passing check. So what *should* report is read from
a tree rather than from the forge's list of what did -- and not by this
script either. It asks
`ops/devlane/workflow/checks/ci_contexts.py`, the derivation CI's own
suite pins, which expands both workflow files and the registry into
the check-run names GitHub renders (a templated job name included),
and refuses rather than shortening the list. Nothing in the script
lists a name or a count; a job added to a workflow, an app added to
the matrix, or a check flipped to `"ci": true`, is expected here with
no edit. The same list, by hand:

```sh
python3 ops/devlane/workflow/wf.py --json status --checks > checks.json
python3 ops/devlane/workflow/checks/ci_contexts.py checks.json
```

**Each PR is judged against its own head.** The tree asked what should
report is *that pull request's* head commit, fetched from the forge one
archive per head — not the checkout you ran this from, and not whatever
repository your shell happens to be in. One set shared across every row
waits forever for a check a later PR added and never reports one a
later PR removed; running the overview from a branch that adds a check
imposed it on every open PR. A head that cannot be fetched or derived
is `cannot tell`, never a quiet fall back to your checkout's set.

**A skipped check is not a passed one.** `gh pr checks` buckets a run as
pass, fail, pending, skipping or cancel. A skipped check *has* reported,
so comparing names alone counts it present. The per-name buckets are
read too, so a skipped *expected* check is `CI skipped` and says which
name skipped, while a skipped stranger blocks nothing — the two have
identical bucket counts.

## Reading the `>>` line

One line per PR, in priority order: CI red, unresolved threads, CI
pending, CI cancelled, no checks, cannot tell, checks incomplete, CI
skipped, never reviewed, stale review, blocked, ready. It is the first
thing to act on, not a summary of everything true.

Four of those are neither "ready" nor a fault, and they must not be
read as either:

- **`checks incomplete — … not reported`** names the expected checks
  that have not reported. Usually the matrix is still registering;
  if it stays that way, a job did not run.
- **`CI skipped — … expected check(s) skipped: <names>`** names the
  expected checks that reported as skipped. They are not failures and
  not absences; they are checks that did not run, so "all N passed"
  would be false.
- **`cannot tell — …`** means the script could not establish what
  should report (names not collected, the PR's head unfetchable or
  underivable, or a skip with no per-name buckets to attribute it),
  so it does not claim to. It is not "nothing is wrong".
- **`ready to merge — all N expected check(s) passed`** says what it
  verified: every name *that PR's own head* says CI produces has
  reported and passed. Both workflows are unfiltered by policy (a test
  refuses a `paths:` filter), so there is no check that reports only
  sometimes and nothing the sentence leaves uncounted.

Unresolved threads are listed by file. **An absent comment is not an
absent review** — automated reviewers answer inline, on diff lines, and
those threads stay open until someone resolves them.
