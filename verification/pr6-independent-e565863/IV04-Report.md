# IV04 independent verification

**Verdict: the demonstrated false-detection defect is closed on `e565863ae72180571678c8f6118ee6e00b129e2e` within the executed scope.** No unrelated or ambiguous exception earned credit in the fresh challenges. Genuine assertions remain detectable in normal rewritten/plain execution and in all twelve original source calibrations. Two valid Python source encodings reveal an explicit sensitivity limitation, described separately below; they do not recreate false detection.

The exact tree is `474de55c5947890ab4092daeca7ff39f9bbbc400`. The original candidate was `ef7567516520543970af55b6f19534cb66eda357`, tree `a0813aae760e6e5bb6245693bd92b7e7c321348b`. These product checkouts came from the root's fresh GitHub clone and remained clean. The repair evidence publication `37547a138758fe70e6cc471b491aaab01bfc3a05` supplied the handoff and retained witness, not the measured product. The original review publication is `3ac2342b6655f1a30652380c85ceb55262b7ad0c`. Root owns publication/archive verification and hosted provenance.

## Independent expectations

Owner brief B4 requires a passing baseline, a targeted fault, and its expected assertion or diagnostic; unrelated failure, setup error, skip and escape remain distinct. The handoff tightens this to the actual intended assertion raise. The fixture designs and external event traces determine whether a true assertion was followed by an unrelated exception, an expression/helper failed, or the designated assertion itself was false. Classifier agreement is measured against those expectations, not treated as an independent oracle.

The source repair uses terminal traceback identity, bytecode offset, `RAISE_VARARGS 1`, complete line/column ranges, and one containing AST assertion. Tests execute in fresh subprocesses with a retained unmodified observer copy. A second checkout of this code provides isolation only.

## Original witness, freshly replayed

`original-baseline/results.json` and `original-candidate/results.json` record the unchanged original witness. Its SHA-256 is `8d7ece0f4bb7af50ab253cad0b5ce2a20dfa6f36152d28477767131d092ed719`. Both calibrators produce:

| Case | Original candidate | Repaired candidate | Semantic expectation |
|---|---|---|---|
| True assertion followed by same-line explicit raise | detected | unrelated_failure | unrelated_failure |
| True assertion followed by next-line explicit raise | unrelated_failure | unrelated_failure | unrelated_failure |
| Actual false assertion | detected | detected | detected |

All six control subprocesses exit 0; all six deliberately faulty subprocesses exit 1. The witness program exits 0 for either candidate because it records results; that exit is not closure evidence. Sources, mutation descriptions, command records, observer copies, logs and XML are retained in the two directories.

## Fresh adversarial challenges

`challenge.py` creates sixteen independently designed cases under each of pytest's rewrite and plain modes. All **32 case/mode combinations** matched the expected classification, control/fault exits and independently written event sequences (`independent-attempt1/results.json`). Each runs a separate valid unaffected control.

Rejection cases include a condition-call exception, exception during boolean coercion, assertion-message helper failure, a context manager throwing after a successful assertion, a finally-block exception, adjacent Unicode/semicolon statements, an exception group, a nested helper assertion, an assertion replaced by a new exception, and an earlier same-line assertion with another requirement's marker. In the last case the observer correctly identifies an assertion, but its different message prevents intended credit. The replacement-error case rejects even though an intended assertion previously failed: the final reported exception is a separate explicit raise.

Successful detection cases include multiline conditional messages, loop assertions, a saved assertion exception re-raised explicitly, context-manager propagation, Unicode identifiers and tab-indented source. These preserve legitimate detection while testing multiple ways the actual failing assertion reaches the report.

`challenge_positions_v2.py` adds ten encoding/runtime-mode combinations:

- UTF-8 with CRLF line endings detects the genuine assertion in rewrite and plain modes.
- Missing debug ranges yield `unrelated_failure` in both modes. The assertion raises, but its executed source span is unavailable; refusing unambiguous credit is the required conservative outcome.
- With `PYTHONOPTIMIZE=1`, rewritten assertions remain detectable; plain assertions are removed by Python and correctly classify `escaped_fault`.
- UTF-8 BOM and a Latin-1 coding-cookie file each execute their intended assertion but classify `unrelated_failure`, in both modes. These four cases miss the independently expected `detected` result. They are **not passing positive controls**.

## Demonstrated optional sensitivity improvement

