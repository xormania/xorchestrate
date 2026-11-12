---
name: red-first
description: Prove every test fails for the intended reason before the implementation or fix exists, against the defective code for a fix, and back new checking machinery with a deliberate fault and a healthy control.
---

# red-first

Read and follow [Prove detection](../../../docs/pr-quality.md#prove-detection) in
the PR quality standard, and [the testing framework](../../../docs/testing-framework.md)
for where tests belong.

- A red result counts only when the intended assertion fails. A setup error, a
  missing function or an import error proves nothing.
- Write the test before the implementation or fix exists. For a fix, run it red
  against the existing defective code and green against the fix, with the same
  relevant inputs.
- Record the commands, the revisions and the failing assertion, then the green
  outcome.
- New checking machinery with no defective predecessor always gets a deliberate
  fault and a healthy control, in a disposable copy or isolated worktree.
  Restore the healthy candidate before publishing. Planting further faults in
  finished, already-proven work happens only when whoever directs the work asks.
- When refactoring test support, repeat the relevant fault witnesses before and
  after the change.
- When work is delegated, the test author and the implementer are different, as
  the PR quality standard asks. In a split team the implementer does not edit
  tests, and the tests go in unchanged as their own commit before the
  implementation ([team roles](../../../docs/agent-workflow.md#team-roles)).
