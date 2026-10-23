"""A check-less or unreadable PR is a row, not a board-wide refusal.

Defect 10: gh pr checks exiting non-zero with empty stdout and
"no checks reported" is the real answer (no checks), not Unavailable.
One PR's forge error is a cannot-tell row. The overview refuses only
when it could read none of the open PRs.

The fake gh is the fixture at tests/fixtures/gh_stub.py, planted through
the kit's Tools registry with per-PR checks errors in the stub state.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import unittest
from unittest import mock

from support import (
    REPO,
    ROOT,
    in_world,
    load_overview,
    plant_gh,
    ready_pr,
)

from ops.testkit import World

NO_CHECKS_STDERR = "no checks reported on the 'lane/workflow' branch"
AUTH_STDERR = "gh: authentication failed"


def snapshot_live_derivation() -> dict[str, str]:
    """Files the overview fetches for a PR head, from this tree.

    This snapshot's workflows are not the ci-dev.yml pair the neighboring
    head-tree fixture archives, so those helpers cannot run here.
    """
    out: dict[str, str] = {}
    skip_names = {"__pycache__", "wf.db"}
    for rel in (".github/workflows", "ops/devlane/workflow"):
        root = ROOT / rel
        if not root.exists():
            raise AssertionError(f"live tree missing {rel}")
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skip_names or part.endswith(".pyc") for part in path.parts):
                continue
            key = str(path.relative_to(ROOT))
            try:
                out[key] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise AssertionError(f"{key} is not utf-8 ({exc})") from exc
    if ".github/workflows/ci.yml" not in out:
        raise AssertionError("live snapshot missing .github/workflows/ci.yml")
    if "ops/devlane/workflow/gate-kinds/artifact.json" not in out:
        raise AssertionError("live snapshot missing artifact.json")
    return out


def no_checks_error() -> dict:
    return {"rc": 1, "stderr": NO_CHECKS_STDERR, "stdout": ""}


def auth_error() -> dict:
    return {"rc": 1, "stderr": AUTH_STDERR, "stdout": ""}


ROW_HEADER = re.compile(r"^#(\d+)\b")


def row_headers(rendered: str) -> list[int]:
    """Distinct PR row blocks, in render order. A line that merely
    mentions two numbers is not two rows."""
    found = []
    for line in rendered.splitlines():
        match = ROW_HEADER.match(line.lstrip())
        if match:
            found.append(int(match.group(1)))
    return found


def pr_block(rendered: str, number: int) -> list[str]:
    """Lines of the rendered block whose header is this PR number."""
    lines = rendered.splitlines()
    start = None
    for i, line in enumerate(lines):
        match = ROW_HEADER.match(line.lstrip())
        if match and int(match.group(1)) == number:
            start = i
            break
    if start is None:
        raise AssertionError(
            f"no rendered block header is #{number}:\n{rendered}"
        )
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if ROW_HEADER.match(lines[i].lstrip()):
            end = i
            break
    return lines[start:end]


def checks_line_for(rendered: str, number: int) -> str:
    block = pr_block(rendered, number)
    for line in block:
        if "checks:" in line:
            return line
    raise AssertionError(
        f"no checks: line in #{number} block {block!r} of:\n{rendered}"
    )


def blocking_line_for(rendered: str, number: int) -> str:
    block = pr_block(rendered, number)
    for line in block:
        stripped = line.lstrip()
        if stripped.startswith(">>"):
            return stripped
    raise AssertionError(
        f"no >> line for #{number} in:\n{rendered}"
    )


HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
OK_CHECK = "ci"

TWO_JOB_WORKFLOW = """\
name: ci
"on":
  pull_request:
    branches: [dev]
    types: [opened, synchronize, reopened]
  merge_group:
  push:
    branches: [dev]
jobs:
  lane:
    runs-on: self-hosted
    steps:
      - run: echo lane
  app:
    runs-on: self-hosted
    steps:
      - run: echo app
"""

RENAMED_JOB_WORKFLOW = """\
name: ci
"on":
  pull_request:
    branches: [dev]
    types: [opened, synchronize, reopened]
  merge_group:
  push:
    branches: [dev]
jobs:
  audit:
    runs-on: self-hosted
    steps:
      - run: echo audit
  lint:
    runs-on: self-hosted
    steps:
      - run: echo lint
