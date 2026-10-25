#!/usr/bin/env python3
"""Run the declared audit ladder in a branch's owning worktree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings. `Refused` is this driver's name for the shared
# refusal class; its suites and Stopped's callers keep the name.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import IDENTITY, now, refuse
    from instrument import Refusal as Refused
    from instrument import conf as conf_value
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None
RECORD_ID = re.compile(r"^record:\s+(\S+)\s*$", re.MULTILINE)
LANDED = re.compile(r"^LANDED\t([0-9a-f]{40})\t", re.MULTILINE)
RECORD_ID_SHAPE = re.compile(r"^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}$")
MARKER_MIN = 24
DISPATCH_STAGES = (
    ("tests", "author-tests", "grok", "tests"),
    ("check-tests", "check-tests", "codex", "check-tests"),
    ("tests-fix", "author-tests", "grok", "tests"),
    ("code", "implement", "codex", "code"),
    ("security", "security-audit", "codex", "review"),
    ("adversarial", "adversarial-review", "grok", "review"),
    ("fix-tests", "author-tests", "grok", "tests"),
    ("fix-code", "implement", "codex", "code"),
    ("security-re", "security-audit", "codex", "review"),
)
REVIEW_STAGES = frozenset(
    {"check-tests", "security", "adversarial", "security-re"}
)
WITHHELD_STAGES = frozenset(
    {"tests", "check-tests", "tests-fix", "fix-tests"}
)
# The stage set is chosen per work order (scope-stage-set-20260912, owner
# 2026-09-12): the owner's tracked table maps a risk class to the stages
# above it runs; the floor lives in load_stage_sets, not in the table.
STAGE_SETS_REL = Path("ops") / "conductor" / "stage-sets.json"
DEFAULT_STAGE_SET = "lane"
STAGE_ORDER = tuple(row[0] for row in DISPATCH_STAGES)
FIX_ROUND = ("fix-tests", "fix-code")


def load_stage_sets(path: Path) -> dict[str, list[str]]:
    """The owner's table, validated; every failure refuses `stage-set`."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        refuse("stage-set", f"a readable stage-set table at {path}", str(error),
               "restore ops/conductor/stage-sets.json")
    except json.JSONDecodeError as error:
        refuse("stage-set", f"valid JSON in {path}", str(error), "repair the table")
    if not isinstance(raw, dict) or not raw:
        refuse("stage-set", "an object of class name to stage list", repr(raw)[:80],
               "declare at least one class")
    table: dict[str, list[str]] = {}
    for name, stages in raw.items():
        if (not isinstance(name, str) or not name.strip() or not isinstance(stages, list)
                or not all(isinstance(item, str) for item in stages)):
            refuse("stage-set", f"class {name!r} as a list of stage names", repr(stages)[:80],
                   "repair the row")
        unknown = [item for item in stages if item not in STAGE_ORDER]
        if unknown:
            refuse("stage-set", f"stages of class {name} from " + ", ".join(STAGE_ORDER),
                   ", ".join(unknown), "name driver stages only")
        if len(set(stages)) != len(stages):
            refuse("stage-set", f"each stage once in class {name}", ", ".join(stages),
                   "drop the duplicate")
        chosen = [item for item in STAGE_ORDER if item in stages]
        for needed in ("tests", "check-tests"):
            if needed not in chosen:
                refuse("stage-set", f"class {name} keeping {needed} (the floor)",
                       ", ".join(chosen), f"add {needed}")
        if "code" in chosen and not ({"security", "adversarial"} & set(chosen)):
            refuse("stage-set", f"class {name} reviewing code (the floor)", ", ".join(chosen),
                   "add security or adversarial")
        fix = [item for item in FIX_ROUND if item in chosen]
        if len(fix) == 1:
            refuse("stage-set", f"class {name} naming the fix round whole", ", ".join(chosen),
                   "name fix-tests and fix-code together or neither")
        if "security-re" in chosen and not fix:
            refuse("stage-set", f"class {name} naming security-re only with the fix round",
                   ", ".join(chosen), "add the fix round or drop security-re")
        table[name] = chosen
    return table


def choose_stage_set(
    table: dict[str, list[str]],
    slices: list[dict],
    override: tuple[str, str] | None,
    agent: str,
) -> dict:
    """The set this ladder runs: the override, else the plan's class, else the declared default."""
    at = utc_stamp()
    if override is not None:
        name, because = override
        if name not in table:
            refuse("stage-set", "a class of the table: " + ", ".join(table), name,
                   "choose a declared class or add it to the table")
        return {"name": name, "stages": list(table[name]), "source": "override",
                "because": because, "by": agent, "at": at}
    classes = sorted({item["class"] for item in slices if item.get("class")})
    if len(classes) > 1:
        refuse("plan", "one stage-set class across the plan's slice lines", ", ".join(classes),
               "one class per ladder")
    if classes:
        name = classes[0]
        if name not in table:
            refuse("plan", "a slice class of the table: " + ", ".join(table), name,
                   "choose a declared class or add it to the table")
        return {"name": name, "stages": list(table[name]), "source": "plan", "at": at}
    if DEFAULT_STAGE_SET not in table:
        refuse("stage-set", f"the declared default class {DEFAULT_STAGE_SET} in the table",
               ", ".join(table), "declare it")
    return {"name": DEFAULT_STAGE_SET, "stages": list(table[DEFAULT_STAGE_SET]),
            "source": "default", "at": at}
CONF_KEYS = (
    "LANE_NAME",
    "TRUNK_BRANCH",
    "REPO_SLUG",
    "CHECK_NAMES",
    "LANE_AUTHOR",
    "SCOPE_CAP",
    "WALL_DEFAULT",
    "WORKTREE_ROOT",
    "ADDRESS_GROK",
    "ADDRESS_CODEX",
    "ADDRESS_CLAUDE",
)


class Stopped(Exception):
    """A mid-act stop after the ladder has a durable record."""

    def __init__(
        self,
        stage: str,
        record_id: str = "none",
        reason: str | None = None,
        verdict: str | None = None,
        detail: str | None = None,
        exit_code: int = 3,
        keys: list[str] | None = None,
        wire: str | None = None,
    ) -> None:
        self.wire = wire
        self.stage = stage
        self.record_id = record_id
        self.reason = reason
        self.verdict = verdict
        self.detail = detail
        self.exit_code = exit_code
        self.keys = keys
        super().__init__(self.line())

    def line(self) -> str:
        fields = [f"stopped stage={self.stage}", f"record={self.record_id}"]
        if self.reason:
            fields.append(f"reason={self.reason}")
        if self.verdict:
            fields.append(f"verdict={self.verdict}")
        return " ".join(fields)


@dataclass
class Pending:
    """A detached dispatch waiting to be consumed."""

    name: str
    record_id: str
    record_path: Path
    model: str
    effort: str
    base_sha: str
    started: float


RESUME_FORM = "resume LADDER_ID [--rescope] [--retry STAGE --because TEXT]"


def resume_options(args: list[str]) -> tuple[bool, tuple[str, str] | None]:
    """(rescope, retry) from ``resume LADDER_ID [--rescope] [--retry STAGE --because TEXT]``.

    LD-19: ``--retry`` names a stage at its two-attempt cap and ``--because``
    the reason one more attempt is honest (a driver-caused stop, not the
    worker's); both are recorded on the stage row when the attempt is minted.
    """
    if len(args) < 2:
        refuse("arguments", RESUME_FORM, " ".join(args), "pass the named record id")
    rescope = False
    stage: str | None = None
    because: str | None = None
    rest = list(args[2:])
    while rest:
        flag = rest.pop(0)
        if flag == "--rescope":
            rescope = True
        elif flag in ("--retry", "--because") and rest:
            value = rest.pop(0)
            if flag == "--retry":
                stage = value
            else:
                because = value
        else:
            refuse("arguments", RESUME_FORM, " ".join(args), "choose the documented resume form")
    if (stage is None) != (because is None):
        refuse("arguments", "--retry STAGE together with --because TEXT", " ".join(args),
               "name both the stage and the reason")
    if stage is None:
        return rescope, None
    if stage not in {row[0] for row in DISPATCH_STAGES}:
        refuse("arguments", "a dispatch stage for --retry", stage,
               "one of " + ", ".join(row[0] for row in DISPATCH_STAGES))
    if not because.strip():
        refuse("arguments", "a non-empty --because", repr(because), "say why one more attempt is honest")
    return rescope, (stage, because)


def configuration() -> dict[str, str]:
    """Read every driver-owned required key through the shared reader."""
    return {
        key: conf_value(key)
        for key in ("WORKTREE_ROOT", "ADDRESS_GROK", "ADDRESS_CODEX", "ADDRESS_CLAUDE")
    }


SEVERITIES = ("p1", "p2", "p3")
FINDING_FIELDS = ("severity", "where", "claim", "reproduce")
# The optional label (ops/devlane/task/envelope.py PRACTICAL_FIELDS): both or neither.
PRACTICAL_FIELDS = ("practical", "failing")


class EnvelopeRefused(Refused):
    """A malformed check-tests envelope; ``field`` is the dotted field named."""

    def __init__(self, field: str, expected: str, found: str, needed: str) -> None:
        self.field = field
        super().__init__("envelope", expected, found, needed)


