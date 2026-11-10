# Pull request quality

A pull request makes a clear, justified change, demonstrates its important
guarantees, and gives a reviewer enough evidence to assess it independently.
This standard applies to Xorchestrate contributions. xor's current direction
remains authoritative; scope, priorities and accepted limitations can change.

## Explain the change

Describe the concrete problem or capability, its evidence, the cause where known,
and the resulting behavior. A short before/after example often explains a fix
better than an implementation inventory. Keep one coherent purpose per PR;
closely related changes can stay together when they can be reviewed together.
Explain consequential architecture, dependency and maintenance choices.

Start from the existing implementation and tests. Use the
[application architecture](hexagonal-architecture.md) and
[testing framework](testing-framework.md) to preserve separation of concerns and
reuse contracts, support adapters and execution services. An arbitrary file or
test-count target does not determine quality.

Rewrite the title and description when the implementation's scope changes. The
final description should stand on its own for someone who has not read the chat.
Scale detail to the change: a small fix may need only a few sentences, one coverage
row and its verification evidence. Remove unused template sections or state why
they do not apply.

## Map guarantees to coverage

For behavior or test changes, identify each material guarantee touched, the
boundary at which it is checked, and whether coverage is new, extended, moved or
removed. Describe reused support, justified new shared support and remaining gaps.
For moved or removed tests, explain where each guarantee now lives or why current
intent makes it obsolete. A documentation-only change can say that no behavioral
coverage changed.

Use `contracts/testing.json` as the existing suite catalog; update affected
declarations when module ownership or guarantees change. The PR coverage map
describes the change and does not create a second complete test inventory.
Prefer scenarios in existing contracts over duplicated fixtures or one-off
runners. Keep distinct boundaries where they provide distinct evidence.

Assertions derive expected behavior independently of the code they exercise.
Test the relevant public, integration, consumer or installation boundary when
that boundary is part of the claim. For example, a mock transport cannot establish
that a native harness authenticated successfully. Use separate implementation
and test authors when work is delegated, while reviewing the actual assertions
and evidence regardless of who wrote them.

## Prove detection

For a behavioral fix, run the same regression test against the defective
implementation and the fix with the same relevant inputs. Record the intended
failing assertion, the observed red outcome, and the green outcome. A setup error,
missing function or unavailable dependency does not prove detection of the defect.
Keep the commands, revisions and concise evidence needed to reproduce the result.

For new checking machinery with no meaningful defective predecessor, use a
relevant deliberate fault and a healthy control. Verify the fault actually took
effect and that the real check detected it. Plant faults in disposable copies or
isolated worktrees; restore the healthy candidate before publishing.

For test-support refactoring or consolidation, repeat relevant fault witnesses
before and after the change and show that the intended failures are still
detected. Preserve the mapping when test names or grouping deliberately change.
Run healthy controls as well. Select faults that exercise the changed guarantees;
this does not require mutation testing the entire repository for every PR.

Reuse the framework's existing calibration, command and evidence services.
The [testing guide](testing-framework.md#adding-or-changing-coverage) explains
where reusable expectations and support belong. Documentation-only changes need
appropriate content/link checks; do not invent a behavioral red result for prose.

## Bind evidence to the work checked

Record the source revision and the actual execution revision when they differ,
such as GitHub's generated PR merge commit. For local uncommitted changes, identify
the relevant working-tree inputs instead of claiming the clean commit was tested.
Retain material runtime versions, commands, selected scope and outcomes using the
existing test-kit receipts and artifacts. Link those records rather than copying
large logs into the PR.

Confirm the intended checks actually ran. Test selectors, skips, caches or a
successful empty command can bypass the behavior a claim depends on. Installation
changes need evidence that exercises the installation path; select cold or warm
conditions according to the behavior being tested. Preserve failed attempts and
retries, and distinguish failed setup, cancellation, unavailable execution and
assertion failures. Report local, hosted and native execution separately.

Source or material input changes invalidate the affected evidence. Recheck the
changed property and affected behavior; reuse applicable evidence for unchanged
work with its original revision and scope visible. Complete the repository's
required checks and agreed acceptance work on the candidate being handed over.
See [CI](../.github/CI.md) for the actual jobs, execution policy and limitations.

Current CI status belongs in GitHub Checks. The PR description retains durable
verification evidence and links to runs where they support a specific claim;
avoid hand-maintained paragraphs that become stale when another run starts.
Documentation saying a check is required does not establish that a repository
rule enforces it. Verify the active setting when making an enforcement claim.

## Review and delivery

1. Plan the coherent change and its acceptance evidence. Parallelize independent
   implementation and test work through isolated branches or worktrees.
2. Complete the implementation and relevant verification, including red proof for
   behavioral fixes and checking machinery as described above.
3. Before opening a draft PR, have an adversarial sub-agent inspect the candidate
   diff, scope, assertions, failure paths and evidence. Record the reviewed
   revision or working-tree identity, material findings and their disposition.
4. Evaluate findings by practical consequence, likelihood, benefit and cost. Fix
   and verify warranted defects; explain why an incorrect finding is rejected or
   a limitation is deferred. Resolving a thread should reflect that disposition.
   Findings do not automatically create new requirements or merge prerequisites.
5. Open the PR as a draft and get applicable CI green. Evaluate subsequent Codex
   review findings on the same basis, verify repairs and accurately retain any
   material remaining limitation. Update affected user documentation, and add
   concise release notes for user-visible changes to the `Unreleased` entry of
   [`CHANGELOG.md`](../CHANGELOG.md). Record configuration format, data version,
   lifecycle definition, adapter option, operator source schema and telemetry
   collector changes under its **Upgrade notes**, stating "None" for each
   unaffected category when the release is cut.
6. Hand the draft and its evidence to xor for review. xor controls readiness,
   merging and releases. Follow the repository's current merge settings.

The pre-draft review is a focused delivery step, not the start of an unlimited
review loop. After repairs, use targeted verification of the affected properties.
Broaden or repeat review only when a change, failure or unresolved material
question justifies it. Finish when the agreed work and required checks are done;
explain actual blockers and accepted limits without silently changing the goal.
