# Keyless replay and independent controls

Use Git history containing the frozen product subject. Review scripts live in this directory's `scripts/`; original source and archives remain untouched. Choose fresh output directories. Python 3.11+ is supported by the project; this review used 3.12.14 locally and 3.12.15 hosted.

```sh
git clone https://github.com/xormania/xorchestrate.git xorchestrate-pr7-review
cd xorchestrate-pr7-review
git checkout verification/pr7-independent-cfa3549
# For an immutable checkout, replace the branch above with the publication SHA
# from the review's delivery message. The tested subject stays cfa3549490ee9db1c3e6cad918523cd854902e51.
python3 -m venv /tmp/pr7-review-venv
/tmp/pr7-review-venv/bin/python -m pip install -e '.[test]'
mkdir -p .agents
cp -R reference/imported-agent-skills .agents/skills
mkdir -p /tmp/pr7-review-raw
/tmp/pr7-review-venv/bin/python -m zipfile -e experiments/jev_failure_correlation_v2/evidence/cloud-20261024-01.zip /tmp/pr7-review-raw
/tmp/pr7-review-venv/bin/python -m zipfile -e experiments/jev_failure_correlation_v2/evidence/supplement-20261024-02.zip /tmp/pr7-review-raw
```

The following commands make no provider calls and need no credentials:

```sh
/tmp/pr7-review-venv/bin/python -m experiments.jev_failure_correlation_v2.artifacts verify /tmp/pr7-review-raw/cloud-20261024-01
/tmp/pr7-review-venv/bin/python -m experiments.jev_failure_correlation_v2.runner score --out /tmp/pr7-review-raw/cloud-20261024-01
/tmp/pr7-review-venv/bin/python -m experiments.jev_failure_correlation_v2.artifacts verify /tmp/pr7-review-raw/cloud-20261024-01
/tmp/pr7-review-venv/bin/python experiments/jev_failure_correlation_v2/evidence/supplements/random_probe.py replay --out /tmp/pr7-review-raw/supplement-20261024-02 --main /tmp/pr7-review-raw/cloud-20261024-01

/tmp/pr7-review-venv/bin/python reviews/pr7-independent-cfa3549/scripts/recompute_raw.py /tmp/pr7-review-raw/cloud-20261024-01 --output /tmp/pr7-recomputed.json
/tmp/pr7-review-venv/bin/python reviews/pr7-independent-cfa3549/scripts/recompute_economics.py --main /tmp/pr7-review-raw/cloud-20261024-01 --supplement /tmp/pr7-review-raw/supplement-20261024-02 --output /tmp/pr7-economics.json
/tmp/pr7-review-venv/bin/python reviews/pr7-independent-cfa3549/scripts/verify_provenance.py --repo "$PWD" --out /tmp/pr7-provenance
PYTHONPATH=. /tmp/pr7-review-venv/bin/python reviews/pr7-independent-cfa3549/scripts/methodology_checks.py /tmp/pr7-methodology.json
```

`recompute_raw.py` and `recompute_economics.py` use stdlib only, without importing the original scorer or reading its evaluation summary as truth. They reconstruct actions, native/raw joins, routes, audits, acceptance summaries, scenarios and costs. They preserve the declared outcome key; fresh executable controls separately assess the behavioral grounding. `verify_provenance.py` uses explicit pinned Git revisions, so review-only additions do not change its candidate identity.

Run independent post-hoc behavioral/tamper controls on a host permitting local sockets; each output directory must be new:

```sh
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY /tmp/pr7-review-venv/bin/python reviews/pr7-independent-cfa3549/scripts/reproduce_controls.py --repo "$PWD" --original /tmp/pr7-review-raw/cloud-20261024-01 --out /tmp/pr7-controls-new
/tmp/pr7-review-venv/bin/python reviews/pr7-independent-cfa3549/scripts/tamper_replay.py --repo "$PWD" --original /tmp/pr7-review-raw/cloud-20261024-01 --out /tmp/pr7-tamper-new
env -u TYPESAFE_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY /tmp/pr7-review-venv/bin/python -m pytest experiments/jev_failure_correlation_v2/tests experiments/jev_failure_correlation/tests experiments/jev_failure_correlation/test_corpus.py tests/hexagonal -q -ra --junitxml=/tmp/pr7-tests.xml
```

The tamper script mutates disposable copies only. Expected result: untouched control passes; six mutations fail semantic scoring and six reveal its coverage gaps; every changed copy fails the unchanged complete seal. Do not replace the original outcomes with mutation results. The published independent crosscheck additionally confirms the raw recomputation rejects those six omissions.

For local environments without tmux or sockets, retain `not_exercised`/skip/failure evidence. The review's exact scoped GitHub workflow and original hosted ZIP provide an independently executed route, not permission to weaken assertions or silently substitute fixture answers. See `EXECUTION.md`.

For saved-behavior-only replay, extract `evidence/behavior-controls.zip` and run `reproduce_controls.py` with `--out <extraction>/controls-final --summarize-existing`, plus the same `--repo` and `--original`. This explicitly aggregates retained records and does not execute new behavior. Absolute paths inside historical records describe their original execution; published scripts accept relocated roots.

Verify the review package's own retained bytes from the repository root:

```sh
python3 - <<'PY'
import hashlib, json
from pathlib import Path
root=Path('reviews/pr7-independent-cfa3549')
expected=json.loads((root/'SHA256.json').read_text())
actual={str(p.relative_to(root)):{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file() and p.name!='SHA256.json' and '__pycache__' not in p.parts}
assert actual==expected
print('Review byte inventory verified; validity remains a separate question.')
PY
```
