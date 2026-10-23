"""Ladder stop before fix stages when audit findings are undisposed.

Written from scope-gate-20260910.md item 9 (and the item-1 shape those
fix stages must carry). ladder.py is the subject and is not read here.
Tests reuse _LadderFixture; the FINDINGS_PY stub is extended with a
plant answering `cited`, and the dispatch stub plants stage_findings
onto both audit envelopes.

A driver that launches fix-tests while a finding is undisposed still
collects; it fails the stop assertion. A driver that checks only the
first finding, or only one audit envelope, still collects; it fails
the every-finding assertion. A driver that caches the fix-tests check
when launching fix-code still collects; it fails the fresh-check
assertion.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import unittest
from pathlib import Path

from test_ladder_lever import RUN_CAP, _LadderFixture

from ops.testkit import World, run

RECORD_ID = r"^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}$"
ANSWER_KEY = r"^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}#[1-9][0-9]*$"
SCOPE_PATH = ".dev/ladder-inputs/scope-gate-20260910.md"
CITATION = f"contract item 9 of {SCOPE_PATH}"
REVIEW_JOBS = frozenset(
    {
        "check-tests",
        "security-audit",
        "adversarial-review",
        "adjudicate",
        "sweep",
    }
)
AUDIT_STAGES = ("security", "adversarial")


def _finding(tag: str) -> dict:
    return {
        "severity": "p2",
        "where": f"ops/conductor/ladder-lever.md:{tag}",
        "claim": (
            f"UNDISPOSED_{tag}_aa32 audit finding lacks a cited "
            "disposition"
        ),
        "reproduce": (
            "python3 -m unittest discover -s ops/conductor/tests "
            "-p test_ladder_lever_undisposed.py"
        ),
    }


SECURITY_FINDINGS = (_finding("SEC1"), _finding("SEC2"))
ADVERSARIAL_FINDINGS = (_finding("ADV1"), _finding("ADV2"))
UNKNOWN_KEY = "20260901T000000Z-review-grok-aaaaaa#1"

DISPATCH_FINDINGS_NEEDLE = "findings = []\n"
DISPATCH_FINDINGS_INSERT = (
    "findings = []\n"
    "extra = (plant.get(\"stage_findings\") or {}).get(name) or []\n"
    "if extra:\n"
    "    findings.extend(list(extra))\n"
)


class _UndisposedFixture(_LadderFixture):
    """Audit-changes plant plus cited / stage_findings stub extensions."""

    def _plant_stub_scripts(self) -> None:
        super()._plant_stub_scripts()
        self._extend_dispatch_stage_findings()
        self._plant_findings()

    def _extend_dispatch_stage_findings(self) -> None:
        path = self.stub_dir / "dispatch.py"
        before = path.read_text(encoding="utf-8")
        self.assertIn(
            DISPATCH_FINDINGS_NEEDLE,
            before,
            "dispatch stub findings plant anchor missing",
        )
        self.assertEqual(before.count(DISPATCH_FINDINGS_NEEDLE), 1, before)
        after = before.replace(
            DISPATCH_FINDINGS_NEEDLE, DISPATCH_FINDINGS_INSERT, 1,
        )
        self.assertNotEqual(after, before, "stage_findings insert was a no-op")
        self.assertIn("stage_findings", after)
        self._plant_bytes(path, after.encode("utf-8"))
        landed = path.read_text(encoding="utf-8")
        self.assertIn("stage_findings", landed)

    def _plant_findings(
        self,
        *,
        cited_all: bool = False,
        cite_first_only: bool = False,
    ) -> None:
        responses = [{"match": ["export"], "stdout": "{}\n", "exit": 0}]
        if cite_first_only:
            responses.insert(
                0,
                {"match": ["cited", "#1"], "stdout": CITATION + "\n", "exit": 0},
            )
        if cited_all:
            cited_response = {"match": ["cited"], "stdout": CITATION + "\n", "exit": 0}
        else:
            cited_response = {
                "match": ["cited"],
                "stdout": UNKNOWN_KEY + "\topen\n",
                "exit": 1,
            }
        responses.append(cited_response)
        planted = self.world.tools.plant(
            "findings",
            responses=responses,
        )
        dest = self.stub_dir / "findings.py"
        self.world.plant_bytes(dest, planted.read_bytes())
        self.assertIn("fake_program import serve", dest.read_text(encoding="utf-8"))

    def setUp(self):
        super().setUp()
        self.world: World
        wt = self.repo / "ops" / "conductor" / "tools" / "findings.py"
        self.assertTrue(wt.is_file(), wt)
        self.assertIn("fake_program import serve", wt.read_text(encoding="utf-8"))
        self.assertIn(
            "stage_findings",
            (self.stub_dir / "dispatch.py").read_text(encoding="utf-8"),
        )
        probe = self.cited_probe(UNKNOWN_KEY)
        self.assertEqual(
            probe.returncode, 1,
            f"cited stub must treat an unknown key as open: "
            f"{probe.stdout!r} {probe.stderr!r}",
        )
        self.assertIn("\topen", probe.stdout)
        log = self.world.tools.log_path("findings")
        if log.exists():
            log.unlink()
        self.assertEqual(self.findings_argvs(), [])

    def cited_probe(self, key: str):
        tool = self.repo / "ops" / "conductor" / "tools" / "findings.py"
        return run(
            [sys.executable, str(tool), "cited", key],
            cwd=str(self.invoke_cwd),
            env=self.env(),
            timeout=RUN_CAP,
        )

    def plant_undisposed_audit(
        self,
        *,
        cited_all: bool = False,
        cite_first_only: bool = False,
        revoke_after_fix_tests: bool = False,
    ) -> None:
        updates = {
            "verdicts": {
                "security": "changes",
                "adversarial": "changes",
            },
            "stage_findings": {
                "security": [dict(row) for row in SECURITY_FINDINGS],
                "adversarial": [dict(row) for row in ADVERSARIAL_FINDINGS],
            },
            "cited_all": cited_all,
            "cite_first_only": cite_first_only,
            "revoke_after_fix_tests": revoke_after_fix_tests,
        }
        self.patch_plant(**updates)
        self._plant_findings(
            cited_all=cited_all or revoke_after_fix_tests,
            cite_first_only=cite_first_only,
        )
        plant = self.load_plant()
        self.assertEqual(plant["verdicts"].get("security"), "changes")
        self.assertEqual(plant["verdicts"].get("adversarial"), "changes")
        planted_sec = (plant.get("stage_findings") or {}).get("security") or []
        planted_adv = (
            (plant.get("stage_findings") or {}).get("adversarial") or []
        )
        self.assertEqual(len(planted_sec), 2, planted_sec)
        self.assertEqual(len(planted_adv), 2, planted_adv)
        self.assertEqual(planted_sec[0]["claim"], SECURITY_FINDINGS[0]["claim"])
        self.assertEqual(planted_sec[1]["claim"], SECURITY_FINDINGS[1]["claim"])
        self.assertEqual(
            planted_adv[0]["claim"], ADVERSARIAL_FINDINGS[0]["claim"],
        )
        self.assertEqual(
            planted_adv[1]["claim"], ADVERSARIAL_FINDINGS[1]["claim"],
        )
        self.assertEqual(bool(plant.get("cited_all")), cited_all)
        self.assertEqual(bool(plant.get("cite_first_only")), cite_first_only)
        self.assertEqual(
            bool(plant.get("revoke_after_fix_tests")),
            revoke_after_fix_tests,
        )

    def patch_plant(self, **updates) -> dict:
        plant = super().patch_plant(**updates)
        if updates.get("dispositions"):
            self._plant_findings(cited_all=True)
        elif {
            "cited_all",
            "cite_first_only",
            "revoke_after_fix_tests",
        } & updates.keys():
            self._plant_findings(
                cited_all=bool(
                    updates.get("cited_all")
                    or updates.get("revoke_after_fix_tests")
                ),
                cite_first_only=bool(updates.get("cite_first_only")),
            )
        return plant

    def run_undisposed(self, **plant_kw):
        self.plant_undisposed_audit(**plant_kw)
        if not plant_kw.get("revoke_after_fix_tests"):
            return self.run_ladder()
        stop = threading.Event()

        def revoke_after_dispatch() -> None:
            while not stop.wait(0.001):
                try:
                    if "fix-tests" in self.dispatch_names():
                        self._plant_findings()
                        return
                except (json.JSONDecodeError, OSError):
                    continue

        watcher = threading.Thread(target=revoke_after_dispatch, daemon=True)
        watcher.start()
        try:
            return self.run_ladder()
        finally:
            stop.set()
            watcher.join(timeout=RUN_CAP)

    def findings_argvs(self) -> list[list[str]]:
        return [row["argv"] for row in self.world.tools.calls("findings")]

    def closed_envelope(self, name: str) -> dict:
        rec_id = self.record_id_for(name)
        path = Path(self.load_plant()["records"][rec_id]["path"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        envelope = (payload.get("result") or {}).get("envelope") or {}
        self.assertIsInstance(envelope, dict, payload)
        return envelope

    def assert_audit_findings_landed(self) -> None:
        names = self.dispatch_names()
        self.assertIn("security", names, names)
        self.assertIn("adversarial", names, names)
        sec = self.closed_envelope("security").get("findings") or []
        adv = self.closed_envelope("adversarial").get("findings") or []
        self.assertEqual(len(sec), 2, sec)
        self.assertEqual(len(adv), 2, adv)
        self.assertEqual(sec[0]["claim"], SECURITY_FINDINGS[0]["claim"], sec)
        self.assertEqual(sec[1]["claim"], SECURITY_FINDINGS[1]["claim"], sec)
        self.assertEqual(adv[0]["claim"], ADVERSARIAL_FINDINGS[0]["claim"], adv)
        self.assertEqual(adv[1]["claim"], ADVERSARIAL_FINDINGS[1]["claim"], adv)

    def envelope_keys(self, name: str) -> list[str]:
        rec_id = self.record_id_for(name)
        self.assertRegex(rec_id, RECORD_ID, rec_id)
        findings = self.closed_envelope(name).get("findings") or []
        keys = []
        for n, _row in enumerate(findings, start=1):
            key = f"{rec_id}#{n}"
            self.assertRegex(key, ANSWER_KEY, key)
            keys.append(key)
        return keys

    def all_audit_keys(self) -> list[str]:
        self.assert_audit_findings_landed()
        keys: list[str] = []
        for name in AUDIT_STAGES:
            keys.extend(self.envelope_keys(name))
        self.assertEqual(len(keys), 4, keys)
        return keys

    def later_audit_keys(self) -> list[str]:
        """Keys numbered 2 or higher on each audit envelope."""
        keys = []
        for name in AUDIT_STAGES:
            for key in self.envelope_keys(name):
                if key.rsplit("#", 1)[-1] != "1":
                    keys.append(key)
        self.assertEqual(len(keys), 2, keys)
        return keys

    def findings_verbs(self) -> list[str]:
        return [row[0] if row else "" for row in self.findings_argvs()]

    def findings_store_snapshot(self) -> dict[str, str]:
        snap: dict[str, str] = {}
        for root in (self.home, self.xdg):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                name = path.name
                if name == "findings.db" or name.startswith("findings.db"):
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    snap[str(path)] = digest
        return snap

    def dispositions_snapshot(self) -> str:
        return json.dumps(
            self.load_plant().get("dispositions") or {},
            sort_keys=True,
        )

    def assert_driver_did_not_write_dispositions(
        self,
        before_store: dict[str, str],
        before_disp: str,
    ) -> None:
        self.assertEqual(
            self.findings_store_snapshot(),
            before_store,
            "item 9: the driver never writes the findings store",
        )
        self.assertEqual(
            self.dispositions_snapshot(),
            before_disp,
            "item 9: the driver never writes disposition rows",
        )
        self.assertNotIn("disposition", self.findings_verbs())

    def assert_output_names_keys(self, proc, keys: list[str]) -> None:
        text = self.combined(proc)
        self.assertTrue(keys, "plant: expected undisposed keys")
        for key in keys:
            self.assertIn(
                key,
                text,
                f"item 9: the stop names undisposed key {key}; "
                f"got {text!r}",
            )

    def assert_no_fix_dispatch(self) -> None:
        names = self.dispatch_names()
        self.assertNotIn("fix-tests", names, names)
        self.assertNotIn("fix-code", names, names)

    def record_job(self, rec_id: str) -> str:
        info = (self.load_plant().get("records") or {}).get(rec_id) or {}
        job = info.get("job")
        path = info.get("path")
        if path and Path(path).is_file():
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            job = payload.get("job") or job
        return str(job or "")

    def assert_item1_shape(self, name: str) -> None:
        """Item 1: a review record on --follows, or a review out/ input."""
        argv = self.dispatch_argv_for(name)
        follows = self.flag_values(argv, "--follows")
        jobs = [self.record_job(rec_id) for rec_id in follows]
        if any(job in REVIEW_JOBS for job in jobs):
            return
        for value in self.flag_values(argv, "--input"):
            text = str(value).replace("\\", "/")
            if "/out/" not in text:
                continue
            for rec_id, info in (self.load_plant().get("records") or {}).items():
                if rec_id in text and info.get("job") in REVIEW_JOBS:
                    return
        self.fail(
            f"item 1: {name} is not a correction under the mechanical "
            f"shape; follows jobs={jobs} argv={argv!r}",
        )

    def assert_answers_every_key(self, name: str, keys: list[str]) -> None:
        argv = self.dispatch_argv_for(name)
        answers = self.flag_values(argv, "--answers")
        self.assertTrue(keys, "plant: expected finding keys")
        self.assertEqual(
            sorted(answers),
            sorted(keys),
            f"item 9: {name} names every finding it answers; "
            f"found {answers!r} in {argv!r}",
        )
        for key in answers:
            self.assertRegex(key, ANSWER_KEY, key)

    def author_tests_after_audits(self) -> list[str]:
        seen_audit = False
        later: list[str] = []
        for row in self.dispatch_calls():
            if row.get("name") in {"security", "adversarial"}:
                seen_audit = True
                continue
            if seen_audit and row.get("job") == "author-tests":
                later.append(row.get("name") or "")
        return later


class StopBeforeFixTests(_UndisposedFixture):
    """Item 9 — stop before fix-tests when any audit finding is open."""

    def test_declared_stop_at_fix_tests_with_record_none(self):
        proc = self.run_undisposed()
        self.assert_audit_findings_landed()
        self.assert_stopped(
            proc, stage="fix-tests", record="none", reason="undisposed",
        )

    def test_stop_names_every_undisposed_key_from_both_envelopes(self):
        proc = self.run_undisposed()
        keys = self.all_audit_keys()
        self.assert_output_names_keys(proc, keys)
        self.assert_stopped(
            proc, stage="fix-tests", record="none", reason="undisposed",
        )

    def test_no_fix_stage_is_dispatched(self):
        proc = self.run_undisposed()
        names = self.dispatch_names()
        self.assertIn("security", names, names)
        self.assertIn("adversarial", names, names)
        self.assert_no_fix_dispatch()
        self.assertEqual(self.author_tests_after_audits(), [], names)
        self.assert_stopped(
            proc, stage="fix-tests", record="none", reason="undisposed",
        )

    def test_driver_does_not_write_a_disposition(self):
        self.plant_undisposed_audit()
        before_store = self.findings_store_snapshot()
        before_disp = self.dispositions_snapshot()
        self.assertEqual(before_store, {})
        self.assertEqual(before_disp, "{}")
        proc = self.run_ladder()
        self.assert_stopped(
            proc, stage="fix-tests", record="none", reason="undisposed",
        )
        self.assert_driver_did_not_write_dispositions(before_store, before_disp)


class EveryFindingIsChecked(_UndisposedFixture):
    """Item 9 — every finding in both envelopes, including n>=2.

    Finding 1 of each envelope is cited; later findings are open. A
    driver that inspects only adversarial#1, or only n=1, launches.
    """

    def test_later_findings_stop_even_when_each_first_finding_is_cited(self):
        proc = self.run_undisposed(cite_first_only=True)
        self.assert_audit_findings_landed()
        self.assert_no_fix_dispatch()
        self.assert_stopped(
            proc, stage="fix-tests", record="none", reason="undisposed",
        )

    def test_stop_names_the_later_keys_from_security_and_adversarial(self):
        proc = self.run_undisposed(cite_first_only=True)
        self.assert_audit_findings_landed()
        later = self.later_audit_keys()
        self.assert_output_names_keys(proc, later)
        self.assert_stopped(
            proc, stage="fix-tests", record="none", reason="undisposed",
        )


class FreshCheckBeforeFixCode(_UndisposedFixture):
    """Item 9 — a fresh check before fix-code, not the fix-tests result.

    Every key holds until fix-tests is dispatched; the stub then
    reports open. A driver that caches the first check launches
    fix-code.
    """

    def test_fix_code_stops_when_cited_fails_after_fix_tests_launched(self):
        proc = self.run_undisposed(
            cited_all=True, revoke_after_fix_tests=True,
        )
        self.assert_audit_findings_landed()
        names = self.dispatch_names()
        self.assertIn("fix-tests", names, names)
        self.assertNotIn("fix-code", names, names)
        self.assert_stopped(
            proc, stage="fix-code", record="none", reason="undisposed",
        )

    def test_fix_code_stop_names_the_keys_now_undisposed(self):
        proc = self.run_undisposed(
            cited_all=True, revoke_after_fix_tests=True,
        )
        keys = self.all_audit_keys()
        self.assert_output_names_keys(proc, keys)
        self.assertIn("fix-tests", self.dispatch_names())
        self.assertNotIn("fix-code", self.dispatch_names())
        self.assert_stopped(
            proc, stage="fix-code", record="none", reason="undisposed",
        )


class ResumeRechecks(_UndisposedFixture):
    """Item 9 — resume re-checks; rows let the fix stages launch."""

    def test_resume_without_rows_stops_undisposed_again(self):
        first = self.run_undisposed()
        self.assert_stopped(
            first, stage="fix-tests", record="none", reason="undisposed",
        )
        ladder_id = self.ladder_record().get("id")
        self.assertTrue(ladder_id)
        before_store = self.findings_store_snapshot()
        before_disp = self.dispositions_snapshot()
        second = self.invoke(["resume", ladder_id])
        self.assert_stopped(
            second, stage="fix-tests", record="none", reason="undisposed",
        )
        self.assert_no_fix_dispatch()
        self.assert_driver_did_not_write_dispositions(before_store, before_disp)

    def test_resume_once_rows_exist_dispatches_both_fix_stages(self):
        first = self.run_undisposed()
        self.assert_stopped(
            first, stage="fix-tests", record="none", reason="undisposed",
        )
        keys = self.all_audit_keys()
        dispositions = {key: CITATION for key in keys}
        self.patch_plant(dispositions=dispositions)
        landed = self.load_plant().get("dispositions") or {}
        self.assertEqual(landed, dispositions, landed)
        for key in keys:
            probe = self.cited_probe(key)
            self.assertEqual(
                probe.returncode, 0,
                f"plant: cited must hold after the disposition: "
                f"{probe.stdout!r} {probe.stderr!r}",
            )
            self.assertIn(CITATION, probe.stdout)
        ladder_id = self.ladder_record().get("id")
        self.assertTrue(ladder_id)
        before_store = self.findings_store_snapshot()
        before_disp = self.dispositions_snapshot()
        second = self.invoke(["resume", ladder_id])
        self.assertEqual(second.returncode, 0, self.combined(second))
        names = self.dispatch_names()
        self.assertIn("fix-tests", names, names)
        self.assertIn("fix-code", names, names)
        self.assert_item1_shape("fix-tests")
        self.assert_item1_shape("fix-code")
        self.assert_answers_every_key("fix-tests", keys)
        self.assert_answers_every_key("fix-code", keys)
        self.assert_driver_did_not_write_dispositions(before_store, before_disp)


class FixStagesAreCorrections(_UndisposedFixture):
    """Item 9 / item 1 — when every key holds, both fix stages launch
    as corrections and name every finding they answer.

    One followed review record is enough for item 1; these tests do
    not require a fixed --follows count.
    """

    def test_fix_code_is_a_correction_under_item_1(self):
        proc = self.run_undisposed(cited_all=True)
        self.assertIn("fix-code", self.dispatch_names())
        self.assert_item1_shape("fix-code")
        self.assertEqual(proc.returncode, 0, self.combined(proc))

    def test_fix_tests_names_every_finding_key(self):
        proc = self.run_undisposed(cited_all=True)
        keys = self.all_audit_keys()
        self.assert_answers_every_key("fix-tests", keys)
        self.assert_item1_shape("fix-tests")
        self.assertEqual(proc.returncode, 0, self.combined(proc))

    def test_fix_code_names_every_finding_key(self):
        proc = self.run_undisposed(cited_all=True)
        keys = self.all_audit_keys()
        self.assert_answers_every_key("fix-code", keys)
        self.assertEqual(proc.returncode, 0, self.combined(proc))


if __name__ == "__main__":
    unittest.main()
