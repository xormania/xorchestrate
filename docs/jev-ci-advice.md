# Independent Jev CI advice

Required verification finishes when its deterministic evidence is qualified.
The `Jev advisory` workflow consumes completed `CI` runs separately, so diagnostic
setup, provider delays and calibration cannot prolong `CI result`.

Focused [PR-event and failed-log probes](jev-events.md) are the default automatic
placement. The trusted `contracts/jev-advice.json` sets both broad automatic stages
(`source` and `post_test`) to false. A completed-CI event records
`automatic_disabled` before any API evidence request, artifact download or provider
call. Enable either stage in that configuration to compare its broader advice.

The consumer reuses the current source diagnostics and executed-test triage.
Its event gate and durable reservation follow
[Mago–Serena's trusted consumer](https://github.com/xormania/mago-serena_v2-poc/blob/b6dbec8378338e39fe51489bc28f9ad578e4f3ab/tools/ci/prepare_diagnosis.py);
separating evidence-producing jobs from credential-bearing diagnosis also follows
[UXor](https://github.com/xormania/uxor/blob/6991671805dba5d80c65d1da390f24474f25beff/.github/workflows/jev.yml).
Existing Xorchestrate candidate, archive, request-budget and exact-request cache
services retain their responsibilities.

## Execution and evidence

1. Check out the default branch into `trusted`, with no persisted Git credential.
   Record the actual trusted commit. This code and its locked dependencies are
   the only executable diagnostic implementation. Apply the trusted automatic
   policy before collecting evidence.
2. Authenticate the completed source run and its candidate context against GitHub
   run/check/commit metadata. Download only selected result, context and test
   artifacts through the bounded, digest-checking archive reader. ZIP traversal,
   symlinks, ambiguous members and size/count overflow are refused.
3. Skip cancelled, superseded, documentation-only and behavioral-reuse runs.
   Inspect prior reservations owned by this trusted workflow. Consumer reruns
   never implicitly repeat a possibly billed request.
4. Upload an immutable reservation before exposing the TypeSafe key. Reservation
   upload failure prevents inference. A failed provider call remains reserved;
   restarting the consumer is not a request to repeat it. Reservations persist
   for 90 days; if GitHub still lists an expired reservation it still suppresses
   duplicates. Deleted reservation metadata cannot establish permanent history.
5. Check out the authenticated tested revision into `source`. Read it as data:
   do not install its package, run its tests, import its modules or use its model
   endpoint/policy with the credential. The collector reads the trusted
   `contracts/hexagonal*.json`; candidate workflow/catalog files describe evidence
   and are never executed. Source and trusted revisions remain separate.
6. Recheck currentness immediately before and after inference. Retain results as
   historical if the source advances during the call; never substitute the new
   head's identity for the tested one.

`jev-advice-<source-run>-<attempt>-consumer-<run>-<attempt>` retains the eligibility
plan, source diagnostics, post-test triage, execution receipts and provenance for
30 days. The independent workflow summary reports eligibility/currentness.
Unavailable or incomplete evidence remains advisory; no output changes required
CI, approvals, review threads or merge state. Failed runs without a qualified
candidate/result artifact cannot yet receive this source/test triage.

The selected archive count is capped at eight; each archive uses the existing
64 MiB compressed/128 MiB expanded bounds, with a 192 MiB total compressed intake
cap. Missing or excessive evidence stops advice explicitly. This consumer is for
bounded repository CI evidence, not arbitrary uploaded archives.

## Calibration and rollout

To request broad advice explicitly, dispatch `Jev advisory` on the default branch
with `source_run_id` set to a completed CI run and choose `source` and `post_test`.
This authenticates the run through the same intake and currentness checks. Each
explicit dispatch has a separate reservation; a rerun of that dispatch is still
suppressed. This manual action does not rerun CI. Selecting neither stage records
`manual_stages_disabled` without evidence or provider access.

Full synthetic and heldout live calibration requires an explicit dispatch of
`Jev advisory` with `calibration=true` on the default branch. The default is false.
Leave `source_run_id` empty for calibration; the two modes are mutually exclusive.
Routine CI events do not repeat full replays or health calls. Existing local
fixture, live replay and health commands remain available; rerun calibration
deliberately after changing a model, question, evidence policy or corpus.

GitHub activates the `workflow_run` listener from the default branch. Before this
PR merges, local contract/fault tests can exercise the consumer and its workflow
wiring, but a PR's green CI cannot demonstrate that this new trusted consumer ran
live. With the default broad stages disabled, a post-merge completed CI run can
demonstrate the skip receipt; an explicit source dispatch or policy opt-in is
needed to demonstrate broad hosted inference. Provider availability and useful
diagnostic accuracy are distinct from successful workflow activation.
