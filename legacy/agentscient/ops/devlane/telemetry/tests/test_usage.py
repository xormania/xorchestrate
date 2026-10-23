"""The usage reporter, against fixtures shaped like the real session stores.

Each fixture replicates a shape measured on 2026-08-21 from the live
stores: Claude's per-message usage, Codex's cumulative token_count events,
and Grok's summary.json — which records NO token usage, a gap the report
must state rather than estimate around.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import quote

from ops.testkit import World, run

USAGE = Path(__file__).resolve().parents[1] / "usage.py"
REPO = "/home/work/projects/uscient/agentscient"


def line(**kw):
    return json.dumps(kw) + "\n"


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.home = self.world.home

        # Claude: <dir>/<project-slug>/<session>.jsonl, usage per message.
        proj = self.home / "claude" / "-home-work-projects-uscient-agentscient"
        self.world.plant_text(
            proj / "aaaa.jsonl",
            line(timestamp="2026-08-21T10:00:00.000Z",
                 message={"model": "claude-fable-5",
                          "usage": {"input_tokens": 10,
                                    "cache_creation_input_tokens": 1000,
                                    "cache_read_input_tokens": 5000,
                                    "output_tokens": 200}})
            + line(timestamp="2026-08-21T10:05:00.000Z",
                   message={"model": "claude-fable-5",
                            "usage": {"input_tokens": 20,
                                      "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": 6000,
                                      "output_tokens": 300}}))
        other = self.home / "claude" / "-somewhere-else"
        self.world.plant_text(
            other / "bbbb.jsonl",
            line(timestamp="2026-08-21T09:00:00.000Z",
                 message={"model": "claude-fable-5",
                          "usage": {"input_tokens": 999, "output_tokens": 999}}))

        # Codex: sessions/Y/M/D/rollout-*.jsonl, cumulative token_count.
        day = self.home / "codex" / "sessions" / "2026" / "08" / "21"
        self.world.plant_text(
            day / "rollout-2026-08-21T10-00-00-cccc.jsonl",
            line(timestamp="2026-08-21T10:00:00.000Z", type="session_meta",
                 payload={"id": "cccc", "cwd": REPO,
                          "timestamp": "2026-08-21T10:00:00.000Z"})
            + line(timestamp="2026-08-21T10:02:00.000Z", type="event_msg",
                   payload={"type": "token_count",
                            "info": {"total_token_usage": {
                                "input_tokens": 100, "cached_input_tokens": 50,
                                "output_tokens": 40, "total_tokens": 140}}})
            + line(timestamp="2026-08-21T10:09:00.000Z", type="event_msg",
                   payload={"type": "token_count",
                            "info": {"total_token_usage": {
                                "input_tokens": 400, "cached_input_tokens": 300,
                                "output_tokens": 90, "total_tokens": 490}}}))

        # Grok: sessions/<urlencoded-cwd>/<id>/summary.json — no tokens.
        gdir = (self.home / "grok" / "sessions"
                / quote(REPO, safe="") / "dddd")
        self.world.plant_text(gdir / "summary.json", json.dumps({
            "info": {"id": "dddd", "cwd": REPO},
            "session_summary": "SESSIONPROMPTTEXT", "num_messages": 177,
            "created_at": "2026-08-21T10:00:00.000000000Z",
            "updated_at": "2026-08-21T10:20:00.000000000Z",
            "current_model_id": "grok-4.6",
            "git_root_dir": REPO + "/"}))

    def run_usage(self, *args):
        return run(
            [sys.executable, str(USAGE), *args,
             "--claude-dir", str(self.home / "claude"),
             "--codex-dir", str(self.home / "codex"),
             "--grok-dir", str(self.home / "grok"),
             "--repo", REPO],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=30,
        )

    def sessions(self):
        proc = self.run_usage("sessions", "--json")
        self.assertEqual(proc.exit, 0, proc.stderr)
        return json.loads(proc.stdout)["sessions"]


class SessionsAreNormalised(Fixtures):
    def test_all_three_harnesses_appear_once(self):
        rows = self.sessions()
        self.assertEqual(sorted(r["harness"] for r in rows),
                         ["claude", "codex", "grok"])

    def test_the_repo_filter_excludes_other_projects(self):
        for row in self.sessions():
            self.assertNotEqual(row.get("session"), "bbbb",
                                "a session from another repo leaked in")

    def test_claude_tokens_are_summed_per_message(self):
        row = next(r for r in self.sessions() if r["harness"] == "claude")
        self.assertEqual(row["tokens"]["output"], 500)
        self.assertEqual(row["tokens"]["input"], 30)
        self.assertEqual(row["tokens"]["cached"], 12000)

    def test_codex_takes_the_last_cumulative_count(self):
        row = next(r for r in self.sessions() if r["harness"] == "codex")
        self.assertEqual(row["tokens"]["total"], 490,
                         "cumulative counts must not be summed")
        self.assertEqual(row["tokens"]["output"], 90)

    def test_grok_states_its_gap_instead_of_inventing(self):
        row = next(r for r in self.sessions() if r["harness"] == "grok")
        self.assertIsNone(row["tokens"])
        self.assertIn("not yet parsed", row["note"])
        self.assertEqual(row["messages"], 177)
        self.assertEqual(row["model"], "grok-4.6")


class TheRepoPathIsResolvedAtTheBoundary(unittest.TestCase):
    """Origin LD-44 / T-63: `--repo .` from inside the checkout read no
    sessions at all, because collect() handed the readers a relative path
    to compare against the transcripts' absolute cwd. Silence is not a
    declaration: the relative form must read what the absolute one reads.
    worth.py took abspath at the boundary in abf766b5; usage.py takes the
    same one."""

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.repo = self.world.cwd / "checkout"
        # Planting the file makes the directory and proves it landed.
        self.world.plant_text(self.repo / "README.md", "checkout fixture\n")
        slug = "-" + "-".join(str(self.repo).strip("/").split("/"))
        self.world.plant_text(
            self.home / "claude" / slug / "eeee.jsonl",
            line(timestamp="2026-08-21T10:00:00.000Z",
                 message={"model": "claude-fable-5",
                          "usage": {"input_tokens": 7,
                                    "cache_creation_input_tokens": 0,
                                    "cache_read_input_tokens": 0,
                                    "output_tokens": 11}}))

    def _sessions(self, repo_arg, cwd):
        proc = run(
            [sys.executable, str(USAGE), "sessions", "--json",
             "--claude-dir", str(self.home / "claude"),
             "--codex-dir", str(self.home / "codex"),
             "--grok-dir", str(self.home / "grok"),
             "--repo", repo_arg],
            cwd=cwd,
            env=self.world.env(),
            timeout=30,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        return [row["session"] for row in json.loads(proc.stdout)["sessions"]]

    def test_a_relative_repo_reads_what_the_absolute_one_reads(self):
        self.assertEqual(
            self._sessions(str(self.repo), self.repo), ["eeee"],
            "the absolute form did not read the planted session",
        )
        self.assertEqual(
            self._sessions(".", self.repo), ["eeee"],
            "--repo . read no sessions for the checkout it ran in",
        )
        self.assertEqual(
            self._sessions("checkout", self.world.cwd), ["eeee"],
            "a relative path named from the parent read no sessions",
        )


class Planters:
    """The four session stores, planted at a cwd the row chooses.

    Both placement classes below ask the same question of the same four
    readers and differ only in what makes a cwd this repository's, so
    the fixtures are here and the rows are there.
    """

    @staticmethod
    def slug(cwd):
        """Claude's own spelling of a cwd as a project directory name."""
        return "-" + "-".join(cwd.strip("/").split("/"))

    def usage(self, *args):
        return run(
            [sys.executable, str(USAGE), *args,
             "--claude-dir", str(self.home / "claude"),
             "--codex-dir", str(self.home / "codex"),
             "--grok-dir", str(self.home / "grok"),
             # The agy reader runs only when a root is named; the other
             # three have defaults. A root that does not exist answers
             # with no captures, which is what the rows that plant no
             # agy capture want.
             "--agy-dir", str(self.home / "captures"),
             "--repo", self.repo_path, "--json"],
            cwd=self.world.cwd, env=self.world.env(), timeout=60,
        )

    def ids(self):
        proc = self.usage("sessions")
        self.assertEqual(proc.exit, 0, proc.stderr)
        return sorted(s["session"] for s in json.loads(proc.stdout)["sessions"])

    def plant_claude(self, name, cwd):
        # The directory name matters: it is the first of the two filters,
        # and inventing one would let a row pass or fail for a reason the
        # defect has nothing to do with.
        self.world.plant_text(
            self.home / "claude" / self.slug(cwd) / f"{name}.jsonl",
            line(timestamp="2026-08-21T10:00:00.000Z", cwd=cwd,
                 message={"id": f"m-{name}", "model": "claude-fable-5",
                          "usage": {"input_tokens": 10, "output_tokens": 20}}))

    def plant_codex(self, name, cwd):
        day = self.home / "codex" / "sessions" / "2026" / "08" / "21"
        self.world.plant_text(
            day / f"rollout-2026-08-21T10-00-00-{name}.jsonl",
            line(timestamp="2026-08-21T10:00:00.000Z", type="session_meta",
                 payload={"id": name, "cwd": cwd})
            + line(timestamp="2026-08-21T10:02:00.000Z", type="event_msg",
                   payload={"type": "token_count",
                            "info": {"total_token_usage": {
                                "input_tokens": 1, "cached_input_tokens": 0,
                                "output_tokens": 1, "total_tokens": 2}}}))

    def plant_grok(self, name, cwd):
        gdir = self.home / "grok" / "sessions" / quote(cwd, safe="") / name
        self.world.plant_text(gdir / "summary.json", json.dumps({
            "info": {"id": name, "cwd": cwd}, "num_messages": 3,
            "created_at": "2026-08-21T10:00:00.000000000Z",
            "updated_at": "2026-08-21T10:20:00.000000000Z",
            "current_model_id": "grok-4.6"}))

    def plant_agy(self, name, cwd):
        """A captured native stdout: the init frame binds the session to
        its cwd, the result frame carries the usage. Two whole NDJSON
        lines are what the reader calls terminal; a truncated last line
        is a different fact and belongs to a different row."""
        raw = (json.dumps({"event": "init", "conversation_id": name,
                           "init": {"cwd": cwd, "model": "grok-4.6"}}) + "\n"
               + json.dumps({"event": "result",
                             "result": {"conversation_id": name,
                                        "num_turns": 2,
                                        "usage": {"input_tokens": 5,
                                                  "cache_read_tokens": 1,
                                                  "output_tokens": 2,
                                                  "total_tokens": 8}}}) + "\n")
        self.world.plant_text(
            self.home / "captures" / name / "raw.out", raw)

    def plant_all(self, suffix, cwd):
        """One session per harness at the same cwd, named <harness><suffix>."""
        self.plant_claude("claude" + suffix, cwd)
        self.plant_codex("codex" + suffix, cwd)
        self.plant_grok("grok" + suffix, cwd)
        self.plant_agy("agy" + suffix, cwd)
        return [h + suffix for h in ("claude", "codex", "grok", "agy")]


