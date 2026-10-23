# Continuous integration

[ci.yml](workflows/ci.yml) runs on pull requests targeting any branch, pushes to `dev` and `master`, merge groups, and manual invocation.
Pull-request edits rerun CI, including base-branch changes, so retargeting a stacked
PR recomputes its selected suites against the new base. Title and body edits also
rerun the same checks.
The workflow always starts so its required `lane` check can report a result.
The `changes` job selects suites using [ci-paths.json](ci-paths.json):

| Job | Checks |
| --- | --- |
| `changes` | Tests CI routing, the aggregate gate and PHP cache integrity; selects jobs from the Git revisions; checks CUE exports and rejection cases, registry/configuration/tool/skill consistency, Ruff and its security-rule detection before selected dev-lane jobs can start. |
| `live-mcp` | Python service, stdio MCP, native protocol, Git and GitHub fixture tests, with checked discovery, an execution/skip census and the ten slowest tests listed in the log. |
| `maker-mate` | Real installed Maker/Mate integration through normal MCP in a disposable job worktree. Runs its generated kernel test through `work_check`, checking the test environment and test service container. |
| `symfony / php` | Product checks: locked Composer metadata/platform, PHP/YAML/container lint and the full application PHPUnit suite. Uses [ci-prod-lane.yml](workflows/ci-prod-lane.yml). |
| `serena` | Build the provider image from the pinned Serena source and exercise real REPL persistence, PHPactor navigation, edits, protected paths, pause/resume, checkpointing and provider shutdown after release. |
| `lane` | Requires successful selection and every selected job to succeed. Accepts a skipped job only when the selector explicitly excluded it. |

The Symfony job uploads its tested revision, dependency observations, PHPUnit
results as a CI artifact. Maker/Mate retains separate revision and integration
evidence. The Serena job records its build and integration results in the logs.

Both PHP jobs retain streamed PHPUnit events, including the active test when a
process crashes. They also record the PHP binary hash, extensions, loaded INI
paths and selected OPcache/JIT/stack settings. This uses selected runtime facts,
not `phpinfo()` or an environment dump. Maker/Mate measures the filtered child
environment used by `work_check`; its `result.json` and event log are copied out
before the disposable worktree is deleted, including on failure. Signalled
development checks retain the signal name alongside their negative exit code.

## File filters

| Changed files | Selected suites |
| --- | --- |
| Shared `devtools/` code, agent instructions and skills | `live-mcp`, `maker-mate`, `serena`; no product suite. |
| Python test modules and native/HTTP harness fixtures | `live-mcp` only. The shared `devtools/tests/support.py` client still selects both integrations. |
| Maker/Mate integration / Serena integration or image | `live-mcp` plus the corresponding integration. |
| Application code/configuration, Composer, PHP environment, Mate or PHPUnit configuration/bootstrap | Product suite and `maker-mate`. |
| Other product tests, public files, assets, templates and translations | Product suite. |
| `ruff.toml` / `.phpactor.json` | `live-mcp` / `serena`, respectively. |
| Ordinary Markdown documentation and listed repository metadata | No suites; selection tests and `lane` still run. Agent instructions/skills remain tested inputs. |
| CI machinery, `.gitignore`, unclassified paths or manual invocation | All suites. |

Rules use case-sensitive Python `fnmatch` patterns: `*` also matches `/`, so
`devtools/*` includes nested files. `ignore` takes precedence, then `all`, then
the union of matching jobs after each job's `exclude` patterns. Unclassified paths select everything so new code
cannot silently lose coverage. Add an explicit rule when adding a component.
The workflow summary lists selected and skipped jobs.

Selection uses the full Git history without GitHub's changed-file API limits.
PRs compare their tested merge commit with the event's base SHA, including job
PRs targeting work orders. Pushes compare the event's entire before/after range;
merge groups compare their base/head commits. Renames include both old and new
paths, and deletions count. Manual runs, initial pushes and unavailable Git
comparisons run everything. A selector failure, missing output, or failed,
cancelled or unexpectedly skipped selected job makes `lane` fail.

