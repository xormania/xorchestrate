# Xorchestrate implementation handoff

This branch implements the complete scope of the supplied
[owner brief](owner-brief.md), sections 2–10. It is prepared for review; no merge
or release has been performed. The baseline is
`30254fd0f184b7e31aa7e0896347429004933ed4` on `integration/hexagonal`.

The exact measured candidate and run outcomes are in the accompanying
[`evidence/final-manifest.json`](evidence/final-manifest.json), and every requirement
is mapped in [`evidence/requirement-matrix.md`](evidence/requirement-matrix.md).
The evidence publication commit may contain documentation/evidence additions after
the measured code commit. Check out the **candidate commit named in that manifest**
when reproducing its content hash; keep the evidence publication checkout separate.
Generated evidence is not represented as a new measured code input.

## Read and reproduce

1. Read [change/expectation review](CHANGE-REVIEW.md) and
   [delegation/integration record](EXECUTION.md). Internal reviews are recorded
   implementation checks; independent fresh-session review is still separate.
2. Install Python 3.11+ and the candidate's dependencies:
   `python -m pip install -e '.[test]' -r .github/requirements-ci.txt`.
3. Run `python -m pytest tests/hexagonal -q`. The final command record identifies
   executed tests, skips and environmental limitations. Tests that require tmux
   need tmux and an operating system permitting its Unix socket; a skip is not a pass.
4. Run `python -m ruff check xorchestrate tests devtools .github scripts`,
   `python .github/lint_checks.py`, and the dependency checks in
   `tests/hexagonal/test_boundaries.py`.
5. Use [the full verification procedure](../behavioral-verification.md) to preserve
   the original brief and selected criteria **outside** the candidate, then run
   `scripts.verification.multi_harness` in `protocol-fixture` mode. This exercises
   actual Codex, Claude and Grok adapter code against deterministic subprocess peers,
   including routing, storage, native observations and external-host lifecycle.
6. Calibrate the frozen candidate in separate output directories:
   `python -m scripts.verification.calibrate_suite --out /absolute/new-native-calibration`
   and `python tests/hexagonal/calibrate_work_acceptance.py --output /absolute/new-work-calibration`.
   The inventories retain exact source mutations, named assertions and unaffected
   controls. A setup failure, unrelated failure, skipped case or escaped fault
   never counts as detection.

## Local authenticated native acceptance

Follow [behavioral verification](../behavioral-verification.md), using
[`scripts/verification/native-config.example.json`](../../scripts/verification/native-config.example.json)
as a configuration shape. Supply installed executable paths, workspace, desired
models/settings, permissions and authenticated environments. The procedure records
versions and requested/resolved/observed settings, including mismatches and unknowns.

Use `--mode native` only for the authenticated local run. The same 46-case inventory
requires six directed exchanges, three held-tool submissions, six unfinished-work
exchanges, nine work batches, persistent private markers, all actual native calls,
and separate resume/attach/detach/reconnect/replacement scenarios. Busy submission
is measured inside the outstanding hold; it does not assert token-generation
interruption. Reply-before-work-completion timing is recorded separately.

Communication, work correctness, turn completion and acceptance have separate
results. A native report cannot be substituted by a fixture report. An unavailable
protocol or required case stays incomplete with its evidence. Native acceptance
and the section 11 fresh-session review remain pending until actually performed.

Keep native state, gateway descriptors, host resume secrets and raw traces private.
The included deterministic evidence uses synthetic data; published derivatives
redact resumable bridge credentials and identify their original hashes. Reproduce
raw local evidence from the exact candidate when reviewing sensitive correlations.
Ownership-aware cleanup is part of each run. The bridge recovery documentation
explains how to confirm a crashed host exited before removing its stale endpoint.
