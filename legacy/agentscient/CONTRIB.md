# CONTRIB.md — conventions for agents working together

This file owns naming conventions and the templates for documenting changes
and PRs. Where it disagrees with the seed conventions in `AGENTS.md`, this
file wins (that is `AGENTS.md`'s own rule). Attribution and sourcing rules
live in `AGENTS.md` §Attribution and are not restated here.

Authorship of this file is itself multi-agent; each section names its
authors in the commit that introduced it.

## Naming

- **Branches**: `<area>/<short-slug>` — `<area>` is the surface touched
  (`workflow`, `contracts`, a crate name, …) or `repo` for anything else.
  Cut them from `dev` and target `dev`; `main` is the owner's (`AGENTS.md`
  §Git workflow). Once the routing contract lands
  (`ops/conductor/routing.md`), new branches use its base-led form —
  `dev/<project>/<wo>/<slug>` — and existing names are grandfathered,
  never renamed.
- **Commits**: `<area>: <imperative summary>`. The body states the decision
  and its reason, never the story of arriving at it. Trailers per
  `AGENTS.md` (`Source:`, `Co-Authored-By:`, `Reviewed-by:`).
- **Work orders**: form is owner decision D6 (`ops/devlane/workflow/PLAN.md`);
  until decided, `WO-NNN` in dev-lane surfaces.
- **PR titles**: same `<area>: <summary>` form as the headline commit.

## Documenting a change

Every PR body answers four questions, in this order, each under its own
heading:

1. **What** — the change in two or three sentences a triaging owner can act
   on without reading further.
2. **Sources** — where the content came from, matching the commits'
   `Source:` trailers.
3. **Evidence** — every factual claim produced by a command at the moment
   of writing, stamped with the SHA it was measured against. A number
   without a command is a recollection and must say so.
4. **For reviewers** — the substantive questions this change actually
   raises; where to aim skepticism.

## Review protocol (multi-agent)

Reviews from any agent use one wire format so stamps aggregate:

```
VERDICT: APPROVE | CHANGES
STAMP: <specific model name>
FINDINGS:
- [P1|P2|P3] <file> §<section>: <finding>
(or "- none")
```

- A review is pinned to the commit it read; any push voids it and the
  re-request must be explicit.
- Findings are applied by an agent, never pushed by the reviewer; the fix
  commit credits the finder (`Co-Authored-By`) and the reviewer
  (`Reviewed-by`), which is how reviewers appear in a file's attribution
  honestly.
- A finding is verified against the live tree before it is applied; the
  verification command belongs in the fix commit's body.
- Denial is never an error: a CHANGES verdict recorded and addressed is the
  process working.

A finding is fixed only when it shows a concrete failure on the exit
path — a wrong result, a crash, or a bypassed refusal, on an invocation
someone will actually make. Every other finding is `ruled`, never
silently dropped: `findings.py disposition <key> ruled '<why>'`, `<why>`
beginning `declined: not practical — <judgement>`, the judgement itself
a reading produced by a command, never dictated. `practical: no` is
declined unless the XO overrides it with the failing invocation written
down. Owner, relayed by the Lead (agentscient-d8), 2026-09-23: "all
findings aren't requirements - practical issues, not theorietical are
important."; "i've said that many times over many weeks - findings are
just suggestions - judgement is needed"; "get moving - and make sure i
NEVER have to say that again." At `gh pr ready`, `matrix.py`'s
`finding_dispositions()` enforces the disposition half mechanically:
every finding of the closed audit is named in `## Proof` with a
disposition token equal to the findings export's value at the head and
its judgement line, and a `practical`-labeled ruled finding's judgement
must begin "declined: not practical — "; no grant escape (D-FINDINGS-1).

## Draft first

A pull request opens as a **draft** and is marked ready only when its
lineage is stationary — stages done, suites green, no push queued.
External reviewers trigger on *open* and on *ready*: a PR that opens
ready invites review of a moving target, and a verdict pinned to a
mid-flight commit answers a question nobody can use (measured
2026-09-01: two pushes landed under an in-flight review on PR #113
and the reviewed commit was unknowable until the report named it).
Marking ready is the deliberate signal that the target holds still; a
push after ready voids reviews and demands the explicit re-request
(§Review protocol). Owner rule, D-OPS-14.

