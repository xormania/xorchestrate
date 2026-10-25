# Role entry points, the brief and the acceptance gate — contract

Standing: proposed, package E of the agent-coordination objective, at
the owner's word relayed by the Lead session (owner, 2026-09-16: "hand
grant ok'd" at the session's start, and today "let's get the PRs green
and merged" and "get it done", relayed by agentscient-11). Source of
demand: the objective's §10, role guidance and entry points; §11,
completion and decision evidence; and acceptance criteria AC-10, AC-11
and AC-12. Package E is built on package B's core
(`coordination-lever.md`), whose ladder it does not re-implement.

Measured on `lane/session-20260916-j` at `fc8b801a`, before a line of
this page's code was written:

- AC-11 is met by the core for the two checks it names:
  `not_the_producer` (coordination.py:417) refuses a producer verifying
  or accepting its own candidate, `verify` requires role verifier or
  lead, `accept` requires lead, and `submitted_by`, `verified_by` and
  `accepted_by` are columns of `assignments`. What is absent is a
  reading that puts the three side by side where a Lead decides.
- AC-10 is not enforced anywhere: `core_accept` checks the role, the
  generation and the producer, and nothing else. An assignment whose
  required behaviour was never examined, and one whose evidence went
  stale when the candidate changed, both accept without a word.
- AC-12 is half met: `core_brief` returns held assignments, the
  unresolved inbox addressed to the participant, a count of open
  decisions and a count of stale observations. It carries no live
  executions, no requests in either direction, no decision rows and no
  applicability.

## Conventions

Inherits `ops/conductor/instrument-conventions.md` for the refusal
wire, the exit classes and the identity rule, and
`ops/conductor/coordination-lever.md` for the core's seam: every verb is
a function in `CORE`, keyed by its wire name, taking the resolved
participant and raising `instrument.Refusal`. Every check this page
adds lives in those functions, never in a projection and never in a
hook, because the hook engine cannot see an MCP call and a check that
cannot run on both projections is not a check.

## The entry points

`entry [--role ROLE]` answers one question: what does a session of this
role do first, here, now. It is a read: no identity, no store created,
and it never refuses because the store is absent — a participant that
cannot bind yet is exactly the reader this verb exists for.

Its answer has three parts, and each part is a reading and not a
recital:

- **the role's duties**, one line each, from the table below;
- **the state this session is in**: bound or not, its participant id
  and roles when it is, and the count of assignments, requests and
  decisions that are its own;
- **the next steps**, the verbs to run in order, each with the argument
  the reader must supply.

Roles and their first duties:

| role | the entry point says |
|---|---|
| lead | read `coverage`, decide from `decision DC-n` with its evidence, accept only what the gate below allows, and record any exception in the acceptance |
| coordinator | read `assignments --open` and `resources`, place work with `offer` and `reassign`, and clear a blocked holder before a dependency becomes a stall |
| dispatcher | read `outstanding`, launch against a held assignment, and reconcile a launch whose outcome is uncertain rather than launching again |
| worker | read `brief`, claim what is offered, `observe` what the work measures, `submit` a candidate with its revision, and `ask` rather than guess |
| verifier | read `coverage --requirement REF`, examine the candidate against the requirement's own words, `observe` the finding, and `verify` or `return` it |

A session with several roles reads several blocks, in the order of the
table, and the state and next steps are computed once.

## The brief

`brief` is what a restarted or compacted participant reads to recover
its obligations (AC-12). It carries five readings and nothing else:

1. **assignments** — every assignment this participant holds, with its
   place, generation and applicable revision.
2. **executions** — the live dispatch records for those assignments,
   read from the dispatch store by the assignment id the record's
   lineage carries. The store is another instrument's, so its absence
   is declared and never inferred: `{"executions": null, "store":
   "absent"}` when there is no store to read, an empty list when there
   is one and it holds nothing for this participant.
3. **requests** — both directions, never one: the messages addressed to
   this participant that are still open, and the messages it sent that
   are open, each with the purpose that says what is owed. A request
   acknowledged but not resolved is still owed, and the two stamps stay
   separate.
4. **decisions** — the open decision rows themselves, with the question
   and who asked, not a count; a count is not something a restarted
   session can act on.
5. **evidence** — the applicability of this participant's observations,
   current and stale, with the stale ones naming the reason and the
   revision they were taken against.

A reader of the brief can act without reading a transcript. That is the
whole measure of this verb.

## The acceptance gate

AC-10: missing requirements and stale evidence prevent an unsupported
acceptance. `accept` therefore refuses, after the role and producer
checks it already makes and before the move, when either holds. The
order is authority first: a participant that may not accept at all
learns that, and not what the evidence lacks.

- **no current examination.** The assignment names a requirement and no
  observation of the applicable revision is `current` for it. Class
  `evidence`: expected an examination of the requirement at the
  applicable revision; found none; needed an independent observation
  before acceptance.
- **stale evidence.** An observation *of the applicable revision* is
  `stale` — something moved under evidence that was supposed to cover
  the candidate being accepted. Class `evidence`: expected every
  observation of the applicable revision current at acceptance; found N
  stale with the first one's reason; needed reassessment of the changed
  candidate, or an exception recorded. A reading of an earlier candidate
  is history and never blocks: `reopen` stales what it replaces, and
  holding that against the new candidate would make an assignment
  unacceptable for ever.

Both are refusals of the acceptance, not of the work: the candidate
stays where it is and the ladder is untouched.

**The recorded exception.** The objective allows a Lead to accept under
an explicitly permitted exception (§11). `accept --exception TEXT`
carries it: the acceptance proceeds, and the exception is recorded on
the transition's evidence so that it reads back as an exception and not
as a pass. An exception without words refuses class `arguments`; the
gate is never lifted silently and never by a flag alone.

## Attribution

AC-11 asks that verification be attributable to an independent producer
and acceptance to the authorized Lead. The core records all three
participants; package E surfaces them in `coverage` and in the
acceptance's own reading, so the Lead sees producer, verifier and
acceptor before it accepts rather than after. The refusal of a
self-issued verification stays where it is, in the core, and this page
adds a row to the suite that fires it through both projections.

## Refusals

| class | when |
|---|---|
| `participant` | the caller's row is not this session's |
| `role` | the caller does not hold the role the verb requires |
| `producer` | the caller submitted the candidate it is verifying or accepting |
| `evidence` | acceptance without a current examination, or over stale evidence, with no exception recorded |
| `arguments` | an exception with no words, or an unknown option |

## What is measured

Every row of the suite runs against isolated state: a World with its
own HOME and XDG roots, a store created inside it, and no reading of
the operator's own. The restart reading is taken from a live run: a
participant binds, holds work, has a request and a decision open, the
process ends, a new process binds the same session and reads `brief`,
and the two readings are compared. Both are pasted in the commit that
lands them.
