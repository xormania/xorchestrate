"""symfony-app fixture-app tests, written from the slice-4 contract.

Contracts (only): the fixture-apps scope and the plan for slices 3 to 5
in the job input (slice 4: layout, seven faults, neighbours, toolchain,
manifest keys, the merged lever `ops/bin/fixture.sh`, and the lever
change this slice lands). The app under
`ops/devlane/fixtures/apps/symfony-app` is the implementation and is
not read here beyond the paths the contract names. The lever change
is not written here; it is pinned on a synthetic app staged in a
scratch repository.

Fix round: Codex skeptic (record 20260904T094604Z-review-codex-a01f03)
found 2 P1 and 6 P2. This file addresses all eight. The lock assertion
follows the contract (symfony/flex ^2 / a v2. lock version, the rest
8.1.* / v8.1.); probes are proved by the contract behaviours, not the
implementer's probe command; check is proved by running composer
validate, php -l and phpunit; the manifest is pinned per id with
severity, location, probe and fix; probe_timeout is proved on
prove --fixed too; all six preflight exclusions are planted; each fix
is one hunk confined to the declared span; a failed composer install
is a failure, never a skip.

Red at this ref because that directory is absent (the existence test
fails on `FAULTS.json`) and because the lever ignores `probe_timeout`
(a 3 s probe is UNREACHABLE where a colour is pinned) and copies
`vendor/` into the preflight (an unreadable file makes the copy fail).
Each is the intended assertion, not an import.

The app's own `tests/`, probes, fixes, `FAULTS.json`, and the lever
edit are the implementer's. Worlds, commands and fakes go through
ops/testkit.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unittest
from pathlib import Path

from test_fixture_lever import _LeverFixture, assert_refusal_wire

from ops.testkit import Timeout, World, plant_text, run

REPO = Path(__file__).resolve().parents[4]
LEVER = REPO / "ops" / "bin" / "fixture.sh"
APPS = REPO / "ops" / "devlane" / "fixtures" / "apps"
APP = APPS / "symfony-app"
MANIFEST = APP / "FAULTS.json"
CALIBRATION = REPO / "ops" / "conductor" / "findings" / "calibration.jsonl"

APP_NAME = "symfony-app"
AGENT = "Symfony App Fixture Tester <symfony-app-fixture@example.invalid>"
RUN_CAP = 60
CHECK_CAP = 120
INSTALL_CAP = 120
PROBE_CAP = 45
PROVE_CAP = 300
SUITES_CAP = 60
DISPATCH_CAP = 30
SLEEP_PROVE_CAP = 20

EXPECTED_IDS = tuple(f"SA-{index:02d}" for index in range(1, 8))
NEEDS_IDS = EXPECTED_IDS[:6]
STATIC_ID = "SA-07"
REQUIRED_KINDS = frozenset({"K1", "K2", "K3", "K4", "K5", "K6", "K8"})
EXPECTED_ROWS = {
    "SA-01": {
        "kind": "K1",
        "visible_to": "audit",
        "severity": "p1",
        "location": "src/Command/ArchiveCommand.php",
        "probe": "probes/sa01.sh",
        "fix": "fixes/sa01.patch",
    },
    "SA-02": {
        "kind": "K2",
        "visible_to": "both",
        "severity": "p1",
        "location": "src/Controller/ServeController.php",
        "probe": "probes/sa02.sh",
        "fix": "fixes/sa02.patch",
    },
    "SA-03": {
        "kind": "K3",
        "visible_to": "test",
        "severity": "p2",
        "location": "src/Service/Ledger.php",
        "probe": "probes/sa03.sh",
        "fix": "fixes/sa03.patch",
    },
    "SA-04": {
        "kind": "K4",
        "visible_to": "audit",
        "severity": "p1",
        "location": "src/Service/Ledger.php",
        "probe": "probes/sa04.sh",
        "fix": "fixes/sa04.patch",
    },
    "SA-05": {
        "kind": "K5",
        "visible_to": "test",
        "severity": "p2",
        "location": "src/Service/Ledger.php",
        "probe": "probes/sa05.sh",
        "fix": "fixes/sa05.patch",
    },
    "SA-06": {
        "kind": "K6",
        "visible_to": "test",
        "severity": "p2",
        "location": "src/Model/Interval.php",
        "probe": "probes/sa06.sh",
        "fix": "fixes/sa06.patch",
    },
    "SA-07": {
        "kind": "K8",
        "visible_to": "audit",
        "severity": "p3",
        "location": "tests/Service/LedgerTest.php",
        "probe": "probes/sa07.sh",
        "fix": "fixes/sa07.patch",
    },
}
# Contract behaviours, independent of FAULTS.json probe commands.
FAULT_BEHAVIOUR = {
    "SA-01": ("CommandTester", "; touch"),
    "SA-02": ("../outside.txt",),
    "SA-03": ("renderCount(0)",),
    "SA-04": ("FIXTURE_TOKEN",),
    "SA-05": ("LedgerException",),
    "SA-06": ("contains(3)",),
    "SA-07": ("Checksum::of('fixture');",),
}
PREFLIGHT_UNREADABLE = (
    "vendor/blocked",
    "target/blocked",
    ".venv/blocked",
    "var/blocked",
    ".phpunit.cache/blocked",
    "node_modules/blocked",
)
CHECK_SHORT_CIRCUIT_RE = re.compile(
    r"(?:^|[;&\n])\s*(?:true|:)\s*\|\|",
    re.MULTILINE,
)
COMPOSER_REQUIRE = {
    "php": ">=8.4",
    "symfony/console": "8.1.*",
    "symfony/framework-bundle": "8.1.*",
    "symfony/runtime": "8.1.*",
    "symfony/yaml": "8.1.*",
    "symfony/dotenv": "8.1.*",
    "symfony/process": "8.1.*",
    "symfony/flex": "^2",
}
COMPOSER_REQUIRE_DEV = {
    "phpunit/phpunit": "^13.3",
    "symfony/phpunit-bridge": "8.1.*",
}
GITIGNORE_PATTERNS = (
    "/vendor/",
    "/var/",
    "/.env.local",
    "/.env.*.local",
    "/phpunit.xml",
    "/.phpunit.cache/",
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
FAULT_ID_RE = re.compile(r"\b(SA-\d{2})\b")
COUNTS_RE = re.compile(
    r"red=(?P<red>\d+)\s+green=(?P<green>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)"
)
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
UNITTEST_RAN_RE = re.compile(r"Ran ([1-9]\d*) tests?")
PHPUNIT_RAN_RE = re.compile(
    r"OK \((?P<ok>[1-9]\d*) tests?(?:, \d+ assertions?)?\)"
    r"|Tests:\s+(?P<tests>[1-9]\d*)"
)
CONF_ASSIGN_RE = re.compile(
    r"^(?P<key>[A-Z][A-Z0-9_]*)='(?P<value>[^']*)'\s*$", re.MULTILINE
)
PHPUNIT_TESTS_DIR_RE = re.compile(r"<directory>\s*tests\s*</directory>")
SA07_ORACLE = "Checksum::of('fixture');"
DIRECT_INTERVAL_PHP = b"""<?php
require 'tests/bootstrap.php';
$v = (new \\App\\Model\\Interval(1, 3))->contains(3);
fwrite(STDOUT, $v ? "true\\n" : "false\\n");
exit($v ? 1 : 0);
"""

# Contract-derived dirty shapes at the practiced span (slice-4 PHP idiom).
KIND_DIRTY = {
    "K1": re.compile(r"fromShellCommandline|shell_exec|exec\(|system\("),
    "K2": re.compile(r"\.\s*'/'\s*\.|\$root\s*\."),
    "K3": re.compile(r"empty\(|==\s*(0|null|false)|!\$"),
    "K4": re.compile(r"getenv\(\)|\$_ENV|\$_SERVER"),
    "K5": re.compile(r"catch\s*\(\\?Throwable|catch\s*\(\\?Exception"),
    "K6": re.compile(r"<=\s*\$this->end"),
    "K8": re.compile(r"\$expected\s*=\s*\w+::"),
}

# Contract-derived clean neighbour shapes, discovered without FAULTS.json.
KIND_CLEAN = {
    "K1": re.compile(r"new\s+Process\s*\(\s*\["),
    "K2": re.compile(r"realpath\s*\(|str_starts_with\s*\("),
    "K3": re.compile(r"===\s*null"),
    "K4": re.compile(r"\[REDACTED\]"),
    "K5": re.compile(r"catch\s*\(\\?JsonException"),
    "K6": re.compile(
        r"range\s*\(\s*\$this->start\s*,\s*\$this->end\s*-\s*1\s*\)"
    ),
    "K8": re.compile(r"\$expected\s*=\s*\d+\s*;"),
}

SLEEPER_PROBE = b"#!/bin/sh\nsleep 3\nexit 1\n"


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


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _script_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, list):
                parts.append(" ".join(str(piece) for piece in item))
        return "\n".join(parts)
    return ""






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


def _patch_hunks(text: str) -> list[tuple[str, int, int]]:
    """Return (path, old_start, old_count) for every hunk in a unified diff."""
    current: str | None = None
    hunks: list[tuple[str, int, int]] = []
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip().removeprefix("b/")
            current = None if path == "/dev/null" else path
            continue
        hunk = HUNK_RE.match(line)
        if hunk is None or current is None:
            continue
        start = int(hunk.group(1))
        count = int(hunk.group(2)) if hunk.group(2) is not None else 1
        hunks.append((current, start, count))
    return hunks


def _required_check_commands_are_executed(stripped: str) -> list[str]:
    """Return required check commands that sit behind || or are missing."""
    required = (
        "composer validate --strict",
        "php -l",
        "vendor/bin/phpunit",
    )
    missing: list[str] = []
    if CHECK_SHORT_CIRCUIT_RE.search(stripped):
        missing.append("short-circuit wrapper (true || or : ||)")
    for needle in required:
        if needle not in stripped:
            missing.append(needle)
            continue
        if re.search(rf"\|\|\s*{re.escape(needle)}", stripped):
            missing.append(f"{needle} sits behind ||")
    if not re.search(r"\bsrc\b", stripped):
        missing.append("php -l covers src")
    if not re.search(r"\btests\b", stripped):
        missing.append("php -l covers tests")
    return missing


def _iter_app_php(root: Path) -> list[tuple[str, Path, str]]:
    found: list[tuple[str, Path, str]] = []
    for folder in ("src", "tests"):
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.php")):
            rel = path.relative_to(root).as_posix()
            found.append((rel, path, path.read_text(encoding="utf-8")))
    return found


def discover_clean_neighbours(root: Path) -> dict[str, list[tuple[str, int, int, str]]]:
    """Find clean-neighbour spans from the contract shapes, not FAULTS.json."""
    found: dict[str, list[tuple[str, int, int, str]]] = {
        kind: [] for kind in KIND_CLEAN
    }
    for rel, _path, text in _iter_app_php(root):
        for kind, pattern in KIND_CLEAN.items():
            if kind == "K8" and not rel.startswith("tests/"):
                continue
            for match in pattern.finditer(text):
                start = text[: match.start()].count("\n") + 1
                end = text[: match.end()].count("\n") + 1
                snippet = "\n".join(text.splitlines()[start - 1 : end])
                found[kind].append((rel, start, end, snippet))
    return found




class SymfonyAppLayout(unittest.TestCase):

    def test_composer_json_pins_php_and_symfony(self):
        data = load_json_object(APP / "composer.json")
        self.assertEqual(data.get("name"), "agentscient/fixture-symfony-app")
        self.assertEqual(data.get("type"), "project")
        require = data.get("require") if isinstance(data.get("require"), dict) else {}
        self.assertTrue(require, "composer.json require is missing")
        for name, pin in COMPOSER_REQUIRE.items():
            with self.subTest(require=name):
                self.assertEqual(
                    require.get(name),
                    pin,
                    f"require.{name} must be {pin!r}; found {require.get(name)!r}",
                )
        for name, ver in require.items():
            if not str(name).startswith("symfony/") or name == "symfony/flex":
                continue
            with self.subTest(symfony=name):
                self.assertEqual(
                    ver,
                    "8.1.*",
                    f"{name} must be pinned 8.1.*, not {ver!r}",
                )
        self.assertEqual(require.get("php"), ">=8.4")
        dev = (
            data.get("require-dev")
            if isinstance(data.get("require-dev"), dict)
            else {}
        )
        self.assertTrue(dev, "composer.json require-dev is missing")
        for name, pin in COMPOSER_REQUIRE_DEV.items():
            with self.subTest(require_dev=name):
                self.assertEqual(
                    dev.get(name),
                    pin,
                    f"require-dev.{name} must be {pin!r}; found {dev.get(name)!r}",
                )
        autoload = (
            data.get("autoload") if isinstance(data.get("autoload"), dict) else {}
        )
        psr4 = autoload.get("psr-4") if isinstance(autoload.get("psr-4"), dict) else {}
        self.assertEqual(psr4.get("App\\"), "src/")
        autoload_dev = (
            data.get("autoload-dev")
            if isinstance(data.get("autoload-dev"), dict)
            else {}
        )
        psr4_dev = (
            autoload_dev.get("psr-4")
            if isinstance(autoload_dev.get("psr-4"), dict)
            else {}
        )
        self.assertEqual(psr4_dev.get("App\\Tests\\"), "tests/")
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
        symfony = extra.get("symfony") if isinstance(extra.get("symfony"), dict) else {}
        self.assertEqual(symfony.get("require"), "8.1.*")
        self.assertEqual(symfony.get("allow-contrib"), False)

    def test_composer_json_has_check_and_no_fix(self):
        data = load_json_object(APP / "composer.json")
        scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
        self.assertIn(
            "check",
            scripts,
            "composer.json scripts.check must be present",
        )
        self.assertNotIn(
            "fix",
            scripts,
            "composer.json must not stub scripts.fix; the README declares it absent",
        )
        check = _script_text(scripts.get("check"))
        stripped = _strip_shell_comments(check)
        problems = _required_check_commands_are_executed(stripped)
        self.assertEqual(
            problems,
            [],
            "scripts.check must actually run composer validate --strict, "
            "php -l over src and tests, and vendor/bin/phpunit; a true || "
            f"no-op is not a check: {problems!r} in {stripped!r}",
        )

    def test_composer_lock_pins_symfony_v81(self):
        lock = load_json_object(APP / "composer.lock")
        digest = lock.get("content-hash")
        self.assertIsInstance(digest, str)
        self.assertTrue(digest.strip(), "composer.lock must carry a content-hash")
        packages = []
        for key in ("packages", "packages-dev"):
            raw = lock.get(key)
            if isinstance(raw, list):
                packages.extend(item for item in raw if isinstance(item, dict))
        by_name = {}
        for item in packages:
            name = item.get("name")
            if isinstance(name, str) and name:
                by_name[name] = item
        self.assertIn(
            "symfony/flex",
            by_name,
            "composer.lock must name symfony/flex (^2 in composer.json)",
        )
        flex_ver = str(by_name["symfony/flex"].get("version") or "")
        self.assertTrue(
            flex_ver.startswith("v2."),
            f"symfony/flex is ^2; lock must be a v2. version, found {flex_ver!r}",
        )
        manifest = json.loads(_read(APP / "composer.json"))
        pinned = {
            name
            for section in ("require", "require-dev")
            for name, constraint in (manifest.get(section) or {}).items()
            if name.startswith("symfony/") and str(constraint).startswith("8.1.")
        }
        # Transitive symfony/* contracts and polyfills carry their own
        # versions; only what the manifest pins at 8.1.* must be v8.1.
        others = [
            (name, str(item.get("version") or ""))
            for name, item in by_name.items()
            if name in pinned
        ]
        self.assertTrue(
            others,
            "composer.lock must name at least one 8.1.* symfony/* package "
            "besides flex",
        )
        for name, version in others:
            with self.subTest(package=name):
                self.assertTrue(
                    version.startswith("v8.1."),
                    f"{name} must be at a v8.1. version; found {version!r}",
                )

    def test_phpunit_dist_xml_names_tests_only(self):
        text = _read(APP / "phpunit.dist.xml")
        self.assertRegex(
            text,
            PHPUNIT_TESTS_DIR_RE,
            "phpunit.dist.xml must discover tests/ only",
        )
        self.assertNotIn(
            "probes",
            text.lower(),
            "phpunit.dist.xml must not discover probes/",
        )

    def test_gitignore_names_install_residue(self):
        text = _read(APP / ".gitignore")
        self.assertTrue(text.strip(), ".gitignore is missing")
        for pattern in GITIGNORE_PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertIn(
                    pattern,
                    text,
                    f".gitignore must name {pattern!r}",
                )



    def test_probe_files_name_contract_behaviours(self):
        for fault_id, tokens in FAULT_BEHAVIOUR.items():
            if fault_id == STATIC_ID:
                path = APP / "tests" / "Service" / "LedgerTest.php"
            else:
                index = int(str(fault_id).split("-")[1])
                path = APP / "probes" / f"Sa{index:02d}Test.php"
            text = _read(path)
            self.assertTrue(
                text.strip(),
                f"{path} must exist so {fault_id} can exercise its contract behaviour",
            )
            for token in tokens:
                with self.subTest(fault=fault_id, token=token):
                    self.assertIn(
                        token,
                        text,
                        f"{fault_id} must exercise {token!r} in {path.name}, "
                        "not a source-grep of a dirty token",
                    )

    def test_runtime_probe_shells_run_phpunit_not_grep(self):
        for fault_id in NEEDS_IDS:
            index = int(str(fault_id).split("-")[1])
            rel = f"probes/sa{index:02d}.sh"
            text = _strip_shell_comments(_read(APP / rel))
            with self.subTest(fault=fault_id):
                self.assertIn(
                    "vendor/bin/phpunit",
                    text,
                    f"{rel} must run vendor/bin/phpunit",
                )
                self.assertIn(
                    f"probes/Sa{index:02d}Test.php",
                    text,
                    f"{rel} must name probes/Sa{index:02d}Test.php",
                )
                self.assertNotRegex(
                    text,
                    r"\bgrep\b",
                    f"{rel} is a runtime probe, not a grep of the planted span",
                )

    def test_sa07_probe_greps_the_checksum_oracle(self):
        text = _read(APP / "probes" / "sa07.sh")
        self.assertTrue(text.strip(), "probes/sa07.sh is missing")
        self.assertRegex(text, r"\bgrep\b", "SA-07's probe is the static grep")
        self.assertIn(
            SA07_ORACLE,
            text,
            "probes/sa07.sh must grep for Checksum::of('fixture');",
        )



class SymfonyAppManifest(unittest.TestCase):



    def test_manifest_kind_and_visible_to_match_the_contract(self):
        faults = fault_rows(load_json_object(MANIFEST))
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
            "seven rows, ids SA-01 to SA-07, before per-id oracle checks",
        )
        for row in faults:
            fault_id = row.get("id")
            expected = EXPECTED_ROWS[fault_id]
            with self.subTest(fault=fault_id):
                self.assertEqual(row.get("kind"), expected["kind"])
                self.assertEqual(row.get("visible_to"), expected["visible_to"])
                self.assertEqual(row.get("severity"), expected["severity"])
                loc = (
                    row.get("location")
                    if isinstance(row.get("location"), dict)
                    else {}
                )
                self.assertEqual(loc.get("path"), expected["location"])
                probe = row.get("probe") if isinstance(row.get("probe"), str) else ""
                self.assertIn(
                    expected["probe"],
                    probe,
                    f"{fault_id} probe must name {expected['probe']}",
                )
                self.assertEqual(row.get("fix"), expected["fix"])

    def test_manifest_probe_timeout_toolchain_and_needs(self):
        data = load_json_object(MANIFEST)
        timeout = data.get("probe_timeout")
        self.assertIsInstance(timeout, int)
        self.assertNotIsInstance(timeout, bool)
        self.assertEqual(timeout, 30)
        self.assertGreaterEqual(timeout, 1)
        self.assertLessEqual(timeout, 300)
        toolchain = (
            data.get("toolchain") if isinstance(data.get("toolchain"), dict) else {}
        )
        self.assertIn("install", toolchain)
        self.assertIn("ready", toolchain)
        install = toolchain.get("install") if isinstance(toolchain.get("install"), str) else ""
        ready = toolchain.get("ready") if isinstance(toolchain.get("ready"), str) else ""
        self.assertRegex(install, r"composer install")
        self.assertRegex(install, r"--prefer-dist")
        self.assertRegex(install, r"--no-progress")
        self.assertRegex(install, r"--no-interaction")
        self.assertRegex(ready, r"vendor/bin/phpunit")
        faults = fault_rows(data)
        self.assertEqual(
            tuple(row.get("id") for row in faults),
            EXPECTED_IDS,
            "needs are pinned on the seven contract rows",
        )
        needs_ids = []
        for row in faults:
            fault_id = row.get("id")
            needs = row.get("needs")
            if needs:
                self.assertIsInstance(needs, list)
                self.assertTrue(
                    any("vendor/bin/phpunit" in str(cmd) for cmd in needs),
                    f"{fault_id} needs must gate on vendor/bin/phpunit",
                )
                needs_ids.append(fault_id)
        self.assertEqual(
            tuple(needs_ids),
            NEEDS_IDS,
            "needs on exactly SA-01 to SA-06; none on SA-07",
        )

    def test_manifest_not_planted_names_k7_and_k9(self):
        data = load_json_object(MANIFEST)
        self.assertIn("not_planted", data)
        blob = json.dumps(data.get("not_planted"))
        self.assertIn("K7", blob)
        self.assertIn("K9", blob)

    def test_check_command_names_composer_php_lint_and_phpunit(self):
        check = load_json_object(MANIFEST).get("check")
        text = check if isinstance(check, str) else ""
        stripped = _strip_shell_comments(text)
        self.assertTrue(stripped.strip(), "manifest check is missing")
        self.assertIsNone(
            CHECK_SHORT_CIRCUIT_RE.search(stripped),
            f"manifest check must not short-circuit: {stripped!r}",
        )
        self.assertRegex(
            stripped,
            r"composer check|composer validate --strict",
            "check is composer check (comments do not count)",
        )



class _RepoFreeze(unittest.TestCase):
    """The tracked calibration export and this repo stay put.

    Every subprocess this fixture runs (fixture.sh, git, composer, phpunit,
    php) goes through self.env()/self.scratch_env()/self.git_env(), which
    all route through self.world.env() and so carry HOME and
    XDG_STATE_HOME redirected to this World's own tree (docs/tests.md
    \xa7World; ops/testkit/world.py). A subject run that way cannot reach
    ~/.local/state/agentscient/findings.db or .../fixtures on the real
    host, so watching those paths proved nothing about the subject and
    only failed when another session on the shared host wrote to its own
    live store mid-run (LD-80).
    """

    def setUp(self):
        self.world = World(prefix="symfony-app-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self.world.require("bash")
        self._live = self.world.watch(CALIBRATION, contents=False)
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
            self, "tracked calibration.jsonl mutated"
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


class SymfonyAppMakeProve(_RepoFreeze):
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
                f"colour line names a SA-* fault id: {line!r}",
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

    def fault_snapshots(self, directory: Path):
        faults = self.load_faults(directory)
        snapshots = []
        for row in faults:
            loc = row.get("location") if isinstance(row.get("location"), dict) else {}
            loc_path = loc.get("path") if isinstance(loc.get("path"), str) else ""
            loc_lines = _as_lines(loc.get("lines"))
            self.assertTrue(loc_path, f"{row.get('id')} location.path missing")
            self.assertIsNotNone(loc_lines, f"{row.get('id')} location.lines missing")
            path = directory / loc_path
            self.assertTrue(path.is_file(), f"{row.get('id')} location {loc_path} missing")
            span = _span_text(path, loc_lines)
            self.assertTrue(span.strip(), f"{row.get('id')} location span is empty")
            snapshots.append((row, loc_path, loc_lines, span, path.read_bytes()))
        return faults, snapshots

    def install_from_lock(self, directory: Path) -> dict[str, str]:
        composer = shutil.which("composer")
        if composer is None:
            self.skipTest("UNREACHABLE: composer not on PATH")
        env = self.scratch_env()
        for key, value in os.environ.items():
            if key.startswith("COMPOSER"):
                env[key] = value
        env["COMPOSER_DISABLE_NETWORK"] = "1"
        if "COMPOSER_CACHE_DIR" not in env:
            import pwd
            for candidate in (
                Path(os.environ.get("HOME") or "") / ".cache" / "composer",
                Path(pwd.getpwuid(os.getuid()).pw_dir) / ".cache" / "composer",
            ):
                if (candidate / "files").is_dir():
                    env["COMPOSER_CACHE_DIR"] = str(candidate)
                    break
        argv = [
            composer,
            "install",
            "--prefer-dist",
            "--no-progress",
            "--no-interaction",
        ]
        try:
            proc = run(
                argv,
                cwd=directory,
                env=env,
                timeout=INSTALL_CAP,
            )
        except Timeout:
            self.skipTest(
                "UNREACHABLE: composer install --prefer-dist --no-progress "
                "--no-interaction exceeded 120s"
            )
        self.assertEqual(
            proc.exit,
            0,
            "composer install failed; a broken lock or manifest is a wrong "
            "app, never a skip: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        phpunit = directory / "vendor" / "bin" / "phpunit"
        self.assertTrue(
            os.access(phpunit, os.X_OK),
            "composer install left vendor/bin/phpunit unusable; that is a "
            "wrong app, never a skip",
        )
        return env

    def contract_probe_php(self, fault_id: str) -> str:
        index = int(str(fault_id).split("-")[1])
        return f"probes/Sa{index:02d}Test.php"

    def contract_behaviour_colour(self, directory: Path, fault_id: str) -> str:
        """Colour the contract behaviour, ignoring FAULTS.json probe commands."""
        if fault_id == STATIC_ID:
            text = _read(directory / "tests" / "Service" / "LedgerTest.php")
            return "RED" if SA07_ORACLE in text else "GREEN"
        phpunit = directory / "vendor" / "bin" / "phpunit"
        self.assertTrue(
            os.access(phpunit, os.X_OK),
            f"{fault_id} contract probe needs vendor/bin/phpunit",
        )
        env = self.scratch_env()
        env["APP_ENV"] = "test"
        rel = self.contract_probe_php(fault_id)
        try:
            proc = run(
                [
                    str(phpunit),
                    "--no-configuration",
                    "--bootstrap",
                    "tests/bootstrap.php",
                    rel,
                ],
                cwd=directory,
                env=env,
                timeout=PROBE_CAP,
            )
        except Timeout:
            return "UNREACHABLE"
        if proc.exit in {126, 127} or proc.exit < 0:
            return "UNREACHABLE"
        return "GREEN" if proc.exit == 0 else "RED"

    def assert_check_tools_ran(self, directory: Path, env: dict[str, str]) -> None:
        composer = shutil.which("composer")
        self.assertIsNotNone(composer, "INVALID: composer vanished after install")
        try:
            validate = run(
                [composer, "validate", "--strict"],
                cwd=directory,
                env=env,
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"composer validate --strict exceeded {CHECK_CAP}s: {exc}")
        self.assertEqual(
            validate.exit,
            0,
            "composer validate --strict must run and pass: "
            f"stdout={validate.stdout!r} stderr={validate.stderr!r}",
        )
        php = shutil.which("php")
        self.assertIsNotNone(php, "INVALID: php not on PATH after composer install")
        php_files: list[Path] = []
        for folder in ("src", "tests"):
            base = directory / folder
            self.assertTrue(base.is_dir(), f"fresh install must carry {folder}/")
            found = sorted(base.rglob("*.php"))
            self.assertTrue(
                found,
                f"{folder}/ must contain PHP files for php -l to lint",
            )
            php_files.extend(found)
        for path in php_files:
            try:
                lint = run(
                    [php, "-l", str(path)],
                    cwd=directory,
                    env=env,
                    timeout=CHECK_CAP,
                )
            except Timeout as exc:
                self.fail(f"php -l {path} exceeded {CHECK_CAP}s: {exc}")
            combined = (lint.stdout or "") + (lint.stderr or "")
            self.assertEqual(
                lint.exit,
                0,
                f"php -l must actually lint {path}: {combined!r}",
            )
            self.assertIn(
                "No syntax errors detected",
                combined,
                f"php -l produced no lint proof for {path}: {combined!r}",
            )
        phpunit = directory / "vendor" / "bin" / "phpunit"
        try:
            unit = run(
                [str(phpunit)],
                cwd=directory,
                env=env,
                timeout=CHECK_CAP,
            )
        except Timeout as exc:
            self.fail(f"vendor/bin/phpunit exceeded {CHECK_CAP}s: {exc}")
        unit_out = (unit.stdout or "") + (unit.stderr or "")
        self.assertEqual(
            unit.exit,
            0,
            f"vendor/bin/phpunit must run and pass: {unit_out!r}",
        )
        ran = PHPUNIT_RAN_RE.search(unit_out)
        self.assertIsNotNone(
            ran,
            f"phpunit must collect tests, not a silent no-op: {unit_out!r}",
        )

    def assert_interval_contains_three_is_red(self, directory: Path) -> None:
        php = shutil.which("php")
        self.assertIsNotNone(php, "INVALID: php not on PATH after composer install")
        script = self.root / "sa06-direct.php"
        self.world.plant_bytes(script, DIRECT_INTERVAL_PHP)
        env = self.scratch_env()
        try:
            proc = run(
                [php, str(script)],
                cwd=directory,
                env=env,
                timeout=PROBE_CAP,
            )
        except Timeout as exc:
            self.fail(f"direct Interval::contains(3) exceeded {PROBE_CAP}s: {exc}")
        self.assertNotIn(
            proc.exit,
            {126, 127},
            "direct Interval::contains(3) must be reachable after install: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertIn(
            "true",
            (proc.stdout or "") + (proc.stderr or ""),
            "SA-06 planted: (new Interval(1, 3))->contains(3) must be true: "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertNotEqual(
            proc.exit,
            0,
            "SA-06 planted: contains(3) true is RED, not a passing oracle",
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
            "suites.sh must actually run tests, not skip every suite as ABSENT: "
            f"{combined!r}",
        )
        self.assertGreaterEqual(int(ran.group(1)), 1, combined)
        self.assertNotIn("FAILED", combined)
        scope = self.root / "dry-run-scope.txt"
        self.world.plant_bytes(scope, b"fixture-apps slice-4 dry-run scope\n")
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

    def test_prove_on_fresh_make_colours_needs_unreachable_and_sa07_red(self):
        target = self.root / "prove-fresh"
        self.make(target)
        proc = self.invoke(["prove", str(target)], agent=None)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(
            set(rows),
            set(EXPECTED_IDS),
            f"prove must colour every planted fault: {proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )
        for fault_id in NEEDS_IDS:
            self.assertEqual(
                rows[fault_id],
                "UNREACHABLE",
                f"{fault_id} must be UNREACHABLE without vendor/: {proc.stdout!r}",
            )
        self.assertEqual(
            rows[STATIC_ID],
            "RED",
            f"{STATIC_ID} must be RED with no toolchain: {proc.stdout!r}",
        )
        self.assertNotIn("GREEN", set(rows.values()))
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["unreachable"], len(NEEDS_IDS), proc.stdout)
        self.assertEqual(counts["red"], 1, proc.stdout)
        self.assertEqual(counts["green"], 0, proc.stdout)
        self.assertEqual(
            proc.exit,
            1,
            "fresh make without vendor/ is mixed UNREACHABLE/RED, never green: "
            f"{proc.stderr or proc.stdout}",
        )

    def test_each_fix_is_confined_to_the_declared_span(self):
        made = self.made_app("independent-fixes")
        _faults, snapshots = self.fault_snapshots(made)
        for row, loc_path, loc_lines, span, loc_before in snapshots:
            fault_id = row.get("id")
            kind = row.get("kind")
            fix_rel = row.get("fix") if isinstance(row.get("fix"), str) else ""
            with self.subTest(fault=fault_id, stage="kind"):
                dirty = KIND_DIRTY.get(kind)
                self.assertIsNotNone(dirty, f"no dirty shape for {kind}")
                self.assertRegex(
                    span,
                    dirty,
                    f"{fault_id} practiced span must look like {kind}, not a label: "
                    f"{span!r}",
                )
            expected = EXPECTED_ROWS[fault_id]
            self.assertEqual(kind, expected["kind"])
            self.assertEqual(loc_path, expected["location"])
            probe_cmd = row.get("probe") if isinstance(row.get("probe"), str) else ""
            self.assertIn(expected["probe"], probe_cmd)
            self.assertEqual(fix_rel, expected["fix"])
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
                hunks = _patch_hunks(patch.decode("utf-8"))
                self.assertEqual(
                    len(hunks),
                    1,
                    f"{fault_id} fix must be one hunk, found {len(hunks)}: "
                    f"{hunks!r}",
                )
                self.assertEqual(
                    hunks[0][0],
                    loc_path,
                    f"{fault_id} hunk must target {loc_path}, found {hunks[0]!r}",
                )
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
            self.reset_tree(made)
            reset = (made / loc_path).read_bytes()
            self.assertEqual(reset, loc_before, f"{fault_id} reset did not restore")

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
                    f"no clean neighbour for {kind} in src/ or tests/; "
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

    def test_full_proof_after_composer_install(self):
        made = self.made_app("full-proof")
        env = self.install_from_lock(made)
        self.assert_check_tools_ran(made, env)
        manifest = load_json_object(made / "FAULTS.json")
        check = manifest.get("check") if isinstance(manifest.get("check"), str) else ""
        self.assertTrue(check.strip(), "fresh make must carry a check command")
        self.assertIsNone(
            CHECK_SHORT_CIRCUIT_RE.search(_strip_shell_comments(check)),
            f"manifest check must not short-circuit: {check!r}",
        )
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
            f"app check must be green on a fresh install: stdout={proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )
        prove = self.invoke(["prove", str(made)], agent=None, timeout=PROVE_CAP)
        rows = self.parse_prove(prove.stdout)
        self.assertEqual(set(rows), set(EXPECTED_IDS), prove.stdout)
        for fault_id in EXPECTED_IDS:
            self.assertEqual(
                rows[fault_id],
                "RED",
                f"{fault_id} must be RED after install: {prove.stdout!r}",
            )
        self.assertNotIn("GREEN", set(rows.values()))
        self.assertNotIn("UNREACHABLE", set(rows.values()))
        counts = self.parse_counts(prove.stdout)
        self.assertEqual(counts["red"], len(EXPECTED_IDS), prove.stdout)
        self.assertEqual(counts["unreachable"], 0, prove.stdout)
        self.assertEqual(prove.exit, 0, prove.stderr or prove.stdout)

        faults, snapshots = self.fault_snapshots(made)
        self.assert_interval_contains_three_is_red(made)
        for row, loc_path, loc_lines, span, loc_before in snapshots:
            fault_id = row.get("id")
            expected = EXPECTED_ROWS[fault_id]
            probe_cmd = row.get("probe") if isinstance(row.get("probe"), str) else ""
            fix_rel = expected["fix"]
            self.assertEqual(row.get("fix"), fix_rel)
            if fault_id == STATIC_ID:
                behaviour_source = made / expected["location"]
            else:
                behaviour_source = made / self.contract_probe_php(fault_id)
            behaviour_text = _read(behaviour_source)
            for token in FAULT_BEHAVIOUR[fault_id]:
                self.assertIn(
                    token,
                    behaviour_text,
                    f"{fault_id} must name {token!r} in {behaviour_source.name}",
                )
            with self.subTest(fault=fault_id, stage="probe-red"):
                colour = self.contract_behaviour_colour(made, fault_id)
                self.assertEqual(
                    colour,
                    "RED",
                    f"{fault_id} contract behaviour must be RED while planted",
                )
                manifest_colour = self.run_probe(made, probe_cmd)
                self.assertEqual(
                    manifest_colour,
                    "RED",
                    f"{fault_id} manifest probe must agree it is RED: {probe_cmd!r}",
                )
            with self.subTest(fault=fault_id, stage="probe-green"):
                self.apply_fix(made, fix_rel)
                colour = self.contract_behaviour_colour(made, fault_id)
                self.assertEqual(
                    colour,
                    "GREEN",
                    f"{fault_id} contract behaviour must be GREEN after its own fix",
                )
                manifest_colour = self.run_probe(made, probe_cmd)
                self.assertEqual(
                    manifest_colour,
                    "GREEN",
                    f"{fault_id} manifest probe must agree it is GREEN after the fix",
                )
            with self.subTest(fault=fault_id, stage="others-stay-red"):
                for other in faults:
                    other_id = other.get("id")
                    if other_id == fault_id:
                        continue
                    colour = self.contract_behaviour_colour(made, other_id)
                    self.assertEqual(
                        colour,
                        "RED",
                        f"{other_id} must stay RED while only {fault_id} is fixed",
                    )
            self.reset_tree(made)
            reset = (made / loc_path).read_bytes()
            self.assertEqual(reset, loc_before, f"{fault_id} reset did not restore")
            colour = self.contract_behaviour_colour(made, fault_id)
            self.assertEqual(
                colour,
                "RED",
                f"{fault_id} contract behaviour must be RED again after reset",
            )
            del loc_lines, span

        fixed = self.invoke(
            ["prove", "--fixed", str(made)],
            agent=None,
            timeout=PROVE_CAP,
        )
        rows = self.parse_prove(fixed.stdout)
        self.assertEqual(set(rows), set(EXPECTED_IDS), fixed.stdout)
        for fault_id in EXPECTED_IDS:
            self.assertEqual(
                rows[fault_id],
                "GREEN",
                f"{fault_id} must be GREEN after planted fixes: {fixed.stdout!r}",
            )
        self.assertNotIn("RED", set(rows.values()))
        self.assertNotIn("UNREACHABLE", set(rows.values()))
        counts = self.parse_counts(fixed.stdout)
        self.assertEqual(counts["green"], len(EXPECTED_IDS), fixed.stdout)
        self.assertEqual(counts["unreachable"], 0, fixed.stdout)
        self.assertEqual(fixed.exit, 0, fixed.stderr or fixed.stdout)




class SymfonyAppLeverChange(_LeverFixture):
    """Slice-4 lever pins, staged on a synthetic app, not the missing fixture."""

    def _plant_timeout_key(self, made: Path, value) -> None:
        manifest_path = made / "FAULTS.json"
        before = manifest_path.read_bytes()
        data = json.loads(before.decode("utf-8"))
        if value is None:
            data.pop("probe_timeout", None)
        else:
            data["probe_timeout"] = value
        planted = (json.dumps(data, indent=2) + "\n").encode("utf-8")
        self._plant_bytes(manifest_path, planted)
        self.assertEqual(manifest_path.read_bytes(), planted)
        if value is None:
            self.assertNotIn(b"probe_timeout", planted)
        else:
            self.assertNotEqual(manifest_path.read_bytes(), before)

    def _plant_sleeper(self, made: Path) -> None:
        probe = made / "probes" / "syn-01.sh"
        self._plant_bytes(probe, SLEEPER_PROBE)
        self.assertEqual(probe.read_bytes(), SLEEPER_PROBE)

    def test_probe_timeout_five_colours_a_three_second_probe_default_is_unreachable(
        self,
    ):
        coloured = self.plant_made_dir("timeout-five")
        self._plant_sleeper(coloured)
        self._plant_timeout_key(coloured, 5)
        proc = self.invoke(
            ["prove", str(coloured)],
            agent=None,
            timeout=SLEEP_PROVE_CAP,
        )
        self.assertNotEqual(proc.returncode, 3, "timeout is UNREACHABLE, not abort")
        rows = self.parse_prove(proc.stdout)
        self.assertIn("SYN-01", rows, proc.stdout)
        self.assertIn(
            rows["SYN-01"],
            {"RED", "GREEN"},
            "probe_timeout 5 must colour a 3 s probe, not UNREACHABLE: "
            f"{proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertNotEqual(rows["SYN-01"], "UNREACHABLE")

        default = self.plant_made_dir("timeout-default")
        self._plant_sleeper(default)
        self._plant_timeout_key(default, None)
        proc = self.invoke(
            ["prove", str(default)],
            agent=None,
            timeout=SLEEP_PROVE_CAP,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr or proc.stdout)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(
            rows.get("SYN-01"),
            "UNREACHABLE",
            "without probe_timeout the 3 s probe stays the two-second default: "
            f"{proc.stdout!r}",
        )

        coloured_fixed = self.plant_made_repo("timeout-five-fixed")
        self._plant_sleeper(coloured_fixed)
        self._plant_timeout_key(coloured_fixed, 5)
        proc = self.invoke(
            ["prove", "--fixed", str(coloured_fixed)],
            agent=None,
            timeout=SLEEP_PROVE_CAP,
        )
        self.assertNotEqual(proc.returncode, 3, "timeout is UNREACHABLE, not abort")
        rows = self.parse_prove(proc.stdout)
        self.assertIn("SYN-01", rows, proc.stdout)
        self.assertIn(
            rows["SYN-01"],
            {"RED", "GREEN"},
            "probe_timeout 5 must colour a 3 s probe on prove --fixed too, "
            f"not UNREACHABLE: {proc.stdout!r} stderr={proc.stderr!r}",
        )
        self.assertNotEqual(rows["SYN-01"], "UNREACHABLE")

        default_fixed = self.plant_made_repo("timeout-default-fixed")
        self._plant_sleeper(default_fixed)
        self._plant_timeout_key(default_fixed, None)
        proc = self.invoke(
            ["prove", "--fixed", str(default_fixed)],
            agent=None,
            timeout=SLEEP_PROVE_CAP,
        )
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(
            rows.get("SYN-01"),
            "UNREACHABLE",
            "prove --fixed without probe_timeout keeps the two-second default: "
            f"{proc.stdout!r}",
        )

    def test_probe_timeout_x_zero_and_301_refuse_manifest(self):
        for value in ("x", 0, 301, True, False, -1, 1.5):
            with self.subTest(probe_timeout=value):
                made = self.plant_made_dir(f"bad-timeout-{value}")
                self._plant_timeout_key(made, value)
                proc = self.invoke(["prove", str(made)], agent=None)
                assert_refusal_wire(self, proc, "manifest")
                err = proc.stderr or ""
                self.assertIn("probe_timeout", err.lower())
                self.assertRegex(err, r"1\.\.300")

    def test_probe_timeout_1_and_300_are_accepted(self):
        for value in (1, 300):
            with self.subTest(probe_timeout=value, stage="no-refuse"):
                made = self.plant_made_dir(f"ok-timeout-{value}")
                self._plant_sleeper(made)
                self._plant_timeout_key(made, value)
                proc = self.invoke(
                    ["prove", str(made)],
                    agent=None,
                    timeout=SLEEP_PROVE_CAP,
                )
                self.assertNotEqual(
                    proc.returncode,
                    2,
                    f"probe_timeout {value} is in 1..300 and must not refuse: "
                    f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
                )
                rows = self.parse_prove(proc.stdout)
                self.assertIn("SYN-01", rows, proc.stdout)
                if value == 1:
                    self.assertEqual(
                        rows["SYN-01"],
                        "UNREACHABLE",
                        "probe_timeout 1 must not colour a 3 s probe: "
                        f"{proc.stdout!r}",
                    )
                else:
                    self.assertIn(
                        rows["SYN-01"],
                        {"RED", "GREEN"},
                        "probe_timeout 300 must colour a 3 s probe, not "
                        f"UNREACHABLE: {proc.stdout!r} stderr={proc.stderr!r}",
                    )
                    self.assertNotEqual(rows["SYN-01"], "UNREACHABLE")

    def test_prove_fixed_ignores_vendor_target_and_venv_with_unreadable_files(self):
        made = self.plant_made_repo("preflight-ignore")
        blocked: list[Path] = []
        try:
            for rel in PREFLIGHT_UNREADABLE:
                path = made / rel
                self._plant_bytes(path, b"UNREADABLE_PREFLIGHT_BAIT\n")
                path.chmod(0o000)
                self.assertTrue(path.is_file(), rel)
                self.assertFalse(
                    os.access(path, os.R_OK),
                    f"{rel} must be unreadable so a copy that follows it fails",
                )
                blocked.append(path)
            proc = self.invoke(["prove", "--fixed", str(made)], agent=None)
            self.assertEqual(
                proc.returncode,
                0,
                "prove --fixed must ignore vendor/, target/, .venv/, var/, "
                ".phpunit.cache/ and node_modules/ in the preflight copy: "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
            rows = self.parse_prove(proc.stdout)
            self.assertEqual(rows.get("SYN-01"), "GREEN", proc.stdout)
            self.assertEqual(rows.get("SYN-02"), "GREEN", proc.stdout)
        finally:
            for path in blocked:
                if path.exists():
                    path.chmod(0o644)


if __name__ == "__main__":
    unittest.main()
