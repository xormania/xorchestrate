"""Independent tests for the permission doctor (permission-surface.md).

Contract sources (only): `ops/conductor/permission-surface.md`,
`.claude/skills/co-doc/SKILL.md`, and the author-tests brief. The tool
is copied as unread bytes into a throwaway tree and run with HOME
pointed at a temp dir.

PIN_CLEAN_SURFACE: exit 0 requires every probe to run with no findings.
The fixture plants the layers the contract names (settings objects,
ledgers, a wf.py that answers --help, copies of dispatch.conf /
dispatch.sh). Paths a probe examines that the contract does not name
are filled only if the doctor's own output names them.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import unittest
from pathlib import Path

from ops.testkit import World, make_executable, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
CO_DOC = "co_doc.py"

PROBE_IDS = [
    "P-SET-1",
    "P-PERM-1",
    "P-PERM-2",
    "P-HOOK-1",
    "P-HOOK-2",
    "P-LEV-1",
    "P-ID-1",
    "P-WF-1",
    "P-DB-1",
]

CLASSES = ("Rejection", "Denial", "Abort", "Finding")
FINDING_LINE = re.compile(
    r"^\[(?P<cls>Rejection|Denial|Abort|Finding)\] "
    r"(?P<probe>P-[A-Z]+-\d+): .+"
    r"(—|\u2013|--) *remedy: .+"
)

INERT_PATH = "/no/such/agentscient-inert-grant-9f3c1e2a"
AGENT = "Test Author <noreply@example.invalid>"


class DoctorFixture(unittest.TestCase):
    """Temp repo + HOME; live tree, live settings, live ledgers stay put."""

    def setUp(self):
        self.world = World(prefix="co-doc-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.repo = self.world.cwd
        self.scratch = self.world.tmp
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self.state = self.world.xdg_state / "agentscient"
        self.state.mkdir(parents=True, exist_ok=True)

        tools = self.repo / "ops" / "conductor" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        self.tool = self._plant_subject(tools)

        self.launch = self.world.tools.plant("launch", stdout="launch\n", exit=0)
        self._plant_program("gate", tools / "gate.sh")
        self.world.plant_text(
            tools / "dispatch.conf",
            f"LAUNCHER_PIN='{self.launch}'\n",
            recognisable="LAUNCHER_PIN",
        )
        self.assertTrue(self.launch.is_file() and (tools / "gate.sh").is_file())

        wf_dir = self.repo / "ops" / "devlane" / "workflow"
        self._plant_program("wf", wf_dir / "wf.py", stdout="usage: wf\n")

        self.skills_db = self.state / "skills.db"
        self.handwork_db = self.state / "handwork.db"
        for db in (self.skills_db, self.handwork_db):
            sqlite3.connect(str(db)).close()
            self.assertTrue(db.is_file(), f"plant: {db.name} must exist")

        claude = self.repo / ".claude"
        claude.mkdir(parents=True, exist_ok=True)
        self.repo_settings = claude / "settings.json"
        self.repo_local = claude / "settings.local.json"
        self.user_settings = self.home / ".claude" / "settings.json"
        empty = "{}\n"
        for path in (self.repo_settings, self.repo_local, self.user_settings):
            self.world.plant_text(path, empty, recognisable="{")

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
        src = TOOLS / CO_DOC
        self.assertTrue(src.is_file(), f"missing tool at {src}")
        siblings = ["instrument.py", "laneconf.py"]
        if (TOOLS / "dispatch.sh").is_file():
            siblings.append("dispatch.sh")
        staged = self.world.stage(src, siblings=tuple(siblings))
        for path in staged.parent.iterdir():
            dest = self.world.plant_bytes(tools / path.name, path.read_bytes())
            if path.suffix == ".sh":
                make_executable(dest)
        return tools / CO_DOC

    def _plant_program(self, name: str, dest: Path, *, stdout: str = "",
                       exit: int = 0) -> Path:
        fake = self.world.tools.plant(name, stdout=stdout, exit=exit)
        planted = self.world.plant_bytes(dest, fake.read_bytes())
        make_executable(planted)
        return planted

    def _run(self, *argv, agent=AGENT):
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

    def _write_settings(self, path: Path, payload) -> bytes:
        before = path.read_bytes() if path.exists() else b""
        text = json.dumps(payload, indent=2) + "\n"
        self.world.plant_text(path, text)
        after = path.read_bytes()
        self.assertNotEqual(after, before, f"plant did not change {path.name}")
        loaded = json.loads(after.decode("utf-8"))
        self.assertIsInstance(loaded, dict, "planted settings must still parse")
        return after


class TestArguments(DoctorFixture):
    def test_an_unknown_argument_refuses_arguments_on_the_wire(self):
        """instrument-conventions.md §The refusal wire: one stderr line,
        exit 2, stdout empty; the class token leads."""
        proc, _ = self._run("--bogus")
        wire = proc.refusal(exit=2)
        self.assertEqual(wire.cls, "arguments")
        self.assertTrue(wire.expected)
        self.assertTrue(wire.found)
        self.assertTrue(wire.needed)


class TestListProbes(DoctorFixture):
    def test_list_probes_prints_exactly_the_nine_contract_ids(self):
        # Broken impl that drifts from the table (missing, extra, renamed).
        self.assertEqual(len(PROBE_IDS), 9)
        proc, out = self._run("--list-probes")
        self.assertEqual(proc.exit, 0, out)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        self.assertEqual(lines, PROBE_IDS)


class TestExitCodes(DoctorFixture):
    def test_exit_0_on_clean_surface(self):
        # Broken impl that exits 1 on notes, or 0 while a probe is INVALID.
        proc, out = self._run()
        self.assertEqual(proc.exit, 0, out)

    def test_exit_1_when_findings_present(self):
        # Broken impl that still exits 0 after a P-HOOK-2 finding.
        self._plant_dot_star_matcher()
        proc, out = self._run()
        self.assertEqual(proc.exit, 1, out)
        self.assertIn("P-HOOK-2", out)

    def test_exit_2_when_any_probe_is_invalid(self):
        # Broken impl that reports unparseable settings as a finding (1)
        # rather than INVALID (2).
        planted = "{not-json"
        self.world.plant_text(self.repo_settings, planted)
        self.assertNotEqual(
            self.repo_settings.read_text(encoding="utf-8").strip(),
            "{}",
            "plant: unparseable settings must differ from the clean object",
        )
        proc, out = self._run()
        self.assertEqual(proc.exit, 2, out)
        self.assertIn("P-SET-1", out)
        self.assertRegex(out, r"INVALID")

    def _plant_dot_star_matcher(self):
        hook_cmd = self._plant_program("hook", self.scratch / "hook.sh")
        payload = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": ".*",
                        "hooks": [
                            {"type": "command", "command": str(hook_cmd)},
                        ],
                    }
                ]
            }
        }
        self._write_settings(self.repo_settings, payload)
        loaded = json.loads(self.repo_settings.read_text(encoding="utf-8"))
        self.assertEqual(
            loaded["hooks"]["PreToolUse"][0]["matcher"],
            ".*",
            "plant: matcher must still be .*, fixture recognizable",
        )


class TestPSet1(DoctorFixture):
    def test_absent_settings_file_is_reported_not_silent(self):
        # Broken impl that treats an absent file as clean-by-silence.
        self.user_settings.unlink()
        self.assertFalse(self.user_settings.exists(), "plant: user settings gone")
        _proc, out = self._run()
        self.assertTrue(out.strip(), "absent settings must produce a line")
        self.assertRegex(out, r"settings\.json")
        self.assertRegex(out, r"(?i)absent")

    def test_unparseable_settings_makes_probe_invalid(self):
        planted = "this is not json {"
        self.world.plant_text(self.repo_local, planted)
        self.assertEqual(
            self.repo_local.read_text(encoding="utf-8"),
            planted,
            "plant: unparseable local settings landed",
        )
        proc, out = self._run()
        self.assertEqual(proc.exit, 2, out)
        self.assertIn("P-SET-1", out)
        self.assertRegex(out, r"INVALID")


class TestPPerm1(DoctorFixture):
    def test_inert_grant_finding_names_the_unresolving_path(self):
        # Broken impl that skips allow rules whose targets do not resolve,
        # or that reports a finding without naming the path.
        clean = {"permissions": {"allow": []}}
        self.world.plant_text(
            self.repo_settings,
            json.dumps(clean, indent=2) + "\n",
        )
        before = self.repo_settings.read_bytes()
        planted = {
            "permissions": {
                "allow": [f"Read({INERT_PATH})"],
            }
        }
        after = self._write_settings(self.repo_settings, planted)
        self.assertNotEqual(after, before, "plant: settings must differ from clean")
        self.assertIn(INERT_PATH.encode("utf-8"), after)
        loaded = json.loads(after.decode("utf-8"))
        self.assertEqual(loaded["permissions"]["allow"], [f"Read({INERT_PATH})"])
        proc, out = self._run()
        self.assertEqual(proc.exit, 1, out)
        self.assertIn("P-PERM-1", out)
        self.assertIn(INERT_PATH, out)
        self.assertRegex(out, r"inert", re.IGNORECASE)


class TestPHook2(DoctorFixture):
    def test_hook_matcher_dot_star_yields_a_finding(self):
        # Broken impl that accepts matcher '.*' as "names the tools".
        hook_cmd = self._plant_program("hook", self.scratch / "hook.sh")
        self.world.plant_text(self.repo_settings, "{}\n", recognisable="{")
        before = self.repo_settings.read_bytes()
        payload = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": ".*",
                        "hooks": [
                            {"type": "command", "command": str(hook_cmd)},
                        ],
                    }
                ]
            }
        }
        after = self._write_settings(self.repo_settings, payload)
        self.assertNotEqual(after, before)
        loaded = json.loads(self.repo_settings.read_text(encoding="utf-8"))
        self.assertEqual(loaded["hooks"]["PreToolUse"][0]["matcher"], ".*")
        proc, out = self._run()
        self.assertEqual(proc.exit, 1, out)
        self.assertIn("P-HOOK-2", out)


class TestReadOnly(DoctorFixture):
    def test_doctor_creates_no_files_absent_ledger_stays_absent(self):
        # Broken impl that opens ledgers with write/create and mints a db.
        self.skills_db.unlink()
        self.handwork_db.unlink()
        self.assertFalse(self.skills_db.exists(), "plant: skills.db absent")
        self.assertFalse(self.handwork_db.exists(), "plant: handwork.db absent")
        watch = self.world.watch(self.home, self.repo, self.state)
        proc, _out = self._run()
        self.assertFalse(
            self.skills_db.exists(),
            "absent skills.db must still be absent after the doctor runs",
        )
        self.assertFalse(
            self.handwork_db.exists(),
            "absent handwork.db must still be absent after the doctor runs",
        )
        watch.assert_unchanged(self)
        # Read-only is the pin; INVALID (2) is acceptable when the ledger
        # cannot be opened. Creating a file is not.
        self.assertIn(proc.exit, (0, 1, 2))


class TestFindingShape(DoctorFixture):
    def test_finding_lines_carry_class_probe_id_and_remedy(self):
        # Broken impl that prints bare prose, omits the class, or drops
        # the remedy fragment.
        hook_cmd = self._plant_program("hook", self.scratch / "hook.sh")
        self._write_settings(
            self.repo_settings,
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": ".*",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": str(hook_cmd),
                                }
                            ],
                        }
                    ]
                }
            },
        )
        proc, out = self._run()
        self.assertEqual(proc.exit, 1, out)
        classified = [
            ln for ln in out.splitlines() if ln.startswith("[")
        ]
        self.assertTrue(classified, "expected classified finding lines:\n" + out)
        findings = []
        for ln in classified:
            if "INVALID" in ln:
                continue
            match = FINDING_LINE.search(ln)
            self.assertIsNotNone(
                match,
                "finding line must carry class, probe id, and remedy:\n"
                + ln,
            )
            self.assertIn(match.group("cls"), CLASSES)
            self.assertRegex(match.group("probe"), r"^P-[A-Z]+-\d+$")
            self.assertRegex(ln, r"remedy:", re.IGNORECASE)
            findings.append(ln)
        self.assertTrue(findings, "P-HOOK-2 must produce a finding line:\n" + out)
        self.assertTrue(any("P-HOOK-2" in ln for ln in findings))


class TestPLev1(DoctorFixture):
    """P-LEV-1: an unset LAUNCHER_PIN examines the sibling launcher."""

    def _conf(self) -> Path:
        return self.repo / "ops" / "conductor" / "tools" / "dispatch.conf"

    def _sibling_launcher(self) -> Path:
        return self.repo / "ops" / "devlane" / "dispatch" / "launch.py"

    def _plant_conf_without_launcher_pin(self) -> None:
        conf = self._conf()
        before = conf.read_text(encoding="utf-8")
        self.assertIn("LAUNCHER_PIN", before, "plant: default fixture pins")
        body = "# pin unset; sibling default applies\n"
        self.world.plant_text(conf, body)
        landed = conf.read_text(encoding="utf-8")
        self.assertEqual(landed, body, "plant: conf without pin did not land")
        self.assertNotIn("LAUNCHER_PIN=", landed)

    def _plant_live_sibling_launcher(self) -> Path:
        path = self._sibling_launcher()
        planted = self.world.plant_bytes(path, self.launch.read_bytes())
        make_executable(planted)
        self.assertEqual(planted.read_bytes(), self.launch.read_bytes())
        self.assertTrue(os.access(path, os.X_OK), f"sibling not executable: {path}")
        return path

    def test_unset_pin_with_a_live_sibling_launcher_is_no_finding(self):
        self._plant_conf_without_launcher_pin()
        sibling = self._plant_live_sibling_launcher()
        self.assertTrue(sibling.is_file())
        proc, out = self._run()
        classified = [
            ln for ln in out.splitlines()
            if ln.startswith("[") and "P-LEV-1" in ln and "INVALID" not in ln
        ]
        self.assertEqual(classified, [], "P-LEV-1 must not be a finding:\n" + out)
        self.assertEqual(proc.exit, 0, out)
        self.assertIn("P-LEV-1", out)
        self.assertRegex(out, r"(?i)live")
        self.assertIn("sibling default", out)
        self.assertIn(str(sibling), out)

    def test_unset_pin_with_an_absent_sibling_is_inert(self):
        self._plant_conf_without_launcher_pin()
        sibling = self._sibling_launcher()
        self.assertFalse(sibling.exists(), "plant: sibling launcher must be absent")
        proc, out = self._run()
        self.assertIn("sibling default", out)
        self.assertIn("P-LEV-1", out)
        self.assertIn(str(sibling), out)
        self.assertRegex(out, r"inert", re.IGNORECASE)
        self.assertEqual(proc.exit, 1, out)


if __name__ == "__main__":
    unittest.main()


class TestSharedNativeProfile(DoctorFixture):
    """A-218 same dev-lane WALL producer; root independently verifies."""
    def test_unknown_or_foreign_profile_is_invalid_and_never_activated(self):
        for data in ({"schema": "unknown"}, {"schema": "shared-hook-profile/1", "harness": "codex", "source": str(self.scratch / "foreign")}):
            path = self.world.plant_text("native-profile.json", json.dumps(data))
            before = path.read_bytes()
            proc = run([sys.executable, str(self.tool)], cwd=str(self.repo),
                       env=self.world.env(extra={"WF_AGENT": AGENT, "AGENTSCIENT_HOOK_PROFILE": str(path)}), timeout=30)
            self.assertEqual(proc.exit, 2, proc.stdout + proc.stderr)
            self.assertIn("P-HOOK-1", proc.stdout)
            self.assertIn("missing, malformed or changed native profile", proc.stdout)
            self.assertEqual(path.read_bytes(), before)
