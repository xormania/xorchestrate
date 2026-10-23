# MCP tools for a conductor with less context to carry

Supersession notice, owner 2026-09-05:
[AGENTS.md §Lane boundary and precedence](../../AGENTS.md#lane-boundary-and-precedence)
governs current operation. The later-Symfony-twin / bounded-incumbent
replacement recommendation below is withdrawn. Dev tooling evolves
independently, without a Symfony application dependency or replacement mandate.
The original 2026-09-05 research body is preserved below; its measured demand
retains its original ref/date, not authority for that recommendation.

Measured 2026-09-05 against `dev` at
`c91d47a3ce165e620b6f03405d8f055e36b6a8e7`, before this feature's dispatches.
This is research and a scoped recommendation, not new pipeline law.

## What was examined

The local scan inventoried tracked lane records and reports, the canonical
dispatch store, and retained worktree handoffs. It read and hashed every file
in the following selected corpus, parsed every dispatch record, and extracted
every structured finding. Report headings and relevant passages were then
read alongside the current contracts and tools.

| Corpus | Files | Bytes | Method and boundary |
| --- | ---: | ---: | --- |
| Tracked lane text under `ops/` and `.dev/` | 323 | 3,373,583 | `git ls-files`; `.md`, `.txt`, `.json`, `.jsonl`, `.log`; excludes tests, fixtures, controls, contracts, gate-kinds and registry directories |
| Canonical dispatch `record.json` | 247 | 2,817,715 | Explicit lane state directory; full JSON parse |
| Selected dispatch artifacts | 877 | 54,603,095 | `raw.out`, stdout/stderr, launcher/breaker logs, `TRIPPED.md`, and text/JSON/Markdown under `out/`; full byte scan |
| Markdown reports within those artifacts | 43 | 718,361 | A subset of the previous row; headings and relevant passages |
| Unique retained AAR/handoff contents | 11 | — | SHA-256 deduplication across 122 retained worktrees |

No selected file was unreadable and no enumerated worktree was missing. This
is exhaustive byte and structured-data coverage of the selected corpus,
with targeted semantic reading; it is not a claim to have semantically read
every echoed prompt in 54 MB of logs. Credentials, unrelated home directories,
private provider session stores and external billing were outside the scan.
The measurements are preserved in [the research summary](reports/mcp-research-2026-09-05.json).
New dispatches after this baseline are deliberately not included.

The scan found 244 closed dispatches and three recorded as died. The jobs
were 75 implementation, 67 test authoring, 34 security, 31 adversarial
review, 22 test checking, 13 planning, three sweeps and two adjudications.
There were 2,226 structured entries in `findings`. Of these, 1,698 were P3
entries from implementation jobs: the implementation prompt requires a
command diary in that field. Consequently, a tool must preserve the job and
entry type context; summing severities does not produce a defect count.

## Repeated work with concrete evidence

| Work the conductor repeats | Evidence | Useful read |
| --- | --- | --- |
| Open records, extract findings, locate a report, copy its provenance | [Existing tool requests](mcp-tools.md), especially `record.report`; 89 review/check/ruling records in this corpus occupy 986,236 bytes and all carry notes | Retrieve a small, source-stamped page from one selected record |
| Re-read the same status while waiting | [Driver AAR](AAR-2026-09-04T17-driver.md), [context-churn analysis](../../.dev/wip/token-churn-2026-09-04.md), [subscription idea](ideas/I-019-subscriptions-and-usage-as-a-service.md) | Return only changed selected records after a caller-held checkpoint |
| Read registry JSON correctly before choosing a job | [Pipeline experiments](pipelines.md), anomaly A-5 and `job_catalogue`; current `ops/devlane/task/jobs.json` mixes direct and dispatchable jobs | A small job catalogue/card, showing required inputs and whether a scope slot exists |
| Rebuild context after a session boundary | Closeout handoff and AAR on retained branch `records/codex-closeout-20260905` at `ce2bbf97aba915fcf0b3583f5ef04dfadcbdd52e` | A future handoff packet assembled from explicitly selected records and decisions |
| Confuse a recorded reading with current authority | [MCP contract](lane-mcp-lever.md), [watch-the-watchers idea](ideas/I-017-who-watches-the-watchers.md) | Show source identity, missing/unavailable distinctions and evidence age; never infer readiness |

The current session also supplied two direct probes at the baseline SHA:

- `tools/call` for `lane_brief` with empty arguments succeeds. The same
  request with `params._meta.progressToken` returns `-32602`. Likewise,
  `resources/read` rejects an otherwise valid URI when `params._meta` is
  present. The standard permits request metadata. A connected-client call
  also returned Invalid params, but its exact frame was not captured;
  that failure alone does not prove the same cause.
- Dispatching `adjudicate` through the wrapper refuses a scope even when
  the supplied file is empty: the wrapper supplies `--scope` and this job
  has no scope placeholder. A catalogue can expose the mismatch; changing
  dispatch semantics is separate work. The README review used a bounded
  adjudicative `adversarial-review` job instead, with the reports as inputs.

Literal searches for terms about evidence navigation, polling, tool errors,
scope inputs and context reuse were used to locate passages. Their hit counts
are not incident counts: templates and transcripts repeat text. Provider
token totals are likewise not interchangeable with cost or distinct context.

## What MCP can contribute

MCP provides tools and addressable resources, so a conductor can ask a small
question first and fetch evidence only when needed. Structured tool results
can preserve machine-readable fields alongside text, and resource links can
identify larger evidence without embedding it. These are portable protocol
features, unlike a particular host's automatic tool discovery.
[MCP tools, protocol 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

The external design guidance supports workflow-shaped tools, concise useful
results, and evaluation by completed tasks as well as calls, latency and
context consumption. It also advocates retaining lightweight references and
retrieving detailed context on demand. This motivates bounded evidence reads;
it does not establish a savings percentage for this repository.
[Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents),
[Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).

GitHub's MCP server exposes selectable toolsets and individual tools, including
a read-only filter. That is a useful precedent for a small, deliberate surface
instead of exposing every internal function.
[GitHub server configuration](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md).

| Candidate | Benefit and evidence | Disposition |
| --- | --- | --- |
| `lane_findings` | Page source-stamped findings and bounded notes from one allowed dispatch; supports the repeated report-extraction work | Build now |
| `lane_changes` | Compare caller-held fingerprints with the selected records; unchanged checks return little data | Build now; stateless polling reduction, not automatic wakeup |
| `lane_jobs` | List compact job cards or fetch one, distinguishing dispatchable jobs and required input slots without dumping prompts | Build now, with explicitly configured registry source |
| Native evidence resources/templates | Stable URIs let clients revisit the exact page; discover capabilities without a filesystem path | Build alongside the selected reads if bounded and compatible |
| Handoff packet | Goal, relevant decisions, open findings, last checkpoints and missing evidence in one small response | Next: needs an explicit selection/decision contract |
| CI evidence packet | Compare recorded checks/reviews with the exact head and expose stale evidence | Next: a separate observer must acquire GitHub state; this server must stay network-free |
| Dispatch precheck | Detect missing inputs, snapshot omissions and unusable job/scope combinations before paying for a run | Next: share the dispatcher's real validator through a declared read boundary |
| Pipeline yield | Duration, invalid envelopes, retries and upheld findings by lineage/stage | Next: distinguish diaries, duplicated findings and vendor usage semantics first |
| Attempt and artifact state | Separate process exit, envelope validity, verdict and report-write availability; locate a saved artifact even after an interrupted read job | Next: the pipeline observations below supply a direct example |
| Selected artifact index | Names, byte sizes, hashes and bounded retrieval of explicitly allowed reports | Next: declare which artifact types may be read; never accept caller-supplied filesystem paths |
| Failure neighbourhood | Retrieve a failure plus its nearest positive control and prior upheld ruling | Explore after precise record retrieval; evidence relationships need explicit indexes |
| Native subscriptions | Tell a supporting client that a selected resource changed | Separate extension; requires a running observer and client support, and does not promise an idle model turn |
| Resource argument completion | Suggest allowed dispatch IDs or job names while a person selects evidence | Optional client convenience; no global filesystem search |
| Interactive conductor board | A host-rendered lane timeline with source/detail drill-down and stale-source indicators | Explore as an optional MCP App; same read service, no hidden actuation |

Resource subscriptions are negotiated capabilities, and completion has its
own declared capability and request shape. Neither is implicitly available
because a server speaks MCP.
[Resources](https://modelcontextprotocol.io/specification/2025-06-18/server/resources),
[Completion](https://modelcontextprotocol.io/specification/2025-06-18/server/utilities/completion).
MCP Apps can associate a tool with an interactive HTML resource rendered by
a supporting host. A board could keep visual navigation out of long textual
transcripts, but host compatibility and context savings would need a probe.
[MCP Apps overview](https://modelcontextprotocol.io/extensions/apps/overview).

## Reuse and boundary

The existing `lane_read.py` is the shared CLI/MCP read service. Extend that
boundary, keeping record allowlisting, bounded regular-file reads, no writes,
no network and no launcher/monitor-writer imports. The schema/config evolution
must be explicit and preserve existing version-1 configurations. A job card
is evidence about registry bytes, not a promise that a launch will succeed.
Untrusted finding text stays data, never instructions or shell commands.

The Symfony reuse question was re-measured with `composer.lock`: this tree
has `symfony/ai-mate` and its Symfony extension at `v0.13.0`; neither
`symfony/mcp-bundle` nor `mcp/sdk` is installed. Symfony documents an MCP bundle
using the official SDK with stdio/HTTP and tools/resources/prompts. That is
the candidate for the later Symfony twin, not a reason to introduce a second
server during this bounded incumbent-lane change. Current upstream docs do
not prove support in this repository's installed packages.
[Symfony MCP bundle](https://symfony.com/doc/current/ai/bundles/mcp-bundle.html).

The transport should accept object-valued request `_meta` independently of
domain arguments, while continuing to refuse unknown tool arguments. Keep
the server's declared protocol versions unless a separate compatibility probe
supports a change; 2025-06-18 is its current newest version, not the newest
published MCP specification.
[MCP metadata](https://modelcontextprotocol.io/specification/2025-06-18/basic/index).

## What will count as success

Independent tests must ask conductor questions: find the requested finding
and source revision, distinguish no findings from unavailable evidence,
detect a changed record without re-sending unchanged ones, refuse stale page
cursors, and identify valid job inputs. Test positive controls beside refusal
cases, including unselected paths/IDs and malformed sources. Compare CLI,
tools and resource payloads over the real stdio transport.

Measure serialized response bytes for a fixed finding-retrieval task, an
unchanged check and a job lookup against their raw-source baselines. Preserve
answer correctness and state what was excluded; report bytes, not estimated
billed tokens. Run `ops/bin/suites.sh`, independent quality and security
reviews, and CI before presenting the new PR.

The existing ladder automatically enters test/code fixes when a review says
changes (`tools/ladder.py`, `Driver.run` at the measured SHA), with no
adjudication stage. Cross-review law requires a separate ruling first. This
pipeline therefore uses the existing dispatcher and land lever stage by
stage, retaining records and pausing for rulings when needed. It does not
change the ladder or bypass the role separation. Whole-tree launcher snapshots
remain a declared custody limitation: prompts withhold implementation reads,
but the existing implementation files are physically present.

## Subsequent observations from this pipeline

Measured 2026-09-05 after the baseline scan, from the canonical records named
in the [pipeline report](reports/mcp-pipeline-2026-09-05.md): the two planning
attempts left useful contract/plan files but no final envelope, and the
Daybreak ruling completed with process exit 0 while reporting `status:
tripped` because its sandbox denied a separate report-file write. Its
supervisor recorded `tripped: false`. These are different facts, and a future
attempt-state projection should preserve them separately.

The current collector only accepts failed write jobs. A compact artifact
index could make the saved read output visible without reconstructing paths
or misreporting the overall run as accepted. The three reads in this slice
do not constitute that index: findings come from the envelope, and their
recorded top-level status is not an envelope-validity or readiness decision.
