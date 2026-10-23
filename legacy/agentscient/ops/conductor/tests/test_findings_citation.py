"""Citation grammar, citation_holds, and the cited verb.

Written from scope-gate-20260910.md items 1, 4 and 9 (the ledger half)
and rulings R1, R3, R4. findings.py is the subject and is not read
here: tests load parse_citation / citation_holds as callables when
present, and invoke the staged tool as a subprocess.

A missing callable is treated as "this evidence does not parse / does
not hold", so a positive assertion is red today for the right reason
rather than an import error. Each negative case also requires the
positive neighbour so a missing callable cannot make the rejection
look green.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "findings.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")
LIVE_STORE = Path.home() / ".local" / "state" / "agentscient" / "findings.db"
AGENT = "Citation Tester <citation-tester@example.invalid>"
REC = "20260901T000000Z-review-grok-aaaaaa"
ADJ_REC = "20260901T010000Z-adjudicate-grok-bbbbbb"
SWEEP_REC = "20260901T020000Z-sweep-codex-cccccc"
PLAN_REC = "20260901T030000Z-plan-claude-dddddd"
SCOPE_REL = ".dev/ladder-inputs/scope-gate-20260910.md"
OTHER_REL = ".dev/ladder-inputs/other-scope.md"
DISCIPLINE_REL = ".dev/ladder-inputs/scope-discipline.md"
CAP_REL = ".dev/ladder-inputs/scope-gate-20260910.scope.md"
VALID_EVIDENCE = (
    "contract item 3 of .dev/ladder-inputs/scope-gate-20260910.md: "
    "found-text missing"
)
DISCIPLINE_EVIDENCE = (
    "contract item 1 of .dev/ladder-inputs/scope-discipline.md: "
    "discipline clause is not the work-order Contract"
)
SCOPE_BODY = "# Work order\n\n" + "".join(
    f"{n}. Item {n} of the work order.\n" for n in range(1, 11)
)
OTHER_BODY = "# Other\n\n1. **A numbered item on another file.**\n"
DISCIPLINE_BODY = (
    "# Scope discipline\n\n"
    "1. The requirements are the numbered items in the work order's "
    "Contract section. Nothing else is a requirement.\n"
    "2. A reviewer finding is evidence about the contract, not a "
    "requirement.\n"
    "3. One fix round per ladder.\n"
    "4. The frozen test set is the acceptance criterion.\n"
    "5. Reviewers: for every finding, quote the contract item violated.\n"
    "6. A correction dispatch names the finding key and the contract item.\n"
)
CAP_BODY = (
    "Work order: the uncited-correction gate. The requirements are the ten "
    "numbered Contract items of .dev/ladder-inputs/scope-gate-20260910.md, "
    "tracked in this snapshot at the ref.\n"
)
RUN_CAP = 30


def evidence_for(item: int, path: str = SCOPE_REL, why: str = "why") -> str:
    return f"contract item {item} of {path}: {why}"


def _load_findings(path: Path):
    spec = importlib.util.spec_from_file_location("findings_citation", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _item_path(got):
    if got is None:
        return None
    if hasattr(got, "group"):
        return got.group("item"), got.group("path")
    if isinstance(got, dict) and "item" in got and "path" in got:
        return str(got["item"]), str(got["path"])
    return None


class _CitationFixture(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.assertTrue(TOOL.is_file(), f"{TOOL} is missing")
        self.world = World(prefix="findings-citation-")
        self._live = self.world.watch(LIVE_STORE)
        self.home = self.world.home
        self.scratch = self.world.tmp
        self.repo = self.world.cwd
        self.records = self.repo / ".dev" / "records" / "dispatches"
        self.records.mkdir(parents=True)
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under XDG_STATE_HOME is the one
        # asserted.
        self.conf = self.world.plant_text(
            self.world.root / "lane.conf",
            "LANE_NAME='agentscient'\n",
        )
        self.store = self.world.xdg_state / "agentscient" / "findings.db"
        self.scope = self.repo / SCOPE_REL
        self.other = self.repo / OTHER_REL
        self._stage_tool_bytes()
        self.world.plant_text(self.scope, SCOPE_BODY)
        self.world.plant_text(self.other, OTHER_BODY)
        self.world.enter()
        self.mod = _load_findings(self.staged)
        self.assertIsNotNone(self.mod, "staged findings.py did not load")
        self.assertFalse(
            self.store.exists(),
            "plant: isolated store must start absent",
        )

    def tearDown(self):
        try:
            self._live.assert_unchanged(self, f"live store mutated: {LIVE_STORE}")
        finally:
            self.world.close()

    def _stage_tool_bytes(self) -> None:
        dest = self.repo / "ops" / "conductor" / "tools" / "findings.py"
        data = TOOL.read_bytes()
        self.staged = self.world.plant_bytes(dest, data)
        for name in SIBLINGS:
            sibling = TOOL.with_name(name)
            self.world.plant_bytes(self.staged.with_name(name), sibling.read_bytes())

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        merged = {"WF_AGENT": AGENT, "LANE_CONF": str(self.conf)}
        if extra:
            merged.update(extra)
        return self.world.env(extra=merged)

    def invoke(self, argv: list[str], *, extra_env: dict[str, str] | None = None):
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=self.repo,
            env=self.env(extra_env),
            timeout=RUN_CAP,
        )

    def parse(self, evidence: str):
        fn = getattr(self.mod, "parse_citation", None)
        if not callable(fn):
            return None
        return fn(evidence)

    def holds(self, evidence: str, root: Path, scope_path: str):
        fn = getattr(self.mod, "citation_holds", None)
        if not callable(fn):
            return False, "citation_holds is not defined"
        try:
            return fn(evidence, root, scope_path)
        except TypeError:
            return False, "citation_holds does not take scope_path"

    def plant_record(
        self,
        rec_id: str,
        job: str,
        claim: str,
        brief_inputs: list[str] | None = None,
    ) -> Path:
        envelope = {
            "id": rec_id,
            "lane": "dev",
            "stage": "review",
            "unit": "lane/citation",
            "job": job,
            "role": "read",
            "status": "closed",
            "harness": {"name": "grok"},
            "model": {"ran": "grok-4.6"},
            "result": {
                "envelope": {
                    "status": "ok",
                    "verdict": "changes",
                    "findings": [
                        {
                            "severity": "p1",
                            "where": "ops/x.py",
                            "claim": claim,
                            "reproduce": None,
                        }
                    ],
                }
            },
        }
        if brief_inputs is not None:
            envelope["brief"] = {
                "inputs": [
                    {"path": path, "sha256": "0" * 64} for path in brief_inputs
                ]
            }
        path = self.records / f"{rec_id}.json"
        payload = json.dumps(envelope, indent=2) + "\n"
        self.world.plant_text(path, payload, recognisable=claim)
        return path

    def plant_three_scope_files(self) -> None:
        discipline = self.repo / DISCIPLINE_REL
        cap = self.repo / CAP_REL
        self.world.plant_text(discipline, DISCIPLINE_BODY)
        self.world.plant_text(cap, CAP_BODY)
        names = sorted(
            p.name
            for p in (self.repo / ".dev" / "ladder-inputs").glob("scope-*.md")
        )
        self.assertEqual(
            names,
            [
                "scope-discipline.md",
                "scope-gate-20260910.md",
                "scope-gate-20260910.scope.md",
            ],
            "plant: the three scope-*.md names from the work-order tree",
        )
        self.assertNotRegex(
            cap.read_text(encoding="utf-8"),
            r"(?m)^\s*[1-9][0-9]*\.\s",
            "plant: the cap file has no numbered Contract item",
        )

    def import_records(self):
        proc = self.invoke(["import"])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return proc

    def store_keys(self) -> list[str]:
        if not self.store.is_file():
            return []
        con = sqlite3.connect(self.store)
        try:
            rows = list(con.execute("select key from findings order by key"))
        finally:
            con.close()
        return [row[0] for row in rows]

    def dispose(self, key: str, disposition: str, evidence: str):
        proc = self.invoke(
            ["disposition", key, disposition, evidence],
            extra_env={"WF_AGENT": AGENT},
        )
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return proc


class ParseCitation(unittest.TestCase):
    """Item 4 / R1 — grammar only: item and path, colon is a delimiter."""

    maxDiff = None

    def setUp(self):
        self.assertTrue(TOOL.is_file(), f"{TOOL} is missing")
        self.world = World(prefix="findings-citation-parse-")
        self.addCleanup(self.world.close)
        dest = self.world.cwd / "ops" / "conductor" / "tools" / "findings.py"
        self.staged = self.world.plant_bytes(dest, TOOL.read_bytes())
        for name in SIBLINGS:
            sibling = TOOL.with_name(name)
            self.world.plant_bytes(self.staged.with_name(name), sibling.read_bytes())
        self.world.enter()
        self.mod = _load_findings(self.staged)
        self.assertIsNotNone(self.mod, "findings.py did not load")

    def parse(self, evidence: str):
        fn = getattr(self.mod, "parse_citation", None)
        if not callable(fn):
            return None
        return fn(evidence)

    def test_prescribed_evidence_captures_item_and_path_without_the_colon(self):
        got = self.parse(VALID_EVIDENCE)
        captured = _item_path(got)
        self.assertIsNotNone(
            captured,
            "item 4: prescribed evidence must parse to item and path",
        )
        item, path = captured
        self.assertEqual(item, "3")
        self.assertEqual(
            path,
            ".dev/ladder-inputs/scope-gate-20260910.md",
            "R1: the path stops before the colon in "
            "`contract item N of PATH: why`",
        )
        self.assertFalse(path.endswith(":"))

    def test_CITATION_is_the_grammar_and_stops_before_colon_comma_semicolon(self):
        pat = getattr(self.mod, "CITATION", None)
        self.assertIsNotNone(pat, "item 4: the grammar is data named CITATION")
        search = getattr(pat, "search", None)
        self.assertTrue(callable(search), "CITATION must be a regex")
        matched = pat.search(VALID_EVIDENCE)
        self.assertIsNotNone(matched, "CITATION must match prescribed evidence")
        self.assertEqual(matched.group("item"), "3")
        self.assertEqual(matched.group("path"), SCOPE_REL)
        comma = pat.search(f"contract item 3 of {SCOPE_REL},trail: why")
        self.assertIsNotNone(comma)
        self.assertEqual(comma.group("path"), SCOPE_REL)
        self.assertNotIn(",", comma.group("path"))
        semi = pat.search(f"contract item 3 of {SCOPE_REL};trail: why")
        self.assertIsNotNone(semi)
        self.assertEqual(semi.group("path"), SCOPE_REL)
        self.assertNotIn(";", semi.group("path"))

    def test_every_numbered_item_from_1_through_10_parses(self):
        valid = _item_path(self.parse(VALID_EVIDENCE))
        self.assertIsNotNone(valid, "grammar must parse the prescribed evidence")
        for n in range(1, 11):
            with self.subTest(item=n):
                captured = _item_path(self.parse(evidence_for(n)))
                self.assertIsNotNone(
                    captured,
                    f"item 4: contract item {n} must parse",
                )
                item, path = captured
                self.assertEqual(item, str(n))
                self.assertEqual(path, SCOPE_REL)

    def test_missing_of_is_rejected(self):
        valid = _item_path(self.parse(VALID_EVIDENCE))
        got = self.parse(
            "contract item 3 .dev/ladder-inputs/scope-gate-20260910.md"
        )
        self.assertIsNotNone(valid, "grammar must parse the prescribed evidence")
        self.assertIsNone(got, "a citation without 'of' is not a citation")

    def test_zero_item_is_rejected(self):
        valid = _item_path(self.parse(VALID_EVIDENCE))
        got = self.parse(
            "contract item 0 of .dev/ladder-inputs/scope-gate-20260910.md"
        )
        self.assertIsNotNone(valid, "grammar must parse the prescribed evidence")
        self.assertIsNone(got, "item 0 is not a numbered contract item")

    def test_bare_text_is_rejected(self):
        valid = _item_path(self.parse(VALID_EVIDENCE))
        got = self.parse("fixed in commit abc without a contract item")
        self.assertIsNotNone(valid, "grammar must parse the prescribed evidence")
        self.assertIsNone(got, "bare disposition text is not a citation")

    def test_leading_zero_multi_digit_item_is_rejected(self):
        valid = _item_path(self.parse(VALID_EVIDENCE))
        got = self.parse(
            "contract item 03 of .dev/ladder-inputs/scope-gate-20260910.md"
        )
        self.assertIsNotNone(valid, "grammar must parse the prescribed evidence")
        self.assertIsNone(
            got,
            "a leading zero is not a numbered item [1-9][0-9]*",
        )

    def test_comma_at_the_path_boundary_does_not_enter_the_path(self):
        valid = _item_path(self.parse(VALID_EVIDENCE))
        got = _item_path(
            self.parse(f"contract item 3 of {SCOPE_REL},trail: why")
        )
        self.assertIsNotNone(valid, "grammar must parse the prescribed evidence")
        self.assertIsNotNone(
            got,
            "R1: comma is a path delimiter, so the path before it still parses",
        )
        item, path = got
        self.assertEqual(item, "3")
        self.assertEqual(path, SCOPE_REL)
        self.assertNotIn(",", path)
        self.assertNotIn("trail", path)

    def test_semicolon_at_the_path_boundary_does_not_enter_the_path(self):
        valid = _item_path(self.parse(VALID_EVIDENCE))
        got = _item_path(
            self.parse(f"contract item 3 of {SCOPE_REL};trail: why")
        )
        self.assertIsNotNone(valid, "grammar must parse the prescribed evidence")
        self.assertIsNotNone(
            got,
            "R1: semicolon is a path delimiter, so the path before it "
            "still parses",
        )
        item, path = got
        self.assertEqual(item, "3")
        self.assertEqual(path, SCOPE_REL)
        self.assertNotIn(";", path)
        self.assertNotIn("trail", path)


class CitationHolds(_CitationFixture):
    """Item 4 / R3 — grammar plus the named scope file under root."""

    def test_item_present_on_the_named_scope_file_holds(self):
        ok, reason = self.holds(VALID_EVIDENCE, self.repo, SCOPE_REL)
        self.assertTrue(
            ok,
            f"item 4: numbered item on the named scope file holds; "
            f"reason={reason!r}",
        )

    def test_every_numbered_item_on_the_named_scope_file_holds(self):
        ok3, _ = self.holds(VALID_EVIDENCE, self.repo, SCOPE_REL)
        self.assertTrue(ok3, "positive neighbour (item 3) must hold")
        for n in range(1, 11):
            with self.subTest(item=n):
                ok, reason = self.holds(evidence_for(n), self.repo, SCOPE_REL)
                self.assertTrue(
                    ok,
                    f"item 4: item {n} is a numbered line of the scope; "
                    f"reason={reason!r}",
                )

    def test_item_absent_from_the_named_scope_file_does_not_hold(self):
        ok, _ = self.holds(VALID_EVIDENCE, self.repo, SCOPE_REL)
        missing = (
            "contract item 99 of .dev/ladder-inputs/scope-gate-20260910.md: "
            "no such item"
        )
        bad, reason = self.holds(missing, self.repo, SCOPE_REL)
        self.assertTrue(ok, "positive neighbour must hold")
        self.assertFalse(bad, "item 99 is not a numbered line of the scope")
        self.assertTrue(reason, "a failed hold names a reason")

    def test_path_absent_from_root_does_not_hold(self):
        ok, _ = self.holds(VALID_EVIDENCE, self.repo, SCOPE_REL)
        missing = "contract item 3 of .dev/ladder-inputs/no-such-scope.md: why"
        bad, reason = self.holds(
            missing, self.repo, ".dev/ladder-inputs/no-such-scope.md"
        )
        self.assertTrue(ok, "positive neighbour must hold")
        self.assertFalse(bad, "a missing scope file does not hold")
        self.assertTrue(reason, "a failed hold names a reason")

    def test_path_outside_root_does_not_hold(self):
        ok, _ = self.holds(VALID_EVIDENCE, self.repo, SCOPE_REL)
        outside = self.scratch / "outside.md"
        self.world.plant_text(outside, SCOPE_BODY)
        evidence = f"contract item 3 of {outside}: why"
        bad, reason = self.holds(evidence, self.repo, str(outside))
        self.assertTrue(ok, "positive neighbour must hold")
        self.assertFalse(bad, "a path outside root does not hold")
        self.assertTrue(reason, "a failed hold names a reason")

    def test_citation_to_a_different_numbered_file_does_not_hold(self):
        ok, _ = self.holds(VALID_EVIDENCE, self.repo, SCOPE_REL)
        other = "contract item 1 of .dev/ladder-inputs/other-scope.md: why"
        # The other file has item 1; R3 still refuses it as the scope.
        bad, reason = self.holds(other, self.repo, SCOPE_REL)
        self.assertTrue(ok, "positive neighbour must hold")
        self.assertFalse(
            bad,
            "R3: a citation to any file other than the correction's "
            "scope file does not hold",
        )
        self.assertTrue(reason, "a failed hold names a reason")

    def test_item_without_whitespace_after_the_dot_does_not_hold(self):
        ok, _ = self.holds(VALID_EVIDENCE, self.repo, SCOPE_REL)
        mutant_rel = ".dev/ladder-inputs/mutant-scope.md"
        mutant = self.repo / mutant_rel
        self.world.plant_bytes(mutant, b"# X\n3.no-space\n")
        evidence = f"contract item 3 of {mutant_rel}: why"
        bad, reason = self.holds(evidence, self.repo, mutant_rel)
        self.assertTrue(ok, "positive neighbour must hold")
        self.assertFalse(
            bad,
            "item 4: the numbered line is '^\\s*<item>\\.\\s', not '3.no-space'",
        )
        self.assertTrue(reason, "a failed hold names a reason")


class CitedVerb(_CitationFixture):
    """Item 9 ledger half / D11 — cited KEY, exits 0 / 1 / 2."""

    def test_cited_of_a_holding_disposition_prints_the_citation(self):
        self.plant_record(REC, "adversarial-review", "holding-citation")
        self.import_records()
        key = f"{REC}#1"
        self.assertIn(key, self.store_keys())
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.invoke(["cited", key])
        self.assertEqual(
            proc.exit,
            0,
            proc.stderr or proc.stdout,
        )
        line = (proc.stdout or "").strip()
        self.assertEqual(
            line,
            f"{key}\tcontract item 3 of {SCOPE_REL}",
        )
        self.assertEqual((proc.stderr or "").strip(), "")

    def test_cited_prints_item_1_and_item_10_not_only_item_3(self):
        self.plant_record(REC, "adversarial-review", "item-one")
        extra = "20260901T000000Z-review-grok-bbbbbb"
        self.plant_record(extra, "adversarial-review", "item-ten")
        self.import_records()
        first = f"{REC}#1"
        second = f"{extra}#1"
        self.dispose(first, "fixed", evidence_for(1, why="item 1 holds"))
        self.dispose(second, "fixed", evidence_for(10, why="item 10 holds"))
        one = self.invoke(["cited", first])
        ten = self.invoke(["cited", second])
        self.assertEqual(one.exit, 0, one.stderr or one.stdout)
        self.assertEqual(ten.exit, 0, ten.stderr or ten.stdout)
        self.assertEqual(
            (one.stdout or "").strip(),
            f"{first}\tcontract item 1 of {SCOPE_REL}",
        )
        self.assertEqual(
            (ten.stdout or "").strip(),
            f"{second}\tcontract item 10 of {SCOPE_REL}",
        )

    def test_cited_of_an_open_key_exits_1_with_open(self):
        self.plant_record(REC, "adversarial-review", "still-open")
        self.import_records()
        key = f"{REC}#1"
        self.assertIn(key, self.store_keys())
        proc = self.invoke(["cited", key])
        self.assertEqual(proc.exit, 1, proc.stderr or proc.stdout)
        self.assertEqual((proc.stdout or "").strip(), f"{key}\topen")

    def test_cited_of_an_uncited_disposition_exits_1_with_the_reason(self):
        self.plant_record(REC, "adversarial-review", "uncited-evidence")
        self.import_records()
        key = f"{REC}#1"
        self.dispose(key, "fixed", "fixed in tests, no item")
        proc = self.invoke(["cited", key])
        self.assertEqual(proc.exit, 1, proc.stderr or proc.stdout)
        line = (proc.stdout or "").strip()
        self.assertTrue(
            line.startswith(f"{key}\tuncited:"),
            f"D11: uncited disposition is KEY\\tuncited: <reason>; got {line!r}",
        )

    def test_cited_of_item_absent_from_the_named_scope_file_is_uncited(self):
        self.plant_record(REC, "adversarial-review", "item-absent")
        self.import_records()
        key = f"{REC}#1"
        evidence = evidence_for(99, why="no such item")
        self.dispose(key, "fixed", evidence)
        ok, reason = self.holds(evidence, self.repo, SCOPE_REL)
        self.assertFalse(ok, "item 99 must not hold on the named scope")
        self.assertTrue(reason)
        proc = self.invoke(["cited", key])
        self.assertEqual(proc.exit, 1, proc.stderr or proc.stdout)
        line = (proc.stdout or "").strip()
        self.assertTrue(
            line.startswith(f"{key}\tuncited:"),
            f"item 4/9: a parseable missing item is uncited, not cited; "
            f"got {line!r}",
        )
        self.assertIn(reason, line)

    def test_cited_of_a_citation_to_a_different_numbered_file_is_uncited(self):
        self.plant_record(REC, "adversarial-review", "different_numbered")
        self.import_records()
        key = f"{REC}#1"
        evidence = evidence_for(1, path=OTHER_REL, why="other file")
        self.dispose(key, "fixed", evidence)
        ok, reason = self.holds(evidence, self.repo, SCOPE_REL)
        self.assertFalse(
            ok,
            "R3: other-scope.md is not the named scope file",
        )
        proc = self.invoke(["cited", key])
        self.assertEqual(
            proc.exit,
            1,
            f"item 4/9: cited is the driver's holding check; a citation "
            f"to another numbered file must not exit 0; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        line = (proc.stdout or "").strip()
        self.assertTrue(
            line.startswith(f"{key}\tuncited:"),
            f"different_numbered citation is uncited; got {line!r}",
        )
        self.assertIn(reason, line)

    def test_cited_of_a_parseable_path_absent_from_root_is_uncited(self):
        self.plant_record(REC, "adversarial-review", "missing-scope-path")
        self.import_records()
        key = f"{REC}#1"
        missing_rel = ".dev/ladder-inputs/no-such-scope.md"
        evidence = evidence_for(3, path=missing_rel, why="path absent")
        self.dispose(key, "fixed", evidence)
        ok, reason = self.holds(evidence, self.repo, missing_rel)
        self.assertFalse(ok, "a missing scope file does not hold")
        proc = self.invoke(["cited", key])
        self.assertEqual(proc.exit, 1, proc.stderr or proc.stdout)
        line = (proc.stdout or "").strip()
        self.assertTrue(
            line.startswith(f"{key}\tuncited:"),
            f"item 4/9: parseable path absent from root is uncited; "
            f"got {line!r}",
        )
        self.assertIn(reason, line)

    def test_cited_of_an_absent_key_is_class_key_exit_2(self):
        self.plant_record(REC, "adversarial-review", "some-other-finding")
        self.import_records()
        missing = "20260901T000000Z-review-grok-ffffff#1"
        proc = self.invoke(["cited", missing])
        err = proc.stderr or ""
        self.assertEqual(proc.exit, 2, err or proc.stdout)
        self.assertEqual((proc.stdout or "").strip(), "")
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        head = lines[0].split(":", 1)[0].strip().lower().replace("_", "-")
        self.assertEqual(head, "key", lines[0])
        lower = lines[0].lower()
        for word in ("expected", "found", "needed"):
            self.assertIn(word, lower, lines[0])

    def test_cited_is_a_read_and_does_not_need_identity(self):
        self.plant_record(REC, "adversarial-review", "read-no-identity")
        self.import_records()
        key = f"{REC}#1"
        env = self.env()
        env.pop("WF_AGENT", None)
        self.assertNotIn("WF_AGENT", env)
        proc = run(
            [sys.executable, str(self.staged), "cited", key],
            cwd=self.repo,
            env=env,
            timeout=RUN_CAP,
        )
        head = (proc.stderr or "").split(":", 1)[0].strip().lower()
        self.assertNotEqual(
            head,
            "identity",
            "item 9: cited is a read; identity is not required",
        )
        self.assertEqual(
            proc.exit,
            1,
            proc.stderr or proc.stdout,
        )
        self.assertEqual((proc.stdout or "").strip(), f"{key}\topen")


class ImportReviewJobs(_CitationFixture):
    """Item 1 / R4 — every review job named by the gate can acquire rows."""

    def test_import_takes_an_adjudicate_record(self):
        self.plant_record(ADJ_REC, "adjudicate", "adjudicate-marker")
        proc = self.import_records()
        self.assertIn("1 finding", proc.stdout)
        self.assertIn(f"{ADJ_REC}#1", self.store_keys())

    def test_import_takes_a_sweep_record(self):
        self.plant_record(SWEEP_REC, "sweep", "sweep-marker")
        proc = self.import_records()
        self.assertIn("1 finding", proc.stdout)
        self.assertIn(f"{SWEEP_REC}#1", self.store_keys())

    def test_import_takes_a_plan_record(self):
        # A plan job's findings against the tree and against itself are
        # findings (T-1, 2026-09-14: the six p3 findings of record
        # 20260913T034513Z-plan-claude-892490 imported to the ledger).
        self.plant_record(PLAN_REC, "plan", "plan-marker")
        proc = self.import_records()
        self.assertIn("1 finding", proc.stdout)
        self.assertIn(f"{PLAN_REC}#1", self.store_keys())


class CitedWorkOrderScope(_CitationFixture):
    """Items 4 and 9 — cited uses the work-order Contract, not first scope-*."""

    def test_cited_of_a_work_order_citation_with_three_scope_files(self):
        self.plant_three_scope_files()
        self.plant_record(REC, "adversarial-review", "holding-citation")
        self.import_records()
        key = f"{REC}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.invoke(["cited", key])
        self.assertEqual(
            proc.exit,
            0,
            "items 4 and 9: cited of a holding work-order citation is "
            "exit 0 when the tree also has scope-discipline.md and the "
            f"cap file; stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertEqual(
            (proc.stdout or "").strip(),
            f"{key}\tcontract item 3 of {SCOPE_REL}",
        )
        self.assertEqual((proc.stderr or "").strip(), "")

    def test_cited_of_a_work_order_citation_when_discipline_is_first_brief_input(
        self,
    ):
        self.plant_three_scope_files()
        self.plant_record(
            REC,
            "adversarial-review",
            "holding-citation",
            brief_inputs=[DISCIPLINE_REL, SCOPE_REL],
        )
        self.import_records()
        key = f"{REC}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.invoke(["cited", key])
        self.assertEqual(
            proc.exit,
            0,
            "item 4: the first scope-* brief input must not make a "
            "work-order Contract citation uncited; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertEqual(
            (proc.stdout or "").strip(),
            f"{key}\tcontract item 3 of {SCOPE_REL}",
        )

    def test_cited_of_a_discipline_citation_when_discipline_is_first_brief_input(
        self,
    ):
        self.plant_three_scope_files()
        self.plant_record(
            REC,
            "adversarial-review",
            "discipline-citation",
            brief_inputs=[DISCIPLINE_REL, SCOPE_REL],
        )
        self.import_records()
        key = f"{REC}#1"
        self.dispose(key, "fixed", DISCIPLINE_EVIDENCE)
        proc = self.invoke(["cited", key])
        self.assertEqual(
            proc.exit,
            1,
            "item 4: a citation to scope-discipline.md is uncited even "
            "when that file is the first brief input; "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        line = (proc.stdout or "").strip()
        self.assertTrue(
            line.startswith(f"{key}\tuncited:"),
            f"discipline-clause citation is uncited; got {line!r}",
        )


class CitedRecordContainment(_CitationFixture):
    """Item 9 — cited does not read a record path outside the records dir."""

    def test_cited_refuses_a_record_field_that_escapes_the_records_directory(
        self,
    ):
        self.plant_record(REC, "adversarial-review", "escape")
        self.import_records()
        key = f"{REC}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        neighbour = self.invoke(["cited", key])
        self.assertEqual(
            neighbour.exit,
            0,
            "positive neighbour: a holding row with a record-id still "
            f"cites; stdout={neighbour.stdout!r} stderr={neighbour.stderr!r}",
        )
        outside = self.scratch / "agentscient-probe.json"
        self.world.plant_text(
            outside,
            json.dumps(
                {
                    "id": "escaped",
                    "job": "adversarial-review",
                    "brief": {
                        "inputs": [
                            {"path": SCOPE_REL, "sha256": "0" * 64}
                        ]
                    },
                }
            )
            + "\n",
        )
        escaped = str(self.scratch / "agentscient-probe")
        con = sqlite3.connect(self.store)
        try:
            con.execute(
                "update findings set record = ? where key = ?",
                (escaped, key),
            )
            con.commit()
            rows = list(
                con.execute(
                    "select record from findings where key = ?",
                    (key,),
                )
            )
        finally:
            con.close()
        self.assertEqual(rows, [(escaped,)])
        proc = self.invoke(["cited", key])
        err = proc.stderr or ""
        self.assertEqual(
            proc.exit,
            2,
            "item 9: a ledger record field that is not a record id is "
            f"class key, not a read of {outside}; stdout={proc.stdout!r} "
            f"stderr={err!r}",
        )
        self.assertEqual((proc.stdout or "").strip(), "")
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        head = lines[0].split(":", 1)[0].strip().lower().replace("_", "-")
        self.assertEqual(head, "key", lines[0])


if __name__ == "__main__":
    unittest.main()
