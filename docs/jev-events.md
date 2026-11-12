# Jev at PR events

The event pilot supplies small, advisory answers while deterministic CI remains
responsible for verification. It uses existing work from Agentscient, CCE and
Mago–Serena; [the retained cases](../tests/fixtures/jev-events/cases.json) record
which inputs are adaptations and which historical measurements are available.
They are not a new live accuracy result.

The default automatic placement is this focused event pilot. In
`contracts/jev-advice.json`, broad `automatic.source` and `automatic.post_test`
are both off. The older independent consumer records that decision before any
GitHub evidence request, archive download or provider call. Enable either stage
explicitly in trusted configuration for a broader comparison.

Broad source/test advice also remains available through **Jev advisory → Run
workflow** on the default branch: supply `source_run_id` and choose `source` and
`post_test`. For the existing bounded live controls, choose `calibration` instead
and leave the source run empty. This explicit dispatch is separate from automatic
events and does not rerun CI or alter its result.

| Event | Selected evidence and question |
|---|---|
| Draft opened, reopened, ready, or meaningful title/body edit | PR purpose and one complete changed file; which configured review topics directly apply? |
| Review submitted | Latest inline finding in that review and its complete current file; is the specified failure mechanism still present? |
| Source push | Latest existing inline finding in a changed file, when available; otherwise the scope question above |
| Failed PR CI run | Actual failed job and step names plus up to two complete log windows; failure category and useful existing excerpt |

The source selector is deliberately bounded: one file, one finding, or one failed
job per event. Reports identify selected source and omissions. Large or missing
files, absent inline findings, superseded heads and unavailable evidence remain
explicitly unassessed. Review comments can be historical; this pilot does not
infer whether their threads are resolved or reopen them. A selected-file answer
is not a verdict about the whole PR. Passing CI triggers no failure diagnosis.

`contracts/jev-events.json` owns topics, questions, rubrics, model, thresholds,
byte budgets and the provider deadline. Scope and review questions use one
shared-state request. Failure category and excerpt selection use **two requests
sharing a five-second deadline**, because the existing Choice adapter uses one
criteria map per request. This preserves the donor rubrics without a broad
adapter change. Neither request is retried automatically. Input estimates and
actual reported token use remain distinct.

The existing request cache keys exact semantic evidence, questions, criteria,
model and endpoint. A lifecycle transition or unchanged new commit can reuse
inference; the new report still binds the current source head. Original reports
are not relabeled. Failures are not cached. Cache restoration is best effort,
not an exactly-once guarantee across interrupted runners. Provider timing excludes
GitHub queries, runner queues and environment setup.

`jev-events.yml` executes only trusted `master` code. PR code and comments are
fetched as bounded API data, never checked out or executed. The submitted-review
workflow is an unprivileged data relay; the trusted consumer verifies its workflow
and PR association and re-fetches the review data. Same-repository PRs only.
Known credentials and explicit secret fields are protected with the existing
telemetry protector before retention or inference. The source head is checked
before spending and again before publishing; a stale result retains a stale
receipt without presenting its assessments as current advice.

The workflow writes one summary and a 30-day artifact containing source identity,
trusted code identity, selected packet, policy, validated answers, full probability
distributions, original versus routed outcomes, cache receipts, elapsed request
time and fresh/reused usage. It posts no comments, resolves no threads and changes
no required result. Unknown is an ordinary useful outcome, not a trigger for a
full test rerun. These workflows activate after reaching the default branch;
pre-merge fixture checks establish integration behavior, not live Jev usefulness.

For a bounded local or retained-packet experiment:

```bash
python -m scripts.ci.jev_events --packet /path/to/packet.json \
  --cache /path/to/trusted-cache --output /path/to/observations
```

Use configured credentials only for intentionally live runs. Evaluate useful
findings, false alerts, missed findings, abstentions, calls, latency and actual
review effort separately. Keep labels outside provider packets. The retained
Agentscient success, inconclusive control and CCE layout lessons seed evaluation;
they do not establish that a different prompt or repository inherits their scores.

Donor sources: [Agentscient failure policy](https://github.com/xormania/agentscient/blob/1aabada854fd377f08c3976cf427649f46eafec4/.github/jev-ci.json),
[live proof](https://github.com/xormania/agentscient/pull/356),
[CCE selector](https://github.com/xormania/cce/blob/99a1470620da4f8d60a9dc6117504cd60d24696d/tools/ci/ledger_select.py),
[Mago–Serena retained calibration](https://github.com/xormania/mago-serena_v2-poc/blob/b6dbec8378338e39fe51489bc28f9ad578e4f3ab/docs/evidence/ci-performance/live-calibration-summary.json),
[UXor independent consumer](https://github.com/xormania/uxor/blob/6991671805dba5d80c65d1da390f24474f25beff/.github/workflows/jev.yml).
