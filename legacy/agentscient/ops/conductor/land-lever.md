# The land lever — contract

Standing: PROPOSED. Source of demand: the handwork ledger's own
recurrence data — `land=3` declared by-hand landings, plus `amend=4`
in the same family — and the stale-ref dispatch of 2026-08-31, where a
lineage worktree left behind origin landed work onto old law. Landing
is the conductor's write-path: this contract makes it one verb whose
preconditions are checked instead of recalled.

## Conventions

This instrument inherits `ops/conductor/instrument-conventions.md`:
the refusal wire, identity rule, four-class exits, instrument shape,
and lint duty apply as written there. This contract states only what
is land-specific.

## Instrument

`python3 ops/conductor/tools/land.py RECORD_ID [--push] [--no-fetch]
[--author 'Name <address>'] [--co-author 'Name <address>']`
— run inside the lineage worktree, on the lineage branch. Landing
writes commits, so identity is required. subprocess is used only to
run `git`.

The dispatch store is `$XDG_STATE_HOME/agentscient/dispatch` (else
`~/.local/state/agentscient/dispatch`).

## What landing is

The snapshot ref `refs/dispatch/RECORD_ID` holds the producer's
commits on top of the tree it was dispatched from. Landing cherry-picks
every commit in `HEAD..refs/dispatch/RECORD_ID`, in order, onto the
current branch. With no re-authoring flags, messages remain verbatim
(records land verbatim; no `-x` line), each author is `LANE_AUTHOR`,
and the committer is `WF_AGENT`.

`--author` selects the replacement author only for commits whose author is
neither `LANE_AUTHOR` nor the requested author. Commits already authored by
either identity keep their author unchanged.
`--co-author` adds a matching `Co-Authored-By:` trailer when absent from
each landed message, leaving existing `Source:` and other attribution
trailers intact. A matching trailer already present is not duplicated.
The flags are independent and additive; neither changes the committer.

## Preconditions, checked in this order

Each is a refusal (the wire, exit 2), so a hermetic conformance
invocation fails fast at `record`:

| refusal | fires when |
|---|---|
| identity | `WF_AGENT` unset or malformed (inherited, checked first) |
| record | no record directory named RECORD_ID in the dispatch store |
| exit-status | the record's `exit` file is missing or nonzero and the envelope has no verified collection — run `launch.py collect RECORD_ID --verify COMMAND`; a present collection also refuses here when its `verify.exit` is not 0 |
| ref | `refs/dispatch/RECORD_ID` does not resolve here |
| envelope | `.dev/records/dispatches/RECORD_ID.json` is absent from the working tree |
| envelope-status | the envelope's `result.envelope.status` is not `ok` and the result was not collected and verified — run `launch.py collect RECORD_ID --verify COMMAND`; an attempt can exit 0 while the dispatch system rejects the answer (schema-invalid), and the lever lands only accepted or recovered work |
| lineage | the current branch is not the envelope's `lineage.branch` |
| dirty | the working tree has staged or unstaged changes |
| stale | the branch is behind `origin/<branch>` — a three-way ruling. With a remote and the fetch allowed: the upstream branch is probed first (`ls-remote --exit-code`); an upstream that lacks the branch is fresh evidence (a first landing on an unpushed branch is the normal case), a probe or fetch failure refuses — an unperformed freshness check is not freshness. With `--no-fetch`: the last-known `origin/<branch>` ref is the comparison, and its absence refuses — skipping the fetch presumes prior evidence. With no origin remote: a planted origin ref compares, otherwise there is nothing to be behind |
| attribution | a commit to be landed carries no git-parseable trailer, checked before any pick |
| author | `--author` is not in full `Name <address>` form |
| co-author | `--co-author` is not in full `Name <address>` form |

Refusal text is one line by the inherited wire even when git's
diagnostics are not: embedded newlines are normalized before the
refusal is emitted.

A failed dispatch is recoverable through the launcher's `collect` verb.
Landing accepts its `result.collected` evidence only when `verify.exit` is
the JSON integer 0 and `collected.head` is exactly the resolved dispatch ref; a different
head refuses as `ref`. That verified collection satisfies both the
`exit-status` and `envelope-status` preconditions.

## The act and its exits

- Nothing to land: print `landed n=0` and exit 0 — landing is
  idempotent, and idempotence is judged by **patch equivalence**
  (`rev-list --right-only --cherry-pick HEAD...ref`), never by
  ancestry: a landed commit carries a new sha, and a re-run must see
  nothing left.
- Each landed commit prints `LANDED<tab><sha><tab><subject>` — the sha
  is the **landed** commit's, the one now on the branch; then the
  summary `landed n=N ref=refs/dispatch/RECORD_ID`; exit 0.
- `--push` pushes the branch to origin after a successful landing
  (n ≥ 1 only); a push failure is a Finding, exit 1, with the landing
  kept.
- A cherry-pick conflict is an **Abort, exit 3**: the in-progress pick
  is aborted (`git cherry-pick --abort`), the tree is left clean, the
  commits already landed are kept, and the last stdout line — an
  outcome, not a diagnostic — names the conflicted source sha and the
  remainder — a stopped landing leaves commits, never residue.

## Lint

Inherited. The tool shells to `git` — it earns its THIS-FILE-ONLY row
(S603), stated in the PR.