class ACwdInsideTheRepoIsTheRepo(Planters, unittest.TestCase):
    """The comparison is containment, not string equality.

    All four readers asked whether the cwd was the repo path exactly, so
    a session that worked from a subdirectory of the repo counted for no
    repo at all. Measured 2026-09-16 across 243 claude sessions on this
    host, no session is dropped by this today — every session that
    starts at the root keeps the root in its cwd set forever. The rule
    is still wrong, and an exact compare on a path is the kind of wrong
    that is invisible until the day it is not.

    All four readers have rows here. The agy ones arrived a commit later
    than the rest: f60f00b0 changed that site with the other three and
    declared the gap rather than leave one reader inconsistent, and this
    is the gap closed.
    """

    SUB = REPO + "/ops/conductor"
    SIBLING = REPO + "-other"

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.home = self.world.home
        # A path, not a checkout: these rows never reach the filesystem,
        # which is what keeps them measuring the path comparison alone.
        self.repo_path = REPO

    def test_a_claude_session_run_from_a_subdirectory_counts(self):
        self.plant_claude("inside", self.SUB)
        self.assertIn("inside", self.ids())

    def test_a_codex_session_run_from_a_subdirectory_counts(self):
        self.plant_codex("inside", self.SUB)
        self.assertIn("inside", self.ids())

    def test_a_grok_session_run_from_a_subdirectory_counts(self):
        self.plant_grok("inside", self.SUB)
        self.assertIn("inside", self.ids())

    def test_an_agy_session_run_from_a_subdirectory_counts(self):
        self.plant_agy("inside", self.SUB)
        self.assertIn("inside", self.ids())

    def test_a_sibling_path_that_merely_shares_a_prefix_is_not_the_repo(self):
        # The containment test is on path components, not on characters:
        # <repo>-other starts with the repo's spelling and is a different
        # checkout. A prefix compare would silently absorb its spend. For
        # claude this is the whole reason the cwd is consulted at all: the
        # slug flattens '/' and '-' alike, so this sibling and a genuine
        # subdirectory produce the same directory name, and only the cwd
        # in the entries tells them apart.
        self.plant_claude("stranger", self.SIBLING)
        self.plant_codex("stranger-c", self.SIBLING)
        self.plant_grok("stranger-g", self.SIBLING)
        self.plant_agy("stranger-a", self.SIBLING)
        found = self.ids()
        for name in ("stranger", "stranger-c", "stranger-g", "stranger-a"):
            self.assertNotIn(name, found)

    def test_the_repo_itself_still_counts(self):
        self.plant_claude("root", REPO)
        self.plant_codex("root-c", REPO)
        self.plant_grok("root-g", REPO)
        self.plant_agy("root-a", REPO)
        found = self.ids()
        for name in ("root", "root-c", "root-g", "root-a"):
            self.assertIn(name, found)


