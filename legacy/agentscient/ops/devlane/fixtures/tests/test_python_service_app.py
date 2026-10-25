"""python-service fixture-app tests, written from the slice-3 contract.

Contracts (only): the fixture-apps scope and the plan for slices 3 to 5
in the job input, taking the slice-3 section as the pins (layout, seven
faults by kind with spans, probes, clean neighbours and fixes, the
toolchain and how prove reaches it, the manifest keys, and what this
file proves through the merged lever `ops/bin/fixture.sh`). The app
under `ops/devlane/fixtures/apps/python-service` is the implementation
and is not read here beyond the paths the contract names.

Fix round: Codex skeptic (record 20260904T094422Z-review-codex-1cbf26)
found 3 P1, 4 P2, 1 P3. This file addresses all eight. Planted faults
are proved by the contract operations (export_archive, record_path,
log_request, handle, page, the K7 test file, the K9 spec), not by an
implementer-authored regex; clean neighbours are called, not classified
by a positive regex; toolchain.install runs so a fresh make has its
venv; python -m service is run (serve, export NAME, the wire, identity
on export); FAULTS.json is pinned to each assigned probe and fix,
severities per id, one confined hunk per fix; no empty needs keys;
not_planted names K3/K8 coverage, the candidate eighth K9, and why it
waits.

Slice 3 assigns no lever change. `probe_timeout` and the preflight
ignore list wait for slice 4; they are not pinned here.

Red at this ref because that directory is absent: the existence test
fails on `FAULTS.json`. Make/prove/grade cases invoke the merged lever
and fail on their own promises rather than on an import.

The app, its own tests, its manifest, probes, and fixes are the
implementer's.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import sys
import tomllib
import unittest
from contextlib import suppress
from pathlib import Path

from ops.testkit import Timeout, World, run

REPO = Path(__file__).resolve().parents[4]
LEVER = REPO / "ops" / "bin" / "fixture.sh"
APPS = REPO / "ops" / "devlane" / "fixtures" / "apps"
APP = APPS / "python-service"
MANIFEST = APP / "FAULTS.json"
CALIBRATION = REPO / "ops" / "conductor" / "findings" / "calibration.jsonl"

APP_NAME = "python-service"
AGENT = "Python Service Fixture Tester <python-service-fixture@example.invalid>"
RUN_CAP = 60
CHECK_CAP = 60
PROBE_CAP = 15
SUITES_CAP = 60
DISPATCH_CAP = 30

EXPECTED_IDS = tuple(f"PS-{index:02d}" for index in range(1, 8))
REQUIRED_KINDS = frozenset({"K1", "K2", "K4", "K5", "K6", "K7", "K9"})
EXPECTED_KIND_VISIBLE = {
    "PS-01": ("K1", "audit"),
    "PS-02": ("K2", "both"),
    "PS-03": ("K4", "audit"),
    "PS-04": ("K5", "test"),
    "PS-05": ("K6", "test"),
    "PS-06": ("K7", "audit"),
    "PS-07": ("K9", "audit"),
}
EXPECTED_SEVERITY = {
    "PS-01": "p1",
    "PS-02": "p1",
    "PS-03": "p1",
    "PS-04": "p2",
    "PS-05": "p2",
    "PS-06": "p3",
    "PS-07": "p2",
}
EXPECTED_LOCATION_PATHS = {
    "PS-01": "service/export.py",
    "PS-02": "service/store.py",
    "PS-03": "service/audit.py",
    "PS-04": "service/api.py",
    "PS-05": "service/paging.py",
    "PS-06": "tests/test_api.py",
    "PS-07": "pyproject.toml",
}
EXPECTED_NEIGHBOUR_PATHS = {
    "PS-01": "service/export.py",
    "PS-02": "service/store.py",
    "PS-03": "service/audit.py",
    "PS-04": "service/api.py",
    "PS-05": "service/paging.py",
    "PS-06": "tests/test_api.py",
    "PS-07": "pyproject.toml",
}
DECLARED = {
    "PS-01": (
        "Request fields are passed to helpers as argument-vector entries, "
        "never as shell text."
    ),
    "PS-02": "Served files always resolve beneath the store root.",
    "PS-03": "Credentials never enter the request log.",
    "PS-04": (
        "A malformed request is refused with a problem, never answered "
        "as empty success."
    ),
    "PS-05": (
        "Pages are half-open: the start is included and the stop excluded."
    ),
    "PS-06": "Tests exercise the real request parser at the boundary.",
    "PS-07": (
        "Runtime dependencies are pinned to one version in the manifest "
        "and the lock agrees."
    ),
}
INSTALL_CMD = (
    "python3 -m venv .venv && .venv/bin/python -m pip install -r "
    "requirements.lock"
)
READY_CMD = "test -x .venv/bin/python && .venv/bin/python -c 'import pytest'"
CHECK_CMD = ".venv/bin/python -m pytest -q tests && ruff check ."
PYPROJECT_NAME = "fixture-python-service"
PINNED_RUNTIME = "python-dotenv"
PINNED_RUNTIME_VERSION = "1.0.1"
PLANTED_RUNTIME = "python-json-logger"
PLANTED_LOCK_VERSION = "2.0.7"
DEV_PYTEST = "pytest==8.3.3"
EXPECTED_LOCK_PINS = {
    "python-dotenv": PINNED_RUNTIME_VERSION,
    "python-json-logger": PLANTED_LOCK_VERSION,
    "pytest": "8.3.3",
}
INSTALL_CAP = 120
REFUSAL_LINE_RE = re.compile(
    r"^[a-z][a-z0-9_-]*: expected .+; found .+; needed .+$"
)
PS06_STUB_MARK = 'monkeypatch.setattr(api, "parse_request"'
GET_LINE = '{"verb": "get", "name": "fixture"}'
MISSING_GET_LINE = '{"verb": "get"}'
BEARER_TOKEN = "ps-03-planted-bearer-token"
EXPORT_NAME = "records"
RUFF_REQUIRED = ">=0.16,<0.17"
RUFF_TARGET = "py312"
RUFF_SELECT = ("E4", "E7", "E9", "F", "I", "B", "UP")
GITIGNORE_MARKS = (
    ".venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    "*.egg-info/",
)


def _lint_the_lane_copies_under_the_lane_contract(made: Path, world: World) -> None:
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
    world.plant_text(made / "ops" / "ruff.toml", text.replace('"ops/', '"'))
APPARATUS_PROBES = tuple(f"probes/ps{index:02d}.py" for index in range(1, 8))
APPARATUS_FIXES = tuple(f"fixes/ps{index:02d}.patch" for index in range(1, 8))
EXPECTED_PROBE_PATHS = dict(zip(EXPECTED_IDS, APPARATUS_PROBES, strict=True))
EXPECTED_FIX_PATHS = dict(zip(EXPECTED_IDS, APPARATUS_FIXES, strict=True))

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
FAULT_ID_RE = re.compile(r"\b(PS-\d{2})\b")
COUNTS_RE = re.compile(
    r"red=(?P<red>\d+)\s+green=(?P<green>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)"
)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
UNITTEST_RAN_RE = re.compile(r"Ran ([1-9]\d*) tests?")
PYTEST_PASSED_RE = re.compile(r"\b([1-9]\d*) passed\b")
CONF_ASSIGN_RE = re.compile(
    r"^(?P<key>[A-Z][A-Z0-9_]*)='(?P<value>[^']*)'\s*$", re.MULTILINE
)
_SPEC_SEPS = ("===", "==", "!=", "~=", ">=", "<=", ">", "<")

# Contract-derived dirty shapes at the practiced span (slice-3 idiom).
KIND_DIRTY = {
    "K1": re.compile(
        r"shell\s*=\s*True|os\.system|os\.popen|/bin/sh|\bsh\s+-c\b|\bbash\s+-c\b"
    ),
    "K2": re.compile(r"root\s*/\s*name|os\.path\.join|\bPath\([^)]*\)\s*/"),
    "K4": re.compile(r"headers\s*="),
    "K5": re.compile(r"except\s+Exception\s*:"),
    "K6": re.compile(r"stop\s*\+\s*1|start\s*:\s*stop\s*\+\s*1"),
    "K7": re.compile(r"monkeypatch\.setattr|patch\(|Mock\("),
    "K9": re.compile(r">=|~=|\^|>|\*"),
}

# Contract-named clean neighbours, discovered without FAULTS.json.
# These must not match a span that still contains the planted fault.
KIND_CLEAN = {
    "K1": re.compile(
        r"subprocess\.(?:run|Popen|call|check_call|check_output)\s*\(\s*\["
        r"[^\]\n]+\](?![^)\n]*shell\s*=\s*True)"
    ),
    "K2": re.compile(r"\.relative_to\(|\.is_relative_to\("),
    "K4": re.compile(r"authorization\s*=\s*\[REDACTED\]", re.IGNORECASE),
    "K5": re.compile(r"except KeyError"),
    "K6": re.compile(r"range\(\s*0\s*,\s*total\s*,\s*size\s*\)"),
    "K7": re.compile(r"^def test_parser_reads_a_get\b", re.MULTILINE),
    "K9": re.compile(r"python-dotenv==1\.0\.1"),
}

DIRTY_LOOKALIKES = {
    "K1": 'subprocess.run(["tar"], shell=True)',
    "K2": "candidate.relative_to(base); return root / name",
    "K4": 'print(headers); print("authorization=[REDACTED]")',
    "K5": "try: run()\nexcept KeyError: return []",
    "K6": "range(0, total + 1, size)",
    "K7": (
        'monkeypatch.setattr(api, "parse_request", fake); parse_request("{}")'
    ),
    "K9": "python-dotenv==9.9.9",
}

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


def _file_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _rule_prefix(token: str) -> str:
    return str(token).rstrip("0123456789")


def _ruff_select(data: dict) -> list[str]:
    lint = data.get("lint")
    raw = None
    if isinstance(lint, dict) and "select" in lint:
        raw = lint.get("select")
    elif "select" in data:
        raw = data.get("select")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _norm_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name)).lower()


def _dep_name_spec(item: str) -> tuple[str, str]:
    text = str(item).strip()
    if not text:
        return "", ""
    name_part = text
    spec = ""
    for sep in _SPEC_SEPS:
        idx = text.find(sep)
        if idx != -1:
            name_part = text[:idx]
            spec = text[idx:]
            break
    name = name_part.split("[", 1)[0].strip()
    return name, spec.strip()


def _is_exact_pin(spec: str) -> bool:
    return spec.startswith("==") and not spec.startswith("===")


def _requirement_line_name(line: str) -> str | None:
    stripped = line.split("#", 1)[0].strip()
    if not stripped or stripped.startswith("-"):
        return None
    stripped = stripped.rstrip("\\").strip()
    if not stripped:
        return None
    body = stripped.split(";", 1)[0].split("--", 1)[0].strip()
    name, spec = _dep_name_spec(body)
    if not name:
        return None
    if not _is_exact_pin(spec):
        return ""
    return name


def _lock_names(text: str) -> list[str]:
    names: list[str] = []
    for line in text.splitlines():
        name = _requirement_line_name(line)
        if name is None:
            continue
        names.append(name)
    return names


def _lock_pins(text: str) -> dict[str, str]:
    """name -> version for exact == pins; comments never count as a pin."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or stripped.startswith("-"):
            continue
        stripped = stripped.rstrip("\\").strip()
        if not stripped:
            continue
        body = stripped.split(";", 1)[0].split("--", 1)[0].strip()
        name, spec = _dep_name_spec(body)
        if not name or not _is_exact_pin(spec):
            continue
        pins[_norm_name(name)] = spec[2:].strip()
    return pins




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


