"""python-tooling fixture-app tests, written from the slice-2 contract.

Contracts (only): the fixture-apps scope and the slice-2 plan in the
job input (layout, seven faults by kind, neighbours, the merged lever
`ops/bin/fixture.sh`). The app under
`ops/devlane/fixtures/apps/python-tooling` is the implementation and is
not read here beyond the paths the contract names.

Fix round: Codex skeptic (record 20260904T082333Z-review-codex-7afd2e)
found 1 P1, 3 P2, 1 P3. This file addresses all five. Each planted
fault is proved by its own probe and its own fix, constrained to the
declared span; clean neighbours are discovered from the contract's
clean shapes in tool/ and tests/, not from the neighbour field that
grade also reads; unittest and ruff are actually run; make copies the
lane and the scratch runs suites.sh plus dispatch.sh --dry-run;
README.md itself carries the apparatus declaration.

Red at this ref because that directory is absent: the existence test
fails on `FAULTS.json`. Make/prove/grade cases invoke the merged lever
and fail on their own promises rather than on an import.

The app's own `tests/`, probes, fixes, and `FAULTS.json` are the
implementer's. Worlds, commands and fakes go through ops/testkit.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
import unittest
from pathlib import Path

from ops.testkit import Timeout, World, plant_text, run

REPO = Path(__file__).resolve().parents[4]
LEVER = REPO / "ops" / "bin" / "fixture.sh"
APPS = REPO / "ops" / "devlane" / "fixtures" / "apps"
APP = APPS / "python-tooling"
MANIFEST = APP / "FAULTS.json"
CALIBRATION = REPO / "ops" / "conductor" / "findings" / "calibration.jsonl"

APP_NAME = "python-tooling"
AGENT = "Python Tooling Fixture Tester <python-tooling-fixture@example.invalid>"
RUN_CAP = 60
CHECK_CAP = 60
PROBE_CAP = 15
SUITES_CAP = 60
DISPATCH_CAP = 30

EXPECTED_IDS = tuple(f"PT-{index:02d}" for index in range(1, 8))
REQUIRED_KINDS = frozenset({"K1", "K2", "K3", "K4", "K5", "K6", "K8"})
EXPECTED_KIND_VISIBLE = {
    "PT-01": ("K1", "audit"),
    "PT-02": ("K2", "both"),
    "PT-03": ("K3", "test"),
    "PT-04": ("K4", "audit"),
    "PT-05": ("K5", "test"),
    "PT-06": ("K6", "test"),
    "PT-07": ("K8", "audit"),
}
PACKAGING_NAMES = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Pipfile",
    "poetry.lock",
    "uv.lock",
    "pdm.lock",
)

# Lane files the portability proof copies and then actually runs.
LANE_COPY_PATHS = (
    "ops/devlane/fixtures/apps/FAULT-KINDS.md",
    "ops/bin/suites.sh",
    "ops/devlane/workflow/checks/lane.py",
    "ops/testkit/checks.py",
    "ops/testkit/suite.py",
    "ops/bin/dispatch.sh",
    "ops/conductor/tools/dispatch.sh",
    "ops/conductor/tools/instrument.py",
    "ops/conductor/tools/laneconf.py",
    "ops/devlane/dispatch/launch.py",
    "ops/lane.conf",
    "ops/devlane/task/envelope.py",
    "ops/devlane/task/tests/support.py",
    "ops/devlane/task/tests/test_envelope.py",
    # the kit the task family's support imports (docs/tests.md §Reaching it)
    "ops/testkit/__init__.py",
    "ops/testkit/world.py",
    "ops/testkit/command.py",
    "ops/testkit/fakes.py",
    "ops/testkit/fake_program.py",
    "ops/testkit/cases.py",
)


COLOUR_RE = re.compile(r"\b(RED|GREEN|UNREACHABLE)\b")
FAULT_ID_RE = re.compile(r"\b(PT-\d{2})\b")
COUNTS_RE = re.compile(
    r"red=(?P<red>\d+)\s+green=(?P<green>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)"
)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
UNITTEST_RAN_RE = re.compile(r"Ran ([1-9]\d*) tests?")
CONF_ASSIGN_RE = re.compile(
    r"^(?P<key>[A-Z][A-Z0-9_]*)='(?P<value>[^']*)'\s*$", re.MULTILINE
)

# Contract-derived dirty shapes at the practiced span (FAULT-KINDS.md).
KIND_DIRTY = {
    "K1": re.compile(
        r"os\.system|os\.popen|shell\s*=\s*True|/bin/sh|\bsh\s+-c\b|\bbash\s+-c\b"
    ),
    "K2": re.compile(r"os\.path\.join|\bPath\([^)]*\)\s*/|root\s*/\s*name"),
    "K3": re.compile(r"\bif not\b|\bif [A-Za-z_][\w.]*:"),
    "K4": re.compile(
        r"os\.environ|os\.getenv|\benviron\b|\blog\b|\btoken\b", re.IGNORECASE
    ),
    "K5": re.compile(r"except\s+(?:Exception|BaseException)?\s*:"),
    "K6": re.compile(r"<=\s*(?:self\.)?(end|stop|hi|high|until|last)\b"),
    "K8": re.compile(r"expected\s*=\s*[A-Za-z_]\w*\(|from\s+\S+\s+import|import\s+tool"),
}

# Contract-derived clean neighbour shapes, discovered without FAULTS.json.
KIND_CLEAN = {
    "K1": re.compile(
        r"subprocess\.(?:run|Popen|call|check_call|check_output)\s*\(\s*\["
    ),
    "K2": re.compile(r"\.relative_to\(|\.is_relative_to\("),
    "K3": re.compile(r"\bis None\b|\bis not None\b"),
    "K4": re.compile(
        r"redact|redacted|\[REDACTED\]|\*\*\*|mask_secret", re.IGNORECASE
    ),
    "K5": re.compile(
        r"except [^\n:]+:(?:[ \t]*raise\b|(?:[^\n]*\n)+?[ \t]*raise\b)"
    ),
    "K6": re.compile(
        r"<(?!=)\s*(?:self\.)?(end|stop|hi|high|until|last)\b"
        r"|range\(\s*[^,]+,\s*(?:self\.)?(end|stop)"
    ),
    "K8": re.compile(
        r"assert(?:Equal|NotEqual)\(\s*[^,\n]+,\s*(?:[0-9]+|b?['\"][^'\"]+['\"])"
        r"|^\s*(?:expected|want|oracle)\s*=\s*(?:[0-9]+|b?['\"])",
        re.MULTILINE,
    ),
}


def _lint_the_lane_copies_under_the_lane_contract(made: Path) -> None:
    """suites.sh runs `ruff check .` since 2026-09-13, as the CI lane job
    does. The scratch holds two lint contracts: the app's own, at its root,
    whose planted faults (FAULT-KINDS.md) its calibration relies on, and
    the lane's for the copies under ops/. A lane ruff.toml at the scratch
    root would shadow the app's (ruff reads ruff.toml before
    pyproject.toml), so the lane contract rides as the nested
    ops/ruff.toml, which ruff's hierarchical discovery applies to the
    copies alone; its per-file keys are re-rooted from ops/."""
    source = REPO / "ruff.toml"
    text = source.read_text(encoding="utf-8")
    if text.count('"ops/') < 1:
        raise AssertionError("the repository ruff.toml keys its rows under ops/")
    plant_text(made / "ops" / "ruff.toml", text.replace('"ops/', '"'))


def load_json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def fault_rows(data: dict) -> list[dict]:
    faults = data.get("faults")
    if not isinstance(faults, list):
        return []
    return [row for row in faults if isinstance(row, dict)]


def _rule_prefix(token: str) -> str:
    return str(token).rstrip("0123456789")


def _ruff_select(text: str) -> list[str]:
    if not text.strip():
        return []
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    lint = data.get("lint")
    raw = None
    if isinstance(lint, dict) and "select" in lint:
        raw = lint.get("select")
    elif "select" in data:
        raw = data.get("select")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _live_fixture_roots() -> list[Path]:
    paths: list[Path] = []
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        paths.append(Path(xdg) / "agentscient" / "fixtures")
    paths.append(Path.home() / ".local" / "state" / "agentscient" / "fixtures")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path if not path.exists() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _live_findings_stores() -> list[Path]:
    paths: list[Path] = []
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        paths.append(Path(xdg) / "agentscient" / "findings.db")
    paths.append(Path.home() / ".local" / "state" / "agentscient" / "findings.db")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path if not path.exists() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique






def _as_lines(value) -> list[int] | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        return [value[0], value[1]]
    return None


def _span_text(path: Path, lines: list[int]) -> str:
    start, end = lines
    body = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(body[start - 1 : end])


def _spans_overlap(path_a, lines_a, path_b, lines_b) -> bool:
    if path_a != path_b:
        return False
    span_a = _as_lines(lines_a)
    span_b = _as_lines(lines_b)
    if span_a is None or span_b is None:
        return False
    return span_a[0] <= span_b[1] and span_b[0] <= span_a[1]


def _strip_shell_comments(command: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in command.splitlines())


def _conf_value(text: str, key: str) -> str | None:
    for match in CONF_ASSIGN_RE.finditer(text):
        if match.group("key") == key:
            return match.group("value")
    return None


def _probe_script_paths(command: str) -> list[str]:
    found: list[str] = []
    for token in command.split():
        if "probes/" in token or token.startswith("probes"):
            found.append(token)
    return found


def _patch_removed_lines(text: str) -> dict[str, list[int]]:
    current: str | None = None
    old_line = 0
    removed: dict[str, list[int]] = {}
    hunk_starts: dict[str, list[int]] = {}
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip().removeprefix("b/")
            current = None if path == "/dev/null" else path
            if current is not None:
                removed.setdefault(current, [])
                hunk_starts.setdefault(current, [])
            continue
        hunk = HUNK_RE.match(line)
        if hunk is not None:
            old_line = int(hunk.group(1))
            if current is not None:
                hunk_starts[current].append(old_line)
            continue
        if current is None:
            continue
        if line.startswith("\\"):
            continue
        if line.startswith("-") and not line.startswith("---"):
            removed[current].append(old_line)
            old_line += 1
        elif line.startswith("+") and not line.startswith("+++"):
            continue
        elif line.startswith(" "):
            old_line += 1
    for path, lines in list(removed.items()):
        if not lines:
            removed[path] = list(hunk_starts.get(path, []))
    return {path: lines for path, lines in removed.items() if path}


def _iter_app_python(root: Path) -> list[tuple[str, Path, str]]:
    found: list[tuple[str, Path, str]] = []
    for folder in ("tool", "tests"):
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            found.append((rel, path, path.read_text(encoding="utf-8")))
    return found


def discover_clean_neighbours(root: Path) -> dict[str, list[tuple[str, int, int, str]]]:
    """Find clean-neighbour spans from the contract shapes, not FAULTS.json."""
    found: dict[str, list[tuple[str, int, int, str]]] = {kind: [] for kind in KIND_CLEAN}
    for rel, _path, text in _iter_app_python(root):
        for kind, pattern in KIND_CLEAN.items():
            if kind == "K8" and not rel.startswith("tests/"):
                continue
            for match in pattern.finditer(text):
                start = text[: match.start()].count("\n") + 1
                end = text[: match.end()].count("\n") + 1
                snippet = "\n".join(text.splitlines()[start - 1 : end])
                found[kind].append((rel, start, end, snippet))
    return found




class PythonToolingLayout(unittest.TestCase):

    def test_ruff_toml_selects_dev_dialect_not_security(self):
        text = (
            (APP / "ruff.toml").read_text(encoding="utf-8")
            if (APP / "ruff.toml").is_file()
            else ""
        )
        select = _ruff_select(text)
        prefixes = {_rule_prefix(token) for token in select}
        for family in ("E", "F", "I", "B"):
            with self.subTest(family=family):
                self.assertIn(
                    family,
                    prefixes,
                    f"ruff.toml must select {family} (dev dialect); found {select!r}",
                )
        self.assertNotIn(
            "S",
            prefixes,
            "ruff.toml must not select S, so K1 and K4 survive the app lint",
        )




    def test_manifest_says_k9_does_not_apply(self):
        text = MANIFEST.read_text(encoding="utf-8") if MANIFEST.is_file() else ""
        self.assertRegex(
            text,
            r"\bK9\b",
            "the dialect has no packaging or lock; the manifest must say K9 does not apply",
        )
        self.assertRegex(
            text.lower(),
            r"does not apply|not apply|not planted",
            "K9 must be named as not applying to this dialect",
        )

    def test_no_packaging_or_lock(self):
        self.assertTrue(
            APP.is_dir(),
            f"{APP} is missing; python-tooling is stdlib only, no packaging",
        )
        for name in PACKAGING_NAMES:
            with self.subTest(path=name):
                self.assertFalse(
                    (APP / name).is_file(),
                    f"{name} must not exist: K9 does not apply to this dialect",
                )


class PythonToolingManifest(unittest.TestCase):



    def test_manifest_kind_and_visible_to_match_the_contract(self):
        faults = fault_rows(load_json_object(MANIFEST))
        mapping = {
            row.get("id"): (row.get("kind"), row.get("visible_to"))
            for row in faults
        }
        self.assertEqual(
            mapping,
            EXPECTED_KIND_VISIBLE,
            "kind and visible_to must match the slice-2 fault table",
        )

    def test_check_command_names_unittest_and_ruff(self):
        check = load_json_object(MANIFEST).get("check")
        text = check if isinstance(check, str) else ""
        stripped = _strip_shell_comments(text)
        self.assertRegex(
            stripped,
            r"\bunittest\b",
            "check is unittest discovery over tests/ from the app root "
            "(comments do not count)",
        )
        self.assertRegex(
            stripped,
            r"\bruff\b",
            "check includes ruff check . from the app root (comments do not count)",
        )



class _RepoFreeze(unittest.TestCase):
    """Live fixture roots, findings store, calibration export, and this repo stay put."""

    def setUp(self):
        self.world = World(prefix="python-tooling-app-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self.world.require("bash")
        self._live = self.world.watch(
            *(_live_fixture_roots() + _live_findings_stores() + [CALIBRATION]),
            contents=False,
        )
        env = self.world.env()
        self._head_before = run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            env=env,
            timeout=RUN_CAP,
        ).stdout
        self._diff_before = run(
            ["git", "diff", "HEAD"],
            cwd=REPO,
            env=env,
            timeout=RUN_CAP,
        ).stdout
        self.root = self.world.root
        self.home = self.world.home
        listed = "".join(f"{path}\n" for path in LANE_COPY_PATHS)
        self.conf_path = self.world.plant_text(self.root / "copy-list.txt", listed)
        self.lane_conf_path = self.world.plant_text(
            self.root / "lane.conf",
            "LANE_NAME='agentscient'\n",
        )

    def tearDown(self):
        self._live.assert_unchanged(
            self, "live fixture, findings store, or tracked calibration.jsonl mutated"
        )
        env = self.world.env()
        head_after = run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            env=env,
            timeout=RUN_CAP,
        ).stdout
        self.assertEqual(head_after, self._head_before, "real repo HEAD moved")
        diff_after = run(
            ["git", "diff", "HEAD"],
            cwd=REPO,
            env=env,
            timeout=RUN_CAP,
        ).stdout
        self.assertEqual(
            diff_after,
            self._diff_before,
            "real repo tracked files mutated",
        )

    def git_env(self) -> dict[str, str]:
        return self.world.env()

    def env(
        self,
        *,
        agent: str | None = AGENT,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        extra_env = {
            "TZ": "UTC",
            "LANE_CONF": str(self.lane_conf_path),
            "FIXTURE_COPY_LIST": str(self.conf_path),
        }
        if agent is not None:
            extra_env["WF_AGENT"] = agent
        if extra:
            extra_env.update(extra)
        env = self.world.env(extra_env)
        self.assertEqual(env["HOME"], str(self.world.home))
        self.assertEqual(env["XDG_STATE_HOME"], str(self.world.xdg_state))
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        return env

    def scratch_env(self) -> dict[str, str]:
        env = self.world.env({"TZ": "UTC"})
        self.assertEqual(env["HOME"], str(self.world.home))
        self.assertEqual(env["XDG_STATE_HOME"], str(self.world.xdg_state))
        self.assertNotIn("LANE_CONF", env)
        self.assertNotIn("LAUNCHER_PIN", env)
        self.assertNotIn("WF_AGENT", env)
        return env


class PythonToolingMakeProve(_RepoFreeze):
    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = AGENT,
        extra: dict[str, str] | None = None,
        timeout: int = RUN_CAP,
        cwd: Path | None = None,
        lever: Path | None = None,
    ):
        env = self.env(agent=agent, extra=extra)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        lever_path = lever or LEVER
        try:
            return run(
                [str(lever_path), *argv],
                cwd=cwd or REPO,
                env=env,
                timeout=timeout,
            )
        except Timeout as exc:
            self.fail(f"fixture.sh {argv} exceeded {timeout}s: {exc}")

    def make(self, target: Path):
        return self.invoke(["make", APP_NAME, str(target)])

    def parse_dir(self, proc) -> Path:
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        self.assertEqual(
            len(lines),
            1,
            f"make prints one directory on stdout: {proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )
        path = Path(lines[0].strip())
        self.assertTrue(path.is_dir(), f"printed DIR is not a directory: {path}")
        return path

    def parse_prove(self, stdout: str) -> dict[str, str]:
        rows: dict[str, str] = {}
        for line in stdout.splitlines():
            colour_match = COLOUR_RE.search(line)
            if not colour_match:
                continue
            colour = colour_match.group(1)
            id_match = FAULT_ID_RE.search(line)
            self.assertIsNotNone(
                id_match,
                f"colour line names a PT-* fault id: {line!r}",
            )
            fault_id = id_match.group(1)
            self.assertNotIn(fault_id, rows, f"duplicate colour for {fault_id}")
            rows[fault_id] = colour
        return rows

    def parse_counts(self, stdout: str) -> dict[str, int]:
        match = COUNTS_RE.search(stdout)
        self.assertIsNotNone(
            match,
            f"prove prints red= green= unreachable= counts: {stdout!r}",
        )
        return {key: int(value) for key, value in match.groupdict().items()}

    def made_app(self, name: str) -> Path:
        self.assertTrue(
            LEVER.is_file(),
            "INVALID: ops/bin/fixture.sh missing; slice 1 must be merged",
        )
        target = self.root / name
        self.assertFalse(target.exists())
        proc = self.make(target)
        self.assertEqual(proc.exit, 0, proc.stderr or proc.stdout)
        return self.parse_dir(proc)

    def git(self, directory: Path, *args: str):
        proc = run(
            ["git", *args],
            cwd=directory,
            env=self.git_env(),
            timeout=RUN_CAP,
        )
        self.assertEqual(
            proc.exit,
            0,
            f"git {args} in {directory}: {proc.stderr or proc.stdout}",
        )
        return proc

    def run_probe(self, directory: Path, command: str) -> str:
        self.assertTrue(command.strip(), "probe command is empty")
        env = self.scratch_env()
        try:
            proc = run(
                ["/bin/sh", "-c", command],
                cwd=directory,
                env=env,
                timeout=PROBE_CAP,
            )
        except Timeout:
            return "UNREACHABLE"
        if proc.exit in {126, 127} or proc.exit < 0:
            return "UNREACHABLE"
        return "GREEN" if proc.exit == 0 else "RED"

    def apply_fix(self, directory: Path, rel: str) -> bytes:
        patch = directory / rel
        self.assertTrue(patch.is_file(), f"fix {rel} is missing")
        data = patch.read_bytes()
        self.assertTrue(data, f"fix {rel} is empty")
        check = run(
            ["git", "apply", "--check", str(patch)],
            cwd=directory,
            env=self.git_env(),
            timeout=RUN_CAP,
        )
        self.assertEqual(
            check.exit,
            0,
            f"git apply --check {rel}: {check.stderr or check.stdout}",
        )
        applied = run(
            ["git", "apply", str(patch)],
            cwd=directory,
            env=self.git_env(),
            timeout=RUN_CAP,
        )
        self.assertEqual(
            applied.exit,
            0,
            f"git apply {rel}: {applied.stderr or applied.stdout}",
        )
        return data

    def reset_tree(self, directory: Path) -> None:
        self.git(directory, "reset", "--hard", "HEAD")

    def load_faults(self, directory: Path) -> list[dict]:
        faults = fault_rows(load_json_object(directory / "FAULTS.json"))
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
            "seven planted faults must be present before independent proofs run",
        )
        return faults


    def test_scratch_runs_suites_and_dispatch_dry_run(self):
        made = self.made_app("portable")
        _lint_the_lane_copies_under_the_lane_contract(made)
        self.assertTrue((made / "ops/bin/suites.sh").is_file())
        self.assertTrue((made / "ops/bin/dispatch.sh").is_file())
        self.assertTrue((made / "ops/devlane/dispatch/launch.py").is_file())
        self.assertTrue((made / "ops/lane.conf").is_file())
        env = self.scratch_env()
        try:
            suites = run(
                ["bash", str(made / "ops" / "bin" / "suites.sh"),
                 "--suite", "ops/devlane/task/tests"],
                cwd=made,
                env=env,
                timeout=SUITES_CAP,
            )
        except Timeout as exc:
            self.fail(f"suites.sh in the scratch exceeded {SUITES_CAP}s: {exc}")
        self.assertEqual(
            suites.exit,
            0,
            f"bash ops/bin/suites.sh must be green in the scratch: "
            f"stdout={suites.stdout!r} stderr={suites.stderr!r}",
        )
        self.assertIn("PARTIAL: PASS", suites.stdout)
        combined = (suites.stdout or "") + (suites.stderr or "")
        ran = UNITTEST_RAN_RE.search(combined)
        self.assertIsNotNone(
            ran,
            "suites.sh must actually run tests, not skip every suite as ABSENT: "
            f"{combined!r}",
        )
        self.assertGreaterEqual(int(ran.group(1)), 1, combined)
        self.assertNotIn("FAILED", combined)
        scope = self.root / "dry-run-scope.txt"
        self.world.plant_bytes(scope, b"fixture-apps slice-2 dry-run scope\n")
        try:
            dry = run(
                [
                    "bash",
                    str(made / "ops" / "bin" / "dispatch.sh"),
                    "--worktree",
                    str(made),
                    "--job",
                    "sweep",
                    "--harness",
                    "claude",
                    "--scope-file",
                    str(scope),
                    "--dry-run",
                ],
                cwd=made,
                env=env,
                timeout=DISPATCH_CAP,
            )
        except Timeout as exc:
            self.fail(f"dispatch.sh --dry-run exceeded {DISPATCH_CAP}s: {exc}")
        self.assertEqual(
            dry.exit,
            0,
            f"dispatch.sh --dry-run must resolve from the copied conf: "
            f"stdout={dry.stdout!r} stderr={dry.stderr!r}",
        )
        lines = [ln for ln in (dry.stdout or "").splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, dry.stdout)
        payload = json.loads(lines[0])
        self.assertIn("launcher", payload)
        self.assertIn("argv", payload)
        launcher = payload["launcher"]
        self.assertEqual(
            launcher.get("source"),
            "sibling",
            "dry-run must resolve the launcher from the copied tree, not the env",
        )
        launch_path = Path(str(launcher.get("path") or ""))
        self.assertEqual(
            launch_path.resolve(),
            (made / "ops" / "devlane" / "dispatch" / "launch.py").resolve(),
        )
        argv = payload.get("argv")
        self.assertIsInstance(argv, list)
        conf_text = (made / "ops" / "lane.conf").read_text(encoding="utf-8")
        model = _conf_value(conf_text, "MODEL_CLAUDE_READ")
        effort = _conf_value(conf_text, "EFFORT_CLAUDE")
        self.assertTrue(model, "copied lane.conf must declare MODEL_CLAUDE_READ")
        self.assertTrue(effort, "copied lane.conf must declare EFFORT_CLAUDE")
        self.assertIn("--model", argv)
        self.assertIn(model, argv)
        self.assertIn("--effort", argv)
        self.assertIn(effort, argv)



    def test_each_planted_fault_is_proved_by_its_own_probe(self):
        made = self.made_app("independent-probes")
        faults = self.load_faults(made)
        snapshots = []
        for row in faults:
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            loc_path = loc.get("path") if isinstance(loc.get("path"), str) else ""
            loc_lines = _as_lines(loc.get("lines"))
            self.assertTrue(loc_path, f"{row.get('id')} location.path missing")
            self.assertIsNotNone(loc_lines, f"{row.get('id')} location.lines missing")
            path = made / loc_path
            self.assertTrue(path.is_file(), f"{row.get('id')} location {loc_path} missing")
            span = _span_text(path, loc_lines)
            self.assertTrue(span.strip(), f"{row.get('id')} location span is empty")
            snapshots.append((row, loc_path, loc_lines, span, path.read_bytes()))

        for row, loc_path, loc_lines, span, loc_before in snapshots:
            fault_id = row.get("id")
            kind = row.get("kind")
            probe_cmd = row.get("probe") if isinstance(row.get("probe"), str) else ""
            fix_rel = row.get("fix") if isinstance(row.get("fix"), str) else ""
            with self.subTest(fault=fault_id, stage="kind-and-red"):
                dirty = KIND_DIRTY.get(kind)
                self.assertIsNotNone(dirty, f"no dirty shape for {kind}")
                self.assertRegex(
                    span,
                    dirty,
                    f"{fault_id} practiced span must look like {kind}, not a label: "
                    f"{span!r}",
                )
                colour = self.run_probe(made, probe_cmd)
                self.assertEqual(
                    colour,
                    "RED",
                    f"{fault_id} probe must be RED while planted: {probe_cmd!r}",
                )

            probe_files = _probe_script_paths(probe_cmd)
            self.assertTrue(
                probe_files,
                f"{fault_id} probe must name a path under probes/",
            )
            probe_before = {
                rel: (made / rel).read_bytes()
                for rel in probe_files
                if (made / rel).is_file()
            }
            self.assertEqual(
                set(probe_before),
                set(probe_files),
                f"{fault_id} probe path missing: {probe_cmd!r}",
            )

            with self.subTest(fault=fault_id, stage="own-fix"):
                patch = self.apply_fix(made, fix_rel)
                loc_after = (made / loc_path).read_bytes()
                self.assertNotEqual(
                    loc_after,
                    loc_before,
                    f"{fault_id} fix must change {loc_path}",
                )
                self.assertTrue(loc_after, f"{fault_id} fix emptied {loc_path}")
                for rel, data in probe_before.items():
                    landed = (made / rel).read_bytes()
                    self.assertEqual(
                        landed,
                        data,
                        f"{fault_id} fix must not rewrite the probe {rel}",
                    )
                    self.assertEqual(len(landed), len(data))
                changed = [
                    line
                    for line in self.git(made, "diff", "--name-only").stdout.splitlines()
                    if line.strip()
                ]
                self.assertEqual(
                    set(changed),
                    {loc_path},
                    f"{fault_id} fix must change only {loc_path}, found {changed}",
                )
                removed = _patch_removed_lines(patch.decode("utf-8"))
                self.assertEqual(
                    set(removed),
                    {loc_path},
                    f"{fault_id} fix must touch only {loc_path}, found {sorted(removed)}",
                )
                start, end = loc_lines
                self.assertTrue(
                    removed[loc_path],
                    f"{fault_id} fix has no changed lines to constrain",
                )
                for line_no in removed[loc_path]:
                    self.assertGreaterEqual(
                        line_no,
                        start,
                        f"{fault_id} fix line {line_no} is before declared span "
                        f"{start}-{end}",
                    )
                    self.assertLessEqual(
                        line_no,
                        end,
                        f"{fault_id} fix line {line_no} is after declared span "
                        f"{start}-{end}",
                    )
                loc_text_after = (made / loc_path).read_text(encoding="utf-8")
                for other_row, other_path, _other_lines, other_span, other_bytes in snapshots:
                    if other_row.get("id") == fault_id:
                        continue
                    if other_path == loc_path:
                        self.assertIn(
                            other_span,
                            loc_text_after,
                            f"{fault_id} fix must leave {other_row.get('id')} planted",
                        )
                    else:
                        self.assertEqual(
                            (made / other_path).read_bytes(),
                            other_bytes,
                            f"{fault_id} fix must not touch {other_path} "
                            f"({other_row.get('id')})",
                        )
                colour = self.run_probe(made, probe_cmd)
                self.assertEqual(
                    colour,
                    "GREEN",
                    f"{fault_id} probe must be GREEN after its own fix",
                )

            with self.subTest(fault=fault_id, stage="others-stay-red"):
                for other in faults:
                    other_id = other.get("id")
                    if other_id == fault_id:
                        continue
                    other_probe = (
                        other.get("probe") if isinstance(other.get("probe"), str) else ""
                    )
                    colour = self.run_probe(made, other_probe)
                    self.assertEqual(
                        colour,
                        "RED",
                        f"{other_id} must stay RED while only {fault_id} is fixed",
                    )

            self.reset_tree(made)
            reset = (made / loc_path).read_bytes()
            self.assertEqual(reset, loc_before, f"{fault_id} reset did not restore")
            colour = self.run_probe(made, probe_cmd)
            self.assertEqual(
                colour,
                "RED",
                f"{fault_id} probe must be RED again after reset",
            )

    def test_clean_neighbours_are_found_apart_from_the_manifest(self):
        made = self.made_app("neighbours")
        faults = self.load_faults(made)
        locations = []
        for row in faults:
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            locations.append((loc.get("path"), loc.get("lines"), row.get("kind"), row))
        discovered = discover_clean_neighbours(made)
        for kind in REQUIRED_KINDS:
            with self.subTest(kind=kind, stage="discovered"):
                self.assertTrue(
                    discovered.get(kind),
                    f"no clean neighbour for {kind} in tool/ or tests/; "
                    "discovery does not read FAULTS.json neighbour fields",
                )

        for path, lines, kind, row in locations:
            fault_id = row.get("id")
            with self.subTest(fault=fault_id, stage="not-the-fault"):
                candidates = []
                for rel, start, end, snippet in discovered.get(kind, []):
                    if _spans_overlap(rel, [start, end], path, lines):
                        continue
                    candidates.append((rel, start, end, snippet))
                self.assertTrue(
                    candidates,
                    f"{fault_id} ({kind}) has no independently found clean neighbour",
                )
                neighbour = (
                    row.get("neighbour") if isinstance(row.get("neighbour"), dict) else {}
                )
                self.assertTrue(
                    any(
                        _spans_overlap(
                            neighbour.get("path"),
                            neighbour.get("lines"),
                            rel,
                            [start, end],
                        )
                        for rel, start, end, _snippet in candidates
                    ),
                    f"{fault_id} manifest neighbour is not one of the independently "
                    f"found clean {kind} spans: {candidates!r} vs {neighbour!r}",
                )

    def test_app_check_runs_unittest_and_ruff_on_a_fresh_make(self):
        made = self.made_app("check-fresh")
        tests_dir = made / "tests"
        self.assertTrue(tests_dir.is_dir(), "fresh make must carry tests/")
        test_files = list(tests_dir.glob("test_*.py"))
        self.assertGreaterEqual(
            len(test_files),
            1,
            "tests/ must contain at least one test_*.py; an empty suite is not a check",
        )
        env = self.scratch_env()
        try:
            unit = run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_*.py",
                ],
                cwd=made,
                env=env,
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"unittest discover exceeded {CHECK_CAP}s: {exc}")
        unit_out = (unit.stdout or "") + (unit.stderr or "")
        self.assertEqual(
            unit.exit,
            0,
            f"unittest discovery over tests/ must be green: {unit_out!r}",
        )
        ran = UNITTEST_RAN_RE.search(unit_out)
        self.assertIsNotNone(
            ran,
            f"unittest must collect tests, not Ran 0: {unit_out!r}",
        )
        self.assertGreaterEqual(int(ran.group(1)), 1, unit_out)
        ruff = self.world.require("ruff")
        try:
            lint = run(
                [ruff, "check", "."],
                cwd=made,
                env=env,
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"ruff check exceeded {CHECK_CAP}s: {exc}")
        self.assertEqual(
            lint.exit,
            0,
            f"ruff check . must be green on a fresh make: "
            f"stdout={lint.stdout!r} stderr={lint.stderr!r}",
        )
        manifest = load_json_object(made / "FAULTS.json")
        check = manifest.get("check") if isinstance(manifest.get("check"), str) else ""
        self.assertTrue(check.strip(), "fresh make must carry a check command")
        stripped = _strip_shell_comments(check)
        self.assertRegex(stripped, r"\bunittest\b")
        self.assertRegex(stripped, r"\bruff\b")
        try:
            proc = run(
                ["bash", "-c", check],
                cwd=made,
                env=env,
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"app check exceeded {CHECK_CAP}s: {exc}")
        self.assertEqual(
            proc.exit,
            0,
            f"app check must be green on a fresh make: stdout={proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )




if __name__ == "__main__":
    unittest.main()