def _short(value: object, limit: int = 120) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def validate_check_tests(envelope: object) -> None:
    """Item 31 items 2 and 3: the skeptic's findings on the wire, or EnvelopeRefused.

    Checked in this order: artifacts, findings is a list, each finding's
    fields, severity, where, claim, reproduce, counts, verdict.
    """
    if not isinstance(envelope, dict):
        raise EnvelopeRefused("envelope", "a check-tests envelope object", _short(envelope),
                              "an envelope on stdout")
    artifacts = envelope.get("artifacts")
    if artifacts != {}:
        raise EnvelopeRefused("artifacts", "artifacts {} on check-tests (no file is written)",
                              f"artifacts {_short(artifacts)}", "an envelope on stdout and no file")
    findings = envelope.get("findings")
    if not isinstance(findings, list):
        raise EnvelopeRefused("findings", "findings as a list of quotation pairs",
                              f"findings {_short(findings)}", "a list, empty when nothing was refuted")
    for index, finding in enumerate(findings):
        name = f"findings[{index}]"
        if not isinstance(finding, dict):
            raise EnvelopeRefused(name, f"{name} as an object with the fields " + ", ".join(FINDING_FIELDS),
                                  f"{name} {_short(finding)}", "a quotation pair")
        labelled = any(field in finding for field in PRACTICAL_FIELDS)
        fields = FINDING_FIELDS + PRACTICAL_FIELDS if labelled else FINDING_FIELDS
        extra = sorted(set(finding) - set(fields))
        missing = [field for field in fields if field not in finding]
        if extra or missing:
            raise EnvelopeRefused(name, f"{name} with exactly the fields " + ", ".join(FINDING_FIELDS)
                                  + ", and practical with failing or neither",
                                  f"{name} with extra {extra} and missing {missing}",
                                  "the four fields, with practical and failing both or neither, and no other")
        if finding["severity"] not in SEVERITIES:
            raise EnvelopeRefused(f"{name}.severity", f"{name}.severity one of " + ", ".join(SEVERITIES),
                                  f"{name}.severity {_short(finding['severity'])}", "p1, p2 or p3")
        where = finding["where"]
        if (not isinstance(where, str) or not where or any(ch.isspace() for ch in where)
                or not ("/" in where or "." in where)):
            raise EnvelopeRefused(f"{name}.where", f"{name}.where as the practiced location, no whitespace, a / or .",
                                  f"{name}.where {_short(where)}", "the test file and line")
        claim = finding["claim"]
        if not isinstance(claim, str) or not _quotation_pair(claim):
            raise EnvelopeRefused(f"{name}.claim",
                                  f"{name}.claim as declared (<location>): <words>; practiced: <evidence>; misses: <implementation>",
                                  f"{name}.claim {_short(claim)}",
                                  "both quotes, the declared location and the missed implementation")
        reproduce = finding["reproduce"]
        if not isinstance(reproduce, str) or not reproduce.strip():
            raise EnvelopeRefused(f"{name}.reproduce", f"{name}.reproduce as a command",
                                  f"{name}.reproduce {_short(reproduce)}",
                                  "a reproduce command (a finding without one is an opinion)")
        if labelled:
            _check_practical(name, finding["practical"], finding["failing"])
    tally = {key: sum(1 for finding in findings if finding["severity"] == key) for key in SEVERITIES}
    tally["opinions"] = 0
    if envelope.get("counts") != tally:
        raise EnvelopeRefused("counts", f"counts equal to the tally of findings {tally}",
                              f"counts {_short(envelope.get('counts'))}", "counts recomputed from findings")
    verdict = envelope.get("verdict")
    wanted = "changes" if findings else "approve"
    if verdict != wanted:
        raise EnvelopeRefused("verdict", f"verdict {wanted} with {len(findings)} finding(s)",
                              f"verdict {_short(verdict)}", "verdict changes exactly when findings is non-empty")


def _check_practical(name: str, practical: object, failing: object) -> None:
    """The label: yes names the failing invocation, no names none."""
    if practical not in ("yes", "no"):
        raise EnvelopeRefused(f"{name}.practical", f"{name}.practical yes or no",
                              f"{name}.practical {_short(practical)}",
                              "yes with the failing invocation, or no")
    given = isinstance(failing, str) and bool(failing.strip())
    if practical == "yes" and not given:
        raise EnvelopeRefused(f"{name}.failing", f"{name}.failing as the invocation that fails, on practical yes",
                              f"{name}.failing {_short(failing)}",
                              "the command someone will actually run, showing the failure")
    if practical == "no" and failing is not None:
        raise EnvelopeRefused(f"{name}.failing", f"{name}.failing null on practical no",
                              f"{name}.failing {_short(failing)}",
                              "null, or practical yes")


def _quotation_pair(claim: str) -> bool:
    head, sep, tail = claim.partition("; practiced: ")
    if not sep:
        return False
    practiced, sep2, misses = tail.rpartition("; misses: ")
    if not sep2:
        return False
    if not head.startswith("declared (") or "): " not in head:
        return False
    location, _, words = head[len("declared ("):].partition("): ")
    if not location.strip() or "(" in location or ")" in location:
        return False
    return all(part.strip() for part in (words, practiced, misses))


def check_envelope_source(raw: str) -> None:
    """The check-envelope verb: a dispatch record or a bare envelope as JSON."""
    if not raw.strip():
        raise EnvelopeRefused("envelope", "a check-tests dispatch record or envelope as JSON on stdin",
                              "empty stdin", "pipe the record JSON or name its path")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise EnvelopeRefused("envelope", "a check-tests dispatch record or envelope as JSON",
                              f"invalid JSON: {error}", "pipe the record JSON or name its path") from error
    envelope = data
    if isinstance(data, dict) and isinstance(data.get("result"), dict) and "envelope" in data["result"]:
        envelope = data["result"]["envelope"]
    validate_check_tests(envelope)


def run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("git") or "git"
    return run([executable, *args], cwd)


def git_out(cwd: Path, *args: str) -> str:
    result = git(cwd, *args)
    if result.returncode != 0:
        refuse(
            "worktree",
            "a readable git worktree",
            result.stderr.strip() or f"git exited {result.returncode}",
            "repair the repository metadata",
        )
    return result.stdout.strip()


def worktrees(cwd: Path) -> list[tuple[Path, str | None]]:
    result = git(cwd, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        refuse(
            "worktree",
            "git worktree metadata",
            result.stderr.strip() or f"git exited {result.returncode}",
            "invoke the driver from a checkout of the repository",
        )
    rows: list[tuple[Path, str | None]] = []
    path: Path | None = None
    branch: str | None = None
    for raw in [*result.stdout.splitlines(), ""]:
        if raw.startswith("worktree "):
            path = Path(raw.removeprefix("worktree ")).resolve()
        elif raw.startswith("branch refs/heads/"):
            branch = raw.removeprefix("branch refs/heads/")
        elif not raw and path is not None:
            rows.append((path, branch))
            path = None
            branch = None
    return rows


def branch_worktree(cwd: Path, branch: str, root_hint: str) -> Path:
    for path, checked_out in worktrees(cwd):
        if checked_out == branch:
            return path
    slug = branch.rsplit("/", 1)[-1]
    refuse(
        "worktree",
        f"branch {branch} checked out in a worktree",
        "none",
        f"run git worktree add {root_hint}/ag-{slug} {branch}",
    )


def record_worktree(cwd: Path, ladder_id: str) -> tuple[Path, dict]:
    relative = Path(".dev") / "records" / "ladders" / f"{ladder_id}.json"
    for path, _branch in worktrees(cwd):
        candidate = path / relative
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            refuse(
                "record",
                f"a valid ladder record {ladder_id}",
                str(error),
                "repair the named record",
            )
        if not isinstance(data, dict) or data.get("id") != ladder_id:
            refuse(
                "record",
                f"record id {ladder_id}",
                repr(data.get("id") if isinstance(data, dict) else data),
                "repair the named record",
            )
        return path, data
    refuse(
        "record",
        f"ladder record {ladder_id} in a repository worktree",
        "absent",
        "choose or repair the record",
    )


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc_stamp() -> str:
    return now().strftime("%Y%m%dT%H%M%SZ")


def full_identity(value: str, label: str) -> str:
    if IDENTITY.fullmatch(value) is None:
        refuse(
            "identity",
            f"{label} in the Name <address> form",
            value or "unset",
            f"supply {label} as Name <address>",
        )
    return value


def file_arg(path: str, label: str, *, required: bool = True) -> Path | None:
    candidate = Path(path).resolve()
    needed = f"supply a readable {label} file"
    if candidate.is_file():
        try:
            candidate.read_bytes()
        except OSError as error:
            refuse(label, f"a readable {label} file", str(error), needed)
        return candidate
    if required:
        refuse(label, f"a readable {label} file", str(candidate), needed)
    return None


def flag_value(argv: list[str], flag: str) -> str | None:
    try:
        index = argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def parse_dispatch_json(text: str) -> dict:
    for raw in reversed(text.splitlines()):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("argv"), list):
            return payload
    raise ValueError("dispatcher dry-run emitted no argv object")


