"""Per-job runtime caps: resolution order, policy values, enforcement.

Written from CONTRACT.md §The record, the ``caps.timeout`` bullet:
``DISPATCH_TIMEOUT`` from the invoking environment, else the job's own
``caps.timeout`` in jobs.json, else 900 — and ``caps.timeout_source``
names the layer that answered: ``DISPATCH_TIMEOUT``, ``job``, or
``default``; the job's cap bounds ``DISPATCH_TIMEOUT``, and
``caps.timeout_requested`` records what the environment asked for.
Policy values and the author-tests commit-on-red clause
come from ``ops/devlane/task/jobs.json``. launch.py and record.py were
not read.
"""

from __future__ import annotations

import json
import os
import time
import unittest
from pathlib import Path

import launch_support as ls

from ops.testkit import World

DEFAULT_TIMEOUT = 900.0
COMMIT_ON_RED = "Commit each test file as soon as its red is proven"
# The reading discipline, 2026-09-16: the largest single tool output in
# each of the two heaviest author-tests runs was a whole-file dump of
# about 40 KB, one of them the law under docs/, compounding over 80 to 98
# turns into 9 to 13M cumulative input (measured by agentscient-11).
READING_DISCIPLINE = (
    "do not read docs/",
    "sed -n",
    "tail, not its whole output",
)
BUDGET_HEAD = "Turn budget:"
BUDGET_STOP = "stop and report status short"
JOB_TURNS = 37
ENV_TURNS = 21
JOB_TIMEOUT = 42
ENV_TIMEOUT = 123.5
ENV_BELOW = 21.5
LOOP_TIMEOUT = 0.3
ONE_HOUR = 3600.0


def _caps(test, rec):
    test.assertIsInstance(rec, dict)
    test.assertIn("caps", rec)
    caps = rec["caps"]
    test.assertIsInstance(caps, dict)
    test.assertIn("timeout", caps)
    test.assertIn("timeout_source", caps)
    timeout = caps["timeout"]
    test.assertIsInstance(timeout, (int, float))
    test.assertNotIsInstance(timeout, bool)
    test.assertIsInstance(caps["timeout_source"], str)
    return caps


def plant_job_timeout(test, job, seconds):
    """Write ``caps.timeout`` onto one job in the fixture catalog."""
    test.assertIsInstance(test.world, World)
    path = test.jobs_file
    before = path.read_text(encoding="utf-8")
    before_obj = json.loads(before)
    test.assertIsInstance(before_obj, dict)
    test.assertIn(job, before_obj)
    prior = (before_obj.get(job) or {}).get("caps")
    prior_timeout = None if not isinstance(prior, dict) else prior.get("timeout")
    test.assertNotEqual(
        prior_timeout, seconds,
        f"plant {job} timeout={seconds} was already the fixture",
    )
    jobs = json.loads(before)
    spec = dict(jobs[job])
    spec["caps"] = {"timeout": seconds}
    jobs[job] = spec
    content = json.dumps(jobs, indent=2) + "\n"
    # The plant replaces caps that are already there, so the file may shrink;
    # what it has to prove is that it changed the catalogue and landed.
    test.assertNotEqual(content, before, "plant changed nothing")
    test.assertIn(job, content)
    test.assertIn('"timeout"', content)
    test.world.plant_text(path, content, recognisable='"timeout"')
    landed = json.loads(path.read_text(encoding="utf-8"))
    test.assertEqual(landed[job]["caps"]["timeout"], seconds)
    test.assertNotEqual(landed[job].get("caps"), prior)
    test.ref = test._commit(f"plant {job} caps.timeout={seconds}")
    return seconds


