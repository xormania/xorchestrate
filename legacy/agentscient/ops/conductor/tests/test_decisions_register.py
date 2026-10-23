"""Decisions-register acceptance tests, written from the contract.

Contract: `ops/conductor/decisions-register.md`. The tool
`ops/conductor/tools/decisions.py` is the subject and is not read here.
Tests invoke it as a subprocess with HOME pointed at a temp dir.

The live store (`~/.local/state/agentscient/decisions.db` at import-time
HOME) must stay untouched — asserted in tearDown.

The implementation lands in parallel. A missing tool file is one red
(`test_the_tool_file_is_present`). Every other case stages a
copy-tool-bytes stand-in so a missing file is not every test's failure:
when the tool exists its bytes are copied and run; when it does not, a
no-op stub (exit 0, no output) is planted so each assertion goes red on
the contract promise rather than Python's missing-file exit 2.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "decisions.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")
# Captured at import, before any test mutates a subprocess HOME.
LIVE_STORE = Path.home() / ".local" / "state" / "agentscient" / "decisions.db"
RUN_CAP = 60

VENDOR_ANTHROPIC = "Claude Fable 5 <noreply@anthropic.com>"
VENDOR_OPENAI = "GPT-5 Codex <noreply@openai.com>"
VENDOR_XAI = "Grok 4.6 <noreply@x.ai>"
VENDORS = (VENDOR_ANTHROPIC, VENDOR_OPENAI, VENDOR_XAI)
OWNER = "xormania <owner@agentscient.example>"
# PIN_EVIDENCE_PRINCIPAL: decided_by is the first `.+ <addr>` identity
# appearing in --evidence, never WF_AGENT.
PRINCIPAL = "xormania <127287135+xormania@users.noreply.github.com>"
EVIDENCE = f"{PRINCIPAL}: register should be SQLite python script"

DID = "D-WO-1"
SUCCESSOR = "D-WO-2"
LAW = "D-LAW-3"
OPS = "D-OPS-1"
MISSING = "D-WO-99"
QUESTION = "Should this decision stand as recorded"
HOME_PATH = "ops/conductor/decisions-register.md#the-row"

# World's absent stand-in (World.stage): exit 0, no output.
ABSENT_STUB = b"import sys\nsys.exit(0)\n"


class _RegisterFixture(unittest.TestCase):
    """Isolated HOME, staged tool copy, live store frozen."""

    def setUp(self):
        self.world = World(prefix="decisions-register-")
        self._live = self.world.watch(LIVE_STORE)
        self.home = self.world.home
        self.scratch = self.world.path("scratch")
        self.scratch.mkdir()
        self.store = self.home / ".local" / "state" / "agentscient" / "decisions.db"
        self.assertFalse(
            self.store.exists(),
            "plant: isolated store must start absent",
        )
        self._stage_tool_bytes()

    def tearDown(self):
        try:
            self._live.assert_unchanged(self, f"live store mutated: {LIVE_STORE}")
        finally:
            self.world.close()

    def _stage_tool_bytes(self) -> None:
        self.staged = self.world.stage(
            TOOL,
            "decisions.py",
            siblings=SIBLINGS if TOOL.is_file() else (),
        )
        if TOOL.is_file():
            data = TOOL.read_bytes()
            landed = self.staged.read_bytes()
            self.assertEqual(landed, data, "copy-tool-bytes did not land intact")
            self.assertEqual(len(landed), len(data))
            self.assertNotEqual(landed, ABSENT_STUB)
        else:
            landed = self.staged.read_bytes()
            self.assertEqual(landed, ABSENT_STUB, "stub plant did not land intact")
            self.assertEqual(len(landed), len(ABSENT_STUB))
        self.assertTrue(self.staged.is_file())
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under HOME is the one asserted.
        self.conf = self.world.plant_text(
            "lane.conf",
            "LANE_NAME='agentscient'\n",
        )

    def env(
        self,
        agent: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        payload = {
            "LANE_CONF": str(self.conf),
            "XDG_STATE_HOME": str(self.home / ".local" / "state"),
        }
        if agent is not None:
            payload["WF_AGENT"] = agent
        if extra:
            payload.update(extra)
        return self.world.env(extra=payload)

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = None,
        extra_env: dict[str, str] | None = None,
    ):
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=REPO,
            env=self.env(agent=agent, extra=extra_env),
            timeout=RUN_CAP,
        )

    def add(
        self,
        did: str,
        question: str = QUESTION,
        home: str = HOME_PATH,
        *,
        agent: str = OWNER,
        extra: list[str] | None = None,
    ):
        argv = ["add", did, question, "--home", home]
        if extra:
            argv.extend(extra)
        return self.invoke(argv, agent=agent)

    def assert_refusal(self, proc, class_name: str) -> None:
        err = proc.stderr or ""
        compact = err.lower().replace("_", "-")
        token = class_name.lower().replace("_", "-")
        spaced = token.replace("-", " ")
        self.assertTrue(
            token in compact or spaced in err.lower(),
            f"refusal class {class_name!r} not named in stderr: {err!r}",
        )
        self.assertEqual(proc.exit, 2, err)
        self.assertTrue(err.strip(), "refusal was silent")
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        lower = err.lower()
        self.assertIn("expected", lower)
        self.assertIn("found", lower)
        self.assertIn("needed", lower)
        self.assertEqual(
            (proc.stdout or "").strip(),
            "",
            "refusal text belongs on stderr, not stdout",
        )

    def field(self, text: str, name: str):
        """Read a named field from show output.

        PIN_SHOW_FIELDS: show prints a full row with named fields so
        decided_by and recorded_by are distinguishable. JSON object,
        JSON line, or `field: value` / `field=value` are accepted.
        """
        stripped = text.strip()
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
            for key in ("row", "decision"):
                inner = obj.get(key)
                if isinstance(inner, dict) and name in inner:
                    return inner[name]
        for line in text.splitlines():
            raw = line.strip()
            if raw.startswith("{"):
                try:
                    item = json.loads(raw)
                except json.JSONDecodeError:
                    item = None
                if isinstance(item, dict) and name in item:
                    return item[name]
        pat = re.compile(
            rf"^{re.escape(name)}\s*[:=]\s*(.+)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        match = pat.search(text)
        if match:
            return match.group(1).strip().strip('"').strip("'")
        return None

    def parse_export(self, stdout: str) -> list:
        text = stdout.strip()
        self.assertTrue(text, "export wrote nothing")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            rows = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    self.fail(f"export is not JSON: {exc}; got {stdout!r}")
            data = rows
        if isinstance(data, dict):
            for key in ("rows", "decisions", "items"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                if "id" in data:
                    data = [data]
                else:
                    self.fail(f"export JSON is not a row list: {data!r}")
        self.assertIsInstance(data, list, data)
        return data

    def list_lines(self, proc) -> list[str]:
        return [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]


class ToolPresence(_RegisterFixture):
    """Missing tool file counts once."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class AddListShow(_RegisterFixture):
    """1. add → list → show round-trip; standing starts proposed."""

    def test_add_list_show_round_trip_starts_proposed(self):
        added = self.add(DID, agent=OWNER)
        self.assertEqual(added.exit, 0, added.stderr)
        listed = self.invoke(["list"], agent=OWNER)
        self.assertEqual(listed.exit, 0, listed.stderr)
        lines = self.list_lines(listed)
        self.assertEqual(len(lines), 1, listed.stdout)
        line = lines[0]
        self.assertIn(DID, line)
        self.assertIn("proposed", line)
        self.assertIn(QUESTION, line)
        shown = self.invoke(["show", DID], agent=OWNER)
        self.assertEqual(shown.exit, 0, shown.stderr)
        self.assertIn(DID, shown.stdout)
        self.assertIn(QUESTION, shown.stdout)
        self.assertIn(HOME_PATH, shown.stdout)
        standing = self.field(shown.stdout, "standing")
        if standing is not None:
            self.assertEqual(standing, "proposed")
        else:
            self.assertIn("proposed", shown.stdout)
        self.assertTrue(
            self.store.is_file(),
            f"write must create {self.store} under isolated HOME",
        )


