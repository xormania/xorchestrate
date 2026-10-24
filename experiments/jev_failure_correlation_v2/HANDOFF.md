# Independent adversarial review handoff

Read `spec/05-INDEPENDENT-VERIFICATION.md` in a separate review session. The existing preparation/methodology review is not a substitute for that owner-requested review. Do not merge or deploy. No new paid calls are needed for replay.

## Candidate and evidence

The main measured source is `9c6d7612e31bb78eb07868b5ac2a046629c5876c`, tree `5313e3a176515e7b160315a56da430fcde63b189`. Hosted validation uses `0d8d5187209c1fb2c92e8d2a4305c6f7b40da9aa`, tree `febf6a8676f5231ff585019b6374dbc190b75be4`; its only difference is `fetch-depth: 0` in the v2 workflow. Later commits add supplemental analysis, documentation and evidence. Main executable/data hashes remain frozen in the main run manifest.

Product subject: `d2d2123bb8d4e866454c8e8e364f7749fa63a198`, tree `7bb88d5fd01a21478b720ad2d42b2b627b74bb92`. Repair PR #6 was still open/draft at `ef7567516520543970af55b6f19534cb66eda357` when refreshed; it was neither merged nor changed.

`evidence/archive-index.json` identifies downloadable retained archives. The main archive contains the exact requests/wire bytes, typed raw provider responses, 22 native request/result/invocation records, frozen route population, random audits, all probe observations, actual acceptance-action records, costs, raw replay and completion records. `ARTIFACTS.sha256.json` seals the complete main file set. Hashes establish bytes, not experimental validity.

`evidence/actions-40071712425.zip` is the original GitHub artifact ZIP, verified against GitHub's digest. Jobs and artifact metadata are in `evidence/access-and-validation/`. This was a successful offline hosted run; the paid hosted fallback was not used. The earlier failed hosted run log remains retained.

The post-hoc supplement is separate. Its prepared plan precedes its new probe execution and binds its script, source, exact population, seed and visible packets. It does not replace the original zero-call random reasoning arm or become a fresh confirmatory holdout.

## Keyless replay

Use the final PR checkout, with Git history containing the frozen subject. Python 3.11+ is supported. Install declared dependencies in an isolated virtual environment:

```sh
python3 -m venv /tmp/xor-jev-v2-review-venv
/tmp/xor-jev-v2-review-venv/bin/python -m pip install -e '.[test]'
mkdir -p .agents
cp -R reference/imported-agent-skills .agents/skills
mkdir -p /tmp/xor-jev-v2-review
/tmp/xor-jev-v2-review-venv/bin/python -m zipfile -e \
  experiments/jev_failure_correlation_v2/evidence/cloud-20261024-01.zip \
  /tmp/xor-jev-v2-review
/tmp/xor-jev-v2-review-venv/bin/python -m experiments.jev_failure_correlation_v2.artifacts verify \
  /tmp/xor-jev-v2-review/cloud-20261024-01
/tmp/xor-jev-v2-review-venv/bin/python -m experiments.jev_failure_correlation_v2.runner score \
  --out /tmp/xor-jev-v2-review/cloud-20261024-01
/tmp/xor-jev-v2-review-venv/bin/python -m experiments.jev_failure_correlation_v2.artifacts verify \
  /tmp/xor-jev-v2-review/cloud-20261024-01
```

Scoring validates raw wire/request identity, the exact pinned Jev model and typed distributions, native response joins, observed probe re-projections, composed decisions and the audit sampling design. It uses no credentials. Run-directory relocation is supported; original absolute paths inside raw metadata describe execution history, while replay links use the extracted run root.

For the supplemental archive, extract it alongside the main run, then run the committed supplemental script's `replay` command with `--out` for the supplement and `--main` pointing at the extracted main run. The `--main` relocation does not change the frozen plan or its expected input hashes. See `evidence/supplements/PLAN.md` and the archive index for exact names.

Representative executable checks:

```sh
/tmp/xor-jev-v2-review-venv/bin/python -m pytest \
  experiments/jev_failure_correlation_v2/tests \
  experiments/jev_failure_correlation/tests \
  experiments/jev_failure_correlation/test_corpus.py tests/hexagonal -q
```

Some protocol tests require local sockets. A sandbox socket denial is `not_exercised`, not successful fault detection. Use GitHub-hosted validation or an environment permitting those local test sockets; do not weaken the assertions. Installed vendor behavior is not established by these protocol peers.

## Claims to attack

1. Verify that the explicit structured operands make the strong comparator appropriate, while limiting generalization to unstructured operational incidents.
2. Check whether each initial judgment is supported without later probe truth, and whether every post-probe row actually receives the decisive observation credited to it.
3. Keep seven dependency groups distinct from 24 cases, 30 robustness transformations, 22 native invocations and repeated probe runs.
4. Recompute the two confident P4 false clears, including the fact that both escaped the 13-case random audit sample. A zero sample estimate is not absence of error.
5. Separate stable wrong judgments from robustness failures: irrelevant context and option reversal did not flip judgments, but some judgments were wrong before and after.
6. Check corrections, newly introduced uncertainty, probe resolutions and actual acceptance-state effects separately. No policy improved the context end-to-end subset.
7. Audit the supplemental five-probe comparison for hidden-key selection, source/input substitution, unequal prefix costs, cost/time mismatch and post-hoc exposure. The non-Jev candidate selector and Jev-selected candidate route are not an isolated routing-only treatment.
8. Recompute the public Jev list-price estimate and sensitivity to native calls, review time and probe cost. Unknown subscription/invoice/queue/labor costs must remain unknown.
9. Verify actual native separate invocation records, while preserving the limitation that exact native model/effort and provider billing metadata were not exposed. They are not implementations' retrospective opinions or API-model attestations.
10. Test the known-bad controls and raw-response/action tamper rejection; do not infer sufficiency from valid hashes or green CI alone.

Report E01–E14 as demonstrated, contradicted or unverified with concrete evidence. A valid negative result is acceptable. Distinguish implementation correctness, measurement coverage and economic usefulness. The recommendation can change if independently sampled operational evidence, broader relationship controls, or properly matched investigation costs contradict this pilot.