def stage_spec(name: str) -> tuple[str, str, str]:
    base = "code" if name.startswith("code-") else name
    for stage, job, harness, flag in DISPATCH_STAGES:
        if stage == base:
            return job, harness, flag
    raise KeyError(name)


def parse_plan(plan: Path | None) -> tuple[list[dict], list[dict]]:
    buckets: list[dict] = []
    slices: list[dict] = []
    if plan is not None:
        for raw in plan.read_text(encoding="utf-8").splitlines():
            fields = raw.split("\t")
            if len(fields) >= 3 and fields[0] == "slice":
                slices.append({
                    "name": fields[1],
                    "declared": fields[2],
                    "class": fields[3].strip() if len(fields) >= 4 and fields[3].strip() else None,
                })
            elif len(fields) >= 4 and fields[0] == "bucket":
                buckets.append(
                    {
                        "name": fields[1],
                        "declared": fields[2],
                        "files": fields[3].split(),
                    }
                )
    for item in slices:
        if item["declared"] != "M":
            continue
        valid = len(buckets) == 2 and all(
            bucket["name"] and bucket["declared"] == "S"
            for bucket in buckets
        )
        if not valid:
            refuse(
                "plan",
                f"slice {item['name']} declared M with exactly two named S buckets",
                f"{len(buckets)} buckets in {plan}",
                "declare exactly two named S buckets",
            )
    if not buckets:
        buckets.append({"name": "scope", "declared": "S", "files": []})
    return buckets, slices


def recorded_file(
    worktree: Path, data: object, label: str, *, rescope: list | None = None
) -> Path:
    """Resolve and verify one file named by a durable ladder record.

    With ``rescope`` given, a digest mismatch is not a refusal: the entry
    is appended to ``rescope`` and the record's digest is updated to the
    bytes on disk. Without it, changed bytes refuse ``record``.
    """
    if not isinstance(data, dict):
        refuse("record", f"a recorded {label}", repr(data), "repair the record")
    raw_path = data.get("path")
    expected = data.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        refuse("record", f"a recorded {label} path", repr(raw_path), "repair the record")
    candidate = Path(raw_path).resolve()
    if not candidate.is_relative_to(worktree.resolve()):
        refuse(
            "record",
            f"the {label} path inside {worktree}",
            str(candidate),
            "repair the record with an in-worktree path",
        )
    if not candidate.is_file():
        refuse("record", f"a readable recorded {label}", str(candidate), "repair the record")
    actual = digest(candidate)
    if not isinstance(expected, str) or actual != expected:
        if rescope is None or not isinstance(expected, str):
            refuse(
                "record",
                f"the recorded {label} sha256 {expected}",
                actual,
                "restore the recorded bytes, or resume with --rescope to record the change",
            )
        rescope.append({"label": label, "path": str(candidate), "from": expected, "to": actual})
        data["sha256"] = actual
    return candidate


