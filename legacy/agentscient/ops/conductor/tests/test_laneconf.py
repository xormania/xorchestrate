"""Lane-conf acceptance tests, written from the unit 1 contract.

Contracts (only): the unit 1 contract in the dispatch input
(`contract-laneconf.md` §2) and the inherited
`ops/conductor/instrument-conventions.md`. The instrument
`ops/conductor/tools/laneconf.py` and the tracked `ops/lane.conf` are
the subject and are not read here. Tests plant a scratch conf through
`LANE_CONF` and never write the tracked file.

World owns HOME, the XDG roots and TMPDIR; every subprocess goes
through run with a timeout. A copied laneconf.py needs instrument.py
beside it. When the tool is absent a stub that exits 0 with no output
is staged, so each assertion is the red, not a missing-file error.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import unittest
from pathlib import Path

import worker_conf

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "laneconf.py"
TRACKED_CONF = REPO / "ops" / "lane.conf"
# The tool imports instrument beside itself (HANDOFF-2026-09-13-wall-migration.md).
SIBLINGS = ("instrument.py",)
RUN_CAP = 60

# PIN_CONF_OVERRIDE: tests point at a scratch conf through LANE_CONF.
# The tracked file is never written.
CONF_ENV = "LANE_CONF"
LOCAL_NAME = "lane.local.conf"

# PIN_GET_STDOUT: `get KEY` prints the value and a single trailing
# newline on stdout; stderr is empty on success.
# PIN_SCAN_ROOT: `closure` and `literals` scan the git toplevel of the
# file named by LANE_CONF (else the repository that holds the tracked
# conf). Tests plant a scratch git repo with ops/lane.conf.
# PIN_UNDECLARED_READ: a `get("KEY")` call in lane code outside tests/
# is a read. The finding names the key and the file.
# PIN_EXEMPTION: `ops/devlane/dispatch/record.py` may carry the lane
# kind `lane = "dev"`; a second `"dev"` in that file is a hit.
# PIN_POLICY_PROTOCOL: for the agentscient token only, the exact
# stripped line POLICY_SCHEMA = "agentscient.lane-policy/v1" at
# ops/devlane/dispatch/record.py is permitted. Outer-whitespace
# normalization remains. Every other line, path and token is a hit.
# The two MCP protocol declarations remain allowed.
# PIN_INFRA_SKIP: `ops/devlane/infra/` is skipped and the skip is
# printed, naming that path.

REQUIRED_KEYS = (
    "LANE_NAME",
    "TRUNK_BRANCH",
    "RELEASE_BRANCH",
    "REPO_SLUG",
    "CHECK_NAMES",
    "WF_AGENT_DEFAULT",
    "LANE_AUTHOR",
    "MODEL_GROK",
    "MODEL_CODEX",
    "MODEL_CLAUDE_READ",
    "MODEL_CLAUDE_PLAN",
    "EFFORT_GROK",
    "EFFORT_CODEX",
    "EFFORT_CLAUDE",
    "WALL_DEFAULT",
    "SCOPE_CAP",
)

NAME_KEYS = (
    "LANE_NAME",
    "TRUNK_BRANCH",
    "RELEASE_BRANCH",
    "REPO_SLUG",
    "CHECK_NAMES",
)

REQUIRED_DEFAULTS = {
    "LANE_NAME": "lanetest",
    "TRUNK_BRANCH": "trunktest",
    "RELEASE_BRANCH": "reltest",
    "REPO_SLUG": "org/proj",
    "CHECK_NAMES": "lane app",
    "WF_AGENT_DEFAULT": "Conf Agent <conf@example.invalid>",
    "LANE_AUTHOR": "Lane Owner <lane-owner@example.invalid>",
    "MODEL_GROK": "model-grok",
    "MODEL_CODEX": "model-codex",
    "MODEL_CLAUDE_READ": "model-claude-read",
    "MODEL_CLAUDE_PLAN": "model-claude-plan",
    "EFFORT_GROK": "high",
    "EFFORT_CODEX": "high",
    "EFFORT_CLAUDE": "high",
    "WALL_DEFAULT": "900",
    "SCOPE_CAP": "1024",
}

DECL_RE = re.compile(r"^([A-Z][A-Z0-9_]*)='([^']*)'\s*$")

TWO_JOB_WORKFLOW = json.dumps({"name": "ci", "on": ["push"], "jobs": {
    "lane": {"runs-on": "self-hosted", "steps": [{"run": "echo lane"}]},
    "app": {"runs-on": "self-hosted", "steps": [{"run": "echo app"}]},
}})
THREE_JOB_WORKFLOW = json.dumps({**json.loads(TWO_JOB_WORKFLOW), "jobs": {
    **json.loads(TWO_JOB_WORKFLOW)["jobs"],
    "extra": {"runs-on": "self-hosted", "steps": [{"run": "echo extra"}]},
}})


def conf_text(**overrides: object) -> str:
    omit = tuple(overrides.pop("omit", ()) or ())
    keys = dict(REQUIRED_DEFAULTS)
    for name, value in overrides.items():
        keys[name] = str(value)
    for name in omit:
        keys.pop(name, None)
    lines = ["# scratch lane conf\n"]
    for name, value in keys.items():
        lines.append(f"{name}='{value}'\n")
    return "".join(lines)


def declared_keys(text: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in text.splitlines():
        match = DECL_RE.match(line)
        if match:
            found[match.group(1)] = match.group(2)
    return found


def reader_source(keys: tuple[str, ...] = REQUIRED_KEYS) -> str:
    return "".join(f'get("{name}")\n' for name in keys)


class _LaneconfFixture(unittest.TestCase):
    """Scratch HOME, scratch conf via LANE_CONF, isolated from the tree."""

    def setUp(self):
        self.world = World(prefix="laneconf-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.scratch = self.world.path("scratch")
        self.scratch.mkdir()
        self.conf_path = self.scratch / "lane.conf"
        self.world.plant_text(self.conf_path, conf_text())
        self.staged = self._stage_tool()
        self.assertFalse(
            (self.home / ".local" / "state").exists(),
            "plant: isolated HOME has no state dir",
        )

    def _stage_tool(self) -> Path:
        siblings = SIBLINGS if TOOL.is_file() else ()
        staged = self.world.stage(TOOL, "laneconf.py", siblings=siblings)
        if TOOL.is_file():
            self.assertEqual(
                staged.read_bytes(),
                TOOL.read_bytes(),
                "copy-tool-bytes did not land intact",
            )
            self.assertTrue(
                staged.with_name("instrument.py").is_file(),
                "instrument.py must ride beside the staged copy",
            )
        else:
            # World.stage's absent stand-in exits 0, which would satisfy
            # the exit-0 cases; a non-zero stub makes each invoke assertion
            # the red.
            self.world.plant_text(staged, "import sys\nsys.exit(99)\n")
        return staged

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)
        landed = path.read_bytes()
        self.assertEqual(landed, data, f"plant did not land intact: {path}")
        self.assertEqual(len(landed), len(data))

    def plant_conf(self, path: Path | None = None, **overrides: object) -> Path:
        path = path or self.conf_path
        text = conf_text(**overrides)
        self.world.plant_text(path, text)
        landed = path.read_text(encoding="utf-8")
        self.assertEqual(landed, text, f"conf plant did not land: {path}")
        return path

    def plant_local(self, text: str, *, beside: Path | None = None) -> Path:
        parent = (beside or self.conf_path).parent
        path = parent / LOCAL_NAME
        self.world.plant_text(path, text)
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        return path

    def env(
        self,
        extra: dict[str, str | None] | None = None,
        *,
        lane_conf: Path | str | None = "default",
    ) -> dict[str, str]:
        extra_env: dict[str, str] = {}
        if lane_conf == "default":
            extra_env[CONF_ENV] = str(self.conf_path)
        elif lane_conf is not None:
            extra_env[CONF_ENV] = str(lane_conf)
        if extra:
            for name, value in extra.items():
                if value is not None:
                    extra_env[name] = value
        env = self.world.env(extra=extra_env)
        if extra:
            for name, value in extra.items():
                if value is None:
                    env.pop(name, None)
        return env

    def invoke(
        self,
        argv: list[str],
        *,
        extra: dict[str, str | None] | None = None,
        lane_conf: Path | str | None = "default",
        cwd: Path | None = None,
        timeout: float = RUN_CAP,
    ):
        env = self.env(extra, lane_conf=lane_conf)
        # Default lookup is relative to the tool file. Tree-scan cases
        # with lane_conf is None run the tree's own path (kit gap:
        # World.stage always lands under world.root/stage/). Scratch
        # cases run the staged copy. An absent tool uses the staged stub.
        tool = TOOL if lane_conf is None and TOOL.is_file() else self.staged
        return run(
            [sys.executable, str(tool), *argv],
            cwd=cwd or self.home,
            env=env,
            timeout=timeout,
        )

    def combined(self, proc) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}"

    def assert_refusal(
        self,
        proc,
        class_name: str,
        *,
        named: tuple[str, ...] = (),
    ) -> str:
        err = proc.stderr or ""
        self.assertEqual(proc.exit, 2, err or proc.stdout)
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        lower = lines[0].lower()
        token = class_name.lower().replace("_", "-")
        self.assertIn(token, lower, lines[0])
        for word in ("expected", "found", "needed"):
            self.assertIn(word, lower, lines[0])
        self.assertLess(lower.index("expected"), lower.index("found"), lines[0])
        self.assertLess(lower.index("found"), lower.index("needed"), lines[0])
        self.assertEqual(
            (proc.stdout or "").strip(),
            "",
            "refusal text belongs on stderr, not stdout",
        )
        for item in named:
            self.assertIn(item, lines[0], lines[0])
        return lines[0]

    def assert_get(self, proc, value: str) -> None:
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual((proc.stderr or "").strip(), "")
        self.assertEqual((proc.stdout or "").splitlines(), [value])

    def plant_scratch_repo(
        self,
        files: dict[str, str],
        *,
        conf_overrides: dict[str, object] | None = None,
    ) -> Path:
        n = getattr(self, "_tree_n", 0) + 1
        self._tree_n = n
        repo = self.scratch / f"tree-{n}"
        repo.mkdir()
        self.world.require("git")
        self.world.git("init", "-b", "work", cwd=repo)
        conf = repo / "ops" / "lane.conf"
        self.plant_conf(conf, **(conf_overrides or {}))
        for rel, content in files.items():
            self._plant_bytes(
                repo / rel, content.encode("utf-8"),
            )
        return repo


class ToolAndTrackedConfPresence(_LaneconfFixture):
    """Missing tool and missing tracked conf each count once."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")

    def test_the_tracked_conf_is_present(self):
        self.assertTrue(
            TRACKED_CONF.is_file(),
            f"RED: tracked conf is absent: {TRACKED_CONF}",
        )


