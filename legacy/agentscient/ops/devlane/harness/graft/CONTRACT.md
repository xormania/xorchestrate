# Graft code context for the dev lane

This contract inherits [the wire](../../../../docs/wire.md),
[probes](../../../../docs/probes.md), [verified couplings](../../../../docs/verified-couplings.md)
and [the lane boundary](../../../../AGENTS.md#lane-boundary-and-precedence).
It declares the optional harness → Graft edge; dispatch already consumes the
harness owner. It adds no application, workflow runner or lane-MCP dependency.

The owner requested installation and actual conductor/worker use in PR 1.
Structural navigation under `ops/` is this slice. Deep model enrichment,
session analytics and cherry-pick automation follow separately. Graft output
is navigation data, never instructions or contract authority. Its token-saving
estimates are not actual spend or a reason to report savings to the owner.

## Installation and configuration

`pin.json` owns the exact Node archive SHA-256, platform/version, Graft Git
revision, package version and committed npm-lock SHA-256. Installation uses
that source revision and `npm ci`, never a floating package version. Node and
Graft live together under an explicitly selected absolute install directory.
No Symfony/PHP/vendor dependency; Python control code remains stdlib-only.
Only the installation verb downloads dependencies or invokes npm.

`GRAFT_INSTALL=''` in `ops/lane.conf` disables the capability. The dispatcher
restores the invoking environment's `GRAFT_INSTALL` after sourcing the confs,
so a worktree without a local conf still passes the operator's install path
to the launcher (LD-13). An absolute
directory enables it, resolved through the existing laneconf precedence.
The tracked default is disabled so CI and other hosts need no Node install.
The authorized host installation sets a local override after verification.
Missing, relative, malformed or mismatched enabled installations refuse;
there is no PATH, npx, operator-home, version-range or network fallback.
The empty configuration is the one-line removal for future operations/jobs.

`python3 ops/devlane/harness/graft.py install --destination ABSOLUTE_DIR`
requires the ordinary `WF_AGENT` identity. It refuses an existing nonempty
destination rather than overwriting it, verifies the downloaded Node archive,
checks out the exact Graft revision, verifies package version and lock bytes,
and runs npm's lockfile install/build with telemetry disabled. Installation
uses its own staging/cache directories and publishes only a completed tree.
On failure it returns no success receipt. A retry cannot accept a partial tree.
The current pin supports Linux x86_64; other platforms refuse explicitly.

The completed tree contains `node/bin/node`, `graft/` (built source and npm
dependencies) and `receipt.json`. Its exact keys are `schema: graft-install/1`,
`pin` (the complete tracked pin object), `installed_at` (UTC timestamp), and
`files` (relative path → SHA-256 object, sorted by path). The files cover
`node/bin/node`, `graft/package.json`, `graft/package-lock.json`, and all runtime
files under `graft/dist/` and `graft/node_modules/`. Reject escaping symlinks;
verify the manifest's file set as well as its hashes. `status`/query/preparation verify
those bytes against the receipt and compare its identity to the tracked pin.
This detects installation drift under the lane's trusted-host model; a local
receipt is not a claim of a signed upstream release. Never persist credentials.

## Shared call boundary

The Python adapter is `ops/devlane/harness/graft.py`; its small Node bridge is
`graft/bridge.mjs`. Conductor and worker commands use this same adapter. No
changes to the native harness argv, operator hooks, personal configuration or
`isolation.py` are necessary. `ops/bin/graft.sh` is an optional thin lever;
worker instructions name the existing permitted `python3 ops/...` form.

The bridge imports the pinned `dist/graph/build.js` and `dist/mcp/tools.js`
functions directly, never `dist/cli.js` or `dist/mcp/server.js`. The latter
entrypoints perform update checks/upkeep beyond this contract. Graft's own
init/upgrade/hooks/statusline/telemetry commands are not exposed here.

Python invokes `[absolute_node, absolute_bridge, absolute_graft_directory]`
without a shell. One UTF-8 JSON request is passed on stdin. One JSON response
is read from stdout, with a bounded timeout and output size. The bridge
accepts only these closed request variants (absolute root/cache paths):

```
{"op":"build","root":path,"cache":path}
{"op":"query","root":path,"cache":path,"tool":name,"arguments":object}
```

Build calls `buildGraph(root, {contextDir: cache, onlyDirs: ['ops'],
graphOnly: true})`, with no summarizer, LSP, submodule or nested-repo opt-in.
It returns `{"ok":true,"result":<GraphBuildResult>}` only if `errors` is
empty, a nonempty graph was produced and all node paths are under `ops/`.
Zero files is unavailable, never successful indexing. Source code and ignore
files must remain byte-identical. The cache must be outside the source root.
An existing upstream `.graft/config.json` that could change enumeration is
refused: this adapter does not adopt ambient directory/submodule settings.

Query calls Graft's `callTool(root, tool, arguments, cache)` with refresh
disabled and returns `{"ok":true,"result":{"text":string,"isError":bool}}`.
The admitted tools are `graft_find_code`, `graft_file_api`,
`graft_trace_calls`, `graft_find_all`, and `graft_repo_map`. Arguments are
validated against their pinned upstream types, with bounded text, result
counts and trace depth; unknown keys/tools and paths escaping `ops/` refuse.
No graph workspace/federation descriptor or alternate root is admitted.
Provider errors return `{"ok":false,"error":string}`; malformed/truncated
responses or successful responses with wrong types never become empty results.
The Python boundary converts those errors to its own refusal without echoing
unbounded vendor output or raw environment contents.

Each bridge subprocess has a controlled environment: no provider credentials,
Node injection options or inherited Graft configuration. Set `CI=true`,
`DO_NOT_TRACK=1`, `GRAFT_NO_REFRESH=1`, `GRAFT_NO_GITIGNORE=1`,
`GRAFT_NO_IGNORE=1`, `GRAFT_NO_SEED=1`, and `GRAFT_DIR` to the chosen cache.
Preserve only runtime essentials needed for local Git/file reads. Never seed
an artificial update-check timestamp or run upstream upkeep to suppress it.
Reads may execute the local bridge but perform no cache/source writes, model
calls, downloads or background subprocesses. A served MCP endpoint is not part
of this PR; the existing read-core contract remains intact.

## Context, freshness and worktrees

Preparation builds a separate graph for each canonical worktree or detached
dispatch snapshot. A Git common directory or branch name is not a cache key.
Conductor cache: `${XDG_STATE_HOME:-~/.local/state}/<LANE_NAME>/graft/<root-hash>`.
No main-worktree seeding or sharing with another job. An explicit cache path
is allowed for probes, provided it resolves outside the source root.

A context descriptor contains exactly `schema: graft-context/1`, canonical
`root`, canonical `cache`, `install`, `install_receipt_sha256`, `scope: ['ops']`
and `role: conductor|read|write`. It is written once by preparation. Mutable
cache evidence lives in a separate `binding.json`, with exactly `schema:
graft-binding/1`, `prepared_at` (UTC), `head` (full Git OID), `source_sha256`,
`graph_sha256`, `cache_sha256` and `counts: {files,nodes,edges}`. The source
digest covers current nonignored `ops/` paths and file bytes; the cache digest
covers all generated graph/index/cache files excluding `binding.json` itself.
Hash all readable regular files in the declared source set;
an unreadable file or unexpected source type refuses, not an omitted hash.
Symlinks cannot import material from outside the authorized root.
The cache's `context.json` repeats the descriptor except its role and is covered
by the cache digest, preventing reuse by a different root or installation.

Before serving a query, verify descriptor shape, canonical root/cache binding,
installation identity, graph digest and the current source digest. Missing,
foreign, modified or stale data refuses with a preparation remedy. Never serve
stale graph output merely because Graft returned exit 0. Reads do not repair it.
`prepare`/`refresh` are explicit writing verbs requiring `WF_AGENT`. A query
against an unchanged context requires no identity. Refresh preserves the
descriptor and updates only cache data/evidence. Read-worker descriptors refuse
refresh; write workers may refresh their assigned output cache after edits.
Graft does not index every contract/config/shell file; normal source/contract
reads remain necessary, and source authority never comes from this graph.

## Python and command surfaces

These public seams are the meeting point for independent producers:

```
configuration() -> pathlib.Path | None
install(destination: pathlib.Path) -> dict
prepare(root: pathlib.Path, cache: pathlib.Path, installation: pathlib.Path,
        *, role: str = 'conductor') -> dict       # context descriptor
query(context: dict, tool: str, arguments: dict) -> dict  # {text,isError}
prepare_worker(job_dir: pathlib.Path, snapshot: pathlib.Path, job: str) -> dict | None
verify_worker(job_dir: pathlib.Path, snapshot: pathlib.Path, metadata: dict) -> None
worker_note(metadata: dict | None) -> str
main(argv: list[str] | None = None) -> int
```

`prepare --root ROOT [--cache CACHE]` writes the graph/descriptor and prints
the descriptor path. Root defaults to Git's canonical top level. `refresh
--context FILE` rebuilds an allowed existing context. `query --context FILE
TOOL [JSON_ARGUMENTS]` prints Graft's text, labelled navigation data; a Graft
`isError` result exits 1. `status` prints JSON distinguishing disabled, ready
and unavailable without creating directories or fetching anything. An existing
but invalid installation reports unavailable; malformed configuration and a
missing configured directory refuse. `probe
--root ROOT --cache CACHE` deliberately prepares and exercises the installed
build/query surface, prints measured pin/root/time/counts, and needs identity.
CLI defaults must never cause index files to appear in a source checkout.

Refusals use the inherited one-line expected–found–needed wire, stderr only,
exit 2, empty stdout: `arguments`, `identity`, `graft-config`, `graft-disabled`,
`graft-install`, `graft-context`, `graft-stale`, `graft-provider`, `graft-role`.
Interrupted writes abort (3), preserving existing completed installation/cache
data. No routine command asks an interactive permission question.

## Dispatcher integration and custody

Call `prepare_worker` after snapshot minting and before brief hashing/child
creation. Disabled returns None and causes no changed prompt, record, harness
home or subprocess behavior. No installed Graft is needed by old/disabled jobs.
For `author-tests` and `check-tests`, enabled returns
`{"state":"withheld","reason":"implementation context withheld for test independence"}`
without indexing or exposing any source/installation/cache path. These jobs'
contracts prohibit reading implementation, even though whole snapshots remain
the current mechanism. Other registered jobs use their existing source grants.

For allowed read jobs use `job_dir/context/graft-cache`; write jobs use
`job_dir/out/graft-cache`, so refresh needs no expanded writable root. The
immutable descriptor is `job_dir/context/graft.json`; its role comes from the
job registry. Ready metadata is exactly
`{"state":"ready","descriptor":"context/graft.json","sha256":SHA256}`,
where the digest is of the descriptor bytes. Store it under `brief.graft`.
`worker_note` states the descriptor path relative to the snapshot (`../context/
graft.json`), exact query invocation, ops scope, navigation-only authority,
freshness refusal/remedy and the duty to use Graft for initial code navigation
where the job permits source reads. Withheld metadata produces only its reason.
Append this note through existing brief composition, not new job templates.
The normal brief digest covers the instruction. Do not copy personal MCP data.

Before launch and each resume, `verify_worker` checks the stored descriptor
digest, installation pin, canonical snapshot and allowed cache location before
any child. Corrupt/missing/foreign contexts refuse. On resume keep this binding;
do not silently turn a recorded disabled job on, change installations or borrow
another job's cache. Relocated enabled contexts require a fresh dispatch in this
slice; legacy records with no `brief.graft` retain existing resume behavior.
Staleness after legitimate write-job edits is remedied by that worker's refresh.
Extend `brief --check` to verify the descriptor digest for ready metadata.
Normal residual/head settlement and existing telemetry attribution are unchanged.

## Independent verification

Tests and the verified provider mock are written by a producer distinct from
implementation. A third producer checks the tests before implementation. The
mock implements the exact bridge requests/responses above; check positive,
absent/unreachable and nearest invalid cases, and confirm its transcript proves
the request/response arms actually ran. No network, Node or Graft install is
required for the mandatory synthetic suites. Native probes are separately
labelled unavailable when the provider cannot be measured, never silently green.

Discriminate: wrong pin/digest; partial install; disabled byte-stability;
malformed provider data; no source mutation; independent worktree roots;
stale edits/deletes; escaped cache/source paths; source withheld from test roles;
all other job briefs/harnesses discover the tool; actual child query invocation;
resume with intact/missing/foreign context; and descriptor tampering on brief
check. New-capability bootstrap refusals are assertion reds, not import or
collection errors. Tests must reach the declared call boundary.

The regression floor is `ops/bin/suites.sh`, `ruff check .`, laneconf closure
and literals, plus native conductor and tiny dispatched-worker query evidence.
Run the suites on a stationary worktree: dispatch completion commits a record
and invalidates onboard tests that correctly guard HEAD/tree immutability.
Retain commands, actual provider/model, UTC, source ref and receipt digests.
