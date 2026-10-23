---
name: explain
description: How anything is explained to the owner - a status, a summary, a finding, a choice. Background in a few lines, then the specifics named so the owner can understand and decide, with suggestions, then the proof that every claim was measured. Use before writing any reply that explains, reports or asks for a decision.
---

# explain — background, the specifics, the proof

Why it exists, in the owner's words (2026-09-13): "the way you explain
something - sucks. What I need is a brief summary of the overall -
background info - then the specifics named in such a way that I can
both understand and have enough info to make a decision (you're
suggestions are welcome) - ending in something that proves you actually
verified everything". Measured that day: the three-tool summary gave
each tool's behaviour in plain words and no caller count, no cost, no
recommendation, and no reading the owner could check; the owner had to
ask twice.

## The shape

1. **Background, three to six lines.** What the thing is, why it is in
   front of the owner now, and what the owner is being asked, if
   anything. No history of how the session got here; no mechanism
   before the point.

2. **The specifics, one block per item**, each with the same fields in
   the same order so the blocks compare:
   - *what it is*: one sentence in plain words, the owner's register,
     not the lane's.
   - *who depends on it*: the live callers, counted by a command, with
     records and tests excluded from the count and said so.
   - *what changes*: what a caller or the owner sees differently, and
     what stays the same.
   - *cost*: size in lines or files, tests that must move, a measured
     rate from the records where one exists, never a happy path.
   - *recommendation*: do, defer, or drop, and the one reason.
   A status report uses the same blocks with *what changes* read as
   "what happened" and *recommendation* as "what is next".

3. **The proof, last.** One line per claim above that came from a
   command: the command and its reading, stamped with the sha or the
   time. A claim with no line here is a recollection and is labelled
   one. Absence is declared: "looked and found none" and "could not
   look" are different lines.

## What it never does

- Explains the mechanism before the decision or the outcome.
- Gives a recommendation without a reason, or a reason without a reading.
- Puts a number in prose that has no command beside it in the proof.
- Uses a term invented in the session; the `terms` skill exists for
  words whose authority is in doubt.
- Pads: if the owner can decide from the background alone, the
  specifics are one block and the proof is one line.

Record the use: `python3 ops/conductor/tools/skills.py log-use explain "<what was explained>"`
with `WF_AGENT` set.