class GetVerb(_LaneconfFixture):
    """`get KEY` prints the value and exits 0; a missing key refuses
    class `key` naming the key and the file."""

    def test_get_prints_the_value_and_exits_0(self):
        value = "planted-lane"
        self.plant_conf(LANE_NAME=value)
        proc = self.invoke(["get", "LANE_NAME"])
        self.assert_get(proc, value)

    def test_get_prints_lane_author(self):
        value = "Owner Name <owner@example.invalid>"
        self.assertNotEqual(value, REQUIRED_DEFAULTS["LANE_AUTHOR"])
        self.plant_conf(LANE_AUTHOR=value)
        proc = self.invoke(["get", "LANE_AUTHOR"])
        self.assert_get(proc, value)

    def test_get_does_not_need_identity(self):
        proc = self.invoke(["get", "LANE_NAME"])
        env = self.env()
        self.assertNotIn("WF_AGENT", env)
        self.assert_get(proc, REQUIRED_DEFAULTS["LANE_NAME"])

    def test_a_missing_key_refuses_class_key_naming_the_key_and_file(self):
        missing = "NO_SUCH_KEY"
        self.assertNotIn(missing, declared_keys(
            self.conf_path.read_text(encoding="utf-8"),
        ))
        proc = self.invoke(["get", missing])
        self.assert_refusal(
            proc, "key", named=(missing, str(self.conf_path)),
        )

    def test_absent_local_file_is_not_a_refusal(self):
        local = self.conf_path.parent / LOCAL_NAME
        self.assertFalse(local.exists(), "plant: local file must be absent")
        proc = self.invoke(["get", "LANE_NAME"])
        self.assert_get(proc, REQUIRED_DEFAULTS["LANE_NAME"])


class EnvironmentBeatsConf(_LaneconfFixture):
    """`KEY=env` in the invoking environment wins over the file."""

    def test_environment_beats_conf_for_the_same_key(self):
        self.plant_conf(LANE_NAME="from-conf")
        self.assertNotEqual("from-env", "from-conf")
        proc = self.invoke(
            ["get", "LANE_NAME"],
            extra={"LANE_NAME": "from-env"},
        )
        self.assert_get(proc, "from-env")
        self.assertNotEqual(
            (proc.stdout or "").strip(), "from-conf",
        )


class LocalFile(_LaneconfFixture):
    """The local file beside the conf is sourced after it and wins for
    an allowed key; a name key in the local file refuses `local-key`."""

    def test_local_file_wins_for_an_allowed_key(self):
        self.plant_conf(MODEL_GROK="from-conf")
        local = self.plant_local("MODEL_GROK='from-local'\n")
        self.assertTrue(local.is_file())
        self.assertEqual(local.name, LOCAL_NAME)
        proc = self.invoke(["get", "MODEL_GROK"])
        self.assert_get(proc, "from-local")
        self.assertNotEqual((proc.stdout or "").strip(), "from-conf")

    def test_local_file_setting_a_name_key_refuses_local_key(self):
        for key in NAME_KEYS:
            with self.subTest(key=key):
                self.plant_conf()
                local = self.plant_local(f"{key}='from-local'\n")
                proc = self.invoke(["get", "MODEL_GROK"])
                self.assert_refusal(
                    proc, "local-key", named=(key, str(local)),
                )


class ThreeLayerPrecedence(_LaneconfFixture):
    """Environment beats the local file, which beats the tracked conf."""

    def test_environment_beats_local_file_beats_conf(self):
        self.plant_conf(MODEL_GROK="from-conf")
        local = self.plant_local("MODEL_GROK='from-local'\n")
        self.assertTrue(local.is_file())
        self.assertNotEqual("from-env", "from-local")
        self.assertNotEqual("from-env", "from-conf")
        self.assertNotEqual("from-local", "from-conf")
        proc = self.invoke(
            ["get", "MODEL_GROK"],
            extra={"MODEL_GROK": "from-env"},
        )
        self.assert_get(proc, "from-env")
        self.assertNotEqual((proc.stdout or "").strip(), "from-local")
        self.assertNotEqual((proc.stdout or "").strip(), "from-conf")


class SyntaxRefusal(_LaneconfFixture):
    """A line that is not KEY='literal' or a comment refuses class
    `syntax` naming the line number. Double-quoted, unquoted, export,
    and command-substitution forms are syntax, and must not execute."""

    def test_a_non_key_line_refuses_syntax_naming_the_line_number(self):
        text = (
            "# header\n"
            "LANE_NAME='ok'\n"
            "this is not a key line\n"
        )
        self._plant_bytes(self.conf_path, text.encode("utf-8"))
        landed = self.conf_path.read_text(encoding="utf-8")
        self.assertEqual(landed.splitlines()[2], "this is not a key line")
        proc = self.invoke(["get", "LANE_NAME"])
        line = self.assert_refusal(proc, "syntax")
        self.assertIn("3", line)

    def test_double_quoted_assignment_refuses_syntax(self):
        text = "# header\nMODEL_GROK=\"double-quoted\"\n"
        self._plant_bytes(self.conf_path, text.encode("utf-8"))
        self.assertIn('MODEL_GROK="double-quoted"', self.conf_path.read_text())
        proc = self.invoke(["get", "MODEL_GROK"])
        line = self.assert_refusal(proc, "syntax")
        self.assertIn("2", line)

    def test_unquoted_assignment_refuses_syntax(self):
        text = "# header\nMODEL_GROK=unquoted\n"
        self._plant_bytes(self.conf_path, text.encode("utf-8"))
        self.assertIn("MODEL_GROK=unquoted", self.conf_path.read_text())
        proc = self.invoke(["get", "MODEL_GROK"])
        line = self.assert_refusal(proc, "syntax")
        self.assertIn("2", line)

    def test_export_assignment_refuses_syntax(self):
        text = "# header\nexport MODEL_GROK='from-export'\n"
        self._plant_bytes(self.conf_path, text.encode("utf-8"))
        self.assertIn("export MODEL_GROK=", self.conf_path.read_text())
        proc = self.invoke(["get", "MODEL_GROK"])
        line = self.assert_refusal(proc, "syntax")
        self.assertIn("2", line)

    def test_command_substitution_does_not_run_and_refuses_syntax(self):
        witness = self.scratch / "pwned-syntax"
        self.assertFalse(witness.exists(), "plant: witness must start absent")
        text = (
            "# header\n"
            f'MODEL_GROK="$(printf PWNED > {witness})"\n'
        )
        self._plant_bytes(self.conf_path, text.encode("utf-8"))
        landed = self.conf_path.read_text(encoding="utf-8")
        self.assertIn("$(printf PWNED", landed)
        proc = self.invoke(["get", "MODEL_GROK"])
        self.assert_refusal(proc, "syntax")
        self.assertFalse(
            witness.exists(),
            "command substitution must not execute; "
            f"witness="
            f"{witness.read_text(encoding='utf-8') if witness.exists() else ''!r}",
        )

    def test_get_does_not_need_bash_on_path(self):
        nobash = self.scratch / "nobash-bin"
        nobash.mkdir()
        path = str(nobash)
        self.assertIsNone(shutil.which("bash", path=path))
        self.assertIsNone(shutil.which("sh", path=path))
        proc = self.invoke(["get", "LANE_NAME"], extra={"PATH": path})
        self.assert_get(proc, REQUIRED_DEFAULTS["LANE_NAME"])