def _patch_hunks(text: str) -> list[tuple[str, int, int, int, int]]:
    """(path, old_start, old_count, new_start, new_count) per @@ hunk."""
    current: str | None = None
    hunks: list[tuple[str, int, int, int, int]] = []
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip().removeprefix("b/")
            current = None if path == "/dev/null" else path
            continue
        hunk = HUNK_RE.match(line)
        if hunk is None or current is None:
            continue
        old_start = int(hunk.group(1))
        old_count = int(hunk.group(2)) if hunk.group(2) is not None else 1
        new_start = int(hunk.group(3))
        new_count = int(hunk.group(4)) if hunk.group(4) is not None else 1
        hunks.append((current, old_start, old_count, new_start, new_count))
    return hunks


def _iter_app_sources(root: Path) -> list[tuple[str, Path, str]]:
    found: list[tuple[str, Path, str]] = []
    for folder in ("service", "tests"):
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            found.append((rel, path, path.read_text(encoding="utf-8")))
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        found.append(
            ("pyproject.toml", pyproject, pyproject.read_text(encoding="utf-8"))
        )
    return found


def _snippet_is_clean(kind: str, snippet: str, window: str | None = None) -> bool:
    """A neighbour span is clean only if it matches the clean shape and not the fault."""
    clean = KIND_CLEAN.get(kind)
    dirty = KIND_DIRTY.get(kind)
    if clean is None or not clean.search(snippet):
        return False
    body = window if window is not None else snippet
    if dirty is not None and dirty.search(body):
        return False
    if kind == "K4" and (
        re.search(r"print\(\s*headers\b", body)
        or re.search(r"headers\s*=(?!\s*\[REDACTED\])", body)
    ):
        return False
    if kind == "K5" and re.search(r"return\s*\[\s*\]", body):
        return False
    if kind == "K6" and re.search(r"total\s*\+\s*1", body):
        return False
    if kind == "K9":
        return PINNED_RUNTIME_VERSION in snippet
    return True


