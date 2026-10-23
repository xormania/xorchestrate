"""Independent tests for the skills registry CLI (sdb-skills contract).

Contract sources (only): `.claude/skills/sdb-skills/SKILL.md` and the
author-tests brief. The tool is copied as unread bytes into a throwaway
tree and run with HOME pointed at a temp dir — never the live registry.

PIN_KIND_SET: the skill doc shows `--kind=K` but does not name the valid
set. Tests discover a valid kind from the contract's own promise that a
bad kind is refused naming that set.
PIN_REPORT_SHAPE: report is promised as per-skill use/fail/miss counts,
wished names ranked by wish count, and a zero-misses caveat line; the
exact table/line syntax is unnamed, so tests pin distinctive counts and
the caveat's presence/absence.
PIN_WISH_ACCUMULATION: ranking by wish count is untestable if a name can
only be wished once. The ranking test records how many wishes landed.
"""

from __future__ import annotations

import re
import shutil
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
SKILLS_PY = "skills.py"
AGENT = "Test Author <noreply@example.invalid>"
BARE_AGENT = "Test Author"

# A kind string the contract cannot have listed as valid.
BOGUS_KIND = "not-a-kind-xyzzy-9f3c1e2a"


class SkillsFixture(unittest.TestCase):
    """Temp repo + HOME; the live tree and live ~/.local/state stay put."""

    def setUp(self):
        self.world = World(prefix="skills-registry-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.repo = self.world.cwd
        self.scratch = self.world.tmp
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self.skills = self.repo / ".claude" / "skills"
        self.skills.mkdir(parents=True, exist_ok=True)

        tools = self.repo / "ops" / "conductor" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        self.tool = self._plant_subject(tools)
        self.world.plant_text(
            self.repo / "ops" / "lane.conf",
            "LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\n",
            recognisable="LANE_NAME",
        )

        # S5, 2026-09-16: the subject runs under this world's own HOME and
        # XDG roots, so the operator's live state is not reachable from
        # it. The snapshot of ~/.local/state/agentscient that stood here,
        # and the tear-down that compared it, are deleted rather than
        # exempted: they failed whenever another session wrote that
        # directory while the suite ran, which was never this tool's
        # write (LD-69's class). What replaces them is the construction
        # itself, stated here.
        env = self.world.env()
        self.assertEqual(Path(env["HOME"]), self.home)
        self.assertEqual(Path(env["XDG_STATE_HOME"]), self.world.xdg_state)
        self.assertNotEqual(self.home.resolve(), Path.home().resolve())

    def _plant_subject(self, tools: Path) -> Path:
        src = TOOLS / SKILLS_PY
        self.assertTrue(src.is_file(), f"missing tool at {src}")
        staged = self.world.stage(
            src, siblings=("instrument.py", "laneconf.py"),
        )
        for path in staged.parent.iterdir():
            dest = self.world.plant_bytes(tools / path.name, path.read_bytes())
            self.assertGreater(dest.stat().st_size, 0)
        return tools / SKILLS_PY

    def _run(self, *argv, agent=None):
        extra = {"PYTHONIOENCODING": "utf-8"}
        if agent is not None:
            extra["WF_AGENT"] = agent
        proc = run(
            [sys.executable, str(self.tool), *argv],
            cwd=str(self.repo),
            env=self.world.env(extra=extra),
            timeout=30,
        )
        out = proc.stdout + proc.stderr
        return proc, out

    def _write_skill(self, dirname, name=None, description="a skill"):
        folder = self.skills / dirname
        folder.mkdir(parents=True, exist_ok=True)
        if name is None:
            body = f"---\ndescription: {description}\n---\n\nbody\n"
        else:
            body = (
                f"---\nname: {name}\ndescription: {description}\n"
                f"---\n\nbody\n"
            )
        path = folder / "SKILL.md"
        self.world.plant_text(path, body, recognisable=description)
        return path

    def _kind_from_refusal(self, text):
        """PIN_KIND_SET: pull quoted/bare tokens out of a kind refusal."""
        quoted = re.findall(r"'([A-Za-z][A-Za-z0-9_-]*)'", text)
        doubled = re.findall(r'"([A-Za-z][A-Za-z0-9_-]*)"', text)
        tokens = [t for t in quoted + doubled if t != BOGUS_KIND]
        if not tokens:
            # argparse-style: choose from a, b, c  — or "one of: a, b"
            tail = re.search(
                r"(?:choose from|one of|valid(?:\s+\w+)*|expected)\s*:?\s*(.+)",
                text,
                re.IGNORECASE | re.DOTALL,
            )
            if tail:
                tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", tail.group(1))
                tokens = [t for t in tokens if t.lower() not in {
                    "choose", "from", "one", "of", "valid", "kind",
                    "kinds", "expected", "are", "is", "the", "set",
                } and t != BOGUS_KIND]
        return tokens

    def _a_valid_kind(self):
        proc, out = self._run(
            "add", "kind-probe", "desc", f"--kind={BOGUS_KIND}",
            agent=AGENT,
        )
        self.assertNotEqual(
            proc.exit, 0,
            "bad --kind must be refused so the valid set can be named:\n"
            + out,
        )
        kinds = self._kind_from_refusal(out)
        self.assertTrue(
            kinds,
            "kind refusal must name the valid set; got:\n" + out,
        )
        return kinds[0]


class TestSync(SkillsFixture):
    """sync derives rows from disk and never deletes."""

    def test_sync_derives_name_and_description_from_frontmatter(self):
        # Broken impl that ignores frontmatter and uses only the directory
        # name would list 'dir-alpha' and omit 'front-name'.
        self._write_skill(
            "dir-alpha", name="front-name", description="front-desc",
        )
        proc, out = self._run("sync")
        self.assertEqual(proc.exit, 0, out)
        _, listed = self._run("list")
        _, shown = self._run("show", "front-name")
        blob = listed + shown + out
        self.assertIn("front-name", blob)
        self.assertIn("front-desc", blob)

    def test_sync_falls_back_to_directory_name(self):
        # Broken impl that requires a frontmatter name would drop this row.
        self._write_skill("fallback-dir", name=None, description="no-name-here")
        proc, out = self._run("sync")
        self.assertEqual(proc.exit, 0, out)
        _, listed = self._run("list")
        blob = listed + out
        self.assertIn("fallback-dir", blob)

    def test_sync_marks_vanished_directory_missing_without_deleting(self):
        # Broken impl that DELETEs vanished rows would drop keep-me.
        self._write_skill("keep-me", name="keep-me", description="stay")
        proc, out = self._run("sync")
        self.assertEqual(proc.exit, 0, out)
        shutil.rmtree(self.skills / "keep-me")
        self.assertFalse((self.skills / "keep-me").exists(), "plant: vanished")
        proc2, out2 = self._run("sync")
        self.assertEqual(proc2.exit, 0, out2)
        _, listed = self._run("list")
        _, shown = self._run("show", "keep-me")
        blob = listed + shown
        self.assertIn("keep-me", blob)
        self.assertRegex(blob, r"missing")

    def test_sync_promotes_wished_row_to_active_when_directory_appears(self):
        # Broken impl that leaves status=wished after the directory appears.
        proc, out = self._run("wish", "soon-real", "please exist", agent=AGENT)
        self.assertEqual(proc.exit, 0, out)
        _, shown_before = self._run("show", "soon-real")
        self.assertRegex(shown_before, r"wished")
        self._write_skill("soon-real", name="soon-real", description="here now")
        proc2, out2 = self._run("sync")
        self.assertEqual(proc2.exit, 0, out2)
        _, shown = self._run("show", "soon-real")
        self.assertRegex(shown, r"active")
        self.assertNotRegex(shown, r"wished")

    def test_sync_refuses_when_skills_dir_absent(self):
        # Broken impl that treats a missing dir as an empty list (exit 0).
        shutil.rmtree(self.skills)
        self.assertFalse(self.skills.exists(), "plant: skills dir absent")
        proc, out = self._run("sync")
        self.assertNotEqual(proc.exit, 0, out)
        self.assertTrue(out.strip(), "refusal must say why")
        self.assertRegex(out, r"skill", re.IGNORECASE)


class TestAdd(SkillsFixture):
    def test_add_bad_kind_refused_naming_the_valid_set(self):
        # Broken impl that accepts any --kind, or refuses without naming
        # the valid set, would pass a silent/empty refusal.
        proc, out = self._run(
            "add", "fresh-kind", "desc", f"--kind={BOGUS_KIND}",
            "--status=draft", agent=AGENT,
        )
        self.assertNotEqual(proc.exit, 0, out)
        self.assertRegex(out, r"kind", re.IGNORECASE)
        kinds = self._kind_from_refusal(out)
        self.assertTrue(kinds, "refusal must name the valid set:\n" + out)
        self.assertNotIn(BOGUS_KIND, kinds)

    def test_add_duplicate_name_refused_toward_note_or_sync(self):
        # Broken impl that inserts a second row, or refuses without
        # pointing at note/sync, would pass.
        kind = self._a_valid_kind()
        first, out1 = self._run(
            "add", "dup-name", "first", f"--kind={kind}",
            "--status=draft", agent=AGENT,
        )
        self.assertEqual(first.exit, 0, out1)
        second, out2 = self._run(
            "add", "dup-name", "again", f"--kind={kind}",
            "--status=draft", agent=AGENT,
        )
        self.assertNotEqual(second.exit, 0, out2)
        self.assertRegex(out2, r"note|sync", re.IGNORECASE)


class TestRetire(SkillsFixture):
    def test_retire_refused_without_a_reason(self):
        # Broken impl that retires on `retire NAME` with no reason.
        self._write_skill("stay-put", name="stay-put", description="d")
        proc, out = self._run("sync")
        self.assertEqual(proc.exit, 0, out)
        refused, msg = self._run("retire", "stay-put", agent=AGENT)
        self.assertNotEqual(refused.exit, 0, msg)
        self.assertRegex(msg, r"(?i)reason")
        _, shown = self._run("show", "stay-put")
        self.assertIn("stay-put", shown)

    def test_retire_keeps_the_row_and_records_the_reason(self):
        # Broken impl that DELETEs the row, or records no reason.
        self._write_skill("old-one", name="old-one", description="d")
        proc, out = self._run("sync")
        self.assertEqual(proc.exit, 0, out)
        reason = "superseded-by-contract"
        done, msg = self._run("retire", "old-one", reason, agent=AGENT)
        self.assertEqual(done.exit, 0, msg)
        _, listed = self._run("list")
        _, shown = self._run("show", "old-one")
        blob = listed + shown + msg
        self.assertIn("old-one", blob)
        self.assertIn(reason, blob)


class TestEvents(SkillsFixture):
    def test_log_use_refused_without_wf_agent(self):
        self._write_skill("used", name="used", description="d")
        self.assertEqual(self._run("sync")[0].exit, 0)
        proc, out = self._run("log-use", "used", "invoked in session")
        self.assertNotEqual(proc.exit, 0, out)
        self.assertRegex(out, r"WF_AGENT|agent", re.IGNORECASE)

    def test_log_fail_refused_without_wf_agent(self):
        self._write_skill("fails", name="fails", description="d")
        self.assertEqual(self._run("sync")[0].exit, 0)
        proc, out = self._run("log-fail", "fails", "boom")
        self.assertNotEqual(proc.exit, 0, out)
        self.assertRegex(out, r"WF_AGENT|agent", re.IGNORECASE)

    def test_log_miss_refused_without_wf_agent(self):
        self._write_skill("missed", name="missed", description="d")
        self.assertEqual(self._run("sync")[0].exit, 0)
        proc, out = self._run("log-miss", "missed", "did it by hand")
        self.assertNotEqual(proc.exit, 0, out)
        self.assertRegex(out, r"WF_AGENT|agent", re.IGNORECASE)

    def test_wish_refused_without_wf_agent(self):
        proc, out = self._run("wish", "ghost", "please exist")
        self.assertNotEqual(proc.exit, 0, out)
        self.assertRegex(out, r"WF_AGENT|agent", re.IGNORECASE)

    def test_events_refused_for_bare_wf_agent_name(self):
        # Broken impl that accepts WF_AGENT='Name' without <address>.
        verbs = (
            ("log-use", ("used", "invoked")),
            ("log-fail", ("used", "boom")),
            ("log-miss", ("used", "by hand")),
            ("wish", ("ghost", "please")),
        )
        self.assertEqual(len(verbs), 4)
        self._write_skill("used", name="used", description="d")
        self.assertEqual(self._run("sync")[0].exit, 0)
        for verb, args in verbs:
            proc, out = self._run(verb, *args, agent=BARE_AGENT)
            self.assertNotEqual(proc.exit, 0, f"{verb}: {out}")
            self.assertTrue(out.strip(), f"{verb} refusal must say why")

    def test_wish_for_existing_name_refused_toward_log_miss(self):
        # Broken impl that wishes an on-disk skill instead of pointing at
        # log-miss.
        self._write_skill("already", name="already", description="d")
        self.assertEqual(self._run("sync")[0].exit, 0)
        proc, out = self._run(
            "wish", "already", "should be a miss", agent=AGENT,
        )
        self.assertNotEqual(proc.exit, 0, out)
        self.assertRegex(out, r"log-miss", re.IGNORECASE)

    def test_log_miss_for_unknown_name_refused_toward_wish_or_sync(self):
        # Broken impl that logs a miss against a name nobody has.
        proc, out = self._run(
            "log-miss", "no-such-skill", "did it by hand", agent=AGENT,
        )
        self.assertNotEqual(proc.exit, 0, out)
        self.assertRegex(out, r"wish|sync", re.IGNORECASE)

    def test_wish_creates_wished_row_carrying_its_detail(self):
        # Broken impl that logs the event without a wished row, or drops
        # the detail.
        detail = "need-a-foo-for-bar-9f3c1e2a"
        proc, out = self._run("wish", "foo-skill", detail, agent=AGENT)
        self.assertEqual(proc.exit, 0, out)
        _, listed = self._run("list")
        _, shown = self._run("show", "foo-skill")
        blob = listed + shown + out
        self.assertIn("foo-skill", blob)
        self.assertRegex(blob, r"wished")
        self.assertIn(detail, blob)


class TestReport(SkillsFixture):
    def test_report_per_skill_use_fail_miss_counts(self):
        # Broken impl that prints the name with the wrong tallies, or
        # only a combined total.
        self._write_skill("metered", name="metered", description="d")
        self.assertEqual(self._run("sync")[0].exit, 0)
        for _ in range(3):
            proc, out = self._run("log-use", "metered", "used", agent=AGENT)
            self.assertEqual(proc.exit, 0, out)
        for _ in range(5):
            proc, out = self._run("log-fail", "metered", "boom", agent=AGENT)
            self.assertEqual(proc.exit, 0, out)
        for _ in range(7):
            proc, out = self._run(
                "log-miss", "metered", "did it by hand", agent=AGENT,
            )
            self.assertEqual(proc.exit, 0, out)
        proc, report = self._run("report")
        self.assertEqual(proc.exit, 0, report)
        self.assertIn("metered", report)
        line = next(
            (ln for ln in report.splitlines() if "metered" in ln),
            report,
        )
        self.assertRegex(line, r"\b3\b")
        self.assertRegex(line, r"\b5\b")
        self.assertRegex(line, r"\b7\b")

    def test_report_ranks_wished_names_by_wish_count(self):
        # Broken impl that lists wished names unsorted, or omits them.
        low = "wish-low"
        high = "wish-high"
        n_low = 0
        n_high = 0
        proc, _out = self._run("wish", low, "need-low", agent=AGENT)
        if proc.exit == 0:
            n_low += 1
        for i in range(3):
            proc, _out = self._run(
                "wish", high, f"need-high-{i}", agent=AGENT,
            )
            if proc.exit == 0:
                n_high += 1
        self.assertGreaterEqual(n_low, 1, "first wish for a new name must land")
        proc, report = self._run("report")
        self.assertEqual(proc.exit, 0, report)
        self.assertIn(low, report)
        self.assertIn(high, report)
        if n_high > n_low:
            self.assertLess(
                report.find(high),
                report.find(low),
                "higher wish count must rank first:\n" + report,
            )

    def test_report_zero_misses_caveat_only_when_no_miss_logged(self):
        # Broken impl that always prints the caveat, or never does.
        self._write_skill("caveat", name="caveat", description="d")
        self.assertEqual(self._run("sync")[0].exit, 0)
        self.assertEqual(
            self._run("log-use", "caveat", "used", agent=AGENT)[0].exit,
            0,
        )
        proc, report = self._run("report")
        self.assertEqual(proc.exit, 0, report)
        # Skill doc: report's zero-misses line is a "declared zero".
        caveat = re.search(
            r"(?i).*(declared zero|zero\s+miss|no\s+miss(?:es)?).*",
            report,
        )
        self.assertIsNotNone(
            caveat,
            "zero-misses caveat must appear before any miss is logged:\n"
            + report,
        )
        caveat_text = caveat.group(0)
        miss, miss_out = self._run(
            "log-miss", "caveat", "did it by hand", agent=AGENT,
        )
        self.assertEqual(miss.exit, 0, miss_out)
        proc2, after = self._run("report")
        self.assertEqual(proc2.exit, 0, after)
        self.assertNotIn(
            caveat_text.strip(),
            after,
            "caveat line must vanish once a miss has been logged:\n" + after,
        )


class TestNegativeSpace(SkillsFixture):
    def test_empty_skills_dir_syncs_without_refusing(self):
        # Broken impl that treats empty the same as absent (nonzero).
        self.assertEqual(list(self.skills.iterdir()), [])
        proc, out = self._run("sync")
        self.assertEqual(proc.exit, 0, out)

    def test_malformed_frontmatter_does_not_drop_a_well_formed_sibling(self):
        # Broken impl that aborts the whole sync on one bad SKILL.md.
        self._write_skill("good", name="good", description="ok-desc")
        bad = self.skills / "bad" / "SKILL.md"
        planted = "---\nname: [unterminated\n---\n"
        self.world.plant_text(bad, planted, recognisable="[unterminated")
        self.assertNotEqual(
            bad.read_text(encoding="utf-8"),
            "---\nname: good\n---\n",
            "plant: malformed bytes must differ from a clean skill",
        )
        proc, out = self._run("sync")
        self.assertNotIn("Traceback (most recent call last)", out)
        _, listed = self._run("list")
        if proc.exit == 0:
            self.assertIn("good", listed + out)
        else:
            self.assertTrue(out.strip(), "refusal must say why")

    def test_empty_db_list_and_report_do_not_crash(self):
        # Broken impl that assumes a schema already exists and tracebacks.
        proc, out = self._run("list")
        self.assertNotIn("Traceback (most recent call last)", out)
        self.assertTrue(
            proc.exit == 0 or out.strip(),
            "empty db must list empty or refuse with a reason",
        )
        proc2, out2 = self._run("report")
        self.assertNotIn("Traceback (most recent call last)", out2)
        self.assertTrue(
            proc2.exit == 0 or out2.strip(),
            "empty db must report or refuse with a reason",
        )


if __name__ == "__main__":
    unittest.main()
