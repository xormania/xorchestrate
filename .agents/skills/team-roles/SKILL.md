---
name: team-roles
description: How work is split across a team of agent sessions on this repository, with coordinator, test writer, implementer and lookup roles, default path ownership and one committer per branch.
---

# team-roles

Read and follow [team roles](../../../docs/agent-workflow.md#team-roles). These
rules apply when work is split across sessions; a single contributor follows
the PR quality standard alone.

Rules that must survive any summary:

- The coordinator decides design and scope and takes those questions to xor. A
  direct message between sessions never settles either.
- The test writer writes the tests and proves them red first; the implementer
  does not edit them, as the PR quality standard asks for separate authors when
  work is delegated.
- Path ownership in the guide is the default; the coordinator may change it per
  task.
- One committer per branch, tests committed first and unchanged. One writer per
  file.
- For everyone: commit and push work in progress at every checkpoint, draft pull
  requests only, xor merges, never force-push.
- Nothing in the repository names sessions, models or harness assignments; the
  coordinator holds the current roster.
