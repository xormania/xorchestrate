"""The ladder driver's pure functions as case tables (plan slice 12):
derive_command, stage_wall, resume_options and retry_granted, each row
addressed by id with its origin, the ledger row or PR it came from
(docs/tests.md rule 2). The subject is loaded once by path and is not
read here; the rows are the sentences of ops/conductor/ladder-lever.md
and the eight regression files these tables replace:
test_ladder_derive_command.py, test_ladder_command_names_the_tree.py,
test_ladder_support_modules.py (LD-15, LD-19), test_ladder_wall.py,
test_ladder_wall_from_job_cap.py (LD-17), test_ladder_retry.py and
test_ladder_ld19_second_producer.py (LD-19). The planted faults are
ops/conductor/tests/ladder_faults.py; the side-by-side record under
ops/conductor/handoff/testkit/ shows every mutant the old files caught
is caught here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
LADDER = REPO / "ops" / "conductor" / "tools" / "ladder.py"


def load_ladder():
    spec = importlib.util.spec_from_file_location("ladder_tables_subject", LADDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop(spec.name, None)
    sys.modules[spec.name] = module  # dataclasses resolve postponed annotations here
    spec.loader.exec_module(module)
    return module


SNAP = "/home/u/.local/state/agentscient/dispatch/20260911T025531Z-tests-grok-5a362c/snapshot"
REL = "ops/conductor/tests/test_command_tree.py"
REL_A = "ops/conductor/tests/test_command_tree_a.py"
REL_B = "packages/relay/tests/test_command_tree_b.py"
DISCOVER = "python3 -m unittest discover -s {dir} -p {name}"


def discover(rel: str) -> str:
    path = Path(rel)
    return DISCOVER.format(dir=path.parent.as_posix(), name=path.name)


# -- derive_command ------------------------------------------------------------------

@dataclass(frozen=True)
class DeriveRow:
    id: str
    origin: str
    artifacts: dict
    command: str | None = None       # the one body make_input records
    stop: str | None = None          # or the Stopped reason
    names: tuple[str, ...] = ()      # substrings the detail or the command must carry
    absent: tuple[str, ...] = ()     # substrings the command must not carry
    note: str | None = None


DERIVED_COMMAND_CASES = [
    DeriveRow("relative-used-as-is", "LD-15", {"t": "ops/conductor/tests/test_x.py"},
              command=discover("ops/conductor/tests/test_x.py")),
    DeriveRow("absolute-snapshot-rewritten", "LD-15",
              {"tests": f"{SNAP}/ops/conductor/tests/test_dispatch_wrapper_answers.py"},
              command=discover("ops/conductor/tests/test_dispatch_wrapper_answers.py"), absent=("/snapshot",)),
    DeriveRow("absolute-outside-snapshot-stops", "LD-15",
              {"tests": "/tmp/elsewhere/ops/conductor/tests/test_x.py"},
              stop="artifacts", names=("/tmp/elsewhere/ops/conductor/tests/test_x.py",)),
    DeriveRow("dotdot-stops", "LD-15", {"tests": "ops/conductor/tests/../../x/tests/test_x.py"},
              stop="artifacts", names=("ops/conductor/tests/../../x/tests/test_x.py",)),
    DeriveRow("non-test-artifacts-ignored", "LD-15",
              {"reds": "/home/u/.local/state/agentscient/dispatch/x/out/reds.log", "t": "ops/conductor/tests/test_x.py"},
              command=discover("ops/conductor/tests/test_x.py")),
    DeriveRow("two-artifacts-join-in-order", "LD-15", {"first": REL_A, "second": REL_B},
              command=f"{discover(REL_A)} && {discover(REL_B)}",
              note="test_ladder_command_names_the_tree.py and test_ladder_support_modules.py both pinned this"),
    DeriveRow("two-artifacts-same-directory-both-run", "20260915T025600Z-adjudicate-codex-68d3d9 rank 5",
              {"alpha": "ops/conductor/tests/test_alpha.py", "beta": "ops/conductor/tests/test_beta.py"},
              command=f"{discover('ops/conductor/tests/test_alpha.py')} && {discover('ops/conductor/tests/test_beta.py')}",
              names=("test_alpha.py", "test_beta.py", " && "),
              note="the retired DerivedCommand.test_two_test_artifacts_become_joined_discover_invocations pinned this; "
                   "M-derive-first-per-directory is its fault"),
    DeriveRow("non-py-ignored", "LD-15", {"notes": "ops/conductor/tests/notes.md", "tree": REL},
              command=discover(REL), absent=("notes.md",)),
    DeriveRow("outside-tests-ignored", "LD-15", {"tool": "ops/conductor/tools/helper.py", "tree": REL},
              command=discover(REL), absent=("helper.py", "conductor/tools")),
    DeriveRow("no-artifact-stops", "LD-15", {}, stop="artifacts"),
    DeriveRow("support-module-not-run", "LD-19",
              {"test": "ops/devlane/dispatch/tests/test_gate.py", "support": "ops/devlane/dispatch/tests/launch_support.py"},
              command=discover("ops/devlane/dispatch/tests/test_gate.py"), absent=("launch_support", "&&")),
    DeriveRow("only-support-module-stops", "LD-19", {"support": "ops/x/tests/launch_support.py"}, stop="artifacts"),
    DeriveRow("helper-beside-test-module", "LD-19", {"helper": "ops/conductor/tests/helper.py", "tests": "ops/conductor/tests/test_x.py"},
              command=discover("ops/conductor/tests/test_x.py"), absent=("helper.py", " && "),
              note="PR 124, the second producer's row"),
    DeriveRow("helper-alone-stops", "LD-19", {"helper": "ops/conductor/tests/helper.py"}, stop="artifacts", note="PR 124"),
    DeriveRow("test-star-outside-tests-ignored", "LD-19",
              {"outside": "ops/conductor/tools/test_x.py", "tests": "ops/conductor/tests/test_y.py"},
              command=discover("ops/conductor/tests/test_y.py"), absent=("test_x.py", "conductor/tools"), note="PR 124"),
    DeriveRow("test-star-outside-tests-alone-stops", "LD-19", {"outside": "ops/conductor/tools/test_x.py"},
              stop="artifacts", note="PR 124"),
]


class _DeriveStub:
    """conf and a make_input that records the body, as the briefs required."""

    conf = {"SCOPE_CAP": "4096"}

    def __init__(self):
        self.bodies = []

    def make_input(self, name, body):
        self.bodies.append(body)
        return Path(name)

    def stop(self, stopped):
        raise stopped


class DerivedCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ladder = load_ladder()

    def test_rows(self):
        ids = [row.id for row in DERIVED_COMMAND_CASES]
        self.assertEqual(len(ids), len(set(ids)), "rows are addressed by id")
        for row in DERIVED_COMMAND_CASES:
            with self.subTest(id=row.id, origin=row.origin):
                stub = _DeriveStub()
                if row.stop is not None:
                    with self.assertRaises(self.ladder.Stopped) as caught:
                        self.ladder.Driver.derive_command(stub, {"artifacts": row.artifacts})
                    self.assertEqual(caught.exception.reason, row.stop)
                    for name in row.names:
                        self.assertIn(name, caught.exception.detail or "")
                    self.assertEqual(stub.bodies, [], "a stop records no command")
                    continue
                self.ladder.Driver.derive_command(stub, {"artifacts": row.artifacts})
                self.assertEqual(stub.bodies, [row.command])
                for name in row.names:
                    self.assertIn(name, stub.bodies[0])
                for absent in row.absent:
                    self.assertNotIn(absent, stub.bodies[0])


# -- stage_wall ------------------------------------------------------------------------

FIVE_JOBS = {
    "author-tests": {"caps": {"timeout": 1800}},
    "implement": {"caps": {"timeout": 3600}},
    "check-tests": {"caps": {"timeout": 1800}},
    "security-audit": {"caps": {"timeout": 1200}},
    "adversarial-review": {"caps": {"timeout": 1800}},
}


@dataclass(frozen=True)
class WallRow:
    id: str
    origin: str
    stage: str
    launched: dict
    expect: object
    jobs: object = None        # a dict to write as jobs.json, a raw string, or None for no registry
    note: str | None = None


STAGE_WALL_CASES = [
    WallRow("record-without-caps-uses-job-cap", "LD-17", "tests-fix", {}, 1800, FIVE_JOBS),
    WallRow("record-with-other-caps-uses-job-cap", "LD-17", "code", {"caps": {"cap-out": 500000}}, 3600, FIVE_JOBS),
    WallRow("security-re-uses-its-job-cap", "LD-17", "security-re", {}, 1200, FIVE_JOBS),
    WallRow("record-cap-wins", "LD-17", "tests-fix", {"caps": {"timeout": 1500.0}}, 1500.0, FIVE_JOBS),
    WallRow("unknown-stage-is-none", "LD-17", "no-such-stage", {}, None, FIVE_JOBS),
    WallRow("missing-registry-is-none", "LD-17", "tests-fix", {}, None, None),
    WallRow("record-cap-wins-over-registry", "LD-17", "tests", {"caps": {"timeout": 17}}, 17,
            {"author-tests": {"caps": {"timeout": 91}}}, note="PR 121"),
    WallRow("tests-uses-its-job-cap", "LD-17", "tests", {}, 23, {"author-tests": {"caps": {"timeout": 23}}}, note="PR 121"),
    WallRow("review-stage-uses-its-job-cap", "LD-17", "security", {}, 29, {"security-audit": {"caps": {"timeout": 29}}}, note="PR 121"),
    WallRow("stage-absent-from-table-is-none", "LD-17", "not-a-dispatch-stage", {}, None,
            {"author-tests": {"caps": {"timeout": 31}}}, note="PR 121"),
    WallRow("unparseable-registry-is-none", "LD-17", "tests", {}, None, "not json", note="PR 121"),
    WallRow("job-without-timeout-is-none", "LD-17", "tests", {}, None, {"author-tests": {"caps": {"memory": 512}}}, note="PR 121"),
]


class _WallStub:
    def __init__(self, worktree):
        self.worktree = worktree
        self.conf = {"WALL_DEFAULT": "900", "LADDER_WAIT_GRACE": "300"}


class StageWall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ladder = load_ladder()

    def test_rows(self):
        ids = [row.id for row in STAGE_WALL_CASES]
        self.assertEqual(len(ids), len(set(ids)), "rows are addressed by id")
        dispatch_stages = {row[0] for row in self.ladder.DISPATCH_STAGES}
        for row in STAGE_WALL_CASES:
            with self.subTest(id=row.id, origin=row.origin), World(prefix="stage-wall-") as world:
                worktree = world.root / "wt"
                if row.jobs is not None:
                    text = row.jobs if isinstance(row.jobs, str) else json.dumps(row.jobs)
                    world.plant_text(worktree / "ops" / "devlane" / "task" / "jobs.json", text)
                if row.id == "stage-absent-from-table-is-none":
                    self.assertNotIn(row.stage, dispatch_stages)
                wall = self.ladder.Driver.stage_wall(_WallStub(worktree), row.stage, row.launched)
                self.assertEqual(wall, row.expect)


# -- resume_options ---------------------------------------------------------------------

BECAUSE = "the derived command discovered a support module"


@dataclass(frozen=True)
class ResumeRow:
    id: str
    origin: str
    args: tuple[str, ...]
    expect: object            # (rescope, retry) or "refused"
    note: str | None = None


RESUME_OPTIONS_CASES = [
    ResumeRow("plain", "LD-19", ("resume", "id"), (False, None)),
    ResumeRow("rescope", "LD-19", ("resume", "id", "--rescope"), (True, None)),
    ResumeRow("retry-with-because", "LD-19", ("resume", "id", "--retry", "code", "--because", BECAUSE), (False, ("code", BECAUSE))),
    ResumeRow("retry-without-a-reason-refused", "LD-19", ("resume", "id", "--retry", "code"), "refused"),
    ResumeRow("reason-without-a-stage-refused", "LD-19", ("resume", "id", "--because", BECAUSE), "refused"),
    ResumeRow("unknown-stage-refused", "LD-19", ("resume", "id", "--retry", "not-a-stage", "--because", BECAUSE), "refused"),
    ResumeRow("empty-reason-refused", "LD-19", ("resume", "id", "--retry", "code", "--because", ""), "refused"),
    ResumeRow("unknown-flag-refused", "LD-19", ("resume", "id", "--nope"), "refused", note="PR 124"),
]


class ResumeOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ladder = load_ladder()

    def test_rows(self):
        ids = [row.id for row in RESUME_OPTIONS_CASES]
        self.assertEqual(len(ids), len(set(ids)), "rows are addressed by id")
        for row in RESUME_OPTIONS_CASES:
            with self.subTest(id=row.id, origin=row.origin):
                if row.expect == "refused":
                    with self.assertRaises(self.ladder.Refused):
                        self.ladder.resume_options(list(row.args))
                else:
                    self.assertEqual(self.ladder.resume_options(list(row.args)), row.expect)


# -- retry_granted --------------------------------------------------------------------

AGENT = "Owner <owner@example.test>"


@dataclass(frozen=True)
class RetryRow:
    id: str
    origin: str
    retry: object                    # the parsed (stage, because) or None
    stage: str
    calls: tuple[bool, ...]          # the expected answer of each successive call
    grant: dict = field(default_factory=dict)   # fields the row's retry grant must carry after the calls
    untouched: tuple[str, ...] = ()  # stages whose rows must carry no grant
    note: str | None = None


RETRY_CASES = [
    RetryRow("no-retry-stays-capped", "LD-19", None, "code", (False,), untouched=("code",)),
    RetryRow("another-stage-does-not-apply", "LD-19", ("tests", "why"), "code", (False,), untouched=("code", "tests")),
    RetryRow("named-stage-once-and-recorded", "LD-19", ("code", "LD-19: the derived command, not the worker"), "code",
             (True, False), grant={"because": "LD-19: the derived command, not the worker", "by": AGENT, "from_attempt": 2}),
    RetryRow("second-call-is-spent", "LD-19", ("code", BECAUSE), "code", (True, False), grant={"because": BECAUSE}, note="PR 124"),
]


class _RetryStub:
    def __init__(self, retry, attempt=2):
        self.retry = retry
        self.agent = AGENT
        self.rows = {}
        self.attempt = attempt

    def row(self, name):
        return self.rows.setdefault(name, {"name": name, "attempt": self.attempt, "outcome": "stopped"})


class RetryGranted(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ladder = load_ladder()

    def test_rows(self):
        ids = [row.id for row in RETRY_CASES]
        self.assertEqual(len(ids), len(set(ids)), "rows are addressed by id")
        for row in RETRY_CASES:
            with self.subTest(id=row.id, origin=row.origin):
                stub = _RetryStub(row.retry)
                answers = tuple(self.ladder.Driver.retry_granted(stub, row.stage) for _ in row.calls)
                self.assertEqual(answers, row.calls)
                for stage in row.untouched:
                    self.assertNotIn("retry", stub.row(stage))
                if row.grant:
                    grant = stub.row(row.stage)["retry"]
                    for key, value in row.grant.items():
                        self.assertEqual(grant.get(key), value, key)
                    self.assertRegex(grant["at"], r"^\d{8}T\d{6}Z$")


if __name__ == "__main__":
    unittest.main()
