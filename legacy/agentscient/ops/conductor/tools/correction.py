"""Refuse correction dispatches whose findings are not disposed and cited."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import sqlite3
import sys
from pathlib import Path

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from the launcher, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import Refusal, emit, refuse, state_dir
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

ROOT = Path(__file__).resolve().parents[3]
REVIEW_JOBS = (
    "check-tests",
    "security-audit",
    "adversarial-review",
    "adjudicate",
    "sweep",
)
JOBS = REVIEW_JOBS + ("verify", "author-tests", "plan", "implement")
RECORD_ID = re.compile(
    r"^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}$"
)
ANSWER_KEY = re.compile(
    r"^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}#[1-9][0-9]*$"
)
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _module(path):
    spec = importlib.util.spec_from_file_location("correction_findings", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FINDINGS = _module(Path(__file__).with_name("findings.py"))


def _read_record(record_id, records):
    records = Path(records).resolve()
    if RECORD_ID.fullmatch(record_id) is None:
        refuse(
            "follows", f"a readable record for {record_id} under {records}",
            "a malformed record id", "name a matching record in .dev/records/dispatches",
        )
    path = records / f"{record_id}.json"
    resolved = path.resolve()
    try:
        resolved.relative_to(records)
    except ValueError:
        refuse(
            "follows", f"a readable record for {record_id} under {records}",
            f"{path} escapes the records directory",
            "name a matching record in .dev/records/dispatches",
        )
    try:
        record = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        refuse(
            "follows", f"a readable record for {record_id} under {records}",
            f"none ({error})", "name a matching record in .dev/records/dispatches",
        )
    if record.get("id") != record_id:
        refuse(
            "follows", f"record {record_id} with its own id equal to {record_id}",
            f"record id {record.get('id')!r}",
            "name a matching record in .dev/records/dispatches",
        )
    if record.get("job") not in JOBS:
        refuse(
            "follows", f"record {record_id} naming a registry job",
            f"job {record.get('job')!r}",
            "name a matching record in .dev/records/dispatches",
        )
    return record


def _input_record_id(path, jobs_root):
    jobs_root = Path(jobs_root).absolute()
    candidate = Path(path)
    candidate = (candidate if candidate.is_absolute() else Path.cwd() / candidate)
    # The shape is read from the path as given, collapsed lexically: a
    # `..` component must not reclassify a review artifact under another
    # record's out/ (hand review of 7b7979b, 2026-09-11). No symlink is
    # resolved here; the launcher refuses symlinked components itself.
    candidate = Path(os.path.normpath(str(candidate)))
    try:
        relative = candidate.relative_to(jobs_root)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 3 or parts[1] != "out":
        return None
    return parts[0]


def _scope_path(inputs, root):
    return (
        FINDINGS.work_order_scope(inputs, root)
        or ".dev/ladder-inputs/<scope path>"
    )


def _disposition_command(key, scope_path, item="1"):
    evidence = f"contract item {item} of {scope_path}: correction"
    return " ".join((
        "python3 ops/conductor/tools/findings.py disposition",
        shlex.quote(key),
        shlex.quote("fixed"),
        shlex.quote(evidence),
    ))


def _expected(at):
    return (
        "every finding answered by this correction disposed with a "
        f"contract citation before {at}"
    )


def _correction_refusal(found, needed, at):
    refuse("correction", _expected(at), found, needed)


def _answer_row(key, at, root, scope_path, review_ids):
    store = FINDINGS.database_path()
    command = _disposition_command(key, scope_path)
    if not store.is_file():
        _correction_refusal(
            f"key {key} absent from the findings store {store} (no store)",
            "python3 ops/conductor/tools/findings.py import, then " + command,
            at,
        )
    try:
        con = instrument.connect_store(store)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT key, record, disposition, evidence, disposed_at, claim "
                "FROM findings WHERE key = ?",
                (key,),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error as error:
        _correction_refusal(
            f"key {key} absent from the findings store {store} ({error})",
            "python3 ops/conductor/tools/findings.py import, then " + command,
            at,
        )
    if row is None:
        _correction_refusal(
            f"key {key} absent from the findings store {store}",
            "python3 ops/conductor/tools/findings.py import, then " + command,
            at,
        )
    if row["record"] not in review_ids:
        expected_id = next(iter(review_ids))
        expected_key = f"{expected_id}#1"
        _correction_refusal(
            f"key {key} does not belong to a review followed by this correction",
            "pass --answers keys belonging to the followed review, such as "
            f"{shlex.quote(expected_key)}, disposed by "
            f"{_disposition_command(expected_key, scope_path)}",
            at,
        )
    claim_citation = FINDINGS.parse_citation(row["claim"])
    finding_citation = None
    if claim_citation is not None:
        claim_holds, _reason = FINDINGS.citation_holds(
            row["claim"], root, scope_path
        )
        if claim_holds:
            finding_citation = {
                "item": claim_citation.group("item"),
                "path": claim_citation.group("path"),
            }
            command = _disposition_command(
                key, scope_path, claim_citation.group("item")
            )
    if row["disposition"] == "open":
        _correction_refusal(f"key {key} still open", command, at)
    holds, reason = FINDINGS.citation_holds(row["evidence"], root, scope_path)
    if not holds:
        _correction_refusal(
            f"key {key} disposed {row['disposition']} without a contract "
            f"citation ({reason})",
            command,
            at,
        )
    disposed_at = row["disposed_at"] or "unset"
    if disposed_at >= at:
        if disposed_at == at:
            _correction_refusal(
                f"key {key} disposed at {disposed_at}, not before the mint "
                f"attempt at {at}",
                "wait one second and retry",
                at,
            )
        _correction_refusal(
            f"key {key} disposed at {disposed_at}, after the mint attempt at {at}",
            command,
            at,
        )
    citation = FINDINGS.parse_citation(row["evidence"])
    answer = {
        "key": key,
        "disposition": row["disposition"],
        "disposed_at": disposed_at,
        "citation": {
            "item": citation.group("item"),
            "path": citation.group("path"),
        },
    }
    if finding_citation is not None:
        answer["finding_citation"] = finding_citation
    return answer


def check(role, follows, inputs, answers, at, records, jobs_root, root, job=None):
    if role != "write":
        return {"correction": False, "answers": []}
    if any(ANSWER_KEY.fullmatch(key) is None for key in answers):
        refuse(
            "arguments", "--answers keys matching the declared finding-key shape",
            "a malformed --answers key", "pass only a valid <record>#<n> key",
        )

    reviewed = []
    for record_id in follows:
        record = _read_record(record_id, records)
        if record["job"] in REVIEW_JOBS:
            reviewed.append((record_id, record["job"]))
    for value in inputs:
        record_id = _input_record_id(value, jobs_root)
        if record_id is None:
            continue
        record = _read_record(record_id, records)
        if record["job"] in REVIEW_JOBS:
            reviewed.append((record_id, record["job"]))

    if not reviewed:
        if answers:
            refuse(
                "arguments", "no unverified answers on a non-correction write job",
                "--answers on a write job outside the correction shape",
                "remove --answers",
            )
        return {"correction": False, "answers": []}

    scope_path = _scope_path(inputs, root)
    if not answers:
        record_id, review_job = reviewed[0]
        key = f"{record_id}#1"
        needed = (
            f"pass --answers {shlex.quote(key)} for each finding answered, "
            f"each disposed by {_disposition_command(key, scope_path)}"
        )
        _correction_refusal(
            f"no --answers key named for a correction following {record_id} "
            f"({review_job})",
            needed,
            at,
        )
    review_ids = tuple(dict.fromkeys(
        record_id for record_id, _review_job in reviewed
    ))
    return {
        "correction": True,
        "answers": [
            _answer_row(key, at, root, scope_path, review_ids) for key in answers
        ],
    }


def _parse(argv):
    if not argv or argv[0] != "check":
        refuse("arguments", "the check verb", argv[0] if argv else "no verb", "name check")
    values = {"follows": [], "inputs": [], "answers": []}
    scalar = {
        "--job": "job",
        "--role": "role",
        "--at": "at",
        "--records": "records",
        "--jobs-root": "jobs_root",
        "--root": "root",
    }
    repeated = {
        "--follows": "follows",
        "--input": "inputs",
        "--answers": "answers",
    }
    index = 1
    while index < len(argv):
        option = argv[index]
        if option not in scalar and option not in repeated:
            refuse("arguments", "a documented check option", option, "remove or correct it")
        if index + 1 >= len(argv):
            refuse("arguments", f"a value after {option}", "nothing", "supply the value")
        value = argv[index + 1]
        if option in repeated:
            values[repeated[option]].append(value)
        else:
            name = scalar[option]
            if name in values:
                refuse("arguments", f"one {option}", "more than one", "pass it once")
            values[name] = value
        index += 2

    missing = [name for name in ("job", "role", "at") if not values.get(name)]
    if missing:
        refuse(
            "arguments", "--job JOB --role write|read --at STAMP",
            "missing " + ", ".join(missing), "supply every required option",
        )
    if values["job"] not in JOBS or values["role"] not in ("read", "write"):
        refuse(
            "arguments", "a registry job and role write or read",
            "an unknown job or role", "name their declared values",
        )
    if STAMP.fullmatch(values["at"]) is None:
        refuse(
            "arguments", "--at in YYYY-MM-DDTHH:MM:SSZ form",
            "a malformed timestamp", "pass the mint-attempt timestamp",
        )
    root = Path(values.get("root", Path.cwd()))
    values.setdefault("root", str(root))
    values.setdefault("records", str(root / ".dev" / "records" / "dispatches"))
    if "jobs_root" not in values:
        explicit = os.environ.get("DISPATCH_JOBS")
        values["jobs_root"] = explicit or str(state_dir() / "dispatch")
    return values


def main(argv):
    try:
        values = _parse(argv[1:])
        result = check(**values)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Refusal as error:
        return emit(error)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
