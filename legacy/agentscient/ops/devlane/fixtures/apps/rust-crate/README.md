# rust-crate fixture

This tree is calibration apparatus for the Rust target profile; it is not a real project and never product code.

Warm the host cache once with `cargo fetch`. Afterwards the fixture's green check is:

    cargo fmt --check && cargo clippy --offline --all-targets -- -D warnings && cargo test --offline --locked

The labels cargo--offline and cargo--locked refer to those two Cargo flags.

`CARGO_TARGET_DIR` may point outside the scratch so compilation residue need not enter it.

RC-01: User-supplied names are passed to Command as arguments, never through sh -c.

RC-02: Record paths stay beneath the store root.

RC-03: A count of zero is an answer and is distinct from an absent record.

RC-04: Credentials never leave the process in failure logs.

RC-05: An unreadable or corrupt store is reported as an error, never served as empty.

RC-06: Intervals are half-open, including their start and excluding their end.

RC-07: Dependencies are pinned to one version and locked.

The files under `probes/` mechanically prove each fault red, and the patches under `fixes/` independently make its probe green. `FAULTS.json` binds every declaration to its practiced span and clean neighbour. K7 is exercised by python-service, and K8 by python-tooling and symfony-app.
