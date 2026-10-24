# Independent provenance and preservation audit

The candidate, evidence publication, raw measurements and preserved coverage reconcile. This track found no provenance or coverage-removal blocker. That conclusion does **not** close IV01–IV04: adversarial behavioral verdicts in the other tracks determine whether the repairs satisfy their requirements. A second checkout of the checker supplies execution isolation only.

The reviewed product is `e565863ae72180571678c8f6118ee6e00b129e2e`, tree `474de55c5947890ab4092daeca7ff39f9bbbc400`. The evidence publication `37547a138758fe70e6cc471b491aaab01bfc3a05` has that product as its sole parent and adds exactly 21 files beneath `verification/pr6-iv-repairs-e565863/`. It changes no product file. The original candidate is `ef7567516520543970af55b6f19534cb66eda357`, tree `a0813aae760e6e5bb6245693bd92b7e7c321348b`.

## Independently executed audit

`identities.py` checked all 23 indexed input files against their byte counts and SHA-256 values, all four retained original witnesses against the separately retrieved original review archive, all eight source-bundle refs, Git bundle validity, candidate/baseline tree IDs and the publication-only diff. See `identities.json`. The root review separately checksummed and restored both complete publications; those logs are under `../setup/`.

`audit.py` uses only Python's standard library. It compares the original 89 and frozen 165 maps with the original publication's requirement matrix, checks candidate and baseline ASTs, checks all mapped selectors against raw JUnit class/function identities and parameterized outcomes, matches archived artifact bytes to freshly fetched GitHub SHA-256 digests, verifies every extracted member, compares freshly fetched job logs to the retained logs, constructs the required 46-case inventory independently, recomputes all canonical bindings and checks every recorded source/module hash against the exact candidate. `audit-final.json` passes.

The final active full suite contains **1,127 primary testcase elements plus 164 subtest events**, with zero failure/error/skip elements. JUnit's total 1,291 includes those subtest events. The fresh local, retained local, hosted Python 3.11 and hosted Python 3.12 full-suite XML each reconcile to these counts. All original/frozen/added selectors match passing actual test identities in all four XML files. Focused suite totals overlap the full suite and must not be added to it. The first fresh focused run's failure remains in the root evidence; a later focused pass does not erase it or retroactively change its status.

`raw_audit.py` independently checks observations and durable records without importing product, checker or normalizer code and without reading the normalized event stream. For each of the fresh local, retained local and hosted protocol runs, it verifies:

- 69 main-execution native calls, each with exactly one independently observed native tool record, requested callback and completed callback; all three agree on actual qualified identity, actor, work, action and independently supplied arguments. Completed results agree with durable calls.
- 15 challenge/reply exchanges, covering six direct, three busy and six unfinished-work exchanges. Actual messages preserve sender/recipient, nonce, private-memory reply, temporal order and submitted-attempt identity.
- Busy challenge submission occurs strictly inside the observed provider hold; unfinished-work replies occur strictly after work starts and before it completes.
- Nine work answers equal a new calculation from the latest per-job fixture attempts: failed-job sets and failed-test totals agree for every peer and all three batches.

See `raw-audit.json`. Its scope excludes lifecycle semantics and adversarial false-input coverage, which have separate tracks. This is independent behavioral corroboration of the composed positive controls; it is not inferred from the candidate checker's green report.

`recompute.py` additionally reran the selected checker from its separate exact checkout over both retained local and hosted raw inputs. Both parsed reports exactly equal their saved reports, 46/46. Root independently executed and recomputed the fresh protocol, also 46/46. Equality is a reproducibility result, distinct from the independent raw audit above.

## Hosted provenance

