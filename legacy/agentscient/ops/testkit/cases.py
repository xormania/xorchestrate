#!/usr/bin/env python3
"""The case pattern on the kit: tables of rows addressed by id, the
refusal-wire assertion on a row, planted faults as data, and two verbs.

``calibrate`` runs a table's command against every mutant of its
subject in an archive copy of the tree and reports caught and quiet per
mutant, exit 1 when any is quiet or the clean run is red. ``sidebyside``
runs an old command and a new one over one mutant set and writes the
JSON record a retirement cites: a mutant caught by the old command and
quiet in the new one is listed, and the verb exits 1. Both refuse
``anchor`` when a mutant's anchor is absent from the subject and
``arguments`` on any other misuse, one line on stderr, exit 2. Neither
touches the tree: the subject is mutated only in the archive copy.

    python3 ops/testkit/cases.py calibrate --subject PATH --mutants PATH --command CMD [--out FILE]
    python3 ops/testkit/cases.py sidebyside --subject PATH --mutants PATH --old CMD --new CMD --out FILE
"""

from __future__ import annotations

import importlib.util
import io
import json
import shlex
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):  # run as a script from the repository root
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.testkit.command import NotOnTheWire, Refusal, Result, run
from ops.testkit.world import PlantFailed, World

COMMAND_TIMEOUT = 900.0


# -- rows ------------------------------------------------------------------

@dataclass(frozen=True)
class RefusalCase:
    """One row of a refusal table: the argv, the class the wire must
    carry, the strings the line must name, and where the row came from."""

    id: str
    argv: tuple[str, ...]
    cls: str
    exit: int = 2
    named: tuple[str, ...] = ()
    origin: str | None = None
    note: str | None = None
    stdin: str | None = None


def refusal_wire(case: RefusalCase, result: Result) -> Refusal:
    """Assert the wire on one row: exit as declared, stdout empty, one
    stderr line, the class token first, expected then found then needed,
    every named string present. Raises AssertionError naming the row."""
    try:
        refusal = result.refusal(exit=case.exit)
    except NotOnTheWire as exc:
        raise AssertionError(f"{case.id}: {exc}") from None
    if refusal.cls != case.cls:
        raise AssertionError(f"{case.id}: class {case.cls!r} expected, found {refusal.cls!r}: {refusal.line!r}")
    for item in case.named:
        if str(item) not in refusal.line:
            raise AssertionError(f"{case.id}: the line must name {item!r}: {refusal.line!r}")
    return refusal


def run_table(test, rows, invoke) -> None:
    """One subTest per row, rows addressed by id; ``invoke(row)`` returns
    the ``Result`` the row is judged on."""
    ids = [row.id for row in rows]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise AssertionError(f"rows are addressed by id; duplicated: {duplicates}")
    if not ids:
        raise AssertionError("an empty table is a declared gap, not a pass")
    for row in rows:
        with test.subTest(id=row.id):
            refusal_wire(row, invoke(row))


# -- planted faults ----------------------------------------------------------

@dataclass(frozen=True)
class Mutant:
    id: str
    path: str
    anchor: str
    replacement: str
    note: str | None = None


class AnchorMissing(Exception):
    """The mutant's anchor does not match the subject exactly once."""


def apply(mutant: Mutant, root) -> Path:
    """Apply one mutant to the copy under ``root``; the anchor must match
    exactly once and the replacement must change the file."""
    target = Path(root) / mutant.path
    if not target.is_file():
        raise AnchorMissing(f"{mutant.id}: {mutant.path} is absent under {root}")
    text = target.read_text(encoding="utf-8")
    count = text.count(mutant.anchor)
    if count != 1:
        raise AnchorMissing(f"{mutant.id}: the anchor matches {count} times in {mutant.path}, not once")
    mutated = text.replace(mutant.anchor, mutant.replacement)
    if mutated == text:
        raise AnchorMissing(f"{mutant.id}: the replacement equals the anchor; nothing would change")
    target.write_text(mutated, encoding="utf-8")
    if target.read_text(encoding="utf-8") != mutated:
        raise PlantFailed(f"INVALID: mutant {mutant.id} did not land in {target}")
    return target