class Driver:
    """The stateful orchestration projection of the ladder contract."""

    def __init__(
        self,
        worktree: Path,
        branch: str,
        scope: Path,
        plan: Path | None,
        conf: dict[str, str],
        agent: str,
        record: dict | None = None,
        inputs: list[Path] | None = None,
        stage_set: tuple[str, str] | None = None,
    ) -> None:
        self.worktree = worktree
        self.branch = branch
        self.scope = scope
        # LD-19: one recorded extra attempt, set by resume --retry.
        self.retry: tuple[str, str] | None = None
        self.plan = plan
        self.conf = conf
        self.agent = agent
        self.start_inputs: list[Path] = list(inputs or [])
        self.buckets, self.slices = parse_plan(plan)
        self.stage_sets_path = worktree / STAGE_SETS_REL
        self.stage_sets = load_stage_sets(self.stage_sets_path)
        if record is not None and isinstance(record.get("stage_set"), dict):
            self.stage_set = record["stage_set"]
        else:
            self.stage_set = choose_stage_set(self.stage_sets, self.slices, stage_set, agent)
        self.record = record or self.new_record()
        self.record_path = (
            worktree
            / ".dev"
            / "records"
            / "ladders"
            / f"{self.record['id']}.json"
        )
        self.inputs_dir = self.record_path.with_suffix("")
        self.dispatch = worktree / "ops" / "bin" / "dispatch.sh"
        self.launch = worktree / "ops" / "devlane" / "dispatch" / "launch.py"
        self.land = worktree / "ops" / "conductor" / "tools" / "land.py"
        self.findings = worktree / "ops" / "conductor" / "tools" / "findings.py"
        self.gh = shutil.which("gh") or "gh"
        self.stage_by_name = {
            row["name"]: row for row in self.record.get("stages", [])
        }
        self.implementation_markers: list[str] = self.normalize_markers(
            self.record.get("implementation_markers") or []
        )
        self.correction_follows: list[str] = []
        self.correction_answers: list[str] = []
        # LD-23: one key per skeptic finding, named on tests-fix, which
        # the launcher classifies as a correction of the check-tests
        # record it follows.
        self.skeptic_answers: list[str] = []

    def new_record(self) -> dict:
        suffix = hashlib.sha256(
            f"{os.getpid()}:{now().timestamp()}".encode()
        ).hexdigest()[:6]
        ladder_id = f"{utc_stamp()}-ladder-{suffix}"
        base_sha = git_out(self.worktree, "rev-parse", "HEAD")
        stages = [
            {
                "name": name,
                "job": job,
                "harness": harness,
                "stage": flag,
                "condition": "declared stage condition not yet evaluated",
                "attempt": 0,
                "outcome": "pending",
            }
            for name, job, harness, flag in DISPATCH_STAGES
        ]
        for row in stages:
            if row["name"] not in self.stage_set["stages"]:
                row["outcome"] = "skipped"
                row["condition"] = f"not in stage set {self.stage_set['name']}"
        stages.append(
            {
                "name": "pr",
                "job": None,
                "harness": None,
                "stage": None,
                "condition": "audits approve",
                "attempt": 0,
                "outcome": "pending",
            }
        )
        return {
            "id": ladder_id,
            "lane": self.conf["LANE_NAME"],
            "branch": self.branch,
            "worktree": str(self.worktree),
            "base_sha": base_sha,
            "status": "running",
            "stopped": None,
            "pr": None,
            "scope": {"path": str(self.scope), "sha256": digest(self.scope)},
            "plan": (
                {"path": str(self.plan), "sha256": digest(self.plan)}
                if self.plan is not None
                else None
            ),
            "inputs": [
                {"path": str(path), "sha256": digest(path)}
                for path in self.start_inputs
            ],
            "conf": {
                "path": str(self.worktree / "ops" / "lane.conf"),
                "sha256": digest(self.worktree / "ops" / "lane.conf"),
            },
            "stage_sets": {
                "path": str(self.stage_sets_path),
                "sha256": digest(self.stage_sets_path),
            },
            "stage_set": self.stage_set,
            "stages": stages,
            "calibration": [],
            "implementation_markers": [],
        }

    def row(self, name: str) -> dict:
        if name not in self.stage_by_name:
            job, harness, flag = stage_spec(name)
            row = {
                "name": name,
                "job": job,
                "harness": harness,
                "stage": flag,
                "condition": "one declared S bucket",
                "attempt": 0,
                "outcome": "pending",
            }
            pr_index = next(
                (
                    index
                    for index, item in enumerate(self.record["stages"])
                    if item["name"] == "pr"
                ),
                len(self.record["stages"]),
            )
            self.record["stages"].insert(pr_index, row)
            self.stage_by_name[name] = row
        return self.stage_by_name[name]

    def commit(self, *extra: Path) -> None:
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.record["implementation_markers"] = self.implementation_markers
        self.record_path.write_text(
            json.dumps(self.record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        paths = [self.record_path, *extra]
        # Every stage input the driver wrote rides with the record: a resume
        # rewrites inputs for stages it then finds already done, and a stop
        # must leave the worktree clean for the landing lever (LD-11).
        if self.inputs_dir.is_dir():
            paths.extend(sorted(self.inputs_dir.iterdir()))
        relative = [str(path.relative_to(self.worktree)) for path in paths if path.exists()]
        added = git(self.worktree, "add", "--", *relative)
        if added.returncode != 0:
            raise Stopped("record", reason="commit", detail=added.stderr.strip())
        message = (
            f"ladder: record {self.record['id']}\n\n"
            "Source: generated: ops/conductor/tools/ladder.py\n"
            f"Co-Authored-By: {self.agent}\n"
        )
        committed = git(
            self.worktree,
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            message,
        )
        if committed.returncode != 0:
            raise Stopped("record", reason="commit", detail=committed.stderr.strip())

    def stop(self, stopped: Stopped) -> NoReturn:
        self.record["status"] = "stopped"
        self.record["stopped"] = {
            "stage": stopped.stage,
            "record": stopped.record_id,
            "reason": stopped.reason,
            "verdict": stopped.verdict,
        }
        if stopped.keys is not None:
            self.record["stopped"]["keys"] = stopped.keys
        row = self.row(stopped.stage)
        row["outcome"] = stopped.verdict or "stopped"
        if stopped.reason:
            row["reason"] = stopped.reason
        self.commit()
        raise stopped

    def audit_envelope_input(self, audit: str, record_path: Path) -> Path:
        """The audit's envelope alone, for a withheld stage.

        A whole audit record names the implementation record it follows
        and the landed head it reviewed, so handing it to the test author
        would breach custody by construction; the stage table promises the
        envelopes.
        """
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        envelope = ((payload.get("result") or {}).get("envelope")) or {}
        body = (
            f"{audit} audit envelope, record {record_path.stem}\n\n"
            + json.dumps(envelope, indent=2, sort_keys=True)
        )
        return self.make_input(f"{audit}-envelope", body)

    def make_input(self, name: str, body: str) -> Path:
        """Write one stage input under the inputs directory, or refuse `custody`.

        Item 31 item 4: a bare name, no symlinked component between the
        worktree and the inputs directory, no symlink or non-regular file at
        the target, O_NOFOLLOW on open, and a resolved path under the
        resolved inputs directory after the write.
        """
        if "/" in name or name in ("", ".", ".."):
            refuse("custody", "a bare input name", repr(name), "name the input without a path")
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        probe = self.worktree
        for part in self.inputs_dir.relative_to(self.worktree).parts:
            probe = probe / part
            if probe.is_symlink():
                refuse("custody", f"a regular directory at {probe}",
                       f"{probe} is a symlink to {os.readlink(probe)}", "remove the symlink and resume")
        path = self.inputs_dir / f"{name}.md"
        if path.is_symlink():
            refuse("custody", f"a regular file under {self.inputs_dir}",
                   f"{path} is a symlink to {os.readlink(path)}", "remove the symlink and resume")
        if path.exists() and not stat.S_ISREG(path.lstat().st_mode):
            refuse("custody", f"a regular file under {self.inputs_dir}",
                   f"{path} is not a regular file", "remove it and resume")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body.rstrip() + "\n")
        if not stat.S_ISREG(path.lstat().st_mode):
            refuse("custody", f"a regular file at {path}", "not a regular file after the write",
                   "remove it and resume")
        try:
            path.resolve().relative_to(self.inputs_dir.resolve())
        except ValueError:
            refuse("custody", f"a file under {self.inputs_dir}", f"{path} resolves to {path.resolve()}",
                   "remove the link and resume")
        return path

    def skeptic_input(self, record_id: str) -> Path:
        """Item 31 item 4: tests-fix reads a file the driver wrote from the validated envelope."""
        record_path = self.worktree / ".dev" / "records" / "dispatches" / f"{record_id}.json"
        try:
            payload = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        envelope = ((payload.get("result") or {}).get("envelope")) or {}
        try:
            validate_check_tests(envelope)
        except EnvelopeRefused as refusal:
            raise Stopped("check-tests", record_id, "envelope", exit_code=2, wire=refusal.line()) from refusal
        body = (
            f"check-tests envelope, record {record_id}\n\n"
            + json.dumps(envelope, indent=2, sort_keys=True)
        )
        try:
            return self.make_input("skeptic-findings", body)
        except Refused as refusal:
            if refusal.kind != "custody":
                raise
            raise Stopped("tests-fix", reason="custody", exit_code=2, wire=refusal.line()) from refusal

    def dispatch_args(
        self,
        name: str,
        inputs: list[Path],
        previous: str | None,
    ) -> list[str]:
        job, harness, flag = stage_spec(name)
        scope = self.stage_scope(name, inputs)
        argv = [
            str(self.dispatch),
            "--branch",
            self.branch,
            "--job",
            job,
            "--harness",
            harness,
            "--unit",
            self.branch,
            "--stage",
            flag,
            "--scope-file",
            str(scope),
        ]
        for path in inputs:
            if path == scope:
                continue
            argv.extend(("--input", str(path)))
        if previous:
            argv.extend(("--follows", previous))
        if name in {"fix-tests", "fix-code"}:
            for record_id in self.correction_follows:
                argv.extend(("--follows", record_id))
            for key in self.correction_answers:
                argv.extend(("--answers", key))
        if name == "tests-fix":
            for key in self.skeptic_answers:
                argv.extend(("--answers", key))
        return argv

    def require_cited(self, stage: str, keys: list[str]) -> None:
        undisposed: list[str] = []
        for key in keys:
            checked = run(
                [sys.executable, str(self.findings), "cited", key],
                self.worktree,
            )
            if checked.returncode == 1:
                undisposed.append(key)
            elif checked.returncode != 0:
                raise Stopped(
                    stage,
                    reason="ledger",
                    detail=checked.stderr.strip() or checked.stdout.strip(),
                )
        if undisposed:
            raise Stopped(
                stage,
                reason="undisposed",
                detail="undisposed keys: " + " ".join(undisposed),
                keys=undisposed,
            )

    def stage_scope(self, name: str, inputs: list[Path]) -> Path:
        if name == "fix-code" or name == "code" or name.startswith("code-"):
            for path in inputs:
                if path.name == "test-command.md":
                    return path
        return self.scope

    def resolved(
        self,
        name: str,
        inputs: list[Path],
        previous: str | None,
    ) -> tuple[str, str, list[str]]:
        base = self.dispatch_args(name, inputs, previous)
        result = run([*base, "--dry-run"], self.worktree)
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            reason = detail.split(":", 1)[0] if detail else "job"
            raise Stopped(name, reason=reason, detail=detail)
        try:
            launch = parse_dispatch_json(result.stdout)
        except ValueError as error:
            raise Stopped(name, reason="model", detail=str(error)) from error
        argv = [str(item) for item in launch["argv"]]
        model = flag_value(argv, "--model")
        effort = flag_value(argv, "--effort")
        if not model or not effort:
            raise Stopped(
                name,
                reason="model",
                detail="dispatcher dry-run omitted model or effort",
            )
        return model, effort, base

    def custody(self, name: str, previous: str | None) -> dict:
        first = self.scope.read_text(encoding="utf-8").splitlines()[0]
        given = [first]
        if previous:
            given.append(previous)
        withheld = list(self.implementation_markers) if name in WITHHELD_STAGES else []
        return {"given": given, "withheld": withheld}

    def mint(
        self,
        name: str,
        inputs: list[Path],
        previous: str | None,
    ) -> Pending:
        row = self.row(name)
        attempt = int(row.get("attempt") or 0) + 1
        row["attempt"] = attempt
        model, effort, base = self.resolved(name, inputs, previous)
        row["dispatched"] = {"model": model, "effort": effort}
        proof = self.custody(name, previous)
        self.verify_custody(name, inputs, previous, proof)
        input_path = inputs[-1] if inputs else self.scope
        row["input"] = {
            "path": str(input_path),
            "sha256": digest(input_path),
            "proof": proof,
        }
        self.commit(*[path for path in inputs if path.is_relative_to(self.inputs_dir)])
        base_sha = git_out(self.worktree, "rev-parse", "HEAD")
        result = run(
            [*base, "--model", model, "--effort", effort, "--detach"],
            self.worktree,
        )
        match = RECORD_ID.search(result.stdout)
        if result.returncode != 0 or match is None:
            reason = "job"
            detail = result.stderr.strip() or result.stdout.strip()
            if detail and ":" in detail:
                reason = detail.split(":", 1)[0].split()[-1]
            raise Stopped(name, reason=reason, detail=detail)
        record_id = match.group(1)
        record_path = (
            self.worktree
            / ".dev"
            / "records"
            / "dispatches"
            / f"{record_id}.json"
        )
        if not record_path.is_file():
            raise Stopped(
                name,
                record_id,
                "record",
                detail="dispatch record absent",
            )
        row["dispatch_record"] = record_id
        # The row names the record it first minted; dispatch_record follows a retry.
        row.setdefault("record", record_id)
        row["outcome"] = "launched"
        self.commit(record_path)
        try:
            launched = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise Stopped(name, record_id, "record", detail=str(error)) from error
        selection = launched.get("model") or {}
        if (
            selection.get("requested") != model
            or selection.get("effort_requested") != effort
        ):
            raise Stopped(
                name,
                record_id,
                "model",
                detail="minted selection differs from dispatcher dry-run",
            )
        return Pending(
            name,
            record_id,
            record_path,
            model,
            effort,
            base_sha,
            time.monotonic(),
        )

    def load_envelope(self, pending: Pending) -> tuple[dict, dict]:
        try:
            dispatch_record = json.loads(pending.record_path.read_text(encoding="utf-8"))
            result = dispatch_record["result"]
            envelope = result["envelope"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise Stopped(
                pending.name,
                pending.record_id,
                "envelope",
                detail=str(error),
            ) from error
        if dispatch_record.get("status") != "closed" or envelope.get("status") != "ok":
            raise Stopped(
                pending.name,
                pending.record_id,
                "envelope",
                detail="dispatch or envelope was not accepted",
            )
        job, harness, stage = stage_spec(pending.name)
        lineage = dispatch_record.get("lineage") or {}
        binding = {
            "job": dispatch_record.get("job"),
            "stage": dispatch_record.get("stage"),
            "harness": (dispatch_record.get("harness") or {}).get("name"),
            "branch": lineage.get("branch"),
            "unit": dispatch_record.get("unit"),
        }
        expected_binding = {
            "job": job,
            "stage": stage,
            "harness": harness,
            "branch": self.branch,
            "unit": self.branch,
        }
        if binding != expected_binding:
            raise Stopped(
                pending.name,
                pending.record_id,
                "record",
                detail=(
                    f"dispatch record binding differs: expected {expected_binding!r}; "
                    f"found {binding!r}"
                ),
            )
        requested = (dispatch_record.get("model") or {}).get("requested")
        effort = (dispatch_record.get("model") or {}).get("effort_requested")
        if requested != pending.model or effort != pending.effort:
            raise Stopped(
                pending.name,
                pending.record_id,
                "model",
                detail="recorded selection differs from dispatcher dry-run",
            )
        row = self.row(pending.name)
        row["recorded"] = {
            "model_requested": requested,
            "effort": effort,
            "model_ran": (dispatch_record.get("model") or {}).get("ran"),
        }
        return dispatch_record, envelope

    def stage_wall(self, stage: str, launched: dict):
        """The wait wall for a stage, in seconds, or None for WALL_DEFAULT.

        LD-17 (2026-09-11): the launcher writes ``caps.timeout`` into the
        record copy only when the job closes, so reading it at wait time
        found nothing and every stage waited WALL_DEFAULT; a 22-minute
        tests-fix with an 1800-second cap was stopped as ``wall``. The
        record copy wins when it carries a cap; otherwise the stage's job
        cap from the job registry, the launcher's own default source.
        """
        cap = (launched.get("caps") or {}).get("timeout")
        if cap is not None:
            return cap
        job = next((row[1] for row in DISPATCH_STAGES if row[0] == stage), None)
        if job is None:
            return None
        jobs_path = self.worktree / "ops" / "devlane" / "task" / "jobs.json"
        try:
            jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return ((jobs.get(job) or {}).get("caps") or {}).get("timeout")

    def await_result(self, pending: Pending) -> tuple[dict, dict]:
        try:
            launched = json.loads(pending.record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            launched = {}
        cap = self.stage_wall(pending.name, launched)
        try:
            wall = int(cap or self.conf["WALL_DEFAULT"])
            grace = int(self.conf["LADDER_WAIT_GRACE"])
        except (TypeError, ValueError) as error:
            raise Stopped(
                pending.name,
                pending.record_id,
                "wall",
                detail=f"invalid wait cap: {error}",
            ) from error
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(self.launch),
                    "wait",
                    pending.record_id,
                    "--timeout",
                    str(wall + grace),
                ],
                cwd=self.worktree,
                capture_output=True,
                text=True,
                check=False,
                timeout=wall + grace,
            )
        except subprocess.TimeoutExpired as error:
            raise Stopped(
                pending.name,
                pending.record_id,
                "wall",
                detail=f"launch.py wait exceeded {wall + grace} seconds",
            ) from error
        if result.returncode != 0:
            detail = "\n".join(
                text.strip() for text in (result.stdout, result.stderr) if text.strip()
            )
            terminal = next((
                line.lower() for line in reversed(result.stdout.splitlines())
                if re.search(r"\b(?:closed|died)\b", line) and "envelope=" in line
            ), "")
            if terminal:
                reason = (
                    "tripped" if "tripped=true" in terminal
                    else "died" if re.search(r"\bdied\b", terminal)
                    else "envelope"
                )
            else:
                reason = "wall" if result.returncode == 2 else "died"
            if reason in {"tripped", "died"}:
                detail = (
                    f"{detail}\nneeded run launch.py collect {pending.record_id} "
                    "--verify COMMAND"
                )
            raise Stopped(pending.name, pending.record_id, reason, detail=detail)
        self.commit(pending.record_path)
        dispatch_record, envelope = self.load_envelope(pending)
        return dispatch_record, envelope

    def existing_result(self, name: str) -> tuple[Pending, dict, dict] | None:
        row = self.row(name)
        record_id = row.get("dispatch_record")
        dispatched = row.get("dispatched") or {}
        if not record_id or not dispatched.get("model") or not dispatched.get("effort"):
            return None
        record_path = (
            self.worktree
            / ".dev"
            / "records"
            / "dispatches"
            / f"{record_id}.json"
        )
        pending = Pending(
            name,
            record_id,
            record_path,
            dispatched["model"],
            dispatched["effort"],
            self.record.get("base_sha") or git_out(self.worktree, "rev-parse", "HEAD"),
            time.monotonic(),
        )
        try:
            dispatch_record, envelope = self.load_envelope(pending)
        except Stopped as error:
            if error.reason == "record":
                raise
            return None
        return pending, dispatch_record, envelope

    def land_result(self, pending: Pending, dispatch_record: dict) -> None:
        row = self.row(pending.name)
        ran = (dispatch_record.get("model") or {}).get("ran")
        harness = (dispatch_record.get("harness") or {}).get("name")
        address = self.conf.get(f"ADDRESS_{str(harness).upper()}")
        if not isinstance(ran, str) or not ran or not address:
            raise Stopped(pending.name, pending.record_id, "model", detail="ran model has no address")
        author = full_identity(self.conf["LANE_AUTHOR"], "LANE_AUTHOR")
        coauthor = full_identity(f"{ran} <{address}>", "harness co-author")
        stage_base = git_out(self.worktree, "rev-parse", "HEAD")
        result = run(
            [
                sys.executable,
                str(self.land),
                pending.record_id,
                "--author",
                author,
                "--co-author",
                coauthor,
            ],
            self.worktree,
        )
        if result.returncode != 0:
            detail = result.stdout.strip() or result.stderr.strip()
            raise Stopped(pending.name, pending.record_id, "land", detail=detail)
        shas = LANDED.findall(result.stdout)
        for sha in shas:
            ancestry = git(self.worktree, "merge-base", "--is-ancestor", stage_base, sha)
            if ancestry.returncode != 0:
                raise Stopped(pending.name, pending.record_id, "lineage", detail=sha)
        row["landed_shas"] = shas
        row["landed"] = shas
        if (pending.name == "code" or pending.name.startswith("code-")) and shas:
            index = 0 if pending.name == "code" else int(pending.name.split("-", 1)[1]) - 1
            bucket = self.buckets[index]
            allowed = set(bucket["files"])
            if allowed:
                measured = git(
                    self.worktree,
                    "diff",
                    "--name-only",
                    stage_base,
                    shas[-1],
                    "--",
                )
                if measured.returncode != 0:
                    raise Stopped(
                        pending.name,
                        pending.record_id,
                        "bucket",
                        detail=measured.stderr.strip(),
                    )
                outside = sorted(set(measured.stdout.splitlines()) - allowed)
                if outside:
                    raise Stopped(
                        pending.name,
                        pending.record_id,
                        "bucket",
                        detail=f"landed paths outside bucket {bucket['name']}: {outside!r}",
                    )
        row["outcome"] = "ok"
        if pending.name == "code" or pending.name.startswith("code-") or pending.name == "fix-code":
            envelope = (dispatch_record.get("result") or {}).get("envelope") or {}
            marker_values = [pending.record_id]
            for key in ("note", "artifacts", "findings"):
                marker_values.extend(self.strings(envelope.get(key)))
            for marker in self.normalize_markers(marker_values):
                if marker not in self.implementation_markers:
                    self.implementation_markers.append(marker)
        self.commit()

    def ledger_result(self, pending: Pending, envelope: dict) -> None:
        imported = run([sys.executable, str(self.findings), "import"], self.worktree)
        if imported.returncode != 0:
            raise Stopped(
                pending.name,
                pending.record_id,
                "ledger",
                detail=imported.stderr.strip() or imported.stdout.strip(),
            )
        exported = run([sys.executable, str(self.findings), "export"], self.worktree)
        if exported.returncode != 0:
            raise Stopped(
                pending.name,
                pending.record_id,
                "ledger",
                detail=exported.stderr.strip() or exported.stdout.strip(),
            )
        destination = self.worktree / "ops" / "conductor" / "findings" / "findings.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(exported.stdout, encoding="utf-8")
        row = self.row(pending.name)
        row["ledger"] = {"imported": True, "exported": str(destination)}
        row["verdict"] = envelope.get("verdict")
        counts = envelope.get("counts") or {}
        row["findings"] = sum(
            int(counts.get(key) or 0) for key in ("p1", "p2", "p3")
        )
        row["outcome"] = "ok"
        self.commit(destination)

    def finish(self, pending: Pending, *, already_closed: tuple[dict, dict] | None = None) -> dict:
        if already_closed is None:
            dispatch_record, envelope = self.await_result(pending)
        else:
            dispatch_record, envelope = already_closed
            self.commit(pending.record_path)
        if pending.name == "check-tests":
            try:
                validate_check_tests(envelope)
            except EnvelopeRefused as refusal:
                row = self.row(pending.name)
                row["envelope"] = {"field": refusal.field, "refusal": refusal.line()}
                raise Stopped(
                    pending.name, pending.record_id, "envelope", exit_code=2, wire=refusal.line()
                ) from refusal
        if pending.name in REVIEW_STAGES:
            self.ledger_result(pending, envelope)
        else:
            self.land_result(pending, dispatch_record)
        self.row(pending.name)["minutes"] = round(
            (time.monotonic() - pending.started) / 60,
            6,
        )
        self.commit()
        return envelope

    def inputs_for(
        self,
        name: str,
        records: dict[str, str],
        command_file: Path | None,
    ) -> list[Path]:
        inputs = [self.plan] if self.plan is not None else []
        inputs.extend(self.start_inputs)
        dispatch_dir = self.worktree / ".dev" / "records" / "dispatches"
        if name == "check-tests" and "tests" in records:
            inputs.append(dispatch_dir / f"{records['tests']}.json")
        elif name == "tests-fix" and "check-tests" in records:
            inputs.append(self.skeptic_input(records["check-tests"]))
        elif name == "code" or name.startswith("code-"):
            inputs.append(self.scope)
            if command_file is not None:
                inputs.append(command_file)
        elif name in {"fix-tests", "fix-code"}:
            for audit in ("security", "adversarial"):
                if audit in records:
                    record_path = dispatch_dir / f"{records[audit]}.json"
                    if name == "fix-tests":
                        inputs.append(self.audit_envelope_input(audit, record_path))
                    else:
                        inputs.append(record_path)
            if name == "fix-code" and command_file is not None:
                inputs.append(self.scope)
                inputs.append(command_file)
                context = self.make_input(
                    "implementation-records",
                    "implementation records: " + " ".join(self.implementation_markers),
                )
                inputs.append(context)
        elif name == "security-re" and "security" in records:
            inputs.append(dispatch_dir / f"{records['security']}.json")
        return [path for path in inputs if path is not None]

    def execute(
        self,
        name: str,
        records: dict[str, str],
        command_file: Path | None,
        previous: str | None,
        *,
        resume: bool = False,
    ) -> tuple[dict, str]:
        inputs = self.inputs_for(name, records, command_file)
        if resume:
            row = self.row(name)
            if row.get("outcome") == "ok":
                existing = self.existing_result(name)
                if existing is None:
                    raise Stopped(
                        name,
                        str(row.get("dispatch_record") or "none"),
                        "record",
                        detail="completed stage has no accepted dispatch record",
                    )
                pending, _dispatch_record, envelope = existing
                records[name] = pending.record_id
                return envelope, pending.record_id
            retried = (
                row.get("reason") == "envelope"
                and self.retry is not None
                and self.retry[0] == name
                and self.retry_granted(name)
            )
            existing = None if retried else self.existing_result(name)
            if existing is not None:
                pending, dispatch_record, envelope = existing
                result = self.finish(pending, already_closed=(dispatch_record, envelope))
                records[name] = pending.record_id
                return result, pending.record_id
            if int(row.get("attempt") or 0) >= 2 and not retried and not self.retry_granted(name):
                raise Stopped(
                    name,
                    str(row.get("dispatch_record") or "none"),
                    "retry-cap",
                    detail="stage already reached its two-attempt cap",
                )
        try:
            pending = self.mint(name, inputs, previous)
            records[name] = pending.record_id
            return self.finish(pending), pending.record_id
        except Stopped as stopped:
            if stopped.wire:
                raise
            if resume and int(self.row(name).get("attempt") or 0) >= 2:
                detail = "\n".join(
                    part
                    for part in (
                        stopped.detail,
                        f"second attempt failed reason={stopped.reason or 'unknown'}",
                    )
                    if part
                )
                raise Stopped(
                    name,
                    stopped.record_id,
                    "retry-cap",
                    detail=detail,
                ) from stopped
            raise

    def retry_granted(self, name: str) -> bool:
        """LD-19: one recorded extra attempt for the stage named by
        ``resume --retry STAGE --because TEXT``, for a cap the driver's own
        command caused. Written on the stage row and spent once."""
        retry = self.retry
        if not retry or retry[0] != name:
            return False
        row = self.row(name)
        if row.get("retry"):
            return False
        row["retry"] = {
            "because": retry[1],
            "at": utc_stamp(),
            "by": self.agent,
            "from_attempt": int(row.get("attempt") or 0),
        }
        return True

    def in_stage_set(self, name: str) -> bool:
        return name in self.stage_set["stages"]

    def mark_skipped(self, name: str, condition: str) -> None:
        row = self.row(name)
        row["condition"] = condition
        row["outcome"] = "skipped"

    def derive_command(self, tests_envelope: dict) -> Path:
        commands: list[str] = []
        artifacts = tests_envelope.get("artifacts")
        if isinstance(artifacts, dict):
            for value in artifacts.values():
                if not isinstance(value, str):
                    continue
                path = Path(value)
                if (
                    path.suffix != ".py"
                    or "tests" not in path.parts[:-1]
                    or re.search(r"[;&|\r\n]", value)
                    # LD-19: only test modules (unittest's default pattern)
                    # become commands; a support module under tests/ rides
                    # as an artifact. A discover over launch_support.py
                    # found zero tests, exited 5 and consumed both code
                    # attempts of ladder fd103b.
                    or not path.name.startswith("test")
                ):
                    continue
                # LD-15: the command names the tree, never a worker's
                # snapshot. An absolute path under a dispatch snapshot is
                # rewritten to the part after `snapshot/`; any other
                # absolute path, or a `..` component, stops the ladder.
                if ".." in path.parts:
                    raise Stopped(
                        "code",
                        reason="artifacts",
                        detail=f"test artifact path escapes the tree: {value}",
                    )
                if path.is_absolute():
                    parts = path.parts
                    if "snapshot" not in parts[:-1]:
                        raise Stopped(
                            "code",
                            reason="artifacts",
                            detail=f"absolute test artifact outside a snapshot: {value}",
                        )
                    cut = max(i for i, part in enumerate(parts[:-1]) if part == "snapshot")
                    path = Path(*parts[cut + 1:])
                commands.append(
                    "python3 -m unittest discover "
                    f"-s {shlex.quote(path.parent.as_posix())} "
                    f"-p {shlex.quote(path.name)}"
                )
        if not commands:
            raise Stopped("code", reason="artifacts", detail="no test artifact")
        command = " && ".join(commands)
        if len(command.encode()) > int(self.conf["SCOPE_CAP"]):
            raise Stopped(
                "code",
                reason="scope",
                detail="derived test command exceeds SCOPE_CAP",
            )
        return self.make_input("test-command", command)

    def names_worktree_path(self, line: str) -> bool:
        """True when ``line`` is a path that exists under the worktree."""
        if " " in line or len(line) > 200 or not ("/" in line or "." in line):
            return False
        try:
            return (self.worktree / line).exists()
        except OSError:
            return False

    def normalize_markers(self, values: list) -> list[str]:
        """The record ids and distinctive lines among ``values``.

        A line is a marker only when it is at least MARKER_MIN characters
        and is not a path present in the worktree: bare paths, severity
        tokens and short receipts name shared surfaces, not the
        implementation, and an audit that names the same file is not a leak.
        """
        markers: list[str] = []
        for value in values:
            if not isinstance(value, str):
                continue
            for line in value.splitlines():
                line = line.strip()
                if not line or line in markers:
                    continue
                if RECORD_ID_SHAPE.fullmatch(line):
                    markers.append(line)
                    continue
                if len(line) < MARKER_MIN or self.names_worktree_path(line):
                    continue
                markers.append(line)
        return markers

    @classmethod
    def strings(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for child in value.values() for item in cls.strings(child)]
        if isinstance(value, list):
            return [item for child in value for item in cls.strings(child)]
        return []

    def has_leak(self, envelope: dict) -> bool:
        encoded = json.dumps(envelope, sort_keys=True)
        return any(marker in encoded for marker in self.implementation_markers) or bool(
            re.search(r"implement(?:ation)?[_ -]+envelope", encoded, re.IGNORECASE)
        )

    def verify_custody(
        self,
        name: str,
        inputs: list[Path],
        previous: str | None,
        proof: dict,
    ) -> None:
        chunks = [str(self.scope), self.scope.read_text(encoding="utf-8")]
        for path in inputs:
            chunks.extend((str(path), path.read_text(encoding="utf-8")))
        if previous:
            chunks.append(previous)
        dispatched = "\n".join(chunks)
        missing = [item for item in proof["given"] if item not in dispatched]
        leaked = [item for item in proof["withheld"] if item in dispatched]
        if missing or leaked:
            raise Stopped(
                name,
                reason="withheld",
                detail=f"custody proof failed missing={missing!r} leaked={leaked!r}",
            )

    def audit_pair(
        self,
        records: dict[str, str],
        command_file: Path,
        previous: str,
        *,
        resume: bool = False,
    ) -> tuple[dict, dict, str]:
        if resume:
            security_envelope, security_id = self.execute(
                "security", records, command_file, previous, resume=True
            )
            adversarial_envelope, adversarial_id = self.execute(
                "adversarial", records, command_file, security_id, resume=True
            )
            return security_envelope, adversarial_envelope, adversarial_id
        security = self.mint(
            "security",
            self.inputs_for("security", records, command_file),
            previous,
        )
        records["security"] = security.record_id
        adversarial = self.mint(
            "adversarial",
            self.inputs_for("adversarial", records, command_file),
            security.record_id,
        )
        records["adversarial"] = adversarial.record_id
        first_error: Stopped | None = None
        try:
            security_envelope = self.finish(security)
        except Stopped as error:
            security_envelope = {}
            first_error = error
        try:
            adversarial_envelope = self.finish(adversarial)
        except Stopped:
            if first_error is None:
                raise
            adversarial_envelope = {}
        if first_error is not None:
            raise first_error
        return security_envelope, adversarial_envelope, adversarial.record_id

    def calibrate(self) -> None:
        head = git_out(self.worktree, "rev-parse", "HEAD")
        changed = git(
            self.worktree,
            "diff",
            "--numstat",
            self.record.get("base_sha") or head,
            head,
        ).stdout.splitlines()
        files = len(changed)
        lines = 0
        for raw in changed:
            fields = raw.split("\t", 2)
            if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
                lines += int(fields[0]) + int(fields[1])
        test_count = sum(1 for raw in changed if "/tests/" in raw or "test_" in raw)
        minutes = {
            row["name"]: float(row.get("minutes") or 0)
            for row in self.record["stages"]
            if row.get("outcome") not in {"pending", "skipped"}
        }
        findings = {
            row["name"]: 0
            for row in self.record["stages"]
            if row.get("verdict") is not None
        }
        slices = self.slices or [{"name": self.buckets[0]["name"], "declared": "S"}]
        self.record["calibration"] = [
            {
                "name": item["name"],
                "declared": item["declared"],
                "stage_set": self.stage_set["name"],
                "measured": {
                    "files": files,
                    "tests": test_count,
                    "lines": lines,
                    "minutes": minutes,
                    "findings": findings,
                },
                "buckets": [
                    {"name": bucket["name"], "declared": bucket["declared"]}
                    for bucket in self.buckets
                ],
            }
            for item in slices
        ]

    def open_pr(self) -> str:
        head = git_out(self.worktree, "rev-parse", "HEAD")
        stage_evidence = []
        open_findings = []
        for stage in self.record["stages"]:
            record_id = stage.get("record") or stage.get("dispatch_record")
            if record_id:
                dispatched = stage.get("dispatched") or {}
                stage_evidence.append(
                    f"- {stage['name']}: record `{record_id}`, model "
                    f"`{dispatched.get('model')}`, effort "
                    f"`{dispatched.get('effort')}`, verdict "
                    f"`{stage.get('verdict')}` — record read at `{head}`"
                )
            if stage.get("verdict") == "changes":
                open_findings.append(f"- Review `{stage['name']}` requested changes.")
        body_text = "\n".join(
            [
                "## What",
                "",
                f"Run the declared audit ladder for `{self.branch}` and open its draft review.",
                f"The branch passed the configured `{self.conf['CHECK_NAMES']}` gate sequence.",
                "",
                "## Sources",
                "",
                "- original",
                f"- scope `{self.scope}`",
                "",
                "## Evidence",
                "",
                f"- Branch head was `{head}` — `git rev-parse HEAD` at `{head}`",
                *stage_evidence,
                "",
                "## For reviewers",
                "",
                *(open_findings or ["- Recheck the stage-selection and custody proofs."]),
            ]
        )
        body = self.make_input(
            "pull-request",
            body_text,
        )
        row = self.row("pr")
        row["attempt"] = 1
        self.commit(body)
        pushed = git(self.worktree, "push", "origin", "--", self.branch)
        if pushed.returncode != 0:
            raise Stopped("pr", reason="push", detail=pushed.stderr.strip())
        argv = [
            self.gh,
            "pr",
            "create",
            "--draft",
            "--base",
            self.conf["TRUNK_BRANCH"],
            "--head",
            self.branch,
            "--repo",
            self.conf["REPO_SLUG"],
            "--title",
            f"Audit ladder {self.record['id']}",
            "--body-file",
            str(body),
        ]
        created = run(argv, self.worktree)
        if created.returncode != 0:
            raise Stopped(
                "pr",
                reason="pr",
                detail=created.stderr.strip() or created.stdout.strip(),
            )
        output = created.stdout.strip().splitlines()
        if not output:
            raise Stopped(
                "pr",
                reason="pr",
                detail="gh pr create returned no URL",
            )
        url = output[-1]
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or any(
            character.isspace() for character in url
        ):
            raise Stopped(
                "pr",
                reason="pr",
                detail="gh pr create returned an invalid URL",
            )
        row["outcome"] = "ok"
        tail = url.rstrip("/").rsplit("/", 1)[-1]
        self.record["pr"] = {
            "number": int(tail) if tail.isdigit() else None,
            "url": url,
        }
        self.record["status"] = "done"
        self.record["stopped"] = None
        self.calibrate()
        self.commit()
        return url

    def run(self, *, resume: bool = False) -> str:
        records = {
            row["name"]: row["dispatch_record"]
            for row in self.record["stages"]
            if row.get("dispatch_record")
        }
        previous: str | None = None
        try:
            if resume:
                self.record["status"] = "running"
                self.record["stopped"] = None
            self.commit()
            tests_envelope, previous = self.execute(
                "tests", records, None, previous, resume=resume
            )
            check_envelope, previous = self.execute(
                "check-tests", records, None, previous, resume=resume
            )
            in_set = self.in_stage_set
            if check_envelope.get("verdict") == "changes":
                if self.has_leak(check_envelope):
                    raise Stopped("tests-fix", reason="withheld")
                if not in_set("tests-fix"):
                    raise Stopped("check-tests", previous, verdict="changes", exit_code=1)
                self.skeptic_answers = [
                    f"{records.get('check-tests', previous)}#{number}"
                    for number, _finding in enumerate(
                        check_envelope.get("findings") or [], start=1
                    )
                ]
                self.require_cited("tests-fix", self.skeptic_answers)
                _fixed, previous = self.execute(
                    "tests-fix", records, None, previous, resume=resume
                )
            elif in_set("tests-fix"):
                self.mark_skipped("tests-fix", "skeptic did not request changes")
            command_file: Path | None = None
            if in_set("code"):
                command_file = self.derive_command(tests_envelope)
                for index, _bucket in enumerate(self.buckets, 1):
                    name = "code" if index == 1 else f"code-{index}"
                    _coded, previous = self.execute(
                        name, records, command_file, previous, resume=resume
                    )
            audits: dict[str, dict] = {}
            if in_set("security") and in_set("adversarial"):
                security, adversarial, previous = self.audit_pair(
                    records, command_file, previous, resume=resume
                )
                audits = {"security": security, "adversarial": adversarial}
            elif in_set("security") or in_set("adversarial"):
                name = "security" if in_set("security") else "adversarial"
                envelope, previous = self.execute(
                    name, records, command_file, previous, resume=resume
                )
                audits = {name: envelope}
            self.correction_follows = [records[name] for name in audits if name in records]
            self.correction_answers = [
                f"{records[name]}#{number}"
                for name, envelope in audits.items()
                for number, _finding in enumerate(
                    envelope.get("findings") or [], start=1
                )
            ]
            changes = any(
                envelope.get("verdict") == "changes" for envelope in audits.values()
            )
            if changes:
                if any(self.has_leak(envelope) for envelope in audits.values()):
                    raise Stopped("fix-tests", reason="withheld")
                if not in_set("fix-tests"):
                    last = [
                        name for name, envelope in audits.items()
                        if envelope.get("verdict") == "changes"
                    ][-1]
                    raise Stopped(
                        last, records.get(last, previous), verdict="changes", exit_code=1
                    )
                self.require_cited("fix-tests", self.correction_answers)
                _test_fix, previous = self.execute(
                    "fix-tests", records, command_file, previous, resume=resume
                )
                self.require_cited("fix-code", self.correction_answers)
                _code_fix, previous = self.execute(
                    "fix-code", records, command_file, previous, resume=resume
                )
                if in_set("security-re"):
                    security_re, previous = self.execute(
                        "security-re", records, command_file, previous, resume=resume
                    )
                    if security_re.get("verdict") == "changes":
                        raise Stopped(
                            "security-re",
                            previous,
                            verdict="changes",
                            exit_code=1,
                        )
            else:
                for name in ("fix-tests", "fix-code", "security-re"):
                    if in_set(name):
                        self.mark_skipped(name, "audits did not request changes")
            return self.open_pr()
        except Stopped as stopped:
            self.stop(stopped)


def initial_conf_path(cwd: Path) -> Path:
    explicit = os.environ.get("LANE_CONF")
    if explicit:
        return Path(explicit).resolve()
    return (cwd / "ops" / "lane.conf").resolve()


def load_conf(worktree: Path) -> dict[str, str]:
    os.environ["LANE_CONF"] = str(worktree / "ops" / "lane.conf")
    values: dict[str, str] = {}
    for key in CONF_KEYS:
        try:
            values[key] = conf_value(key)
        except Refused as error:
            if key != "CHECK_NAMES" or error.kind != "conf-key":
                raise
            values[key] = conf_value("CHECK_NAME")
    try:
        values["LADDER_WAIT_GRACE"] = conf_value("LADDER_WAIT_GRACE")
    except Refused as error:
        if error.kind != "conf-key":
            raise
        values["LADDER_WAIT_GRACE"] = "300"
    values.update(configuration())
    return values


def validate_preconditions(
    worktree: Path,
    conf: dict[str, str],
    scope: Path,
    agent: str,
    *,
    recovery_record: str | None = None,
) -> str:
    try:
        cap = int(conf["SCOPE_CAP"])
    except ValueError:
        refuse(
            "scope",
            "SCOPE_CAP as a positive whole number",
            conf["SCOPE_CAP"],
            "correct the lane conf",
        )
    size = len(scope.read_bytes())
    if cap < 1 or size > cap:
        refuse(
            "scope",
            f"a readable scope at most {cap} bytes",
            f"{size} bytes at {scope}",
            "supply a bounded readable scope",
        )
    dirty = git(worktree, "status", "--porcelain").stdout.splitlines()
    allowed = (
        f".dev/records/dispatches/{recovery_record}.json"
        if recovery_record
        else None
    )
    unexpected = [
        line
        for line in dirty
        if allowed is None or line[3:].strip().strip('"') != allowed
    ]
    if unexpected:
        refuse(
            "dirty",
            "a clean target worktree",
            f"changes in {worktree}: {' | '.join(unexpected)}",
            "commit or stash them",
        )
    full_identity(agent, "WF_AGENT")
    full_identity(conf["LANE_AUTHOR"], "LANE_AUTHOR")
    name = git_out(worktree, "config", "--get", "user.name")
    email = git_out(worktree, "config", "--get", "user.email")
    full_identity(f"{name} <{email}>", "worktree git identity")
    if shutil.which("gh") is None:
        refuse(
            "gh-missing",
            "an executable gh on PATH",
            "none",
            "install or expose the GitHub CLI",
        )
    jobs_path = worktree / "ops" / "devlane" / "task" / "jobs.json"
    try:
        jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        refuse("job", "a readable job registry", str(error), "restore jobs.json")
    for _stage, job, _harness, _flag in DISPATCH_STAGES:
        if job not in jobs:
            refuse(
                "job",
                f"job {job} in {jobs_path}",
                "absent",
                "restore the job declaration",
            )
    return agent


def dry_run(driver: Driver) -> int:
    stages: list[dict] = []
    for name, _job, _harness, _flag in DISPATCH_STAGES:
        inputs = [driver.plan] if driver.plan is not None else []
        inputs.extend(driver.start_inputs)
        model, effort, base = driver.resolved(name, inputs, None)
        resolved = run([*base, "--model", model, "--effort", effort, "--dry-run"], driver.worktree)
        payload = parse_dispatch_json(resolved.stdout)
        stages.append({"name": name, "argv": payload["argv"]})
    stages.append(
        {
            "name": "pr",
            "argv": [
                driver.gh,
                "pr",
                "create",
                "--draft",
                "--base",
                driver.conf["TRUNK_BRANCH"],
                "--head",
                driver.branch,
                "--repo",
                driver.conf["REPO_SLUG"],
            ],
        }
    )
    print(json.dumps({"stage_set": driver.stage_set, "stages": stages}, indent=2))
    return 0


def split_inputs(args: list[str]) -> tuple[list[str], list[str]]:
    """Separate repeatable ``--input PATH`` pairs from the positionals."""
    positionals: list[str] = []
    inputs: list[str] = []
    items = list(args)
    while items:
        item = items.pop(0)
        if item == "--input":
            if not items:
                refuse(
                    "arguments",
                    "SCOPE BRANCH [PLAN] [--input PATH]... or resume LADDER_ID",
                    "--input with no path",
                    "name the input file",
                )
            inputs.append(items.pop(0))
        elif item.startswith("--input="):
            inputs.append(item.partition("=")[2])
        else:
            positionals.append(item)
    return positionals, inputs


def split_options(args: list[str]) -> tuple[list[str], list[str], tuple[str, str] | None]:
    """Positionals, --input paths, and the --stage-set CLASS --because TEXT override."""
    positionals: list[str] = []
    inputs: list[str] = []
    cls: str | None = None
    because: str | None = None
    rest: list[str] = []
    items = list(args)
    while items:
        item = items.pop(0)
        if item in ("--stage-set", "--because"):
            if not items:
                refuse("arguments", f"{item} with a value", item, "name the class or the reason")
            value = items.pop(0)
            if item == "--stage-set":
                cls = value
            else:
                because = value
        else:
            rest.append(item)
    positionals, inputs = split_inputs(rest)
    if (cls is None) != (because is None):
        refuse("arguments", "--stage-set CLASS together with --because TEXT",
               " ".join(args), "name both the class and the reason")
    if cls is not None and not because.strip():
        refuse("arguments", "a non-empty --because", repr(because), "say why this run overrides the plan")
    return positionals, inputs, (cls, because) if cls is not None else None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args:
            refuse(
                "arguments",
                "SCOPE BRANCH [PLAN] [--input PATH]... or resume LADDER_ID [--rescope] [--retry STAGE --because TEXT]; the start form also takes --stage-set CLASS --because TEXT, and stage-sets PATH validates a table",
                "no arguments",
                "choose one documented form",
            )
        cwd = Path.cwd().resolve()
        agent = os.environ.get("WF_AGENT", "")
        resume = args[0] == "resume"
        if args[0] == "check-envelope":
            if len(args) != 2:
                refuse("arguments", "check-envelope PATH|-", " ".join(args), "name the record file or -")
            raw = sys.stdin.read() if args[1] == "-" else Path(args[1]).read_text(encoding="utf-8")
            check_envelope_source(raw)
            return 0
        if args[0] == "stage-sets":
            if len(args) != 2:
                refuse("arguments", "stage-sets PATH", " ".join(args), "name the table file")
            print(json.dumps(load_stage_sets(Path(args[1])), indent=2))
            return 0
        override: tuple[str, str] | None = None
        if resume:
            rescope_flag, retry = resume_options(args)
            worktree, record = record_worktree(cwd, args[1])
            branch = record.get("branch")
            if not isinstance(branch, str) or not branch:
                refuse("record", "a named branch", repr(branch), "repair the record")
            current_branch = git_out(worktree, "symbolic-ref", "--short", "HEAD")
            branch_check = git(worktree, "check-ref-format", f"refs/heads/{branch}")
            if branch.startswith("-") or branch_check.returncode != 0 or branch != current_branch:
                refuse(
                    "record",
                    f"the checked-out branch {current_branch}",
                    branch,
                    "repair the record branch",
                )
            changes: list | None = [] if rescope_flag else None
            scope = recorded_file(worktree, record.get("scope"), "scope", rescope=changes)
            plan_data = record.get("plan")
            plan = (
                recorded_file(worktree, plan_data, "plan", rescope=changes)
                if plan_data
                else None
            )
            start_inputs = [
                recorded_file(worktree, item, "input", rescope=changes)
                for item in (record.get("inputs") or [])
            ]
            recorded_file(worktree, record.get("conf"), "conf")
            if record.get("stage_sets"):
                recorded_file(worktree, record.get("stage_sets"), "stage-set table", rescope=changes)
            if rescope_flag:
                if not changes:
                    refuse(
                        "arguments",
                        "a changed scope, plan or input to record",
                        "every recorded digest unchanged",
                        "drop --rescope",
                    )
                stage = next(
                    (row["name"] for row in record.get("stages", []) if row.get("outcome") != "ok"),
                    None,
                )
                at = utc_stamp()
                record.setdefault("rescoped", []).extend(
                    {**change, "at": at, "stage": stage} for change in changes
                )
        else:
            dry = args[0] == "--dry-run"
            positionals, input_args, override = split_options(args[1:] if dry else args)
            if len(positionals) not in {2, 3}:
                refuse(
                    "arguments",
                    "SCOPE BRANCH [PLAN] [--input PATH]...",
                    " ".join(args),
                    "choose the documented start form",
                )
            scope = file_arg(positionals[0], "scope")
            start_inputs = [file_arg(item, "input") for item in input_args]
            branch = positionals[1]
            os.environ["LANE_CONF"] = str(initial_conf_path(cwd))
            root_hint = configuration()["WORKTREE_ROOT"]
            worktree = branch_worktree(cwd, branch, root_hint)
            record = None
            if len(positionals) == 3:
                plan = file_arg(positionals[2], "plan")
            else:
                default_plan = worktree / "plan.md"
                plan = default_plan if default_plan.is_file() else None
        conf = load_conf(worktree)
        validate_preconditions(
            worktree,
            conf,
            scope,
            agent,
            recovery_record=(
                str((record.get("stopped") or {}).get("record") or "") or None
                if resume
                else None
            ),
        )
        driver = Driver(
            worktree, branch, scope, plan, conf, agent, record, start_inputs, override
        )
        if resume:
            driver.retry = retry
        if not resume and args[0] == "--dry-run":
            return dry_run(driver)
        url = driver.run(resume=resume)
        print(f"ladder={driver.record['id']} pr={url}")
        return 0
    except Refused as error:
        print(error.line(), file=sys.stderr)
        return 2
    except Stopped as error:
        if error.wire:
            print(error.wire, file=sys.stderr)
            return 2
        if error.detail:
            print(error.detail)
        print(error.line())
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