class BashPythonParity(_LaneconfFixture):
    """For the tracked ops/lane.conf, bash source and `get KEY` agree
    for every declared key. Count is asserted first."""

    def test_bash_source_and_get_agree_for_every_declared_key(self):
        self.assertTrue(
            TRACKED_CONF.is_file(),
            f"RED: tracked conf is absent: {TRACKED_CONF}",
        )
        text = TRACKED_CONF.read_text(encoding="utf-8")
        keys = declared_keys(text)
        self.assertGreater(len(keys), 0, "tracked conf declares no keys")
        self.assertGreaterEqual(len(keys), len(REQUIRED_KEYS), keys)
        for name, value in keys.items():
            with self.subTest(key=name):
                self.world.require("bash")
                bash = run(
                    [
                        "bash", "-c",
                        "source "
                        + shlex.quote(str(TRACKED_CONF))
                        + f'; printf %s "${{{name}}}"',
                    ],
                    cwd=self.home,
                    env=self.env(lane_conf=None),
                    timeout=RUN_CAP,
                )
                self.assertEqual(bash.exit, 0, bash.stderr)
                self.assertEqual(bash.stdout, value, name)
                proc = self.invoke(
                    ["get", name], lane_conf=TRACKED_CONF,
                )
                self.assert_get(proc, value)
                self.assertEqual((proc.stdout or "").strip(), bash.stdout)


class TrackedConfRequiredKeys(_LaneconfFixture):
    """The tracked ops/lane.conf declares every required key of unit 1
    and no DEFAULT_TIMEOUT."""

    def test_tracked_conf_declares_every_required_key_and_no_default_timeout(
            self):
        self.assertTrue(
            TRACKED_CONF.is_file(),
            f"RED: tracked conf is absent: {TRACKED_CONF}",
        )
        text = TRACKED_CONF.read_text(encoding="utf-8")
        keys = declared_keys(text)
        self.assertGreater(len(keys), 0, "tracked conf declares no keys")
        for name in REQUIRED_KEYS:
            with self.subTest(key=name):
                self.assertIn(name, keys)
        self.assertNotIn("DEFAULT_TIMEOUT", keys)
        self.assertIn("CHECK_NAMES", keys)
        self.assertNotIn("CHECK_NAME", keys)
        self.assertEqual(keys["CHECK_NAMES"].split(), ["lane"])
        for line in text.splitlines():
            if DECL_RE.match(line):
                self.assertFalse(
                    line.startswith("DEFAULT_TIMEOUT="),
                    line,
                )


