# The Agentscient stack

Current scope notice, owner 2026-09-05:
[AGENTS.md §Lane boundary and precedence](../../AGENTS.md#lane-boundary-and-precedence)
governs current operation. The dated record below is retained as origin;
its bounded-Python and product-convergence doctrine is superseded, not an
instruction to replace dev tooling. Application framework choices remain
scoped to the prod application. Being listed by onboarding does not give
this historical proposal precedence over that correction.

Standing: PROPOSED (D-STACK-1). Source: owner, 2026-09-01 — "I'd
rather focus on a Stack" — closing the kernel-language exchange
(I-009) by reframing it: not one ruling but a composed set of layers,
each with an incumbent, a standing, and a rule for changing it.
Nothing here invents a commitment; every incumbent below is measured
practice, and every open seat is marked as one.

## The layers

| layer | role | incumbent | standing |
|---|---|---|---|
| contract plane | what everything is wired through | CUE (`capability.cue`, `outcome.cue`, board contracts) + markdown contracts with conventions inheritance (D-LAW-8) | practice, in-repo law |
| record plane | evidence, state, history | git (refs, verbatim records, snapshot refs) + SQLite stores (register, ledger, skills, handwork) + JSON envelopes | practice |
| wire | how components talk | the refusal wire, four-class exits, deterministic JSON. Ruled 2026-09-01 (owner: "mcp feels like the way to give you the context and diag - and scripts/bins as surfaces"): MCP is the context-and-diagnostics plane — status, briefs, telemetry served as resources, read-only by its import graph at the time — while scripts and binaries are the acting surfaces, each a lever with a contract and the refusal wire. Superseded 2026-09-16 (D-MCP-1): MCP acts as well as reads; the read server keeps its contract and action operations are typed levers on the wire. | ruled in chat |
| instrument lane | short-lived levers, harness-adjacent glue | Python, stdlib-only — **bounded**: "bound it when possible - but its days are numbered" (I-009); no new kernel component is born Python | bounded incumbent |
| kernel | long-lived services: queue, watcher subscriptions, registries-as-services, forge status | **OPEN SEAT** — evaluation order fixed: Rust first, ruled out before Go is chosen (I-009); repairs-at-reconciliation is the metric, via one pilot unit through the two-producer pipeline | open, order ratified in chat |
| producers | who writes the code | harness dispatch (codex/Sol, grok, Claude) under contracts; model supplied at dispatch, never by table (AGENTS.md, D-ROUTE-2) | ratified |
| forge | where branches, PRs, reviews live | GitHub canonical today; local Gitea built (docker lane) — **posture question open**: Gitea-primary dev lane vs CI muscle only | open question, both built |
| ci | gates in execution | GitHub Actions, 12 runners, parallelized to the longest job; act_runner/Gitea lane steps 1–2 landed; digest-pinned lane image (#103) | practice |
| coordination | sessions, officers, someday workers | conductor sessions today; the delegable Conductor → Officer Corps → async queues and workers on spot (I-007, I-008) | horizon, recorded |
| infra | where it all runs | WSL2 + Docker lane now; AWS with queued work for spot instances on the horizon (I-007) | horizon |

## Alignment with the product

Owner, 2026-09-01, closing the kernel exchange: Rust with Wasm —
"That's what [the House product] is heading towards." The product and the
framework converge on one stack. I-004's namespaces stay segregated
organizationally, but the toolchain, idioms, and producer competence
become shared: the kernel-language pilot (D-LANG-2) is no longer a
dev-lane side question — it is rehearsal for the product's own
architecture, and every Wasm resonance recorded in I-009 (WIT as
contracts, fuel as caps, the sandbox as permission doctrine) describes
the product's future, not just the framework's.

## Change rules

- An incumbent is replaced only by a **measured pilot** through the
  same pipeline that produced it (the kernel seat's Rust pilot is the
  template: contract first, two blind producers, repairs counted), and
  the owner ratifies the swap in the register.
- The wire outranks every layer: any candidate in any language that
  speaks the refusal wire, the four classes, and the envelope JSON is
  admissible; the conformance gate already tests instruments as
  subprocesses, so a Rust or Go binary passes it unchanged.
- Bounded means bounded: Python components keep working and keep their
  contracts; they are not rewritten for sport, and their lane earns no
  new kernel responsibilities.
- Open seats close in the register (D-LANG rows for the kernel, a
  D-FORGE row for the posture), never in prose.
