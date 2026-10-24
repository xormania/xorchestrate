# Controlled corpus and behavioral probes

The v2 corpus has 24 packets in eight grouped boundary scenarios: six development,
six calibration, and twelve holdout packets. Each scenario contributes a passing
boundary claim, a contradicted claim, and an initially missing-observation claim.
These are seven dependency clusters, not 24 independent incidents. The argument
and ordered-attempt scenarios share one receipt fixture and one calibration
cluster; treating them as independent would overstate evidence. All mechanisms were known
from the retained review; the pilot cannot establish discovery of unseen mechanisms
or production rare-error performance. The split is fixed before model measurement.

The first preparation replay reused historical schedules. It is retained in
`data/reference_replay` as development evidence and was replaced before any model
evaluation. Its packets are not the v2 holdout. The final holdout executions have
materially different controlled trajectories:

| Probe | Historical schedule | V2 holdout trajectory |
| --- | --- | --- |
| Adapter replay | One owner revision followed by replay | Two owner revisions with an intervening replay, followed by another semantic replay and fresh native reads on three adapters |
| Post-effect recovery | One reconnect during held response | Two connection replacements while the already-applied effect response remains held |
| Busy activity | Two holds, first finishes | Three concurrent holds, middle finishes while two remain outstanding |
| Callback authority | One principal/work replacement | Two principal/work replacements before the originally observed callback executes |

These are transfer variants of known families. Repeated calls and their controls
stay in their dependency cluster. Application material-context cases and calibration
cases remain development; argument normalization and ordered-attempt substitutions
remain calibration. Raw receipt substitutions exercise the actual normalizer and
production receipt evaluator, but do not establish a live misdelivery. Native
subprocess peers establish the real adapters' behavior under their schedules;
installed vendor behavior is unmeasured.

`cases.json` keeps `packet` separate from initial and outcome keys and hidden
execution selectors. Evaluators receive only the packet. A missing initial
observation remains unresolved even when the executable probe later reveals a
fault. Missing context and assertion packets omit both compared operands when
both are observations. A wrong-subject transformation retains convincing data but
changes its bindings, while remove-decisive transformations omit data. Other
prespecified transformations reverse options, reword the claim, or add irrelevant
context. Thirty derivatives are diagnostics, not extra independent incidents.

Claims concern the specific local boundary, not an entire task's success. A busy
session can correctly remain active without the work being complete. A current
acceptance read can correctly report rejection. `violations` identifies unsatisfied
requirements of the supplied claim; it does not automatically identify a product
bug. In argument/attempt substitution cases, the production receipt correctly
rejects the claim. The misleading summary is an untrusted claim and is never the
oracle. The corpus contains concurrent argument and unresolved-attempt obligations,
wrong-run evidence, an actual unrelated helper exception, and explicit low-impact
versus duplicate-effect valuation classes. Monetary values remain analyst scenarios.

The calibration probe runs real pytest subprocesses and the actual candidate
classifier. A healthy returned value is its control, an intended returned-value
AssertionError reaches the comparison, and an unrelated helper AssertionError
contains the same text but never reaches the comparison. Structured phase evidence,
not diagnostic substring matching, attributes intended detection. Candidate false
credit remains a sustained fault. All eight probes also have setup/helper/timeout
controls that must remain `not_exercised` without utility credit. Source revisions,
raw responses, original arguments, ordered attempts, protocol wire results, patches,
and actual assertion operands remain retained. Executed Python assertion sources
are retained byte-for-byte with a `.py.txt` suffix to prevent accidental recursive
pytest collection of the intended failures.

The independent premeasurement reviewer first receives `initial_packets.json` and
`transformation_packets.json`, records judgments, then opens the keys and raw
outcomes. The review exposed and corrected a target-assertion label conflation,
broad claim wording, a missing-observation operand leak, and confounding in the
wrong-subject transformation before live measurement. Review records carry the
specific artifact hashes and reviewer exposure.

Reproduce the corpus in a fresh directory from the pinned experiment source:

```sh
python -m experiments.jev_failure_correlation_v2.corpus --out /tmp/jev-v2-fresh-corpus
python -m pytest -q experiments/jev_failure_correlation_v2/tests/test_corpus_probes.py
```

Native adapter probes require local socket access. A denied socket, timeout, or
setup failure remains unexercised; no generic passing test substitutes for it.
`execute_probe(case, probe_id, output_dir)` runs the selected boundary in fresh
state and returns the exact additional visible observations plus raw artifact
references. A mismatched candidate is unsupported and receives no utility credit.

`execute_action(case, action, output_dir)` supplies a bounded end-to-end subset
for material/cosmetic context cases. In a fresh application and SQLite store it
obtains an old assessment, changes context or title, applies the selected action,
and reads durable acceptance. Acceptance consumes the old assessment; rejection,
deferral, or investigation withholds acceptance, and investigation obtains a fresh
assessment. Independent current assessment is retained after the action. This
measures actual false acceptance or unnecessary withholding in controlled state;
it does not measure production loss, repair effort, or broader downstream effects.
