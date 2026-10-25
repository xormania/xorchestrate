> Historical source guidance, preserved for reference. The Xorchestrate root
> AGENTS.md governs this repository; the instructions below are not active policy.

# AGENTS.md — the dev-lane (dispatch/task/harness/telemetry)

The Agentscient dev-lane. The machinery: `dispatch/` launches harness workers,
`task/` holds the job registry, `harness/` proves isolation, `telemetry/`
reads usage, `fixtures/` supplies synthetic replay data.

Read [the root lane boundary](../../AGENTS.md#lane-boundary-and-precedence)
and [ops/README.md](../README.md) first. This tooling must operate without
the Symfony application or its vendor directory and evolves independently.
Application twins do not require its retirement. Work profile is separate
from harness and conductor/worker role; application work dispatched here
still follows the prod application's Symfony rules. Shared contracts and
application inspection are permitted without making the app a dependency.

## Trust boundary — READ THIS BEFORE DISPATCHING

This lane runs harness workers **on the operator's host** with edit and
shell authority inside a snapshot. It does **not** provide OS-level
containment; `harness/isolation.py` strips operator config, not the
filesystem or credentials. Therefore:

- **Dispatch only TRUSTED work here** — maintainer-directed changes on
  branches the maintainer opened. Never dispatch an untrusted or
  public contributor's commit on this host: a hostile `conftest.py`,
  CUE tool, or test import would run with the operator's authority.
- Dispatching untrusted PRs requires an **ephemeral VM/container** with
  no operator home, SSH agent, or host mounts. That boundary is not
  built; until it is, untrusted dispatch is out of scope.
- `dispatch.conf` is sourced as shell — treat it as owner-controlled
  code, never worker-editable.

## Fixtures are synthetic

`fixtures/envelopes/` are shaped like real harness output but carry only
synthetic ids, costs, and paths. Never replace them with raw captures:
a real capture leaks session metadata into a public repo. Regenerate
via `stores.py`, never by copying a live run.
