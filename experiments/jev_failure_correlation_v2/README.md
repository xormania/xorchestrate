# Jev failure correlation and investigation experiment v2

This isolated experiment compares structured deterministic checks, Jev assessments,
executed investigations, adversarial assessment, and selective native reasoning.
It measures action changes and investigation cost separately from label agreement.
The result may support no additional model stage. It does not add a production
acceptance gate or alter Xorchestrate's configurable application and adapters.

The branch starts from frozen v1 publication
`d2d2123bb8d4e866454c8e8e364f7749fa63a198`, tree
`7bb88d5fd01a21478b720ad2d42b2b627b74bb92`. The intended PR base is
`experiment/jev-failure-correlation-v1`, so the v2 delta can be reviewed separately
from prior product and v1 work. Repair PR #6 remains separate. Each run manifest
identifies its measured source and product subject; a later evidence-publication
commit must not be substituted for those identities.

## Prepare and verify

Use Python 3.11 or newer from the repository root. The existing Cloud environment
has `/workspace/jev-venv`; a new checkout can create its own virtual environment:

```sh
python3 -m venv /tmp/xor-jev-v2-venv
/tmp/xor-jev-v2-venv/bin/python -m pip install -e '.[test]'
mkdir -p .agents
cp -R reference/imported-agent-skills .agents/skills
/tmp/xor-jev-v2-venv/bin/python -m pytest experiments/jev_failure_correlation_v2/tests -q
/tmp/xor-jev-v2-venv/bin/python -m pytest tests/hexagonal -q
```

The following commands use the activated environment's `python`. Use a fresh
output directory for each execution. Reproduction executes the controlled
application, native adapter, or protocol-peer boundaries and retains raw output:

```sh
python -m experiments.jev_failure_correlation_v2.corpus --out /tmp/jev-v2-reproduced-corpus
python -m experiments.jev_failure_correlation_v2.runner prepare --out /tmp/jev-v2-offline
python -m experiments.jev_failure_correlation_v2.runner run --out /tmp/jev-v2-offline
python -m experiments.jev_failure_correlation_v2.runner score --out /tmp/jev-v2-offline
```

`prepare` reads the checked-in frozen corpus rather than silently regenerating
its labels. The separate corpus reproduction does not overwrite frozen evidence.
The default `run` uses no provider credentials or paid calls. Missing model arms
remain pending; successful offline execution is not completion of the experiment.
For the scope and provenance of each probe, inspect `CORPUS.md` and its raw source
references. Protocol peers do not establish installed-vendor behavior.

## Fresh direct Jev measurement

The primary route reads the already configured `TYPESAFE_API_KEY` environment
variable and uses the normal HTTPS proxy. No credential belongs in a command,
file, prompt, artifact, or Git commit. A new live run performs a bounded
authenticated smoke and requires a valid response from exactly `jev-1.14.0`
before measured calls. It never replaces the pinned version with an alias.

```sh
python -m experiments.jev_failure_correlation_v2.runner prepare --out /tmp/jev-v2-live
python -m experiments.jev_failure_correlation_v2.runner run --out /tmp/jev-v2-live --live --ledger /tmp/jev-v2-budget.json --label-review experiments/jev_failure_correlation_v2/reviews/label-review.json
python -m experiments.jev_failure_correlation_v2.runner export-reasoning --out /tmp/jev-v2-live
```

The machine-readable label review must record approved status and the exact
final corpus hash before live evaluation. The runner checks this binding; the
mere existence of a review file does not establish sufficient labels or resolve
reviewer findings. The accompanying `reviews/preflight.md` retains the review's
evidence and qualifications.

Keep the same budget ledger across new local live runs in one experiment. The
ledger reserves a full default-context allowance for every attempted call,
including failed attempts, while raw usage supports a separate list-price
estimate. The run manifest and transport plan record exact finite request,
retry, timeout, byte, elapsed-time, and spending limits. A budget stop is an
unresolved state, never a clearance.

The retained official price source is `https://docs.typesafe.ai/models`, inspected
2026-10-24: $0.042 per million input tokens, with output tokens free. Multiplying
reported input usage by this rate yields an estimate, not an observed invoice.
Actual billed cost remains unknown unless a billing source supplies it.

## Native reasoning comparisons

Reasoning evaluation is a separate execution route. Exported, hash-bound packets
distinguish the blind reasoning-only input from the full P3 fallback input.
Evaluate each packet in a separately recorded native invocation using its fixed
prompt and requested model/effort; retain the response, invocation identity,
observed metadata, context exposure, latency, and any available usage. Do not
use the implementing agent's retrospective judgment as a blinded measurement.

```sh
python -m experiments.jev_failure_correlation_v2.runner import-reasoning --out /tmp/jev-v2-live --responses /tmp/jev-v2-native-responses.json
python -m experiments.jev_failure_correlation_v2.runner score --out /tmp/jev-v2-live
```

Native subscription costs are unknown, not zero. Economic comparisons retain
that unknown and explicit sensitivity assumptions. Never/always, selective,
simple-risk and random escalation policies share a declared paired subset;
the independent random audit is a separate stream with its own sample design.
Random routing matched by call count is only a budget proxy when latency and
execution costs differ.

The response file is a JSON list, or an object with a `responses` list. Each
entry must satisfy the complete choices and invocation metadata contract in
its exported packet. The import rejects unknown or duplicate request identities,
mutated packets, missing metadata, and invalid choices.

## Replay and hosted verification

Replay scoring from a retained run requires no provider key and makes no new
model calls:

```sh
python -m experiments.jev_failure_correlation_v2.runner score --out PATH_TO_RETAINED_RUN
```

Use the measured source revision recorded in that run's manifest, and retain
its corpus, policy, requests, raw responses, probe observations, routes and
audit records. Hash checks establish byte identity; independently executed
controls and evidence checks establish what the bytes demonstrate. Cases from
the same incident group are correlated and cannot be counted as independent
support for a production error guarantee.

`.github/workflows/jev-failure-correlation-v2.yml` runs offline validation on
scoped pushes to `experiment/jev-failure-correlation-v2`, before merge. Report or
evidence-only changes do not trigger paid evaluation. The workflow uses
GitHub-hosted Ubuntu runners, checks v2 and frozen v1 tests plus established
hexagonal behavior, and retains artifacts even if a check fails. Artifact
names include source commit, run ID and attempt; the job summary records the
artifact ID, URL and digest. Inspect individual jobs and raw artifacts as well
as summaries.

An explicit `workflow_dispatch` with `live=true`, when that workflow is available
for dispatch, enables a separate hosted Jev fallback using the existing
repository `TYPESAFE_API_KEY` secret. It does not supply native reasoning
results. Direct Cloud and hosted runs retain separate identities. GitHub may
require the workflow to be registered on the default branch before exposing
manual dispatch. The pre-merge fallback is a scoped source push on the exact
experiment branch whose head commit message contains `[jev-v2-live]`. That
marker explicitly requests a fresh paid run after offline validation passes;
do not put it on ordinary source or evidence commits. Both routes require the
owner repository and exact experiment branch. The scoped push validation does
not depend on manual dispatch being available.

The final delivery report and E01–E14 evidence matrix distinguish implemented,
measured, contradicted, and still unverified requirements. Send the immutable
source/run/artifact handoff with the package's `05-INDEPENDENT-VERIFICATION.md`
to the separate adversarial review session. The implementation-time methodology
review is retained with its exposure limits and does not replace that review.