Maker/Mate still installs the locked PHP development dependencies to exercise
the actual tools. Its fixture configures only the generated kernel test as a check,
with PHP's environment variables enabled (`variables_order=EGPCS`) so a forced
application environment cannot be hidden by the interpreter's defaults;
normal agents retain their default container, YAML and application test checks.
The product workflow can also be invoked manually on its own.

## Python security lint

The existing pinned Ruff checks `devtools` and `.github` recursively, including
nested action scripts. [ruff.toml](../ruff.toml) explicitly selects reviewed
Bandit-derived rules for unsafe execution/deserialization, archive extraction,
temporary files, permissions, weak hashes, TLS/SSH verification and shell
injection. This is a bounded static check, not every `S` rule or a substitute for
runtime authorization tests, dependency auditing or manual review. SQL/URL
construction and other unselected rules still require review.

Security findings fail the existing lint step and therefore the required `lane`
gate. There is no blanket baseline or test-directory security exclusion. The
trusted runner-script loader has one documented, rule-specific `S102` exception
on its `exec` line; future exceptions should be equally narrow and explained.

After lint, [lint_checks.py](lint_checks.py) uses the installed Ruff, the actual
workflow lint arguments and repository configuration to check unsafe and safe
temporary samples. It verifies the selected security rules and discovery of
nested `.github` code. Missing Ruff, an undetected unsafe sample, or a failing
safe sample fails the check. The earlier CI routing tests remain stdlib-only.

## Scheduling and caches

Selection and lint finish before the selected suites start in parallel. Product
checks do not wait for the Python suite, and integrations do not wait for each
other. An updated PR cancels its obsolete run. Pushes, merge groups and manual
runs keep separate concurrency groups: push filters cover only that event's
delta, so cancelling an earlier push could discard its selected coverage.

When `live-mcp` is selected, `changes` installs pinned CUE v0.17.1 and runs the
role-package and shared-configuration CUE rejection tests explicitly, alongside
export and consistency checks. `AGENTSCIENT_CUE=cue` makes a missing compiler fail
these checks. The remaining role-package tests run with the full Python suite in
`live-mcp`; that job may skip the two CUE cases when CUE is unavailable because
`changes` has already required them. The full suite uses strict execution with
only those two exact test IDs permitted to skip. Unexpected skips, skipped
subtests, expected failures, incomplete execution and an empty run fail the job;
the retained census still records every permitted skip. Local runs remain
permissive unless `--strict` is requested.

| Cache | Local runners | Hosted runners |
| --- | --- | --- |
| Ruff downloads | pip's existing local cache | `setup-python` pip cache, keyed by [requirements-ci.txt](requirements-ci.txt). |
| Composer package downloads | Composer's existing local cache | GitHub cache keyed by OS, architecture and `composer.lock`, with a platform fallback. |
| Pinned PHP and Composer executables | `RUNNER_TOOL_CACHE/agentscient-php` and `agentscient-composer`, keyed and verified by SHA-256 on every use. | The same verified installer; fresh runners download from the retained release. |
| Serena Docker layers | Existing Docker builder cache | Buildx with the GitHub Actions layer cache. |

Only downloaded dependencies and build layers are cached. Each job still installs
locked Composer dependencies into its own checkout/worktree and executes its
selected tests. No application `vendor/`, runtime state, credentials, or passing
test results are stored in these caches. A damaged PHP archive is downloaded and
verified again before use; an invalid download fails the job.

Serena uses a unique image tag per run and attempt, passed through to the integration
and removed afterwards. Concurrent jobs sharing the WSL Docker daemon cannot
replace the image another job is about to test. Layer caching remains available.
Serena's PHP base and uv installer use immutable image digests. APT uses only
signed, dated Debian snapshots and fails on any metadata fetch error. Python
runtime dependencies come from the pinned Serena revision's own `uv.lock`;
build dependencies have a separate complete hash lock and build isolation is
disabled. The build checks dependency compatibility and unchanged upstream lock
metadata. PHPactor remains checksum-verified. These inputs fix dependency
selection per platform; CI verifies the resulting image's behavior, without
claiming byte-identical OCI builds. See the [image maintenance guide](../devtools/serena/README.md)
for updates, lock regeneration and cold-build acceptance.

