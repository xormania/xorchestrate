---
name: dev-subs
description: Show every background dispatch — what is still running, how long it has run, what it was for, and what it produced. Use when you have lost track of subagents, before starting work that depends on one, or to check whether something died quietly.
---

Run the script and show its output.

```sh
python3 .claude/skills/dev-subs/subs.py
```

Add `--json` for machine-readable output, or `--root <dir>` (repeatable)
to scan a scratchpad other than this session's.

## What it reads

Nothing is remembered. Every field comes from the dispatch snapshots on
disk at the moment the command runs — the brief the role was given, the
launcher's markers, the process table, and the harness's own trace file.
So the answer is current, and a stale one is not possible.

A dispatch snapshot is any directory holding both `prompt.txt` and
`launched`.

## The states, and the one that matters

| state | means |
|:--|:--|
| `running` | the process is alive |
| `finished` | an exit file exists; its code is shown |
| `tripped` | a supervision wire terminated it; the reason is shown |
| **`DIED`** | the process is gone and **no exit was recorded** |
| `unlaunched` | no pid — the launcher probably refused |

**`DIED` is not a kind of finished.** A dispatch whose process vanished
without recording an exit was never observed to complete. Reporting it
as done would turn "nobody looked" into "we checked and it was fine",
which is the failure this lane keeps paying for. If you see `DIED`, the
work is not done and its report does not exist.

`unlaunched` usually means the launcher refused — an isolation module it
could not find, or a harness with no entry in the registry. That is the
fail-closed guard working, not a bug.

## Reading the numbers

The token column is **output only**, deliberately. Totals are dominated
by cache re-reads — measured at 93.4% across twenty dispatches — so a
total tracks how many turns a run took rather than what it produced. A
run with large output is working; a run with a large total may just be
old.

Elapsed for a finished run is measured to its last write, not to now.

## When reporting this to someone

Lead with what is still running and what is blocked on it. A list of
twenty finished dispatches is not the answer to "is anything going". If
something shows `DIED` or `tripped`, say so first — those are the two
that need a decision.

Do not describe a purpose the brief does not state. Where a dispatch's
brief names no role, the script prints that plainly and so should you.
