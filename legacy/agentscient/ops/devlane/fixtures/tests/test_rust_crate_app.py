"""rust-crate fixture-app tests, written from the slice-5 contract.

Contracts (only): the fixture-apps scope and the slices-3-to-5 plan in
the job input, using the slice-5 section (layout, seven faults by kind,
neighbours, probes, fixes, toolchain, manifest keys, the merged lever
`ops/bin/fixture.sh`). The app under
`ops/devlane/fixtures/apps/rust-crate` is the implementation and is not
read here beyond the paths the contract names. Slice 5 assigns no lever
change; `probe_timeout` and the preflight ignore list are slice 4's.

Fix round: Codex skeptic (record 20260904T094353Z-review-codex-e6b38f)
found 1 P1 and 4 P2. This file addresses all five. Each fault is pinned
to its shell gate, Cargo test target and behavioural probe, not to a
manifest-supplied command; the bin's read/write verbs and the identity
rule are exercised; FAULTS.json locations are pinned by function, line
span and exact quotations; RC-07's fix must be an exact = lock pin so a
compatible range does not pass; toolchain.install, toolchain.ready and
check are the contracted commands and are executed from the manifest.

Red at this ref because that directory is absent: the existence test
fails on `FAULTS.json`. The K3-row assertion fails on FAULT-KINDS.md as
it stands (Python, PHP). Make/prove/grade cases invoke the merged lever
and fail on their own promises rather than on an import.

The app, its own tests, probes, fixes, FAULTS.json, and the K3 idiom
cell are the implementer's. Worlds, commands and fakes go through
ops/testkit.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
import unittest
from pathlib import Path

from test_fixture_manifests import parse_kind_rows

from ops.testkit import Timeout, World, plant_text, run

REPO = Path(__file__).resolve().parents[4]
LEVER = REPO / "ops" / "bin" / "fixture.sh"
APPS = REPO / "ops" / "devlane" / "fixtures" / "apps"
APP = APPS / "rust-crate"
MANIFEST = APP / "FAULTS.json"
KINDS = APPS / "FAULT-KINDS.md"
CALIBRATION = REPO / "ops" / "conductor" / "findings" / "calibration.jsonl"

APP_NAME = "rust-crate"
AGENT = "Rust Crate Fixture Tester <rust-crate-fixture@example.invalid>"
RUN_CAP = 60
CHECK_CAP = 180
PROBE_CAP = 130
SUITES_CAP = 60
DISPATCH_CAP = 30
WARM_CAP = 180
METADATA_CAP = 30

EXPECTED_IDS = tuple(f"RC-{index:02d}" for index in range(1, 8))
REQUIRED_KINDS = frozenset({"K1", "K2", "K3", "K4", "K5", "K6", "K9"})
EXPECTED_KIND_VISIBLE = {
    "RC-01": ("K1", "audit"),
    "RC-02": ("K2", "both"),
    "RC-03": ("K3", "test"),
    "RC-04": ("K4", "audit"),
    "RC-05": ("K5", "test"),
    "RC-06": ("K6", "test"),
    "RC-07": ("K9", "audit"),
}
EXPECTED_SEVERITY = {
    "RC-01": "p1",
    "RC-02": "p1",
    "RC-03": "p2",
    "RC-04": "p1",
    "RC-05": "p2",
    "RC-06": "p2",
    "RC-07": "p2",
}
EXPECTED_LOCATION_PATHS = {
    "RC-01": "src/shell.rs",
    "RC-02": "src/store.rs",
    "RC-03": "src/render.rs",
    "RC-04": "src/audit.rs",
    "RC-05": "src/store.rs",
    "RC-06": "src/interval.rs",
    "RC-07": "Cargo.toml",
}
EXPECTED_DECLARATIONS = {
    "RC-01": (
        "User-supplied names are passed to Command as arguments, "
        "never through sh -c."
    ),
    "RC-02": "Record paths stay beneath the store root.",
    "RC-03": (
        "A count of zero is an answer and is distinct from an "
        "absent record."
    ),
    "RC-04": "Credentials never leave the process in failure logs.",
    "RC-05": (
        "An unreadable or corrupt store is reported as an error, "
        "never served as empty."
    ),
    "RC-06": (
        "Intervals are half-open, including their start and "
        "excluding their end."
    ),
    "RC-07": "Dependencies are pinned to one version and locked.",
}
# Functions that enclose the planted span. RC-07 is a Cargo.toml key.
EXPECTED_LOCATION_FUNCTIONS = {
    "RC-01": "run_helper",
    "RC-02": "record_path",
    "RC-03": "render_count",
    "RC-04": "write_failure_log",
    "RC-05": "read_records",
    "RC-06": "contains",
    "RC-07": None,
}
# Exact practiced quotations from the slice-5 fault table.
EXPECTED_PRACTICED = {
    "RC-01": (
        'Command::new("sh").arg("-c").arg(format!("printf \'%s\\\\n\' '
        '{name} >/dev/null")).status()'
    ),
    "RC-02": "root.join(name)",
    "RC-03": (
        'let n = count.unwrap_or(0); if n == 0 { "no record".into() } '
        'else { format!("count={n}") }'
    ),
    "RC-04": (
        'format!("write failed: environment={:?}\\n", '
        "std::env::vars().collect::<Vec<_>>())"
    ),
    "RC-05": "Err(_) => Ok(Vec::new())",
    "RC-06": "self.start <= v && v <= self.end",
    "RC-07": 'anyhow = "*"',
}
EXPECTED_FINDINGS = {
    "RC-01": (
        "User-supplied names are interpolated into sh -c despite the "
        "argv declaration."
    ),
    "RC-02": (
        "record_path joins an untrusted name without checking "
        "containment under the root."
    ),
    "RC-03": (
        "render_count treats Some(0) as absent via unwrap_or(0) and "
        "n == 0."
    ),
    "RC-04": (
        "write_failure_log dumps the process environment, including "
        "credentials."
    ),
    "RC-05": (
        "read_records swallows the read error and returns an empty Ok "
        "vector."
    ),
    "RC-06": "Interval::contains treats the end as inclusive.",
    "RC-07": (
        'anyhow is a wildcard "*" in Cargo.toml while the lock names '
        "one version."
    ),
}
EXPECTED_NEIGHBOUR_PATHS = {
    "RC-01": "src/shell.rs",
    "RC-02": "src/store.rs",
    "RC-03": "src/render.rs",
    "RC-04": "src/audit.rs",
    "RC-05": "src/store.rs",
    "RC-06": "src/interval.rs",
    "RC-07": "Cargo.toml",
}
EXPECTED_NEIGHBOUR_FUNCTIONS = {
    "RC-01": "run_helper_safely",
    "RC-02": "checked_record_path",
    "RC-03": "render_label",
    "RC-04": "write_safe_log",
    "RC-05": "write_records",
    "RC-06": "values",
    "RC-07": None,
}
EXPECTED_NEIGHBOUR_QUOTES = {
    "RC-01": 'Command::new("printf").arg("%s\\n").arg(name)',
    "RC-02": "starts_with(base)",
    "RC-03": "match label",
    "RC-04": '"write failed: token=[REDACTED]\\n"',
    "RC-05": "fs::write(path, body)?",
    "RC-06": "self.start..self.end",
    "RC-07": 'hex = "=0.4.3"',
}
EXPECTED_PROBES = {
    fault_id: f"sh probes/rc{index:02d}.sh"
    for index, fault_id in enumerate(EXPECTED_IDS, start=1)
}
EXPECTED_FIXES = {
    fault_id: f"fixes/rc{index:02d}.patch"
    for index, fault_id in enumerate(EXPECTED_IDS, start=1)
}
EXPECTED_NEEDS = ["command -v cargo"]
SHELL_GATE = "command -v cargo >/dev/null || exit 127"
RC07_GREP = r"""grep -Eq '^anyhow *= *"\*"' Cargo.toml"""
EXPECTED_INSTALL = "cargo fetch"
EXPECTED_READY = (
    "command -v cargo >/dev/null && cargo metadata --offline "
    "--format-version 1 >/dev/null"
)
EXPECTED_CHECK = (
    "cargo fmt --check && cargo clippy --offline --all-targets -- "
    "-D warnings && cargo test --offline --locked"
)
# Behavioural needles in probes/rcNN.rs (active Rust, not comments).
EXPECTED_PROBE_RS = {
    "RC-01": ("run_helper", "; touch", "exists"),
    "RC-02": ("record_path", "../outside", "is_ok"),
    "RC-03": ("render_count", "Some(0)", "count=0"),
    "RC-04": ("write_failure_log", "FIXTURE_TOKEN", "set_var"),
    "RC-05": ("read_records", "is_ok"),
    "RC-06": ("contains(3)", "Interval::new(1, 3)"),
}
SRC_MODULES = ("store", "interval", "shell", "audit", "render")
SRC_FILES = (
    "src/lib.rs",
    "src/store.rs",
    "src/interval.rs",
    "src/shell.rs",
    "src/audit.rs",
    "src/render.rs",
    "src/main.rs",
)
INTEGRATION_TESTS = (
    "tests/store.rs",
    "tests/interval.rs",
    "tests/render.rs",
)


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
CARGO_TEST_NAMES = tuple(f"rc{index:02d}" for index in range(1, 7))
PROBE_TIMEOUT = 120
HEX_PIN = "=0.4.3"

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
CARGO_ENV_KEYS = (
    "CARGO_HOME",
    "CARGO_TARGET_DIR",
    "CARGO_TERM_COLOR",
    "RUSTUP_HOME",
    "RUSTUP_TOOLCHAIN",
    "RUSTFLAGS",
)


COLOUR_RE = re.compile(r"\b(RED|GREEN|UNREACHABLE)\b")
FAULT_ID_RE = re.compile(r"\b(RC-\d{2})\b")
COUNTS_RE = re.compile(
    r"red=(?P<red>\d+)\s+green=(?P<green>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)"
)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
UNITTEST_RAN_RE = re.compile(r"Ran ([1-9]\d*) tests?")
CARGO_TEST_RAN_RE = re.compile(
    r"test result: ok\. (\d+) passed",
    re.IGNORECASE,
)
CONF_ASSIGN_RE = re.compile(
    r"^(?P<key>[A-Z][A-Z0-9_]*)='(?P<value>[^']*)'\s*$", re.MULTILINE
)
FN_RE = re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)")
COMPATIBLE_RANGE_RE = re.compile(r"[\*^~]|>=|<=|>|<")
REFUSAL_RE = re.compile(
    r"^(?P<class>[a-z][a-z0-9_-]*): expected .+; found .+; needed .+$"
)

# Contract-derived dirty shapes at the practiced span (slice-5 Rust idiom).
KIND_DIRTY = {
    "K1": re.compile(r'"sh"|-c"|format!\('),
    "K2": re.compile(r"\.join\("),
    "K3": re.compile(r"unwrap_or\(0\)|== 0"),
    "K4": re.compile(r"env::vars|environment="),
    "K5": re.compile(r"Ok\(Vec::new\(\)\)|\.ok\(\)|let _ ="),
    "K6": re.compile(r"<= self\.end"),
    "K9": re.compile(r'anyhow\s*=\s*"\*"'),
}

# Contract-derived clean neighbour shapes, discovered without FAULTS.json.
KIND_CLEAN = {
    "K1": re.compile(r'Command::new\("printf"\)'),
    "K2": re.compile(r"starts_with\("),
    "K3": re.compile(r"match label"),
    "K4": re.compile(r"\[REDACTED\]"),
    "K5": re.compile(r"fs::write\([^;]+\)\?"),
    "K6": re.compile(r"self\.start\.\.self\.end"),
    "K9": re.compile(r'hex\s*=\s*"=0\.4\.3"'),
}


def load_json_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_toml_object(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def fault_rows(data: dict) -> list[dict]:
    faults = data.get("faults")
    if not isinstance(faults, list):
        return []
    return [row for row in faults if isinstance(row, dict)]


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
        and all(
            isinstance(item, int) and not isinstance(item, bool)
            for item in value
        )
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


def _active_shell_lines(command: str) -> list[str]:
    lines: list[str] = []
    for raw in command.splitlines():
        stripped = raw.split("#", 1)[0].strip()
        if stripped:
            lines.append(stripped)
    return lines


def _strip_rust_comments(text: str) -> str:
    no_block = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    lines: list[str] = []
    for line in no_block.splitlines():
        if "//" in line:
            line = line[: line.index("//")]
        lines.append(line)
    return "\n".join(lines)


def _enclosing_fn(text: str, line_no: int) -> str | None:
    body = text.splitlines()
    if line_no < 1 or line_no > len(body):
        return None
    for index in range(line_no - 1, -1, -1):
        match = FN_RE.search(body[index])
        if match:
            return match.group(1)
    return None


def _dep_spec(deps: dict, name: str) -> str:
    value = deps.get(name)
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("version"), str):
        return value["version"]
    return ""


def _lock_package_version(data: dict, name: str) -> str | None:
    packages = data.get("package")
    if not isinstance(packages, list):
        return None
    versions = [
        row.get("version")
        for row in packages
        if isinstance(row, dict)
        and row.get("name") == name
        and isinstance(row.get("version"), str)
        and row.get("version")
    ]
    if not versions:
        return None
    return versions[0]


def _cargo_test_line(name: str) -> str:
    return f"cargo test --offline --locked --quiet --test {name}"


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


def _iter_app_sources(root: Path) -> list[tuple[str, Path, str]]:
    found: list[tuple[str, Path, str]] = []
    for folder in ("src", "tests"):
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.rs")):
            rel = path.relative_to(root).as_posix()
            found.append((rel, path, path.read_text(encoding="utf-8")))
    cargo = root / "Cargo.toml"
    if cargo.is_file():
        found.append(
            ("Cargo.toml", cargo, cargo.read_text(encoding="utf-8"))
        )
    return found


def discover_clean_neighbours(
    root: Path,
) -> dict[str, list[tuple[str, int, int, str]]]:
    """Find clean-neighbour spans from the contract shapes, not FAULTS.json."""
    found: dict[str, list[tuple[str, int, int, str]]] = {
        kind: [] for kind in KIND_CLEAN
    }
    for rel, _path, text in _iter_app_sources(root):
        for kind, pattern in KIND_CLEAN.items():
            if kind == "K9" and rel != "Cargo.toml":
                continue
            if kind != "K9" and rel == "Cargo.toml":
                continue
            for match in pattern.finditer(text):
                start = text[: match.start()].count("\n") + 1
                end = text[: match.end()].count("\n") + 1
                snippet = "\n".join(text.splitlines()[start - 1 : end])
                found[kind].append((rel, start, end, snippet))
    return found


def _cargo_env_passthrough() -> dict[str, str]:
    extra: dict[str, str] = {}
    for key in CARGO_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            extra[key] = value
    return extra




class RustCrateLayout(unittest.TestCase):

    def test_src_lib_bin_and_modules_exist(self):
        for rel in SRC_FILES:
            with self.subTest(path=rel):
                self.assertTrue(
                    (APP / rel).is_file(),
                    f"{APP / rel} is missing; the crate is a lib and a bin",
                )
        text = (
            (APP / "src/lib.rs").read_text(encoding="utf-8")
            if (APP / "src/lib.rs").is_file()
            else ""
        )
        for name in SRC_MODULES:
            with self.subTest(module=name):
                self.assertIn(
                    f"pub mod {name}",
                    text,
                    "src/lib.rs must declare the contracted modules",
                )

    def test_integration_tests_exist(self):
        for rel in INTEGRATION_TESTS:
            with self.subTest(path=rel):
                self.assertTrue(
                    (APP / rel).is_file(),
                    f"{APP / rel} is missing; integration tests ship green",
                )


    def test_probe_shell_scripts_are_the_contracted_gates(self):
        for index in range(1, 7):
            rel = f"probes/rc{index:02d}.sh"
            name = f"rc{index:02d}"
            path = APP / rel
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            lines = _active_shell_lines(text)
            with self.subTest(path=rel, stage="gate"):
                self.assertIn(
                    SHELL_GATE,
                    lines,
                    f"{rel} must gate with {SHELL_GATE!r}, not a comment",
                )
            with self.subTest(path=rel, stage="cargo-target"):
                self.assertTrue(
                    _cargo_test_line(name) in lines
                    or f"exec {_cargo_test_line(name)}" in lines,
                    f"{rel} must run {_cargo_test_line(name)!r}",
                )
            with self.subTest(path=rel, stage="not-a-grep"):
                self.assertFalse(
                    any("grep" in line for line in lines),
                    f"{rel} is a cargo test target, not a static grep",
                )
        rc07 = APP / "probes/rc07.sh"
        text = rc07.read_text(encoding="utf-8") if rc07.is_file() else ""
        lines = _active_shell_lines(text)
        self.assertIn(
            RC07_GREP,
            lines,
            "probes/rc07.sh must grep the planted anyhow wildcard",
        )
        self.assertFalse(
            any("cargo" in line for line in lines),
            "RC-07 probe needs no cargo",
        )

    def test_probe_rs_files_exercise_the_contracted_behaviour(self):
        for fault_id, needles in EXPECTED_PROBE_RS.items():
            index = fault_id.split("-", 1)[1]
            rel = f"probes/rc{index}.rs"
            path = APP / rel
            raw = path.read_text(encoding="utf-8") if path.is_file() else ""
            text = _strip_rust_comments(raw)
            with self.subTest(path=rel, stage="is-a-test"):
                self.assertRegex(
                    text,
                    r"\bfn\s+main\b|#\[test\]",
                    f"{rel} must be a cargo test body, not an empty file",
                )
            for needle in needles:
                with self.subTest(path=rel, needle=needle):
                    self.assertIn(
                        needle,
                        text,
                        f"{rel} must exercise {needle!r} in active code, "
                        "not a comment or a grep over a planted marker",
                    )

    def test_main_rs_names_the_read_write_wire(self):
        path = APP / "src/main.rs"
        raw = path.read_text(encoding="utf-8") if path.is_file() else ""
        text = _strip_rust_comments(raw)
        self.assertRegex(
            text,
            r'"read"',
            'src/main.rs must name the read verb as "read"',
        )
        self.assertRegex(
            text,
            r'"write"',
            'src/main.rs must name the write verb as "write"',
        )
        self.assertIn(
            "WF_AGENT",
            text,
            "src/main.rs must read WF_AGENT for the write verb",
        )
        self.assertIn(
            "anyhow::Result",
            text,
            "src/main.rs uses anyhow::Result",
        )
        self.assertIn(
            "hex::encode",
            text,
            "src/main.rs uses hex::encode",
        )

    def test_cargo_toml_parses_as_the_contracted_crate(self):
        data = load_toml_object(APP / "Cargo.toml")
        package = data.get("package") if isinstance(data.get("package"), dict) else {}
        self.assertEqual(
            package.get("name"),
            "fixture-rust-crate",
            "Cargo.toml package name is fixture-rust-crate",
        )
        self.assertEqual(
            package.get("edition"),
            "2021",
            "edition 2021 so set_var in a probe is safe code",
        )
        self.assertIn("lib", data, "Cargo.toml must declare [lib]")
        bins = data.get("bin")
        self.assertIsInstance(bins, list, "Cargo.toml must declare [[bin]]")
        self.assertEqual(len(bins), 1, "exactly one [[bin]]")
        bin0 = bins[0] if bins and isinstance(bins[0], dict) else {}
        self.assertEqual(
            bin0.get("name"),
            "fixture-rust-crate",
            '[[bin]] name = "fixture-rust-crate"',
        )
        deps = (
            data.get("dependencies")
            if isinstance(data.get("dependencies"), dict)
            else {}
        )
        self.assertEqual(
            deps.get("hex"),
            HEX_PIN,
            f'hex must be pinned exactly as "{HEX_PIN}"',
        )
        self.assertEqual(
            deps.get("anyhow"),
            "*",
            'anyhow is planted as "*" (the K9 wildcard, not a range)',
        )
        lints = data.get("lints") if isinstance(data.get("lints"), dict) else {}
        clippy = lints.get("clippy") if isinstance(lints.get("clippy"), dict) else {}
        self.assertTrue(clippy, "[lints.clippy] must be present")
        self.assertEqual(
            clippy.get("all"),
            "deny",
            '[lints.clippy] all = "deny"',
        )
        tests = data.get("test")
        self.assertIsInstance(
            tests,
            list,
            "Cargo.toml must declare [[test]] probe targets",
        )
        by_name = {
            row.get("name"): row
            for row in tests
            if isinstance(row, dict)
        }
        for name in CARGO_TEST_NAMES:
            with self.subTest(test=name):
                self.assertIn(
                    name,
                    by_name,
                    f"[[test]] name = {name!r} with test = false",
                )
                row = by_name.get(name) if isinstance(by_name.get(name), dict) else {}
                self.assertIs(
                    row.get("test"),
                    False,
                    f"{name} must set test = false (outside default discovery)",
                )
                path = row.get("path") if isinstance(row.get("path"), str) else ""
                self.assertEqual(
                    path,
                    f"probes/{name}.rs",
                    f"{name} path must be probes/{name}.rs, not a stand-in",
                )

    def test_cargo_lock_names_anyhow_and_hex(self):
        lock_path = APP / "Cargo.lock"
        self.assertTrue(
            lock_path.is_file(),
            "Cargo.lock is committed generated data",
        )
        data = load_toml_object(lock_path)
        packages = data.get("package")
        if not isinstance(packages, list):
            packages = []
        names = {
            row.get("name")
            for row in packages
            if isinstance(row, dict)
        }
        self.assertIn("anyhow", names, "Cargo.lock must name the anyhow package")
        self.assertIn("hex", names, "Cargo.lock must name the hex package")

    def test_rustfmt_toml_pins_edition_and_width(self):
        data = load_toml_object(APP / "rustfmt.toml")
        self.assertEqual(
            data.get("edition"),
            "2021",
            "rustfmt.toml edition = 2021",
        )
        self.assertEqual(
            data.get("max_width"),
            100,
            "rustfmt.toml max_width = 100",
        )

    def test_gitignore_names_target(self):
        text = (
            (APP / ".gitignore").read_text(encoding="utf-8")
            if (APP / ".gitignore").is_file()
            else ""
        )
        self.assertRegex(
            text,
            r"(?m)^/target/?$",
            "the app .gitignore must name /target/ "
            "(the root gitignore is rooted)",
        )





class RustCrateKinds(unittest.TestCase):
    def test_fault_kinds_k3_row_names_rust(self):
        text = KINDS.read_text(encoding="utf-8") if KINDS.is_file() else ""
        rows = parse_kind_rows(text)
        self.assertIn("K3", rows, "FAULT-KINDS.md must have a K3 row")
        row = rows["K3"]
        self.assertIn(
            "a loose comparison (false as zero)",
            row,
            "K3 title is unchanged",
        )
        self.assertIn(
            "Rust",
            row,
            "slice 5 adds Rust to the K3 idiom cell",
        )


class RustCrateManifest(unittest.TestCase):




    def test_manifest_kind_visible_to_and_severity_match_the_contract(self):
        faults = fault_rows(load_json_object(MANIFEST))
        mapping = {
            row.get("id"): (row.get("kind"), row.get("visible_to"))
            for row in faults
        }
        self.assertEqual(
            mapping,
            EXPECTED_KIND_VISIBLE,
            "kind and visible_to must match the slice-5 fault table",
        )
        severities = {row.get("id"): row.get("severity") for row in faults}
        self.assertEqual(
            severities,
            EXPECTED_SEVERITY,
            "severity must match the slice-5 fault table",
        )

    def test_manifest_locations_are_pinned_by_function_lines_and_quotes(self):
        faults = fault_rows(load_json_object(MANIFEST))
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
            "locations are pinned per id, not by fixture luck",
        )
        for row in faults:
            fault_id = row.get("id")
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            declared = (
                row.get("declared") if isinstance(row.get("declared"), dict) else {}
            )
            practiced = (
                row.get("practiced") if isinstance(row.get("practiced"), dict) else {}
            )
            neighbour = (
                row.get("neighbour") if isinstance(row.get("neighbour"), dict) else {}
            )
            loc_path = loc.get("path") if isinstance(loc.get("path"), str) else ""
            loc_lines = _as_lines(loc.get("lines"))
            with self.subTest(fault=fault_id, stage="location"):
                self.assertEqual(
                    loc_path,
                    EXPECTED_LOCATION_PATHS[fault_id],
                    f"{fault_id} location.path must be the contracted file",
                )
                self.assertIsNotNone(
                    loc_lines,
                    f"{fault_id} location.lines must be a [start, end] span",
                )
                loc_file = APP / loc_path
                body = (
                    loc_file.read_text(encoding="utf-8") if loc_file.is_file() else ""
                )
                span = (
                    _span_text(loc_file, loc_lines)
                    if loc_file.is_file() and loc_lines
                    else ""
                )
                wanted = EXPECTED_PRACTICED[fault_id]
                self.assertIn(
                    wanted,
                    span,
                    f"{fault_id} location span must carry the practiced "
                    f"quotation {wanted!r}; found {span!r}",
                )
                fn_wanted = EXPECTED_LOCATION_FUNCTIONS[fault_id]
                if fn_wanted is None:
                    self.assertEqual(loc_path, "Cargo.toml")
                else:
                    found_fn = (
                        _enclosing_fn(body, loc_lines[0]) if loc_lines else None
                    )
                    self.assertEqual(
                        found_fn,
                        fn_wanted,
                        f"{fault_id} planted span must sit in fn {fn_wanted}",
                    )
            with self.subTest(fault=fault_id, stage="quotes"):
                self.assertEqual(
                    declared.get("text"),
                    EXPECTED_DECLARATIONS[fault_id],
                    f"{fault_id} declared.text is the contract sentence",
                )
                self.assertEqual(
                    declared.get("path"),
                    "README.md",
                    f"{fault_id} declared.path is README.md",
                )
                self.assertEqual(
                    practiced.get("text"),
                    EXPECTED_PRACTICED[fault_id],
                    f"{fault_id} practiced.text is the exact planted line",
                )
                self.assertEqual(
                    practiced.get("path"),
                    EXPECTED_LOCATION_PATHS[fault_id],
                    f"{fault_id} practiced.path matches the location file",
                )
                self.assertEqual(
                    row.get("finding"),
                    EXPECTED_FINDINGS[fault_id],
                    f"{fault_id} finding is the contract contrast, not "
                    "an implementer-chosen oracle",
                )
            with self.subTest(fault=fault_id, stage="neighbour"):
                self.assertEqual(
                    neighbour.get("path"),
                    EXPECTED_NEIGHBOUR_PATHS[fault_id],
                    f"{fault_id} neighbour.path is the contracted file",
                )
                neigh_lines = _as_lines(neighbour.get("lines"))
                self.assertIsNotNone(
                    neigh_lines,
                    f"{fault_id} neighbour.lines must be a [start, end] span",
                )
                neigh_file = APP / EXPECTED_NEIGHBOUR_PATHS[fault_id]
                neigh_body = (
                    neigh_file.read_text(encoding="utf-8")
                    if neigh_file.is_file()
                    else ""
                )
                neigh_span = (
                    _span_text(neigh_file, neigh_lines)
                    if neigh_file.is_file() and neigh_lines
                    else ""
                )
                self.assertIn(
                    EXPECTED_NEIGHBOUR_QUOTES[fault_id],
                    neigh_span,
                    f"{fault_id} neighbour span must carry "
                    f"{EXPECTED_NEIGHBOUR_QUOTES[fault_id]!r}",
                )
                neigh_fn = EXPECTED_NEIGHBOUR_FUNCTIONS[fault_id]
                if neigh_fn is None:
                    self.assertEqual(neighbour.get("path"), "Cargo.toml")
                else:
                    found_nfn = (
                        _enclosing_fn(neigh_body, neigh_lines[0])
                        if neigh_lines
                        else None
                    )
                    self.assertEqual(
                        found_nfn,
                        neigh_fn,
                        f"{fault_id} neighbour span must sit in fn {neigh_fn}",
                    )
            with self.subTest(fault=fault_id, stage="probe-and-fix"):
                self.assertEqual(
                    row.get("probe"),
                    EXPECTED_PROBES[fault_id],
                    f"{fault_id} probe command is {EXPECTED_PROBES[fault_id]!r}",
                )
                self.assertEqual(
                    row.get("fix"),
                    EXPECTED_FIXES[fault_id],
                    f"{fault_id} fix is {EXPECTED_FIXES[fault_id]!r}",
                )

    def test_probe_timeout_is_120(self):
        data = load_json_object(MANIFEST)
        timeout = data.get("probe_timeout")
        self.assertIsInstance(
            timeout,
            int,
            "probe_timeout is a top-level integer (a patched probe recompiles)",
        )
        self.assertFalse(
            isinstance(timeout, bool),
            "probe_timeout must not be a JSON boolean",
        )
        self.assertEqual(
            timeout,
            PROBE_TIMEOUT,
            "probe_timeout is 120 seconds so cargo test after a patch can finish",
        )
        self.assertGreaterEqual(timeout, 1)
        self.assertLessEqual(timeout, 300)

    def test_needs_on_exactly_the_six_cargo_rows(self):
        faults = fault_rows(load_json_object(MANIFEST))
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
        )
        for row in faults[:6]:
            with self.subTest(fault=row.get("id")):
                self.assertEqual(
                    row.get("needs"),
                    EXPECTED_NEEDS,
                    f"{row.get('id')} needs is {EXPECTED_NEEDS!r}",
                )
        rc07 = faults[6] if len(faults) > 6 else {}
        rc07_needs = rc07.get("needs")
        self.assertTrue(
            rc07_needs in (None, [], ())
            or (isinstance(rc07_needs, list) and not rc07_needs),
            "RC-07 must not carry needs; grep is RED with no toolchain",
        )
        self.assertEqual(
            rc07.get("probe"),
            EXPECTED_PROBES["RC-07"],
            "RC-07 probe is the grep shell script, not a cargo test",
        )

    def test_toolchain_install_and_ready_are_the_contracted_commands(self):
        data = load_json_object(MANIFEST)
        toolchain = (
            data.get("toolchain")
            if isinstance(data.get("toolchain"), dict)
            else {}
        )
        install = (
            toolchain.get("install")
            if isinstance(toolchain.get("install"), str)
            else ""
        )
        ready = (
            toolchain.get("ready") if isinstance(toolchain.get("ready"), str) else ""
        )
        self.assertEqual(
            install,
            EXPECTED_INSTALL,
            "toolchain.install is exactly cargo fetch, not a quoted no-op",
        )
        self.assertEqual(
            ready,
            EXPECTED_READY,
            "toolchain.ready is the contracted cargo-metadata command, "
            "not a quoted no-op",
        )

    def test_check_command_is_the_contracted_pipeline(self):
        check = load_json_object(MANIFEST).get("check")
        text = check if isinstance(check, str) else ""
        self.assertEqual(
            text,
            EXPECTED_CHECK,
            "check is the contracted fmt && clippy && test pipeline, "
            "not a quoted no-op that happens to mention those words",
        )

    def test_not_planted_names_k7_and_k8(self):
        data = load_json_object(MANIFEST)
        not_planted = data.get("not_planted")
        self.assertIsInstance(
            not_planted,
            dict,
            "not_planted names the kinds this app leaves open",
        )
        self.assertIn("K7", not_planted, "K7 is in python-service")
        self.assertIn("K8", not_planted, "K8 is in python-tooling and symfony-app")



class _RepoFreeze(unittest.TestCase):
    """Live fixture roots, findings store, calibration export, and this repo stay put."""

    def setUp(self):
        self.world = World(prefix="rust-crate-app-")
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
        cargo: bool = True,
    ) -> dict[str, str]:
        extra_env = {
            "TZ": "UTC",
            "LANE_CONF": str(self.lane_conf_path),
            "FIXTURE_COPY_LIST": str(self.conf_path),
        }
        if cargo:
            extra_env.update(_cargo_env_passthrough())
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

    def scratch_env(self, *, cargo: bool = True) -> dict[str, str]:
        extra_env: dict[str, str] = {"TZ": "UTC"}
        if cargo:
            extra_env.update(_cargo_env_passthrough())
        env = self.world.env(extra_env)
        self.assertEqual(env["HOME"], str(self.world.home))
        self.assertEqual(env["XDG_STATE_HOME"], str(self.world.xdg_state))
        self.assertNotIn("LANE_CONF", env)
        self.assertNotIn("LAUNCHER_PIN", env)
        self.assertNotIn("WF_AGENT", env)
        return env

    def path_without_cargo(self) -> str:
        kept: list[str] = []
        for item in os.environ.get("PATH", "/usr/bin:/bin").split(":"):
            if not item:
                continue
            if (Path(item) / "cargo").exists():
                continue
            kept.append(item)
        for essential in ("/usr/bin", "/bin"):
            if essential not in kept:
                kept.append(essential)
        self.assertTrue(kept, "PATH without cargo must still run sh and grep")
        return ":".join(kept)


class RustCrateMakeProve(_RepoFreeze):
    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = AGENT,
        extra: dict[str, str] | None = None,
        timeout: int = RUN_CAP,
        cwd: Path | None = None,
        lever: Path | None = None,
        cargo: bool = True,
    ):
        env = self.env(agent=agent, extra=extra, cargo=cargo)
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
                f"colour line names an RC-* fault id: {line!r}",
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

    def manifest_toolchain(self, directory: Path) -> tuple[str, str]:
        data = load_json_object(directory / "FAULTS.json")
        toolchain = (
            data.get("toolchain") if isinstance(data.get("toolchain"), dict) else {}
        )
        install = (
            toolchain.get("install")
            if isinstance(toolchain.get("install"), str)
            else ""
        )
        ready = (
            toolchain.get("ready") if isinstance(toolchain.get("ready"), str) else ""
        )
        self.assertEqual(
            install,
            EXPECTED_INSTALL,
            "scratch toolchain.install must be the contracted cargo fetch",
        )
        self.assertEqual(
            ready,
            EXPECTED_READY,
            "scratch toolchain.ready must be the contracted metadata command",
        )
        return install, ready

    def run_manifest_command(
        self, directory: Path, command: str, *, timeout: int
    ):
        self.assertTrue(command.strip(), "manifest command is empty")
        env = self.scratch_env()
        try:
            return run(
                ["/bin/sh", "-c", command],
                cwd=directory,
                env=env,
                timeout=timeout,
            )
        except Timeout as exc:
            self.fail(f"manifest command {command!r} exceeded {timeout}s: {exc}")

    def cargo_ready_skip(self, directory: Path) -> str | None:
        if shutil.which("cargo") is None:
            return "UNREACHABLE: no cargo on PATH"
        _install, ready = self.manifest_toolchain(directory)
        proc = self.run_manifest_command(
            directory, ready, timeout=METADATA_CAP
        )
        if proc.exit != 0:
            return "UNREACHABLE: no cargo on PATH"
        return None

    def install_from_manifest(self, directory: Path) -> str | None:
        install, _ready = self.manifest_toolchain(directory)
        proc = self.run_manifest_command(directory, install, timeout=WARM_CAP)
        if proc.exit != 0:
            return (
                f"UNREACHABLE: toolchain.install {install!r} failed: "
                f"{proc.stderr or proc.stdout}"
            )
        return None

    def warm_crate(self, directory: Path) -> None:
        env = self.scratch_env()
        try:
            proc = run(
                ["cargo", "build", "--offline", "--all-targets"],
                cwd=directory,
                env=env,
                timeout=WARM_CAP,
            )
        except Timeout as exc:
            self.fail(f"cargo build --offline exceeded {WARM_CAP}s: {exc}")
        self.assertEqual(
            proc.exit,
            0,
            "cargo build --offline --all-targets must warm the crate: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )

    def crate_bin(self, directory: Path) -> Path:
        env = self.scratch_env()
        target_dir = env.get("CARGO_TARGET_DIR")
        if target_dir:
            candidate = Path(target_dir) / "debug" / "fixture-rust-crate"
        else:
            candidate = directory / "target" / "debug" / "fixture-rust-crate"
        if not candidate.is_file():
            try:
                proc = run(
                    [
                        "cargo",
                        "build",
                        "--offline",
                        "--bin",
                        "fixture-rust-crate",
                    ],
                    cwd=directory,
                    env=env,
                    timeout=WARM_CAP,
                )
            except Timeout as exc:
                self.fail(f"cargo build --bin exceeded {WARM_CAP}s: {exc}")
            self.assertEqual(
                proc.exit,
                0,
                "cargo build --offline --bin fixture-rust-crate must produce "
                f"the binary: stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
        self.assertTrue(
            candidate.is_file(),
            f"the crate bin must exist at {candidate}",
        )
        return candidate

    def run_crate_bin(
        self,
        directory: Path,
        args: list[str],
        *,
        agent: str | None = None,
    ):
        binary = self.crate_bin(directory)
        env = self.scratch_env()
        if agent is not None:
            env["WF_AGENT"] = agent
        try:
            return run(
                [str(binary), *args],
                cwd=directory,
                env=env,
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"fixture-rust-crate {args} exceeded {CHECK_CAP}s: {exc}")

    def assert_identity_refusal(self, proc, *, label: str) -> None:
        self.assertEqual(
            proc.exit,
            2,
            f"{label} must refuse on the wire: stdout={proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )
        self.assertEqual(
            (proc.stdout or "").strip(),
            "",
            f"{label} refusal has empty stdout",
        )
        err_lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
        self.assertEqual(
            len(err_lines),
            1,
            f"{label} refusal is one stderr line: {proc.stderr!r}",
        )
        match = REFUSAL_RE.match(err_lines[0])
        self.assertIsNotNone(
            match,
            f"{label} must speak expected-found-needed: {err_lines[0]!r}",
        )
        class_token = match.group("class") if match else ""
        self.assertEqual(
            class_token,
            "identity",
            f"{label} refusal class is identity: {err_lines[0]!r}",
        )

    def assert_anyhow_exact_pin(self, directory: Path, *, label: str) -> None:
        cargo = load_toml_object(directory / "Cargo.toml")
        deps = (
            cargo.get("dependencies")
            if isinstance(cargo.get("dependencies"), dict)
            else {}
        )
        spec = _dep_spec(deps, "anyhow")
        lock = load_toml_object(directory / "Cargo.lock")
        locked = _lock_package_version(lock, "anyhow")
        self.assertTrue(locked, f"{label}: Cargo.lock must name anyhow")
        self.assertEqual(
            spec,
            f"={locked}",
            f"{label}: anyhow must be pinned to ={locked}, not a compatible "
            f"range; found {spec!r}",
        )
        self.assertIsNone(
            COMPATIBLE_RANGE_RE.search(spec),
            f"{label}: {spec!r} is a compatible range, not an exact pin",
        )


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
            "suites.sh must actually run tests, not skip every suite as "
            f"ABSENT: {combined!r}",
        )
        self.assertGreaterEqual(int(ran.group(1)), 1, combined)
        self.assertNotIn("FAILED", combined)
        scope = self.root / "dry-run-scope.txt"
        self.world.plant_bytes(scope, b"fixture-apps slice-5 dry-run scope\n")
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

    def test_prove_without_cargo_colours_six_unreachable_and_rc07_red(self):
        target = self.root / "prove-no-cargo"
        proc_make = self.make(target)
        self.assertEqual(
            proc_make.exit,
            0,
            proc_make.stderr or proc_make.stdout,
        )
        proc = self.invoke(
            ["prove", str(target)],
            agent=None,
            extra={"PATH": self.path_without_cargo()},
            timeout=RUN_CAP,
            cargo=False,
        )
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(
            set(rows),
            set(EXPECTED_IDS),
            f"prove must colour every planted fault: {proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )
        for fault_id in EXPECTED_IDS[:6]:
            self.assertEqual(
                rows[fault_id],
                "UNREACHABLE",
                f"{fault_id} needs cargo; without it the colour is "
                f"UNREACHABLE: {proc.stdout!r}",
            )
        self.assertEqual(
            rows["RC-07"],
            "RED",
            f"RC-07 is grep and RED with no toolchain: {proc.stdout!r}",
        )
        self.assertNotIn("GREEN", set(rows.values()))
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["unreachable"], 6, proc.stdout)
        self.assertEqual(counts["red"], 1, proc.stdout)
        self.assertEqual(counts["green"], 0, proc.stdout)
        self.assertEqual(
            proc.exit,
            1,
            "mixed UNREACHABLE/RED is not every expected colour: "
            f"stderr={proc.stderr!r} stdout={proc.stdout!r}",
        )



    def test_each_fault_is_pinned_to_gate_target_quotes_and_rc07(self):
        made = self.made_app("oracle-probes")
        faults = self.load_faults(made)
        for row in faults:
            fault_id = row.get("id")
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            loc_path = loc.get("path") if isinstance(loc.get("path"), str) else ""
            loc_lines = _as_lines(loc.get("lines"))
            with self.subTest(fault=fault_id, stage="oracle"):
                self.assertEqual(row.get("probe"), EXPECTED_PROBES[fault_id])
                self.assertEqual(row.get("fix"), EXPECTED_FIXES[fault_id])
                self.assertEqual(
                    loc_path, EXPECTED_LOCATION_PATHS[fault_id]
                )
                self.assertIsNotNone(loc_lines)
                span = _span_text(made / loc_path, loc_lines)
                self.assertIn(EXPECTED_PRACTICED[fault_id], span)
                practiced = (
                    row.get("practiced")
                    if isinstance(row.get("practiced"), dict)
                    else {}
                )
                self.assertEqual(
                    practiced.get("text"), EXPECTED_PRACTICED[fault_id]
                )
                self.assertEqual(
                    row.get("finding"), EXPECTED_FINDINGS[fault_id]
                )
            sh_rel = f"probes/rc{fault_id.split('-', 1)[1]}.sh"
            sh_text = (made / sh_rel).read_text(encoding="utf-8")
            lines = _active_shell_lines(sh_text)
            if fault_id == "RC-07":
                with self.subTest(fault=fault_id, stage="grep-gate"):
                    self.assertIn(RC07_GREP, lines)
            else:
                name = f"rc{fault_id.split('-', 1)[1]}"
                with self.subTest(fault=fault_id, stage="shell-gate"):
                    self.assertIn(SHELL_GATE, lines)
                    self.assertTrue(
                        _cargo_test_line(name) in lines
                        or f"exec {_cargo_test_line(name)}" in lines
                    )
                    rs_rel = f"probes/{name}.rs"
                    rs_text = _strip_rust_comments(
                        (made / rs_rel).read_text(encoding="utf-8")
                    )
                    for needle in EXPECTED_PROBE_RS[fault_id]:
                        self.assertIn(needle, rs_text)
        rc07_cmd = EXPECTED_PROBES["RC-07"]
        colour = self.run_probe(made, rc07_cmd)
        self.assertEqual(colour, "RED", "RC-07 grep is RED on the planted *")
        self.apply_fix(made, EXPECTED_FIXES["RC-07"])
        self.assert_anyhow_exact_pin(
            made, label="RC-07 after its own fix"
        )
        colour = self.run_probe(made, rc07_cmd)
        self.assertEqual(
            colour,
            "GREEN",
            "RC-07 grep is GREEN only after the exact = pin",
        )

    def test_each_planted_fault_is_proved_by_its_own_probe(self):
        made = self.made_app("independent-probes")
        reason = self.cargo_ready_skip(made)
        if reason:
            self.skipTest(reason)
        install_reason = self.install_from_manifest(made)
        if install_reason:
            self.skipTest(install_reason)
        self.warm_crate(made)
        faults = self.load_faults(made)
        snapshots = []
        for row in faults:
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            loc_path = loc.get("path") if isinstance(loc.get("path"), str) else ""
            loc_lines = _as_lines(loc.get("lines"))
            self.assertTrue(loc_path, f"{row.get('id')} location.path missing")
            self.assertIsNotNone(
                loc_lines, f"{row.get('id')} location.lines missing"
            )
            path = made / loc_path
            self.assertTrue(
                path.is_file(),
                f"{row.get('id')} location {loc_path} missing",
            )
            span = _span_text(path, loc_lines)
            self.assertTrue(span.strip(), f"{row.get('id')} location span is empty")
            snapshots.append((row, loc_path, loc_lines, span, path.read_bytes()))

        for row, loc_path, loc_lines, span, loc_before in snapshots:
            fault_id = row.get("id")
            kind = row.get("kind")
            probe_cmd = EXPECTED_PROBES[fault_id]
            fix_rel = EXPECTED_FIXES[fault_id]
            self.assertEqual(
                row.get("probe"),
                probe_cmd,
                f"{fault_id} manifest probe must be the contracted command",
            )
            self.assertEqual(
                row.get("fix"),
                fix_rel,
                f"{fault_id} manifest fix must be the contracted patch",
            )
            self.assertIn(
                EXPECTED_PRACTICED[fault_id],
                span,
                f"{fault_id} practiced quotation must sit in the location span",
            )
            with self.subTest(fault=fault_id, stage="kind-and-red"):
                dirty = KIND_DIRTY.get(kind)
                self.assertIsNotNone(dirty, f"no dirty shape for {kind}")
                self.assertRegex(
                    span,
                    dirty,
                    f"{fault_id} practiced span must look like {kind}, "
                    f"not a label: {span!r}",
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
                    f"{fault_id} fix must touch only {loc_path}, "
                    f"found {sorted(removed)}",
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
                        f"{fault_id} fix line {line_no} is before declared "
                        f"span {start}-{end}",
                    )
                    self.assertLessEqual(
                        line_no,
                        end,
                        f"{fault_id} fix line {line_no} is after declared "
                        f"span {start}-{end}",
                    )
                loc_text_after = (made / loc_path).read_text(encoding="utf-8")
                for (
                    other_row,
                    other_path,
                    _other_lines,
                    other_span,
                    other_bytes,
                ) in snapshots:
                    if other_row.get("id") == fault_id:
                        continue
                    if other_path == loc_path:
                        self.assertIn(
                            other_span,
                            loc_text_after,
                            f"{fault_id} fix must leave {other_row.get('id')} "
                            "planted",
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
                if fault_id == "RC-07":
                    self.assert_anyhow_exact_pin(
                        made, label="RC-07 GREEN is not a compatible range"
                    )

            with self.subTest(fault=fault_id, stage="others-stay-red"):
                for other in faults:
                    other_id = other.get("id")
                    if other_id == fault_id:
                        continue
                    other_probe = EXPECTED_PROBES[other_id]
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
            locations.append(
                (loc.get("path"), loc.get("lines"), row.get("kind"), row)
            )
        discovered = discover_clean_neighbours(made)
        for kind in REQUIRED_KINDS:
            with self.subTest(kind=kind, stage="discovered"):
                self.assertTrue(
                    discovered.get(kind),
                    f"no clean neighbour for {kind} in src/, tests/, or "
                    "Cargo.toml; discovery does not read FAULTS.json "
                    "neighbour fields",
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
                    f"{fault_id} ({kind}) has no independently found "
                    "clean neighbour",
                )
                neighbour = (
                    row.get("neighbour")
                    if isinstance(row.get("neighbour"), dict)
                    else {}
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
                    f"{fault_id} manifest neighbour is not one of the "
                    f"independently found clean {kind} spans: "
                    f"{candidates!r} vs {neighbour!r}",
                )

    def test_app_check_green_when_cargo_is_ready(self):
        made = self.made_app("check-fresh")
        reason = self.cargo_ready_skip(made)
        if reason:
            self.skipTest(reason)
        install_reason = self.install_from_manifest(made)
        if install_reason:
            self.skipTest(install_reason)
        self.warm_crate(made)
        for rel in INTEGRATION_TESTS:
            self.assertTrue(
                (made / rel).is_file(),
                f"fresh make must carry {rel}",
            )
        manifest = load_json_object(made / "FAULTS.json")
        check = (
            manifest.get("check") if isinstance(manifest.get("check"), str) else ""
        )
        self.assertEqual(
            check,
            EXPECTED_CHECK,
            "scratch check must be the contracted pipeline, not a no-op",
        )
        proc = self.run_manifest_command(made, check, timeout=CHECK_CAP)
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(
            proc.exit,
            0,
            f"app check must be green on a fresh make: stdout={proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )
        ran = CARGO_TEST_RAN_RE.search(combined)
        self.assertIsNotNone(
            ran,
            f"the manifest check must actually run cargo test: {combined!r}",
        )
        self.assertGreaterEqual(int(ran.group(1)), 1, combined)

    def test_toolchain_install_and_ready_are_executed(self):
        made = self.made_app("toolchain-exec")
        if shutil.which("cargo") is None:
            self.skipTest("UNREACHABLE: no cargo on PATH")
        install, ready = self.manifest_toolchain(made)
        ready_proc = self.run_manifest_command(
            made, ready, timeout=METADATA_CAP
        )
        if ready_proc.exit != 0:
            self.skipTest("UNREACHABLE: no cargo on PATH")
        install_proc = self.run_manifest_command(
            made, install, timeout=WARM_CAP
        )
        self.assertEqual(
            install_proc.exit,
            0,
            f"toolchain.install must run from the manifest and exit 0: "
            f"cmd={install!r} stdout={install_proc.stdout!r} "
            f"stderr={install_proc.stderr!r}",
        )
        self.assertEqual(
            ready_proc.exit,
            0,
            f"toolchain.ready must run from the manifest and exit 0: "
            f"cmd={ready!r} stdout={ready_proc.stdout!r} "
            f"stderr={ready_proc.stderr!r}",
        )

    def test_bin_read_and_write_verbs_and_identity_rule(self):
        made = self.made_app("bin-wire")
        reason = self.cargo_ready_skip(made)
        if reason:
            self.skipTest(reason)
        install_reason = self.install_from_manifest(made)
        if install_reason:
            self.skipTest(install_reason)
        self.warm_crate(made)
        store = made / "wire-store"
        store.mkdir()
        name = "rec1"
        write_no_id = self.run_crate_bin(
            made, ["write", str(store), name, "1"]
        )
        self.assert_identity_refusal(
            write_no_id, label="write without WF_AGENT"
        )
        write_ok = self.run_crate_bin(
            made, ["write", str(store), name, "1"], agent=AGENT
        )
        self.assertNotEqual(
            write_ok.exit,
            2,
            "write with identity must not refuse: "
            f"stdout={write_ok.stdout!r} stderr={write_ok.stderr!r}",
        )
        self.assertEqual(
            write_ok.exit,
            0,
            "write ROOT NAME COUNT with WF_AGENT exits 0: "
            f"stdout={write_ok.stdout!r} stderr={write_ok.stderr!r}",
        )
        read_no_id = self.run_crate_bin(
            made, ["read", str(store), name]
        )
        err_lines = [
            ln for ln in (read_no_id.stderr or "").splitlines() if ln.strip()
        ]
        if read_no_id.exit == 2 and err_lines:
            match = REFUSAL_RE.match(err_lines[0])
            self.assertFalse(
                match is not None and match.group("class") == "identity",
                "read never requires identity: "
                f"stdout={read_no_id.stdout!r} stderr={read_no_id.stderr!r}",
            )
        self.assertEqual(
            read_no_id.exit,
            0,
            "read ROOT NAME without WF_AGENT exits 0 after a write: "
            f"stdout={read_no_id.stdout!r} stderr={read_no_id.stderr!r}",
        )




if __name__ == "__main__":
    unittest.main()
