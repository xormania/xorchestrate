# Concurrent launcher activity and test isolation

Owner scope, 2026-09-09: fix process failures encountered in this session;
filename input presentation is owned separately.

Measured at source `20f9bcaf2b3ae2152b9cab766637b5a89b71214b`:
`sed -n '140,162p' ops/conductor/tests/test_onboard_lever.py` shows the
live-store comparison already tolerates newly minted job directories.
`sed -n '280,345p' ops/conductor/tools/dispatch.sh` shows detached launch
creates `.launcher.XXXXXX` in that same store and later moves it into its job.
The full suite at `16f870930c373b8e266343af222f985898be6303`, ended
2026-09-10T01:24:32Z, failed in the onboarding test teardown because a parallel
canonical dispatch added `.launcher.iCO9sU`. The tested behavior assertion
passed. This is a reproducible mismatch in test infrastructure.

The existing `_without_new_job_dirs` comparison must additionally tolerate
creation/removal of canonical transient launcher-log names: exactly
`.launcher.` followed by six ASCII letters or digits. Normalize these names
on both sides of the comparison; retaining a launcher log is not a test
failure when a concurrent launcher still owns it. Keep the existing tolerance
for new canonical job directories. Missing stores, changed store types,
removed preexisting job directories, ordinary added/removed files, and near
miss launcher names must still fail. Do not mask the whole store or swallow
assertions. Tests remain isolated under their existing scratch HOME and store.
This comparison has never established process attribution or OS containment;
its named concurrency exclusions confer neither write permission nor evidence
that an arbitrary host actor is safe.

Only this test helper and its use in teardown may change. Do not change the
onboarding product, filename dispatch handling, source observation assertions,
or wrapper log lifecycle. In particular moving a live open log across file
systems can lose later child output and is not the remedy here.

A different producer authors a focused synthetic test file before Astra edits
the helper. Pin creation, removal and unchanged launcher logs; six-character
shape boundaries; unchanged and newly added job directories; removed original
jobs; ordinary mutations; absent/type-changing stores. Valid fixtures execute
before comparison. Prove the new expected comparisons red, then green with
identical test bytes. Run that file, existing test_onboard_lever.py, and the
lane suite. No paid model or real live-store mutation is needed to reproduce.
Standing: helper normalization implemented; the independently authored 24-case
suite passes after six intended RED failures. Full acceptance is pending.

## The shared guard, 2026-09-10

Owner, 2026-09-10: lane defects observed live get a direct repair. On
PR 87 three teardown guards failed with nothing in the change: two
`.launcher.*` logs from concurrent dispatches in suites that had not
taken the launcher-log exclusion, and the live `findings.db` growing
under a concurrent ladder's ledger import.

The comparison now lives once, in
`ops/conductor/tests/live_store_guard.py`, and every conductor suite's
teardown calls `assert_unchanged`. The exclusions are exactly the two
above: new canonical job directories and transient launcher logs,
normalized on both sides. Nothing else is tolerated. The retained
24-case `test_store_guard.py` keeps binding to
`test_onboard_lever._without_new_job_dirs`, now an alias of the shared
function, so its pins hold unchanged.

The app fixture suite's live-findings-store check compares presence and
kind, not byte size. Declared gap: a tool under test that appends to the
live ledger is no longer caught by size; the ledger's own identity and
the suites' scratch HOME remain the isolation, and this guard has never
claimed attribution. A tool that creates or removes a live store is
still caught.
