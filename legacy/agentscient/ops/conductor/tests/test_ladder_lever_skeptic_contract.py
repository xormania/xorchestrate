"""Document pins for the check-tests stop and the custody file.

Written from contract items 3, 4 and 7 of
`.dev/ladder-inputs/scope-item31-20260911.md`. Slice 1 only: these tests
read `ops/conductor/ladder-lever.md` and never import ladder.py.

Item 3: the driver validates the envelope at the check-tests stop,
class `envelope`, never retried as a flake. Item 4: tests-fix receives
`skeptic-findings.md` the driver wrote; class `custody` refuses a
symlink. Item 7: the verb `check-envelope` is on the instrument.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
LEVER = REPO / "ops" / "conductor" / "ladder-lever.md"

CUSTODY_FILE = "`skeptic-findings.md`"
DRIVER_WROTE = "written by the driver from the validated check-tests envelope"
SKEPTIC_MD = "SKEPTIC.md"
NEVER_RETRIED = "never retried"
RETRY_ONLY = "`--retry` is the only further attempt"
CHECK_ENVELOPE = "`check-envelope`"
CHECK_ENVELOPE_FORM = "PATH|-"
ENVELOPE_SHAPE = '{"field": "<dotted field>", "refusal": "<line>"}'
STOPPED_ENVELOPE = "reason=envelope"
STOPPED_CUSTODY = "reason=custody"
INVALID_ENVELOPE_PROSE = (
    "a stage refuses, trips, dies, records an invalid envelope, "
    "violates a guarantee, or an invoked write aborts"
)
REGULAR_FILE = "regular file"
PATH_AND_DIGEST = "path and digest"
CUSTODY_REFUSAL = (
    "refuses to write through a symlink or outside the inputs directory"
)
ITEM3_CHECKS = (
    "findings is a list of item 2 shapes",
    "counts agree with findings",
    "verdict agrees with item 2",
    "artifacts is empty",
)
ONE_STDERR_LINE = "one stderr line"
STDOUT_EMPTY = "stdout empty"
WIRE_GRAMMAR = "expected …; found …; needed …"
WIRE_GRAMMAR_ASCII = "expected ...; found ...; needed ..."

_EXIT_2 = re.compile(r"(?i)(?<![\w])exit\s+2(?!\d)")

# Item 3 near-miss: names a field and never-retried, skips the four checks.
ITEM3_FIELD_ONLY_MUTANT = (
    "field; never retried; "
    '{"field": "<dotted field>", "refusal": "<line>"}; '
    "validates only the field name and ignores findings, counts, "
    "verdict, and artifacts"
)

# Item 4 near-miss: expected filename, allows a symlink, records no digest.
ITEM4_SYMLINK_MUTANT = (
    "`skeptic-findings.md` written by the driver from the validated "
    "check-tests envelope; custody; files may be symlinks outside the "
    "inputs directory and have no recorded digest"
)

# Item 7 near-miss: exit 30 and two output lines.
ITEM7_EXIT_30_MUTANT = (
    "envelope exit 30 reason=envelope field "
    "the wire line then the stopped line"
)

# Item 7 near-miss: the item-7 phrases plus a following stopped line.
# Survived the first frozen set (security 20260911T224145Z, P2).
ITEM7_THEN_STOPPED_MUTANT = (
    "exit 2, one stderr line `envelope: expected …; found …; needed …`, "
    "stdout empty, then `stopped stage=check-tests record=<id> "
    "reason=envelope`"
)

# Item 3 near-miss: never-retried plus the standing resume unamended.
# Survived the first frozen set (adversarial 20260911T225002Z, P2).
CONSUMES_AND_LANDS = "resume consumes and lands it"
RELAUNCHES_ONCE = "Otherwise it relaunches that stage once."
NOT_CONSUMED = "not consumed and landed"
DOES_NOT_RELAUNCH = "does not relaunch"
SCHEMA_ACCEPTED = "schema-accepted"
ITEM3_UNAMENDED_RESUME_MUTANT = (
    "A malformed envelope records the failed field and is never retried "
    "as a flake; `--retry` is the only further attempt. If its stopped "
    "dispatch record is now closed with an accepted envelope, resume "
    "consumes and lands it without relaunching. Otherwise it relaunches "
    "that stage once."
)


def _item3_checks_hold(text: str) -> bool:
    return all(phrase in text for phrase in ITEM3_CHECKS)


def _item4_custody_holds(text: str) -> bool:
    if CUSTODY_FILE not in text:
        return False
    if REGULAR_FILE not in text:
        return False
    if PATH_AND_DIGEST not in text:
        return False
    return CUSTODY_REFUSAL in text


def _has_wire_grammar(text: str) -> bool:
    collapsed = " ".join(text.split())
    return WIRE_GRAMMAR in collapsed or WIRE_GRAMMAR_ASCII in collapsed


def _item7_refusal_holds(text: str) -> bool:
    if _EXIT_2.search(text) is None:
        return False
    if ONE_STDERR_LINE not in text:
        return False
    if STDOUT_EMPTY not in text:
        return False
    return _has_wire_grammar(text)


def _has_following_stopped_line(text: str) -> bool:
    """True when the refusal output continues with a stopped line.

    Item 7 is one stderr line and empty stdout. A last-output cell that
    adds `then stopped …` is a second line, even when the item-7
    phrases are present.
    """
    collapsed = " ".join(text.split())
    return (
        "then `stopped" in collapsed
        or "then stopped stage=" in collapsed
    )


def _item3_resume_amended(text: str) -> bool:
    """True when never-retried amends standing resume for envelope stops."""
    collapsed = " ".join(text.split())
    if NEVER_RETRIED not in collapsed:
        return False
    if NOT_CONSUMED not in collapsed:
        return False
    if DOES_NOT_RELAUNCH not in collapsed:
        return False
    if SCHEMA_ACCEPTED not in collapsed:
        return False
    return CONSUMES_AND_LANDS not in collapsed


def _exit_cell(row: list[str] | None) -> str:
    if not row:
        return ""
    return row[0].replace("`", "").strip()


def _section(text: str, prefix: str) -> str:
    lines = text.splitlines()
    heading_level = None
    start = None
    for i, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix) :]
        if rest == "" or rest[0] in " \t—-,":
            heading_level = len(line) - len(line.lstrip("#"))
            start = i + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            hashes = len(line) - len(line.lstrip("#"))
            if hashes <= heading_level:
                break
        body.append(line)
    return "\n".join(body)


def _heading_body(text: str, title: str) -> str:
    lines = text.splitlines()
    start = None
    heading_level = None
    for i, line in enumerate(lines):
        if not line.startswith("#"):
            continue
        hashes = len(line) - len(line.lstrip("#"))
        label = line.lstrip("#").strip()
        if label == title:
            start = i + 1
            heading_level = hashes
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("#"):
            hashes = len(line) - len(line.lstrip("#"))
            if hashes <= heading_level:
                break
        body.append(line)
    return "\n".join(body)


def _tables(block: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in block.splitlines():
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(cell and set(cell) <= set("-: ") for cell in cells):
                continue
            current.append(cells)
            continue
        if current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _row_id(cells: list[str]) -> str:
    token = cells[0].replace("`", "")
    return token.split()[0] if token.split() else ""


def _row_names(cells: list[str], ident: str) -> bool:
    """True when a stop row is the named class, not a prose mention.

    The existing exit-3 condition 'records an invalid envelope' must not
    count as the envelope stop row: the ident has to be the cell, or
    the cell's first token, not a later word in a sentence.
    """
    for cell in cells:
        compact = cell.replace("`", "").strip()
        if not compact:
            continue
        if compact == ident:
            return True
        first = compact.split()[0].strip(",;:")
        if first == ident:
            return True
        if compact.startswith(ident + " ") or compact.startswith(ident + "("):
            return True
    return False


def _chunks_naming(text: str, ident: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [chunk for chunk in chunks if ident in chunk]


class _LeverPin(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="ladder-lever-skeptic-")
        self.addCleanup(self.world.close)
        self.assertTrue(LEVER.is_file(), f"missing {LEVER}")
        self.lever_path = self.world.stage(LEVER, "ladder-lever.md")
        self.lever = self.lever_path.read_text(encoding="utf-8")
        self.assertTrue(self.lever.strip(), "ladder-lever.md is empty")

    def section(self, prefix: str) -> str:
        block = _section(self.lever, prefix)
        self.assertTrue(block, f"ladder-lever.md is missing {prefix!r}")
        return block

    def stage_row(self, ident: str) -> list[str]:
        tables = _tables(self.section("## Stage table"))
        self.assertTrue(tables, "stage table is missing")
        for table in tables:
            for cells in table[1:]:
                if _row_id(cells) == ident:
                    return cells
        have = [
            _row_id(cells)
            for table in tables
            for cells in table[1:]
        ]
        self.fail(f"no stage row {ident!r}; have {have}")

    def stage_input(self, ident: str) -> str:
        row = self.stage_row(ident)
        if len(row) > 4:
            return row[4]
        return " ".join(row)

    def stop_row(self, ident: str) -> list[str] | None:
        tables = _tables(self.section("## Stops"))
        for table in tables:
            for cells in table[1:]:
                if _row_names(cells, ident):
                    return cells
        return None

    def stop_blob(self, ident: str) -> str:
        row = self.stop_row(ident)
        if row is None:
            return ""
        return " ".join(row)

    def check_tests_stop(self) -> str:
        return _heading_body(self.lever, "The check-tests stop")


class TestsFixCustodyInput(_LeverPin):
    """Item 4 — tests-fix is given the driver-written custody file."""

    def test_tests_fix_input_names_skeptic_findings_md(self):
        mutant = "scope, plan, skeptic envelope and `SKEPTIC.md`"
        self.assertNotIn(CUSTODY_FILE, mutant)
        self.assertIn(CUSTODY_FILE, self.stage_input("tests-fix"))

    def test_tests_fix_input_is_written_by_the_driver_from_the_validated_envelope(
        self,
    ):
        mutant = "scope, plan, skeptic envelope and `SKEPTIC.md`"
        self.assertNotIn(DRIVER_WROTE, mutant)
        self.assertIn(DRIVER_WROTE, self.stage_input("tests-fix"))

    def test_tests_fix_input_does_not_name_skeptic_md(self):
        cell = self.stage_input("tests-fix")
        self.assertTrue(cell, "tests-fix input cell is empty")
        self.assertNotIn(
            SKEPTIC_MD,
            cell,
            "item 4: tests-fix receives the driver-written file, "
            "not a file the skeptic wrote",
        )

    def test_custody_is_a_regular_file_with_path_and_digest(self):
        mutant = ITEM4_SYMLINK_MUTANT
        self.assertIn(CUSTODY_FILE, mutant)
        self.assertIn(DRIVER_WROTE, mutant)
        self.assertNotIn(SKEPTIC_MD, mutant)
        self.assertFalse(
            _item4_custody_holds(mutant),
            "item 4: a symlink outside the inputs directory with no "
            "digest must not hold",
        )
        surface = "\n".join(
            [
                self.stage_input("tests-fix"),
                self.stop_blob("custody"),
                self.check_tests_stop(),
                self.section("## Record"),
            ]
        )
        self.assertIn(
            REGULAR_FILE,
            surface,
            "item 4: the custody file is a regular file",
        )
        self.assertIn(
            PATH_AND_DIGEST,
            surface,
            "item 4: recorded with path and digest like every other input",
        )
        self.assertTrue(
            _item4_custody_holds(surface),
            "item 4: regular file under the inputs directory, path and "
            "digest, refuses a symlink or a path outside",
        )

    def test_custody_refuses_a_symlink_or_a_path_outside_the_inputs_directory(
        self,
    ):
        mutant = ITEM4_SYMLINK_MUTANT
        self.assertIn("symlink", mutant)
        self.assertIn("outside the inputs directory", mutant)
        self.assertNotIn(CUSTODY_REFUSAL, mutant)
        self.assertFalse(_item4_custody_holds(mutant))
        surface = "\n".join(
            [
                self.stage_input("tests-fix"),
                self.stop_blob("custody"),
                self.check_tests_stop(),
            ]
        )
        self.assertIn(
            CUSTODY_REFUSAL,
            surface,
            "item 4: refuses to write through a symlink or outside "
            "the inputs directory",
        )


class EnvelopeAndCustodyStops(_LeverPin):
    """Item 3 / item 4 / item 7 — stop rows envelope and custody."""

    def test_invalid_envelope_prose_is_not_the_envelope_stop_row(self):
        mutant = ["3", INVALID_ENVELOPE_PROSE, "the last reading, then stopped"]
        self.assertFalse(
            _row_names(mutant, "envelope"),
            "a prose mention of an invalid envelope is not class envelope",
        )
        self.assertIsNotNone(
            self.stop_row("envelope"),
            "stops table has no envelope row",
        )

    def test_custody_proof_prose_is_not_the_custody_stop_row(self):
        mutant = [
            "3",
            (
                "ordered stage rows naming job, harness, attempt, input path "
                "and digest, custody proof, dispatch record"
            ),
            "the last reading",
        ]
        self.assertFalse(
            _row_names(mutant, "custody"),
            "the record's custody proof is not class custody",
        )
        self.assertIsNotNone(
            self.stop_row("custody"),
            "stops table has no custody row",
        )

    def test_envelope_refusal_is_exit_2_one_stderr_line_stdout_empty(self):
        mutant = ITEM7_EXIT_30_MUTANT
        self.assertIn("3", mutant)
        self.assertIn(STOPPED_ENVELOPE, mutant)
        self.assertIsNone(_EXIT_2.search(mutant))
        self.assertFalse(
            _item7_refusal_holds(mutant),
            "item 7: exit 30 and two output lines must not hold",
        )
        row = self.stop_row("envelope")
        self.assertIsNotNone(row, "stops table has no envelope row")
        self.assertEqual(
            _exit_cell(row),
            "2",
            "item 7: envelope refusal is exit 2, not a substring of 20 or 30",
        )
        blob = self.stop_blob("envelope")
        self.assertIn(STOPPED_ENVELOPE, blob)
        surface = "\n".join([blob, self.check_tests_stop()])
        self.assertTrue(
            _item7_refusal_holds(blob) or _item7_refusal_holds(surface),
            "item 7: one stderr line `class: expected …; found …; "
            f"needed …`, exit 2, stdout empty; found {blob!r}",
        )

    def test_custody_refusal_is_exit_2_one_stderr_line_stdout_empty(self):
        mutant = ITEM7_EXIT_30_MUTANT.replace("envelope", "custody")
        self.assertIn("3", mutant)
        self.assertIsNone(_EXIT_2.search(mutant))
        self.assertFalse(_item7_refusal_holds(mutant))
        row = self.stop_row("custody")
        self.assertIsNotNone(row, "stops table has no custody row")
        self.assertEqual(
            _exit_cell(row),
            "2",
            "item 7: custody refusal is exit 2, not a substring of 20 or 30",
        )
        blob = self.stop_blob("custody")
        self.assertIn(STOPPED_CUSTODY, blob)
        surface = "\n".join([blob, self.check_tests_stop()])
        self.assertTrue(
            _item7_refusal_holds(blob) or _item7_refusal_holds(surface),
            "item 7: one stderr line, exit 2, stdout empty; "
            f"found {blob!r}",
        )

    def test_envelope_refusal_is_not_one_stderr_line_then_a_stopped_line(
        self,
    ):
        mutant = ITEM7_THEN_STOPPED_MUTANT
        self.assertTrue(
            _item7_refusal_holds(mutant),
            "plant must land: the item-7 phrases plus a following "
            "stopped line",
        )
        self.assertTrue(
            _has_following_stopped_line(mutant),
            "plant must land: stdout empty, then a stopped line",
        )
        row = self.stop_row("envelope")
        self.assertIsNotNone(row, "stops table has no envelope row")
        blob = self.stop_blob("envelope")
        self.assertFalse(
            _has_following_stopped_line(blob),
            "item 7: one stderr line, stdout empty; a following "
            f"stopped line is a second line; found {blob!r}",
        )

    def test_custody_refusal_is_not_one_stderr_line_then_a_stopped_line(
        self,
    ):
        mutant = ITEM7_THEN_STOPPED_MUTANT.replace("envelope", "custody")
        self.assertTrue(_item7_refusal_holds(mutant))
        self.assertTrue(_has_following_stopped_line(mutant))
        row = self.stop_row("custody")
        self.assertIsNotNone(row, "stops table has no custody row")
        blob = self.stop_blob("custody")
        self.assertFalse(
            _has_following_stopped_line(blob),
            "item 7: one stderr line, stdout empty; a following "
            f"stopped line is a second line; found {blob!r}",
        )

    def test_envelope_stop_names_the_field_and_the_four_checks(self):
        mutant = ITEM3_FIELD_ONLY_MUTANT
        self.assertIn("field", mutant)
        self.assertIn("never retried", mutant)
        self.assertIn("<dotted field>", mutant)
        self.assertFalse(
            _item3_checks_hold(mutant),
            "item 3: naming a field is not checking findings, counts, "
            "verdict, and artifacts",
        )
        blob = self.stop_blob("envelope")
        body = self.check_tests_stop()
        self.assertIn("field", blob)
        self.assertTrue(
            _item3_checks_hold(body) or _item3_checks_hold(blob),
            "item 3: the driver checks findings is a list of item 2 "
            "shapes; counts agree with findings; verdict agrees with "
            "item 2; artifacts is empty",
        )


class CheckTestsStopNeverRetried(_LeverPin):
    """Item 3 — a malformed envelope is never retried as a flake."""

    def test_the_check_tests_stop_paragraph_exists(self):
        body = self.check_tests_stop()
        self.assertTrue(
            body.strip(),
            "ladder-lever.md is missing paragraph The check-tests stop",
        )

    def test_the_check_tests_stop_is_never_retried(self):
        mutant = (
            "The skeptic runs once on the first tests, never again "
            "after a test fix."
        )
        self.assertNotIn(NEVER_RETRIED, mutant)
        body = self.check_tests_stop()
        self.assertIn(NEVER_RETRIED, body)

    def test_retry_is_the_only_further_attempt(self):
        mutant = (
            "A stage has at most two attempts. Resume relaunches that "
            "stage once."
        )
        self.assertNotIn(RETRY_ONLY, mutant)
        body = self.check_tests_stop()
        self.assertIn(RETRY_ONLY, body)

    def test_a_schema_accepted_envelope_that_fails_item_2_is_not_consumed_and_landed(
        self,
    ):
        mutant = ITEM3_UNAMENDED_RESUME_MUTANT
        self.assertIn(NEVER_RETRIED, mutant)
        self.assertIn(RETRY_ONLY, mutant)
        self.assertIn(CONSUMES_AND_LANDS, mutant)
        self.assertNotIn(NOT_CONSUMED, mutant)
        self.assertNotIn(SCHEMA_ACCEPTED, mutant)
        self.assertFalse(
            _item3_resume_amended(mutant),
            "item 3: never-retried plus unamended standing resume "
            "must not hold",
        )
        body = self.check_tests_stop()
        self.assertIn(
            NOT_CONSUMED,
            body,
            "item 3: a schema-accepted envelope that fails item 2 "
            "shape is not consumed and landed",
        )
        self.assertIn(
            SCHEMA_ACCEPTED,
            body,
            "item 3: the carve-out names a schema-accepted envelope",
        )
        self.assertTrue(
            _item3_resume_amended(body),
            "item 3: never retried as a flake amends standing resume",
        )

    def test_standing_resume_does_not_relaunch_an_envelope_stop(self):
        mutant = ITEM3_UNAMENDED_RESUME_MUTANT
        self.assertIn(NEVER_RETRIED, mutant)
        self.assertIn(RELAUNCHES_ONCE, mutant)
        self.assertNotIn(DOES_NOT_RELAUNCH, mutant)
        self.assertFalse(_item3_resume_amended(mutant))
        body = self.check_tests_stop()
        self.assertIn(
            DOES_NOT_RELAUNCH,
            body,
            "item 3: resume does not relaunch an envelope-class stop "
            "as a flake",
        )
        self.assertNotIn(
            RELAUNCHES_ONCE,
            body,
            "item 3: the standing relaunch sentence is not the "
            "envelope-stop rule",
        )


class CheckEnvelopeVerb(_LeverPin):
    """Item 7 — the verb check-envelope lives on the instrument."""

    def test_instrument_names_the_check_envelope_verb(self):
        mutant = (
            "Review stages import their envelope through findings.py import"
        )
        self.assertNotIn(CHECK_ENVELOPE, mutant)
        self.assertIn(CHECK_ENVELOPE, self.section("## Instrument"))

    def test_check_envelope_accepts_a_path_or_stdin(self):
        mutant = "`check-envelope` reads whatever file the skeptic wrote"
        self.assertNotIn(CHECK_ENVELOPE_FORM, mutant)
        self.assertIn(CHECK_ENVELOPE_FORM, self.section("## Instrument"))

    def test_check_envelope_refusal_is_item7_wire(self):
        mutant = ITEM7_EXIT_30_MUTANT
        self.assertIn("3", mutant)
        self.assertFalse(_item7_refusal_holds(mutant))
        chunks = _chunks_naming(self.section("## Instrument"), "check-envelope")
        self.assertTrue(chunks, "instrument does not name check-envelope")
        surface = " ".join(chunks)
        self.assertTrue(
            _item7_refusal_holds(surface)
            or any(_item7_refusal_holds(c) for c in chunks),
            "item 7: check-envelope is one stderr line, exit 2, "
            "stdout empty, expected …; found …; needed …",
        )


class RecordEnvelopeField(_LeverPin):
    """Item 3 — the stage row records the envelope field that failed."""

    def test_the_record_carries_the_envelope_field_shape(self):
        mutant = (
            "ordered stage rows naming job, harness, attempt, input path "
            "and digest, custody proof, dispatch record"
        )
        self.assertNotIn(ENVELOPE_SHAPE, mutant)
        self.assertIn(ENVELOPE_SHAPE, self.section("## Record"))


if __name__ == "__main__":
    unittest.main()