Test fixtures use short HTTP shutdown polling and explicit release markers for
work that must remain running through an MCP disconnect. Assertions wait for the
state they need; CI does not retry failed tests to obtain a green result. If a
runner loses Docker or PHP crashes, inspect the failing step and retained evidence
as an environment/runtime failure before labelling it a test flake.

The stdlib unittest entrypoint in `devtools/tests/run.py` rejects empty discovery,
import errors and duplicate test IDs before running cases. It does not require a
fixed test count. The log and retained `test-census.json` report collected cases,
executed cases, skipped cases and cases not run because setup failed. Class and
module setup skips expand to their collected methods; a skipped subtest does not
make its containing method disappear. An import-time skip is reported as the
single discovery placeholder available to unittest. Expected failures and
unexpected successes retain unittest's ordinary meaning. Standard unittest
discovery and individual test selection remain supported.

The small fault-calibration tests run three existing boundary assertions once
against healthy code and once with a temporary service-seam fault: bypassed stale
completion verification, fallback to the first runtime profile, and unrestricted
Arbitrator operation reads. Each mutation must fail the intended assertion;
setup errors, unrelated failures and skips do not count as detection. These
offline checks establish sensitivity to those faults, not comprehensive mutation
coverage or authenticated harness/model acceptance. Race tests synchronize at
the competing channel poll, output lock or SQLite write attempt rather than
using a short quiet period as evidence that an action was blocked.

The bootstrap environment handoff test requires Linux abstract Unix sockets,
independently of tmux. Constrained local environments may skip this test after a
socket capability probe; `AGENTSCIENT_REQUIRE_ABSTRACT_SOCKETS=1` makes an unavailable
socket fail `live-mcp`. Real terminal tests separately require tmux through
`AGENTSCIENT_REQUIRE_TMUX=1`.

## Optional Jev failure diagnosis

The failed test/integration command in `live-mcp`, `maker-mate` and `serena`
can run two advisory Jev questions on its runner: classify the likely failure
cause and select a useful excerpt from the captured log. The original command
exit status and required `lane` result are preserved. No test is retried,
skipped, accepted or repaired by a diagnosis. Setup/build failures and cancelled
or timed-out jobs are outside this initial command-capture coverage.

Create the repository Actions secret **`TYPESAFE_API_KEY`** under **Settings →
Secrets and variables → Actions → New repository secret**. The workflow passes
it only to the diagnostic step, on either runner type. No key belongs in the
checkout or JSON configuration. Fork/Dependabot runs without this secret retain
an unavailable assessment. Successful checks make no Jev calls.

[jev-ci.json](jev-ci.json) owns enablement, the failure categories and questions,
confidence threshold, log matching and bounded evidence selection. It references
[the shared service configuration](../devtools/configuration.cue) for the pinned model and transport
settings, using the same client and credential filter as the MCP advice tools.
The initial confidence threshold is a configurable presentation threshold, not
a measured accuracy guarantee. Low confidence becomes `unknown` or no selected
excerpt; the original probabilities remain in the artifact.

Logs stay on the runner during collection. The client sends bounded, redacted
candidate windows and allowlisted `check-*.json`, `result.json` and
`php-runtime.json` evidence, plus bounded recent events from retained fixture
`telemetry/*.jsonl`, to TypeSafe. Fixture paths stay attached to those events;
the rubric distinguishes intentional fixture errors from the actual failed check.
Early error windows and late failures both get candidates. Logs exceeding
`limits.log_bytes` split that retained byte budget between the head and tail.
The omitted middle is scanned in bounded chunks only to count line endings,
so tail excerpts keep their original line numbers. Cut boundary lines are
dropped before redaction, and excerpts never join across the omitted gap.
Truncation and omitted candidates/files are recorded;
symlinks and unreadable/oversized JSON documents are excluded. JSONL traces use
a bounded tail window instead of loading the full trace. The shared filter
redacts known environment credentials, sensitive JSON fields and bearer strings;
it cannot identify every possible secret in arbitrary log text.

