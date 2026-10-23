#!/usr/bin/env python3
"""The census ratchet: three counts over the test modules that never rise.

The test census of 2026-09-13 (record 20260913T023636Z-review-codex-bdbe55)
measured 49 modules setting HOME by hand, 76 embedded fake programs and
171 subprocess.run calls with no timeout. docs/tests.md rules 4 to 6 say
a world is hermetic, a fake is planted through the registry and every
subprocess goes through run; the kit takes each family off those counts
as it migrates. This check holds the ceiling: each count may fall, never
rise, and a fall prints the new floor for the baseline to take.

    testkit_ratchet.py [--root DIR] [--baseline FILE]      exit 0 no count rose; 1 a count rose (Finding)
    testkit_ratchet.py --write-baseline [...]              needs WF_AGENT; writes the baseline at the counts now
    testkit_ratchet.py --census [--root DIR]               one line: modules=N on_kit=N exempt=N off_kit=N

Counts, by ast over every *.py under a tests/ directory of the root
(ops/ and .claude/skills/), the kit's own tests included, the fixture
applications under ops/devlane/fixtures/apps/ excluded (they are subjects,
not lane tests):
  home_by_hand           modules with an assignment to ["HOME"] or a dict literal keyed "HOME"
  embedded_programs      string literals carrying #!/usr/bin/env python3
  run_without_timeout    subprocess.run(...) calls with no timeout keyword
Refusals, one stderr line, exit 2: baseline (absent or malformed), identity, arguments.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASELINE = Path(__file__).resolve().parent / "testkit-ratchet.json"
COUNTS = ("home_by_hand", "embedded_programs", "run_without_timeout")
SHEBANG = "#!/usr/bin/env python3"
TEST_ROOTS = ("ops", ".claude/skills")
NOT_LANE_TESTS = ("ops/devlane/fixtures/apps",)
KIT_IMPORT = re.compile(r"^\s*(from\s+ops\.testkit(\.\w+)*\s+import\b|import\s+ops\.testkit\b)", re.MULTILINE)
KIT_EXEMPT = re.compile(r"^[ \t]*# testkit: exempt[ \t]+\S", re.MULTILINE)


def refuse(cls, expected, found, needed) -> int:
    sys.stderr.write(f"{cls}: expected {expected}; found {found}; needed {needed}\n")
    return 2


def test_modules(root: Path) -> list[Path]:
    found = []
    for top in TEST_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git", "node_modules", "vendor")]
            if any(Path(dirpath).relative_to(root).as_posix().startswith(skip) for skip in NOT_LANE_TESTS):
                dirnames[:] = []
                continue
            # every directory at or below a tests/ directory (ruled rank 7,
            # record 20260915T025600Z-adjudicate-codex-68d3d9: tests/fakes/
            # grew the debt uncounted)
            if "tests" not in Path(dirpath).relative_to(base).parts:
                continue
            for name in filenames:
                if name.endswith(".py"):
                    found.append(Path(dirpath) / name)
    return sorted(found)


def measure_module(path: Path) -> dict[str, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return dict.fromkeys(COUNTS, 0)
    home = 0
    programs = 0
    runs = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value == "HOME":
                home = 1
        elif isinstance(node, ast.Dict):
            if any(isinstance(k, ast.Constant) and k.value == "HOME" for k in node.keys):
                home = 1
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and SHEBANG in node.value:
            programs += 1
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run"
              and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
              and not any(kw.arg == "timeout" for kw in node.keywords)):
            runs += 1
    return {"home_by_hand": home, "embedded_programs": programs, "run_without_timeout": runs}


def census(root: Path) -> dict:
    """modules, on_kit (a real kit import in the first ten lines or anywhere),
    exempt (`# testkit: exempt <reason>` in the first ten lines), off_kit."""
    modules = test_modules(root)
    on_kit = exempt = 0
    for path in modules:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if KIT_IMPORT.search(text):
            on_kit += 1
        elif KIT_EXEMPT.search("\n".join(text.splitlines()[:10])):
            exempt += 1
    return {"modules": len(modules), "on_kit": on_kit, "exempt": exempt, "off_kit": len(modules) - on_kit - exempt}


def measure(root: Path) -> dict:
    files = {name: {} for name in COUNTS}
    for path in test_modules(root):
        rel = path.relative_to(root).as_posix()
        for name, value in measure_module(path).items():
            if value:
                files[name][rel] = value
    counts = {name: sum(files[name].values()) for name in COUNTS}
    return {"counts": counts, "files": files}


def compare(baseline: dict, now: dict) -> tuple[list[str], list[str]]:
    """(rose, fell): one line per count that moved, naming the files behind a rise."""
    rose, fell = [], []
    for name in COUNTS:
        before = int(baseline["counts"][name])
        after = int(now["counts"][name])
        if after > before:
            old_files = baseline.get("files", {}).get(name, {})
            new_files = now["files"][name]
            culprits = sorted(f for f, v in new_files.items() if v > int(old_files.get(f, 0)))
            rose.append(f"{name}: {before} -> {after}; rose in " + ", ".join(culprits))
        elif after < before:
            fell.append(f"{name}: {before} -> {after}; the new floor")
    return rose, fell


def load_baseline(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("counts"), dict):
        return None
    if any(name not in data["counts"] for name in COUNTS):
        return None
    return data


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root, baseline_path, write, want_census = ROOT, BASELINE, False, False
    while args:
        flag = args.pop(0)
        if flag == "--root" and args:
            root = Path(args.pop(0)).resolve()
        elif flag == "--baseline" and args:
            baseline_path = Path(args.pop(0))
        elif flag == "--write-baseline":
            write = True
        elif flag == "--census":
            want_census = True
        else:
            return refuse("arguments", "[--root DIR] [--baseline FILE] [--write-baseline | --census]", flag, "drop or correct it")
    if want_census:
        print(" ".join(f"{k}={v}" for k, v in census(root).items()))
        return 0
    now = measure(root)
    if write:
        agent = os.environ.get("WF_AGENT", "")
        if not re.fullmatch(r".+ <[^<>]+>", agent):
            return refuse("identity", "WF_AGENT as Name <address>", repr(agent), "set WF_AGENT")
        payload = {"counts": now["counts"], "files": now["files"], "by": agent,
                   "note": "the ceiling the ratchet holds; regenerated by --write-baseline when a count fell"}
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline written at {baseline_path}: " + ", ".join(f"{k}={v}" for k, v in now["counts"].items()))
        return 0
    baseline = load_baseline(baseline_path)
    if baseline is None:
        return refuse("baseline", f"a baseline with the three counts at {baseline_path}", "absent or malformed",
                      "run with --write-baseline under WF_AGENT")
    rose, fell = compare(baseline, now)
    for line in fell:
        print(line)
    if rose:
        for line in rose:
            print(line)
        print("the census counts never rise (docs/tests.md rules 4 to 6): build the world, the fake and the run through the kit")
        return 1
    print("ratchet holds: " + ", ".join(f"{k}={v}" for k, v in now["counts"].items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
