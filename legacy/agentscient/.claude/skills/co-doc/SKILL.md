---
name: co-doc
description: Diagnose conductor permission lockouts — probe hooks, permission rules, levers, launcher, identity and ledgers read-only, then walk the owner through remedies one decision at a time. The owner-invoked bypass for a wedged conductor; applies nothing unattended.
---

# co-doc — the conductor's permission doctor

The contract is `ops/conductor/permission-surface.md`; the instrument is
`python3 ops/conductor/tools/co_doc.py`. The doctor depends on nothing it
diagnoses — stdlib reads only — so it runs even when everything else is
wedged. It proposes; it does not fix.

## When invoked

1. Run `python3 ops/conductor/tools/co_doc.py` and present its findings
   **verbatim** — each line already carries the class, what was expected,
   what was found, and the proposed remedy.
2. Exit 0: say so and stop — a clean surface is the result, not a
   preamble. Exit 2 means a probe could not examine its layer: report
   which, as INVALID, never as clean.
3. For findings, walk the owner through remedies **one decision per
   message**: plain-language tl;dr, the tradeoff, a recommendation, and
   the consequence of each answer (the owner's stated format).
4. Apply a remedy only after the owner confirms it in-session, then
   re-run the doctor to prove the finding closed — a fix without a
   re-probe is a claim, not a repair. Log each applied remedy to the
   handwork ledger.
5. Never apply a remedy that widens any permission, even confirmed —
   widening is the owner's own edit, guided, not performed.

## Drift duty

`co_doc.py --list-probes` must name exactly the probe ids in the
contract's table. On any mismatch, the mismatch is the first finding
reported, before the probes run.

## Why this exists

Measured lockouts: F9's classifier denied a pure read with nobody to
approve; the tool-guard refused 21 legitimate tools after six green
skeptic passes; an allow rule shipped inert because its target path did
not resolve. The only channel automation cannot wedge is the owner,
present and interactive — this skill makes that bypass cost one command
and produce evidence.