def discover_clean_neighbours(root: Path) -> dict[str, list[tuple[str, int, int, str]]]:
    """Find clean-neighbour spans from the contract shapes, not FAULTS.json."""
    found: dict[str, list[tuple[str, int, int, str]]] = {
        kind: [] for kind in KIND_CLEAN
    }
    for rel, _path, text in _iter_app_sources(root):
        for kind, pattern in KIND_CLEAN.items():
            if kind == "K7" and not rel.startswith("tests/"):
                continue
            if kind == "K9" and rel != "pyproject.toml":
                continue
            if kind not in {"K7", "K9"} and not rel.startswith("service/"):
                continue
            for match in pattern.finditer(text):
                start = text[: match.start()].count("\n") + 1
                end = text[: match.end()].count("\n") + 1
                lines = text.splitlines()
                take_end = end + 6 if kind == "K5" else end
                snippet = "\n".join(lines[start - 1 : min(len(lines), take_end)])
                if not _snippet_is_clean(kind, snippet):
                    continue
                found[kind].append((rel, start, end, snippet))
    return found


def _drop_imported_service() -> None:
    for key in list(sys.modules):
        if key == "service" or key.startswith("service."):
            del sys.modules[key]


def _import_service(directory: Path):
    _drop_imported_service()
    package_dir = directory / "service"
    spec = importlib.util.spec_from_file_location(
        "service",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    if spec is None or spec.loader is None:
        raise AssertionError(f"service package cannot be loaded from {package_dir}")
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["service"] = pkg
    spec.loader.exec_module(pkg)
    for name in ("api", "audit", "export", "paging", "store"):
        child = importlib.import_module(f"service.{name}")
        setattr(pkg, name, child)
    return pkg




class PythonServiceLayout(unittest.TestCase):

    def test_gitignore_names_install_residue(self):
        text = _file_text(APP / ".gitignore")
        for mark in GITIGNORE_MARKS:
            with self.subTest(mark=mark):
                self.assertIn(
                    mark,
                    text,
                    f".gitignore must name {mark}; the scratch inherits it",
                )

    def test_ruff_toml_selects_production_dialect_not_security(self):
        data = _load_toml(APP / "ruff.toml")
        self.assertEqual(
            data.get("required-version"),
            RUFF_REQUIRED,
            f"ruff.toml required-version must be {RUFF_REQUIRED}",
        )
        self.assertEqual(
            data.get("target-version"),
            RUFF_TARGET,
            f"ruff.toml target-version must be {RUFF_TARGET}",
        )
        select = _ruff_select(data)
        for token in RUFF_SELECT:
            with self.subTest(token=token):
                self.assertIn(
                    token,
                    select,
                    f"ruff.toml must select {token}; found {select!r}",
                )
        prefixes = {_rule_prefix(token) for token in select}
        self.assertNotIn(
            "S",
            prefixes,
            "ruff.toml must not select S, so K1 and K4 survive the app lint",
        )

    def test_pyproject_parses_as_the_production_dialect(self):
        data = _load_toml(APP / "pyproject.toml")
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        self.assertEqual(
            project.get("name"),
            PYPROJECT_NAME,
            f"[project] name must be {PYPROJECT_NAME}",
        )
        requires = str(project.get("requires-python") or "").replace(" ", "")
        self.assertEqual(
            requires,
            ">=3.12",
            "requires-python must be >=3.12",
        )
        scripts = project.get("scripts")
        self.assertIsInstance(scripts, dict, "[project.scripts] must be a table")
        self.assertEqual(
            len(scripts),
            1,
            f"[project.scripts] must have one entry point; found {scripts!r}",
        )
        self.assertTrue(
            all(isinstance(value, str) and value.strip() for value in scripts.values()),
            f"[project.scripts] values must be non-empty; found {scripts!r}",
        )
        optional = (
            project.get("optional-dependencies")
            if isinstance(project.get("optional-dependencies"), dict)
            else {}
        )
        dev = optional.get("dev")
        self.assertIsInstance(dev, list, "optional-dependencies.dev must be a list")
        self.assertTrue(
            any(_norm_name(_dep_name_spec(str(item))[0]) == "pytest" for item in dev),
            f"dev extra must name pytest; found {dev!r}",
        )
        self.assertTrue(
            any(DEV_PYTEST.replace(" ", "") in str(item).replace(" ", "") for item in dev),
            f"dev extra must pin {DEV_PYTEST}; found {dev!r}",
        )
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        pytest_tool = (
            tool.get("pytest") if isinstance(tool.get("pytest"), dict) else {}
        )
        ini = (
            pytest_tool.get("ini_options")
            if isinstance(pytest_tool.get("ini_options"), dict)
            else {}
        )
        testpaths = ini.get("testpaths")
        if isinstance(testpaths, str):
            names = [testpaths]
        elif isinstance(testpaths, list):
            names = [str(item) for item in testpaths]
        else:
            names = []
        self.assertIn(
            "tests",
            names,
            "[tool.pytest.ini_options] testpaths must include tests",
        )

    def test_runtime_dependencies_are_pinned_except_the_planted_k9(self):
        data = _load_toml(APP / "pyproject.toml")
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        deps = project.get("dependencies")
        self.assertIsInstance(deps, list, "project.dependencies must be a list")
        self.assertEqual(
            len(deps),
            2,
            f"two pure-Python runtime dependencies; found {deps!r}",
        )
        parsed = [_dep_name_spec(str(item)) for item in deps]
        names = {_norm_name(name) for name, _spec in parsed if name}
        self.assertEqual(
            names,
            {_norm_name(PINNED_RUNTIME), _norm_name(PLANTED_RUNTIME)},
            f"runtime dependencies must be {PINNED_RUNTIME} and {PLANTED_RUNTIME}",
        )
        exact = [
            (name, spec) for name, spec in parsed if name and _is_exact_pin(spec)
        ]
        unpinned = [
            name for name, spec in parsed if name and not _is_exact_pin(spec)
        ]
        self.assertEqual(
            [(_norm_name(name), spec.replace(" ", "")) for name, spec in exact],
            [
                (
                    _norm_name(PINNED_RUNTIME),
                    f"=={PINNED_RUNTIME_VERSION}",
                )
            ],
            f"{PINNED_RUNTIME}=={PINNED_RUNTIME_VERSION} is the clean runtime pin; "
            f"exact={exact!r}",
        )
        self.assertEqual(
            [_norm_name(name) for name in unpinned],
            [_norm_name(PLANTED_RUNTIME)],
            f"{PLANTED_RUNTIME} carries the K9 range; unpinned={unpinned!r}",
        )

    def test_requirements_lock_names_every_manifest_package(self):
        data = _load_toml(APP / "pyproject.toml")
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        runtime = project.get("dependencies")
        self.assertIsInstance(runtime, list)
        optional = (
            project.get("optional-dependencies")
            if isinstance(project.get("optional-dependencies"), dict)
            else {}
        )
        dev = optional.get("dev") if isinstance(optional.get("dev"), list) else []
        wanted = []
        for item in list(runtime) + list(dev):
            name, _spec = _dep_name_spec(str(item))
            if name:
                wanted.append(_norm_name(name))
        self.assertIn(_norm_name(PINNED_RUNTIME), wanted)
        self.assertIn(_norm_name(PLANTED_RUNTIME), wanted)
        self.assertIn("pytest", wanted)
        lock_text = _file_text(APP / "requirements.lock")
        self.assertTrue(
            lock_text.strip(),
            "requirements.lock must name every package at one == version",
        )
        locked = _lock_names(lock_text)
        self.assertTrue(
            locked,
            "requirements.lock must contain name==version lines",
        )
        self.assertNotIn(
            "",
            locked,
            "every requirements.lock package must be pinned with ==",
        )
        locked_norm = {_norm_name(name) for name in locked}
        missing = [name for name in wanted if name not in locked_norm]
        self.assertEqual(
            missing,
            [],
            f"requirements.lock must name every package in the manifest: {missing}",
        )
        pins = _lock_pins(lock_text)
        self.assertTrue(pins, "requirements.lock must pin name==version, not comments")
        for package, version in EXPECTED_LOCK_PINS.items():
            with self.subTest(package=package):
                self.assertEqual(
                    pins.get(_norm_name(package)),
                    version,
                    f"requirements.lock must pin {package}=={version}; "
                    "a comment or a drifted pin is not the lock",
                )
        planted_line = f"{PLANTED_RUNTIME}=={PLANTED_LOCK_VERSION}"
        self.assertTrue(
            any(
                line.split("#", 1)[0].replace(" ", "").strip() == planted_line
                for line in lock_text.splitlines()
            ),
            f"requirements.lock must carry a {planted_line} line, not a comment",
        )





class PythonServiceManifest(unittest.TestCase):




    def test_manifest_kind_and_visible_to_match_the_contract(self):
        faults = fault_rows(load_json_object(MANIFEST))
        mapping = {
            row.get("id"): (row.get("kind"), row.get("visible_to"))
            for row in faults
        }
        self.assertEqual(
            mapping,
            EXPECTED_KIND_VISIBLE,
            "kind and visible_to must match the slice-3 fault table",
        )
        severities = {row.get("id"): row.get("severity") for row in faults}
        self.assertEqual(
            severities,
            EXPECTED_SEVERITY,
            "per-id severity must match the slice-3 fault table; the sweep "
            "accepts any p1/p2/p3 and is not this pin",
        )

    def test_manifest_location_and_neighbour_paths_match_the_contract(self):
        faults = fault_rows(load_json_object(MANIFEST))
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
            "seven planted faults before path pins run",
        )
        for row in faults:
            fault_id = row.get("id")
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            neighbour = (
                row.get("neighbour") if isinstance(row.get("neighbour"), dict) else {}
            )
            with self.subTest(fault=fault_id, field="location"):
                self.assertEqual(
                    loc.get("path"),
                    EXPECTED_LOCATION_PATHS[fault_id],
                    f"{fault_id} location.path must be the contract's file",
                )
            with self.subTest(fault=fault_id, field="neighbour"):
                self.assertEqual(
                    neighbour.get("path"),
                    EXPECTED_NEIGHBOUR_PATHS[fault_id],
                    f"{fault_id} neighbour.path must be the contract's file",
                )

    def test_not_planted_names_k3_and_k8(self):
        data = load_json_object(MANIFEST)
        not_planted = data.get("not_planted")
        self.assertTrue(
            not_planted,
            "not_planted must name K3/K8 coverage and the candidate eighth",
        )
        if isinstance(not_planted, dict):
            blob = " ".join(f"{key} {value}" for key, value in not_planted.items())
            keys = {str(key) for key in not_planted}
        else:
            blob = str(not_planted)
            keys = set()
        self.assertTrue(
            "K3" in keys or bool(re.search(r"\bK3\b", blob)),
            "not_planted must name K3 (covered by python-tooling)",
        )
        self.assertTrue(
            "K8" in keys or bool(re.search(r"\bK8\b", blob)),
            "not_planted must name K8 (covered by python-tooling)",
        )
        self.assertRegex(
            blob,
            r"(?i)python-tooling",
            "not_planted must say K3 and K8 are covered by python-tooling",
        )
        self.assertTrue(
            "K9" in keys or bool(re.search(r"\bK9\b", blob)),
            "not_planted must name the candidate eighth as a second K9",
        )
        self.assertRegex(
            blob,
            r"(?i)lock",
            "not_planted must say the eighth is a lock that contradicts the pin",
        )
        self.assertRegex(
            blob,
            r"(?i)grad",
            "not_planted must say the eighth waits on the first grading run",
        )

    def test_toolchain_install_and_ready_present(self):
        data = load_json_object(MANIFEST)
        toolchain = (
            data.get("toolchain") if isinstance(data.get("toolchain"), dict) else {}
        )
        install = toolchain.get("install") if isinstance(toolchain.get("install"), str) else ""
        ready = toolchain.get("ready") if isinstance(toolchain.get("ready"), str) else ""
        self.assertEqual(
            install,
            INSTALL_CMD,
            "toolchain.install must be the README's install command",
        )
        self.assertEqual(
            ready,
            READY_CMD,
            "toolchain.ready must prove .venv/bin/python imports pytest",
        )
        readme = _file_text(APP / "README.md")
        self.assertIn(
            install,
            readme,
            "toolchain.install is the command the README names",
        )

    def test_no_fault_row_carries_needs(self):
        faults = fault_rows(load_json_object(MANIFEST))
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
            "seven planted faults before needs pins run",
        )
        for row in faults:
            with self.subTest(fault=row.get("id")):
                self.assertNotIn(
                    "needs",
                    row,
                    f"{row.get('id')} must not carry a needs key, empty or not; "
                    "prove is stdlib-reachable on python3 alone",
                )

    def test_manifest_rows_name_assigned_probes_fixes_and_spans(self):
        faults = fault_rows(load_json_object(MANIFEST))
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
            "seven planted faults before probe/fix/span pins run",
        )
        for row in faults:
            fault_id = row.get("id")
            probe = row.get("probe") if isinstance(row.get("probe"), str) else ""
            fix = row.get("fix") if isinstance(row.get("fix"), str) else ""
            declared = (
                row.get("declared") if isinstance(row.get("declared"), dict) else {}
            )
            practiced = (
                row.get("practiced") if isinstance(row.get("practiced"), dict) else {}
            )
            location = (
                row.get("location") if isinstance(row.get("location"), dict) else {}
            )
            with self.subTest(fault=fault_id, field="probe"):
                self.assertIn(
                    EXPECTED_PROBE_PATHS[fault_id],
                    _probe_script_paths(probe),
                    f"{fault_id} probe must name {EXPECTED_PROBE_PATHS[fault_id]}",
                )
                self.assertEqual(
                    _probe_script_paths(probe),
                    [EXPECTED_PROBE_PATHS[fault_id]],
                    f"{fault_id} probe must be exactly {EXPECTED_PROBE_PATHS[fault_id]}",
                )
            with self.subTest(fault=fault_id, field="fix"):
                self.assertEqual(
                    fix,
                    EXPECTED_FIX_PATHS[fault_id],
                    f"{fault_id} fix must be {EXPECTED_FIX_PATHS[fault_id]}",
                )
            with self.subTest(fault=fault_id, field="declared"):
                self.assertEqual(
                    declared.get("path"),
                    "README.md",
                    f"{fault_id} declared.path must be the README declaration",
                )
                self.assertEqual(
                    declared.get("text"),
                    DECLARED[fault_id],
                    f"{fault_id} declared.text must be the contract sentence",
                )
            with self.subTest(fault=fault_id, field="practiced"):
                self.assertEqual(
                    practiced.get("path"),
                    location.get("path"),
                    f"{fault_id} practiced.path must equal location.path",
                )
                self.assertEqual(
                    _as_lines(practiced.get("lines")),
                    _as_lines(location.get("lines")),
                    f"{fault_id} practiced.lines must equal location.lines",
                )

    def test_check_command_names_pytest_and_ruff(self):
        check = load_json_object(MANIFEST).get("check")
        text = check if isinstance(check, str) else ""
        self.assertEqual(
            text,
            CHECK_CMD,
            "check is the venv pytest over tests/ and ruff check .",
        )
        stripped = _strip_shell_comments(text)
        self.assertRegex(
            stripped,
            r"\bpytest\b",
            "check is pytest over tests/ from the venv (comments do not count)",
        )
        self.assertRegex(
            stripped,
            r"\bruff\b",
            "check includes ruff check . from the app root (comments do not count)",
        )
        self.assertIn(
            ".venv",
            stripped,
            "check runs through the venv the install creates",
        )