class JobTimeoutIsRecordedWhenEnvIsAbsent(ls._TempLaunch):
    """Else the job's own caps.timeout; source is job."""

    def test_a_job_cap_is_recorded_as_timeout_with_source_job(self):
        self.assertNotIn("DISPATCH_TIMEOUT", os.environ)
        plant_job_timeout(self, "plan", JOB_TIMEOUT)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _caps(self, rec)
        self.assertEqual(float(caps["timeout"]), float(JOB_TIMEOUT))
        self.assertNotEqual(float(caps["timeout"]), DEFAULT_TIMEOUT)
        self.assertEqual(caps["timeout_source"], "job")
        self.assertIn("timeout_requested", caps, "an unset request is declared, not absent")
        self.assertIsNone(caps["timeout_requested"])


class EnvTimeoutAboveTheJobCapIsNotAdmitted(ls._TempLaunch):
    """The Lead's ruling, 2026-09-22 23:39Z: the job's cap bounds
    DISPATCH_TIMEOUT. Asked for more than the cap, the launcher applies
    the cap, names the job as the source, and records the request."""

    def test_dispatch_timeout_above_the_cap_applies_the_cap(self):
        plant_job_timeout(self, "plan", JOB_TIMEOUT)
        self.assertGreater(ENV_TIMEOUT, JOB_TIMEOUT)
        os.environ["DISPATCH_TIMEOUT"] = str(ENV_TIMEOUT)
        self.addCleanup(os.environ.pop, "DISPATCH_TIMEOUT", None)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _caps(self, rec)
        self.assertEqual(float(caps["timeout"]), float(JOB_TIMEOUT))
        self.assertEqual(caps["timeout_source"], "job")
        self.assertIn("timeout_requested", caps)
        self.assertEqual(float(caps["timeout_requested"]), ENV_TIMEOUT)


class EnvTimeoutBelowTheJobCapIsAdmitted(ls._TempLaunch):
    """At or below the cap, DISPATCH_TIMEOUT answers and the source names
    it; the request is recorded beside the value applied."""

    def test_dispatch_timeout_below_the_cap_is_applied(self):
        plant_job_timeout(self, "plan", JOB_TIMEOUT)
        self.assertLess(ENV_BELOW, JOB_TIMEOUT)
        os.environ["DISPATCH_TIMEOUT"] = str(ENV_BELOW)
        self.addCleanup(os.environ.pop, "DISPATCH_TIMEOUT", None)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _caps(self, rec)
        self.assertEqual(float(caps["timeout"]), ENV_BELOW)
        self.assertEqual(caps["timeout_source"], "DISPATCH_TIMEOUT")
        self.assertIn("timeout_requested", caps)
        self.assertEqual(float(caps["timeout_requested"]), ENV_BELOW)


class MalformedEnvTimeoutIsRefusedBeforeLaunch(ls._TempLaunch):
    """20260923T000804Z-review-codex-da66dd#1 and #2: a DISPATCH_TIMEOUT
    that is not a positive finite number of seconds is refused, class
    timeout, before anything is minted or spawned. At 0c001217 a
    non-number started the harness and then exited 2 with the child alive
    (#1), and NaN launched and wrote an unquoted NaN into the record (#2).
    The below-the-cap row above is the clean neighbour."""

    def test_a_value_that_is_not_a_positive_finite_number_is_refused(self):
        plant_job_timeout(self, "plan", JOB_TIMEOUT)
        self.addCleanup(os.environ.pop, "DISPATCH_TIMEOUT", None)
        for raw in ("not-a-number", "NaN", "inf", "-inf", "0", "-5"):
            with self.subTest(value=raw):
                os.environ["DISPATCH_TIMEOUT"] = raw
                code, out, err = self.dispatch(job="plan", harness="grok", stage="plan")
                text = self.combined(out, err)
                self.assertEqual(code, ls.REFUSAL_EXIT, f"{raw}: {text!r}")
                self.assertIn("timeout: expected", text)
                self.assertEqual(self.record_files(), [], f"{raw}: a record was written")
                self.assertFalse(self.start_witness.is_file(), f"{raw}: the harness started")


