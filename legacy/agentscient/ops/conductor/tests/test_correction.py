"""Uncited-correction gate: correction.py check, the five found-texts.

Written from scope-gate-20260910.md items 1-8 and 10, and rulings R1,
R2, R5, R6, R7, R8. The tool is the subject and is not read here.
When the file is absent a stub that exits 0 with no output is invoked,
so the assertions fail rather than the suite erroring on a missing
path. findings.py is staged only to plant the ledger; the gate is
invoked as a subprocess against that planted HOME and records dir.
World owns HOME and the XDG roots; every subprocess goes through run.
"""

from __future__ import annotations

import importlib.util
import json
import shlex
import sqlite3
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
CORRECTION = REPO / "ops" / "conductor" / "tools" / "correction.py"
FINDINGS = REPO / "ops" / "conductor" / "tools" / "findings.py"
# findings.py imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")
REGISTRY = REPO / "ops" / "conductor" / "registry" / "correction.py.json"
LIVE_STORE = Path.home() / ".local" / "state" / "agentscient" / "findings.db"
AGENT = "Correction Tester <correction-tester@example.invalid>"

REVIEW = "20260901T000000Z-review-grok-aaaaaa"
PLAN = "20260901T030000Z-plan-claude-dddddd"
IMPLEMENT = "20260901T040000Z-code-codex-eeeeee"
AUTHOR = "20260901T050000Z-tests-grok-ffffff"
CHECK = "20260901T060000Z-check-tests-codex-111111"
SECURITY = "20260901T070000Z-review-codex-222222"
ADJUDICATE = "20260901T080000Z-adjudicate-grok-333333"
SWEEP = "20260901T090000Z-sweep-codex-444444"
MISSING = "20260101T000000Z-review-codex-000000"
OTHER_REVIEW = "20260901T000000Z-review-grok-ffffff"
OTHER_KEY = f"{OTHER_REVIEW}#1"
BAD_KEY = "not-a-key"
ZERO_KEY = f"{REVIEW}#0"
SCOPE_REL = ".dev/ladder-inputs/scope-gate-20260910.md"
OTHER_REL = ".dev/ladder-inputs/other-scope.md"
DISCIPLINE_REL = ".dev/ladder-inputs/scope-discipline.md"
CAP_REL = ".dev/ladder-inputs/scope-gate-20260910.scope.md"
VALID_EVIDENCE = (
    "contract item 3 of .dev/ladder-inputs/scope-gate-20260910.md: "
    "found-text missing"
)
ITEM8_CLAIM = (
    "contract item 8 of .dev/ladder-inputs/scope-gate-20260910.md: "
    "the accepted record does not carry the item the finding cites"
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
FUTURE = "2099-01-01T00:00:00Z"
PAST = "2000-01-01T00:00:00Z"
SAME = "2026-09-10T12:00:00Z"
SPY_REASON = "substituted-citation-holds-reason"
DISPOSITION_MARK = "python3 ops/conductor/tools/findings.py disposition"

REVIEW_JOBS = (
    (REVIEW, "adversarial-review"),
    (CHECK, "check-tests"),
    (SECURITY, "security-audit"),
    (ADJUDICATE, "adjudicate"),
    (SWEEP, "sweep"),
)

REGISTRY_ROWS = (
    {
        "argv": ["check"],
        "class": "arguments",
        "identity": False,
    },
    {
        "argv": [
            "check",
            "--job",
            "implement",
            "--role",
            "write",
            "--at",
            "2026-01-01T00:00:00Z",
            "--follows",
            MISSING,
        ],
        "class": "follows",
        "identity": False,
    },
)


def evidence_for(item: int, path: str = SCOPE_REL, why: str = "why") -> str:
    return f"contract item {item} of {path}: {why}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _GateFixture(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="correction-gate-")
        self.addCleanup(self.world.close)
        self._live = self.world.watch(LIVE_STORE)
        self.scratch = self.world.path("scratch")
        self.repo = self.world.path("repo")
        self.records = self.repo / ".dev" / "records" / "dispatches"
        self.jobs_root = self.world.path("jobs")
        self.scratch.mkdir()
        self.records.mkdir(parents=True)
        self.jobs_root.mkdir()
        self.store = (
            self.world.xdg_state / "agentscient" / "findings.db"
        )
        self.scope = self.repo / SCOPE_REL
        self.other = self.repo / OTHER_REL
        self._stage_findings()
        self.world.plant_text(self.scope, SCOPE_BODY)
        self.world.plant_text(self.other, OTHER_BODY)
        self.tool = self._correction_tool()
        self.assertFalse(
            self.store.exists(),
            "plant: isolated store must start absent",
        )

    def tearDown(self):
        self._live.assert_unchanged(self, f"live store mutated: {LIVE_STORE}")

    def _tools_dir(self) -> Path:
        return self.repo / "ops" / "conductor" / "tools"

    def _plant_staged_into_repo(self, staged: Path) -> Path:
        """World.stage always lands under world.root/stage/; the needed
        command names ops/conductor/tools/findings.py relative to --root,
        so the proven copy is planted into that layout too."""
        dest = self._tools_dir() / staged.name
        self.world.plant_bytes(dest, staged.read_bytes())
        for sibling in SIBLINGS:
            src = staged.with_name(sibling)
            if src.is_file():
                self.world.plant_bytes(dest.with_name(sibling), src.read_bytes())
        return dest

    def _stage_findings(self) -> None:
        self.assertTrue(FINDINGS.is_file(), f"{FINDINGS} is missing")
        staged = self.world.stage(FINDINGS, siblings=SIBLINGS)
        self.assertEqual(
            staged.read_bytes(),
            FINDINGS.read_bytes(),
            "copy-findings-bytes did not land intact",
        )
        self.findings = self._plant_staged_into_repo(staged)
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under HOME is the one asserted.
        self.conf = self.findings.with_name("lane.conf")
        self.world.plant_text(self.conf, "LANE_NAME='agentscient'\n")

    def _correction_tool(self) -> Path:
        staged = self.world.stage(CORRECTION, siblings=SIBLINGS)
        return self._plant_staged_into_repo(staged)

    def env(self) -> dict[str, str]:
        return self.world.env(
            extra={
                "WF_AGENT": AGENT,
                "LANE_CONF": str(self.conf),
            }
        )

    def findings_invoke(self, argv: list[str]):
        return run(
            [sys.executable, str(self.findings), *argv],
            cwd=self.repo,
            env=self.env(),
            timeout=RUN_CAP,
        )

    def plant_record(
        self,
        rec_id: str,
        job: str,
        claims: list[str] | None = None,
        brief_inputs: list[str] | None = None,
    ):
        if claims is None:
            claims = [f"claim-{rec_id}"]
        findings = [
            {
                "severity": "p1",
                "where": "ops/x.py",
                "claim": claim,
                "reproduce": None,
            }
            for claim in claims
        ]
        envelope = {
            "id": rec_id,
            "lane": "dev",
            "stage": "review",
            "unit": "lane/correction",
            "job": job,
            "role": (
                "read" if job not in ("implement", "author-tests") else "write"
            ),
            "status": "closed",
            "harness": {"name": "grok"},
            "model": {"ran": "grok-4.6"},
            "result": {
                "envelope": {
                    "status": "ok",
                    "verdict": "changes",
                    "findings": findings,
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
        self.world.plant_text(path, payload)
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
        self.assertIn(
            "1. The requirements are the numbered items",
            discipline.read_text(encoding="utf-8"),
        )
        self.assertNotRegex(
            cap.read_text(encoding="utf-8"),
            r"(?m)^\s*[1-9][0-9]*\.\s",
            "plant: the cap file has no numbered Contract item",
        )

    def import_records(self):
        proc = self.findings_invoke(["import"])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return proc

    def dispose(self, key: str, disposition: str, evidence: str):
        proc = self.findings_invoke(
            ["disposition", key, disposition, evidence]
        )
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return proc

    def set_disposed_at(self, key: str, stamp: str) -> None:
        self.assertTrue(self.store.is_file(), "store missing for timestamp plant")
        con = sqlite3.connect(self.store)
        try:
            con.execute(
                "update findings set disposed_at = ? where key = ?",
                (stamp, key),
            )
            con.commit()
            rows = list(
                con.execute(
                    "select disposed_at from findings where key = ?",
                    (key,),
                )
            )
        finally:
            con.close()
        self.assertEqual(rows, [(stamp,)])

    def _store_state(self):
        paths = [
            self.store,
            Path(str(self.store) + "-wal"),
            Path(str(self.store) + "-shm"),
            Path(str(self.store) + "-journal"),
        ]
        return tuple(
            (p.name, p.is_file(), p.read_bytes() if p.is_file() else None)
            for p in paths
        )

    def check(
        self,
        *,
        job: str = "implement",
        role: str = "write",
        at: str = FUTURE,
        follows: list[str] | None = None,
        inputs: list[str] | None = None,
        answers: list[str] | None = None,
        extra: list[str] | None = None,
    ):
        argv = [
            "check",
            "--job",
            job,
            "--role",
            role,
            "--at",
            at,
            "--records",
            str(self.records),
            "--jobs-root",
            str(self.jobs_root),
            "--root",
            str(self.repo),
        ]
        for rec in follows or []:
            argv.extend(["--follows", rec])
        for path in inputs or []:
            argv.extend(["--input", path])
        for key in answers or []:
            argv.extend(["--answers", key])
        if extra:
            argv.extend(extra)
        before = self._store_state()
        proc = run(
            [sys.executable, str(self.tool), *argv],
            cwd=self.repo,
            env=self.env(),
            timeout=RUN_CAP,
        )
        after = self._store_state()
        self.assertEqual(
            after,
            before,
            "items 3 and 6: the gate does not create, rewrite, or "
            "auto-dispose the findings store",
        )
        return proc

    def holds(self, evidence: str, root: Path, scope_path: str):
        mod = _load_module(self.findings, "findings_for_gate")
        fn = getattr(mod, "citation_holds", None) if mod is not None else None
        if not callable(fn):
            return False, "citation_holds is not defined"
        try:
            return fn(evidence, root, scope_path)
        except TypeError:
            return False, "citation_holds does not take scope_path"

    def assert_refusal(self, proc, class_name: str) -> str:
        refusal = proc.refusal()
        self.assertEqual(
            refusal.cls,
            class_name,
            f"refusal class must be {class_name!r}: {refusal.line!r}",
        )
        return refusal.line

    def found_clause(self, line: str) -> str:
        lower = line.lower()
        start = lower.index("found ")
        end = lower.index("; needed")
        return line[start + len("found ") : end]

    def needed_clause(self, line: str) -> str:
        lower = line.lower()
        start = lower.index("needed ")
        return line[start + len("needed ") :]

    def assert_expected_before_stamp(self, line: str, stamp: str) -> None:
        self.assertIn(
            "every finding answered by this correction "
            "disposed with a contract citation before",
            line,
        )
        self.assertIn(stamp, line)

    def parse_needed_disposition(self, needed: str) -> list[str]:
        self.assertIn(DISPOSITION_MARK, needed)
        start = needed.index(DISPOSITION_MARK)
        tail = needed[start:]
        try:
            parts = shlex.split(tail)
        except ValueError as exc:
            self.fail(f"needed command is not shell-quotable: {needed!r} ({exc})")
        self.assertGreaterEqual(
            len(parts),
            6,
            f"needed omits disposition operands: {parts!r}",
        )
        self.assertEqual(parts[0], "python3")
        self.assertEqual(parts[1], "ops/conductor/tools/findings.py")
        self.assertEqual(parts[2], "disposition")
        return parts[:6]

    def assert_needed_disposition(
        self,
        needed: str,
        key: str,
        *,
        import_then: bool = False,
    ) -> list[str]:
        if import_then:
            self.assertIn(
                "python3 ops/conductor/tools/findings.py import, then",
                needed,
            )
        parts = self.parse_needed_disposition(needed)
        self.assertEqual(parts[3], key, parts)
        self.assertTrue(parts[4].strip(), "needed omits the disposition")
        evidence = parts[5]
        self.assertIn("contract item ", evidence, evidence)
        self.assertIn(f" of {SCOPE_REL}", evidence, evidence)
        self.assertIn(shlex.quote(key), needed)
        self.assertIn(shlex.quote(evidence), needed)
        return parts

    def run_needed_disposition(self, parts: list[str]):
        self.assertEqual(parts[:3], [
            "python3",
            "ops/conductor/tools/findings.py",
            "disposition",
        ])
        proc = run(
            [sys.executable, *parts[1:6]],
            cwd=self.repo,
            env=self.env(),
            timeout=RUN_CAP,
        )
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return proc


class FiveFoundTexts(_GateFixture):
    """Item 3 — distinct found-texts, founding wire, exact needed command."""

    def test_no_answers_key_named_for_a_correction(self):
        self.plant_record(REVIEW, "adversarial-review")
        proc = self.check(follows=[REVIEW])
        line = self.assert_refusal(proc, "correction")
        self.assert_expected_before_stamp(line, FUTURE)
        found = self.found_clause(line)
        self.assertEqual(
            found,
            f"no --answers key named for a correction following {REVIEW} "
            f"(adversarial-review)",
        )
        needed = self.needed_clause(line)
        self.assertIn("pass --answers", needed)
        self.assertIn(DISPOSITION_MARK, needed)
        self.assertIn(SCOPE_REL, needed)
        self.assertIn("contract item ", needed)
        self.assertIn(shlex.quote(f"{REVIEW}#1"), needed)

    def test_key_absent_from_the_store_names_the_path(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        self.assertTrue(self.store.is_file())
        proc = self.check(follows=[REVIEW], answers=[OTHER_KEY])
        line = self.assert_refusal(proc, "correction")
        self.assert_expected_before_stamp(line, FUTURE)
        found = self.found_clause(line)
        self.assertEqual(
            found,
            f"key {OTHER_KEY} absent from the findings store {self.store}",
        )
        self.assertNotIn("(no store)", found)
        needed = self.needed_clause(line)
        self.assert_needed_disposition(needed, OTHER_KEY, import_then=True)

    def test_missing_store_names_no_store_and_does_not_create_one(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.assertFalse(self.store.exists())
        key = f"{REVIEW}#1"
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertIn("absent from the findings store", found)
        self.assertIn("(no store)", found)
        self.assertIn(str(self.store), found)
        self.assertFalse(
            self.store.exists(),
            "item 3: the gate never creates the store",
        )
        needed = self.needed_clause(line)
        self.assert_needed_disposition(needed, key, import_then=True)

    def test_key_still_open(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertEqual(found, f"key {key} still open")
        needed = self.needed_clause(line)
        parts = self.assert_needed_disposition(needed, key)
        self.run_needed_disposition(parts)
        again = self.check(follows=[REVIEW], answers=[key])
        self.assertEqual(again.exit, 0, again.stderr or again.stdout)
        body = (again.stdout or "").strip()
        self.assertTrue(body, "accept path prints JSON on stdout")
        payload = json.loads(body)
        self.assertTrue(payload["correction"])
        self.assertEqual(payload["answers"][0]["key"], key)

    def test_disposed_without_a_contract_citation(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", "fixed in tests, no item")
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertIn(key, found)
        self.assertIn("without a contract citation", found)
        self.assertIn("fixed", found)
        ok, reason = self.holds("fixed in tests, no item", self.repo, SCOPE_REL)
        self.assertFalse(ok)
        self.assertIn(reason, found)
        needed = self.needed_clause(line)
        parts = self.assert_needed_disposition(needed, key)
        self.run_needed_disposition(parts)
        again = self.check(follows=[REVIEW], answers=[key])
        self.assertEqual(again.exit, 0, again.stderr or again.stdout)

    def test_newlines_in_evidence_still_one_stderr_line(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", "line one\nline two without a citation")
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        self.assertNotIn("\n", line)
        self.assertIn("without a contract citation", line)

    def test_disposed_after_the_mint_attempt(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.check(follows=[REVIEW], answers=[key], at=PAST)
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertIn(key, found)
        self.assertIn("after the mint attempt at", found)
        self.assertIn(PAST, found)
        needed = self.needed_clause(line)
        self.assert_needed_disposition(needed, key)

    def test_same_second_is_not_before_the_mint(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        self.set_disposed_at(key, SAME)
        proc = self.check(follows=[REVIEW], answers=[key], at=SAME)
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertEqual(
            found,
            f"key {key} disposed at {SAME}, not before the mint attempt "
            f"at {SAME}",
        )
        needed = self.needed_clause(line)
        self.assertIn("wait one second and retry", needed)
        self.assertNotIn(DISPOSITION_MARK, needed)

    def test_item_present_in_grammar_but_absent_from_the_file_is_uncited(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        evidence = evidence_for(99, why="no such item")
        self.dispose(key, "fixed", evidence)
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertIn("without a contract citation", found)
        ok, reason = self.holds(evidence, self.repo, SCOPE_REL)
        self.assertFalse(ok)
        self.assertIn(reason, found)

    def test_citation_to_a_different_numbered_file_is_uncited(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        evidence = evidence_for(1, path=OTHER_REL, why="other file")
        self.dispose(key, "fixed", evidence)
        proc = self.check(
            follows=[REVIEW],
            answers=[key],
            inputs=[str(self.scope)],
        )
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertIn("without a contract citation", found)
        ok, reason = self.holds(evidence, self.repo, SCOPE_REL)
        self.assertFalse(ok)
        self.assertIn(reason, found)

    def test_first_failure_in_answers_order_is_the_open_key(self):
        self.plant_record(REVIEW, "adversarial-review", ["one", "two"])
        self.import_records()
        first = f"{REVIEW}#1"
        second = f"{REVIEW}#2"
        self.dispose(second, "fixed", VALID_EVIDENCE)
        proc = self.check(follows=[REVIEW], answers=[first, second])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertEqual(found, f"key {first} still open")
        self.assertNotIn(second, found)

    def test_later_open_answer_is_refused_when_the_first_is_cited(self):
        self.plant_record(REVIEW, "adversarial-review", ["one", "two"])
        self.import_records()
        first = f"{REVIEW}#1"
        second = f"{REVIEW}#2"
        self.dispose(first, "fixed", VALID_EVIDENCE)
        proc = self.check(follows=[REVIEW], answers=[first, second])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertEqual(found, f"key {second} still open")
        self.assertNotIn(first, found)


class AcceptPath(_GateFixture):
    """Item 8 — accepted correction prints keys and citations."""

    def test_satisfying_row_prints_json_answers_with_citations(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.check(follows=[REVIEW], answers=[key])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        self.assertEqual((proc.stderr or "").strip(), "")
        body = (proc.stdout or "").strip()
        self.assertTrue(body, "accept path prints JSON on stdout")
        payload = json.loads(body)
        self.assertTrue(payload["correction"])
        rows = payload["answers"]
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["key"], key)
        self.assertEqual(row["disposition"], "fixed")
        self.assertTrue(row["disposed_at"])
        self.assertLess(row["disposed_at"], FUTURE)
        citation = row["citation"]
        self.assertEqual(str(citation["item"]), "3")
        self.assertEqual(citation["path"], SCOPE_REL)

    def test_two_satisfying_answers_record_item_1_and_item_10(self):
        self.plant_record(REVIEW, "adversarial-review", ["one", "two"])
        self.import_records()
        first = f"{REVIEW}#1"
        second = f"{REVIEW}#2"
        self.dispose(first, "fixed", evidence_for(1, why="item 1 holds"))
        self.dispose(second, "fixed", evidence_for(10, why="item 10 holds"))
        proc = self.check(follows=[REVIEW], answers=[first, second])
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        body = (proc.stdout or "").strip()
        self.assertTrue(body, "accept path prints JSON on stdout")
        payload = json.loads(body)
        self.assertTrue(payload["correction"])
        rows = payload["answers"]
        self.assertEqual(len(rows), 2, rows)
        self.assertEqual([row["key"] for row in rows], [first, second])
        self.assertEqual(str(rows[0]["citation"]["item"]), "1")
        self.assertEqual(str(rows[1]["citation"]["item"]), "10")
        self.assertEqual(rows[0]["citation"]["path"], SCOPE_REL)
        self.assertEqual(rows[1]["citation"]["path"], SCOPE_REL)

    def test_author_tests_following_check_tests_is_a_correction(self):
        self.plant_record(CHECK, "check-tests")
        proc = self.check(job="author-tests", follows=[CHECK])
        line = self.assert_refusal(proc, "correction")
        self.assertIn("no --answers key named", line)
        self.assertIn(CHECK, line)
        self.assertIn("check-tests", line)


class ReviewJobShape(_GateFixture):
    """Item 1 — every read review job is the correction shape."""

    def test_every_review_job_follow_is_a_correction(self):
        for rec_id, job in REVIEW_JOBS:
            with self.subTest(job=job):
                self.plant_record(rec_id, job)
                proc = self.check(follows=[rec_id])
                line = self.assert_refusal(proc, "correction")
                found = self.found_clause(line)
                self.assertEqual(
                    found,
                    f"no --answers key named for a correction following "
                    f"{rec_id} ({job})",
                )

    def test_every_review_job_out_input_is_a_correction(self):
        for rec_id, job in REVIEW_JOBS:
            with self.subTest(job=job):
                self.plant_record(rec_id, job)
                note = self.jobs_root / rec_id / "out" / "note.md"
                self.world.plant_bytes(note, b"review artifact\n")
                proc = self.check(inputs=[str(note)])
                line = self.assert_refusal(proc, "correction")
                self.assertIn("no --answers key named", line)
                self.assertIn(rec_id, line)
                self.assertIn(job, line)


class NonCorrections(_GateFixture):
    """Item 7 / R8 — write jobs that are not the item-1 shape are untouched."""

    def _assert_not_a_correction(self, proc) -> None:
        err = proc.stderr or ""
        self.assertEqual(
            proc.exit,
            0,
            "item 7: a non-correction is not inspected and must not be "
            f"refused; got exit {proc.exit} stderr={err!r} "
            f"stdout={proc.stdout!r}",
        )
        self.assertEqual(err.strip(), "")
        body = (proc.stdout or "").strip()
        self.assertTrue(body, "non-correction prints JSON")
        payload = json.loads(body)
        self.assertFalse(payload["correction"])
        self.assertEqual(payload.get("answers", []), [])

    def test_write_following_a_plan_record_is_not_a_correction(self):
        self.plant_record(PLAN, "plan")
        proc = self.check(follows=[PLAN])
        self._assert_not_a_correction(proc)

    def test_write_following_an_implement_record_is_not_a_correction(self):
        self.plant_record(IMPLEMENT, "implement")
        proc = self.check(follows=[IMPLEMENT])
        self._assert_not_a_correction(proc)

    def test_write_following_an_author_tests_record_is_not_a_correction(self):
        self.plant_record(AUTHOR, "author-tests")
        proc = self.check(follows=[AUTHOR])
        self._assert_not_a_correction(proc)

    def test_write_following_nothing_is_not_a_correction(self):
        proc = self.check()
        self._assert_not_a_correction(proc)

    def test_read_role_following_a_review_is_not_inspected(self):
        self.plant_record(REVIEW, "adversarial-review")
        proc = self.check(job="check-tests", role="read", follows=[REVIEW])
        self._assert_not_a_correction(proc)
        self.assertFalse(
            self.store.exists(),
            "item 7: a read role does not open the store",
        )

    def test_answers_on_a_non_correction_is_arguments(self):
        self.plant_record(PLAN, "plan")
        proc = self.check(follows=[PLAN], answers=[f"{REVIEW}#1"])
        line = self.assert_refusal(proc, "arguments")
        needed = self.needed_clause(line).lower()
        self.assertIn("remove --answers", needed)
        self.assertNotIn("still open", line)
        self.assertNotIn("absent from the findings store", line)

    def test_answers_on_a_write_following_nothing_is_arguments(self):
        proc = self.check(answers=[f"{REVIEW}#1"])
        line = self.assert_refusal(proc, "arguments")
        self.assertIn("remove --answers", self.needed_clause(line).lower())


class OutInputShape(_GateFixture):
    """Item 1 — an input under a review job's out/ is the correction shape."""

    def test_out_input_without_answers_is_the_no_key_arm(self):
        self.plant_record(REVIEW, "adversarial-review")
        note = self.jobs_root / REVIEW / "out" / "note.md"
        self.world.plant_bytes(note, b"review artifact\n")
        proc = self.check(inputs=[str(note)])
        line = self.assert_refusal(proc, "correction")
        self.assertIn("no --answers key named", line)
        self.assertIn(REVIEW, line)


class FollowsRefusals(_GateFixture):
    """Item 1 / R5 / R6 — shape is read from the record, never guessed."""

    def test_unknown_followed_id_is_class_follows(self):
        proc = self.check(follows=[MISSING])
        line = self.assert_refusal(proc, "follows")
        self.assertIn(MISSING, line)
        self.assertIn(".dev/records/dispatches", line)

    def test_malformed_followed_id_is_class_follows(self):
        proc = self.check(follows=["not-an-id"])
        line = self.assert_refusal(proc, "follows")
        self.assertIn("not-an-id", line)

    def test_record_id_field_mismatch_is_class_follows(self):
        path = self.plant_record(REVIEW, "adversarial-review")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["id"] = CHECK
        self.world.plant_bytes(
            path, (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        )
        proc = self.check(follows=[REVIEW])
        line = self.assert_refusal(proc, "follows")
        self.assertIn(REVIEW, line)

    def test_job_outside_the_registry_is_class_follows(self):
        self.plant_record(REVIEW, "not-a-job")
        proc = self.check(follows=[REVIEW])
        line = self.assert_refusal(proc, "follows")
        self.assertIn(REVIEW, line)

    def test_symlink_escape_from_records_is_class_follows(self):
        outside = self.scratch / f"{REVIEW}.json"
        self.plant_record(REVIEW, "adversarial-review")
        src = self.records / f"{REVIEW}.json"
        outside.write_bytes(src.read_bytes())
        src.unlink()
        src.symlink_to(outside)
        self.assertTrue(src.is_symlink())
        proc = self.check(follows=[REVIEW])
        line = self.assert_refusal(proc, "follows")
        self.assertIn(REVIEW, line)


class ArgumentsShape(_GateFixture):
    """Item 2 / R2 — a key that fails the shape is refused, never echoed."""

    def test_malformed_answers_key_is_arguments_and_not_echoed(self):
        self.plant_record(REVIEW, "adversarial-review")
        proc = self.check(follows=[REVIEW], answers=[BAD_KEY])
        line = self.assert_refusal(proc, "arguments")
        self.assertNotIn(
            BAD_KEY,
            line,
            "R2: a key that fails the shape is never echoed",
        )

    def test_zero_n_answers_key_is_arguments_and_not_echoed(self):
        self.plant_record(REVIEW, "adversarial-review")
        proc = self.check(follows=[REVIEW], answers=[ZERO_KEY])
        line = self.assert_refusal(proc, "arguments")
        self.assertNotIn(
            ZERO_KEY,
            line,
            "R2: n starts at 1; #0 is not echoed in the remedy",
        )


class SharedDefinition(_GateFixture):
    """Item 4 — the gate and findings.py read one citation definition."""

    def test_gate_uncited_reason_comes_from_citation_holds(self):
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        marker = SPY_REASON
        suffix = (
            "\n\ndef citation_holds(evidence, root, scope_path=None, "
            "*args, **kwargs):\n"
            f"    return False, {marker!r}\n"
        )
        before = self.findings.read_text(encoding="utf-8")
        self.world.plant_text(self.findings, before + suffix)
        landed = self.findings.read_text(encoding="utf-8")
        self.assertTrue(
            landed.endswith(suffix),
            "plant: citation_holds substitution must land intact",
        )
        self.assertIn(marker, landed)
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertIn("without a contract citation", found)
        self.assertIn(marker, found)


class NoOverride(_GateFixture):
    """Item 6 — no overrides row and no hand-grant escape."""

    def test_override_flag_does_not_admit_a_correction(self):
        self.plant_record(REVIEW, "adversarial-review")
        proc = self.check(
            follows=[REVIEW],
            extra=["--override", "correction:reason"],
        )
        err = proc.stderr or ""
        self.assertNotEqual(proc.exit, 0, proc.stdout)
        head = err.split(":", 1)[0].strip().lower().replace("_", "-")
        self.assertNotEqual(head, "correction")
        self.assertNotIn("override permitted", err.lower())
        self.assertNotIn("hand-grant", err.lower())
        body = (proc.stdout or "").strip()
        if body:
            payload = json.loads(body)
            self.assertFalse(payload.get("correction"))


class RegistryJoin(_GateFixture):
    """Item 10 — the gate joins the refusal registry with two hermetic rows."""

    def test_registry_file_has_the_two_hermetic_rows(self):
        self.assertTrue(
            REGISTRY.is_file(),
            "item 10: correction.py joins the registry in the landing change",
        )
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual(data["tool"], "correction.py")
        self.assertEqual(data["rows"], list(REGISTRY_ROWS))

    def test_bare_check_is_class_arguments(self):
        proc = run(
            [sys.executable, str(self.tool), "check"],
            cwd=self.repo,
            env=self.env(),
            timeout=RUN_CAP,
        )
        self.assert_refusal(proc, "arguments")


class WorkOrderScope(_GateFixture):
    """Items 1, 3, 4 — the work-order Contract is the scope file.

    Item 1 reads the correction from --follows or an out/ input, not from
    a scope file on --input. Item 4 requires the disposition to name a
    numbered item of that work-order file. scope-discipline.md and the
    cap file are present on the real tree and are not the Contract.
    """

    def test_holding_work_order_citation_is_accepted_among_three_scope_files(
        self,
    ):
        self.plant_three_scope_files()
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.check(follows=[REVIEW], answers=[key])
        self.assertEqual(
            proc.exit,
            0,
            "items 1 and 4: a correction is --follows a review plus "
            "a holding citation to the work-order Contract; the other "
            f"scope-*.md files must not refuse it; stderr={proc.stderr!r} "
            f"stdout={proc.stdout!r}",
        )
        payload = json.loads((proc.stdout or "").strip())
        self.assertTrue(payload["correction"])
        self.assertEqual(str(payload["answers"][0]["citation"]["item"]), "3")
        self.assertEqual(payload["answers"][0]["citation"]["path"], SCOPE_REL)

    def test_needed_command_then_recheck_accepts_with_no_scope_input(self):
        self.plant_three_scope_files()
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        needed = self.needed_clause(line)
        self.assertNotIn("<scope path>", needed)
        self.assertNotIn(DISCIPLINE_REL, needed)
        self.assertNotIn(CAP_REL, needed)
        parts = self.assert_needed_disposition(needed, key)
        self.assertIn(f" of {SCOPE_REL}", parts[5])
        self.run_needed_disposition(parts)
        again = self.check(follows=[REVIEW], answers=[key])
        self.assertEqual(
            again.exit,
            0,
            "item 3: the printed disposition command satisfies the "
            f"next mint; stderr={again.stderr!r} stdout={again.stdout!r}",
        )
        payload = json.loads((again.stdout or "").strip())
        self.assertTrue(payload["correction"])

    def test_work_order_citation_holds_when_discipline_is_the_first_input(self):
        self.plant_three_scope_files()
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.check(
            follows=[REVIEW],
            answers=[key],
            inputs=[
                str(self.repo / DISCIPLINE_REL),
                str(self.repo / SCOPE_REL),
            ],
        )
        self.assertEqual(
            proc.exit,
            0,
            "item 4: the first scope-* input must not make a work-order "
            "Contract citation fail; "
            f"stderr={proc.stderr!r} stdout={proc.stdout!r}",
        )
        payload = json.loads((proc.stdout or "").strip())
        self.assertTrue(payload["correction"])
        self.assertEqual(payload["answers"][0]["citation"]["path"], SCOPE_REL)

    def test_discipline_citation_is_uncited_when_it_is_the_first_input(self):
        self.plant_three_scope_files()
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", DISCIPLINE_EVIDENCE)
        proc = self.check(
            follows=[REVIEW],
            answers=[key],
            inputs=[
                str(self.repo / DISCIPLINE_REL),
                str(self.repo / SCOPE_REL),
            ],
        )
        line = self.assert_refusal(proc, "correction")
        self.assertIn("without a contract citation", self.found_clause(line))

    def test_cap_file_input_does_not_replace_the_work_order_contract(self):
        self.plant_three_scope_files()
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        proc = self.check(
            follows=[REVIEW],
            answers=[key],
            inputs=[str(self.repo / CAP_REL)],
        )
        self.assertEqual(
            proc.exit,
            0,
            "item 4: the 1024-byte cap file has no numbered Contract "
            "item; a holding citation to the work-order file still "
            f"satisfies; stderr={proc.stderr!r} stdout={proc.stdout!r}",
        )
        payload = json.loads((proc.stdout or "").strip())
        self.assertTrue(payload["correction"])
        self.assertEqual(payload["answers"][0]["citation"]["path"], SCOPE_REL)

    def test_needed_does_not_cite_the_cap_file_when_it_is_the_only_input(self):
        self.plant_three_scope_files()
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        proc = self.check(
            follows=[REVIEW],
            answers=[key],
            inputs=[str(self.repo / CAP_REL)],
        )
        line = self.assert_refusal(proc, "correction")
        needed = self.needed_clause(line)
        self.assertNotIn(CAP_REL, needed)
        self.assertNotIn("<scope path>", needed)
        parts = self.assert_needed_disposition(needed, key)
        self.assertIn(f" of {SCOPE_REL}", parts[5])
        self.run_needed_disposition(parts)
        again = self.check(
            follows=[REVIEW],
            answers=[key],
            inputs=[str(self.repo / CAP_REL)],
        )
        self.assertEqual(
            again.exit,
            0,
            "item 3: running the printed command must satisfy the next "
            f"check; stderr={again.stderr!r} stdout={again.stdout!r}",
        )


class AnswerKeysTiedToTheReview(_GateFixture):
    """Items 1, 2, 6, 8 — named keys belong to the review that is the shape.

    One scope-*.md so a holding citation can satisfy item 4; the
    assertion here is only that an unrelated review's cited key is
    not an override.
    """

    def test_cited_key_from_an_unrelated_review_does_not_admit_the_correction(
        self,
    ):
        self.plant_record(REVIEW, "adversarial-review", ["this-review"])
        self.plant_record(OTHER_REVIEW, "adversarial-review", ["other-review"])
        self.import_records()
        self.dispose(OTHER_KEY, "fixed", VALID_EVIDENCE)
        own = f"{REVIEW}#1"
        self.dispose(own, "fixed", VALID_EVIDENCE)
        neighbour = self.check(follows=[REVIEW], answers=[own])
        self.assertEqual(
            neighbour.exit,
            0,
            "positive neighbour: the followed review's own cited key "
            f"is accepted; stderr={neighbour.stderr!r}",
        )
        proc = self.check(follows=[REVIEW], answers=[OTHER_KEY])
        self.assertNotEqual(
            proc.exit,
            0,
            "items 1, 2, 6, 8: a previously cited key from another "
            "review must not stand in for the followed review; "
            f"stdout={proc.stdout!r}",
        )
        line = self.assert_refusal(proc, "correction")
        needed = self.needed_clause(line)
        self.assertIn(f"{REVIEW}#1", needed)


class NeededCitesTheFindingItem(_GateFixture):
    """Items 3 and 4 — the printed command cites the item the finding quotes."""

    def test_needed_open_arm_cites_item_8_from_the_finding_claim(self):
        self.plant_record(REVIEW, "adversarial-review", [ITEM8_CLAIM])
        self.import_records()
        key = f"{REVIEW}#1"
        proc = self.check(follows=[REVIEW], answers=[key])
        line = self.assert_refusal(proc, "correction")
        found = self.found_clause(line)
        self.assertEqual(found, f"key {key} still open")
        needed = self.needed_clause(line)
        parts = self.assert_needed_disposition(needed, key)
        evidence = parts[5]
        self.assertIn(
            f"contract item 8 of {SCOPE_REL}",
            evidence,
            "items 3 and 4: the needed disposition cites the numbered "
            "item the finding quotes, not a hard-coded item 1",
        )
        self.assertNotIn(f"contract item 1 of {SCOPE_REL}", evidence)

    def test_cited_and_the_gate_agree_on_the_work_order_scope(self):
        self.plant_three_scope_files()
        self.plant_record(REVIEW, "adversarial-review")
        self.import_records()
        key = f"{REVIEW}#1"
        self.dispose(key, "fixed", VALID_EVIDENCE)
        cited = self.findings_invoke(["cited", key])
        gate = self.check(follows=[REVIEW], answers=[key])
        self.assertEqual(
            cited.exit,
            0,
            "item 9: cited of a holding work-order citation is exit 0 "
            f"when three scope-*.md files exist; stdout={cited.stdout!r} "
            f"stderr={cited.stderr!r}",
        )
        self.assertEqual(
            (cited.stdout or "").strip(),
            f"{key}\tcontract item 3 of {SCOPE_REL}",
        )
        self.assertEqual(
            gate.exit,
            0,
            "items 4 and 9: the mint gate and cited must resolve the "
            f"same scope file; stderr={gate.stderr!r} stdout={gate.stdout!r}",
        )


if __name__ == "__main__":
    unittest.main()
