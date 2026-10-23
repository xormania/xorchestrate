# Recovered Agentscient toolkit

`agentscient/` preserves the reusable toolkit from the historical branch at
`456457b9a0a9eb1ff285ee2d3e40535b6f722cdb`. It is an independent source collection,
not a second active runtime or a required dependency of `devtools/`.

The collection includes the conductor, coordination store, message courier,
native session adapters, MCP facades, dispatch and isolation helpers, task and
workflow contracts, telemetry, synthetic application fixtures, and reusable testkit.
The original source paths are retained beneath `agentscient/` so sibling imports
and fixture references remain meaningful.

Run `python3 scripts/check_legacy.py` from the Xorchestrate root to test the recovered
testkit, coordination, MCP, courier, adapter, and state-machine components. It
creates a temporary Git repository because historical tests archive their HEAD.
These are the recovered components covered by CI; the rest is preserved for
selective reuse and has not been certified as a complete application here.

Historical hooks, dispatch, provisioning, release automation, role rules, and
defaults are not installed into Xorchestrate. Many still assume the original
`dev`/`master` branches, repository slug, and optional host tools. Review and adapt
those settings before enabling them. Synthetic application fixtures deliberately
contain defects used to test agent workflows.

Live `.dev/` records, handoffs, findings, reports, decision history, and old
operational archives were not copied. See `../docs/extraction.md` and the root
extraction manifest for the exact provenance and boundaries.