class ClosureVerb(_LaneconfFixture):
    """`closure` exits 0 on the tree; unread and undeclared keys and a
    CHECK_NAMES mismatch against workflow jobs each exit 1 naming what
    they found."""

    def test_closure_exits_0_on_the_tree(self):
        proc = self.invoke(["closure"], lane_conf=None, cwd=REPO)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_declared_key_no_code_reads_exits_1_naming_it(self):
        orphan = "ORPHAN_KEY"
        repo = self.plant_scratch_repo(
            {"ops/conductor/tools/reads.py": reader_source()},
            conf_overrides={orphan: "orphan-value"},
        )
        conf = repo / "ops" / "lane.conf"
        planted = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertIn(orphan, planted)
        reader = (repo / "ops/conductor/tools/reads.py").read_text(
            encoding="utf-8",
        )
        self.assertNotIn(orphan, reader)
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(orphan, text)

    def test_code_reading_an_undeclared_key_exits_1_naming_key_and_file(self):
        ghost = "GHOST_KEY"
        rel = "ops/bin/ghost.py"
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                rel: f'get("{ghost}")\n',
            },
        )
        conf = repo / "ops" / "lane.conf"
        planted = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertNotIn(ghost, planted)
        ghost_file = repo / rel
        self.assertIn(ghost, ghost_file.read_text(encoding="utf-8"))
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(ghost, text)
        self.assertIn(rel, text)

    def test_undeclared_shell_and_env_reads_name_each_key_and_file(self):
        ghost_shell = "GHOST_SHELL"
        ghost_env = "GHOST_ENV"
        ghost_get = "GHOST_GET"
        rel_shell = "ops/bin/ghost.sh"
        rel_env = "ops/bin/ghost_env.py"
        rel_get = "ops/bin/ghost_get.py"
        rel_clean_sh = "ops/bin/clean.sh"
        rel_clean_py = "ops/bin/clean.py"
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                rel_clean_sh: "echo ok\n",
                rel_clean_py: "print('ok')\n",
                rel_shell: 'echo "${' + ghost_shell + '}"\n',
                rel_env: (
                    "import os\n"
                    f"os.environ[{ghost_env!r}]\n"
                ),
                rel_get: (
                    "import os\n"
                    f"os.environ.get({ghost_get!r})\n"
                ),
            },
        )
        conf = repo / "ops" / "lane.conf"
        planted = declared_keys(conf.read_text(encoding="utf-8"))
        for ghost in (ghost_shell, ghost_env, ghost_get):
            self.assertNotIn(ghost, planted)
        self.assertIn(
            ghost_shell,
            (repo / rel_shell).read_text(encoding="utf-8"),
        )
        self.assertIn(
            ghost_env,
            (repo / rel_env).read_text(encoding="utf-8"),
        )
        self.assertIn(
            ghost_get,
            (repo / rel_get).read_text(encoding="utf-8"),
        )
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(ghost_shell, text)
        self.assertIn(rel_shell, text)
        self.assertIn(ghost_env, text)
        self.assertIn(rel_env, text)
        self.assertIn(ghost_get, text)
        self.assertIn(rel_get, text)

    def test_matching_jobs_and_check_names_exit_0(self):
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                ".github/workflows/ci.yml": TWO_JOB_WORKFLOW,
            },
            conf_overrides={"CHECK_NAMES": "lane app"},
        )
        conf = repo / "ops" / "lane.conf"
        keys = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertEqual(keys["CHECK_NAMES"].split(), ["lane", "app"])
        yml = (repo / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(yml, TWO_JOB_WORKFLOW)
        self.assertIn("lane", json.loads(yml)["jobs"])
        self.assertIn("app", json.loads(yml)["jobs"])
        self.assertNotIn("extra", json.loads(yml)["jobs"])
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_planted_third_job_exits_1_naming_the_job(self):
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                ".github/workflows/ci.yml": THREE_JOB_WORKFLOW,
            },
            conf_overrides={"CHECK_NAMES": "lane app"},
        )
        conf = repo / "ops" / "lane.conf"
        keys = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertEqual(keys["CHECK_NAMES"].split(), ["lane", "app"])
        self.assertNotIn("extra", keys["CHECK_NAMES"].split())
        yml = (repo / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(yml, THREE_JOB_WORKFLOW)
        self.assertIn("extra", json.loads(yml)["jobs"])
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn("extra", text)

    def test_final_gate_covers_conditional_jobs_but_not_unrelated_jobs(self):
        jobs = {"scope": {}, "dev-lane": {}, "prod-lane": {},
                "lane": {"needs": ["scope", "dev-lane", "prod-lane"]}}
        repo = self.plant_scratch_repo(
            {"ops/conductor/tools/reads.py": reader_source(),
             ".github/workflows/ci.yml": json.dumps({"jobs": jobs})},
            conf_overrides={"CHECK_NAMES": "lane"},
        )
        conf = repo / "ops/lane.conf"
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        jobs["uncovered"] = {}
        (repo / ".github/workflows/ci.yml").write_text(json.dumps({"jobs": jobs}))
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 1, self.combined(proc))
        self.assertIn("uncovered", self.combined(proc))

    def test_a_planted_extra_check_name_exits_1_naming_the_name(self):
        extra = "ghost-job"
        self.assertNotEqual(extra, "lane")
        self.assertNotEqual(extra, "app")
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                ".github/workflows/ci.yml": TWO_JOB_WORKFLOW,
            },
            conf_overrides={"CHECK_NAMES": f"lane app {extra}"},
        )
        conf = repo / "ops" / "lane.conf"
        keys = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertEqual(keys["CHECK_NAMES"].split(), ["lane", "app", extra])
        yml = (repo / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(yml, TWO_JOB_WORKFLOW)
        self.assertNotIn(extra, yml)
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(extra, text)

    def test_same_cardinality_mismatched_name_exits_1_naming_the_name(self):
        extra = "ghost-job"
        self.assertNotEqual(extra, "lane")
        self.assertNotEqual(extra, "app")
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                ".github/workflows/ci.yml": TWO_JOB_WORKFLOW,
            },
            conf_overrides={"CHECK_NAMES": f"lane {extra}"},
        )
        conf = repo / "ops" / "lane.conf"
        keys = declared_keys(conf.read_text(encoding="utf-8"))
        names = keys["CHECK_NAMES"].split()
        self.assertEqual(names, ["lane", extra])
        self.assertEqual(len(names), 2)
        yml = (repo / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertEqual(yml, TWO_JOB_WORKFLOW)
        self.assertIn("lane", json.loads(yml)["jobs"])
        self.assertIn("app", json.loads(yml)["jobs"])
        self.assertNotIn(extra, json.loads(yml)["jobs"])
        self.assertEqual(len(names), 2)
        self.assertNotEqual(set(names), {"lane", "app"})
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(extra, text)
        self.assertIn("app", text)


class ExternalEnvKeys(_LaneconfFixture):
    """A key the finite controller supplies to its children is read by
    code and declared nowhere in lane.conf, and that is not a finding.
    The fixture set: the two harness session ids and the identity
    override as the faults the exemption must swallow, a near neighbour
    spelled one word short as the fault it must still catch."""

    ENV_READER = "ops/bin/session.py"

    def read_of(self, key: str) -> dict[str, str]:
        return {
            "ops/conductor/tools/reads.py": reader_source(),
            self.ENV_READER: f"import os\nos.environ.get({key!r})\n",
        }

    def closure_over_a_read_of(self, key: str):
        repo = self.plant_scratch_repo(self.read_of(key))
        conf = repo / "ops" / "lane.conf"
        self.assertNotIn(key, declared_keys(conf.read_text(encoding="utf-8")))
        self.assertIn(
            key,
            (repo / self.ENV_READER).read_text(encoding="utf-8"),
            "plant: the reader does not read the key",
        )
        return self.invoke(["closure"], lane_conf=conf, cwd=repo)

    def test_the_harness_session_id_is_external_and_closes(self):
        proc = self.closure_over_a_read_of("CLAUDE_SESSION_ID")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_the_code_session_id_the_harness_supplies_is_external_and_closes(self):
        """CLAUDE_CODE_SESSION_ID is what Claude Code puts in a child's
        environment; the identity hook reads it and lane.conf never
        declares it, so the closure must pass it by name."""
        proc = self.closure_over_a_read_of("CLAUDE_CODE_SESSION_ID")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_the_identity_override_is_external_and_closes(self):
        """WF_AGENT_OVERRIDE is put on the command by whoever launches a
        child and outranks WF_AGENT, which is external already; the
        override is read the same way and declared the same nowhere."""
        proc = self.closure_over_a_read_of("WF_AGENT_OVERRIDE")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_near_neighbour_of_an_external_key_is_still_a_finding(self):
        """The clean neighbour: the exemption is a list of names, not a
        prefix and not a file the scanner skips."""
        ghost = "CLAUDE_CODE_SESSION"
        proc = self.closure_over_a_read_of(ghost)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(ghost, text)
        self.assertIn(self.ENV_READER, text)


class LiteralsVerb(_LaneconfFixture):
    """`literals` exits 0 on the tree; a scratch lane file carrying a
    forbidden token exits 1 naming file and line. The record.py lane
    kind is exempt; a second `"dev"` in that file is not. Infra is
    skipped and the skip is printed."""

    def test_literals_exits_0_on_the_tree(self):
        proc = self.invoke(["literals"], lane_conf=None, cwd=REPO)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_benign_scratch_tree_exits_0(self):
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                "ops/bin/clean.sh": "echo ok\n",
                "ops/conductor/tools/clean.py": "print('ok')\n",
            },
        )
        conf = repo / "ops" / "lane.conf"
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_check_names_are_not_project_identity_literals(self):
        for name in ("lane", "checkx"):
            with self.subTest(name=name):
                repo = self.plant_scratch_repo(
                    {"ops/conductor/tools/reads.py": reader_source(),
                     "ops/bin/clean.sh": f"echo {name}\n"},
                    conf_overrides={"CHECK_NAMES": name},
                )
                proc = self.invoke(["literals"], lane_conf=repo / "ops/lane.conf", cwd=repo)
                self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_scratch_lane_file_carrying_a_forbidden_token_exits_1(self):
        configured = {
            "LANE_NAME": "lanex",
            "TRUNK_BRANCH": "trunkx",
            "RELEASE_BRANCH": "relx",
            "REPO_SLUG": "org/projx",
            "CHECK_NAMES": "checkx",
        }
        plants = (
            ("ops/bin/legacy-dev.sh", '"dev"'),
            ("ops/conductor/tools/legacy_dev.py", '"dev"'),
            ("ops/bin/legacy-origin.sh", "origin/dev"),
            ("ops/conductor/tools/legacy_origin.py", "origin/dev"),
            ("ops/bin/legacy-lane.sh", "'agentscient'"),
            ("ops/conductor/tools/legacy_lane.py", "'agentscient'"),
            ("ops/bin/legacy-slug.sh", "uscient/agentscient"),
            ("ops/conductor/tools/legacy_slug.py", "uscient/agentscient"),
            ("ops/bin/legacy-home.sh", "/home/work/"),
            ("ops/conductor/tools/legacy_home.py", "/home/work/"),
            ("ops/bin/cfg-lane.sh", configured["LANE_NAME"]),
            ("ops/conductor/tools/cfg_lane.py", configured["LANE_NAME"]),
            ("ops/bin/cfg-trunk.sh", configured["TRUNK_BRANCH"]),
            ("ops/conductor/tools/cfg_trunk.py", configured["TRUNK_BRANCH"]),
            ("ops/bin/cfg-rel.sh", configured["RELEASE_BRANCH"]),
            ("ops/conductor/tools/cfg_rel.py", configured["RELEASE_BRANCH"]),
            ("ops/bin/cfg-slug.sh", configured["REPO_SLUG"]),
            ("ops/conductor/tools/cfg_slug.py", configured["REPO_SLUG"]),
        )
        self.assertEqual(len(plants), 18, plants)
        for rel, token in plants:
            with self.subTest(rel=rel, token=token):
                if rel.endswith(".py"):
                    body = (
                        "print('ok')\n"
                        "print('still-ok')\n"
                        f"marker = {token!r}\n"
                    )
                else:
                    body = (
                        "echo ok\n"
                        "echo still-ok\n"
                        f"echo {token}\n"
                    )
                repo = self.plant_scratch_repo(
                    {
                        "ops/conductor/tools/reads.py": reader_source(),
                        rel: body,
                    },
                    conf_overrides=configured,
                )
                conf = repo / "ops" / "lane.conf"
                spread = repo / rel
                landed = spread.read_text(encoding="utf-8")
                self.assertIn(token, landed)
                self.assertGreaterEqual(len(landed.splitlines()), 3)
                line_no = 3
                self.assertIn(token, landed.splitlines()[line_no - 1])
                proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
                text = self.combined(proc)
                self.assertEqual(proc.exit, 1, text)
                self.assertIn(rel, text)
                self.assertIn(str(line_no), text)
                self.assertIn(token, text)

    def test_record_py_lane_kind_is_exempt_and_a_second_dev_fails(self):
        rel = "ops/devlane/dispatch/record.py"
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                rel: 'lane = "dev"\n',
            },
        )
        conf = repo / "ops" / "lane.conf"
        planted = (repo / rel).read_text(encoding="utf-8")
        self.assertEqual(planted.count('"dev"'), 1)
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))

        second = 'lane = "dev"\nother = "dev"\n'
        self._plant_bytes((repo / rel), second.encode("utf-8"))
        landed = (repo / rel).read_text(encoding="utf-8")
        self.assertEqual(landed.count('"dev"'), 2)
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(rel, text)

    def test_symfony_environment_names_do_not_exempt_branch_literals(self):
        rel = "ops/conductor/tools/project_providers/symfony.py"
        environments = (
            'self.env = config.get("environment", "dev")\n'
            'if self.env not in {"dev", "test"}:\n'
        )
        repo = self.plant_scratch_repo({rel: environments})
        conf = repo / "ops/lane.conf"
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))

        # A branch in the provider, another statement on the allowed line,
        # and the same text in a different owner must still be findings.
        for path, content in (
            (rel, environments + 'branch = "dev"\n'),
            (rel, environments.splitlines()[0] + '; branch = "dev"\n'),
            ("ops/conductor/tools/unrelated.py", environments),
        ):
            with self.subTest(path=path, content=content):
                self._plant_bytes(repo / rel, environments.encode())
                self._plant_bytes(repo / path, content.encode())
                proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
                self.assertEqual(proc.exit, 1, self.combined(proc))
                self.assertIn(path, self.combined(proc))

    def test_infra_is_skipped_and_the_skip_is_printed(self):
        rel = "ops/devlane/infra/gitea/bootstrap.sh"
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                rel: "user=agentscient\n",
            },
        )
        conf = repo / "ops" / "lane.conf"
        planted = (repo / rel).read_text(encoding="utf-8")
        self.assertIn("agentscient", planted)
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 0, text)
        self.assertIn("ops/devlane/infra", text)


