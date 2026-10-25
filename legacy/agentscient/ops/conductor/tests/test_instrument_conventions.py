"""The conformance gate of instrument-conventions.md.

The registry is the directory ops/conductor/registry/: one JSON file
per instrument, named by the instrument (watchdog.py.json for
ops/conductor/tools/watchdog.py), read in sorted file-name order. A
new instrument joins by adding its file in the PR that lands it.
skills.py and co_doc.py predate the wire and join as they adopt it.

Every registered row still has one hermetic refusal-provoking
invocation, and the refusal wire is asserted on all of them
identically: exit 2, a single non-empty stderr line, the class token,
expected / found / needed in order, and an empty stdout.

The gate closes both ways: a file whose tool does not exist under
ops/conductor/tools/ is a failure naming the file; a tool under
ops/conductor/tools/ whose module docstring names
instrument-conventions.md and has no file is a failure naming the
tool; a malformed file is a failure naming the file and the field.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

from ops.testkit import RefusalCase, World, refusal_wire, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
REGISTRY_DIR = REPO / "ops" / "conductor" / "registry"
AGENT = "Conventions Referee <noreply@anthropic.com>"
CONVENTIONS_MARK = "instrument-conventions.md"
CLASS_TOKEN = re.compile(r"^[a-z]+(?:-[a-z0-9]+)*$")
VALID_ROW = {"argv": ["unexpected"], "class": "arguments", "identity": False}
DECLARING_STUB = (
    '"""Planted instrument that inherits instrument-conventions.md."""\n'
    "import sys\n"
    "sys.exit(0)\n"
)
SILENT_STUB = (
    '"""A tool that does not declare the conventions."""\n'
    "import sys\n"
    "sys.exit(0)\n"
)

# Original 10 files and 11 rows at a59e8a5, ordered as the gate reads
# them (sorted file name; findings.py rows in tuple encounter order).
# The gate does not iterate this; it is the pin the directory must match.
SEED_FILES = (
    "branches.py.json",
    "ci.py.json",
    "decisions.py.json",
    "findings.py.json",
    "hand.py.json",
    "land.py.json",
    "laneconf.py.json",
    "onboard.py.json",
    "watch.py.json",
    "watchdog.py.json",
)
SEED_ROWS = (
    ("branches.py", ("census", "unexpected"), "arguments", False),
    ("ci.py", ("report",), "store", False),
    (
        "decisions.py",
        ("add", "bad-id", "should this stand as recorded", "--home", "x.md"),
        "id-shape",
        True,
    ),
    (
        "findings.py",
        ("disposition", "NOSUCH#1", "fixed", "why"),
        "key",
        True,
    ),
    ("findings.py", ("calibrate",), "calibration", True),
    (
        "hand.py",
        ("report",),
        "store",
        False,
    ),
    ("land.py", ("NOSUCH-RECORD",), "record", True),
    ("laneconf.py", ("get", "NO_SUCH_KEY"), "key", False),
    ("onboard.py", ("unexpected",), "arguments", False),
    ("watch.py", ("pr", "0"), "number", False),
    ("watchdog.py", ("--store", "/dev/null"), "store", False),
)


@dataclass(frozen=True)
class RegistryRow:
    source: str
    tool: str
    argv: tuple[str, ...]
    refusal: str
    identity: bool


def row_pin(
    row: RegistryRow,
) -> tuple[str, tuple[str, ...], str, bool]:
    return (row.tool, row.argv, row.refusal, row.identity)


@dataclass(frozen=True)
class GateFailure:
    target: str
    field: str | None = None


def declaring_tools(tools_dir: Path) -> list[str]:
    names: list[str] = []
    for path in sorted(tools_dir.glob("*.py"), key=lambda item: item.name):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        doc = ast.get_docstring(tree) or ""
        if CONVENTIONS_MARK in doc:
            names.append(path.name)
    return names


def parse_registry_file(path: Path) -> tuple[list[RegistryRow], GateFailure | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], GateFailure(path.name, "json")
    if not isinstance(data, dict):
        return [], GateFailure(path.name, "object")
    tool = data.get("tool")
    if not isinstance(tool, str) or not tool or path.name != f"{tool}.json":
        return [], GateFailure(path.name, "tool")
    raw_rows = data.get("rows")
    if not isinstance(raw_rows, list):
        return [], GateFailure(path.name, "rows")
    if len(raw_rows) == 0:
        return [], GateFailure(path.name, "rows")
    rows: list[RegistryRow] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            return [], GateFailure(path.name, "rows")
        if "argv" not in item:
            return [], GateFailure(path.name, "argv")
        if "class" not in item:
            return [], GateFailure(path.name, "class")
        if "identity" not in item:
            return [], GateFailure(path.name, "identity")
        argv = item["argv"]
        if not isinstance(argv, list) or any(not isinstance(part, str) for part in argv):
            return [], GateFailure(path.name, "argv")
        refusal = item["class"]
        if not isinstance(refusal, str) or CLASS_TOKEN.fullmatch(refusal) is None:
            return [], GateFailure(path.name, "class")
        identity = item["identity"]
        if not isinstance(identity, bool):
            return [], GateFailure(path.name, "identity")
        rows.append(
            RegistryRow(
                source=path.name,
                tool=tool,
                argv=tuple(argv),
                refusal=refusal,
                identity=identity,
            )
        )
    return rows, None


def read_registry(registry_dir: Path) -> tuple[list[RegistryRow], list[GateFailure]]:
    """Load rows from one-row-file-per-instrument JSON, sorted by file name."""
    rows: list[RegistryRow] = []
    failures: list[GateFailure] = []
    if not registry_dir.is_dir():
        return [], []
    for path in sorted(registry_dir.glob("*.json"), key=lambda item: item.name):
        parsed, failure = parse_registry_file(path)
        if failure is not None:
            failures.append(failure)
            continue
        rows.extend(parsed)
    return rows, failures


def closure_failures(registry_dir: Path, tools_dir: Path) -> list[GateFailure]:
    failures: list[GateFailure] = []
    if registry_dir.is_dir():
        for path in sorted(registry_dir.glob("*.json"), key=lambda item: item.name):
            parsed, failure = parse_registry_file(path)
            if failure is not None:
                failures.append(failure)
                continue
            if not (tools_dir / parsed[0].tool).is_file():
                failures.append(GateFailure(path.name))
    for name in declaring_tools(tools_dir):
        if not (registry_dir / f"{name}.json").is_file():
            failures.append(GateFailure(name))
    return failures


class TheRefusalWireIsUniform(unittest.TestCase):
    def live_rows(self) -> list[RegistryRow]:
        self.assertTrue(
            REGISTRY_DIR.is_dir(),
            f"registry directory missing: {REGISTRY_DIR}",
        )
        rows, failures = read_registry(REGISTRY_DIR)
        self.assertEqual(failures, [], f"malformed registry files: {failures}")
        self.assertGreater(len(rows), 0, "registry has no rows")
        return rows

    def test_seed_is_ten_files_named_by_the_instrument(self):
        names = sorted(path.name for path in REGISTRY_DIR.glob("*.json"))
        # The seed is present, byte for byte; later instruments join beside it.
        self.assertGreaterEqual(len(names), 10)
        self.assertEqual([n for n in names if n in SEED_FILES], list(SEED_FILES))

    def test_registered_wire_examples_remain_covered(self):
        rows, failures = read_registry(REGISTRY_DIR)
        self.assertEqual(failures, [], f"malformed registry files: {failures}")
        pins = [row_pin(row) for row in rows]
        self.assertGreaterEqual(len(pins), 11)
        self.assertEqual([pin for pin in pins if pin in SEED_ROWS], list(SEED_ROWS))
        findings = [row for row in rows if row.tool == "findings.py"]
        self.assertEqual(len(findings), 2)
        self.assertEqual({row.source for row in findings}, {"findings.py.json"})
        self.assertEqual(
            findings[0].argv,
            ("disposition", "NOSUCH#1", "fixed", "why"),
        )
        self.assertEqual(findings[1].argv, ("calibrate",))
        self.assertEqual(findings[0].refusal, "key")
        self.assertEqual(findings[1].refusal, "calibration")
        self.assertTrue(findings[0].identity)
        self.assertTrue(findings[1].identity)

    def invoke(self, tool: str, argv: list[str], *, identity: bool):
        with World(prefix="conventions-") as world:
            extra = {}
            if identity:
                extra["WF_AGENT"] = AGENT
            return run(
                [sys.executable, str(TOOLS / tool), *argv],
                cwd=world.cwd,
                env=world.env(extra),
                stdin="",
                timeout=60,
            )

    def test_every_registered_instrument_exists(self):
        for row in self.live_rows():
            with self.subTest(tool=row.tool):
                self.assertTrue(
                    (TOOLS / row.tool).is_file(),
                    f"registered instrument missing: {row.tool}",
                )

    def test_every_registered_instrument_speaks_the_wire(self):
        for row in self.live_rows():
            with self.subTest(tool=row.tool, refusal=row.refusal):
                result = self.invoke(row.tool, list(row.argv), identity=row.identity)
                refusal_wire(
                    RefusalCase(
                        id=f"{row.source}:{' '.join(row.argv)}",
                        argv=row.argv,
                        cls=row.refusal,
                        origin=row.source,
                    ),
                    result,
                )

    def test_identity_refusal_is_uniform_for_writing_instruments(self):
        rows = self.live_rows()
        writers = [row for row in rows if row.identity]
        self.assertGreater(len(writers), 0, "registry has no writing instruments")
        for row in writers:
            with self.subTest(tool=row.tool, argv=row.argv):
                result = self.invoke(row.tool, list(row.argv), identity=False)
                refusal_wire(
                    RefusalCase(
                        id=f"{row.source}:identity",
                        argv=row.argv,
                        cls="identity",
                        origin=row.source,
                    ),
                    result,
                )


class RegistryClosure(unittest.TestCase):
    def _scratch(self) -> tuple[Path, Path]:
        self.world = World(prefix="registry-")
        self.addCleanup(self.world.close)
        tools = self.world.root / "tools"
        registry = self.world.root / "registry"
        tools.mkdir()
        registry.mkdir()
        return tools, registry

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)
        landed = path.read_bytes()
        self.assertEqual(landed, data, f"plant did not land intact: {path}")
        self.assertEqual(len(landed), len(data))
        self.assertNotEqual(landed, b"")

    def _plant_registry(self, registry: Path, tool: str, rows: list[dict]) -> Path:
        payload = {"tool": tool, "rows": rows}
        body = json.dumps(payload).encode("utf-8")
        path = registry / f"{tool}.json"
        self._plant_bytes(path, body)
        return path

    def _plant_payload(self, registry: Path, filename: str, payload) -> Path:
        if isinstance(payload, (bytes, bytearray)):
            body = bytes(payload)
        else:
            body = json.dumps(payload).encode("utf-8")
        path = registry / filename
        self._plant_bytes(path, body)
        return path

    def _plant_seed_rows(self, registry: Path, pins: tuple) -> None:
        grouped: dict[str, list[dict]] = {}
        for tool, argv, cls, ident in pins:
            grouped.setdefault(tool, []).append(
                {"argv": list(argv), "class": cls, "identity": ident}
            )
        for tool, rows in grouped.items():
            self._plant_registry(registry, tool, rows)

    def _assert_malformed(self, filename: str, payload, field: str, prove) -> None:
        tools, registry = self._scratch()
        path = self._plant_payload(registry, filename, payload)
        landed = path.read_bytes()
        if isinstance(payload, (bytes, bytearray)):
            prove(landed)
        else:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(prove(parsed), f"plant did not keep the fault: {parsed!r}")
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [GateFailure(filename, field)])

    def test_every_declaring_tool_has_a_registry_file(self):
        declaring = declaring_tools(TOOLS)
        self.assertGreater(
            len(declaring), 0, "no tool declares instrument-conventions.md"
        )
        missing = [
            name
            for name in declaring
            if not (REGISTRY_DIR / f"{name}.json").is_file()
        ]
        self.assertEqual(
            missing,
            [],
            "declaring tool with no file: " + ", ".join(missing),
        )

    def test_file_for_a_missing_tool_is_a_failure_naming_the_file(self):
        tools, registry = self._scratch()
        path = self._plant_registry(registry, "ghost.py", [VALID_ROW])
        self.assertTrue(path.is_file(), "plant: registry file did not land")
        self.assertFalse(
            (tools / "ghost.py").exists(),
            "plant: ghost.py must stay missing",
        )
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [GateFailure("ghost.py.json")])

    def test_declaring_tool_with_no_file_is_a_failure_naming_the_tool(self):
        tools, registry = self._scratch()
        planted = tools / "named.py"
        self._plant_bytes(planted, DECLARING_STUB.encode("utf-8"))
        landed = planted.read_text(encoding="utf-8")
        self.assertIn(CONVENTIONS_MARK, landed)
        self.assertFalse(
            (registry / "named.py.json").exists(),
            "plant: named.py.json must stay missing",
        )
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [GateFailure("named.py")])

    def test_malformed_file_that_is_not_the_shape_names_the_file_and_the_field(self):
        tools, registry = self._scratch()
        path = registry / "not-shape.py.json"
        body = b"[]\n"
        self._plant_bytes(path, body)
        self.assertEqual(path.read_bytes(), body)
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [GateFailure("not-shape.py.json", "object")])

    def test_malformed_file_with_empty_rows_names_the_file_and_the_field(self):
        tools, registry = self._scratch()
        path = self._plant_registry(registry, "empty-rows.py", [])
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["rows"], [])
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [GateFailure("empty-rows.py.json", "rows")])

    def test_malformed_file_with_a_class_that_is_not_a_lower_case_token(
        self,
    ):
        tools, registry = self._scratch()
        row = {"argv": ["unexpected"], "class": "Arguments", "identity": False}
        path = self._plant_registry(registry, "bad-class.py", [row])
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["rows"][0]["class"], "Arguments")
        planted_class = payload["rows"][0]["class"]
        self.assertNotEqual(planted_class, planted_class.lower())
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [GateFailure("bad-class.py.json", "class")])

    def test_malformed_file_with_absent_identity_names_the_file_and_the_field(self):
        row = {"argv": ["unexpected"], "class": "arguments"}
        self._assert_malformed(
            "no-identity.py.json",
            {"tool": "no-identity.py", "rows": [row]},
            "identity",
            lambda parsed: "identity" not in parsed["rows"][0]
            and parsed["rows"][0]["class"] == "arguments",
        )

    def test_malformed_file_with_a_wrong_type_per_field_names_the_file_and_the_field(
        self,
    ):
        cases = (
            (
                "tool-type.py.json",
                {"tool": 1, "rows": [VALID_ROW]},
                "tool",
                lambda parsed: isinstance(parsed["tool"], int),
            ),
            (
                "rows-type.py.json",
                {"tool": "rows-type.py", "rows": {"n": 1}},
                "rows",
                lambda parsed: isinstance(parsed["rows"], dict),
            ),
            (
                "argv-type.py.json",
                {
                    "tool": "argv-type.py",
                    "rows": [
                        {
                            "argv": "unexpected",
                            "class": "arguments",
                            "identity": False,
                        }
                    ],
                },
                "argv",
                lambda parsed: isinstance(parsed["rows"][0]["argv"], str),
            ),
            (
                "argv-item-type.py.json",
                {
                    "tool": "argv-item-type.py",
                    "rows": [
                        {
                            "argv": [1],
                            "class": "arguments",
                            "identity": False,
                        }
                    ],
                },
                "argv",
                lambda parsed: parsed["rows"][0]["argv"] == [1],
            ),
            (
                "class-type.py.json",
                {
                    "tool": "class-type.py",
                    "rows": [
                        {
                            "argv": ["unexpected"],
                            "class": 1,
                            "identity": False,
                        }
                    ],
                },
                "class",
                lambda parsed: isinstance(parsed["rows"][0]["class"], int),
            ),
            (
                "identity-type.py.json",
                {
                    "tool": "identity-type.py",
                    "rows": [
                        {
                            "argv": ["unexpected"],
                            "class": "arguments",
                            "identity": 0,
                        }
                    ],
                },
                "identity",
                lambda parsed: parsed["rows"][0]["identity"] == 0
                and not isinstance(parsed["rows"][0]["identity"], bool),
            ),
        )
        for filename, payload, field, prove in cases:
            with self.subTest(field=field, filename=filename):
                self._assert_malformed(filename, payload, field, prove)

    def test_malformed_file_with_an_absent_required_field_names_the_file_and_the_field(
        self,
    ):
        cases = (
            (
                "no-tool.py.json",
                {"rows": [VALID_ROW]},
                "tool",
                lambda parsed: "tool" not in parsed,
            ),
            (
                "no-rows.py.json",
                {"tool": "no-rows.py"},
                "rows",
                lambda parsed: "rows" not in parsed,
            ),
            (
                "no-argv.py.json",
                {
                    "tool": "no-argv.py",
                    "rows": [{"class": "arguments", "identity": False}],
                },
                "argv",
                lambda parsed: "argv" not in parsed["rows"][0],
            ),
            (
                "no-class.py.json",
                {
                    "tool": "no-class.py",
                    "rows": [{"argv": ["unexpected"], "identity": False}],
                },
                "class",
                lambda parsed: "class" not in parsed["rows"][0],
            ),
        )
        for filename, payload, field, prove in cases:
            with self.subTest(field=field, filename=filename):
                self._assert_malformed(filename, payload, field, prove)

    def test_malformed_file_that_is_not_an_object_or_valid_json_names_the_file_and_the_field(
        self,
    ):
        cases = (
            (
                "null.py.json",
                None,
                "object",
                lambda parsed: parsed is None,
            ),
            (
                "string.py.json",
                "nope",
                "object",
                lambda parsed: parsed == "nope",
            ),
            (
                "row-type.py.json",
                {"tool": "row-type.py", "rows": ["nope"]},
                "rows",
                lambda parsed: parsed["rows"] == ["nope"],
            ),
            (
                "tool-mismatch.py.json",
                {"tool": "other.py", "rows": [VALID_ROW]},
                "tool",
                lambda parsed: parsed["tool"] == "other.py",
            ),
        )
        for filename, payload, field, prove in cases:
            with self.subTest(field=field, filename=filename):
                self._assert_malformed(filename, payload, field, prove)
        tools, registry = self._scratch()
        body = b"{not json\n"
        path = self._plant_payload(registry, "bad-json.py.json", body)
        landed = path.read_bytes()
        self.assertEqual(landed, body)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(landed.decode("utf-8"))
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [GateFailure("bad-json.py.json", "json")])

    def test_a_seed_omitting_findings_calibration_fails_the_eleven_row_pin(self):
        _tools, registry = self._scratch()
        omitted = tuple(
            pin
            for pin in SEED_ROWS
            if pin != ("findings.py", ("calibrate",), "calibration", True)
        )
        self._plant_seed_rows(registry, omitted)
        findings_path = registry / "findings.py.json"
        payload = json.loads(findings_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(
            payload["rows"][0]["argv"],
            ["disposition", "NOSUCH#1", "fixed", "why"],
        )
        self.assertNotIn("calibrate", json.dumps(payload))
        names = sorted(path.name for path in registry.glob("*.json"))
        self.assertEqual(len(names), 10)
        self.assertEqual(names, list(SEED_FILES))
        rows, failures = read_registry(registry)
        self.assertEqual(failures, [])
        pins = [row_pin(row) for row in rows]
        self.assertEqual(len(pins), 10)
        self.assertNotEqual(pins, list(SEED_ROWS))
        self.assertNotIn(
            ("findings.py", ("calibrate",), "calibration", True),
            pins,
        )

    def test_clean_planted_registry_passes(self):
        tools, registry = self._scratch()
        alpha = tools / "alpha.py"
        mute = tools / "mute.py"
        self._plant_bytes(alpha, DECLARING_STUB.encode("utf-8"))
        self._plant_bytes(mute, SILENT_STUB.encode("utf-8"))
        self.assertIn(CONVENTIONS_MARK, alpha.read_text(encoding="utf-8"))
        self.assertNotIn(CONVENTIONS_MARK, mute.read_text(encoding="utf-8"))
        rows = [
            {"argv": ["unexpected"], "class": "arguments", "identity": False},
            {"argv": ["NOSUCH"], "class": "record", "identity": True},
        ]
        path = self._plant_registry(registry, "alpha.py", rows)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(payload["tool"], "alpha.py")
        failures = closure_failures(registry, tools)
        self.assertEqual(failures, [])

    def test_registry_is_read_sorted_by_file_name(self):
        _tools, registry = self._scratch()
        self._plant_registry(registry, "zebra.py", [VALID_ROW])
        self._plant_registry(registry, "aardvark.py", [VALID_ROW])
        rows, failures = read_registry(registry)
        self.assertEqual(failures, [])
        self.assertGreater(len(rows), 0, "planted registry has no rows")
        names = [row.source for row in rows]
        self.assertEqual(names, ["aardvark.py.json", "zebra.py.json"])
        self.assertEqual(names, sorted(names))


if __name__ == "__main__":
    unittest.main()