class DefaultTimeoutWhenNeitherLayerAnswers(ls._TempLaunch):
    """Else 900, source default — a catalogue entry declaring no caps, and
    no env. The capless entry is planted (strip_job_caps): every job in the
    real catalogue declares its caps, and an absence this test needs is one
    it makes, never one it hopes to find."""

    def test_no_job_cap_and_no_env_records_900_from_default(self):
        self.assertIsInstance(self.world, World)
        self.assertNotIn("DISPATCH_TIMEOUT", os.environ)
        ls.strip_job_caps(self, "plan")
        jobs = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        self.assertNotIn("caps", jobs["plan"])
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _caps(self, rec)
        self.assertEqual(float(caps["timeout"]), DEFAULT_TIMEOUT)
        self.assertEqual(caps["timeout_source"], "default")


class EveryCatalogueJobLaunchesUnderTheOneHourCap(ls._TempLaunch):
    """CONTRACT.md §The record: every job in jobs.json declares
    caps.timeout 3600 (owner, 2026-09-22, relayed by the Lead: "raise
    them to 60 mins - we're shooting ourselves in the foot"). Each job in
    the real catalogue is launched with no DISPATCH_TIMEOUT and its record
    read, so the row runs the launcher rather than reading the file. The
    planted fault is the catalogue at e20dac19, eight jobs below the hour;
    implement, already at 3600, is the clean neighbour.

    A job whose adapter is not ``harness`` (verify is ``direct``) is not
    the launcher's: the launcher refuses it, and this row asserts that
    refusal and the value it declares. What bounds a direct job at run
    time is task/run.py's runtime.timeout, which this row does not reach;
    that is a declared gap, not a cover."""

    def test_each_job_records_3600_from_the_job_layer(self):
        self.assertNotIn("DISPATCH_TIMEOUT", os.environ)
        # A review's base is the merge-base with dev; plant dev at the
        # middle commit so ref..base is not empty (as the read-role rows do).
        self._git("branch", "dev", self.mid_sha)
        catalogue = json.loads(ls.JOBS_PATH.read_text(encoding="utf-8"))
        self.assertGreater(len(catalogue), 0, "jobs.json must name jobs")
        for job in sorted(catalogue):
            with self.subTest(job=job):
                if catalogue[job].get("adapter") != "harness":
                    code, out, err = self.dispatch(job=job, harness="grok")
                    self.assertEqual(code, ls.REFUSAL_EXIT, f"{job}: {self.combined(out, err)!r}")
                    self.assertIn("job: expected a harness job", self.combined(out, err))
                    self.assertEqual(catalogue[job]["caps"]["timeout"], int(ONE_HOUR))
                    continue
                # A job whose brief names no {scope} refuses one (adjudicate).
                takes_scope = "{scope}" in (catalogue[job].get("prompt") or "")
                before = set(self.record_files())
                code, out, err = self.dispatch(
                    job=job, harness="grok", scope="pin the launcher" if takes_scope else None)
                self.assertEqual(
                    code, 0, f"{job} launch exits 0: {self.combined(out, err)[-400:]!r}")
                new = sorted(set(self.record_files()) - before)
                self.assertEqual(len(new), 1, f"{job} wrote one record: {new}")
                caps = _caps(self, self.read_record(new[0]))
                self.assertEqual(caps["timeout_source"], "job")
                self.assertEqual(float(caps["timeout"]), ONE_HOUR)