Each assessment is tied to the tested `GITHUB_SHA`, run, attempt and job. The
seven-day `jev-ci-<job>-<run>-<attempt>` artifact contains the exact sanitized
request, validated response, both policy snapshots, input fingerprint, provider
usage and elapsed time, plus a short Markdown summary. This provides replayable
inputs for later comparison with confirmed diagnoses. There is no persistent
answer cache or automatic accuracy labeling in this change.

The selected excerpt is literal redacted log text with original line numbers.
It appears in a job warning annotation and step summary; existing CI feedback
can retrieve the annotation. The default excerpt cap fits its 1,500-character
annotation preview; larger configured excerpts remain complete in the artifact.
Raw capture files are not included in the new Jev
artifact. Existing runtime and integration evidence retention stays in place.
Provider errors, invalid responses, missing logs or missing credentials are
reported as unavailable; diagnosis/upload steps are advisory even if they fail.

Offline `.github/tests` exercise the actual workflow capture commands and shared
HTTP client's documented Choice wire format. Fixtures establish wiring and
failure preservation, not live diagnostic accuracy. An authenticated failed run
is needed to assess the latter. See the [TypeSafe API](https://docs.typesafe.ai/api)
and [pinned models](https://docs.typesafe.ai/models), checked on 2026-09-29.

## Runner selection

The repository variable `CI_RUNNER_MODE` selects execution:

| Value | Runner |
| --- | --- |
| `hosted` or unset | GitHub-hosted Ubuntu 24.04 |
| `local` | Registered self-hosted runner |

Any other value fails runner selection. The same jobs run in either mode.
The shared PHP setup requires Linux/X64 on both runner types. Runners need Python 3.12+, Git, Docker and
the download/archive tools used by [php-runtime](actions/php-runtime/action.yml).
That action supplies temporary pinned PHP and Composer, regardless of global
installations. Composer invokes the PHP beside it; global executables are not
changed. Select runner mode as an operator choice.

## Retained PHP and Composer executables

[toolchain.json](actions/php-runtime/toolchain.json) pins PHP 8.5.11, Composer
2.10.3, their SHA-256 digests, asset names and the dedicated GitHub Release tag.
Both PHP jobs install the same binaries through the shared action. PHP retains
the previously approved archive digest; a changed upstream file is not accepted.
Composer no longer uses a floating installer or whichever version is on the runner.

Normal installation downloads only from the retained release in this repository,
using the job's `contents: read` token for private asset access. Upstream URLs are
provenance and inputs to explicit publication, never installation fallbacks.
Credentials are excluded from redirected asset-storage requests. A valid warm
cache needs no release request. Missing/corrupt entries are downloaded, checked
and atomically published to the cache before isolated job installation.
`toolchain.py` owns downloads and cache publication; `install-php.sh` only verifies
and extracts the selected local PHP archive into a fresh runtime. A missing or
changed archive at extraction fails without a second download path.

[Retain CI toolchain](workflows/retain-toolchain.yml) is an operator-only manual
workflow, separate from pull-request CI. Its publication job alone has
`contents: write`. It reuses a verified runtime cache or verifies upstream input,
creates a draft release, uploads missing assets, reads both back into an empty
directory and verifies their digests before publication. An interrupted draft can
resume; existing assets are never replaced. It then verifies a cold installation
on the selected runner and GitHub-hosted Ubuntu using read-only asset access in
the hosted verification job. This is a toolchain release, not a product release,
and does not advance `master` or mark itself as the latest product release.

For an upgrade, review new versions/digests and choose a **new release tag** in
`toolchain.json`; publish and pass cold verification before merging the consumer
change. Run the manual workflow from the reviewed ref once the workflow is
available on the default branch, or run `toolchain.py publish` in an authorized
operator Actions job. The first publication recovered the original pinned PHP
archive from an existing runner cache. Never solve an upstream mismatch by
silently adopting the new bytes or repacking an executable to guess its checksum.

GitHub release immutability is a repository setting; the initial retained release
is not platform-locked. The publisher never overwrites assets, and installations
always enforce the independently reviewed digests, including on cache hits.
Enable immutable releases before publishing future toolchain tags if a platform
lock is desired. This does not require AWS/S3. Composer dependency archives remain
in the existing package-download caches; dependency installation and update
metadata discovery still use their configured upstream repositories.

## Local verification

From the repository root, use the relevant checks:

```sh
python3 -m unittest discover -s .github/tests -v
python3 -m devtools.tests.run -v --durations 10
uv tool run --from ruff==0.16.8 ruff check devtools .github
uv run --with ruff==0.16.8 --no-project python .github/lint_checks.py
```

The Ruff command uses `uv`; an installed Ruff matching the workflow pin also works.
The integration scripts require the installed application dependencies or the
built Serena image described in [setup](../docs/local-agent-readiness.md):

```sh
python3 devtools/integration/symfony.py
python3 devtools/integration/serena.py
```

To investigate the complete agent check sequence in a disposable Maker/Mate
worktree, including the application tests alongside the generated test:

```sh
python3 devtools/integration/symfony.py --full-checks --evidence-dir var/ci/php-diagnostic
```

This is an explicit diagnostic invocation. Normal CI keeps the generated-test
integration separate from the full product suite; neither mode retries failures.

They use disposable repositories/worktrees. The Python suite uses native protocol
and GitHub HTTP fixtures; the integrations execute real Symfony and Serena code.
Neither proves authenticated model behavior. Record that separately through the
[local acceptance run](../docs/local-agent-readiness.md#authenticated-local-acceptance).

## GitHub settings

Configure these as the repository owner in **Settings → Rules → Rulesets**:

| Target | Rules |
| --- | --- |
| `dev` | Require a pull request and successful `lane` from GitHub Actions; block force pushes and deletion. |
| Include `work-order/*`, exclude `work-order/*-job-*` | Require a pull request and successful `lane` from GitHub Actions; block force pushes. Allow branch creation and deletion. |

In the active **work-order** ruleset's **Target branches**, include `work-order/*`
and add an **Exclude by pattern** entry for `work-order/*-job-*`. Both generated
names have one slash: `work-order/<wo>` is protected, while
`work-order/<wo>-job-<job>` accepts normal, non-forced pushes of review fixes.
For example, `work-order/123-job-7` opens its PR into `work-order/123`, and that
work-order branch opens its final PR into `dev`. The exclusion is necessary:
without it, the broad include would require a PR just to update a job branch.
Ensure other active rulesets or classic protections do not independently require
PRs on job branches; an exclusion applies only to the ruleset containing it.
Leave **Restrict creations** and
**Restrict updates** off. For the work-order status check, select **Do not require
status checks on creation**, so a new branch can start at the tested development
revision. Keep the required aggregate named `lane`; remove obsolete required
contexts only after checking they are no longer produced by current CI.

The workflow tests each job PR against its work-order base and the final work-order
PR against development. Pushing a review fix to the same job branch triggers
`pull_request.synchronize`, reruns CI on the updated PR and leaves the merge into
the work order blocked until its required `lane` check passes. The MCP merge
service rechecks both CI and GitHub's merge eligibility before requesting a merge;
it does not use credential bypass authority to ignore reported requirements.
**Require branches to be up to date before merging** is
optional additional strictness: it can require refreshing jobs and an integration
job before final delivery when development advances. No service bypass is needed.
The runtime honors GitHub's merge eligibility and never configures these rules.

Under **Settings → General → Pull Requests**, leave at least one supported merge
method enabled. Merge commits preserve the relationship between sibling jobs;
squash and rebase remain supported, with merge evidence retained separately.
Optional **Automatically delete head branches** removes remote job branches after
their PRs merge and the work-order branch after its final merge. Local cleanup is
independent. This PR does not change release/master or archive protections.

Required approving reviews are an owner choice. The runtime's audit comments are
not approvals from another GitHub identity. Do not grant the automation an owner
bypass merely to satisfy a review count.

References: [creating rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository),
[available rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets),
[required status checks](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks).
