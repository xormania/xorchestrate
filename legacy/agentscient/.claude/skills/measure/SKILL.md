---
name: measure
description: The command behind each kind of claim - a PR's state, a process alive, a hook wired, a rival branch's behaviour, a package's existence, a record complete, the owner's words. Use before stating any of these to the owner, in a record, or as a diagnosis. Belief substituting for measurement is the lane's most-measured failure class.
---

# measure — a claim is a command's output, stamped

Why it exists, measured: a recommendation between #79 and #83 was made
from #83's description in #79's body and reversed on reading the code;
a hook bypass was asserted that git rejects; a sandbox `status` that
could not see host pids was read as worker death and duplicates were
launched; "the matrix is not wired" was stated after reading the
settings entry and not the wrapper it calls; two packages that do not
exist on Packagist were cited from a survey; three of four corrections
one day were proposals narrated as decisions; a softened verb in the
owner's words cost four files and a supersession (AARs of 2026-09-03,
2026-09-10, 2026-09-11; pitfalls 6, 12, 13); PR #141 was merged on a
checks watch that had returned before the run registered (pitfall 22).

## The table

| the claim | the command, and what counts |
|---|---|
| a PR is open, merged, draft, green | `gh pr view N --json state,isDraft,mergeCommit,headRefOid`; `gh pr checks N`. A PR body's description of anything is a claim, not a reading. Green means every check named in `CHECK_NAMES` of `ops/lane.conf` and every Analyze row reads `pass` on the head sha that will merge. `gh pr checks N --watch` exits 0 with `no checks reported` in the seconds after a push, before the run registers; that exit is not a reading. On 2026-09-11 PR #141 was marked ready and merged on it while the checks on its amended head were in progress (pitfall 22). Wait with `python3 ops/conductor/tools/watch.py pr N`: it polls until every `CHECK_NAMES` check has reported with none pending and the same table on two polls, reads gh's `no checks reported` in the seconds after a push as not yet registered (LD-37), and at `--timeout` (default 1800 s) exits 3 printing its last counts; exit 0 is settled green, 1 settled with a failure. |
| a branch or PR does or does not do X | read its diff: `git diff base...head -- <path>`; run its tests. Never its description, never a rival's description of it. |
| a process is alive or dead | host `ps -o pid,etime,cmd -p PID`. A reading from inside a sandbox, a `status` verb, or a pid file establishes nothing about the host. |
| a hook or guard is wired | trace the chain: `.claude/settings.json` entry, the wrapper script it names, the tool the wrapper calls, then provoke it once and paste the refusal. A settings entry alone is not "wired". |
| a hook bypass exists | provoke it and paste the pass. Git 2.43 rejects abbreviated long options; the bypass claimed on 2026-09-10 did not exist. |
| work is on dev | `git merge-base --is-ancestor <sha> origin/dev`, or `git log origin/dev..HEAD` empty in the worktree. A worktree's log is not dev. |
| a record is complete | its copy on dev: `git show origin/dev:.dev/records/...`. The worktree copy is a draft until pushed and merged. |
| a package or tool exists | the registry answers: `composer show --available VENDOR/NAME`, `pip index versions NAME`, or the registry's own page. A survey names things that do not exist. |
| a number (tests, rows, grants, minutes) | the command that counts it, quoted with the command. `wc -l` on a message is not a count (pitfall 6). Ids are pasted from output, never retyped (pitfall 12). |
| a pid found by pattern | `pgrep -f '^python3 .*<tool>'` anchored, or the `bash -c` wrapper is matched and reported dead when it exits (pitfall 13). |
| the owner said X | the owner's words verbatim, with the date, in quotation marks. Every statement built on them carries its standing: RULED, AGREED, PROPOSED or OPEN. A paraphrase is a different sentence. |
| a diagnosis (why something failed) | the file that carries the failure: `stderr` in the job directory, the CI log line, the traceback. Stated after the paste, never before it. |
| an estimate | the measured rate from the records, with the count it rests on. On 2026-09-11 four happy-path estimates missed four times; the measured rate was one lane defect per ladder stage. |

## The rule

Before writing the claim, run the command, paste its output, then
write the claim as a reading of that output. When the command cannot
be run, the claim is written as "not measured: <why>", which is an
answer, and never as the default that reads like one.

Record the use: `python3 ops/conductor/tools/skills.py log-use measure "<claim measured>"`
with `WF_AGENT` set.
