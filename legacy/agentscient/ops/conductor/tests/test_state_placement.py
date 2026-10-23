"""Every conductor tool declares its state engine; a file store is the pinned exception.

Owner, 2026-09-13: "i need to prevent future default to files when SQLite
is better option" and "i don't files as state". Contract:
`ops/conductor/instrument-conventions.md` §State. Each tool's placement
file (`ops/conductor/placement/<tool>.py.json`) carries a `state` object:
`engine` is `sqlite`, `none` or `file`; `files` is the reason whenever the
tool writes files at all. The gate reads the tools by ast and closes the
declaration both ways:

- `sqlite` needs an import of sqlite3 or of the engine (machine.py);
- `none` needs no file-write call in the tool;
- any file-write call needs a `files` reason;
- `file` needs write calls and a place in FILE_BACKED, the list pinned
  here on 2026-09-13 from the census of that day; a tool declaring
  `file` outside the pin is a new file-backed store and fails naming
  it, and a pinned tool that no longer declares `file` fails asking
  for the pin to shrink.

Written by hand under grant 220, one producer (Claude Fable 5.1), the
second producer absent by the owner's word (AGENTS.md §Ground rules).
The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_state_placement.py`;
its first reading, before the placement files carry a `state` key, and
its reading after are quoted in the commit that carries it.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
PLACEMENT = REPO / "ops" / "conductor" / "placement"
ENGINES = ("sqlite", "none", "file")
# The file-backed tools as measured on 2026-09-13 (the census in the
# commit that carries this file). Moving one onto the engine removes it
# here in the same change; adding one is a reviewable diff of this line.
FILE_BACKED = frozenset({"control.py", "deliverable.py", "ladder.py"})
WRITE_ATTRS = frozenset({
    "write_text", "write_bytes", "dump", "rename", "copyfile", "copy2",
    "unlink", "rmtree", "touch", "symlink_to",
})
STATE_MODULES = frozenset({"sqlite3", "machine"})


def _mode_writes(call: ast.Call, *, skip: int) -> bool:
    args = call.args[skip:]
    keywords = {k.arg: k.value for k in call.keywords}
    mode = keywords.get("mode") or (args[0] if args else None)
    return (
        isinstance(mode, ast.Constant)
        and isinstance(mode.value, str)
        and any(letter in mode.value for letter in "wax+")
    )


def write_calls(source: str) -> list[tuple[int, str]]:
    """(line, call) for every file-write call the source makes."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            if func.attr in WRITE_ATTRS:
                found.append((node.lineno, func.attr))
            elif func.attr == "replace" and isinstance(func.value, ast.Name) and func.value.id == "os":
                found.append((node.lineno, "os.replace"))
            elif func.attr == "open" and _mode_writes(node, skip=0):
                found.append((node.lineno, "open(w)"))
        elif isinstance(func, ast.Name) and func.id == "open" and _mode_writes(node, skip=1):
            found.append((node.lineno, "open(w)"))
    return found


