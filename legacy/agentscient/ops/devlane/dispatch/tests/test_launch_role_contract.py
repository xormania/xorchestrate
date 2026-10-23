"""Document pins for the read-role write-instruction refusal.

Written from contract items 5 and 7 of
`.dev/ladder-inputs/scope-item31-20260911.md`. Slice 1 only: these tests
read `ops/devlane/dispatch/CONTRACT.md` and never import launch.py.

Item 5: a read-role prompt with a positive instruction to write a path
is refused at render, class `role`, naming the job and the sentence.
Item 7: the refusal is one stderr line, exit 2, founding wire.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "ops" / "devlane" / "dispatch" / "CONTRACT.md"

EXPECTED = "a read-role prompt with no instruction to write a path"
FOUND_JOB = "found job check-tests:"
FOUND_SENTENCE = (
    "Write the report to {out}/SKEPTIC.md and name that path in artifacts"
)
NEEDED = (
    "drop the sentence from the prompt or declare the job a write role"
)
FOUNDING_WIRE = "founding wire"
SHAPE_RULE = (
    "instruction shape (a write verb and a path-shaped token, "
    "negated sentences excluded), not by a list of forbidden words"
)
ONE_STDERR_LINE = "one stderr line"
STDOUT_EMPTY = "stdout empty"
WIRE_GRAMMAR = "expected …; found …; needed …"
WIRE_GRAMMAR_ASCII = "expected ...; found ...; needed ..."

_EXIT_2 = re.compile(r"(?i)(?<![\w])exit\s+2(?!\d)")

# Item 5 near-miss: the example sentence, matching only that literal.
ITEM5_LITERAL_MUTANT = (
    "found job check-tests: Write the report to {out}/SKEPTIC.md "
    "and name that path in artifacts; match only that literal sentence"
)

# Item 5 near-miss: instruction shape plus a closed eight-verb list at
# sentence start. Survived the first frozen set (security
# 20260911T224145Z, P1): ordinary positive instructions bypass.
EIGHT_VERBS = "`write, save, create, store, emit, put, append, dump`"
PLEASE_WRITE = "Please write /tmp/result.md"
COPY_REPORT = "Copy the report to /tmp/result.md"
ITEM5_EIGHT_VERB_MUTANT = (
    "instruction shape (a write verb and a path-shaped token, "
    "negated sentences excluded), not by a list of forbidden words; "
    "write verbs from the declared set `write, save, create, store, "
    "emit, put, append, dump` as a verb (sentence start, or after "
    "`and`/`then`/`,`); Please write /tmp/result.md and Copy the "
    "report to /tmp/result.md bypass the planned guard"
)

# Item 7 near-miss: "exit 2" as a substring of "exit 20".
ITEM7_EXIT_20_MUTANT = (
    "exit 20; one stderr line is not required; write two stdout lines"
)

def _has_wire_grammar(text: str) -> bool:
    collapsed = " ".join(text.split())
    return WIRE_GRAMMAR in collapsed or WIRE_GRAMMAR_ASCII in collapsed


def _item5_shape_holds(text: str) -> bool:
    if SHAPE_RULE in text:
        return True
    return (
        "instruction shape" in text
        and "write verb" in text
        and "path-shaped token" in text
        and "negated sentences excluded" in text
        and "not by a list of forbidden words" in text
    )


def _has_positive_instruction_pin(sample: str, text: str) -> bool:
    """True when the sample is pinned as a positive write instruction.

    A mention that the sample bypasses the guard is not the pin.
    """
    pins = (
        f'"{sample}" is a positive instruction to write a path',
        f"`{sample}` is a positive instruction to write a path",
    )
    return any(pin in text for pin in pins)


def _item5_ordinary_instructions_hold(text: str) -> bool:
    if not _item5_shape_holds(text):
        return False
    if EIGHT_VERBS in text:
        return False
    if not _has_positive_instruction_pin(PLEASE_WRITE, text):
        return False
    return _has_positive_instruction_pin(COPY_REPORT, text)


def _item7_refusal_holds(text: str) -> bool:
    if _EXIT_2.search(text) is None:
        return False
    if ONE_STDERR_LINE not in text:
        return False
    if STDOUT_EMPTY not in text:
        return False
    return _has_wire_grammar(text)


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


def _first_table(block: str) -> list[list[str]]:
    rows: list[list[str]] = []
    in_table = False
    for line in block.splitlines():
        if line.startswith("|"):
            in_table = True
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(cell and set(cell) <= set("-: ") for cell in cells):
                continue
            rows.append(cells)
            continue
        if in_table:
            break
    return rows


def _row_id(cells: list[str]) -> str:
    token = cells[0].replace("`", "")
    return token.split()[0] if token.split() else ""


def _chunks_naming(text: str, ident: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [chunk for chunk in chunks if ident in chunk]


class _ContractPin(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="launch-role-contract-")
        self.addCleanup(self.world.close)
        self.assertTrue(CONTRACT.is_file(), f"missing {CONTRACT}")
        planted = self.world.plant_text(
            self.world.cwd / "CONTRACT.md",
            CONTRACT.read_text(encoding="utf-8"),
            recognisable="## Dispatch",
        )
        self.text = planted.read_text(encoding="utf-8")
        self.assertTrue(self.text.strip(), "CONTRACT.md is empty")
        dispatch = _section(self.text, "## Dispatch")
        self.assertTrue(dispatch, "CONTRACT.md has no ## Dispatch section")
        self.dispatch = dispatch

    def refusals(self) -> str:
        block = _section(self.dispatch, "### Refusals")
        self.assertTrue(block, "Dispatch is missing ### Refusals")
        return block

    def refusal_table(self) -> list[list[str]]:
        table = _first_table(self.refusals())
        self.assertTrue(table, "Dispatch §Refusals has no table")
        return table

    def refusal_row(self, ident: str) -> list[str] | None:
        for cells in self.refusal_table()[1:]:
            if _row_id(cells) == ident:
                return cells
        return None

    def role_blob(self) -> str:
        row = self.refusal_row("role")
        if row is None:
            return ""
        return " ".join(row)

    def role_surface(self) -> str:
        parts = [self.role_blob()]
        parts.extend(_chunks_naming(self.refusals(), "role"))
        return "\n".join(p for p in parts if p)


class RoleRefusalRow(_ContractPin):
    """Item 5 / item 7 — class role, exit 2, founding wire."""

    def test_refusals_table_has_a_role_row(self):
        have = [_row_id(cells) for cells in self.refusal_table()[1:]]
        self.assertIsNotNone(
            self.refusal_row("role"),
            f"no refusal row id 'role'; have {have}",
        )

    def test_role_row_declares_exit_2(self):
        mutant = ITEM7_EXIT_20_MUTANT
        self.assertIn("exit 2", mutant)
        self.assertIsNone(
            _EXIT_2.search(mutant),
            "item 7: 'exit 2' must not match as a substring of 'exit 20'",
        )
        self.assertFalse(
            _item7_refusal_holds(mutant),
            "item 7: exit 20 and two stdout lines must not hold",
        )
        blob = self.role_blob()
        self.assertRegex(
            blob,
            _EXIT_2,
            "item 7: the role row declares exit 2 as a token, not a "
            f"substring; found {blob!r}",
        )
        surface = self.role_surface()
        self.assertTrue(
            _item7_refusal_holds(blob) or _item7_refusal_holds(surface),
            "item 7: one stderr line, exit 2, stdout empty, "
            "expected …; found …; needed …",
        )

    def test_role_row_declares_the_founding_wire(self):
        mutant = "`role` (exit 2, before the gate)"
        self.assertNotIn(FOUNDING_WIRE, mutant)
        self.assertIn(FOUNDING_WIRE, self.role_blob())

    def test_role_expected_half_is_a_read_role_prompt_with_no_write_instruction(
        self,
    ):
        mutant = "expected a write-role prompt that names an output file"
        self.assertNotIn(EXPECTED, mutant)
        self.assertIn(EXPECTED, self.role_blob())

    def test_role_found_half_names_the_job(self):
        mutant = "found a prompt that writes a file"
        self.assertNotIn(FOUND_JOB, mutant)
        self.assertIn(FOUND_JOB, self.role_blob())

    def test_role_found_half_names_the_sentence(self):
        mutant = ITEM5_LITERAL_MUTANT
        self.assertIn(FOUND_JOB, mutant)
        self.assertIn(FOUND_SENTENCE, mutant)
        self.assertFalse(
            _item5_shape_holds(mutant),
            "item 5: matching only the check-tests sentence is not "
            "instruction shape",
        )
        blob = self.role_blob()
        self.assertIn(FOUND_SENTENCE, blob)
        surface = self.role_surface()
        self.assertTrue(
            _item5_shape_holds(blob) or _item5_shape_holds(surface),
            "item 5: recognition is by instruction shape (a write verb "
            "and a path-shaped token, negated sentences excluded), not "
            "by a list of forbidden words",
        )

    def test_role_needed_half_drops_the_sentence_or_declares_a_write_role(self):
        mutant = "needed remove {out} from the template values"
        self.assertNotIn(NEEDED, mutant)
        self.assertIn(NEEDED, self.role_blob())

    def test_please_write_with_an_introducer_is_a_positive_write_instruction(
        self,
    ):
        mutant = ITEM5_EIGHT_VERB_MUTANT
        self.assertTrue(
            _item5_shape_holds(mutant),
            "plant must land: the shape rule plus a closed verb list",
        )
        self.assertIn(EIGHT_VERBS, mutant)
        self.assertIn(PLEASE_WRITE, mutant)
        self.assertFalse(
            _has_positive_instruction_pin(PLEASE_WRITE, mutant),
            "item 5: naming the sample as a bypass is not the pin",
        )
        self.assertFalse(
            _item5_ordinary_instructions_hold(mutant),
            "item 5: eight verbs at sentence start must not hold",
        )
        surface = self.role_surface()
        self.assertTrue(
            _has_positive_instruction_pin(PLEASE_WRITE, surface),
            "item 5: Please write /tmp/result.md is a positive "
            "instruction to write a path; an introducer before the "
            "verb does not drop it",
        )
        self.assertNotIn(
            EIGHT_VERBS,
            surface,
            "item 5: recognition is not a closed list of write verbs",
        )

    def test_copy_the_report_to_a_path_is_a_positive_write_instruction(self):
        mutant = ITEM5_EIGHT_VERB_MUTANT
        self.assertTrue(_item5_shape_holds(mutant))
        self.assertIn(EIGHT_VERBS, mutant)
        self.assertIn(COPY_REPORT, mutant)
        self.assertFalse(
            _has_positive_instruction_pin(COPY_REPORT, mutant),
            "item 5: naming the sample as a bypass is not the pin",
        )
        self.assertFalse(_item5_ordinary_instructions_hold(mutant))
        surface = self.role_surface()
        self.assertTrue(
            _has_positive_instruction_pin(COPY_REPORT, surface),
            "item 5: Copy the report to /tmp/result.md is a positive "
            "instruction to write a path, not a miss for want of the "
            "verb write",
        )
        self.assertNotIn(
            EIGHT_VERBS,
            surface,
            "item 5: recognition is not a closed list of write verbs",
        )


if __name__ == "__main__":
    unittest.main()
