---
name: skills
description: Look up, manage, and meter the project's skills — the SQLite registry (sync from disk, CRUD, and telemetry on uses, failures, misses, and wishes). Invoke to find a skill, record one that failed or got bypassed, or file demand for one that should exist.
---

# skills — the skills registry and its telemetry

The engine is `python3 ops/conductor/tools/skills.py` (SQLite at
`~/.local/state/agentscient/skills.db`). Disk is the source of truth for what
exists; `sync` derives rows from `.claude/skills/*/SKILL.md` and never
deletes — a vanished directory marks its row `missing`, and a wished
skill whose directory appears is promoted to active. Events are
observations, the non-authoritative-mirror rule. Recording an event needs
`WF_AGENT` set, the same identity contract wf enforces; reading does not.

## When invoked

1. Run `sync` first — the table answers only for the tree it last saw.
2. Then do what was asked:
   - **look something up** — `list`, `show NAME`, or `report` (uses,
     fails, misses per skill; wished skills ranked by demand).
   - **add or annotate** — `add NAME DESC --kind=K --status=draft`,
     `note NAME TEXT`, `retire NAME REASON` (reason mandatory; rows
     never vanish).
   - **record telemetry** — the duties below.

## Standing telemetry duties (any session, not only when invoked)

- `log-use NAME [DETAIL]` — a skill was invoked. Since 2026-09-13 the
  PreToolUse hook `ops/devlane/hooks/claude/skill-use.py` records every
  Skill call with its arguments; log by hand only for a use the tool
  did not carry (a page read directly, a skill followed from memory).
- `log-fail NAME DETAIL` — an invocation broke; DETAIL carries the
  error's own words, never a summary of them.
- `log-miss NAME DETAIL` — a failed potential use: work was done another
  way while an active skill covered it. DETAIL says what was done
  instead. A recurring miss is an ergonomics defect in the skill, not in
  the session.
- `wish NAME DETAIL` — the skill does not exist and should. Refused if
  it exists (that is a miss, not a wish).

## Honesty rules

- An event without its story is refused — counts alone are recollections
  wearing claims' clothes.
- `report`'s zero-misses line is a declared zero only while sessions
  actually declare; this skill exists to make declaring cheap.
- The future registry export (REGISTRY.md, drift-gated) derives from
  this table plus disk; neither is ever hand-edited.