def load_mutants(path) -> list[Mutant]:
    """``MUTANTS`` from a Python file: ``Mutant`` rows or dicts of their fields."""
    path = Path(path)
    if not path.is_file():
        raise AnchorMissing(f"the mutant set {path} is absent")
    spec = importlib.util.spec_from_file_location("planted_faults", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = getattr(module, "MUTANTS", None)
    if not rows:
        raise AnchorMissing(f"{path} declares no MUTANTS")
    out = []
    for row in rows:
        # A row may be this module's Mutant, a copy imported under another
        # module name when the verb runs as a script, or a dict of fields.
        if isinstance(row, dict):
            out.append(Mutant(**row))
        else:
            out.append(Mutant(row.id, row.path, row.anchor, row.replacement, getattr(row, "note", None)))
    ids = [m.id for m in out]
    if len(set(ids)) != len(ids):
        raise AnchorMissing(f"{path} repeats a mutant id")
    return out


# -- the archive copy ----------------------------------------------------------

def head_sha(repo) -> str:
    proc = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(repo),
                          capture_output=True, text=True, check=False, timeout=60)
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def archive(repo, dest, world: World | None = None) -> Path:
    """``git archive HEAD`` into ``dest``: the tree as committed, no
    worktree, nothing of the tree itself touched. With a ``world`` the
    copy is made a repository of its own, one commit at the archived
    content under the world's identity, so a subject or a suite that
    reads HEAD or diffs against it finds one; the copy is still no
    worktree of ``repo``."""
    proc = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=str(repo),
                          capture_output=True, check=False, timeout=300)
    if proc.returncode != 0:
        raise PlantFailed(f"INVALID: git archive HEAD in {repo} failed: {proc.stderr.decode(errors='replace')}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")
    if world is not None:
        world.git("init", "-q", "-b", "archive", cwd=dest)
        world.git("add", "-A", cwd=dest)
        world.git("commit", "-q", "-m", "archive of HEAD", cwd=dest)
    return dest


def verdict(command: str, cwd, world: World, timeout: float) -> str:
    result = run(shlex.split(command), cwd=cwd, env=world.env(), timeout=timeout)
    return "green" if result.exit == 0 else "red"


def _each_mutant(mutants, base, world, commands, timeout):
    """Yield (mutant, {name: verdict}) over fresh copies of the archived base."""
    for mutant in mutants:
        copy = world.root / "copies" / mutant.id
        copy.parent.mkdir(exist_ok=True)
        _copytree(base, copy)
        apply(mutant, copy)
        yield mutant, {name: verdict(cmd, copy, world, timeout) for name, cmd in commands.items()}


def _copytree(src: Path, dst: Path) -> None:
    import shutil
    shutil.copytree(src, dst, symlinks=True)


def check_anchors(mutants, repo) -> None:
    """Every anchor matches its subject in the tree exactly once, before
    any copy is made: an anchor that does not match is refused first."""
    for mutant in mutants:
        target = Path(repo) / mutant.path
        if not target.is_file():
            raise AnchorMissing(f"{mutant.id}: {mutant.path} is absent under {repo}")
        count = target.read_text(encoding="utf-8").count(mutant.anchor)
        if count != 1:
            raise AnchorMissing(f"{mutant.id}: the anchor matches {count} times in {mutant.path}, not once")


