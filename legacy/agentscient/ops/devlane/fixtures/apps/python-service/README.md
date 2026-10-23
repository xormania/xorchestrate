# python-service fixture

This tree is calibration apparatus for the Python production-service dialect; it is never product code.

It is a records service with a JSON-lines request boundary on stdin. Install its pinned toolchain with:

    python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.lock

Run the one green check with:

    .venv/bin/python -m pytest -q tests && ruff check .

PS-01: Request fields are passed to helpers as argument-vector entries, never as shell text.

PS-02: Served files always resolve beneath the store root.

PS-03: Credentials never enter the request log.

PS-04: A malformed request is refused with a problem, never answered as empty success.

PS-05: Pages are half-open: the start is included and the stop excluded.

PS-06: Tests exercise the real request parser at the boundary.

PS-07: Runtime dependencies are pinned to one version in the manifest and the lock agrees.

The files under `probes/` mechanically prove each planted fault red, and the patches under `fixes/` independently make its probe green. `FAULTS.json` connects every declaration, practiced span, clean neighbour, probe, and fix. K3 and K8 are covered by the python-tooling fixture; a second K9 waits for the first grading run.
