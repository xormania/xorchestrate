---
name: red-first
description: Write or receive tests first and prove each one fails for the intended reason before any implementation exists; plant faults only when asked.
---

# red-first

Read and follow [Prove detection](../../../docs/pr-quality.md#prove-detection) in
the PR quality standard, and [the testing framework](../../../docs/testing-framework.md)
for where tests belong.

- A red result counts only when the intended assertion fails. A setup error, a
  missing function or an import error proves nothing.
- Record the command, the revision and the failing assertion, then the green
  result with the same inputs.
- New checking machinery with no defective predecessor gets a deliberate fault
  and a healthy control, only when asked, in a disposable copy or worktree.
  Restore the healthy candidate before publishing.
- When refactoring test support, run the relevant fault witnesses before and
  after the change.
- The test author and the implementer are different, as the PR quality standard
  asks. The implementer does not edit tests; a test that looks wrong goes back
  through the coordinator ([team roles](../../../docs/agent-workflow.md#team-roles)).
- On a shared branch the tests go in unchanged as their own commit before the
  implementation commit.