class PolicyProtocolLiterals(_LaneconfFixture):
    """Exact policy protocol identity at record.py, from the bounded
    repair ruling. LANE_NAME is agentscient. The real literals CLI
    is the subject. Plants are proven; hits name token, path and line.
    """

    POLICY_REL = "ops/devlane/dispatch/record.py"
    POLICY_LINE = 'POLICY_SCHEMA = "agentscient.lane-policy/v1"'
    POLICY_TOKEN = "agentscient"
    MCP_SCHEMA_REL = "ops/conductor/tools/lane_read.py"
    MCP_SCHEMA_LINE = 'SCHEMA = "agentscient.lane/v1"'
    MCP_SERVER_REL = "ops/conductor/tools/lane_mcp.py"
    MCP_SERVER_LINE = (
        '"serverInfo": {"name": "agentscient-lane", "version": "1"},'
    )

    def agentscient_conf(self, **overrides: object) -> dict[str, object]:
        conf: dict[str, object] = {
            "LANE_NAME": "agentscient",
            "TRUNK_BRANCH": "trunkx",
            "RELEASE_BRANCH": "relx",
            "REPO_SLUG": "org/projx",
            "CHECK_NAMES": "checkx",
        }
        conf.update(overrides)
        return conf

    def plant_policy_repo(
        self,
        record_body: str,
        extra: dict[str, str] | None = None,
        **conf_overrides: object,
    ) -> tuple[Path, Path]:
        files = {
            "ops/conductor/tools/reads.py": reader_source(),
            self.POLICY_REL: record_body,
        }
        if extra:
            files.update(extra)
        repo = self.plant_scratch_repo(
            files,
            conf_overrides=self.agentscient_conf(**conf_overrides),
        )
        conf = repo / "ops" / "lane.conf"
        planted = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertEqual(planted["LANE_NAME"], "agentscient")
        return repo, conf

    def assert_forbidden(
        self,
        proc,
        rel: str,
        line_no: int,
        token: str,
    ) -> str:
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        hit = f"{rel}:{line_no}: forbidden literal {token}"
        self.assertIn(hit, text)
        return text

    def test_exact_policy_schema_declaration_at_record_py_exits_0(self):
        body = self.POLICY_LINE + "\n"
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed, body)
        self.assertEqual(landed.splitlines()[0], self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, landed.splitlines()[0])
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_outer_whitespace_policy_schema_declaration_at_record_py_exits_0(
            self):
        body = f"  {self.POLICY_LINE}  \n"
        self.assertNotEqual(body, self.POLICY_LINE + "\n")
        self.assertEqual(body.strip(), self.POLICY_LINE)
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed, body)
        self.assertEqual(landed.splitlines()[0].strip(), self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, landed)
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_exact_policy_schema_declaration_after_benign_lines_exits_0(self):
        benign = (
            "print('ok')\n"
            "print('still-ok')\n"
        )
        body = benign + self.POLICY_LINE + "\n"
        self.assertNotEqual(body, self.POLICY_LINE + "\n")
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed, body)
        lines = landed.splitlines()
        self.assertGreater(len(lines), 1)
        line_no = 3
        self.assertNotEqual(line_no, 1)
        self.assertEqual(lines[0], "print('ok')")
        self.assertEqual(lines[1], "print('still-ok')")
        self.assertEqual(lines[line_no - 1], self.POLICY_LINE)
        self.assertNotIn(self.POLICY_TOKEN, lines[0])
        self.assertNotIn(self.POLICY_TOKEN, lines[1])
        self.assertIn(self.POLICY_TOKEN, lines[line_no - 1])
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_exact_policy_schema_declaration_at_another_path_exits_1(self):
        rel = "ops/conductor/tools/policy_decl.py"
        self.assertNotEqual(rel, self.POLICY_REL)
        body = self.POLICY_LINE + "\n"
        repo, conf = self.plant_policy_repo(
            "print('ok')\n",
            extra={rel: body},
        )
        other = repo / rel
        landed = other.read_text(encoding="utf-8")
        self.assertEqual(landed, body)
        self.assertEqual(landed.splitlines()[0], self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, landed.splitlines()[0])
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, rel, line_no, self.POLICY_TOKEN)

    def test_exact_policy_schema_declaration_at_dispatch_sibling_exits_1(self):
        rel = "ops/devlane/dispatch/schema.py"
        self.assertNotEqual(rel, self.POLICY_REL)
        self.assertEqual(Path(rel).parent, Path(self.POLICY_REL).parent)
        self.assertNotEqual(Path(rel).name, Path(self.POLICY_REL).name)
        body = self.POLICY_LINE + "\n"
        repo, conf = self.plant_policy_repo(
            "print('ok')\n",
            extra={rel: body},
        )
        other = repo / rel
        landed = other.read_text(encoding="utf-8")
        self.assertEqual(landed, body)
        self.assertEqual(landed.splitlines()[0], self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, landed.splitlines()[0])
        record = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertNotEqual(record, body)
        self.assertNotIn(self.POLICY_LINE, record.splitlines())
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, rel, line_no, self.POLICY_TOKEN)

    def test_exact_policy_schema_declaration_at_other_record_py_exits_1(self):
        rel = "ops/conductor/tools/record.py"
        self.assertNotEqual(rel, self.POLICY_REL)
        self.assertEqual(Path(rel).name, Path(self.POLICY_REL).name)
        self.assertNotEqual(Path(rel).parent, Path(self.POLICY_REL).parent)
        body = self.POLICY_LINE + "\n"
        repo, conf = self.plant_policy_repo(
            "print('ok')\n",
            extra={rel: body},
        )
        other = repo / rel
        landed = other.read_text(encoding="utf-8")
        self.assertEqual(landed, body)
        self.assertEqual(landed.splitlines()[0], self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, landed.splitlines()[0])
        record = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertNotEqual(record, body)
        self.assertNotIn(self.POLICY_LINE, record.splitlines())
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, rel, line_no, self.POLICY_TOKEN)

    def test_altered_policy_schema_name_exits_1(self):
        line = 'POLICY_SCHEMA_NAME = "agentscient.lane-policy/v1"'
        self.assertNotEqual(line, self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, line)
        body = line + "\n"
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed.splitlines()[0], line)
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, self.POLICY_REL, line_no, self.POLICY_TOKEN)

    def test_altered_policy_schema_value_exits_1(self):
        line = 'POLICY_SCHEMA = "agentscient.lane-policy/v2"'
        self.assertNotEqual(line, self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, line)
        body = line + "\n"
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed.splitlines()[0], line)
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, self.POLICY_REL, line_no, self.POLICY_TOKEN)

    def test_policy_schema_declaration_with_appended_comment_exits_1(self):
        line = self.POLICY_LINE + "  # protocol identity"
        self.assertNotEqual(line.strip(), self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, line)
        body = line + "\n"
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed.splitlines()[0], line)
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, self.POLICY_REL, line_no, self.POLICY_TOKEN)

    def test_policy_schema_declaration_with_appended_statement_exits_1(self):
        line = self.POLICY_LINE + "; unused = 1"
        self.assertNotEqual(line.strip(), self.POLICY_LINE)
        self.assertIn(self.POLICY_TOKEN, line)
        body = line + "\n"
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed.splitlines()[0], line)
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, self.POLICY_REL, line_no, self.POLICY_TOKEN)

    def test_permitted_declaration_plus_another_agentscient_use_exits_1(self):
        extra_line = 'marker = "agentscient"'
        body = self.POLICY_LINE + "\n" + extra_line + "\n"
        repo, conf = self.plant_policy_repo(body)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        lines = landed.splitlines()
        self.assertEqual(lines[0], self.POLICY_LINE)
        self.assertEqual(lines[1], extra_line)
        self.assertIn(self.POLICY_TOKEN, lines[1])
        self.assertNotEqual(lines[1], self.POLICY_LINE)
        line_no = 2
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        text = self.assert_forbidden(
            proc, self.POLICY_REL, line_no, self.POLICY_TOKEN,
        )
        self.assertNotIn(
            f"{self.POLICY_REL}:1: forbidden literal {self.POLICY_TOKEN}",
            text,
        )

    def test_permitted_declaration_plus_another_forbidden_token_exits_1(self):
        rel = "ops/bin/legacy-origin.sh"
        token = "origin/dev"
        extra = (
            "echo ok\n"
            "echo still-ok\n"
            f"echo {token}\n"
        )
        repo, conf = self.plant_policy_repo(
            self.POLICY_LINE + "\n",
            extra={rel: extra},
        )
        record = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(record.splitlines()[0], self.POLICY_LINE)
        spread = repo / rel
        landed = spread.read_text(encoding="utf-8")
        self.assertEqual(landed, extra)
        line_no = 3
        self.assertIn(token, landed.splitlines()[line_no - 1])
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        text = self.assert_forbidden(proc, rel, line_no, token)
        self.assertNotIn(
            f"{self.POLICY_REL}:1: forbidden literal {self.POLICY_TOKEN}",
            text,
        )

    def test_distinct_configured_token_inside_the_declaration_is_reported(
            self):
        token = "lane-policy"
        self.assertIn(token, self.POLICY_LINE)
        self.assertNotEqual(token, self.POLICY_TOKEN)
        body = self.POLICY_LINE + "\n"
        repo, conf = self.plant_policy_repo(
            body, TRUNK_BRANCH=token,
        )
        planted = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertEqual(planted["TRUNK_BRANCH"], token)
        landed = (repo / self.POLICY_REL).read_text(encoding="utf-8")
        self.assertEqual(landed.splitlines()[0], self.POLICY_LINE)
        self.assertIn(token, landed.splitlines()[0])
        line_no = 1
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assert_forbidden(proc, self.POLICY_REL, line_no, token)

    def test_existing_mcp_declaration_allowances_remain_valid(self):
        files = {
            "ops/conductor/tools/reads.py": reader_source(),
            self.MCP_SCHEMA_REL: self.MCP_SCHEMA_LINE + "\n",
            self.MCP_SERVER_REL: self.MCP_SERVER_LINE + "\n",
        }
        repo = self.plant_scratch_repo(
            files,
            conf_overrides=self.agentscient_conf(),
        )
        conf = repo / "ops" / "lane.conf"
        planted = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertEqual(planted["LANE_NAME"], "agentscient")
        schema = (repo / self.MCP_SCHEMA_REL).read_text(encoding="utf-8")
        server = (repo / self.MCP_SERVER_REL).read_text(encoding="utf-8")
        self.assertEqual(schema.splitlines()[0], self.MCP_SCHEMA_LINE)
        self.assertEqual(server.splitlines()[0], self.MCP_SERVER_LINE)
        self.assertIn(self.POLICY_TOKEN, schema)
        self.assertIn(self.POLICY_TOKEN, server)
        proc = self.invoke(["literals"], lane_conf=conf, cwd=repo)
        self.assertEqual(proc.exit, 0, self.combined(proc))