"""


class CollectRows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.overview = load_overview(ROOT, "pr_overview_collect_rows")
        files = snapshot_live_derivation()
        if not files:
            raise AssertionError("live derivation snapshot is empty")
        cls.trees = {HEAD_SHA: files}
        if HEAD_SHA not in cls.trees:
            raise AssertionError("plant: trees missing HEAD_SHA")
        if ".github/workflows/ci.yml" not in cls.trees[HEAD_SHA]:
            raise AssertionError("plant: tree dropped ci.yml")

    def pair(self, *, empty_checks: bool = False) -> list[dict]:
        first_names = [] if empty_checks else [OK_CHECK]
        prs = [
            ready_pr(
                10,
                "workflow without checks",
                "lane/workflow",
                HEAD_SHA,
                first_names,
            ),
            ready_pr(
                11,
                "workflow with checks",
                "lane/ok",
                HEAD_SHA,
                [OK_CHECK],
            ),
        ]
        if empty_checks and prs[0]["checks"]:
            raise AssertionError("plant: PR 10 still has checks")
        return prs

    def run_board(
        self,
        prs,
        check_errors,
        review_errors=None,
        *,
        derive: bool = False,
        trees: dict | None = None,
        world: World | None = None,
        extra: dict[str, str] | None = None,
        argv: list[str] | None = None,
    ):
        """Invoke main() against the planted gh. expected_checks is pinned
        unless derive=True, which runs the real derivation on the planted
        head tree.

        Existing rows tests pin expected_checks because they judge
        buckets/collect, not derivation.
        """
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = argv if argv is not None else ["pr_overview.py", "--repo", REPO]
        code = 0
        used_trees = trees if trees is not None else self.trees
        own = world is None
        if own:
            world = World()
        try:
            gh_extra = plant_gh(
                world, prs, used_trees,
                check_errors=check_errors,
                review_errors=review_errors,
            )
            if extra:
                gh_extra = {**gh_extra, **extra}
            patches = [
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(self.overview.sys, "argv", argv),
            ]
            if not derive:
                patches.insert(
                    0,
                    mock.patch.object(
                        self.overview,
                        "expected_checks",
                        return_value=frozenset({OK_CHECK}),
                    ),
                )
            with in_world(world, gh_extra):
                with contextlib.ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(
                        stderr
                    ):
                        try:
                            returned = self.overview.main()
                            code = 0 if returned is None else int(returned)
                        except SystemExit as exc:
                            code = 0 if exc.code is None else int(exc.code)
            return code, stdout.getvalue(), stderr.getvalue(), world
        finally:
            if own:
                world.close()

    def test_a_pr_with_no_checks_reported_is_a_row_with_no_checks(self):
        prs = self.pair(empty_checks=True)
        self.assertEqual(len(prs), 2)
        self.assertEqual(prs[0]["checks"], [])
        self.assertTrue(prs[1]["checks"])
        errors = {"10": no_checks_error()}
        self.assertIn("no checks reported", errors["10"]["stderr"])
        self.assertEqual(errors["10"]["stdout"], "")
        self.assertNotEqual(errors["10"]["rc"], 0)
        code, stdout, stderr, _world = self.run_board(prs, errors)
        self.assertEqual(code, 0, stderr or stdout)
        self.assertEqual(row_headers(stdout), [10, 11], stdout)
        self.assertIn("checks: no checks", checks_line_for(stdout, 10))
        block10 = pr_block(stdout, 10)
        self.assertFalse(
            any(ROW_HEADER.match(ln.lstrip()) and ln.lstrip().startswith("#11")
                for ln in block10[1:]),
            f"#10's block must not also be #11: {block10!r}",
        )

    def test_one_unreadable_pr_is_a_cannot_tell_row_not_a_refusal(self):
        prs = self.pair()
        self.assertEqual(len(prs), 2)
        errors = {"10": auth_error()}
        self.assertIn("authentication", errors["10"]["stderr"].lower())
        code, stdout, stderr, _world = self.run_board(prs, errors)
        self.assertEqual(code, 0, stderr or stdout)
        self.assertEqual(row_headers(stdout), [10, 11], stdout)
        blocking = blocking_line_for(stdout, 10)
        self.assertTrue(
            blocking.startswith(">> cannot tell"),
            blocking,
        )
        self.assertIn("checks: unreadable", checks_line_for(stdout, 10))

    def test_every_pr_unreadable_refuses_naming_the_count(self):
        prs = self.pair()
        self.assertEqual(len(prs), 2)
        errors = {"10": auth_error(), "11": auth_error()}
        self.assertEqual(len(errors), 2)
        code, stdout, stderr, _world = self.run_board(prs, errors)
        self.assertEqual(code, 2, stderr or stdout)
        self.assertIn("none of 2", stderr)
        self.assertRegex(
            stderr,
            r"first:.*authentication failed",
            f"refusal names the first underlying error: {stderr!r}",
        )
        self.assertEqual(stdout.strip(), "")

    def test_one_unreadable_review_page_is_a_cannot_tell_row_not_a_refusal(self):
        """Adversarial finding 4: review_pages raising is one PR's
        cannot-tell row, not a board-wide exit 2."""
        prs = self.pair()
        self.assertEqual(len(prs), 2)
        errors = {"10": auth_error()}
        self.assertIn("authentication", errors["10"]["stderr"].lower())
        code, stdout, stderr, _world = self.run_board(
            prs, check_errors=None, review_errors=errors,
        )
        self.assertEqual(code, 0, stderr or stdout)
        self.assertEqual(row_headers(stdout), [10, 11], stdout)
        blocking = blocking_line_for(stdout, 10)
        self.assertTrue(
            blocking.startswith(">> cannot tell"),
            blocking,
        )
        self.assertIn("checks: unreadable", checks_line_for(stdout, 10))

    def test_repo_slug_from_conf_is_the_default_repo(self):
        prs = self.pair()
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
        self.assertIn("org/projx", text)
        with World() as world:
            conf = world.plant_text(world.path("lane.conf"), text)
            self.assertEqual(conf.read_text(encoding="utf-8"), text)
            code, stdout, stderr, world = self.run_board(
                prs,
                check_errors=None,
                world=world,
                extra={"LANE_CONF": str(conf)},
                argv=["pr_overview.py"],
            )
            log = "\n".join(json.dumps(c["argv"]) for c in world.tools.calls("gh"))
            self.assertEqual(code, 0, stderr or stdout)
            self.assertIn("org/projx", log)
            self.assertNotIn(
                "uscient/agentscient", log,
                f"overview still used the literal slug: {log!r}",
            )

    def test_derivation_yields_lane_and_app_from_the_planted_workflow(self):
        files = snapshot_live_derivation()
        files[".github/workflows/ci.yml"] = TWO_JOB_WORKFLOW
        self.assertEqual(files[".github/workflows/ci.yml"], TWO_JOB_WORKFLOW)
        self.assertIn("\n  lane:\n", TWO_JOB_WORKFLOW)
        self.assertIn("\n  app:\n", TWO_JOB_WORKFLOW)
        with World() as world:
            for rel, text in files.items():
                path = world.plant_text(world.cwd / rel, text)
                self.assertEqual(path.read_text(encoding="utf-8"), text)
            try:
                names = frozenset(self.overview.expected_checks(world.cwd))
            except self.overview.Unavailable as exc:
                self.fail(
                    "derivation refused planted lane/app jobs: "
                    f"{exc}"
                )
            self.assertEqual(names, frozenset({"lane", "app"}))
            self.assertEqual(len(names), 2)

    def test_derivation_yields_renamed_jobs_from_the_planted_workflow(self):
        files = snapshot_live_derivation()
        files[".github/workflows/ci.yml"] = RENAMED_JOB_WORKFLOW
        self.assertEqual(files[".github/workflows/ci.yml"], RENAMED_JOB_WORKFLOW)
        self.assertIn("\n  audit:\n", RENAMED_JOB_WORKFLOW)
        self.assertIn("\n  lint:\n", RENAMED_JOB_WORKFLOW)
        self.assertNotIn("\n  lane:\n", RENAMED_JOB_WORKFLOW)
        self.assertNotIn("\n  app:\n", RENAMED_JOB_WORKFLOW)
        with World() as world:
            for rel, text in files.items():
                path = world.plant_text(world.cwd / rel, text)
                self.assertEqual(path.read_text(encoding="utf-8"), text)
            try:
                names = frozenset(self.overview.expected_checks(world.cwd))
            except self.overview.Unavailable as exc:
                self.fail(
                    "derivation refused planted audit/lint jobs: "
                    f"{exc}"
                )
            self.assertEqual(names, frozenset({"audit", "lint"}))
            self.assertNotEqual(names, frozenset({"lane", "app"}))
            self.assertEqual(len(names), 2)

    def test_ready_sentence_counts_acceptance_contexts_not_conditional_jobs(self):
        files = snapshot_live_derivation()
        jobs = json.loads(files[".github/workflows/ci.yml"])["jobs"]
        self.assertEqual(set(jobs["lane"]["needs"]), set(jobs) - {"lane"})
        expected = ["lane"]
        trees = {HEAD_SHA: files}
        self.assertIn(".github/workflows/ci.yml", trees[HEAD_SHA])
        prs = [
            ready_pr(
                12,
                "complete dev verification",
                "lane/ci-complete",
                HEAD_SHA,
                expected,
            ),
        ]
        self.assertEqual(
            [c["name"] for c in prs[0]["checks"]],
            expected,
        )
        code, stdout, stderr, _world = self.run_board(
            prs, check_errors=None, derive=True, trees=trees,
        )
        self.assertEqual(code, 0, stderr or stdout)
        blocking = blocking_line_for(stdout, 12)
        self.assertIn("ready to merge", blocking, stdout)
        self.assertRegex(
            blocking,
            rf"all {len(expected)} expected check",
            blocking,
        )


if __name__ == "__main__":
    unittest.main()
