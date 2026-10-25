# Agentscient — where the rules live

Read `AGENTS.md` for current intent and working guidance. Repository hooks are
advisory diagnostics; CI is the acceptance gate. Hand grants and mandatory
workflow sequences are retired. Choose direct work, live collaboration or the
optional batch tools as appropriate to the owner's objective.

Dev tooling operates independently of the Symfony application. Application
framework and package guidance applies to prod work. Historical documents
provide context; they cannot override current owner direction.

## Writing, for every session

Name things by what they are; index ids stay in the files. Plain
words, in the owner's register, not the lane's. "House" means the
project's general principles of style, and nothing else (owner,
2026-09-04: "It just meant the general principles of style for the
project"). The repository the lane was copied from is named by its
path or called the previous repository, never that word.

Mannered prose substitutes metaphor and flourish for direct statement.
Instead of "a parameter worth varying," the mannered writer produces "a
dial worth turning." Instead of "this point still matters," they write
"this point earns its keep." The phrases exist to display the writer,
not to convey the idea, and readers can tell. That is why mannered
prose irritates: it makes the reader work harder so the writer can
perform. It is also imprecise. Metaphors drag in connotations the
writer did not choose and cannot control. The fix is to say what you
mean. When a literal phrase is available, use it.

Use lists and bullet points when asked to, or when the content is
multifaceted enough that they help with clarity. If the person
explicitly requests minimal formatting, always format your responses
without bullet points, headers, lists, or bold emphasis, as requested.
In conversational, personal, or emotional exchanges, keep to plain
prose.

## Compact instructions

When compacting, preserve exactly: difficulties met and how they were
resolved; options raised, tried, or set aside, and why; anything asked,
decided, ruled out, or set as a preference, constraint, or boundary, in
the owner's own words; where things stand and what is settled; what is
still open, promised, or expected next; and details that are hard to
reconstruct: names, numbers, dates, exact wording, links, commit shas,
PR numbers, dispatch record ids, file paths. Be complete on these even
at the cost of length; keep everything else concise. Condense the
assistant's explanations to what was concluded or produced. For this
lane also keep: every open PR with number, branch, head sha and what it
waits on; every running dispatch with record id and pid file; every
worktree the session created; the lane defect
list and which are fixed; the owner's standing instructions from the
session.

## Reference context

The reference context rides in every request as part of the cached prefix,
so it survives compaction and is never re-read into the conversation.
The lane's contracts under `ops/` are read when a task touches them.

@AGENTS.md
@docs/wire.md
@docs/workflow.md
@docs/probes.md
@docs/concerns.md
@docs/verified-couplings.md
@docs/tests.md