class EveryJobDeclaresItsCaps(unittest.TestCase):
    """Every job in the catalogue declares caps.timeout and caps.turns.

    What this replaces: a pin naming five jobs and their exact numbers.
    It said nothing about a sixth job, which is how four jobs (verify,
    sweep, plan, adjudicate) ran uncapped while the pin was green, and it
    had to be edited beside the catalogue whenever a number moved. The
    contract owns the closure — a job declares what bounds it — and
    jobs.json owns the numbers (docs/tests.md rule 3: a pin on a count
    the contract does not own is a finding). A cap's value is a decision,
    recorded in the commit that moves it.
    """

    def setUp(self):
        self.world = World(prefix="launch-job-caps-catalog-")
        self.addCleanup(self.world.close)
        self.assertTrue(ls.JOBS_PATH.is_file(), f"missing {ls.JOBS_PATH}")
        planted = self.world.plant_text(
            self.world.cwd / "jobs.json",
            ls.JOBS_PATH.read_text(encoding="utf-8"),
            recognisable='"timeout"',
        )
        self.jobs = json.loads(planted.read_text(encoding="utf-8"))
        self.assertIsInstance(self.jobs, dict)
        self.assertGreater(len(self.jobs), 0, "jobs.json must name jobs")

    def positive_int(self, value, where):
        self.assertIsInstance(value, int, where)
        self.assertNotIsInstance(value, bool, where)
        self.assertGreater(value, 0, where)

    def test_every_job_carries_caps_with_turns_and_timeout(self):
        uncapped = sorted(
            name for name, spec in self.jobs.items()
            if not isinstance(spec, dict)
            or not isinstance(spec.get("caps"), dict)
            or "timeout" not in spec["caps"] or "turns" not in spec["caps"]
        )
        self.assertEqual(
            uncapped, [],
            f"every job declares caps.timeout and caps.turns; these do not: {uncapped}",
        )
        for name, spec in self.jobs.items():
            with self.subTest(job=name):
                self.positive_int(spec["caps"]["timeout"], f"{name} caps.timeout")
                self.positive_int(spec["caps"]["turns"], f"{name} caps.turns")


class AuthorTestsPromptContainsTheCommitOnRedClause(unittest.TestCase):
    """The author-tests prompt asks to commit each test file on proven red."""

    def setUp(self):
        self.world = World(prefix="launch-job-caps-prompt-")
        self.addCleanup(self.world.close)
        self.assertTrue(ls.JOBS_PATH.is_file(), f"missing {ls.JOBS_PATH}")
        planted = self.world.plant_text(
            self.world.cwd / "jobs.json",
            ls.JOBS_PATH.read_text(encoding="utf-8"),
            recognisable=COMMIT_ON_RED,
        )
        self.jobs = json.loads(planted.read_text(encoding="utf-8"))

    def test_author_tests_prompt_contains_the_commit_on_red_clause(self):
        self.assertIn("author-tests", self.jobs)
        spec = self.jobs["author-tests"]
        self.assertIsInstance(spec, dict)
        prompt = spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertTrue(prompt.strip(), "author-tests prompt is empty")
        self.assertIn(COMMIT_ON_RED, prompt)

    def test_author_tests_prompt_names_the_reading_discipline(self):
        """Read the law from context, the scope's files in ranges, a run's
        tail: the three the brief has to say, because the cost measured on
        2026-09-16 was whole-file reading, not thinking."""
        prompt = self.jobs["author-tests"]["prompt"]
        self.assertIsInstance(prompt, str)
        missing = [needle for needle in READING_DISCIPLINE if needle not in prompt]
        self.assertEqual(missing, [], f"the author-tests brief does not say: {missing}")


class SupervisionEnforcesTheResolvedJobCap(ls._TempLaunch):
    """The resolved cap, not a flat 900, is what the supervision loop enforces."""

    def test_a_short_job_cap_trips_the_loop_without_waiting_out_900(self):
        self.assertNotIn("DISPATCH_TIMEOUT", os.environ)
        plant_job_timeout(self, "plan", LOOP_TIMEOUT)
        os.environ["TASK_LAUNCH_SLEEP"] = "8"
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        started = time.monotonic()
        self.dispatch(self.argv_for(job="plan", harness="grok", stage="plan"))
        elapsed = time.monotonic() - started
        rec = self.read_record()
        self.assertLess(
            elapsed, 4,
            f"job cap {LOOP_TIMEOUT}s must not wait out 8s, "
            f"let alone 900s: elapsed={elapsed}",
        )
        blob = json.dumps(rec).lower()
        self.assertIn("timeout", blob)
        self.assertTrue(
            self.start_witness.is_file(),
            "plant: the harness child started before the timeout",
        )
        pid = self.read_start_witness()["pid"]
        self.assertFalse(ls.pid_is_alive(pid))


