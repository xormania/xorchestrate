---
name: claim
description: How to word a commit message, PR body, ready note, comment or record so every claim carries its reading - a sha, a record id, a P- row, a PR number, a run id, Ran N tests, or the words not measured, in the same paragraph. Use before writing any of those, and the moment claim-unmeasured refuses.
---

# claim — the reading sits beside the word

Why it exists, measured (`skills.py report`, 2026-09-13): 18
claim-unmeasured refusals in three days, on commit messages, PR bodies
and records, each followed by a rewrite and a second call. The row is
hard for agents by the owner's word (2026-09-12: "you have to enforce
it before you state it as fact - with evidence"); no grant lifts it.

## The rule in one line

Every paragraph that says enforced, done, landed, merged, green,
impossible, verified, fixed, complete, passes, or any word of that
family, carries in the same paragraph one of: a commit sha, a dispatch
record id, a `P-` corpus row id, `#<PR>`, a CI run id, `Ran N tests`,
or the words `not measured`.

## Shapes that pass

| the claim | the shape |
|---|---|
| tests are green | `Ran 34 OK at dee2b4c9`, the command named once above |
| the PR merged | `#187 merged at 48639a71` |
| the change works | `probes.py run at 2e7dec62: pass 145, fail 0, unreachable 0` |
| a row is fixed | `LD-28 closed in #187 (48639a71)` |
| something was not checked | `tokens: not measured this session` |
| the owner's word | quote it with the date; quoted words are not your claim |

## Shapes that refuse, and their fix

- "The fixtures are green now." → "fixtures Ran 132 OK (skipped=6) at
  `a9b79257`."
- "Merged and done." in a commit message → name the merge sha and the
  PR, or leave the verb out of the message.
- A reviewer note in a PR body that reads as a claim ("this is
  verified by CI") → "CI on this head: see the ready note" or the run
  id.
- A record that quotes a past sentence with "merged" in it → keep the
  quotation marks and the date on the quote; the row skips quoted
  words.

## Where the row reads

Commit messages (`-m` and `-F` files), `gh pr create` and `gh pr edit`
bodies, `gh pr comment` and ready notes, records under
`ops/conductor/`, and law pages. It reads paragraph by paragraph; a
reading in the paragraph above does not cover the one below. An
"edit the file, then `gh pr create`" chain is judged on the file
before the edit: two calls.

Record the use: `python3 ops/conductor/tools/skills.py log-use claim "<what was written>"`
with `WF_AGENT` set.
