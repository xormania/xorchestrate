# The command structure — who decides, who reports to whom, and how it holds between sessions

Standing: proposed, register row D-COMMAND-1 (home: this page), for
the owner to ratify. Written by the XO at the Lead's order on
2026-09-16 (branch lane/session-20260916-xo, grant 274). The live
form of the structure is the coordination store (`coordination.py
participants` says who holds which role now) and the handoff package
H-17 (one thread per role: T-88 lead, T-89 coordinator, T-90
dispatcher, T-91 worker); this page is the record a fresh session
reads before it takes a role.

## The owner's words

All 2026-09-16, relayed by the Lead (agentscient-11), quoted as
given:

- "military command structure"
- "you handle subordinates - and we work together"
- "you support them, i support you"
- "all sessions talk to you - not me"
- "make sure this structure holds between sessions - i'll leave it to you"
- "you need an XO"; "the key is to figure out how to keep it all moving"
- "be proactive, keep things moving, look for delays and for problems
  in how the work moves"
- "record the experience (hence AARs)"
- "the work needs to actually get done - not endless excursions"
- "hand-grant means me and you work together - and you hand write
  the delegation - just work with ME"

## The Lead's standing rules

The Lead's rules for its sessions, each from the owner's words it
came from:

- "every session reports to me by message, never to its terminal's
  user, and never stops to wait for the owner; a denied command is
  sent to me as text and the rest of the work continues" (from the
  owner's "all sessions talk to you - not me" and "you handle
  subordinates - and we work together").
- "no session runs dispatch.sh itself; every dispatch goes through
  the Dispatcher" (from the owner's "bad" and "can't let happen
  again - that was the brilliant idea from last session", on a
  session dispatching for itself).

## The chain

