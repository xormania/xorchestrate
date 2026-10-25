#!/usr/bin/env python3
"""probes — deterministic hand-over: run, ask and grade the probe corpus.

Contract: ops/conductor/probes-lever.md (D-OPS-11, ratified 2026-09-01);
inherits ops/conductor/instrument-conventions.md. Every verb is a read:
no store is touched, no ref moves, no identity is required. subprocess
is used only to execute corpus row commands, without a shell, from the
repository root, with WF_AGENT stripped, under a bounded timeout.

Built 2026-09-12 under hand grant 157 (owner: "it has to be made
impossible"): the lever page had named this instrument for eleven days
while 38 corpus rows sat with nothing to run them.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, emit, refuse
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

CORPUS = Path("ops/conductor/handoff/probes")
GIT = shutil.which("git") or "git"
KINDS = ("serve", "absent", "nearmiss")
LANES = ("register", "ledger", "ideas", "git", "forge", "aar", "record",
         "seat", "driver", "spend", "none")
GIT_READS = ("grep", "log", "show", "rev-parse", "rev-list", "merge-base",
             "for-each-ref", "ls-files")
GH_READS = (("pr", "list"), ("pr", "view"), ("pr", "checks"))
SIBLING = re.compile(r"^ops/conductor/tools/[A-Za-z0-9_]+\.py$")
SHELL_TOKENS = ("|", "||", "&&", ";", ">", ">>", "<", "<<", "&", "2>", "2>&1")
# rg options that spawn a process per file (`--pre COMMAND` searches the
# output of `COMMAND PATH`; `-z` runs a decompressor): a row carrying one
# is not a read (Codex review of PR #158, 2026-09-12).
RG_SPAWNING = ("--pre", "--pre-glob", "--search-zip")
RG_SHORT_SPAWNING = "z"
RG_SHORT_WITH_VALUE = "efgtTmABCEMrjd"
DEFAULT_TIMEOUT = 60.0
STATUSES = ("SERVED", "ABSENT")


def repo_root() -> Path:
    try:
        out = subprocess.run([GIT, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        refuse("corpus", "a repository root below the working directory",
               "none", "run from inside the repository")
    return Path(out.stdout.strip())


def short_sha(root: Path) -> str:
    try:
        out = subprocess.run([GIT, "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def rg_reads_only(options: list[str]) -> bool:
    """False when an rg option would spawn a process per file."""
    for option in options:
        if option == "--":
            return True
        if not option.startswith("-") or option == "-":
            continue
        if option.startswith("--"):
            if option.split("=", 1)[0] in RG_SPAWNING:
                return False
            continue
        for letter in option[1:]:
            if letter in RG_SHORT_SPAWNING:
                return False
            if letter in RG_SHORT_WITH_VALUE:
                break
    return True


def allowlisted(tokens: list[str]) -> bool:
    if not tokens or any(t in SHELL_TOKENS for t in tokens):
        return False
    head = tokens[0]
    if head == "git":
        return len(tokens) > 1 and tokens[1] in GIT_READS
    if head == "gh":
        return len(tokens) > 2 and (tokens[1], tokens[2]) in GH_READS
    if head == "rg":
        return rg_reads_only(tokens[1:])
    if head == "python3":
        return len(tokens) > 1 and bool(SIBLING.match(tokens[1]))
    return False


def _text(row: dict, key: str, where: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        refuse("corpus", f"a non-empty string `{key}` on row {where}",
               repr(value) if key in row else "missing", "fix the row")
    return value


def load_corpus(root: Path) -> list[dict]:
    files = sorted((root / CORPUS).glob("*.jsonl")) if (root / CORPUS).is_dir() else []
    if not files:
        refuse("corpus", f"at least one corpus file under {CORPUS}/", "none",
               "land a probes file before running")
    rows: list[dict] = []
    seen: set[str] = set()
    for path in files:
        rel = path.relative_to(root)
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            where = f"{rel}:{n}"
            if not line.strip():
                refuse("corpus", "one JSON object per line", f"blank line at {where}",
                       "remove the blank line")
            try:
                row = json.loads(line)
            except ValueError:
                refuse("corpus", "one JSON object per line", f"non-JSON at {where}",
                       "fix the line")
            if not isinstance(row, dict):
                refuse("corpus", "a JSON object", f"{type(row).__name__} at {where}", "fix the line")
            rid = _text(row, "id", where)
            if not rid.startswith("P-"):
                refuse("corpus", "an id with the `P-` prefix", f"{rid!r} at {where}", "rename the id")
            if rid in seen:
                refuse("corpus", "ids unique across the corpus", f"duplicate {rid} at {where}",
                       "rename one of them")
            seen.add(rid)
            kind = _text(row, "kind", where)
            if kind not in KINDS:
                refuse("corpus", f"kind in {KINDS}", f"{kind!r} at {where}", "fix the kind")
            _text(row, "question", where)
            lane = _text(row, "lane", where)
            if lane not in LANES:
                refuse("corpus", f"lane in {LANES}", f"{lane!r} at {where}", "fix the lane")
            if kind == "absent":
                command = _text(row, "absent", where)
            else:
                command = _text(row, "serve", where)
                expect = _text(row, "expect", where)
                if "\n" in expect:
                    # Pitfall 30: an expect that pins two entries of a sorted
                    # list as adjacent breaks when any session inserts a
                    # neighbour; a row pins membership, one line.
                    refuse("corpus", "an `expect` on one line (pin membership, one entry per row)",
                           f"an expect spanning {expect.count(chr(10)) + 1} lines at {where} ({rid})",
                           "split it, one row per line it pins")
                if kind == "nearmiss":
                    _text(row, "premise_flaw", where)
            try:
                tokens = shlex.split(command)
            except ValueError as exc:
                refuse("corpus", "a command string that splits", f"{exc} at {where} ({rid})",
                       "fix the command")
            if not allowlisted(tokens):
                refuse("corpus",
                       "a row command from the read allowlist (git reads, gh pr reads, "
                       "rg without an option that spawns a process (--pre, --pre-glob, "
                       "-z/--search-zip), python3 ops/conductor/tools/<name>.py; no pipes)",
                       f"{command!r} at {where} ({rid})", "rewrite the row as an allowlisted read")
            rows.append({"id": rid, "kind": kind, "lane": lane, "question": row["question"],
                         "expect": row.get("expect"), "tokens": tokens})
    return rows


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def run_row(root: Path, row: dict, env: dict, timeout: float) -> tuple[str, str]:
    try:
        proc = subprocess.run(row["tokens"], cwd=root, env=env, capture_output=True,
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return "UNREACHABLE", f"timeout after {timeout:g}s"
    except OSError as exc:
        return "UNREACHABLE", f"spawn failure: {exc}"
    out, err, code = _decode(proc.stdout), _decode(proc.stderr), proc.returncode
    first_out = out.splitlines()[0] if out.splitlines() else ""
    first_err = err.splitlines()[0] if err.splitlines() else ""
    if row["kind"] == "absent":
        if err or code > 1 or code < 0:
            return "UNREACHABLE", f"exit {code}; stderr: {first_err}"
        if out:
            return "FAIL", f"stdout: {first_out}"
        return "PASS", ""
    if code != 0:
        return "UNREACHABLE", f"exit {code}; stderr: {first_err}"
    if row["expect"] in out:
        return "PASS", ""
    return "FAIL", "expected substring absent from stdout"


def report(root: Path, results: list[tuple[dict, str, str]]) -> int:
    rows = [{"id": r["id"], "kind": r["kind"], "lane": r["lane"], "result": res, "detail": det}
            for r, res, det in results]
    counts = {"pass": sum(r["result"] == "PASS" for r in rows),
              "fail": sum(r["result"] == "FAIL" for r in rows),
              "unreachable": sum(r["result"] == "UNREACHABLE" for r in rows)}
    sys.stdout.write(json.dumps({"commit": short_sha(root), "rows": rows, "counts": counts},
                                ensure_ascii=False) + "\n")
    return 0 if counts["fail"] == 0 and counts["unreachable"] == 0 else 1


def verb_run(root: Path) -> int:
    rows = load_corpus(root)
    env = {k: v for k, v in os.environ.items() if k != "WF_AGENT"}
    try:
        timeout = float(os.environ.get("PROBES_TIMEOUT", DEFAULT_TIMEOUT))
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    results = [(row, *run_row(root, row, env, timeout)) for row in rows]
    return report(root, results)


def verb_ask(root: Path) -> int:
    for row in load_corpus(root):
        sys.stdout.write(json.dumps({"id": row["id"], "question": row["question"]},
                                    ensure_ascii=False) + "\n")
    return 0


def load_answers(path: Path, known: set[str]) -> dict[str, dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        refuse("answers", "a readable answers JSONL file", f"{path}: {exc.strerror or exc}",
               "pass the answers file arm B wrote")
    answers: dict[str, dict] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            refuse("answers", "one JSON object per line", f"non-JSON at line {n}", "fix the line")
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            refuse("answers", "an object with a string id", f"line {n}", "fix the line")
        rid = row["id"]
        if rid not in known:
            refuse("answers", "ids from the corpus", f"unknown {rid} at line {n}", "drop the row")
        if rid in answers:
            refuse("answers", "one answer per id", f"duplicate {rid} at line {n}", "drop one")
        status = row.get("status")
        if status not in STATUSES:
            refuse("answers", f"status in {STATUSES}", f"{status!r} at line {n}", "fix the status")
        if status == "SERVED" and not isinstance(row.get("answer"), str):
            refuse("answers", "an `answer` string beside SERVED", f"missing at line {n} ({rid})",
                   "add the answer")
        answers[rid] = row
    return answers


def grade_row(row: dict, answer: dict | None) -> tuple[str, str]:
    if answer is None:
        return "FAIL", "unanswered"
    status, text = answer["status"], answer.get("answer") or ""
    if row["kind"] == "absent":
        return ("PASS", "") if status == "ABSENT" else ("FAIL", "confabulation: SERVED on an absent row")
    if status != "SERVED":
        return "FAIL", "ABSENT on a row the record serves"
    if row["expect"] in text:
        return "PASS", ""
    if row["kind"] == "nearmiss":
        return "FAIL", "the corrective fact is missing from the answer"
    return "FAIL", "expected substring absent from the answer"


def verb_grade(root: Path, file: str) -> int:
    rows = load_corpus(root)
    answers = load_answers(Path(file), {r["id"] for r in rows})
    results = [(row, *grade_row(row, answers.get(row["id"]))) for row in rows]
    return report(root, results)


def main(argv: list[str]) -> int:
    try:
        if argv == ["run"]:
            return verb_run(repo_root())
        if argv == ["ask"]:
            return verb_ask(repo_root())
        if len(argv) == 2 and argv[0] == "grade":
            return verb_grade(repo_root(), argv[1])
        refuse("arguments", "run | ask | grade FILE", " ".join(argv) or "nothing",
               "choose one verb with exactly its arguments")
    except Refusal as refusal:
        return emit(refusal)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
