"""Document pins for the uncited-correction stop on the ladder lever.

Written from scope-gate-20260910.md item 9 and item 4. Slice 1 only:
these tests read `ops/conductor/ladder-lever.md` and
`ops/conductor/findings/README.md` and never import ladder.py or
findings.py.

A broken contract that omits the undisposed stop, the fix-stage
--answers arguments, or the cited verb still collects; it fails the
assertion below.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
LEVER = REPO / "ops" / "conductor" / "ladder-lever.md"
README = REPO / "ops" / "conductor" / "findings" / "README.md"

EVERY_FINDING = re.compile(
    r"every finding|each finding|each key|per key|"
    r"one `--answers KEY` per key|one --answers KEY per key|"
    r"\[--answers KEY\]\.\.\.",
    re.IGNORECASE,
)
FIRST_FINDING_ONLY = re.compile(
    r"only the first finding|first finding only",
    re.IGNORECASE,
)
MULTI_FOLLOWS = re.compile(
    r"--follows.{0,80}--follows|"
    r"\[--follows ID\]\.\.\.|"
    r"--follows <previous>|"
    r"each review",
    re.IGNORECASE | re.DOTALL,
)
STOPPED_KEYS = re.compile(
    r"`keys` in the (?:ladder )?record's `stopped` object|"
    r"the record's `stopped` object carries (?:them|the keys|`keys`)|"
    r"stopped\.keys",
    re.IGNORECASE,
)
RESUME_CONTINUES = re.compile(
    r"`?resume`? continues once rows exist",
    re.IGNORECASE,
)
RESUME_RECHECK = re.compile(
    r"re-runs the check at the same place",
    re.IGNORECASE,
)
RESUME_NEGATION = re.compile(
    r"resume does not (repeat|recheck|re-check|re-run)|"
    r"resume (never|skips) (the )?(undisposed )?check",
    re.IGNORECASE,
)
NEVER_WRITES = re.compile(
    r"driver never writes dispositions itself",
    re.IGNORECASE,
)
CITED_UNSUPPORTED = re.compile(
    r"`cited` is unsupported|must not be implemented",
    re.IGNORECASE,
)
CITED_VERB = re.compile(
    r"verb `cited`|`cited` KEY|`cited` \(read|names `cited`",
    re.IGNORECASE,
)
SAME_DEFINITION = re.compile(
    r"(the )?gate and `findings\.py` read the same definition"
    r"|`findings\.py` and (the )?gate read the same definition",
    re.IGNORECASE,
)
PRIVATE_GRAMMAR = re.compile(
    r"private copy|divergent (citation )?parser|uses a private",
    re.IGNORECASE,
)
UNDISPOSED_KEYS_LIST = re.compile(r"undisposed keys:\s*<k1>\s*<k2>")


def _section(text: str, prefix: str) -> str:
    lines = text.splitlines()
    heading_level = None
    start = None
    for i, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix):]
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


def _chunks_naming(text: str, ident: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [chunk for chunk in chunks if ident in chunk]


def _declares_every_answered_finding(blob: str) -> bool:
    """Item 9: every audit finding is checked and named, not the first."""
    if "--follows" not in blob or "--answers" not in blob:
        return False
    if FIRST_FINDING_ONLY.search(blob):
        return False
    if not EVERY_FINDING.search(blob):
        return False
    return bool(blob.count("--follows") >= 2 or MULTI_FOLLOWS.search(blob))


class _LeverPin(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.assertTrue(LEVER.is_file(), f"missing {LEVER}")
        self.world = World(prefix="ladder-contract-")
        self.addCleanup(self.world.close)
        self.lever_path = self.world.stage(LEVER)
        self.readme_path = self.world.stage(README, "findings-README.md")
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

    def stage_names_every_finding(self, ident: str) -> None:
        row = " ".join(self.stage_row(ident))
        chunks = [row, *_chunks_naming(self.section("## Stage table"), ident)]
        self.assertTrue(
            any(_declares_every_answered_finding(chunk) for chunk in chunks),
            f"item 9: {ident} must name every finding it answers with "
            "--follows for the review records and one --answers KEY per key, "
            "not the first finding only",
        )


class FixStagesCarryAnswers(_LeverPin):
    """Item 9 — fix-tests and fix-code are corrections: --follows
    and --answers on the dispatched argv for every finding."""

    def test_fix_tests_row_names_follows_and_answers(self):
        mutant = (
            "`fix-tests` sends one --follows and --answers for only the "
            "first finding"
        )
        self.assertFalse(
            _declares_every_answered_finding(mutant),
            "one --follows and the first finding is not every finding",
        )
        self.stage_names_every_finding("fix-tests")

    def test_fix_code_row_names_follows_and_answers(self):
        mutant = (
            "`fix-code` names only one of several findings and omits "
            "review provenance; --answers"
        )
        self.assertFalse(
            _declares_every_answered_finding(mutant),
            "one named finding is not every finding",
        )
        self.stage_names_every_finding("fix-code")


class UndisposedStop(_LeverPin):
    """Item 9 — stop before either fix stage; resume re-checks;
    the driver never writes dispositions."""

    def test_stops_declare_reason_undisposed(self):
        self.assertIn("undisposed", self.section("## Stops"))

    def test_undisposed_stop_line_names_each_fix_stage(self):
        stops = self.section("## Stops")
        for stage in ("fix-tests", "fix-code"):
            with self.subTest(stage=stage):
                self.assertIn(
                    f"stopped stage={stage} record=none reason=undisposed",
                    stops,
                )

    def test_undisposed_stop_lists_the_keys(self):
        stops = self.section("## Stops")
        self.assertFalse(
            bool(UNDISPOSED_KEYS_LIST.search("undisposed keys: <k1>")),
            "a single key is not every undisposed key",
        )
        self.assertRegex(
            stops,
            UNDISPOSED_KEYS_LIST,
            "item 9: the stop names every undisposed key, not the first",
        )

    def test_the_record_stopped_object_carries_keys(self):
        record = self.section("## Record")
        mutant = (
            "the stopped stage writes a glossary of keys nearby without "
            "finding IDs"
        )
        self.assertIsNone(
            STOPPED_KEYS.search(mutant),
            "a nearby glossary is not stopped.keys",
        )
        self.assertRegex(
            record,
            STOPPED_KEYS,
            "item 9: the record's stopped object carries the undisposed keys",
        )

    def test_resume_repeats_the_undisposed_check(self):
        mutant = "resume does not repeat the undisposed check"
        self.assertIsNotNone(RESUME_NEGATION.search(mutant))
        self.assertIsNone(RESUME_RECHECK.search(mutant))
        self.assertIsNone(RESUME_CONTINUES.search(mutant))
        self.assertRegex(
            self.lever,
            RESUME_CONTINUES,
            "item 9: resume continues once rows exist",
        )
        self.assertRegex(
            self.lever,
            RESUME_RECHECK,
            "item 9: resume re-runs the undisposed check, not a stale stop",
        )
        self.assertIsNone(
            RESUME_NEGATION.search(self.lever),
            "item 9: resume must not skip the undisposed check",
        )

    def test_the_driver_never_calls_findings_py_disposition(self):
        mutant = (
            "the driver never calls `findings.py disposition`; it writes "
            "the disposition row directly"
        )
        self.assertIsNone(
            NEVER_WRITES.search(mutant),
            "banning one command is not never writing dispositions",
        )
        self.assertRegex(
            self.lever,
            NEVER_WRITES,
            "item 9: the driver never writes dispositions itself",
        )


class CitedVerbOnTheLedger(_LeverPin):
    """Item 4 / item 9 — the ledger README names cited as a working
    read verb and the grammar home both sides share."""

    def test_readme_names_the_cited_verb(self):
        self.assertTrue(README.is_file(), f"missing {README}")
        text = self.readme_path.read_text(encoding="utf-8")
        self.assertTrue(text.strip(), "findings README is empty")
        mutant = "`cited` is unsupported and must not be implemented"
        self.assertTrue(bool(CITED_UNSUPPORTED.search(mutant)))
        self.assertFalse(bool(CITED_VERB.search(mutant)))
        self.assertIn("`cited`", text)
        self.assertIsNone(
            CITED_UNSUPPORTED.search(text),
            "item 9: cited is the ledger read the driver runs, not withheld",
        )
        self.assertRegex(
            text,
            CITED_VERB,
            "item 9: the README must name cited as a working verb",
        )

    def test_readme_names_findings_py_as_the_citation_grammar_home(self):
        self.assertTrue(README.is_file(), f"missing {README}")
        text = self.readme_path.read_text(encoding="utf-8")
        mutant = (
            "The grammar lives in findings.py, but the gate uses a private copy"
        )
        self.assertIsNone(SAME_DEFINITION.search(mutant))
        self.assertIsNotNone(PRIVATE_GRAMMAR.search(mutant))
        self.assertRegex(
            text,
            SAME_DEFINITION,
            "item 4: the gate and findings.py read the same definition",
        )
        self.assertIsNone(
            PRIVATE_GRAMMAR.search(text),
            "item 4: the ledger must not declare a private gate parser",
        )
