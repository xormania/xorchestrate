"""Read-role prompts ask for no undeclared file (item 31, contract item 5 as amended).

A read-role job's prompt may not carry a positive instruction to write a
path unless that path is one of the job's declared outputs, the file rows
of its jobs.json outputs array rendered under out. Recognition is by
instruction shape: a write verb and a path-shaped token in the same
sentence with no negation before the token, not a list of forbidden words.
"Write no file", "Do not write to out/X.md" and a bare "out/" do not fire;
"Write the report to {out}/SKEPTIC.md" fires unless SKEPTIC.md is declared;
"Please write /tmp/result.md" and "Copy the report to /tmp/result.md" fire.

CLI: python3 write_path.py [--jobs PATH] [JOB]: checks one or every read-role
job; exit 0 silent, or the role refusal on the wire (one stderr line, exit
2, stdout empty); an unknown job refuses job.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

WRITE_VERBS = ("write", "save", "create", "store", "emit", "put", "append", "dump", "copy", "move")
NEGATIONS = ("not", "no", "never", "don't", "without", "nothing")
SUFFIXES = ("md", "txt", "json", "py", "log", "yaml", "yml", "csv", "html", "sql")
_SUFFIX = re.compile(r"\.(" + "|".join(SUFFIXES) + r")\b", re.IGNORECASE)
_PATH = re.compile(
    r"\{out\}[\w./{}-]*"
    r"|[\w.{}-]*/[\w./{}-]+"
    r"|`[^`]+\.(?:" + "|".join(SUFFIXES) + r")`"
    r"|\b[\w-]+\.(?:" + "|".join(SUFFIXES) + r")\b"
)
_VERB = re.compile(r"\b(?:" + "|".join(WRITE_VERBS) + r")\b", re.IGNORECASE)
_SPLIT = re.compile(r"[.;!?\n]+")
_MARK = "·"


def declared_paths(outputs) -> frozenset[str]:
    """The paths a job's outputs array declares, as the prompt may spell them."""
    names: set[str] = set()
    for row in outputs or []:
        if isinstance(row, dict) and row.get("kind") == "file" and isinstance(row.get("path"), str):
            path = row["path"].strip().strip("/")
            if path:
                names.update({path, "{out}/" + path, "out/" + path})
    return frozenset(names)


def _is_declared(token: str, declared: frozenset[str]) -> bool:
    plain = token.strip("`").rstrip(".,;:")
    if plain in declared:
        return True
    return any(plain.endswith("/" + path) for path in declared if "/" not in path.strip("{}"))


def _negated(sentence: str, verb_end: int, path_start: int, verb_start: int) -> bool:
    between = sentence[verb_end:path_start].lower()
    before = sentence[max(0, verb_start - 24):verb_start].lower()
    for token in NEGATIONS:
        pattern = r"\b" + re.escape(token) + r"\b"
        if re.search(pattern, between) or re.search(pattern, before):
            return True
    return "do not" in before or "do not" in between


def write_instruction(text: str, declared=()) -> str | None:
    """The first sentence that positively instructs a write to an undeclared path, or None."""
    declared = frozenset(declared)
    protected = _SUFFIX.sub(_MARK + r"\1", text or "")
    for raw in _SPLIT.split(protected):
        sentence = raw.replace(_MARK, ".").strip()
        if not sentence:
            continue
        for verb in _VERB.finditer(sentence):
            for path in _PATH.finditer(sentence, verb.end()):
                if _is_declared(path.group(0), declared):
                    continue
                if _negated(sentence, verb.end(), path.start(), verb.start()):
                    continue
                return sentence
    return None


def check_job(name: str, job: dict) -> str | None:
    """The offending sentence of a read-role job, or None."""
    if not isinstance(job, dict) or job.get("role") != "read":
        return None
    return write_instruction(job.get("prompt") or "", declared_paths(job.get("outputs")))


def refusal_line(name: str, sentence: str) -> str:
    return (
        "role: expected a read-role prompt with no instruction to write an undeclared path; "
        f'found job {name}: "{sentence}"; '
        "needed drop the sentence, declare the output in the job's outputs array, or declare the job a write role"
    )


def main(argv: list[str]) -> int:
    jobs_path = Path(__file__).with_name("jobs.json")
    names: list[str] = []
    items = list(argv)
    while items:
        item = items.pop(0)
        if item == "--jobs":
            if not items:
                print("arguments: expected --jobs PATH; found --jobs alone; needed the jobs file", file=sys.stderr)
                return 2
            jobs_path = Path(items.pop(0))
        else:
            names.append(item)
    try:
        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"job: expected a readable jobs.json at {jobs_path}; found {error}; needed restore it", file=sys.stderr)
        return 2
    if names:
        for name in names:
            if name not in jobs:
                print(f"job: expected a job in {jobs_path}; found {name}; needed name a declared job", file=sys.stderr)
                return 2
    for name in names or sorted(jobs):
        sentence = check_job(name, jobs[name])
        if sentence is not None:
            print(refusal_line(name, sentence), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
