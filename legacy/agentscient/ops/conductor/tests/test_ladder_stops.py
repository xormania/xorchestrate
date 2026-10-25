"""The ladder driver's stops at the check-tests boundary as case tables
(plan slice 13): ENVELOPE_STOP_CASES, the eleven malformed fields of a
skeptic envelope that stop the ladder with class envelope (item 3 of
scope-item31-20260911.md), and SKEPTIC_ANSWERS_CASES, LD-23's two rows:
an undisposed skeptic finding stops before tests-fix, and a cited one
reaches tests-fix with one --answers per key. Rows by id with their
origin; one generated test method per row on a fresh fixture. The
subject is the staged driver and is not read here. The planted faults
are ops/conductor/tests/ladder_faults_stops.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from test_ladder_lever import (
    WELL_FORMED_SKEPTIC_COUNTS,
    WELL_FORMED_SKEPTIC_FINDING,
    _LadderFixture,
)
from test_ladder_lever_undisposed import _UndisposedFixture

from ops.testkit import World, run  # noqa: F401 - inherited fixture uses both

ZERO_COUNTS = {"p1": 0, "p2": 0, "p3": 0, "opinions": 0}
PROSE_CLAIM = "Contract item 9 requires a fresh check"
EMPTY_PAIR_CLAIM = "declared (ops/devlane/task/CONTRACT.md): ; practiced: ; misses: x"
ITEM3 = "scope-item31-20260911.md item 3"


def _finding(**overrides):
    row = dict(WELL_FORMED_SKEPTIC_FINDING)
    row.update(overrides)
    return row


@dataclass(frozen=True)
class EnvelopeRow:
    id: str
    origin: str
    field: str                       # the field the envelope refusal must name
    verdict: str = "changes"
    findings: object = None          # None means one well-formed finding
    counts: dict = field(default_factory=lambda: dict(WELL_FORMED_SKEPTIC_COUNTS))
    artifacts: dict = field(default_factory=dict)
    named: tuple = ()                # further strings the line must carry
    note: str | None = None


ENVELOPE_STOP_CASES = [
    EnvelopeRow("artifacts-not-empty", ITEM3, "artifacts", artifacts={"skeptic": "SKEPTIC.md"}),
    EnvelopeRow("findings-not-a-list", ITEM3, "findings", findings={"claim": PROSE_CLAIM}),
    EnvelopeRow("finding-with-a-fifth-field", ITEM3, "findings[0]", findings=[_finding(extra="not-a-field")], named=("extra",)),
    EnvelopeRow("severity-unknown", ITEM3, "findings[0].severity", findings=[_finding(severity="high")]),
    EnvelopeRow("where-empty", ITEM3, "findings[0].where", findings=[_finding(where="")]),
    EnvelopeRow("claim-prose", ITEM3, "findings[0].claim", findings=[_finding(claim=PROSE_CLAIM)], named=(PROSE_CLAIM,)),
    EnvelopeRow("claim-empty-quote-halves", ITEM3, "findings[0].claim", findings=[_finding(claim=EMPTY_PAIR_CLAIM)]),
    EnvelopeRow("reproduce-null", ITEM3, "findings[0].reproduce", findings=[_finding(reproduce=None)]),
    EnvelopeRow("counts-off-by-one", ITEM3, "counts", counts=dict(ZERO_COUNTS)),
    EnvelopeRow("verdict-approve-with-a-finding", ITEM3, "verdict", verdict="approve"),
    EnvelopeRow("verdict-changes-with-no-findings", ITEM3, "verdict", findings=[], counts=dict(ZERO_COUNTS)),
]


class EnvelopeStops(_LadderFixture):
    def run_row(self, row: EnvelopeRow) -> None:
        findings = [_finding()] if row.findings is None else row.findings
        envelope = {"verdict": row.verdict, "findings": findings, "counts": row.counts, "artifacts": row.artifacts}
        self.patch_plant(verdicts={"check-tests": row.verdict}, skeptic_envelope=envelope)
        self.assertEqual(self.load_plant()["skeptic_envelope"], envelope)
        proc = self.run_ladder()
        line = self.assert_refusal(proc, "envelope")
        self.assertIn(row.field, line, line)
        for text in row.named:
            self.assertIn(text, line, line)
        self.assertNotIn("tests-fix", self.dispatch_names())
        verbs = [argv[0] if argv else "" for argv in self.findings_argvs()]
        self.assertNotIn("import", verbs, "item 3: a malformed envelope is never imported")
        record = self.ladder_record()
        stage_row = self.stage_row(record, "check-tests")
        self.assertEqual(stage_row.get("reason"), "envelope", stage_row)
        self.assertEqual(self.dispatch_names().count("check-tests"), 1, self.dispatch_names())


@dataclass(frozen=True)
class AnswersRow:
    id: str
    origin: str
    cited_all: bool
    stop: bool                       # stops before tests-fix with record none, reason undisposed
    note: str | None = None


SKEPTIC_ANSWERS_CASES = [
    AnswersRow("undisposed-stops-before-tests-fix", "LD-23", cited_all=False, stop=True, note="PR 133"),
    AnswersRow("cited-reaches-tests-fix-with-answers", "LD-23", cited_all=True, stop=False, note="PR 133"),
]

SKEPTIC_TWO = tuple(
    {**WELL_FORMED_SKEPTIC_FINDING, "where": f"ops/conductor/tests/test_alpha.py:{n}"} for n in (1, 2)
)


class SkepticAnswers(_UndisposedFixture):
    """The skeptic says changes with two findings; the audits approve."""

    def plant_skeptic_changes(self, *, cited_all: bool) -> None:
        self.patch_plant(
            verdicts={"check-tests": "changes"},
            stage_findings={"check-tests": [dict(row) for row in SKEPTIC_TWO]},
            skeptic_envelope={"counts": {"p1": 0, "p2": 2, "p3": 0, "opinions": 0}, "artifacts": {}},
            cited_all=cited_all,
        )
        plant = self.load_plant()
        self.assertEqual(plant["verdicts"].get("check-tests"), "changes")
        self.assertEqual(len((plant.get("stage_findings") or {}).get("check-tests") or []), 2)
        self.assertEqual(bool(plant.get("cited_all")), cited_all)

    def skeptic_keys(self) -> list[str]:
        self.assertIn("check-tests", self.dispatch_names())
        self.assertEqual(len(self.closed_envelope("check-tests").get("findings") or []), 2)
        keys = self.envelope_keys("check-tests")
        self.assertEqual(len(keys), 2, keys)
        return keys

    def run_row(self, row: AnswersRow) -> None:
        before_store = self.findings_store_snapshot()
        before_disp = self.dispositions_snapshot()
        self.plant_skeptic_changes(cited_all=row.cited_all)
        proc = self.run_ladder()
        keys = self.skeptic_keys()
        if row.stop:
            self.assert_stopped(proc, stage="tests-fix", record="none", reason="undisposed")
            self.assert_output_names_keys(proc, keys)
            self.assertNotIn("tests-fix", self.dispatch_names())
            self.assert_driver_did_not_write_dispositions(before_store, before_disp)
        else:
            self.assertIn("tests-fix", self.dispatch_names(), self.combined(proc))
            self.assert_answers_every_key("tests-fix", keys)
            self.assert_item1_shape("tests-fix")


def _method(row):
    def test(self):
        self.run_row(row)
    test.__name__ = "test_" + row.id.replace("-", "_")
    test.__doc__ = f"{row.origin}: {row.id}"
    return test


for _rows, _cls in ((ENVELOPE_STOP_CASES, EnvelopeStops), (SKEPTIC_ANSWERS_CASES, SkepticAnswers)):
    _ids = [row.id for row in _rows]
    assert len(_ids) == len(set(_ids)), "rows are addressed by id"
    for _row in _rows:
        setattr(_cls, "test_" + _row.id.replace("-", "_"), _method(_row))

# unittest discovers class objects even under private names. Do not leave
# a second reference to SkepticAnswers for it to collect again.
del _cls
