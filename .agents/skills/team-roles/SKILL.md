---
name: team-roles
description: How a team of agent sessions works on this repository, covering the coordinator, test writer, implementer and lookup roles, default path ownership, routing design and scope through the coordinator, and one committer per branch.
---

# team-roles

Read and follow [team roles](../../../docs/agent-workflow.md#team-roles).

Rules that must survive any summary:

- The coordinator decides design and scope and is the only role that talks to
  xor about them. A direct message between sessions never settles either.
- The test writer writes every test and proves it red first; the implementer
  never writes or edits tests.
- Path ownership in the guide is the default; the coordinator may change it per
  task.
- One committer per branch, tests committed first and unchanged. One writer per
  file.
- Commit and push work in progress at every checkpoint. Draft pull requests
  only; xor merges; never force-push.
- Nothing in the repository names sessions, models or harness assignments; the
  coordinator holds the current roster.