Ready is earned, not judged (owner rule, D-OPS-15). Every requirement
below holds at the moment of the flip, each proved by a command run
then — and the flip is recorded in a **ready note** on the PR naming
the head sha and the proof, so ready is auditable before any gate
automates it:

| requirement | proved by, at ready time |
|:--|:--|
| lineage stationary | nothing queued for this branch; the head sha named in the note |
| full check set present and green | `python3 ops/conductor/tools/watch.py pr <n>` exits 0 — the complete expected set (`CHECK_NAMES`), zero pending, zero red, the same table on two polls (the merge rule, applied early) |
| suites green on the merged tree | the touched zone's suite run on a merge of the head with `origin/dev`, not the branch tip |
| body per template | the four headings filled; every evidence claim produced by a command at write time, stamped with its sha |
| trailers on every commit | `git log --format='%h %(trailers:only)' origin/dev..HEAD` — `Source:` and `Co-Authored-By:` on each |
| zone floor named and run | the touched zone's regression floor (coverage-zones.md), cited in the note |
| security audit closed at the head | the `## Proof` section names a closed `security-audit` record on the codex harness whose `model.ran` equals the value `MODEL_CODEX_SECURITY_AUDIT` carries in `ops/lane.conf` at check time (`gpt-daybreak-blue-latest` on 2026-09-16), whose head, read from the record itself (`snapshot.ref_sha` and `result.head`, which must agree; no record carries a `stamp.ref`, measured on 20260916T094142Z-review-codex-995043), is the head sha or an ancestor of it, and every finding of that record is disposed in the findings ledger (`findings.py`), its p1 and p2 rows by fix commits. One audit per PR (owner, 2026-09-16: "you can't keep re-auditing after fixes - draw a line"): it is dispatched at the head after the adversarial fix round, and after its sha every commit is either in the records set or a fix commit in the Fix commit shape below whose `Finding:` line names a key of that same record (`<record id>#<n>`, the findings ledger key); the first commit that is neither voids the audit and is named in the refusal. Only the branch's own commits are judged: the gate walks `<audited sha>..<head> --not origin/<trunk>`, so a commit that reached the branch through a forward merge of the trunk never voids the audit (the Lead, 2026-09-16, measured on #242: the dispatcher merges the trunk forward before every launch, and the rule as written voided every branch that had dispatched anything). Nothing else is dropped. Skipping merge commits skipped every merge rather than the trunk's, so a branch could merge a topic carrying unaudited code and keep its audit (20260916T122524Z-review-codex-05a9f5#5, the Lead 12:43Z); the merges are judged here too, by their combined diff, because what a merge resolved differently from every parent is content no parent carried. The audited sha is a commit id, 7 to 40 hex, before it reaches git, which reads a value beginning with a dash as an option (#4 of the same audit). A fix commit's exemption reaches a commit at least one of whose touched paths the cited finding's `where` names — one path, not every path, because an honest fix commit carries its test file and its record beside the code (#8 of the same audit). The records set is data on the row, matched as path prefixes: `.dev/records/`, `ops/conductor/handoff/`, `ops/conductor/findings/findings.jsonl`, `ops/conductor/AAR-`, `ops/conductor/ideas/` and `ops/conductor/lessons/` — the last three added 2026-09-16 at agentscient-11's word, because a session's close writes an AAR, an idea and a lesson, and a close is not a change to the tree the audit read, read with `git diff --name-only <audited sha>..<head>`; a record landing does not void it (the dispatcher lands the audit's own record on the branch at close: c9361ff7 after the audit at 7f26d42b); a commit touching the audit record's own file after its landing voids it (20260916T102232Z-review-codex-4e6efc#2, upheld by agentscient-11 10:38Z). A record whose two head fields, or whose `model.requested` and `model.ran`, disagree is refused, never read as met. Rulings: agentscient-11, 2026-09-16 09:55Z (an ancestor with record landings after it), 10:24Z (the set of three, the model read at check time, the head from a real record) and 10:32Z (the owner's line: one audit per PR, a fix commit citing the audit's own rows does not void it). Owner, 2026-09-16: "i'd like if you have daybreak security audits made required for draft pr's - and resolution required before they can be made ready for review". By hand until the `pr-proof` row carries it (wall item W3 in agentscient-11's queue, built to this sentence with the set as data). At `gh pr ready` the `finding-disposition` row judges that audit's findings in `## Proof`: each is named `<record>#<n>` or `#<n>`, followed by a disposition token of `findings.py` `DISPOSITIONS` other than `open`, equal to the findings export's value at the head, and then one line of judgement. A finding labelled `practical` and ruled has a judgement beginning `declined: not practical — `. Owner, 2026-09-23, relayed by the Lead: "get moving - and make sure i NEVER have to say that again" |

**Ready means mergeable now** (owner, 2026-09-13: "a draft PR should never
be marked for review unless it can be merged immediately"). The flip is
the merge's own gate applied early: the `## Proof` section naming corpus
rows that pass at the head, merge state clean against the trunk, the
complete check set green on that head, nothing queued. A PR whose merge
would refuse on any row stays a draft. Measured the day of the ruling:
PR #175 was flipped on green checks alone and the merge refused
`pr-proof` an hour later, costing a push, a CI round and a second ready
note (LD-32).

## Templates

A review starts at the body, not the diff. If a heading is missing,
misnamed, or filled with history, that is the first finding. Copy, fill,
strip the comments.

### PR body

```
## What
<!-- Two or three sentences a triaging owner can act on without reading
     further. A file catalogue is not this; counts belong in Evidence.
     If the reviewer cannot quote a decision from here, rewrite. -->

## Sources
<!-- Same values as the commits' Source: trailers. A source in one
     place and not the other is a finding. -->
- original
- <repo>@<sha>

## Evidence
<!-- Every factual claim, produced by a command at write-time, stamped
     with the SHA it was measured against. The reviewer re-runs this;
     a number with no command is a recollection and must say so. -->
- <claim> — `<command>` at `<sha>`

## For reviewers
<!-- The questions this change actually raises — where to aim
     skepticism. Not a file list, not "please check trailers". Name
     the decision and the section it lives in. -->
```

### Fix commit

Verify the finding against the live tree; a finding that does not
reproduce is not applied. The fixer commits; the reviewer does not push.

```
<area>: <imperative summary>

Finding: [P#] <file> §<section>: <one-line finding>
Verified: `<command>` at <sha>

Source: original
Co-Authored-By: <finder model> <vendor-noreply>
Co-Authored-By: <fixer model> <vendor-noreply>
Reviewed-by: <reviewer model> <vendor-noreply>
```

Finder and reviewer are often the same stamp; both trailers still land.
Drop the extra `Co-Authored-By` when finder and fixer are the same.
Several findings: several `Finding:`/`Verified:` pairs, one commit.

<!-- SECTION RESERVED: Codex — findings from its PR #1 review are applied
     here and below; its conventions contribution lands with them. -->

## One checkout per line of work

The repo is worked by several actors at once — harness sessions, background
reviewers, the owner — and a `git checkout` in a shared working tree yanks
HEAD from under every other session using it. Measured on 2026-08-21: 33
branch switches in one tree in a day, and a session that opened a PR found
itself on a different branch five minutes later.

- **One line of work, one worktree.** Before starting on a branch:
  `git worktree add ../wt/<branch-slug> <branch>` (create with
  `-b <branch> ... origin/dev` for new work). Work there; never switch
  branches in a checkout you did not create.
- **A worktree add on an existing branch is a lock grab.** It succeeds
  only while nobody has that branch checked out — if someone does, the
  add itself fails ("already used by worktree") — and from then on git
  refuses the branch to every other tree. Nobody is evicted; they are
  locked out at their NEXT checkout, which is worse: no error reaches
  the actor who was driving the lane. Create worktrees for branches you
  own; for anyone else's, coordinate first. Measured 2026-08-21: an add
  landed while the branch's author was briefly elsewhere, the author's
  return checkout failed on the lock 56 seconds later, and two
  sessions' agent loops collided in one tree.
- **A worktree has one owner: its creator.** Do not edit, stage, or
  commit in a worktree you did not create — files appearing there are
  another actor's live work, not artifacts free to adopt.
- **The main checkout belongs to nobody.** Treat it as read-only reference;
  do not commit or switch branches in it.
- **Every worktree shares one clone** — one object store, one branch
  namespace, one context stream — so `git branch -vv` and the stream see
  everything regardless of which worktree recorded it.
- **Recording is git-native.** `ops/devlane/hooks/install.sh` installs
  `post-checkout`/`post-commit` hooks into the clone's shared hooks dir;
  they append who moved what to the context stream for every actor, not
  only Claude sessions. Set `WF_AGENT` so the record names you.
