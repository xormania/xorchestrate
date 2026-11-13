# Fault inventories

Each inventory file holds calibration cases only: one exact source edit, the
selected tests, the target test that must catch it with its declared assertion
message, and the tests that must stay green. `registry.json` names every
inventory and the stages it runs at (`ci`, `pre-draft`, `manual`); a file that
is not registered, or an unknown name, is an error. `schema.json` defines both
files.

Run with `python -m scripts.verification.calibrate_suite --candidate <tree>
--out <new directory outside it>`, adding `--inventory <name>` (repeatable) or
`--stage <stage>`; with neither, the inventories registered at `ci` run.
See [the testing framework](../../../docs/testing-framework.md).
