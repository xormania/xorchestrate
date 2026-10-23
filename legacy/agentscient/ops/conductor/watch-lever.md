# The watch lever — contract

Standing: PROPOSED. Source of demand: the conducting sessions hand-rolled
`sleep`-and-`gh pr checks` loops at least five times on 2026-08-31 alone,
and one of them misread "no checks reported" as settled (the #91 watcher
defect). Seed-learning 10 pins the fix: **instruments carry their reading
as code** — settled ⇔ rows > 0 ∧ pending == 0. This tool is that reading,
executable.

## Instrument

`python3 ops/conductor/tools/watch.py pr NUMBER [options]`, stdlib only
(argparse, json, subprocess, time). It shells to the `gh` CLI found on
PATH — and only to `gh` — so tests may stub it with a fake executable.

Test apparatus: `WATCH_TICK_SCALE`, when set, scales requested poll sleeps
while counting their declared logical interval. Subprocess startup and other
work consume actual elapsed time once; they are never divided by the scale.
This keeps the interval floor on the wire without amplifying host scheduling
delays. Unset means real time. The suite sets it to 0.02; nothing else does.

## The pinned reading

A pull request's checks are **settled** when the check table has at
least one row, no row is pending, **and the same table has been
observed on two consecutive polls**. Zero rows is NOT settled — it
means *not yet reported* and the watch continues. gh reports that table
as an error, not as an empty array: in the seconds after a push
`gh pr checks N --json name,state` prints `no checks reported on the
'<branch>' branch` on stderr and exits 1 (gh 2.99.0; the error returns
before the JSON write). The watch reads exactly that exit with that text
as zero rows (LD-37). Registration is not
atomic either: one fast check can report before the rest of the table
exists, so a settled-looking observation is a candidate, confirmed only
when the next poll reports the identical table (Codex security review
of PR #102). A skipped row counts as a row, and neither passes nor
fails.

Settling also requires every expected check to have reported. The
expected set is `--expect-names` when given, otherwise `CHECK_NAMES`
through the shared conf reader (the environment's value before the lane
conf's); with neither, the watch refuses `conf-key`.

## Verb

`pr NUMBER` — poll `gh pr checks NUMBER --json name,state` every
`--interval` seconds (default 30) until settled or the wall.

- `--repo OWNER/NAME` — passed through to gh when given.
- `--interval S` — seconds between polls, default 30, minimum 5.
- `--timeout S` — the wall, default 1800.
- `--expect-min N` — refuse to settle while rows < N (default 1); a
  repo whose required checks are known can demand them all.
- `--expect-names LIST` — comma-separated check names that must all
  have reported before settling; they replace `CHECK_NAMES` (see the
  pinned reading). A name never reported keeps the watch polling and
  the wall bounds it.

While polling, print one status line per poll to stderr:
`poll rows=R pass=P fail=F pending=D skip=S elapsed=Es` (with
` missing=K` appended while `--expect-names` entries are unreported,
and ` confirming` on a candidate awaiting its confirmation poll). On
settling, print to stdout one line per check — `PASS|FAIL|SKIP<tab>name`
— then the summary line `settled rows=R pass=P fail=F skip=S`.

## States

gh's `state` values map: SUCCESS → pass; FAILURE, ERROR, CANCELLED,
TIMED_OUT, ACTION_REQUIRED, STARTUP_FAILURE → fail; SKIPPED, NEUTRAL →
skip; QUEUED, PENDING, IN_PROGRESS, WAITING, REQUESTED, EXPECTED and
anything unrecognized → pending. Unrecognized-as-pending is deliberate:
an unknown state must never settle a watch.

## Exit codes — the four classes on the wire

| exit | class | when |
|---|---|---|
| 0 | — | settled, every row pass or skip |
| 1 | Finding | settled, at least one row fail |
| 2 | Rejection | refusal: bad arguments, gh missing, gh errors |
| 3 | Abort | the wall passed before the checks settled |

An Abort prints the last observed counts before exiting — a killed
watch must leave its reading, not residue.

## Refusals

One line per refusal — `class: expected …; found …; needed …` — exit 2
on stderr, nothing on stdout (the register contract's shape, which is
the lane's refusal wire):

| refusal | fires when |
|---|---|
| number | NUMBER is not a positive integer |
| interval | `--interval` under 5 seconds |
| tick-scale | `WATCH_TICK_SCALE` is set and is not a positive number |
| gh-missing | `gh` is not on PATH |
| arguments | the arguments do not parse, or `--expect-min` is under 1 |
| conf-key | no `--expect-names` and no `CHECK_NAMES` to read |
| gh-error | gh exits nonzero, other than exit 1 with `no checks reported on the` (its stderr is quoted in found) |
| json | gh's output is not the JSON shape asked for |

## Identity

Watching is a read; no `WF_AGENT` is required.

## Lint

Repo `ruff.toml` governs. The tool shells to `gh` (S603/S607) — it
earns the same per-file row shape its siblings carry, stated in the PR.