class _RepoFreeze(unittest.TestCase):
    """Live fixture roots, findings store, calibration export, and this repo stay put."""

    def setUp(self):
        self.world = World(
            prefix="python-service-app-",
            ambient=(
                "PIP_CACHE_DIR",
                "PIP_CONFIG_FILE",
                "PIP_INDEX_URL",
                "PIP_EXTRA_INDEX_URL",
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "http_proxy",
                "https_proxy",
                "no_proxy",
            ),
        )
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
        self.conf_path = self.root / "copy-list.txt"
        self.lane_conf_path = self.root / "lane.conf"
        self.world.plant_text(self.lane_conf_path, "LANE_NAME='agentscient'\n")
        listed = "".join(f"{path}\n" for path in LANE_COPY_PATHS)
        self.world.plant_bytes(self.conf_path, listed.encode("utf-8"))

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
        return env

    def scratch_env(self) -> dict[str, str]:
        env = self.world.env({"TZ": "UTC"})
        self.assertEqual(env["HOME"], str(self.world.home))
        self.assertEqual(env["XDG_STATE_HOME"], str(self.world.xdg_state))
        self.assertNotIn("LANE_CONF", env)
        self.assertNotIn("LAUNCHER_PIN", env)
        self.assertNotIn("WF_AGENT", env)
        return env


