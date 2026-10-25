# Dev verification profiles

Owner correction, 2026-09-24: keep dispatch and conductor in the code while
reconsidering their value. Routine hosted CI should be practical. Use local
machines or registered local runners for the broader integration tests;
full workflow and tmux rehearsals do not belong in every routine gate.
This replaces the earlier requirement to run every dev test on every push.
Prod-lane now has an independent Symfony workflow; see [.github/README.md](../../../../.github/README.md).

The required GitHub job is `lane`. Changed files select the `dev-lane` and/or
`prod-lane` jobs; `lane` requires successful results for the selected work.
For dev-lane, `CI_RUNNER_MODE` selects both capacity and an explicit test profile.
An empty variable means `hosted`.

| Mode / command | Required coverage | Execution |
|---|---|---|
| `CI_RUNNER_MODE=hosted` | `ci`: lint, boundaries, configuration, probe corpus, domain and contract tests | Standard `ubuntu-24.04` |
| `CI_RUNNER_MODE=local` | `local`: every `ci` check plus integration tests | Existing GitHub `self-hosted` registration |
| `bash ops/bin/suites.sh --profile ci` | Same fast plan | Developer machine |
| `bash ops/bin/suites.sh` | Same local integration plan | Developer machine |
| `bash ops/bin/suites.sh --profile rehearsal` | All dev suites, including real sessions, tmux, coordination and full workflow rehearsals | Explicit developer run |

`test-profiles.json` classifies every discovered test module exactly once.
CI refuses new unclassified modules, deleted entries and duplicates. Profile
membership is an explicit scope decision, never a timing heuristic or a
failure-driven skip. All profiles share `lane.py` and `ops/testkit` discovery,
subprocess ownership, timeouts and result handling. `--suite DIR` remains an
explicit PARTIAL invocation for focused checks and portability fixtures.
It cannot claim a profile passed. The single Symfony runtime fixture case remains
an optional dev-tooling check, invoked explicitly with an installed application:
`PYTHONPATH=ops/devlane/fixtures/tests python3 -m unittest test_symfony_app_app.SymfonyAppMakeProve.test_full_proof_after_composer_install`.
It is not part of prod-lane CI.

A profile passes only when every required check has a successful result.
Missing files, empty discovery, import failures, spawn failures and timeouts
remain failures. Inexpensive guards run before suites. There is one bounded
worker pool, at most eight workers and no more than available CPUs. The job
has a four-minute limit including setup and artifact upload; a timeout is a
failure, not performance acceptance. Native job duration must substantiate
any performance claim. Dev-lane retains one execution job; the final `lane` job
collects the selected dev/prod results without rerunning their checks.

Fast checks require Git, Python, Bash, Ruff, ripgrep, CUE and jq. Hosted setup
pins Python 3.12, CUE 0.17.1 and Ruff 0.16.8. It installs no tmux, Bubblewrap or
PHP and makes no AppArmor changes. The local integration profile additionally
requires functioning Bubblewrap for isolation behavior. Real tmux/session
rehearsals remain explicitly invoked. Local runners receive no tool installs
or changes to operator configuration.

## Evidence and trust

Every CI receipt and summary names its profile and complete required plan.
A `ci` pass means the fast plan passed; it does not claim integration or
rehearsal coverage. Switching the runner mode changes the profile and
invalidates reuse. An earlier successful fast run cannot satisfy the local
integration gate. The local runner executes that larger plan and publishes
its own original execution evidence.

Reuse remains restricted to original successful checks in GitHub artifacts
from this repository's designated CI workflow and completed job attempt.
A reused marker is not an original execution.
Validate artifact integrity, workflow and repository identity, candidate
revision, tree, ordered parents, tool identities, runner environment, worker
count, TERM_WALL, record refs and check definitions. Reuse-only markers,
malformed data, missing checks, cancelled jobs and mismatched inputs cannot
mint a pass. Canonical commits retain tree and parents while normalizing
publication metadata, allowing equivalent PR and merge snapshots to reuse
proof. Live probes always execute against the actual event checkout.
Unavailable evidence causes the required profile to execute again.

Tests use private HOME/XDG/tmp through the existing testkit. Runner/API
credentials are absent from child test environments. Tracked probe fixtures
are seeded only in the private world. No dispatcher, conductor session,
model call or additional database is a gate prerequisite.

This trial does not accept agent-authored JSON as authenticated local proof,
and does not yet make hosted CI a verifier of standalone workstation runs.
The trusted broader execution path is the existing registered local runner.
No new attestation service or signing-key setup is introduced.

## Runner selection and recovery

`hosted` or an empty variable selects `ubuntu-24.04`; `local` selects
`self-hosted`. Any other value fails workflow expression evaluation. The
adapter checks actual runner identity against the requested mode; hosted
receipts also bind the image identity. The existing hosted-spending stop is
unchanged. Local mode consumes no hosted allowance. A Gitea-only runner
registration does not accept GitHub jobs.

From an authenticated checkout with repository-variable permission:

```sh
python3 -m ops.devlane.workflow.checks.ci_runners set-mode --repo OWNER/REPO --mode local
```

Routing applies to new jobs. To recover a completed blocked run without a
source edit, including quota exhaustion:

```sh
python3 -m ops.devlane.workflow.checks.ci_runners recover --repo OWNER/REPO --mode local --run RUN_ID
gh run watch RUN_ID --repo OWNER/REPO --exit-status
python3 -m ops.devlane.workflow.checks.ci_runners inspect --repo OWNER/REPO --mode local --run RUN_ID --expect-sha ORIGINAL_HEAD_SHA
```

`recover` validates the source workflow/repository/candidate, sets and reads
back the variable, and requests a rerun. Requested or queued is not verified.
`inspect` requires exactly one successful `lane` job on the requested runner
and the original candidate SHA. Missing, duplicate, failed, cancelled,
offline and wrong-environment jobs refuse. There is no automatic retry loop.
Rerunning a historical workflow retains that revision's test plan; to use
these profiles, run a revision containing this change.

To return after the included allowance resets:

```sh
python3 -m ops.devlane.workflow.checks.ci_runners set-mode --repo OWNER/REPO --mode hosted
```

The same controls are available through repository Actions variables and
Re-run all jobs. No dummy commit or dispatch command is required.

## Dispatch and conductor retirement remains undecided

No runtime system is removed by this change. Current code still connects
`ops/bin/dispatch.sh` to the conductor wrapper, `ops/bin/ladder.sh` to the
ladder, hook-matrix dispatch rules to those entry points, and `lane_read.py`
to the conductor's operations. Sessions/courier consume coordination state.
These references show dependencies, not evidence that the whole system is
still worth keeping. A retirement decision should identify the user workflows
still needed and their replacements before cutting those entry points,
hook rules, state readers and associated tests together. Shared primitives
such as storage, schemas and configuration can remain useful independently.
