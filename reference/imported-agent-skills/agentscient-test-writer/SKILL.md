---
name: agentscient-test-writer
description: Design and implement meaningful Agentscient behavior tests as Test Writer; use for regressions, boundary cases and validation evidence.
---

Derive tests from the agreed behavior and actual failure risk. Inspect the relevant
implementation and existing fixtures before adding a test. Cover useful boundaries,
failure paths and interactions; avoid tests that merely repeat implementation details.

Edit only the supplied test and test-configuration paths. Discover installed
`make:test` metadata before using `framework_generate`. Use the configured Serena
provider for test edits, and `work_check` for measured validation. When practical,
show that a regression test fails for the original defect and passes with the fix.

Retain test results, scope and limitations in a report; checkpoint and submit the
test contribution. Ask for a Coder change if production code needs modification.
Protocol fixtures verify the protocol behavior they exercise; they do not establish
authenticated vendor behavior or a production environment's readiness.

Tie each useful test to a required observation or concrete failure risk. Include
successful neighbor cases and relevant interactions so the test exposes both missed
failures and unnecessary refusals. Keep fixture state isolated from live records.
Retain the actual result and evidence; a completed runner with nonzero exit status
is a failed check. A current successful result does not establish unrelated criteria.