class PythonServiceMakeProve(_RepoFreeze):
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
                f"colour line names a PS-* fault id: {line!r}",
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

    def toolchain_ready(self, directory: Path):
        manifest = load_json_object(directory / "FAULTS.json")
        toolchain = (
            manifest.get("toolchain")
            if isinstance(manifest.get("toolchain"), dict)
            else {}
        )
        ready = toolchain.get("ready") if isinstance(toolchain.get("ready"), str) else ""
        self.assertEqual(
            ready,
            READY_CMD,
            "fresh make must carry toolchain.ready before check runs",
        )
        try:
            return run(
                ["/bin/sh", "-c", ready],
                cwd=directory,
                env=self.scratch_env(),
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"toolchain.ready exceeded {CHECK_CAP}s: {exc}")

    def install_env(self) -> dict[str, str]:
        return self.scratch_env()

    def toolchain_install(self, directory: Path):
        manifest = load_json_object(directory / "FAULTS.json")
        toolchain = (
            manifest.get("toolchain")
            if isinstance(manifest.get("toolchain"), dict)
            else {}
        )
        install = (
            toolchain.get("install") if isinstance(toolchain.get("install"), str) else ""
        )
        self.assertEqual(
            install,
            INSTALL_CMD,
            "fresh make must carry toolchain.install before check runs",
        )
        try:
            return run(
                ["/bin/sh", "-c", install],
                cwd=directory,
                env=self.install_env(),
                timeout=INSTALL_CAP,
            )
        except Timeout as exc:
            self.fail(f"toolchain.install exceeded {INSTALL_CAP}s: {exc}")

    def work_dir(self, name: str) -> Path:
        path = self.root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def python_service(self, directory: Path, argv: list[str], **kwargs):
        env = kwargs.pop("env", None) or self.scratch_env()
        env = dict(env)
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        stdin = kwargs.pop("input", None)
        timeout = kwargs.pop("timeout", PROBE_CAP)
        self.assertEqual(kwargs, {}, f"unsupported run options: {kwargs!r}")
        try:
            return run(
                [sys.executable, "-m", "service", *argv],
                cwd=directory,
                env=env,
                stdin=stdin,
                timeout=timeout,
            )
        except Timeout as exc:
            self.fail(f"python -m service {argv} exceeded cap: {exc}")

    def contract_planted(self, directory: Path, fault_id: str) -> bool:
        """True while the contract operation still shows the planted fault."""
        if fault_id == "PS-01":
            return self._ps01_marker_appears(directory)
        if fault_id == "PS-02":
            return self._ps02_escapes(directory)
        if fault_id == "PS-03":
            return self._ps03_token_logged(directory)
        if fault_id == "PS-04":
            return self._ps04_empty_success(directory)
        if fault_id == "PS-05":
            return self._ps05_inclusive(directory)
        if fault_id == "PS-06":
            return self._ps06_stub_present(directory)
        if fault_id == "PS-07":
            return self._ps07_unpinned(directory)
        self.fail(f"no contract operation for {fault_id}")
        return False

    def _ps01_marker_appears(self, directory: Path) -> bool:
        service = _import_service(directory)
        work = self.work_dir("ps01-work")
        payload = work / "payload"
        payload.mkdir(exist_ok=True)
        self.world.plant_text(payload / "item.txt", "x\n")
        marker = work / "INJECTED"
        if marker.exists():
            marker.unlink()
        name = f"payload; touch {marker}"
        with suppress(Exception):
            service.export.export_archive(work, name)
        return marker.is_file()

    def _ps02_escapes(self, directory: Path) -> bool:
        service = _import_service(directory)
        work = self.work_dir("ps02-work")
        try:
            service.store.record_path(work, "../outside.json")
        except ValueError:
            return False
        return True

    def _ps03_token_logged(self, directory: Path) -> bool:
        service = _import_service(directory)
        dest = self.work_dir("ps03-work") / "request.log"
        if dest.exists():
            dest.unlink()
        request = {"headers": {"authorization": f"Bearer {BEARER_TOKEN}"}}
        with suppress(Exception):
            service.audit.log_request(request, dest)
        body = dest.read_text(encoding="utf-8") if dest.is_file() else ""
        return BEARER_TOKEN in body

    def _ps04_empty_success(self, directory: Path) -> bool:
        service = _import_service(directory)
        store = self.work_dir("ps04-store")
        result = service.api.handle("not json", store)
        self.assertIsInstance(result, dict, "handle must return a dict")
        return result.get("ok") is True

    def _ps05_inclusive(self, directory: Path) -> bool:
        service = _import_service(directory)
        result = service.paging.page([1, 2, 3], 0, 2)
        return list(result) != [1, 2]

    def _ps06_stub_present(self, directory: Path) -> bool:
        text = (directory / "tests" / "test_api.py").read_text(encoding="utf-8")
        return PS06_STUB_MARK in text

    def _ps07_unpinned(self, directory: Path) -> bool:
        data = tomllib.loads(
            (directory / "pyproject.toml").read_text(encoding="utf-8")
        )
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        deps = project.get("dependencies")
        self.assertIsInstance(deps, list)
        for item in deps:
            _name, spec = _dep_name_spec(str(item))
            if not _is_exact_pin(spec):
                return True
        return False


    def test_scratch_runs_suites_and_dispatch_dry_run(self):
        made = self.made_app("portable")
        _lint_the_lane_copies_under_the_lane_contract(made, self.world)
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
        self.world.plant_bytes(scope, b"fixture-apps slice-3 dry-run scope\n")
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
            with self.subTest(fault=fault_id, stage="assigned-paths"):
                self.assertEqual(
                    loc_path,
                    EXPECTED_LOCATION_PATHS[fault_id],
                    f"{fault_id} location.path must be the contract file",
                )
                self.assertIn(
                    EXPECTED_PROBE_PATHS[fault_id],
                    _probe_script_paths(probe_cmd),
                    f"{fault_id} probe command must name {EXPECTED_PROBE_PATHS[fault_id]}",
                )
                self.assertEqual(
                    fix_rel,
                    EXPECTED_FIX_PATHS[fault_id],
                    f"{fault_id} fix must be {EXPECTED_FIX_PATHS[fault_id]}",
                )
            with self.subTest(fault=fault_id, stage="kind-and-red"):
                dirty = KIND_DIRTY.get(kind)
                self.assertIsNotNone(dirty, f"no dirty shape for {kind}")
                self.assertRegex(
                    span,
                    dirty,
                    f"{fault_id} practiced span must look like {kind}, not a label: "
                    f"{span!r}",
                )
                self.assertTrue(
                    self.contract_planted(made, fault_id),
                    f"{fault_id} contract operation must still show the planted "
                    "fault; a comment, a sentinel probe, or a matching regex is "
                    "not the proof",
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
                patch_text = patch.decode("utf-8")
                hunks = _patch_hunks(patch_text)
                self.assertEqual(
                    len(hunks),
                    1,
                    f"{fault_id} fix must be one hunk; found {len(hunks)}: {hunks!r}",
                )
                hunk_path, old_start, old_count, _new_start, _new_count = hunks[0]
                self.assertEqual(
                    hunk_path,
                    loc_path,
                    f"{fault_id} hunk path must be {loc_path}, found {hunk_path}",
                )
                start, end = loc_lines
                old_end = old_start if old_count == 0 else old_start + old_count - 1
                self.assertGreaterEqual(
                    old_start,
                    start,
                    f"{fault_id} hunk old start {old_start} is before declared "
                    f"span {start}-{end}",
                )
                self.assertLessEqual(
                    old_end,
                    end,
                    f"{fault_id} hunk old end {old_end} is after declared span "
                    f"{start}-{end}",
                )
                removed = _patch_removed_lines(patch_text)
                self.assertEqual(
                    set(removed),
                    {loc_path},
                    f"{fault_id} fix must touch only {loc_path}, found {sorted(removed)}",
                )
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
                self.assertFalse(
                    self.contract_planted(made, fault_id),
                    f"{fault_id} contract operation must go green after its own fix",
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
                    self.assertTrue(
                        self.contract_planted(made, other_id),
                        f"{other_id} contract operation must stay planted while "
                        f"only {fault_id} is fixed",
                    )
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
            self.assertTrue(
                self.contract_planted(made, fault_id),
                f"{fault_id} contract operation must be planted again after reset",
            )
            colour = self.run_probe(made, probe_cmd)
            self.assertEqual(
                colour,
                "RED",
                f"{fault_id} probe must be RED again after reset",
            )

    def test_clean_neighbours_are_found_apart_from_the_manifest(self):
        made = self.made_app("neighbours")
        for kind, sample in DIRTY_LOOKALIKES.items():
            with self.subTest(kind=kind, stage="lookalike"):
                self.assertFalse(
                    _snippet_is_clean(kind, sample, sample),
                    f"{kind} classifier must not accept a span that still "
                    f"contains its fault: {sample!r}",
                )
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
                    f"no clean neighbour for {kind} in service/, tests/, or "
                    "pyproject.toml; discovery does not read FAULTS.json neighbour "
                    "fields",
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

    def test_clean_neighbours_are_exercised(self):
        made = self.made_app("neighbours-live")
        service = _import_service(made)

        with self.subTest(kind="K1"):
            work = self.work_dir("nb-k1")
            marker = work / "INJECTED"
            target = work / f"sum-me; touch {marker}"
            # The injection string carries the marker's absolute path, so the
            # file lives under a nested directory named by it.
            target.parent.mkdir(parents=True, exist_ok=True)
            self.world.plant_text(target, "payload\n")
            with suppress(Exception):
                service.export.checksum_file(target)
            self.assertFalse(
                marker.exists(),
                "checksum_file must pass the path as an argv entry; a shell "
                "interpolation would create the marker",
            )

        with self.subTest(kind="K2"):
            work = self.work_dir("nb-k2")
            with self.assertRaises(
                ValueError,
                msg="checked_record_path must refuse a path that escapes the root",
            ):
                service.store.checked_record_path(work, "../outside.json")

        with self.subTest(kind="K4"):
            dest = self.work_dir("nb-k4") / "redacted.log"
            request = {"headers": {"authorization": f"Bearer {BEARER_TOKEN}"}}
            service.audit.log_request_redacted(request, dest)
            body = dest.read_text(encoding="utf-8") if dest.is_file() else ""
            self.assertNotIn(
                BEARER_TOKEN,
                body,
                "log_request_redacted must not write the bearer token",
            )
            self.assertRegex(
                body,
                r"\[REDACTED\]",
                "log_request_redacted must write authorization=[REDACTED]",
            )

        with self.subTest(kind="K5"):
            store = self.work_dir("nb-k5")
            result = service.api.handle(MISSING_GET_LINE, store)
            self.assertIsInstance(result, dict)
            self.assertNotEqual(
                result.get("ok"),
                True,
                "a get missing its field must be a problem, not empty success",
            )
            blob = json.dumps(result)
            self.assertRegex(
                blob,
                r"(?i)name|field|missing|needed",
                "the get KeyError path must name the missing field",
            )

        with self.subTest(kind="K6"):
            self.assertEqual(
                service.paging.page_count(4, 2),
                2,
                "page_count must use range(0, total, size); total+1 is inclusive",
            )

        with self.subTest(kind="K7"):
            parsed = service.api.parse_request(GET_LINE)
            self.assertIsNotNone(
                parsed,
                "test_parser_reads_a_get's neighbour is a real parse_request "
                "call on a literal line",
            )

        with self.subTest(kind="K9"):
            data = tomllib.loads((made / "pyproject.toml").read_text(encoding="utf-8"))
            project = (
                data.get("project") if isinstance(data.get("project"), dict) else {}
            )
            deps = project.get("dependencies")
            self.assertIsInstance(deps, list)
            pins = [_dep_name_spec(str(item)) for item in deps]
            dotenv = [
                spec.replace(" ", "")
                for name, spec in pins
                if _norm_name(name) == _norm_name(PINNED_RUNTIME)
            ]
            self.assertEqual(
                dotenv,
                [f"=={PINNED_RUNTIME_VERSION}"],
                "the K9 neighbour is python-dotenv==1.0.1, not any == pin",
            )

    def test_ruff_check_green_on_a_fresh_make(self):
        made = self.made_app("ruff-fresh")
        ruff = self.world.require("ruff")
        env = self.scratch_env()
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

    def test_app_check_green_when_ready_else_skip(self):
        made = self.made_app("check-fresh")
        tests_dir = made / "tests"
        self.assertTrue(tests_dir.is_dir(), "fresh make must carry tests/")
        test_files = list(tests_dir.glob("test_*.py"))
        self.assertGreaterEqual(
            len(test_files),
            1,
            "tests/ must contain at least one test_*.py; an empty suite is not a check",
        )
        self.assertFalse(
            (made / ".venv" / "bin" / "python").exists(),
            "make copies tracked files only; a fresh scratch has no venv until install",
        )
        install = self.toolchain_install(made)
        if install.exit != 0:
            self.skipTest(
                "UNREACHABLE: toolchain.install could not run "
                f"(exit {install.exit}): "
                f"{(install.stderr or install.stdout or '').strip()[:400]}"
            )
        ready = self.toolchain_ready(made)
        self.assertEqual(
            ready.exit,
            0,
            "toolchain.install must leave toolchain.ready green on a fresh make: "
            f"stdout={ready.stdout!r} stderr={ready.stderr!r}",
        )
        manifest = load_json_object(made / "FAULTS.json")
        check = manifest.get("check") if isinstance(manifest.get("check"), str) else ""
        self.assertEqual(check, CHECK_CMD, "fresh make must carry the contract check")
        env = self.scratch_env()
        try:
            proc = run(
                ["bash", "-c", check],
                cwd=made,
                env=env,
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"app check exceeded {CHECK_CAP}s: {exc}")
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(
            proc.exit,
            0,
            f"app check must be green on a fresh make when ready: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        passed = PYTEST_PASSED_RE.search(combined)
        self.assertIsNotNone(
            passed,
            f"check must actually run pytest, not skip the suite: {combined!r}",
        )
        self.assertGreaterEqual(int(passed.group(1)), 1, combined)
        self.assertNotRegex(combined, r"\bfailed\b", combined)

    def test_python_m_service_speaks_the_wire(self):
        made = self.made_app("service-run")
        with self.subTest(stage="no-args"):
            proc = self.python_service(made, [])
            self.assertEqual(
                proc.exit,
                2,
                "python -m service with no verb refuses before acting",
            )
            self.assertEqual(
                (proc.stdout or "").strip(),
                "",
                "a refusal has empty stdout",
            )
            err_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
            self.assertEqual(
                len(err_lines),
                1,
                f"one stderr refusal line: {proc.stderr!r}",
            )
            self.assertRegex(
                err_lines[0],
                REFUSAL_LINE_RE,
                "refusal is class: expected …; found …; needed …",
            )

        with self.subTest(stage="serve"):
            proc = self.python_service(
                made,
                ["serve"],
                input=GET_LINE + "\n",
            )
            self.assertIn(
                proc.exit,
                {0, 1},
                "serve of a well-formed get is success or findings, not a refusal: "
                f"exit={proc.exit} stderr={proc.stderr!r}",
            )
            out_lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
            self.assertEqual(
                len(out_lines),
                1,
                "serve writes one response line per request line: "
                f"{proc.stdout!r}",
            )
            payload = json.loads(out_lines[0])
            self.assertIsInstance(
                payload,
                dict,
                "the JSON-lines response is an object",
            )

        with self.subTest(stage="export-without-identity"):
            env = self.scratch_env()
            self.assertNotIn("WF_AGENT", env)
            proc = self.python_service(made, ["export", EXPORT_NAME], env=env)
            self.assertEqual(
                proc.exit,
                2,
                "export without identity refuses class identity",
            )
            self.assertEqual((proc.stdout or "").strip(), "", proc.stdout)
            err_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
            self.assertEqual(len(err_lines), 1, proc.stderr)
            self.assertRegex(err_lines[0], REFUSAL_LINE_RE)
            self.assertTrue(
                err_lines[0].startswith("identity:"),
                f"export identity gate is class identity: {err_lines[0]!r}",
            )
            self.assertIn("expected", err_lines[0])
            self.assertIn("found", err_lines[0])
            self.assertIn("needed", err_lines[0])

        with self.subTest(stage="export-with-identity"):
            env = self.scratch_env()
            env["WF_AGENT"] = AGENT
            proc = self.python_service(made, ["export", EXPORT_NAME], env=env)
            err = proc.stderr or ""
            if proc.exit == 2:
                err_lines = [ln for ln in err.splitlines() if ln.strip()]
                self.assertTrue(err_lines, err)
                self.assertFalse(
                    err_lines[0].startswith("identity:"),
                    "export with WF_AGENT must not refuse class identity: "
                    f"{err_lines[0]!r}",
                )
            else:
                self.assertIn(
                    proc.exit,
                    {0, 1},
                    f"export with identity is not an abort: exit={proc.exit} "
                    f"stderr={err!r}",
                )




if __name__ == "__main__":
    unittest.main()