class GetWithoutLaneConfUsesTrackedFile(_LaneconfFixture):
    """Readers locate the tracked conf relative to the tool, never cwd."""

    def test_get_without_lane_conf_reads_the_tracked_file_from_any_cwd(self):
        self.assertTrue(
            TRACKED_CONF.is_file(),
            f"RED: tracked conf is absent: {TRACKED_CONF}",
        )
        keys = declared_keys(TRACKED_CONF.read_text(encoding="utf-8"))
        self.assertIn("LANE_NAME", keys)
        proc = self.invoke(
            ["get", "LANE_NAME"],
            lane_conf=None,
            cwd=self.home,
        )
        self.assert_get(proc, keys["LANE_NAME"])


class TrackedConfMove(_LaneconfFixture):
    """dispatch.conf moved to ops/lane.conf; the local file is gitignored."""

    def test_lane_conf_is_tracked_and_dispatch_conf_is_not(self):
        self.world.require("git")
        rel = TRACKED_CONF.relative_to(REPO).as_posix()
        listed_new = run(
            ["git", "ls-files", "--", rel],
            cwd=REPO,
            env=self.world.env(),
            timeout=RUN_CAP,
        )
        listed_old = run(
            ["git", "ls-files", "--", "ops/conductor/tools/dispatch.conf"],
            cwd=REPO,
            env=self.world.env(),
            timeout=RUN_CAP,
        )
        self.assertEqual(listed_new.exit, 0, listed_new.stderr)
        self.assertEqual(listed_old.exit, 0, listed_old.stderr)
        self.assertEqual(listed_new.stdout.strip(), rel)
        self.assertEqual(
            listed_old.stdout.strip(),
            "",
            "dispatch.conf must be moved, not copied: "
            f"ls-files={listed_old.stdout!r}",
        )

    def test_lane_local_conf_is_gitignored(self):
        self.world.require("git")
        proc = run(
            ["git", "check-ignore", "-v", "--", "ops/lane.local.conf"],
            cwd=REPO,
            env=self.world.env(),
            timeout=RUN_CAP,
        )
        self.assertEqual(
            proc.exit, 0,
            proc.stderr or proc.stdout or "check-ignore did not match",
        )
        self.assertIn("lane.local.conf", proc.stdout)


class IteratedConfKeys(_LaneconfFixture):
    """S2: a CONF_KEYS iteration is a read of each member. Closure
    names an undeclared key reached that way, and the file.
    """

    def test_iterated_conf_keys_count_as_reads_for_closure(self):
        ghost = "GHOST_ITERATED"
        rel = "ops/conductor/tools/iter_keys.py"
        src = (
            f"CONF_KEYS = ({ghost!r},)\n"
            "def get(key):\n"
            "    return key\n"
            "def conf_value(key):\n"
            "    return get(key)\n"
            "values = {key: conf_value(key) for key in CONF_KEYS}\n"
        )
        repo = self.plant_scratch_repo(
            {
                "ops/conductor/tools/reads.py": reader_source(),
                rel: src,
            },
        )
        conf = repo / "ops" / "lane.conf"
        planted = declared_keys(conf.read_text(encoding="utf-8"))
        self.assertNotIn(ghost, planted)
        self.assertIn(
            ghost,
            (repo / rel).read_text(encoding="utf-8"),
        )
        proc = self.invoke(["closure"], lane_conf=conf, cwd=repo)
        text = self.combined(proc)
        self.assertEqual(proc.exit, 1, text)
        self.assertIn(ghost, text)
        self.assertIn(rel, text)


class Unit2DriverKeys(_LaneconfFixture):
    """Unit 2 required keys: the tracked conf declares them and closure
    finds the ladder driver as their reader.
    """

    DRIVER_KEYS = (
        "WORKTREE_ROOT",
        "ADDRESS_GROK",
        "ADDRESS_CODEX",
        "ADDRESS_CLAUDE",
    )
    DRIVER = REPO / "ops" / "conductor" / "tools" / "ladder.py"

    def test_tracked_conf_declares_the_driver_keys(self):
        self.assertTrue(
            TRACKED_CONF.is_file(),
            f"RED: tracked conf is absent: {TRACKED_CONF}",
        )
        keys = declared_keys(TRACKED_CONF.read_text(encoding="utf-8"))
        self.assertGreater(len(keys), 0, "tracked conf declares no keys")
        for name in self.DRIVER_KEYS:
            with self.subTest(key=name):
                self.assertIn(name, keys)

    def test_closure_finds_the_driver_as_their_reader(self):
        self.assertTrue(
            TRACKED_CONF.is_file(),
            f"RED: tracked conf is absent: {TRACKED_CONF}",
        )
        keys = declared_keys(TRACKED_CONF.read_text(encoding="utf-8"))
        for name in self.DRIVER_KEYS:
            self.assertIn(name, keys)
        self.assertTrue(
            self.DRIVER.is_file(),
            f"RED: driver is absent: {self.DRIVER}",
        )
        src = self.DRIVER.read_text(encoding="utf-8")
        for name in self.DRIVER_KEYS:
            with self.subTest(key=name):
                self.assertIn(name, src)
        proc = self.invoke(["closure"], lane_conf=None, cwd=REPO)
        self.assertEqual(proc.exit, 0, self.combined(proc))


class _WorkerLaneconfFixture(_LaneconfFixture):
    """Scratch PATH bins for the provisional worker CLI."""

    def setUp(self):
        super().setUp()
        names = worker_conf.DEFAULT_WORKERS + ("agy",)
        self.bins = {
            name: self.world.tools.plant(name, exit=0) for name in names
        }
        self.bindir = self.world.bin
        self.path_only = str(self.bindir)
        self.path_with_host = worker_conf.path_with(self.bindir)

    def invoke_worker(self, name: str, **kwargs):
        extra = dict(kwargs.pop("extra", None) or {})
        extra.setdefault("PATH", self.path_only)
        return self.invoke(["worker", name], extra=extra, **kwargs)

    def assert_kind_refusal(
        self,
        proc,
        class_name: str,
        *,
        named: tuple[str, ...] = (),
    ) -> str:
        line = self.assert_refusal(proc, class_name, named=named)
        token = class_name.lower().replace("_", "-")
        self.assertTrue(
            line.lower().startswith(token + ":"),
            f"class {token} must be the leading token: {line!r}",
        )
        return line

    def assert_worker(
        self,
        proc,
        *,
        name: str,
        executable: Path | str,
        workers_source: str,
        executable_source: str,
        expect_absolute: bool | None = None,
    ) -> dict:
        expected = str(executable)
        if expect_absolute is None:
            expect_absolute = Path(expected).is_absolute()
        payload = worker_conf.parse_worker_object(
            self, proc, expect_absolute=expect_absolute,
        )
        self.assertEqual(payload["name"], name)
        self.assertEqual(payload["executable"], expected)
        self.assertEqual(payload["sources"]["workers"], workers_source)
        self.assertEqual(payload["sources"]["executable"], executable_source)
        return payload


