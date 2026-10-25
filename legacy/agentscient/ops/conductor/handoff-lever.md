# The handoff manager — contract

Standing: built at the owner's word (owner, 2026-09-13: "work on handoff
and aar process - i think a new branch/PR per session handoff is
excessive"; "we need a way to handoff and onboard consistently between
sessions in a multi-agent env"; "maybe handoff states - so if a session
onboards a handoff - it marks it as taken"; "maybe allow for selection
of session handoffs"). Source of demand, measured 2026-09-13 at
`8acab9aa`: 10 of the 40 pull requests merged since the 12th carried
records only, each two to five files, because the close record had to
name the merge sha and so could never ride the work; and `onboard.py`
served the AAR that sorted last by file name, which on a day with five
AARs was not the newest.

## Conventions

This instrument inherits `ops/conductor/instrument-conventions.md` and
runs on the state engine (`machine-lever.md`): its rows live in
`state.db`, never in a file. Writing verbs require `WF_AGENT`; reads
do not and never create the store.

## The shape

A **handoff** is one closing session's package: a title, a summary in
the owner's register, the branch it closed on and the PRs it opened. A
**thread** is one item of it, the unit that is claimed: a title, a body
saying where to pick it up (record ids, PR numbers, files as
citations), and an optional addressee (`--to`), a role or a named
session. Threads are subjects of the `thread` machine; a handoff's own
standing is derived from its threads and is not a machine.

The thread machine, declared as data in the tool:

| transition | from | to | actor | evidence | failure (a refusal, exit 2) | hold |
|---|---|---|---|---|---|---|
| take | open | taken | any | none | `taken` | take |
| release | taken | open | holder-or-lapsed | required (the reason) | `place` | clear |
| done | taken | done | holder | required (a reading) | `place` | keep |
| supersede | open | superseded | any | required (the newer id) | `place` | keep |

The table is the declaration in `handoff.py`, read by `test_handoff.py`
(Ran 33 tests, OK, at the sha named in the commit that carries this page).

A take is held for 8 hours by default (`--ttl MIN` sets another); after
that anyone may release it, so a session that died does not hold a
thread forever. The holder stays on a closed thread as the record of
who closed it.

## Instrument

`python3 ops/conductor/tools/handoff.py <verb>`:

| verb | effect |
|---|---|
| `open TITLE --summary TEXT [--branch B] [--prs TEXT]` | a handoff; prints `handoff H-n` |
| `thread H-n TITLE --body TEXT [--to WHO]` | a thread of it; prints `thread T-n` |
| `take T-n --branch B [--ttl MIN]` | claims the thread for the identity on that branch; prints `taken T-n on B` |
| `take H-n --branch B` | claims every open thread of the handoff in one transaction at one stamp (`machine.move_many`), one line each; refuses `taken` and claims nothing when another identity holds one of them, the check made under the same write lock as the moves |
| `release T-n --why TEXT` | gives the claim back; prints `released T-n` |
| `done T-n --evidence READING [--note TEXT]` | closes the claim on a reading; prints `done T-n: READING` |
| `supersede T-n --by T-m` | an open thread replaced by a newer one |
| `list [--state S] [--to WHO] [--handoff H-n] [--all]` | one line per thread, oldest first; open and taken unless `--state` or `--all`; `no handoffs` when nothing matches or no store exists |
| `show T-n` / `show H-n` | the row, its body or summary, and (for a thread) its marking |
| `held --branch B` | the threads a branch still has claimed; exit 1 (Finding) while any, else `none held` and exit 0 |
| `brief` | the onboard section: `handoffs: open=N taken=M done=K` then, per handoff with live threads, its line and its open and taken threads indented |

A `take` names the branch the work is on, and that branch must be in
the deliverable lock (`deliverable.py show`); otherwise it refuses
`deliverable` with the lock's branches in the found clause and the
`deliverable.py add` command in the remedy. A claim nobody can find on
the board is not a claim.

A reading for `done` is a sha (7 to 40 hex), `#N`, a dispatch record id
(`YYYYMMDDTHHMMSSZ-...`) or a `P-` corpus row id; anything else refuses
`evidence`. The same shapes the claim-unmeasured row accepts.

## Where it sits in the session

- **Onboard** prints `== handoffs` from `brief` after `== state`, the
  last section (the `== thread` section that once followed it is retired,
  T-8); an absent store is a measured fact on its line. The
  section shows; it never claims. A session takes a thread by the
  verb, at the owner's word or its own choice, after the lock names its
  branch.
- **Close** opens the session's handoff and its threads as the last
  commit-free act of the session, and `held --branch <branch>` must
  print `none held` before a session calls itself finished: a thread this session took
  and neither closed nor released is the handoff failing, named. No
  record needs the merge sha, so no trailing records PR exists.
- The existing `HANDOFF-*.md` and `AAR-*.md` files stay as record and
  are not migrated; the open threads of 2026-09-13 are seeded as the
  first rows by the session that built this.

## Refusals

| refusal | fires when |
|---|---|
| arguments | an unknown verb, option or state; a missing required option; a wrong positional count |
| identity | a writing verb without `WF_AGENT` |
| id | an id not of the form `H-n` or `T-n`, or of the wrong kind for the verb |
| unknown | an id the store does not hold; `show` on an absent store |
| deliverable | a take whose branch the lock does not name |
| taken | a take of a thread another identity has taken; a handoff take with such a thread |
| holder | a release before the ttl, or a close, by another identity |
| evidence | a close whose reading has no known shape |
| place | a move from a place the transition does not start from |

## Lint

Inherited; no per-file row. Tests:
`ops/conductor/tests/test_handoff.py`; the onboard section in
`ops/conductor/tests/test_onboard_lever.py` (`HandoffsSection`).