def uses_state_engine(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(a.name in STATE_MODULES for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module in STATE_MODULES:
            return True
    return False


def check(tools: Path, placement: Path, pin: frozenset[str]) -> list[str]:
    """Every failure of the declaration, each naming the tool; empty is clean."""
    failures: list[str] = []
    declared_file: set[str] = set()
    for path in sorted(placement.glob("*.py.json")):
        tool = path.name[: -len(".json")]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"{tool}: placement file unreadable: {error}")
            continue
        state = data.get("state") if isinstance(data, dict) else None
        if not isinstance(state, dict) or state.get("engine") not in ENGINES:
            failures.append(
                f"{tool}: expected state.engine of sqlite, none or file in {path.name}; "
                f"found {state!r}"
            )
            continue
        engine = state["engine"]
        reason = state.get("files")
        source_path = tools / tool
        if not source_path.is_file():
            failures.append(f"{tool}: placement names a tool that is absent")
            continue
        source = source_path.read_text(encoding="utf-8")
        writes = write_calls(source)
        if engine == "sqlite" and not uses_state_engine(source):
            failures.append(f"{tool}: declares sqlite and imports neither sqlite3 nor machine")
        if engine == "none" and writes:
            line, call = writes[0]
            failures.append(f"{tool}: declares none and writes files ({call} at line {line})")
        if writes and not (isinstance(reason, str) and reason.strip()):
            line, call = writes[0]
            failures.append(
                f"{tool}: writes files ({call} at line {line}) and state.files gives no reason"
            )
        if engine == "file":
            declared_file.add(tool)
            if not writes:
                failures.append(f"{tool}: declares file and makes no file-write call")
            if tool not in pin:
                failures.append(
                    f"{tool}: a new file-backed store; needed the state engine (machine.py), "
                    "or a row in FILE_BACKED with the owner's word"
                )
    for tool in sorted(pin - declared_file):
        failures.append(f"{tool}: pinned as file-backed and not declared file; shrink FILE_BACKED")
    return failures


class TheRealTree(unittest.TestCase):
    def test_every_placement_file_declares_its_state_engine_and_the_tree_agrees(self):
        failures = check(TOOLS, PLACEMENT, FILE_BACKED)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_the_two_engine_tools_declare_sqlite(self):
        for name in ("machine.py", "handoff.py"):
            data = json.loads((PLACEMENT / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["state"]["engine"], "sqlite", name)


class PlantedTrees(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="state-placement-")
        self.addCleanup(self.world.close)
        self.tools = self.world.path("tools")
        self.placement = self.world.path("placement")
        self.tools.mkdir()
        self.placement.mkdir()

    def plant(self, name: str, source: str, state: dict | None) -> None:
        self.world.plant_text(self.tools / name, source)
        data = {"placement": "migrated"}
        if state is not None:
            data["state"] = state
        self.world.plant_text(
            self.placement / f"{name}.json", json.dumps(data),
        )

    def test_a_clean_sqlite_tool_passes(self):
        self.plant("a.py", "import sqlite3\n", {"engine": "sqlite"})
        self.assertEqual(check(self.tools, self.placement, frozenset()), [])

    def test_a_tool_on_the_engine_passes_as_sqlite(self):
        self.plant("a.py", "import machine\n", {"engine": "sqlite"})
        self.assertEqual(check(self.tools, self.placement, frozenset()), [])

    def test_a_read_only_tool_passes_as_none(self):
        self.plant("a.py", "from pathlib import Path\nprint(Path('x').read_text())\n", {"engine": "none"})
        self.assertEqual(check(self.tools, self.placement, frozenset()), [])

    def test_a_missing_state_key_fails_naming_the_tool(self):
        self.plant("a.py", "import sqlite3\n", None)
        failures = check(self.tools, self.placement, frozenset())
        self.assertEqual(len(failures), 1)
        self.assertIn("a.py", failures[0])
        self.assertIn("state.engine", failures[0])

    def test_sqlite_without_the_import_fails(self):
        self.plant("a.py", "print(1)\n", {"engine": "sqlite"})
        failures = check(self.tools, self.placement, frozenset())
        self.assertEqual(len(failures), 1)
        self.assertIn("imports neither", failures[0])

    def test_none_with_a_write_fails_naming_the_call_and_line(self):
        self.plant("a.py", "from pathlib import Path\n\nPath('x').write_text('y')\n", {"engine": "none"})
        failures = check(self.tools, self.placement, frozenset())
        self.assertTrue(any("write_text at line 3" in f for f in failures), failures)

    def test_a_write_without_a_files_reason_fails(self):
        self.plant("a.py", "import sqlite3, json\njson.dump({}, open('x', 'w'))\n", {"engine": "sqlite"})
        failures = check(self.tools, self.placement, frozenset())
        self.assertEqual(len(failures), 1)
        self.assertIn("state.files gives no reason", failures[0])

    def test_a_write_with_a_files_reason_passes(self):
        self.plant("a.py", "import sqlite3, json\njson.dump({}, open('x', 'w'))\n",
                   {"engine": "sqlite", "files": "the export is a record"})
        self.assertEqual(check(self.tools, self.placement, frozenset()), [])

    def test_a_new_file_backed_tool_outside_the_pin_fails(self):
        self.plant("a.py", "from pathlib import Path\nPath('x').write_text('y')\n",
                   {"engine": "file", "files": "a lock"})
        failures = check(self.tools, self.placement, frozenset())
        self.assertEqual(len(failures), 1)
        self.assertIn("a new file-backed store", failures[0])
        self.assertIn("machine.py", failures[0])

    def test_a_pinned_file_backed_tool_passes(self):
        self.plant("a.py", "from pathlib import Path\nPath('x').write_text('y')\n",
                   {"engine": "file", "files": "a lock"})
        self.assertEqual(check(self.tools, self.placement, frozenset({"a.py"})), [])

    def test_a_pinned_tool_that_stopped_declaring_file_asks_the_pin_to_shrink(self):
        self.plant("a.py", "import sqlite3\n", {"engine": "sqlite"})
        failures = check(self.tools, self.placement, frozenset({"a.py"}))
        self.assertEqual(len(failures), 1)
        self.assertIn("shrink FILE_BACKED", failures[0])

    def test_file_without_a_write_fails(self):
        self.plant("a.py", "print(1)\n", {"engine": "file", "files": "claimed"})
        failures = check(self.tools, self.placement, frozenset({"a.py"}))
        self.assertTrue(any("makes no file-write call" in f for f in failures), failures)

    def test_the_write_scanner_reads_the_forms_the_tools_use(self):
        source = (
            "import json, os, shutil\nfrom pathlib import Path\n"
            "Path('a').write_bytes(b'')\n"
            "os.replace('a', 'b')\n"
            "with open('c', mode='a') as fh: json.dump({}, fh)\n"
            "with Path('d').open('w') as fh: pass\n"
            "shutil.copyfile('a', 'e')\n"
            "print(open('f').read())\n"
            "print(str.replace('x', 'y'))\n"
        )
        kinds = [kind for _, kind in write_calls(source)]
        self.assertEqual(
            sorted(kinds), sorted(["write_bytes", "os.replace", "open(w)", "dump", "open(w)", "copyfile"])
        )


if __name__ == "__main__":
    unittest.main()