class ProvisionalWorkerDefaultMembership(_WorkerLaneconfFixture):
    """PROVISIONAL: omitted WORKER_HARNESSES means claude codex grok.

    Candidate contract: absence is the three legacy names; listing a
    name never implements an adapter; worker NAME prints the object.
    """

    def test_omitted_list_resolves_each_legacy_name_from_path(self):
        names = worker_conf.DEFAULT_WORKERS
        self.assertEqual(len(names), 3, names)
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertNotIn("WORKER_HARNESSES", planted)
        for name in names:
            with self.subTest(name=name):
                proc = self.invoke_worker(name)
                self.assert_worker(
                    proc,
                    name=name,
                    executable=self.bins[name],
                    workers_source="default",
                    executable_source="PATH",
                )

    def test_omitted_list_refuses_agy_as_unknown_worker(self):
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertNotIn("WORKER_HARNESSES", planted)
        self.assertTrue(self.bins["agy"].is_file())
        proc = self.invoke_worker("agy")
        self.assert_kind_refusal(proc, "worker", named=("agy",))

    def test_listed_agy_resolves_from_path(self):
        self.plant_conf(WORKER_HARNESSES="claude codex grok agy")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(
            planted["WORKER_HARNESSES"].split(),
            ["claude", "codex", "grok", "agy"],
        )
        proc = self.invoke_worker("agy")
        self.assert_worker(
            proc,
            name="agy",
            executable=self.bins["agy"].resolve(),
            workers_source=worker_conf.file_source(
                self.conf_path, "WORKER_HARNESSES",
            ),
            executable_source="PATH",
        )


class ProvisionalWorkerHarnessesEmptyAndMalformed(_WorkerLaneconfFixture):
    """PROVISIONAL: empty list disables all workers; duplicates and
    names outside [a-z][a-z0-9]* refuse class worker.
    """

    def test_explicit_empty_list_disables_grok(self):
        self.plant_conf(WORKER_HARNESSES="")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted["WORKER_HARNESSES"], "")
        proc = self.invoke_worker("grok")
        self.assert_kind_refusal(proc, "worker", named=("grok",))

    def test_whitespace_only_list_disables_grok(self):
        self.plant_conf(WORKER_HARNESSES=" \t ")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted["WORKER_HARNESSES"].strip(), "")
        proc = self.invoke_worker("grok")
        self.assert_kind_refusal(proc, "worker", named=("grok",))

    def test_agy_only_list_refuses_grok(self):
        self.plant_conf(WORKER_HARNESSES="agy")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted["WORKER_HARNESSES"], "agy")
        proc = self.invoke_worker("grok")
        self.assert_kind_refusal(proc, "worker", named=("grok",))

    def test_duplicate_names_refuse_worker(self):
        self.plant_conf(WORKER_HARNESSES="claude grok grok")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(
            planted["WORKER_HARNESSES"].split(),
            ["claude", "grok", "grok"],
        )
        proc = self.invoke_worker("claude")
        self.assert_kind_refusal(proc, "worker")

    def test_malformed_list_name_refuses_worker(self):
        bad = "agy-cli"
        self.assertNotRegex(bad, worker_conf.WORKER_NAME_RE)
        self.plant_conf(WORKER_HARNESSES=f"claude {bad}")
        proc = self.invoke_worker("claude")
        self.assert_kind_refusal(proc, "worker", named=(bad,))

    def test_malformed_requested_name_refuses_worker(self):
        bad = "Grok"
        self.assertNotRegex(bad, worker_conf.WORKER_NAME_RE)
        proc = self.invoke_worker(bad)
        self.assert_kind_refusal(proc, "worker", named=(bad,))

    def test_double_space_unique_names_are_not_duplicates(self):
        self.plant_conf(WORKER_HARNESSES="claude  grok")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted["WORKER_HARNESSES"], "claude  grok")
        self.assertEqual(planted["WORKER_HARNESSES"].split(), ["claude", "grok"])
        proc = self.invoke_worker("grok")
        self.assert_worker(
            proc,
            name="grok",
            executable=self.bins["grok"],
            workers_source=worker_conf.file_source(
                self.conf_path, "WORKER_HARNESSES",
            ),
            executable_source="PATH",
        )


class ProvisionalWorkerExecutableResolution(_WorkerLaneconfFixture):
    """PROVISIONAL: EXECUTABLE_<NAME> is one nonempty absolute regular
    executable; absence uses PATH; failure refuses executable and does
    not fall through.
    """

    def test_absolute_override_is_one_token_and_not_the_path_bin(self):
        override = worker_conf.plant_executable(
            self, self.scratch / "override" / "grok",
        )
        self.assertNotEqual(override, self.bins["grok"])
        key = worker_conf.executable_key("grok")
        self.plant_conf(**{key: str(override)})
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted[key], str(override))
        proc = self.invoke_worker("grok")
        payload = self.assert_worker(
            proc,
            name="grok",
            executable=override,
            workers_source="default",
            executable_source=worker_conf.file_source(self.conf_path, key),
        )
        self.assertNotEqual(payload["executable"], str(self.bins["grok"]))

    def test_path_with_spaces_stays_one_token(self):
        spaced = worker_conf.plant_executable(
            self, self.scratch / "agy tools" / "agy bin",
        )
        self.assertIn(" ", str(spaced))
        key = worker_conf.executable_key("agy")
        self.plant_conf(
            WORKER_HARNESSES="claude codex grok agy",
            **{key: str(spaced)},
        )
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted[key], str(spaced))
        proc = self.invoke_worker("agy")
        payload = self.assert_worker(
            proc,
            name="agy",
            executable=spaced,
            workers_source=worker_conf.file_source(
                self.conf_path, "WORKER_HARNESSES",
            ),
            executable_source=worker_conf.file_source(self.conf_path, key),
        )
        self.assertEqual(payload["executable"].count(str(spaced)), 1)
        self.assertNotEqual(payload["executable"].split()[0], payload["executable"])

    def test_symlink_override_resolves_to_the_target(self):
        target = worker_conf.plant_executable(
            self, self.scratch / "real" / "agy",
        )
        link = self.scratch / "link" / "agy"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), target)
        key = worker_conf.executable_key("agy")
        self.plant_conf(
            WORKER_HARNESSES="agy",
            **{key: str(link)},
        )
        proc = self.invoke_worker("agy")
        payload = self.assert_worker(
            proc,
            name="agy",
            executable=target,
            workers_source=worker_conf.file_source(
                self.conf_path, "WORKER_HARNESSES",
            ),
            executable_source=worker_conf.file_source(self.conf_path, key),
        )
        self.assertEqual(payload["executable"], str(target))
        self.assertNotEqual(payload["executable"], str(link))

    def test_relative_override_refuses_executable(self):
        relative = "./agy"
        self.assertFalse(Path(relative).is_absolute())
        key = worker_conf.executable_key("agy")
        self.plant_conf(WORKER_HARNESSES="agy", **{key: relative})
        proc = self.invoke_worker("agy")
        self.assert_kind_refusal(proc, "executable", named=(relative,))

    def test_empty_override_refuses_and_does_not_use_path(self):
        key = worker_conf.executable_key("grok")
        self.plant_conf(**{key: ""})
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted[key], "")
        proc = self.invoke_worker("grok")
        self.assert_kind_refusal(proc, "executable")
        self.assertNotEqual(proc.exit, 0)

    def test_missing_override_refuses_executable(self):
        missing = self.scratch / "missing" / "agy"
        self.assertFalse(missing.exists(), "plant: override must not exist")
        key = worker_conf.executable_key("agy")
        self.plant_conf(WORKER_HARNESSES="agy", **{key: str(missing)})
        proc = self.invoke_worker("agy")
        self.assert_kind_refusal(proc, "executable", named=(str(missing),))

    def test_non_executable_override_refuses_executable(self):
        path = self.scratch / "not-x" / "agy"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b"#!/bin/sh\nexit 0\n"
        self._plant_bytes(path, data)
        self.assertFalse(os.access(path, os.X_OK), "plant: must not be +x")
        key = worker_conf.executable_key("agy")
        self.plant_conf(WORKER_HARNESSES="agy", **{key: str(path)})
        proc = self.invoke_worker("agy")
        self.assert_kind_refusal(proc, "executable", named=(str(path),))

    def test_directory_override_refuses_executable(self):
        directory = self.scratch / "agy-dir"
        directory.mkdir()
        self.assertTrue(directory.is_dir())
        key = worker_conf.executable_key("agy")
        self.plant_conf(WORKER_HARNESSES="agy", **{key: str(directory)})
        proc = self.invoke_worker("agy")
        self.assert_kind_refusal(proc, "executable", named=(str(directory),))

    def test_failed_path_lookup_falls_back_to_legacy_name(self):
        empty = self.scratch / "empty-bin"
        empty.mkdir()
        self.assertIsNone(shutil.which("grok", path=str(empty)))
        proc = self.invoke_worker("grok", extra={"PATH": str(empty)})
        self.assert_worker(
            proc,
            name="grok",
            executable="grok",
            workers_source="default",
            executable_source="PATH",
            expect_absolute=False,
        )

    def test_failed_agy_path_lookup_refuses_executable(self):
        empty = self.scratch / "empty-bin"
        empty.mkdir()
        self.assertIsNone(shutil.which("agy", path=str(empty)))
        self.plant_conf(WORKER_HARNESSES="claude codex grok agy")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(
            planted["WORKER_HARNESSES"].split(),
            ["claude", "codex", "grok", "agy"],
        )
        proc = self.invoke_worker("agy", extra={"PATH": str(empty)})
        self.assert_kind_refusal(proc, "executable", named=("agy",))

    def test_flag_like_override_does_not_split(self):
        key = worker_conf.executable_key("agy")
        value = f"{self.bins['agy']} --print"
        self.assertIn(" --print", value)
        self.plant_conf(WORKER_HARNESSES="agy", **{key: value})
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted[key], value)
        proc = self.invoke_worker("agy")
        self.assert_kind_refusal(proc, "executable", named=(value,))

    def test_command_substitution_in_override_does_not_run(self):
        witness = self.scratch / "pwned-executable"
        self.assertFalse(witness.exists(), "plant: witness must start absent")
        key = worker_conf.executable_key("agy")
        value = f"/no/such/agy-$(printf PWNED > {witness})"
        self.plant_conf(WORKER_HARNESSES="agy", **{key: value})
        proc = self.invoke_worker("agy")
        self.assert_kind_refusal(proc, "executable")
        self.assertFalse(
            witness.exists(),
            "command substitution must not execute; "
            f"witness="
            f"{witness.read_text(encoding='utf-8') if witness.exists() else ''!r}",
        )


