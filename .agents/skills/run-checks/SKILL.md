---
name: run-checks
description: Set up, run and read this repository's checks (static suite, behavioural tests, families, shards, calibration, live stories), and place new tests, contracts, support code and catalog entries where they belong.
---

# run-checks

Read and follow [the testing framework](../../../docs/testing-framework.md). It is
the source of truth for setup, suites and where tests go; this skill only
repeats what must survive a summary.

- Set up once per session as the guide shows: a fresh `PYTHONPYCACHEPREFIX` and
  `UV_PROJECT_ENVIRONMENT` outside the checkout, the dependency check, the two
  locked `uv sync` commands and the editable install.
- Run the smallest suite that covers the change: `--suite static`,
  `--suite tests --family <family>`, `--suite checks`, `--suite calibration`,
  `--suite acceptance` or a story, all through `python3 -m scripts.testkit`.
- Direct pytest only after the same setup, and always with `-p no:cacheprovider`.
- Declare every new test module's family and guarantee in
  `contracts/testing.json`. Expected behaviour goes in
  `tests/hexagonal/contracts/`, concrete setup in support adapters under
  `tests/hexagonal/support/`. Never import a collected test module.
- Architecture roles live in `contracts/hexagonal.json`, checked by the
  `scripts/architecture` engine.
- Keep evidence outside the checkout (`--output`). A run that selected nothing,
  or only skips, proves nothing.