def plant_job_turns(test, job, turns):
    """Write ``caps.turns`` (beside a timeout) onto one job in the fixture."""
    test.assertIsInstance(test.world, World)
    path = test.jobs_file
    before = path.read_text(encoding="utf-8")
    before_obj = json.loads(before)
    test.assertIn(job, before_obj)
    prior = (before_obj.get(job) or {}).get("caps")
    test.assertNotEqual(
        None if not isinstance(prior, dict) else prior.get("turns"), turns,
        f"plant {job} turns={turns} was already the fixture",
    )
    jobs = json.loads(before)
    spec = dict(jobs[job])
    spec["caps"] = {"timeout": JOB_TIMEOUT, "turns": turns}
    jobs[job] = spec
    content = json.dumps(jobs, indent=2) + "\n"
    # The plant replaces caps that are already there, so the file may shrink;
    # what it has to prove is that it changed the catalogue and landed.
    test.assertNotEqual(content, before, "plant changed nothing")
    test.assertIn(job, content)
    test.assertIn('"turns"', content)
    test.world.plant_text(path, content, recognisable='"turns"')
    landed = json.loads(path.read_text(encoding="utf-8"))
    test.assertEqual(landed[job]["caps"]["turns"], turns)
    test.ref = test._commit(f"plant {job} caps.turns={turns}")
    return turns


def _turn_caps(test, rec):
    caps = _caps(test, rec)
    for key in ("turns", "turns_source", "turns_argv"):
        test.assertIn(key, caps, f"caps has no {key}")
    test.assertIsInstance(caps["turns_argv"], list)
    test.assertIsInstance(caps["turns_source"], str)
    return caps


def _max_turns_value(test, argv):
    test.assertIn("--max-turns", argv, argv)
    idx = argv.index("--max-turns")
    test.assertLess(idx + 1, len(argv), argv)
    test.assertEqual(argv.count("--max-turns"), 1, argv)
    return argv[idx + 1]


class JobTurnCapReachesTheGrokArgvAndTheRecord(ls._TempLaunch):
    """Owner, 2026-09-12: "hand grant - cap turns". Grok takes --max-turns N;
    the record carries caps.turns, its source and the exact argv tokens.
    One producer (Claude Fable 5.1) wrote this test and the change under
    a hand grant; no second producer."""

    def test_grok_gets_max_turns_from_the_job_cap(self):
        self.assertNotIn("DISPATCH_TURNS", os.environ)
        plant_job_turns(self, "plan", JOB_TURNS)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertEqual(caps["turns"], JOB_TURNS)
        self.assertEqual(caps["turns_source"], "job")
        self.assertEqual(caps["turns_argv"], ["--max-turns", str(JOB_TURNS)])
        witness = self.read_start_witness()
        self.assertEqual(_max_turns_value(self, witness["argv"]), str(JOB_TURNS))
        self.assertEqual(rec["harness"]["argv"][-len(witness["argv"]) + 1:],
                         witness["argv"][1:])