class ProvisionalWorkerProvenanceAndPrecedence(_WorkerLaneconfFixture):
    """PROVISIONAL: env > local file > tracked file > default.
    Environment keys work when absent from both files. Blank overrides
    do not fall through. File sources are not labelled env.
    """

    def test_env_list_beats_local_and_file(self):
        self.plant_conf(WORKER_HARNESSES="agy")
        local = self.plant_local("WORKER_HARNESSES='claude'\n")
        self.assertTrue(local.is_file())
        proc = self.invoke_worker(
            "grok",
            extra={"PATH": self.path_only, "WORKER_HARNESSES": "grok"},
        )
        self.assert_worker(
            proc,
            name="grok",
            executable=self.bins["grok"],
            workers_source=worker_conf.env_source("WORKER_HARNESSES"),
            executable_source="PATH",
        )

    def test_local_list_beats_file(self):
        self.plant_conf(WORKER_HARNESSES="agy")
        local = self.plant_local("WORKER_HARNESSES='claude'\n")
        self.assertIn("claude", local.read_text(encoding="utf-8"))
        proc = self.invoke_worker("claude")
        self.assert_worker(
            proc,
            name="claude",
            executable=self.bins["claude"],
            workers_source=worker_conf.file_source(local, "WORKER_HARNESSES"),
            executable_source="PATH",
        )
        refused = self.invoke_worker("agy")
        self.assert_kind_refusal(refused, "worker", named=("agy",))

    def test_env_list_works_when_absent_from_files(self):
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertNotIn("WORKER_HARNESSES", planted)
        proc = self.invoke_worker(
            "agy",
            extra={"PATH": self.path_only, "WORKER_HARNESSES": "agy"},
        )
        self.assert_worker(
            proc,
            name="agy",
            executable=self.bins["agy"].resolve(),
            workers_source=worker_conf.env_source("WORKER_HARNESSES"),
            executable_source="PATH",
        )

    def test_blank_env_list_does_not_fall_through_to_file(self):
        self.plant_conf(WORKER_HARNESSES="claude grok")
        planted = declared_keys(self.conf_path.read_text(encoding="utf-8"))
        self.assertEqual(planted["WORKER_HARNESSES"], "claude grok")
        proc = self.invoke_worker(
            "grok",
            extra={"PATH": self.path_only, "WORKER_HARNESSES": ""},
        )
        self.assert_kind_refusal(proc, "worker", named=("grok",))

    def test_env_executable_beats_file(self):
        file_bin = worker_conf.plant_executable(
            self, self.scratch / "from-file" / "grok",
        )
        env_bin = worker_conf.plant_executable(
            self, self.scratch / "from-env" / "grok",
        )
        self.assertNotEqual(file_bin, env_bin)
        key = worker_conf.executable_key("grok")
        self.plant_conf(**{key: str(file_bin)})
        proc = self.invoke_worker(
            "grok",
            extra={"PATH": self.path_only, key: str(env_bin)},
        )
        payload = self.assert_worker(
            proc,
            name="grok",
            executable=env_bin,
            workers_source="default",
            executable_source=worker_conf.env_source(key),
        )
        self.assertNotEqual(payload["executable"], str(file_bin))
        self.assertNotEqual(
            payload["sources"]["executable"],
            worker_conf.file_source(self.conf_path, key),
        )

    def test_local_executable_beats_file(self):
        file_bin = worker_conf.plant_executable(
            self, self.scratch / "from-file" / "grok",
        )
        local_bin = worker_conf.plant_executable(
            self, self.scratch / "from-local" / "grok",
        )
        self.assertNotEqual(file_bin, local_bin)
        key = worker_conf.executable_key("grok")
        self.plant_conf(**{key: str(file_bin)})
        local = self.plant_local(f"{key}='{local_bin}'\n")
        planted = declared_keys(local.read_text(encoding="utf-8"))
        self.assertEqual(planted[key], str(local_bin))
        proc = self.invoke_worker("grok")
        payload = self.assert_worker(
            proc,
            name="grok",
            executable=local_bin,
            workers_source="default",
            executable_source=worker_conf.file_source(local, key),
        )
        self.assertNotEqual(payload["executable"], str(file_bin))
        self.assertNotEqual(
            payload["sources"]["executable"],
            worker_conf.file_source(self.conf_path, key),
        )

    def test_env_executable_beats_local_and_file(self):
        file_bin = worker_conf.plant_executable(
            self, self.scratch / "from-file" / "grok",
        )
        local_bin = worker_conf.plant_executable(
            self, self.scratch / "from-local" / "grok",
        )
        env_bin = worker_conf.plant_executable(
            self, self.scratch / "from-env" / "grok",
        )
        self.assertNotEqual(file_bin, local_bin)
        self.assertNotEqual(local_bin, env_bin)
        self.assertNotEqual(file_bin, env_bin)
        key = worker_conf.executable_key("grok")
        self.plant_conf(**{key: str(file_bin)})
        local = self.plant_local(f"{key}='{local_bin}'\n")
        planted = declared_keys(local.read_text(encoding="utf-8"))
        self.assertEqual(planted[key], str(local_bin))
        proc = self.invoke_worker(
            "grok",
            extra={"PATH": self.path_only, key: str(env_bin)},
        )
        payload = self.assert_worker(
            proc,
            name="grok",
            executable=env_bin,
            workers_source="default",
            executable_source=worker_conf.env_source(key),
        )
        self.assertNotEqual(payload["executable"], str(file_bin))
        self.assertNotEqual(payload["executable"], str(local_bin))
        self.assertNotEqual(
            payload["sources"]["executable"],
            worker_conf.file_source(local, key),
        )

    def test_file_source_string_uses_absolute_path_and_key(self):
        key = worker_conf.executable_key("agy")
        override = worker_conf.plant_executable(
            self, self.scratch / "abs" / "agy",
        )
        self.plant_conf(WORKER_HARNESSES="agy", **{key: str(override)})
        proc = self.invoke_worker("agy")
        payload = self.assert_worker(
            proc,
            name="agy",
            executable=override,
            workers_source=worker_conf.file_source(
                self.conf_path, "WORKER_HARNESSES",
            ),
            executable_source=worker_conf.file_source(self.conf_path, key),
        )
        self.assertTrue(payload["sources"]["workers"].startswith("file:"))
        self.assertNotIn("env:", payload["sources"]["workers"])
        self.assertTrue(payload["sources"]["executable"].startswith("file:"))
        self.assertNotIn("env:", payload["sources"]["executable"])
        self.assertIn(str(self.conf_path.resolve()), payload["sources"]["workers"])
        self.assertIn(key, payload["sources"]["executable"])

    def test_worker_cli_does_not_need_identity(self):
        env = self.env(extra={"PATH": self.path_only})
        self.assertNotIn("WF_AGENT", env)
        proc = self.invoke_worker("grok")
        self.assert_worker(
            proc,
            name="grok",
            executable=self.bins["grok"],
            workers_source="default",
            executable_source="PATH",
        )


if __name__ == "__main__":
    unittest.main()
