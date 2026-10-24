# Jev failure correlation experiment v1

This experiment measures narrow mechanism, relationship and next-probe choices over independently retained synthetic failure evidence. It changes no product policies and decides neither product acceptance nor deployment. See `REPORT.md` for measured results, `INDEPENDENT_REVIEW.md` for limitations, and `PROBES.md` for execution scope.

The source base is `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` (tree `0f4034870f40fb5a1d022c9f35ad0b866236455f`), also the current repair branch / reviewed PR #5 head at experiment start. The initial `master` checkout remains unchanged. The experiment branch is `experiment/jev-failure-correlation-v1`. Resolve its published head with `git rev-parse HEAD`; an evidence branch identifies the exact source commit in its workflow metadata. Source and material input hashes are retained in each run's `matrix.json`.

## Install and offline replay

From the experiment checkout root, Python 3.11+:

```sh
python3 -m venv /tmp/xor-jev-venv
/tmp/xor-jev-venv/bin/python -m pip install -e '.[test]'
# The reused imported validator initializes its installed role catalog, as in existing CI.
mkdir -p .agents
cp -R reference/imported-agent-skills .agents/skills
/tmp/xor-jev-venv/bin/python -m pytest experiments/jev_failure_correlation/tests experiments/jev_failure_correlation/test_corpus.py -q
/tmp/xor-jev-venv/bin/python -m experiments.jev_failure_correlation.runner prepare --out /tmp/jev-offline-v1
/tmp/xor-jev-venv/bin/python -m experiments.jev_failure_correlation.runner score --out /tmp/jev-offline-v1
/tmp/xor-jev-venv/bin/python experiments/jev_failure_correlation/probes.py --predictions /tmp/jev-offline-v1/baseline.json --selector baseline --out /tmp/jev-offline-v1/baseline-probes
```

Use a new output directory for each execution. Scoring existing retained predictions requires no key. For a downloaded live result directory, run `runner score --out PATH`; it validates frozen identities before recomputing. Unknown, unrun, incorrect, invalid and provider-failed observations remain distinct. Raw typed responses are in `attempts.jsonl`; exact sanitized request bodies are in `requests/`. Predictions are summaries and must remain joined to their attempts and material inputs.

`data/` packages only selected synthetic observations and their source references, not the entire review ZIP. `data/provenance.json` records reversible ID mapping, original raw observations and extraction omissions. `data/answer_key.json` is excluded from all model requests. Freeze metadata records hashes; changes require a disclosed new experiment version. Original raw evidence remains bound to the reviewed revision.

The supplied archive SHA-256 is `67218f0e6a1e180d3368ea25ed8f766a03dc921e96b085b9ee287c1c82b40086`. Extract it outside the checkout for corpus reconstruction using `corpus.py` (see its CLI help). All 1,831 original manifest entries and the embedded owner brief hash were independently verified. Do not overwrite retained original evidence when replaying its historical scripts.

## Fresh authenticated evaluation

The workflow is `.github/workflows/jev-failure-correlation.yml`. A push changing its scoped experiment files on `experiment/jev-failure-correlation-v1` triggers it without merging to `master`. It never runs untrusted fork code with secrets. The repository Actions secret `TYPESAFE_API_KEY` is injected only into the live evaluation step. Configure or reuse it in repository Settings → Secrets and variables → Actions; do not paste it into shell commands, files or chat.

The explicit live command, executed by that workflow, is:

```sh
python -m experiments.jev_failure_correlation.runner live --out OUTPUT
```

A smoke call must authenticate, validate the typed schema and return the exact pinned `jev-1.13.0` identity before measured requests are sent to `https://api.typesafe.ai/v1/systemone`. An unavailable model is a failed attempt, with no automatic alias fallback. Current availability is established only by that smoke. No direct Cloud key is required. Maximum attempts and timeouts are declared in `matrix.json`; all attempts are retained. No provider price was verified, so monetary cost is unknown rather than invented.

Artifacts upload even after an incomplete evaluation. The scoped evidence-publication branch permits Git retrieval when the Cloud network blocks `api.github.com`; its metadata preserves the GitHub run/artifact identities. Inspect workflow status as well as predictions: a successful offline stage does not establish authenticated measurement.

The preserved H1–H8/B1–B5/A1–A2 requirements remain the external basis. F06 establishes the overlapping Claude schedule using a protocol peer; installed-vendor reachability is unverified. No result here establishes authenticated native harness acceptance.

Preflight revision 1.0.0 was scored offline before a chronology-only F05 packet correction and inclusion of corpus tests in CI. Final revision 1.1.0 discloses that change; baseline rules were not tuned, and no Jev call occurred before the correction. Only final revision 1.1.0 supplies measured live comparisons.

## Retrieve this completed measurement

```sh
git fetch origin experiment/jev-failure-correlation-v1 evidence/jev-failure-correlation-40065048378-1
mkdir /tmp/jev-actions-evidence
git archive 9dd1f434b27d41aa0297ccf120a38de1b5f90fb1 | tar -x -C /tmp/jev-actions-evidence
python -m experiments.jev_failure_correlation.runner score --out /tmp/jev-actions-evidence/experiment-results/evaluation
```

The final experiment branch preserves the measured source, corpus, rubric and workflow hashes, so scoring remains valid after report/evidence publication. For an exact original source checkout, use `454f6f25cb01d0aca793e21c72a032f8428535a9`. A fresh live re-evaluation is a separate explicit action; this report update does not trigger another batch.

The original hosted p4 probe has a JUnit classname/root-discovery limitation. Keep its `not_exercised` result. The separate corrected probe harness 1.2.0 can replay the same selections locally without another model call:

```sh
python experiments/jev_failure_correlation/evidence/probe_replay_v1_2.py --predictions /tmp/jev-actions-evidence/experiment-results/evaluation/baseline.json --selector baseline --out /tmp/jev-corrected-baseline
python experiments/jev_failure_correlation/evidence/probe_replay_v1_2.py --predictions /tmp/jev-actions-evidence/experiment-results/evaluation/predictions.json --selector jev --out /tmp/jev-corrected-jev
```

Recorded corrected replays are in `evidence/probe-replay-v1.2-*`. They are local measurements; the original hosted p4 limitation remains visible. The full original published Actions results are also retained at `evidence/actions-40065048378/` for convenience. `publication.json` supplies the run URL, artifact ID/digest and exact measured source identity. All initial and final source/probe/control observations remain separate.