class JobTurnCapReachesTheClaudeArgv(ls._TempLaunch):
    """Claude's print mode takes --max-turns N the same way."""

    def test_claude_gets_max_turns_from_the_job_cap(self):
        self.assertNotIn("DISPATCH_TURNS", os.environ)
        plant_job_turns(self, "plan", JOB_TURNS)
        rec, *_ = self.launch_ok(job="plan", harness="claude", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertEqual(caps["turns"], JOB_TURNS)
        self.assertEqual(caps["turns_argv"], ["--max-turns", str(JOB_TURNS)])
        witness = self.read_start_witness()
        self.assertEqual(_max_turns_value(self, witness["argv"]), str(JOB_TURNS))


class CodexHasNoTurnFlagAndTheRecordSaysSo(ls._TempLaunch):
    """`codex exec --help | grep -c turn` is 0 on this host (2026-09-12):
    the cap is recorded, no flag is forged, and the record declares the
    absence rather than staying silent."""

    def test_codex_records_the_cap_with_empty_argv_and_a_note(self):
        self.assertNotIn("DISPATCH_TURNS", os.environ)
        plant_job_turns(self, "plan", JOB_TURNS)
        rec, *_ = self.launch_ok(job="plan", harness="codex", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertEqual(caps["turns"], JOB_TURNS)
        self.assertEqual(caps["turns_source"], "job")
        self.assertEqual(caps["turns_argv"], [])
        self.assertIn("codex", caps.get("turns_note", ""))
        self.assertNotIn("--max-turns", self.read_start_witness()["argv"])


class NoTurnCapIsDeclaredNotInferred(ls._TempLaunch):
    """A catalogue entry with no caps.turns and no env records turns None,
    source none. The capless entry is planted, as above."""

    def test_no_job_turns_and_no_env_records_none(self):
        self.assertIsInstance(self.world, World)
        self.assertNotIn("DISPATCH_TURNS", os.environ)
        ls.strip_job_caps(self, "plan")
        jobs = json.loads(self.jobs_file.read_text(encoding="utf-8"))
        self.assertNotIn("caps", jobs["plan"])
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertIsNone(caps["turns"])
        self.assertEqual(caps["turns_source"], "none")
        self.assertEqual(caps["turns_argv"], [])
        self.assertNotIn("--max-turns", self.read_start_witness()["argv"])


class EnvTurnsWinOverTheJobCap(ls._TempLaunch):
    """DISPATCH_TURNS from the invoking environment wins; source names it."""

    def test_dispatch_turns_beats_the_job_cap(self):
        plant_job_turns(self, "plan", JOB_TURNS)
        os.environ["DISPATCH_TURNS"] = str(ENV_TURNS)
        self.addCleanup(os.environ.pop, "DISPATCH_TURNS", None)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertEqual(caps["turns"], ENV_TURNS)
        self.assertEqual(caps["turns_source"], "DISPATCH_TURNS")
        self.assertEqual(_max_turns_value(self, self.read_start_witness()["argv"]),
                         str(ENV_TURNS))


def _applied(test, caps):
    """``turns_applied`` read as an assertion, so its absence is a failure
    with a one-line message rather than a KeyError."""
    test.assertIn("turns_applied", caps, f"caps carry no turns_applied: {sorted(caps)}")
    return caps["turns_applied"]


def _carries(test, text, needle):
    """A membership assertion whose message names the needle and the size,
    never the brief: a failing assertion that prints ten kilobytes of
    prompt is the waste this change exists to cut."""
    test.assertTrue(needle in text,
                    f"{needle!r} absent from the {len(text)}-byte brief")


def _prompt_text(test, rec):
    """The composed brief, read from the job directory, so the assertion
    holds for a harness that takes no --prompt-file."""
    root = ((rec.get("snapshot") or {}).get("root") or "")
    test.assertTrue(root, "record names no snapshot root")
    prompt = Path(root).parent / "prompt.txt"
    test.assertTrue(prompt.is_file(), f"no brief at {prompt}")
    return prompt.read_text(encoding="utf-8")


class TheBriefCarriesTheTurnBudget(ls._TempLaunch):
    """Owner, 2026-09-15, on token efficiency. A harness with no turn flag
    can still be told its budget, so the cap reaches every worker as words
    even where it cannot reach one as argv. Belt and braces: the line is in
    every brief, not only the flagless harnesses'."""

    def test_the_grok_brief_names_the_budget_and_the_stop(self):
        plant_job_turns(self, "plan", JOB_TURNS)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        text = _prompt_text(self, rec)
        _carries(self, text, f"{BUDGET_HEAD} {JOB_TURNS} turns")
        _carries(self, text, BUDGET_STOP)

    def test_the_codex_brief_names_the_budget_though_no_flag_carries_it(self):
        plant_job_turns(self, "plan", JOB_TURNS)
        rec, *_ = self.launch_ok(job="plan", harness="codex", stage="plan")
        self.assertEqual((rec.get("caps") or {}).get("turns_argv"), [])
        text = _prompt_text(self, rec)
        _carries(self, text, f"{BUDGET_HEAD} {JOB_TURNS} turns")
        _carries(self, text, BUDGET_STOP)

    def test_no_cap_means_no_budget_line_rather_than_a_guessed_one(self):
        ls.strip_job_caps(self, "plan")
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertIsNone((rec.get("caps") or {}).get("turns"))
        text = _prompt_text(self, rec)
        self.assertFalse(BUDGET_HEAD in text,
                         f"{BUDGET_HEAD!r} present in a brief with no cap "
                         f"({len(text)} bytes)")


class TheRecordSaysWhetherTheCapWasApplied(ls._TempLaunch):
    """The record said ``turns: 200`` for codex when nothing capped it — a
    claim with no reading behind it. ``turns_applied`` separates what was
    declared from what a flag actually carried (docs/wire.md, absence is
    declared, never inferred)."""

    def test_grok_records_the_cap_as_applied(self):
        plant_job_turns(self, "plan", JOB_TURNS)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertEqual(caps["turns"], JOB_TURNS)
        self.assertIs(_applied(self, caps), True)

    def test_codex_records_the_cap_as_declared_but_not_applied(self):
        plant_job_turns(self, "plan", JOB_TURNS)
        rec, *_ = self.launch_ok(job="plan", harness="codex", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertEqual(caps["turns"], JOB_TURNS)
        self.assertIs(_applied(self, caps), False)
        self.assertIn("codex", caps.get("turns_note", ""))

    def test_no_cap_records_applied_false_beside_turns_none(self):
        ls.strip_job_caps(self, "plan")
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        caps = _turn_caps(self, rec)
        self.assertIsNone(caps["turns"])
        self.assertIs(_applied(self, caps), False)


class EveryJobsTurnCapIsDeclared(unittest.TestCase):
    """caps.turns on every job, read through the file's own text.

    The numbers were chosen 2026-09-12 from the store's message counts
    (Grok author-tests: median 229, p90 325, max 500 messages) and
    extended to the four short jobs on 2026-09-16; the harness turn unit
    is not the store's message unit, so a run under a cap re-measures it.
    The values live in jobs.json, not here."""

    def setUp(self):
        self.world = World(prefix="launch-job-caps-turns-")
        self.addCleanup(self.world.close)
        planted = self.world.plant_text(
            self.world.cwd / "jobs.json",
            ls.JOBS_PATH.read_text(encoding="utf-8"),
            recognisable='"turns"',
        )
        self.jobs = json.loads(planted.read_text(encoding="utf-8"))

    def test_the_capped_jobs_carry_the_named_turns(self):
        carrying = {
            name: spec["caps"]["turns"]
            for name, spec in self.jobs.items()
            if isinstance(spec, dict) and isinstance(spec.get("caps"), dict)
            and "turns" in spec["caps"]
        }
        self.assertEqual(sorted(carrying), sorted(self.jobs),
                         f"a job declares no caps.turns: {sorted(set(self.jobs) - set(carrying))}")
        for name, turns in carrying.items():
            self.assertIsInstance(turns, int, name)
            self.assertNotIsInstance(turns, bool, name)
            self.assertGreater(turns, 0, name)
