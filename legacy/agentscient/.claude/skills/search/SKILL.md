---
name: search
description: The one allowed way to look for something in this repository - a count first, then a bounded view, one pattern per call, file lists and counts for verdicts, Graft for navigating code. Use before any grep, rg or git grep, and the moment graft-first or clipped-sweep refuses.
---

# search — count first, then look

Why it exists, measured (`skills.py report`, 2026-09-13): 156 guard
refusals were logged as misses in three days, 21, 55 and 94 per day;
graft-first was 45 of them and clipped-sweep 18, together 40 percent.
Thirty-one of the graft-first refusals were `grep` over a directory.
Each refusal cost a whole-context turn and a retyped command. The
`refused` page says what the guard wants after it fires; this page is
the shape that keeps it quiet.

## The four questions and their one command each

| the question | the command | why this shape |
|---|---|---|
| does it appear at all, how often | `grep -c PATTERN FILE` (one file) or `grep -rl PATTERN DIR` (a file list) | counts and file lists are never searches to the guard, and they cannot truncate |
| where in one file | `grep -n PATTERN FILE`, then `sed -n 'A,Bp' FILE` | one named file is a read, not navigation |
| where in the tree, in data or records | `grep -rn PATTERN DIR --include='*.md'` or any `.json`, `.jsonl`, `.yaml`, `.conf`, `.txt`, `.log` target | data Graft never indexed is not navigation; the guard allows it |
| where in the tree, in code | the Graft query the refusal prints: `python3 ops/devlane/harness/graft.py query --context <ctx> graft_find_code '{"query": "...", "limit": 20}'`; `graft-stale` means `graft.py refresh --context <ctx>` first | code navigation goes through Graft by the owner's word (2026-09-11: "go - force it") |

## The rules that stop the two refusals

1. **One pattern per call.** Never `a\|b\|c` or `-E 'a|b'` into a
   pipe: `evgrep.sh` is basic-regex grep and reads `\|` as a literal
   bar (pitfall 28), and an alternation piped to `head` is the
   clipped-sweep shape. Three counts beat one alternation.
2. **Count before you look.** `grep -c` is the verdict; the view is
   bounded and says so: `ops/devlane/hooks/claude/evgrep.sh PATTERN
   FILE` prints the full count first and a bounded display after.
   Never `| head` on a completeness question.
3. **A `-r` needs `-l` or `-c`, or a data target.** `grep -rn` over
   `ops/` for a code token is the refusal. Get the file list with
   `-rl`, then `grep -n` each file, or ask Graft.
4. **Combined short flags spell one thing.** `-rl` and `-rnc` read as
   a file list or a count since #179; `-rn` does not.
5. **Absence is a count of zero from a pattern that ran.** A zero from
   a basic-regex alternation, a `2>/dev/null` that hid an error, or a
   directory that does not exist is not an absence; check the exit
   code and the pattern before writing "none".

## Worked shapes from the refusal log

- `grep -rn 'ladder\.py|import ladder' ops` refused; the shape is
  `grep -rl -F 'ladder.py' ops` then `grep -n -F 'ladder.py' <file>`.
- `grep -n -E 'a|b|c' file | head -8` refused; the shape is `grep -c
  -E 'a|b|c' file`, then the full output or `evgrep.sh`.
- `grep -rn '\.line\([^)]' ops/ .claude/` refused; the shape is
  `grep -rl -E '\.line\([^)]' ops .claude`, then per file.

Record the use: `python3 ops/conductor/tools/skills.py log-use search "<what was looked for>"`
with `WF_AGENT` set.