At `scripts/verification/pytest_calibration.py:42`, `Path(...).read_text()` uses the default text decoding rather than Python source-encoding rules. A valid UTF-8 BOM remains U+FEFF and makes `ast.parse` fail; a valid Latin-1 source fails UTF-8 decoding. The observer then lacks `assertion_span` and refuses detection despite the genuine `RAISE_VARARGS` and correct marker.

Minimal examples are retained under `positions-v2/{rewrite,plain}-{utf8-bom,latin1-cookie}/fault/test_probe.py`, along with raw XML and control sources. Only the encoding differs from a supported UTF-8 assertion fixture. The sources compile and execute under Python; this is not ambiguous raising-position evidence. The semantic expectation is detection. Source decoding that respects the coding cookie/BOM could improve sensitivity.

This is an optional compatibility/sensitivity improvement for the reusable observer, not a demonstrated unsafe acceptance blocker: it fails closed, does not credit an unrelated exception, does not affect the retained UTF-8 product calibration corpus, and does not prevent legitimate detection in supported cases. The IV04 closure is specifically the original false-credit defect; it is not a claim that all valid Python encodings are accepted.

## Original twelve source faults

`audit_calibration_v2.py` independently inspects the root's fresh `results/execution/native-calibration` and `work-calibration` output. It does not call either verdict classifier. It verifies the actual before/after source substitutions, candidate/control source identity, observer-copy hashes, subprocess exits, raw XML failures/errors/skips, exact assertion messages, AST assertion source, function names and retained raising spans. Every original source fault remains applied and valid. Native case definitions are byte-identical to the original candidate's file; the five work-case definition objects are identical.

`fresh-calibration-audit-v2.json` records **12 faults, 36 passing control cases, 19 intended assertion failures and 17 unaffected mutant passes, zero errors or skips**. Each of the 19 failures has the declared literal assertion marker and one AST assertion at its recorded span. Those original fault detections were valid before this repair and remain valid now; the original independent report did not disprove them.

## Preservation, failed attempts and limits

No product, test, imported historical runtime, or Jev source was changed. Only this external review directory contains new files. `summary.json` records both clean checkout identities, observer hashes, the original-witness hash and result summaries. Root's broader full-suite and composed-protocol execution provide preservation coverage outside this narrow observer review.

All attempts remain retained. `positions-attempt1` used an incorrect expectation for BOM/Latin-1: it expected the implementation's conservative outcome instead of the valid-source semantic outcome. It receives no positive sensitivity credit. `challenge_positions_v2.py` corrects that oracle and reruns the exact fixtures; exit 1 is expected because the four misses are real. `audit_calibration.py` initially looked for work mutations in `application.py`, a reviewer path mistake; its failed log remains. V2 uses the actual `acceptance.py` target and passes. The original scripts were unchanged. A preliminary system-Python dependency check found no pytest; executions use the root's recorded Python 3.12.14 virtual environment and exact dependencies.

Fresh local execution here is Python 3.12.14, pytest 9.1.1. Hosted Python 3.11 evidence is separately audited by the provenance track and is not claimed as a fresh IV04 probe run here. This review does not assert tamper-resistant isolation from malicious self-modifying tests/plugins or forged observer records. It does establish the bounded actual-subprocess attribution cases above. Authenticated vendors are irrelevant to this observer boundary and remain the user's separate local work.

## Replay

Use fresh output directories and the shared recorded dependencies. Substitute absolute paths; no network credentials are needed for these probes.

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$RESTORED/inputs/original-witnesses/iv04.py" --candidate "$BASELINE" --out "$FRESH/original-baseline"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$RESTORED/inputs/original-witnesses/iv04.py" --candidate "$CANDIDATE" --out "$FRESH/original-candidate"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" challenge.py --candidate "$CANDIDATE" --out "$FRESH/independent"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" challenge_positions_v2.py --candidate "$CANDIDATE" --out "$FRESH/positions"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" audit_calibration_v2.py --candidate "$CANDIDATE" --baseline "$BASELINE" --execution "$EXECUTION" --out "$FRESH/calibration-audit.json"
```

The primary challenge and audit should exit 0. The positions replay should exit 1 solely for the four documented encoding-sensitivity misses. Inspect the JSON and raw XML, not exit codes alone. The candidate root and original baseline must be exact checkouts; `EXECUTION` is the root recorder's fresh execution directory, with the twelve calibrations already executed.