class AWorktreeOfTheRepoIsTheRepo(Planters, unittest.TestCase):
    """LD-56: a worktree is the repository's own work, and a cwd that is
    gone is not an answer.

    Measured 2026-09-16 at f9e48c35 across 243 claude sessions with
    usage: 33 matched the repo path exactly, 0 sat under it, 2 were
    worktrees of it and were dropped, 208 were elsewhere. Under one
    branch per session every session moves into a worktree, so the
    report reads lower each week for a reason that has nothing to do
    with spend. A worktree shares the repository's `.git`, and that is
    what these rows ask about: not the path, which says nothing, but
    `git rev-parse --git-common-dir`, which is the same directory for
    the checkout, its subdirectories and every worktree cut from it.

    The path comparison of ACwdInsideTheRepoIsTheRepo still runs first
    and answers without a subprocess; git is asked only about a cwd the
    path test has already refused, so nothing here costs the common case
    anything.
    """

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.checkout = self.plant_checkout("checkout")
        self.repo_path = str(self.checkout)
        self.worktree = self.world.cwd / "wt" / "lane-a"
        self.git(self.checkout, "worktree", "add", "-q",
                 "-b", "lane-a", str(self.worktree))
        self.assertTrue((self.worktree / ".git").exists(),
                        "INVALID: the worktree did not land")

    def git(self, cwd, *args):
        proc = run(["git", "-C", str(cwd),
                    "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=self.world.cwd, env=self.world.env(), timeout=60)
        self.assertEqual(proc.exit, 0, proc.stdout + proc.stderr)
        return proc

    def plant_checkout(self, name):
        """A real repository: the rows below ask git about it, so a path
        that merely looks like one would measure nothing."""
        root = self.world.cwd / name
        root.mkdir(parents=True, exist_ok=True)
        self.git(root, "init", "-q", "-b", "dev")
        self.world.plant_text(root / "README", f"{name}\n")
        self.git(root, "add", "README")
        self.git(root, "commit", "-q", "-m", "seed")
        return root

    def unplaced(self):
        proc = self.usage("sessions")
        self.assertEqual(proc.exit, 0, proc.stderr)
        return json.loads(proc.stdout)["unplaced"]

    def test_a_session_run_from_a_worktree_of_the_repo_counts(self):
        planted = self.plant_all("-wt", str(self.worktree))
        found = self.ids()
        for name in planted:
            self.assertIn(name, found)

    def test_a_worktree_of_another_repository_is_not_this_one(self):
        other = self.plant_checkout("other")
        stranger = self.world.cwd / "wt" / "other-lane"
        self.git(other, "worktree", "add", "-q", "-b", "lane-b", str(stranger))
        planted = self.plant_all("-other", str(stranger))
        found = self.ids()
        for name in planted:
            self.assertNotIn(name, found)

    def test_a_cwd_that_exists_and_is_no_checkout_is_simply_elsewhere(self):
        """The clean neighbour for the row below: this path was read, and
        what was read says it is not this repository. That is an answer,
        so it is not reported as one that could not be taken."""
        elsewhere = self.world.cwd / "not-a-checkout"
        elsewhere.mkdir(parents=True, exist_ok=True)
        planted = self.plant_all("-else", str(elsewhere))
        found = self.ids()
        for name in planted:
            self.assertNotIn(name, found)
        self.assertEqual(self.unplaced(), [])

    def test_a_cwd_that_is_gone_could_not_be_looked_at(self):
        """A worktree removed after its session ended. The path is not
        under the repo and there is nothing left to ask git about, so the
        session is not counted here — and the report says it could not
        look rather than passing the silence off as elsewhere."""
        vanished = self.world.cwd / "wt" / "vanished"
        self.assertFalse(vanished.exists(), "INVALID: the path is still there")
        planted = self.plant_all("-gone", str(vanished))
        found = self.ids()
        for name in planted:
            self.assertNotIn(name, found)
        self.assertEqual(self.unplaced(), [str(vanished)])

    def test_the_checkout_and_a_subdirectory_still_count(self):
        inside = self.checkout / "ops"
        inside.mkdir(parents=True, exist_ok=True)
        planted = self.plant_all("-root", str(self.checkout))
        planted += self.plant_all("-sub", str(inside))
        found = self.ids()
        for name in planted:
            self.assertIn(name, found)
        self.assertEqual(self.unplaced(), [])


class TheReportAggregates(Fixtures):
    def test_totals_per_harness_and_the_gap_stated(self):
        proc = self.run_usage("report")
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertIn("claude", proc.stdout)
        self.assertIn("490", proc.stdout, "codex total")
        self.assertIn("not yet parsed", proc.stdout, "the grok gap is stated")

    def test_the_report_never_prints_prompt_text(self):
        # The stores hold prompts and summaries; the report is aggregates
        # only. The fixture plants a distinctive marker to catch a leak.
        proc = self.run_usage("report")
        self.assertNotIn("SESSIONPROMPTTEXT", proc.stdout,
                         "session content leaked into the report")


class AReaderTakesAPlacementAndSaysSo(unittest.TestCase):
    """Origin: LD-66. cd1083a8 gave the four readers a Placement where they
    took a repo path and left ten callers holding the old shape. Each of
    them failed on the first attribute its reader touched, `place.repo` or
    `place.holds`, and launch.py's failed inside a worker thread in
    test_launch_watch: the traceback went to a log nobody was reading and
    the test waited on a result that was never coming, so the lane job was
    cancelled at 15m15s rather than reporting a failure. The planted faults
    are the three shapes the callers passed; the clean neighbour is the
    same call with a Placement.
    """

    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "agentscient_usage_placement_rows", USAGE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.readers = (
            self.module.agy_sessions,
            self.module.claude_sessions,
            self.module.codex_sessions,
            self.module.grok_sessions,
        )
        # Every store reader the module holds, and no other: a fifth one
        # added without a row here is the gap this class exists to close.
        # cmd_sessions is not one of them — it is the `sessions` verb,
        # which builds a Placement of its own and hands it to the four.
        named = sorted(r.__name__ for r in self.readers)
        found = sorted(
            n for n in vars(self.module)
            if n.endswith("_sessions") and not n.startswith("cmd_")
            and callable(getattr(self.module, n))
        )
        self.assertEqual(named, found, "a reader with no row in this class")

    def test_a_reader_handed_anything_but_a_placement_refuses(self):
        for shape, value in (("NoneType", None),
                             ("str", REPO),
                             ("PosixPath", Path(REPO))):
            for reader in self.readers:
                with self.subTest(reader=reader.__name__, passed=shape):
                    with self.assertRaises(TypeError) as caught:
                        list(reader(Path(REPO) / "no-such-store", value))
                    line = str(caught.exception)
                    self.assertTrue(
                        line.startswith("placement: "), f"no class token: {line}")
                    for word in ("expected", "found", "needed"):
                        self.assertIn(word, line, line)
                    self.assertLess(
                        line.index("expected"), line.index("found"), line)
                    self.assertLess(
                        line.index("found"), line.index("needed"), line)
                    self.assertIn(reader.__name__, line, line)
                    self.assertIn(shape, line, line)

    def test_a_reader_handed_a_placement_answers_with_an_empty_list(self):
        place = self.module.Placement(REPO)
        # REPO is a path no checkout sits at, so the Placement asks git
        # nothing and the readers answer on a store that is not there:
        # looked and found nothing, which is an answer and not a raise.
        self.assertIsNone(place.repo_common)
        for reader in self.readers:
            with self.subTest(reader=reader.__name__):
                self.assertEqual(
                    list(reader(Path(REPO) / "no-such-store", place)), [])


if __name__ == "__main__":
    unittest.main()