def calibrate(subject, mutants_path, command, *, repo=".", timeout=COMMAND_TIMEOUT) -> dict:
    mutants = load_mutants(mutants_path)
    check_anchors(mutants, repo)
    with World(prefix="calibrate-") as world:
        base = archive(repo, world.root / "base", world)
        report = {
            "verb": "calibrate", "subject": str(subject), "command": command,
            "commit": head_sha(repo), "clean": verdict(command, base, world, timeout),
            "mutants": {},
        }
        for mutant, verdicts in _each_mutant(mutants, base, world, {"run": command}, timeout):
            report["mutants"][mutant.id] = "caught" if verdicts["run"] == "red" else "quiet"
    report["quiet"] = sorted(k for k, v in report["mutants"].items() if v == "quiet")
    report["ok"] = report["clean"] == "green" and not report["quiet"]
    return report


def sidebyside(subject, mutants_path, old, new, *, repo=".", timeout=COMMAND_TIMEOUT) -> dict:
    mutants = load_mutants(mutants_path)
    check_anchors(mutants, repo)
    with World(prefix="sidebyside-") as world:
        base = archive(repo, world.root / "base", world)
        record = {
            "verb": "sidebyside", "subject": str(subject), "old": old, "new": new,
            "commit": head_sha(repo),
            "clean": {"old": verdict(old, base, world, timeout), "new": verdict(new, base, world, timeout)},
            "mutants": {},
        }
        for mutant, verdicts in _each_mutant(mutants, base, world, {"old": old, "new": new}, timeout):
            record["mutants"][mutant.id] = verdicts
    record["quiet_in_new"] = sorted(
        k for k, v in record["mutants"].items() if v["old"] == "red" and v["new"] == "green"
    )
    record["ok"] = (record["clean"] == {"old": "green", "new": "green"} and not record["quiet_in_new"])
    return record


# -- the CLI -------------------------------------------------------------------

def refuse(cls: str, expected: str, found: str, needed: str) -> int:
    sys.stderr.write(f"{cls}: expected {expected}; found {found}; needed {needed}\n")
    return 2


def _parse(argv, names):
    values = {}
    it = iter(argv)
    for item in it:
        if not item.startswith("--") or item[2:] not in names:
            raise ValueError(item)
        try:
            values[item[2:]] = next(it)
        except StopIteration:
            raise ValueError(f"{item} without a value") from None
    return values


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    verbs = {"calibrate": ("subject", "mutants", "command", "out", "repo", "timeout"),
             "sidebyside": ("subject", "mutants", "old", "new", "out", "repo", "timeout")}
    if not argv or argv[0] not in verbs:
        return refuse("arguments", "calibrate | sidebyside", argv[0] if argv else "nothing",
                      "name one verb with its --subject, --mutants and command options")
    verb = argv[0]
    try:
        values = _parse(argv[1:], verbs[verb])
    except ValueError as exc:
        return refuse("arguments", f"the options of {verb}: " + ", ".join("--" + n for n in verbs[verb]),
                      str(exc), "drop or correct it")
    required = ("subject", "mutants", "command") if verb == "calibrate" else ("subject", "mutants", "old", "new", "out")
    missing = [n for n in required if n not in values]
    if missing:
        return refuse("arguments", "every required option of " + verb, "missing " + ", ".join("--" + m for m in missing),
                      "pass them")
    repo = values.get("repo", ".")
    timeout = float(values.get("timeout", COMMAND_TIMEOUT))
    if not (Path(repo) / values["subject"]).is_file():
        return refuse("subject", "a file under the repository", values["subject"], "name the subject the mutants edit")
    try:
        if verb == "calibrate":
            report = calibrate(values["subject"], values["mutants"], values["command"], repo=repo, timeout=timeout)
        else:
            report = sidebyside(values["subject"], values["mutants"], values["old"], values["new"], repo=repo, timeout=timeout)
    except AnchorMissing as exc:
        return refuse("anchor", "every mutant's anchor matching its subject exactly once", str(exc),
                      "re-measure the anchor against the subject at this commit")
    except PlantFailed as exc:
        return refuse("repository", "a git repository at --repo whose HEAD archives", str(exc).replace("\n", " "),
                      "run from the repository root, or pass --repo")
    text = json.dumps(report, indent=1, sort_keys=True) + "\n"
    if values.get("out"):
        out = Path(values["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