class Identity(_RegisterFixture):
    """2. Writing verbs need WF_AGENT; reads need none."""

    def test_writing_verb_without_wf_agent_refuses_exit_2(self):
        proc = self.add(DID, agent=None)
        self.assert_refusal(proc, "identity")
        self.assertEqual(proc.exit, 2, proc.stderr)

    def test_malformed_wf_agent_is_identity_refusal(self):
        proc = self.add(DID, agent="JustAName")
        self.assert_refusal(proc, "identity")

    def test_reads_need_no_identity(self):
        self.add(DID, agent=OWNER)
        verbs = (
            ["list"],
            ["show", DID],
            ["export"],
            ["report"],
        )
        for argv in verbs:
            with self.subTest(verb=argv[0]):
                proc = self.invoke(argv, agent=None)
                err = (proc.stderr or "").lower()
                self.assertNotIn("identity", err)
                self.assertEqual(proc.exit, 0, proc.stderr)
                self.assertTrue(
                    (proc.stdout or "").strip(),
                    f"{argv[0]} produced no result",
                )
                if argv[0] in {"list", "show"}:
                    self.assertIn(DID, proc.stdout)


class SelfConferred(_RegisterFixture):
    """3-4. Standing is not self-conferred."""

    def test_vendor_ratify_without_evidence_is_self_conferred(self):
        for i, agent in enumerate(VENDORS, start=1):
            did = f"D-WO-{i}"
            with self.subTest(agent=agent, did=did):
                self.add(did, agent=agent)
                proc = self.invoke(["ratify", did], agent=agent)
                self.assert_refusal(proc, "self-conferred")

    def test_vendor_reject_without_evidence_is_self_conferred(self):
        self.add(DID, agent=VENDOR_XAI)
        proc = self.invoke(["reject", DID], agent=VENDOR_XAI)
        self.assert_refusal(proc, "self-conferred")

    def test_vendor_ratify_with_evidence_takes_decided_by_from_evidence(self):
        self.add(DID, agent=VENDOR_XAI)
        proc = self.invoke(
            ["ratify", DID, "--evidence", EVIDENCE],
            agent=VENDOR_XAI,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        shown = self.invoke(["show", DID], agent=None)
        decided = self.field(shown.stdout, "decided_by")
        self.assertEqual(
            decided,
            PRINCIPAL,
            f"decided_by must come from evidence, not WF_AGENT; got {shown.stdout!r}",
        )
        self.assertNotEqual(decided, VENDOR_XAI)
        recorded = self.field(shown.stdout, "recorded_by")
        if recorded is not None:
            self.assertEqual(recorded, VENDOR_XAI)
        standing = self.field(shown.stdout, "standing")
        if standing is not None:
            self.assertEqual(standing, "ratified")
        else:
            self.assertIn("ratified", shown.stdout)

    def test_non_vendor_ratify_sets_decided_by_to_that_identity(self):
        self.add(DID, agent=OWNER)
        proc = self.invoke(["ratify", DID], agent=OWNER)
        self.assertEqual(proc.exit, 0, proc.stderr)
        shown = self.invoke(["show", DID], agent=None)
        decided = self.field(shown.stdout, "decided_by")
        self.assertEqual(
            decided,
            OWNER,
            f"decided_by must be the non-vendor identity; got {shown.stdout!r}",
        )
        standing = self.field(shown.stdout, "standing")
        if standing is not None:
            self.assertEqual(standing, "ratified")
        else:
            self.assertIn("ratified", shown.stdout)


class Transitions(_RegisterFixture):
    """5. Illegal transitions refuse naming from → to."""

    def test_ratify_of_a_ratified_row_names_the_transition(self):
        self.add(DID, agent=OWNER)
        first = self.invoke(["ratify", DID], agent=OWNER)
        self.assertEqual(first.exit, 0, first.stderr)
        again = self.invoke(["ratify", DID], agent=OWNER)
        err = again.stderr or ""
        self.assertIn("ratified", err.lower())
        self.assertTrue(
            "→" in err or "->" in err,
            f"transition must name from → to; got {err!r}",
        )
        self.assert_refusal(again, "transition")

    def test_supersede_of_a_proposed_row_names_the_transition(self):
        self.add(DID, agent=OWNER)
        self.add(SUCCESSOR, agent=OWNER)
        proc = self.invoke(
            [
                "supersede",
                DID,
                "--by",
                SUCCESSOR,
                "--evidence",
                EVIDENCE,
            ],
            agent=OWNER,
        )
        err = proc.stderr or ""
        self.assertIn("proposed", err.lower())
        self.assertIn("superseded", err.lower())
        self.assertTrue(
            "→" in err or "->" in err,
            f"transition must name from → to; got {err!r}",
        )
        self.assert_refusal(proc, "transition")


class Supersede(_RegisterFixture):
    """6. Both directions; successor must exist; --by itself refuses."""

    def test_supersede_writes_both_directions(self):
        self.add(DID, agent=OWNER)
        self.add(SUCCESSOR, agent=OWNER)
        self.invoke(["ratify", DID], agent=OWNER)
        proc = self.invoke(
            [
                "supersede",
                DID,
                "--by",
                SUCCESSOR,
                "--evidence",
                EVIDENCE,
            ],
            agent=OWNER,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        old = self.invoke(["show", DID], agent=None)
        new = self.invoke(["show", SUCCESSOR], agent=None)
        old_by = self.field(old.stdout, "superseded_by")
        new_of = self.field(new.stdout, "supersedes")
        if old_by is not None:
            self.assertEqual(old_by, SUCCESSOR)
        else:
            self.assertIn(SUCCESSOR, old.stdout)
        if new_of is not None:
            self.assertEqual(new_of, DID)
        else:
            self.assertIn(DID, new.stdout)
        standing = self.field(old.stdout, "standing")
        if standing is not None:
            self.assertEqual(standing, "superseded")
        else:
            self.assertIn("superseded", old.stdout)

    def test_supersede_by_itself_refuses(self):
        self.add(DID, agent=OWNER)
        self.invoke(["ratify", DID], agent=OWNER)
        proc = self.invoke(
            ["supersede", DID, "--by", DID, "--evidence", EVIDENCE],
            agent=OWNER,
        )
        self.assertIn(DID, proc.stderr or "")
        self.assert_refusal(proc, "successor")

    def test_supersede_by_a_missing_id_refuses(self):
        self.add(DID, agent=OWNER)
        self.invoke(["ratify", DID], agent=OWNER)
        proc = self.invoke(
            ["supersede", DID, "--by", MISSING, "--evidence", EVIDENCE],
            agent=OWNER,
        )
        self.assertIn(MISSING, proc.stderr or "")
        self.assert_refusal(proc, "successor")


class IntakeRefusals(_RegisterFixture):
    """7. question-short, duplicate, id-shape."""

    def test_question_under_four_words_refuses(self):
        proc = self.add(DID, question="only three words", agent=OWNER)
        self.assert_refusal(proc, "question-short")

    def test_four_word_question_is_accepted(self):
        question = "Will this four word"
        proc = self.add(DID, question=question, agent=OWNER)
        self.assertEqual(proc.exit, 0, proc.stderr)
        listed = self.invoke(["list"], agent=OWNER)
        self.assertIn(DID, listed.stdout)
        self.assertIn(question, listed.stdout)

    def test_duplicate_id_refuses(self):
        first = self.add(DID, agent=OWNER)
        self.assertEqual(first.exit, 0, first.stderr)
        second = self.add(DID, question="A different four word question", agent=OWNER)
        self.assert_refusal(second, "duplicate")
        self.assertIn(DID, second.stderr or "")

    def test_bad_id_shape_refuses(self):
        for bad in ("not-an-id", "D-WO", "d-WO-1", "D-wo-1", "WO-1"):
            with self.subTest(bad=bad):
                proc = self.add(bad, agent=OWNER)
                self.assertIn(bad, proc.stderr or "")
                self.assert_refusal(proc, "id-shape")


class Unknown(_RegisterFixture):
    """unknown: any verb on an id not in the store."""

    def test_show_of_an_unknown_id_refuses(self):
        proc = self.invoke(["show", MISSING], agent=None)
        self.assert_refusal(proc, "unknown")
        self.assertIn(MISSING, proc.stderr or "")


class Export(_RegisterFixture):
    """8. export is byte-deterministic; ids sorted; keys sorted."""

    def test_export_is_byte_deterministic_and_ids_sorted(self):
        self.add(DID, agent=OWNER)
        self.add(LAW, agent=OWNER)
        self.add(OPS, agent=OWNER)
        first = self.invoke(["export"], agent=None)
        second = self.invoke(["export"], agent=None)
        self.assertEqual(first.exit, 0, first.stderr)
        rows = self.parse_export(first.stdout)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            first.stdout.encode("utf-8"),
            second.stdout.encode("utf-8"),
        )
        self.assertGreaterEqual(len(rows), 3, rows)
        ids = [row["id"] for row in rows]
        self.assertEqual(ids, sorted(ids), ids)
        self.assertEqual(set(ids), {DID, LAW, OPS})
        for row in rows:
            self.assertEqual(list(row.keys()), sorted(row.keys()), row)


class AbsentStore(_RegisterFixture):
    """Codex P1 on PR #99: an unperformed read is not an empty register."""

    def test_aggregate_reads_refuse_when_store_absent(self):
        # Broken impl that answers zero rows from a store that is not there.
        for argv in (["list"], ["export"], ["report"]):
            with self.subTest(verb=argv[0]):
                proc = self.invoke(argv, agent=None)
                self.assert_refusal(proc, "store")

    def test_show_on_absent_store_still_refuses_unknown(self):
        proc = self.invoke(["show", MISSING], agent=None)
        self.assert_refusal(proc, "unknown")


class BlockedQueue(_RegisterFixture):
    """Codex P2 on PR #99: a decided row has stopped waiting."""

    def test_report_blocked_lists_only_proposed_rows(self):
        # Broken impl that reports every non-null blocked_on as blocked.
        self.add(DID, agent=OWNER, extra=["--blocked-on", "PR merge"])
        self.add(LAW, agent=OWNER, extra=["--blocked-on", "corpus landing"])
        ratified = self.invoke(
            ["ratify", DID, "--evidence", "owner: yes, land it"],
            agent=VENDOR_ANTHROPIC,
        )
        self.assertEqual(ratified.exit, 0, ratified.stderr)
        proc = self.invoke(["report"], agent=None)
        self.assertEqual(proc.exit, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        blocked_ids = [row["id"] for row in payload["blocked"]]
        self.assertIn(LAW, blocked_ids, payload)
        self.assertNotIn(DID, blocked_ids, payload)


class LaneNameSelectsStore(_RegisterFixture):
    """LANE_NAME from the conf selects the store under HOME."""

    def test_lane_name_from_conf_selects_the_store(self):
        text = (
            "LANE_NAME='lanex'\n"
            "TRUNK_BRANCH='trunkx'\n"
            "RELEASE_BRANCH='relx'\n"
            "REPO_SLUG='org/projx'\n"
            "CHECK_NAMES='checkx'\n"
            "WF_AGENT_DEFAULT='Conf Agent <conf@example.invalid>'\n"
            "LANE_AUTHOR='Lane Owner <lane-owner@example.invalid>'\n"
            "MODEL_GROK='model-grok'\n"
            "MODEL_CODEX='model-codex'\n"
            "MODEL_CLAUDE_READ='model-claude-read'\n"
            "MODEL_CLAUDE_PLAN='model-claude-plan'\n"
            "EFFORT_GROK='high'\n"
            "EFFORT_CODEX='high'\n"
            "EFFORT_CLAUDE='high'\n"
            "WALL_DEFAULT='900'\n"
            "SCOPE_CAP='1024'\n"
        )
        conf = self.world.plant_text(self.scratch / "lane.conf", text)
        lanex = self.home / ".local" / "state" / "lanex" / "decisions.db"
        self.assertFalse(lanex.exists(), "plant: lanex store must start absent")
        self.assertFalse(self.store.exists(), "plant: default store absent")
        proc = self.invoke(
            ["add", DID, QUESTION, "--home", HOME_PATH],
            agent=OWNER,
            extra_env={"LANE_CONF": str(conf)},
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertTrue(lanex.is_file(), f"store missing under lanex: {lanex}")
        self.assertFalse(
            self.store.exists(),
            f"store used the literal lane name: {self.store}",
        )


if __name__ == "__main__":
    unittest.main()
