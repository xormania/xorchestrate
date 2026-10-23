# python-tooling fixture

This tree is calibration apparatus for the Python development-tooling dialect; it is never product code.

Its green check runs both the application tests and its local lint policy:

    python3 -m unittest discover -s tests -p 'test_*.py' && ruff check .

PT-01: User-supplied helper names are passed as argument-vector entries, never interpolated into a shell command.

PT-02: Record paths always remain beneath the configured store root.

PT-03: A count of zero is an answer and is distinct from an absent record.

PT-04: Credentials never leave the process in failure logs.

PT-05: An unreadable or corrupt store is reported as a refusal, never served as an empty answer.

PT-06: Intervals are half-open, including their start and excluding their end.

PT-07: Test oracles are independent literals, not values computed by the code under test.

The files under `probes/` mechanically prove each planted fault red, and the patches under `fixes/` independently make its probe green. `FAULTS.json` connects every declaration, practiced span, clean neighbour, probe, and fix. K7 belongs to the production-service fixture; K9 does not apply because this stdlib-only dialect has no package manifest or lock.
