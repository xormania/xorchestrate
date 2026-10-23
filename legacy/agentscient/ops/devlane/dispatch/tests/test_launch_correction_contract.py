"""Document pins for the uncited-correction gate in the launch contract.

Written from the numbered Contract items of
`.dev/ladder-inputs/scope-gate-20260910.md`. Slice 1 only: these tests
read `ops/devlane/dispatch/CONTRACT.md` and never import launch.py or
record.py.

Each test is a pin of one declared row, field, or quoted grammar. A
broken contract that omits that row still collects; it fails the
assertion below.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[4]
CONTRACT = REPO / "ops" / "devlane" / "dispatch" / "CONTRACT.md"
# World.stage copies CONTRACT.md into the world; an absent file still
# plants the exit-0 stand-in used to prove red.
_SUBJECT = CONTRACT

# Item 4: the path is a scope-file path. The item-3 needed command
# writes `contract item N of <scope path>: <why>`, so a colon after the
# path is a delimiter, not part of the path. The greedy class
# `[^\s;,]+` is the mutant this pin refuses.
GREEDY_CITATION_GRAMMAR = (
    r"contract item (?P<item>[1-9][0-9]*) of (?P<path>[^\s;,]+)"
)
CITATION_GRAMMAR = (
    r"contract item (?P<item>[1-9][0-9]*) of (?P<path>[^\s;,:]+)"
)
PRESCRIBED_EVIDENCE = (
    "contract item 3 of .dev/ladder-inputs/scope-gate-20260910.md: "
    "found-text missing"
)

FOUND_TEXTS = (
    (
        "no-key",
        "no --answers key named for a correction following",
    ),
    (
        "absent",
        "absent from the findings store",
    ),
    (
        "absent-no-store",
        "(no store)",
    ),
    (
        "open",
        "key <key> still open",
    ),
    (
        "uncited",
        "without a contract citation",
    ),
    (
        "late",
        "after the mint attempt at",
    ),
)

# Item 3's five refusal states. absent-no-store is a variant of absent.
ITEM3_ARMS = ("no-key", "absent", "open", "uncited", "late")

SAME_DEFINITION = re.compile(
    r"(the )?gate and `findings\.py` read the same definition"
    r"|`findings\.py` and (the )?gate read the same definition",
    re.IGNORECASE,
)
PRIVATE_GRAMMAR = re.compile(
    r"private copy|divergent (citation )?parser|uses a private",
    re.IGNORECASE,
)
NEEDED_DISPOSITION = re.compile(
    r"python3 ops/conductor/tools/findings.py disposition "
    r"'(?:<key>|<record>#<n>)' <disposition> "
    r"'contract item N of <[^'>]+>",
)
UNQUOTED_NEEDED = (
    "python3 ops/conductor/tools/findings.py disposition "
    "<key> <disposition> 'contract item N of <scope path>: why'"
)
QUOTED_NEEDED = (
    "python3 ops/conductor/tools/findings.py disposition "
    "'<key>' <disposition> 'contract item N of <scope path>: why'"
)
OVERRIDE_MARK = re.compile(r"\(\*\*override\*\*\)")
OVERRIDE_PERMITTED = re.compile(
    r"\boverride (permitted|allowed|accepted)\b|\bhand-grant\b",
    re.IGNORECASE,
)
OVERRIDABLE = re.compile(r"\boverridable\b", re.IGNORECASE)
NOT_OVERRIDABLE = re.compile(r"\b(not |non-)overridable\b", re.IGNORECASE)


def _section(text: str, prefix: str) -> str:
    lines = text.splitlines()
    heading_level = None
    start = None
    for i, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix):]
        if rest == "" or rest[0] in " \t—-":
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


def _first_fence(block: str) -> str:
    parts = block.split("```")
    if len(parts) < 2:
        return ""
    body = parts[1]
    if body.startswith("text"):
        body = body.split("\n", 1)[-1]
    return body.strip()


def _row_id(cells: list[str]) -> str:
    token = cells[0].replace("`", "")
    return token.split()[0] if token.split() else ""


def _row_authorizes_override(cells: list[str]) -> bool:
    """True when any cell of a refusal row authorizes an override.

    Item 6: the gate has no overrides row and no hand-grant escape.
    A mark in the id cell or a permit in any later cell is enough.
    """
    blob = " ".join(cells)
    if OVERRIDE_MARK.search(blob) or OVERRIDE_PERMITTED.search(blob):
        return True
    return bool(OVERRIDABLE.search(blob) and not NOT_OVERRIDABLE.search(blob))


def _units_from_blob(blob: str) -> list[str]:
    units: list[str] = []
    for part in re.split(r"[\n;]|<br\s*/?>", blob):
        part = part.strip()
        if part:
            units.append(part)
    return units


def _units_from_row(cells: list[str]) -> list[str]:
    units: list[str] = []
    for cell in cells:
        units.extend(_units_from_blob(cell))
    return units


def _concatenating_units(units: list[str], fragments: list[str]) -> list[str]:
    return [unit for unit in units if all(frag in unit for frag in fragments)]


class _ContractPin(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="correction-contract-")
        self.addCleanup(self.world.close)
        self.contract = self.world.stage(_SUBJECT, "CONTRACT.md")
        self.assertTrue(self.contract.is_file(), f"missing {self.contract}")
        if _SUBJECT.is_file():
            self.assertEqual(
                self.contract.read_bytes(),
                _SUBJECT.read_bytes(),
                "staged CONTRACT.md did not land intact",
            )
        self.text = self.contract.read_text(encoding="utf-8")
        self.dispatch = _section(self.text, "## Dispatch")

    def section(self, prefix: str) -> str:
        block = _section(self.dispatch, prefix)
        self.assertTrue(block, f"Dispatch is missing {prefix!r}")
        return block

    def refusal_table(self) -> list[list[str]]:
        table = _first_table(self.section("### Refusals"))
        self.assertTrue(table, "Dispatch §Refusals has no table")
        return table

    def refusal_row(self, ident: str) -> list[str] | None:
        for cells in self.refusal_table()[1:]:
            if _row_id(cells) == ident:
                return cells
        return None

    def require_refusal_row(self, ident: str) -> list[str]:
        row = self.refusal_row(ident)
        if row is not None:
            return row
        have = [_row_id(cells) for cells in self.refusal_table()[1:]]
        self.fail(f"no refusal row id {ident!r}; have {have}")

    def row_or_section(self, ident: str) -> str:
        row = self.refusal_row(ident)
        if row is not None:
            return " ".join(row)
        return self.section("### Refusals")

    def found_text_units(self, ident: str) -> list[str]:
        row = self.refusal_row(ident)
        if row is not None:
            return _units_from_row(row)
        return _units_from_blob(self.section("### Refusals"))


class AnswersVerbIsDeclared(_ContractPin):
    """Item 2 — the declared field is --answers KEY, repeatable."""

    def test_verbs_usage_names_repeatable_answers_after_follows(self):
        usage = _first_fence(self.section("### Verbs"))
        self.assertTrue(usage, "Dispatch §Verbs has no usage fence")
        collapsed = " ".join(usage.split())
        self.assertIn(
            "[--follows ID]... [--answers KEY]...",
            collapsed,
            "§Verbs usage must declare repeatable [--answers KEY]... "
            "after [--follows ID]...",
        )


class CorrectionRefusalRow(_ContractPin):
    """Item 3 / item 6 — class correction, five distinct found-texts,
    exact disposition command, exit 2, founding wire, not overridable."""

    def test_refusals_table_has_a_correction_row_that_is_not_overridable(self):
        # Item 6: a permit in any column is an overrides row.
        self.assertTrue(
            _row_authorizes_override(["correction", "override permitted"]),
            "helper must treat a non-ID 'override permitted' as an escape",
        )
        self.assertTrue(
            _row_authorizes_override(["correction (**override**)", "refuses"]),
            "helper must treat the table's override mark as an escape",
        )
        self.assertFalse(
            _row_authorizes_override(["correction", "not overridable"]),
            "a declared non-escape is not an authorization",
        )
        row = self.require_refusal_row("correction")
        self.assertGreaterEqual(len(row), 2, "correction row is a table stub")
        self.assertFalse(
            _row_authorizes_override(row),
            "item 6: correction has no overrides row and no hand-grant "
            "escape; no cell may authorize one",
        )

    def test_correction_row_names_each_found_text(self):
        blob = self.row_or_section("correction")
        fragments = [frag for arm, frag in FOUND_TEXTS if arm in ITEM3_ARMS]
        for arm, fragment in FOUND_TEXTS:
            with self.subTest(arm=arm):
                self.assertIn(
                    fragment,
                    blob,
                    f"correction missing {arm} found-text {fragment!r}",
                )
        if not all(frag in blob for frag in fragments):
            return
        units = self.found_text_units("correction")
        stuffed = _concatenating_units(units, fragments)
        self.assertEqual(
            stuffed,
            [],
            "item 3 requires distinct found-texts; one unit concatenates "
            f"every arm: {stuffed!r}",
        )
        for arm, fragment in FOUND_TEXTS:
            if arm not in ITEM3_ARMS:
                continue
            others = [frag for a, frag in FOUND_TEXTS if a in ITEM3_ARMS and a != arm]
            homes = [unit for unit in units if fragment in unit]
            private = [
                unit for unit in homes if not all(frag in unit for frag in others)
            ]
            with self.subTest(distinct=arm):
                self.assertTrue(
                    private,
                    f"{arm} found-text only appears inside a concatenated "
                    "blob of every arm",
                )

    def test_correction_expected_half_is_the_constant_citation_before_stamp(self):
        blob = self.row_or_section("correction")
        self.assertIn(
            "every finding answered by this correction "
            "disposed with a contract citation before",
            blob,
        )

    def test_correction_needed_half_names_findings_py_disposition(self):
        self.assertFalse(
            NEEDED_DISPOSITION.search(
                "needed python3 ops/conductor/tools/findings.py disposition"
            ),
            "the bare prefix is not the exact command",
        )
        self.assertIsNone(
            NEEDED_DISPOSITION.search(UNQUOTED_NEEDED),
            "item 3: an unquoted key is not an executable remedy",
        )
        self.assertIsNotNone(
            NEEDED_DISPOSITION.search(QUOTED_NEEDED),
            "the quoted key form is the exact command",
        )
        blob = self.row_or_section("correction")
        self.assertRegex(
            blob,
            NEEDED_DISPOSITION,
            "item 3: the needed half names the exact findings.py "
            "disposition command with a quoted key, disposition, "
            "numbered item, and scope path",
        )

    def test_correction_row_declares_exit_2_on_the_founding_wire(self):
        blob = self.row_or_section("correction")
        for fragment in ("exit 2", "founding wire"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, blob)


class FollowsRefusalRow(_ContractPin):
    """Item 1 — an unreadable followed record is class follows."""

    def test_refusals_table_has_a_follows_row(self):
        row = self.require_refusal_row("follows")
        self.assertGreaterEqual(len(row), 2, "follows row is a table stub")

    def test_follows_row_names_the_unreadable_record_found_text(self):
        blob = self.row_or_section("follows")
        for fragment in ("readable record", ".dev/records/dispatches"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, blob)


class AnswersRecordField(_ContractPin):
    """Item 8 — answers after follows, shaped, absence declared."""

    def test_worker_fields_list_answers_after_follows_with_the_declared_shape(self):
        fence = _first_fence(self.section("### The record"))
        self.assertTrue(fence, "Dispatch §The record has no fields fence")
        collapsed = " ".join(fence.split())
        self.assertIn(
            "follows [ids], answers [{key, disposition, disposed_at, "
            "citation {item, path}}]",
            collapsed,
        )

    def test_the_record_declares_pre_field_absence_is_not_none_answered(self):
        record = self.section("### The record")
        for fragment in ("minted before the field landed", "none answered"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, record)


class CitationGrammarQuote(_ContractPin):
    """Item 4 — the grammar is quoted once; gate and findings.py
    read the same definition."""

    def test_citation_grammar_is_quoted_once(self):
        greedy = re.compile(GREEDY_CITATION_GRAMMAR)
        greedy_match = greedy.search(PRESCRIBED_EVIDENCE)
        self.assertIsNotNone(greedy_match)
        self.assertTrue(
            greedy_match.group("path").endswith(":"),
            "the greedy class is the mutant this pin refuses",
        )
        pin = re.compile(CITATION_GRAMMAR)
        pin_match = pin.search(PRESCRIBED_EVIDENCE)
        self.assertIsNotNone(pin_match)
        self.assertEqual(pin_match.group("item"), "3")
        self.assertEqual(
            pin_match.group("path"),
            ".dev/ladder-inputs/scope-gate-20260910.md",
            "item 4: the grammar must name the scope file by path; "
            "a trailing colon is not a path",
        )
        count = self.text.count(CITATION_GRAMMAR)
        self.assertEqual(
            count,
            1,
            f"citation grammar quoted {count} time(s), want 1: "
            f"{CITATION_GRAMMAR}",
        )
        self.assertNotIn(
            GREEDY_CITATION_GRAMMAR,
            self.text,
            "item 4: the colon-capturing grammar must not remain quoted",
        )

    def test_grammar_quote_names_findings_py_as_its_home(self):
        mutant = (
            "The grammar lives in findings.py, but the gate uses a private copy"
        )
        self.assertIsNone(
            SAME_DEFINITION.search(mutant),
            "a private copy is not the same definition",
        )
        self.assertIsNotNone(PRIVATE_GRAMMAR.search(mutant))
        self.assertRegex(
            self.dispatch,
            SAME_DEFINITION,
            "item 4: the gate and findings.py read the same definition",
        )
        self.assertIsNone(
            PRIVATE_GRAMMAR.search(self.dispatch),
            "item 4: the gate must not own a divergent citation parser",
        )


class KeyShapeIsDeclared(_ContractPin):
    """Item 2 — KEY is a findings-store key <record>#<n>."""

    def test_verbs_declare_the_answers_key_shape(self):
        verbs = self.section("### Verbs")
        self.assertIn("<record>#<n>", verbs)
