---
name: rust-agent-tooling
description: Verified landscape of Rust CI/diagnostic tools for the agent-driven dev-lane — what to adopt, when, and where each plugs into the gate registry.
covers:
  - ops/devlane/workflow/gate-kinds/
  - ops/devlane/workflow/checks/diagnostics.py
  - ops/devlane/workflow/checks/ci_matrix.py
---

# Rust tooling for an agent-driven lane

Every claim here was verified 2026-08-22 by web research agents
against crates.io, GitHub, and project docs (10 agents, ~560k
tokens; full sourced records in the session transcript). Every
"active", "weeks old", or star figure is AS OF that date — tool
freshness rots and `covers:` cannot watch external projects; only
the repo-facing claims are mechanically checkable. The trigger was
a Gemini answer the owner shared; its five claims are graded below,
then the tools it missed.

## Gemini's five, graded

| claim | verdict | the correction that matters |
|:--|:--|:--|
| dylint | confirmed | JSON is cargo's `--message-format=json` passed after `--` — our reader's third shape. Cost is real: every lint library pins its own nightly (+ rustc-dev), one `cargo check` pass per toolchain, its own target dir, heavy CI caching. |
| ast-grep outline | confirmed | `ast-grep outline` is a real CLI command — alpha in 0.44.0 (2026-06-22), `--json` with ranges, "flat source stays flat" for Rust impl blocks. NOT in the official MCP server (that has dump_syntax_tree / test_match_code_rule / find_code). Pin the version; it is weeks old. |
| `#[diagnostic::on_unimplemented]` | confirmed | Stable since 1.78; `do_not_recommend` since 1.85. Hints only — fires on unsatisfied trait bounds, interpolates only `{Self}` and named generics, cannot enforce policy. |
| custom rustc_driver | confirmed, avoid | Real path, wrong cost: nightly-only `rustc_private`, dynamic-links a ~136MB toolchain dylib, imposes its nightly on the analyzed code, zero API stability (clippy/kani maintain sync machinery to survive it). |
| ax-rust-agent-observability | distorted | Not a crate, not a repo — a SKILL.md documentation file on axllm.dev describing the `axllm` crate's usage-observer hooks. That crate is machine-generated from TypeScript and two months old. Nearest real things: OpenTelemetry GenAI conventions, `tracing` — or this lane's own telemetry, which already does the job. |

## Adopt now (no Cargo workspace needed)

- **zizmor** — Rust-native static analyzer FOR GitHub Actions
  workflows (template injection, unpinned actions, excessive
  permissions), JSON/SARIF output. Our CI *is* currently the
  product; ADOPTABLE today as a registered check over
  `ci-dev.yml`/`ci-dev-apps.yml` — not yet registered (the matrix
  is secret-scan and vocabulary-wall only).
- **ast-grep** — YAML rules via `scan --json` for codified
  invariants at zero toolchain cost (the cheap seat dylint competes
  for), and `outline --json` as the token-frugal structure query
  for agents. Version-pin; outline is alpha.

## Adopt when the workspace lands (each as its own new registry
entry with `"ci": true` — see The seam for what the existing pins
do and do not cover)

- **cargo-mutants** — the lane's executed-mutant discipline as a
  tool. Typed exits are the killer feature: 2 = missed mutants,
  4 = baseline already red — refusal distinguished from failure,
  exactly our INVALID-not-pass rule. `outcomes.json` per mutant;
  `--in-diff` scopes PR gates.
- **cargo-nextest** — runner with stable JUnit + `list
  --message-format json`; zero-matching runs FAIL by default
  (refusal-over-silence built in). Streaming JSON still
  experimental — parse the JUnit.
- **cargo-llvm-cov** — `--fail-under-*` renders its own verdict;
  `--show-missing-lines` is an agent-actionable target list. Pair
  with cargo-mutants: coverage suggests, mutants prove.
- **cargo-deny** — advisories, licenses, bans, sources in one
  registered gate.
- **cargo-semver-checks** — API-surface gate off rustdoc JSON;
  with **cargo-public-api** as the one-line-per-item API skeleton
  agents can diff instead of reading source.

Not a gate, adopted alongside the workspace anyway:
`#[diagnostic::on_unimplemented]` — free compiler-to-agent
messaging on base-crate traits (MSRV 1.78). An attribute has no
argv, no exit code, and no refusal semantics, so it lives in the
crates, never in the registry.

## Selective / watch

- **dylint** — only when an invariant needs type-aware analysis an
  ast-grep rule cannot express; budget the nightly-pin chore.
- **insta** — agent-aware (`pending-snapshots --as-json`,
  non-TTY review built for LLMs) but `--accept` can launder a
  behavior change; snapshot diffs review as code diffs.
- **rust-analyzer-mcp (zeenix)** — active as of the 2026-08-22
  measurement (pushed the day before), ~10 symbol/diagnostic
  tools; the live-query complement to Serena. `rust-analyzer scip`
  for offline indexes.
- **Miri / Kani** — UB detection and proof for the unsafe core,
  as scheduled (not per-PR) gates; both slow.

## Avoid

- custom rustc_driver (above), **MIRAI** (dormant),
  **cursor-rust-tools** (dormant, superseded), **mutagen**
  (nightly proc-macro mutation — cargo-mutants ate it),
  RLS/Racer-era anything.

## The linked list

`venuswhispers/Rust-awesome-frameworks` is an unmarked
re-publication of rust-unofficial/awesome-rust (not a GitHub fork;
40 stars vs upstream's ~59k), last pushed 2024-02-19 — its
development-tools section is a Travis/RLS-era snapshot. Use
upstream. Its survivors worth noting: `just`/`cargo-make` (task
running), `hyperfine` (bench), `rr` (deterministic record-replay
debugging — interesting for reproducing agent-triggered failures),
`cargo-modules` (module-graph queries).

## The seam everything plugs into

A tool earns a place here by (1) an argv command, (2) machine-
readable output (JSON/SARIF/JUnit) or a self-rendered exit-code
verdict, (3) refusal distinguished from silence. Registration is a
gate-kinds entry. The two-sided workspace pin covers exactly the
checks it names today — diagnostics (test_gate_binding /
test_ci_shape / test_diagnostics) and cargo-test
(require_workspace_tests, whose match is exactly
`argv[:2] == ["cargo", "test"]`); every NEW tool above needs its
own `"ci": true` entry AND a REQUIRED_BINDINGS row with a planted
fixture — the binding suite is a hardcoded list plus a checks/*.py
argv filter, so nothing gets its pin for free. `diagnostics.py` already
reads rustc, cargo message-format, and bare-severity shapes; JUnit
and SARIF readers are the two adapters worth adding when the first
of these gates arrives.