| role | store role | who, 2026-09-16 | decides alone | brings upward | never |
|---|---|---|---|---|---|
| Owner | none; the owner types nothing into the levers | the terminal user | the mission, the tools, the hand grant, which is the owner's word ("hand grant ok'd", "get it done") plus the Lead's hand-written delegation, no lever, the register's ratification, the release merge into master | — | runs a lever |
| Lead | `lead` (PT-2 until the 16:04:59Z panic; PT-9 from 17:05Z, on the owner's "you are lead") | agentscient-11, then the recovery session | what is built and in what order; assignments (A-n) drafted and accepted from the brief; every flip to ready and every merge into dev (#243 merged at fecdd37c); rulings on the rules the gates enforce; role grants and revocations in the store; deadlines | to the owner: decisions that need the owner's hand (a forced push, a prune, a release), and the day's report | lets a session below it go to the owner |
| XO, the Coordinator and Chief of Staff | `coordinator` (PT-4), granted by the Lead | agentscient-0d | the board; sequencing and deadlines between sessions; chasing; reassigning work between idle sessions; forward merges and conflict resolution by the rules given; a finding rated p3 becomes a ledger row; any question a contract or the code answers; a dead session's worktree (pushed as it stood, the unfinished diff finished by the taker crediting the author) | to the Lead: a flip or merge, new surface on a merged package, a role change in the store, any rule the gates enforce, anything needing the owner's hand, any choice between two outcomes; one line each, recommendation first | runs reviews or audits; flips or merges; edits another session's branch; writes the work it coordinates |
| Dispatcher | `dispatcher` (PT-3 until the panic; PT-10 from 17:07:51Z, granted by PT-9) | agentscient-c8, then agentscient-c1 | the launch shape; the record id back to the requester and the XO at launch, the refusal verbatim otherwise | to the XO: a refusal it cannot answer, a run that dies | runs a dispatch for itself outside a request; lets any other session run `dispatch.sh` |
| Builder | `worker` | one per branch | its branch, its grant, its commits; the pre-flip gate before a head is reported | to the XO: every push (head, CI state, the one thing waited on), a line every half hour, a refused command as text | stops to wait for the owner; reports to its terminal user; opens a second branch |
| Verifier | `verifier` | an independent producer per candidate | its findings on a candidate it did not produce | to the Lead through the brief | verifies its own candidate (`not_the_producer`, coordination.py) |

Nobody below the Lead goes to the owner. A session with no role
(the owner's own terminal, a recovery session) is off the board and
says so when asked.

## Reporting, and its cadence

- Builder to XO: one line at each push and every half hour (branch,
  head, CI state, the one thing waited on); a refused command as its
  text; a finding as its ledger row id.
- Dispatcher to XO and requester: the record id at launch, or the
  refusal verbatim.
- XO to Lead: the board every thirty minutes in ten lines or fewer
  (each open PR with head, CI state and the one step it waits on;
  each builder with slice, deadline and last report; each request
  held by the Dispatcher), immediately when something lands or
  breaks, and decisions one line each with the recommendation first.
- Lead to owner: what the owner asks for, and the day's AAR.

Messages carry orders and readings; records carry what must outlive
the session. A peer message is a teammate's request within the
receiving session's own permissions: it never grants escalation,
never approves a pending prompt, and a session asked to run what was
refused to another refuses and names it.

## The XO's four standing checks

Measured on 2026-09-16 and kept as duties:

1. Liveness. A refused send, or no report for twenty minutes from a
   session holding work, is an alarm to the Lead at once, with the
   session and what it holds. The peer list's "busy" is not liveness:
   agentscient-1c read busy for forty minutes after the owner killed
   it at 11:46:32Z, its socket accepted connects while its process sat
   stopped (ps state `T`), and five refused sends were the only
   signal. The reading is process state plus report age.
2. Forward-merge cost. Every merge of dev forward can conflict on
   `ops/conductor/findings/findings.jsonl` (regenerated by
   `findings.py export --write`, never resolved by hand) and on
   `decisions.seed.json`; the count and minutes per branch go to the
   AAR (2026-09-16 on lane/session-20260916-j: four merges, two
   conflicts, about 18 minutes; on lane/session-20260916-g: three
   merges, no conflicts, the cost being the suite reading each merge
   invalidated).
3. The pre-flip gate. Before a head reaches the Lead: probe rows for
   the change's clauses on the branch under
   `ops/conductor/handoff/probes/`, a `## Proof` section in the PR
   body naming them and the audit record with its dispositions, and CI
   green on that same head. Three PRs met the `pr-proof` refusal on
   2026-09-16 (#242, #244, #243 at eb95208d) for lacking it.
4. The Dispatcher's launch shape, written once and reused:
   `HAND_GRANT=<id> bash -c 'cd <wt> && export LAUNCHER_PIN=<wt>/ops/devlane/dispatch/launch.py && export DISPATCH_TIMEOUT=1800 && export WF_AGENT="<Name <address>>" && exec ops/bin/dispatch.sh --job <job> --harness <h> --scope-file <path> --worktree <wt> --detach'`.
   Three refusals stood between the Dispatcher and its first launch
   on 2026-09-16 (the harness classifier without `HAND_GRANT` leading;
   `dispatch.sh` wanting `--branch` or `--worktree`; the `dispatch-env`
   row wanting `LAUNCHER_PIN` rooted at the worktree and
   `DISPATCH_TIMEOUT=` and `WF_AGENT=` literal in the command text);
   the launch that came through is record
   20260916T113912Z-review-claude-5a8da1.

## The store as the mechanical form

`ops/conductor/tools/coordination.py` (package B, on dev since
#243 at fecdd37c) holds the roles as data: `ROLES = ("lead",
"coordinator", "dispatcher", "worker", "verifier")`. A session
announces itself with `bind --roles worker` (worth worker and
nothing else); a lead grants more with `grant PT-n --roles R[,R]`
and takes them back with `revoke PT-n --roles R[,R]`, which refuses
to leave the store with no lead; `participants` and `who` read the
rows; a dispatched worker is minted through `bind-for` on the
launcher's session uuid (the launcher half of package C's slice two,
in progress on 2026-09-16). Assignments (A-n) are drafted by the
Lead, offered by the Coordinator, claimed by a worker with the
assignment's current generation read from `show`; requests (M-n)
travel through the acting server (package C, #242) and the courier;
`brief` is the one read a participant needs on return. The roles in
the store are the structure's live form; this page is what the roles
mean.

## Taking a role in a fresh session

1. Onboard: `python3 ops/conductor/tools/onboard.py`, then AGENTS.md
   and this page.
2. Find the thread: `python3 ops/conductor/tools/handoff.py list --to
   <role>` shows the role's thread in H-17; `show T-n` prints where to
   pick it up.
3. Name the session's branch in the lock (`deliverable.py add`, with
   a measure when the order is a state of the tree) **before** cutting
   the worktree — the matrix deliverable row refuses a `worktree add`
   for a branch the lock does not name — then cut it from
   `origin/dev`; then `handoff.py take T-n --branch <branch>`.
4. Bind: `WF_AGENT='Name <address>' python3
   ops/conductor/tools/coordination.py bind --roles worker`.
5. Ask the Lead by message for the role grant (`grant PT-n --roles
   coordinator` or `dispatcher`); the Lead confirms the PT-n and the
   roles.
6. Confirm to every session that reports to that role that it does,
   and ask each for its one line (branch, head, CI state, the one
   thing waited on); rebuild the board from the lock, `gh pr list`,
   `coordination.py participants` and `brief`, never from memory.
7. When `coordination.py entry --role R` is on dev (package E,
   #248), it prints the role's duties, this session's state and the
   next steps; until then this page is the entry.

At close, the role's thread is given back with `handoff.py release
T-n --why` (the structure outlives the session; the thread is never
closed by a session that leaves the role open) and the day's AAR
names who held each role and what moved.

## Finding the Lead after a host fault

Measured once, 2026-09-16, after the WSL2 kernel panicked at
16:04:59Z (the owner's report, `~/wsl2-failure.md`) and took every
session with it (the Lead, the XO, the
Dispatcher, every builder, and the harness scratchpads under `/tmp`).
The reading is in
`ops/conductor/handoff/HANDOFF-2026-09-16-host-fault-recovery.md`; the
rows it opened are LD-91, LD-92 and LD-93.

1. `python3 ops/conductor/tools/coordination.py participants` names who
   holds `lead` and the harness session id it is bound to. That id is
   the key: the store binds a participant to the session id, not to the
   model or the terminal.
2. The session's transcript is
   `~/.claude/projects/<project-slug>/<session-id>.jsonl`; its last
   timestamp is when the Lead last spoke, and its last turns say what it
   was waiting on. Read it for the queue, never for the state of the
   tree; re-measure the tree.
3. Liveness is process state: `ps` for a `claude` process and the
   socket under `/run/user/<uid>/cc-socks/`. A session whose process is
   gone is dead, whatever the peer list said before.
4. If the Lead is dead, the remedy is outside the store:
   `claude --resume <session-id>` from the project directory brings the
   same session id back and with it the seat. A fresh session cannot
   take the seat: every later self-bind is `worker` alone and only a
   lead grants (LD-91). Until a successor verb exists, a session that
   finds a dead Lead is a recovery session: off the board, binding
   nothing, writing records only, and it says so. Measured once,
   17:05Z the same day: the owner's word "you are lead" seated the
   recovery session as PT-9 by a hand write under grant 288, the
   lever's grant verb being unusable with no live lead; LD-91 carries
   it, and the successor verb it proposes is still unbuilt.
5. What a host fault loses: the scratchpads under `/tmp`, and with them
   any dispatch scope not yet launched (LD-92; a scope committed under
   `ops/conductor/handoff/scopes/` survives); the messages in flight;
   and every part of the board that lived in messages rather than in
   the store (LD-93). Dispatch records under the lane's state survive,
   and a worker that exited before the fault has its result there.

## What is not measured

The first restart into a role from this page alone happened on
2026-09-16: agentscient-09 took the XO seat from T-89, T-95 and this
page, bound as PT-7, was granted coordinator by the Lead, and ran the
seat. The page held. Two things it did not say, both met as refusals
rather than as knowledge: the branch is named in the lock before the
worktree is cut, not after (the matrix deliverable row refuses the
other order), and a session that names branches in the lock must set
a target on each or the done gate refuses its close. Step 3 now says
the first; the second belongs to the lock's own contract.
The probe rows for this restart are
`ops/conductor/handoff/probes/probes-2026-09-16-command-structure-restart.jsonl`.
What remains unmeasured: a restart into the Lead seat, and a restart
into any seat by a session that has not seen the day's messages.