Direct GitHub reads confirm run [40132785317](https://github.com/xormania/xorchestrate/actions/runs/40132785317), attempt 1, head `e565863`, all six jobs successful. All six newly retrieved decoded logs are byte-identical to the retained logs. All five retained artifact ZIPs match GitHub's current digests and sizes and pass CRC/member verification.

The independently read [Git commit object](https://github.com/xormania/xorchestrate/commit/cf424865c3e86f93b98e015621158c995d5ae187) has the exact candidate tree and ordered parents `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`, `e565863ae72180571678c8f6118ee6e00b129e2e`. The shallow checkout's empty parent line supplies no ancestry proof; the GitHub object does.

Python 3.11 and 3.12 each ran the full application suite. Hosted calibration and composed protocol ran on 3.12 only; the 3.11 workflow intentionally skipped those steps. Hosted raw calibration XML independently totals 36 control passes, 19 mutant assertion failures and 17 unaffected mutant passes, with zero errors/skips. The original seven native and five work fault definitions are unchanged from the original candidate. Exact attribution/source-fault judgments belong to the IV04 audit, rather than being inferred from XML failure counts.

Recovered toolkit logs explicitly reconcile 64+138+35+6+9+33 = 285 tests, each with zero skipped/not-run. The historical runtime artifact reports 825 executed, zero skipped/not-run/failures/errors. These are retained hosted executions of historical source, not fresh executions in this independent session. Historical source is unchanged by the repair. Live tmux, static checks and actual fresh preservation outcomes are indexed by the root review; authenticated vendor execution remains separate planned local work.

## Coverage and source changes

Exactly 46 test functions were added and none removed relative to the original candidate. The supplementary mapping names exactly those 46 functions; the union is 211 distinct selectors. Counts describe declared coverage, not a proof of closure.

| Requirement | Original | Frozen | Added | Combined |
|---|---:|---:|---:|---:|
| H1 | 5 | 19 | 0 | 19 |
| H2 | 6 | 24 | 11 | 35 |
| H3 | 6 | 37 | 25 | 62 |
| H4 | 8 | 24 | 3 | 27 |
| H5 | 7 | 27 | 19 | 46 |
| H6 | 8 | 13 | 0 | 13 |
| H7 | 10 | 43 | 17 | 60 |
| H8 | 5 | 39 | 27 | 66 |
| B1 | 5 | 13 | 0 | 13 |
| B2 | 9 | 29 | 0 | 29 |
| B3 | 9 | 9 | 0 | 9 |
| B4 | 5 | 73 | 17 | 90 |
| B5 | 6 | 40 | 31 | 71 |
| A1 | 5 | 17 | 7 | 24 |
| A2 | 6 | 66 | 46 | 112 |

One frozen selector body changes: `test_successful_transport_does_not_hide_wrong_work_missing_completion_or_unsupported_interjection` adds the actual `tool_completed` positive-control record and shifts sequence numbers; its prior missing-completion and protocol-violation assertions remain. One additional existing, unmapped Codex test changes its expected pending representation from a single slot to a set. Shared support adds native starts/ends and completed callbacks to isolate aggregate-state tests, adds a native-tail gate to the subprocess fixture, and retries only conflicts explicitly marked `retry_safe`. These changes were substantively inspected; unchanged selector names were not assumed to mean unchanged assertions.

The additional maps align with exercised boundaries: IV01 typed projection/opaque-data preservation, IV02 overlap and independent pending-work vetoes, IV03 distinct identity claims, and IV04 actual assertion attribution. H8/B4 entries for synthetic challenges describe checker sensitivity and controls; they are not substitutes for the actual composed run. Requirements retain their bounded meaning: deterministic persistent contexts, delivery/recovery, configurable policies and providers, and real fixture composition are observed; actual vendor fidelity is unmeasured.

Composition, ports and policy adapters are byte-unchanged from the original candidate. Replaceable Memory/SQLite storage, provider/native-driver selection, routing, scheduling, retry, receipt, assessment and acceptance policies remain declared and exercised. The architecture boundary tests pass in the full suite. No new universal approval, fixed evaluator hierarchy or mandatory authenticated-vendor gate was introduced. Jev and historical source were not changed. The seven previously closed findings receive dedicated fresh probes in `../closed-regressions/`; this track's coverage audit corroborates their full-suite coverage but does not replace those behavioral checks.

## Replay and adaptations

After the root replay guide retrieves/restores the two immutable publications and creates `candidate`, `baseline`, `checker` and the venv, set `R` to that review root:

```sh
python3 "$R/results/preservation/identities.py" "$R" /fresh/identities.json
python3 "$R/results/preservation/audit.py" --root "$R" --out /fresh/audit.json \
  --fresh-xml "$R/results/execution/application.xml" \
  --fresh-protocol "$R/results/execution/protocol"
python3 "$R/results/preservation/raw_audit.py" "$R" /fresh/raw-audit.json
"$R/venv/bin/python" "$R/results/preservation/recompute.py" "$R" /fresh/recomputation
```

The first three `audit.py` attempts are preserved as scripts and logs. They failed in the reviewer auditor on (1) omitted empty supplementary requirement keys, (2) symlink entries treated as regular files and (3) hashing a candidate manifest's file map without its `deleted` wrapper. The final script handles those documented input shapes. No failed attempt earns success credit and no subject data was changed. An exploratory print command also tried nonexistent `run.requested_cases`; the actual inventory is in criteria and `executed_cases`/`discovered_cases`, which the final independent inventory audit compares. No behavioral conclusion relied on that exploratory error.

The supplemental raw checker intentionally has a narrow stated scope and is itself reviewable; it does not claim an exhaustive independent replacement for every product verifier. Source bytes, raw observations, product/checker identities and hosted provenance are internally consistent, while integration disposition must follow the demonstrated behavioral counterexamples or closure evidence from the IV tracks.
