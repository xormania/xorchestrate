# Agentscient extraction

Xorchestrate started from reusable implementation, tests and documentation taken
from two immutable Agentscient snapshots. The source repository was not modified.

| Source | Revision | What was extracted |
| --- | --- | --- |
| [`uscient/agentscient`](https://github.com/uscient/agentscient), `dev` | [`9150612`](https://github.com/uscient/agentscient/tree/91506128934b70a8967c626d6ece16d2c8a0815c) (`91506128934b70a8967c626d6ece16d2c8a0815c`) | The imported runtime (formerly `devtools/`), required skills, runtime and operator docs, optional PHP helper, original repository guidance and CI |
| [`uscient/agentscient`](https://github.com/uscient/agentscient), `shit` | [`456457b`](https://github.com/uscient/agentscient/tree/456457b9a0a9eb1ff285ee2d3e40535b6f722cdb) (`456457b9a0a9eb1ff285ee2d3e40535b6f722cdb`) | The earlier conductor toolkit (`ops/conductor/`, `ops/testkit/`), contracts, fixtures and synthetic test applications (formerly `legacy/agentscient/`) |

The Symfony product, live `.dev/` state, operational records, host caches and
credentials were never extracted.

## Removed in-repo copies

The extracted trees (`devtools/`, `legacy/`, `reference/`), the per-file
`extraction-manifest.json` and the documents that only described the imported
runtime were removed after `957c404`. Xorchestrate keeps no in-repo copies for
reuse. The pinned upstream revisions above are the reference for that work; the
removed files, including the manifest's source path and blob hash for every
imported file, remain in Git history at
[`957c404`](https://github.com/xormania/xorchestrate/tree/957c404).

Mechanisms adapted into the active application cite their pinned upstream
sources where they are used, for example in [state management](state-management.md)
and the [live testkit](live-testkit.md). Current validation
is described in [testing-framework.md](testing-framework.md).
