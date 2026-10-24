# Final bounded suite and preservation evidence

The pinned candidate `1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`, tree `75ecb3ef4edbc381012fe74e416a9abd3fb5c667`, passed the required focused contracts/boundary run and one full hexagonal run. No product source was changed. Both executions used Python 3.12.14 with the published pinned replay dependencies and user-space tmux 3.5a.

| Execution | Observed result |
| --- | --- |
| Seven focused files named in the published replay | 2,942 passed, zero failures/errors/skips; 24.14 seconds |
| Full `tests/hexagonal` | 4,067 primary tests plus 164 passing subtests, zero failures/errors/skips; 131.46 seconds |
| Preserved selector inventory | All 211 published selectors resolve and have passing primary JUnit entries |
| Preserved source | All 450 earlier direct test bodies remain unchanged; none removed; original fault-definition files byte-identical |
| Fresh composed evidence binding | All 46 required cases and source/input hashes reconcile; all 17 recursive checker source files bound |

`full.xml` has 4,067 `testcase` elements. Its aggregate `tests` attribute is 4,231, which includes the 164 subtests; stdout explicitly reports both counts. These are coverage records, not an independent correctness oracle. The standard-library inventory audit parses test ASTs/JUnit and hashes bound source/input files; it does not import a candidate verdict.

`commands.json` records the exact commands, cwd, selected environment, timestamps and status. `run_suites.py` is the reproducible execution recorder. `identity-before.json` and `identity-after.json` record every tracked regular-file SHA-256 plus clean Git identity; they match exactly. `inventory-command.json` records the separate published audit invocation and script identity. `summary.json` and `inventory-summary.json` expose semantic results without requiring log interpretation.

No run failed or was repeated. No required check in this assigned scope remained unexecuted. The full suite retained tmux-dependent tests with no skips. Authenticated vendor behavior is not established by these deterministic tests.

Raw pytest temporary directories remain `/tmp/xfs-f-mw6wkcwr` (focused, 43 MB allocated) and `/tmp/xfs-f-tfgd8nyk` (full, 103 MB allocated). `temporaries-manifest.json` binds every regular file, records symlinks without following them and records nonportable IPC paths. The root handoff can include these directories as `suite-runtime/`; the redundant `pytest-temporaries.zip` need not be published alongside their contents.
