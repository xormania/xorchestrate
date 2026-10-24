# IV01 / IV02 independent additional controls

No material blocker was found in this finite pass against product commit
`1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`, tree
`75ecb3ef4edbc381012fe74e416a9abd3fb5c667`.

The six additional scenarios passed on their first execution. These supplement
the root review's original defect replays and documented closure cases; they do
not replace those cases or claim authenticated vendor acceptance.

## Independent expectations

`probe.py` uses candidate fixture adapters only to arrange real public API calls,
stores, native subprocesses, and externally controlled gates. Expected visibility
comes from canaries assigned by this review and the explicitly selected current
deny/review policy. Expected completion comes from unreleased start, callback,
and continuation gates. Candidate projection or verification logic supplies no
expected answer. The checker under test supplies the observed verdict only.

The script records the imported module path and clean Git commit/tree/status
before and after execution. Both source identity records match, and the product
worktree stayed clean. No product implementation was modified.

## IV01: multiple actors and recursively retained history

On MemoryStore and SQLiteStore, two separate actors produced assessments of one
work item. The first actor's native history included an older `get_work` result
containing both assessments, then a retained `native_observations` result
containing that history. Three policy transitions were applied after recording:
deny worker, review worker-two, then review worker. Current `get_work` and all
retained historical copies were inspected by a separate observer.

All six policy/store checks removed the denied actor's unique private canary
from the entire returned representation while preserving the other actor's
canary. Opaque caller/provider results and material inputs remained exact. Stored
records and evaluator input contexts stayed byte-equivalent as JSON values, with
no reevaluation. Restoring all grants reproduced the exact original current and
historical views on both stores. These checks exercise selective actor denial
through an extra typed history nesting level beyond the supplied contract.

Raw evidence: `run-1/inspection-{memory,sqlite}/`, especially `authorized.json`,
the three policy views, `trusted-before.json`, and `restored.json`.

## IV02: newer work finishes while accepted work has not started

The additional schedule submitted A with a held continuation, B with its start,
callback and continuation independently held, and C with a held continuation.
C finished first, then A; B remained accepted but had no native start or callback.
The schedule next released B's start, callback and continuation separately.
It ran with Codex and Grok peers over MemoryStore/direct and SQLiteStore/FIFO.

Across four executions, all twenty held-work stages had live activity `active`
and verifier status `incomplete`, both on original observations and on a copy
with only a forged final idle appended. All four released controls reached live
`idle` and verifier `passed`. The critical B-only stage rejected forged idle with
`completion_native_turn_unresolved`. This preserves the distinction between live
activity and the verifier's conclusion.

`raw_audit.py` independently inspects the saved JSON using only the standard
library. All four raw audits passed. They establish three distinct accepted
submission IDs; A/C terminal records while B is held; no B native tool or callback
before start release; and eventual B native-tool, requested/completed callback,
and native completion records. For Grok they also inspect B's actual
`grok/_x.ai/interject`, `accepted: true`, matching interjection/returned turn ID.
All twenty-four forged variants preserve every original raw record and add only
one idle observation. Direct captures retain explicit start notifications;
bridge captures retain the native-tool/completion envelopes, not those raw
notifications. Physical start gates and callback observations establish the
schedule independently of that capture distinction.

Raw evidence: `run-1/completion-*/capture-01.json` through `capture-06.json`, the
corresponding forged copies and stage reports, plus
`run-1/independent-raw-audit.json` and `run-1/results.json`.

## Commands and retained failed attempts

```sh
PYTHONDONTWRITEBYTECODE=1 /workspace/work/pr6-final/venv/bin/python \
  probe.py --candidate /workspace/work/pr6-final/inspection-completion \
  --out /workspace/work/pr6-final/inspection-completion-results/run-1
python raw_audit.py run-1
```

Use a new output path for a fresh run. The script creates short `/tmp/ic-final-*`
directories for socket/FIFO operation and retains copies of regular evidence
after the fixtures close. The published scripts can be replayed with another
pinned checkout and compatible environment by changing these paths.

The primary probe had no failed attempts. The supplementary raw auditor had two
reviewer schema assumptions, preserved as `raw_audit_v1.py`/`raw-audit.log` and
`raw_audit_v2.py`/`raw-audit-v2.log`: V1 assumed Codex submissions stored message
text at the Grok path; V2 assumed bridge captures retained direct native start
notifications. Actual raw records showed the Codex request under
`result.wire.input[0].text` and bridge native-tool/completion envelopes. V3 reads
those actual formats while retaining accepted-work, no-callback-before-release,
terminal, and exact forged-delta requirements. No product execution was repeated
and no captured evidence was edited. These audit failures are not product defects
and are not counted as passing executions.

No in-scope check assigned to this track remains unexecuted. This finite pass
does not assess authenticated vendors, arbitrary schedules, or the separate
repository-wide architecture and synchronization follow-ups.
