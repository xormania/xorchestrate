#!/usr/bin/env python3
"""Report branch custody and retire closed dispatch refs safely."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, emit, identity, refuse, state_dir
    from instrument import git as bounded_git
    from instrument import run as bounded_run
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

GH = shutil.which("gh") or "gh"
TIMEOUT_S = 30


@dataclass(frozen=True)
class Branch:
    """One derived branch-census row."""

    name: str
    sha: str
    role: str
    state: str
    behind: int
    feed: str


@dataclass(frozen=True)
class Transport:
    """One transport ref and its derived lifecycle readings."""

    record_id: str
    ref: str
    sha: str
    age_days: int
    landed: bool
    closed: bool


def run(executable: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one bounded command without a shell."""
    return bounded_run([executable, *args], timeout=TIMEOUT_S)


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return bounded_git(*args, timeout=TIMEOUT_S)


def git_value(*args: str) -> str:
    result = git(*args)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        refuse("repo", "a readable git work tree", detail, "run inside a git repository")
    return result.stdout.strip()


def require_repo() -> None:
    result = git("rev-parse", "--is-inside-work-tree")
    if result.returncode != 0 or result.stdout.strip() != "true":
        detail = result.stderr.strip() or "not a work tree"
        refuse("repo", "a git work tree", detail, "run from inside the repository")


def positive(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value <= 0:
        refuse(
            "window",
            "--window as a positive integer",
            repr(raw),
            "set --window above zero",
        )
    return value


def dispatch_root() -> Path:
    return state_dir() / "dispatch"


def ref_rows(prefix: str) -> list[tuple[str, str]]:
    raw = git_value("for-each-ref", "--format=%(refname)%09%(objectname)", prefix)
    rows: list[tuple[str, str]] = []
    for line in raw.splitlines():
        ref, sha = line.split("\t", 1)
        rows.append((ref, sha))
    return rows


def resolves(ref: str) -> str | None:
    result = git("rev-parse", "--verify", "--quiet", ref)
    return result.stdout.strip() if result.returncode == 0 else None


def ancestor(older: str, newer: str) -> bool:
    return git("merge-base", "--is-ancestor", older, newer).returncode == 0


def age_days(sha: str) -> int:
    raw = git_value("show", "-s", "--format=%ct", sha)
    try:
        committed = datetime.fromtimestamp(int(raw), UTC)
    except (ValueError, OSError, OverflowError):
        refuse("repo", f"a committer timestamp for {sha}", repr(raw), "repair the ref")
    return max(0, (datetime.now(UTC) - committed).days)


def behind_dev(sha: str, dev_sha: str) -> int:
    raw = git_value("rev-list", "--count", f"{sha}..{dev_sha}")
    try:
        return int(raw)
    except ValueError:
        refuse("repo", "an integer base distance", repr(raw), "repair the repository")


def open_prs() -> list[dict[str, object]]:
    if shutil.which("gh") is None:
        refuse("gh-error", "gh on PATH", "ABSENT", "install and authenticate gh")
    result = run(
        GH,
        "pr",
        "list",
        "--state",
        "open",
        "--json",
        "number,headRefName,baseRefName,isCrossRepository",
        "--limit",
        "1000",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"gh exited {result.returncode}"
        refuse("gh-error", "a successful gh PR query", detail, "restore GitHub access and retry")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        refuse("gh-error", "a JSON PR list", f"invalid JSON at {error.pos}", "retry the gh query")
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        refuse("gh-error", "a JSON PR array", type(payload).__name__, "retry the gh query")
    if len(payload) >= 1000:
        refuse(
            "gh-error",
            "an open-PR listing below its 1000-row limit",
            "a full page, an unmeasured total",
            "narrow the query or raise the limit",
        )
    for row in payload:
        number = row.get("number")
        head = row.get("headRefName")
        base = row.get("baseRefName")
        cross = row.get("isCrossRepository")
        if (
            isinstance(number, bool)
            or not isinstance(number, int)
            or not isinstance(head, str)
            or not head
            or not isinstance(base, str)
            or not base
            or not isinstance(cross, bool)
        ):
            refuse(
                "gh-error",
                "PR rows carrying number, headRefName, baseRefName, and a "
                "boolean isCrossRepository",
                json.dumps(row, sort_keys=True)[:80],
                "retry the gh query",
            )
    return payload


def role_for(name: str) -> str:
    if name == "dev":
        return "trunk"
    if name == "main":
        return "release"
    _namespace, separator, slug = name.partition("/")
    if separator and slug:
        return "unit"
    return "unknown"


def trunk_sha() -> str:
    sha = resolves("refs/remotes/origin/dev") or resolves("refs/heads/dev")
    if sha is None:
        refuse(
            "repo",
            "a resolvable dev trunk (origin/dev or heads/dev)",
            "ABSENT",
            "fetch or create dev; the trunk is never substituted",
        )
    return sha


def branch_rows(window: int) -> tuple[list[Branch], list[dict[str, object]], bool]:
    remote = ref_rows("refs/remotes/origin")
    named = [(ref.removeprefix("refs/remotes/origin/"), sha) for ref, sha in remote]
    named = [(name, sha) for name, sha in named if name != "HEAD"]
    dev_sha = trunk_sha()
    if not any(name == "dev" for name, _sha in named):
        named.append(("dev", dev_sha))
    main_sha = resolves("refs/remotes/origin/main") or resolves("refs/heads/main")

    # The census promises stacked detection (exit 1 on any STACKED row),
    # so the PR query is unconditional: a stacked PR can exist while
    # every branch tip is reachable.
    prs = open_prs()
    open_heads = {
        row["headRefName"] for row in prs if not row.get("isCrossRepository")
    }
    rows: list[Branch] = []
    finding = False
    for name, sha in named:
        role = role_for(name)
        merged = ancestor(sha, dev_sha) or (main_sha is not None and ancestor(sha, main_sha))
        if merged:
            state = "merged-kept"
        elif name in open_heads:
            state = "active"
        elif age_days(sha) > window:
            state = "stray"
        else:
            state = "idle"
        if state == "stray":
            feed = ""
        else:
            feed = "-> main" if name == "dev" else "-> dev" if role == "unit" else ""
        rows.append(Branch(name, sha, role, state, behind_dev(sha, dev_sha), feed))
        finding = finding or role == "unknown" or state == "stray"
    order = {"stray": 0, "active": 1, "idle": 2, "merged-kept": 3}
    rows.sort(key=lambda row: (0 if row.role == "unknown" else order[row.state], row.name))
    stacked_prs = [row for row in prs if row.get("baseRefName") not in {"dev", "main"}]
    return rows, stacked_prs, finding


def stacked_line(row: dict[str, object]) -> str:
    return f"STACKED #{row.get('number')} base={row.get('baseRefName')} retarget before merge"


def census(args: argparse.Namespace) -> int:
    rows, stacked_prs, finding = branch_rows(positive(args.window))
    if args.json:
        payload = {
            "branches": [row.__dict__ for row in rows],
            "stacked": [
                {"base": row.get("baseRefName"), "number": row.get("number")}
                for row in stacked_prs
            ],
        }
        print(json.dumps(payload, sort_keys=True))
    else:
        for row in rows:
            suffix = f"\t{row.feed}" if row.feed else ""
            print(f"{row.name}\t{row.role}\t{row.state}\tbehind dev by {row.behind}{suffix}")
        for row in stacked_prs:
            print(stacked_line(row))
    return 1 if finding or stacked_prs else 0


def stacked(_args: argparse.Namespace) -> int:
    rows = [row for row in open_prs() if row.get("baseRefName") not in {"dev", "main"}]
    for row in rows:
        print(stacked_line(row))
    return 1 if rows else 0


def patch_present(sha: str, dev_sha: str) -> bool:
    result = git("rev-list", "--right-only", "--cherry-pick", f"{dev_sha}...{sha}")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        refuse("repo", "comparable dispatch and dev refs", detail, "repair the refs")
    return not result.stdout.strip()


def transports(window: int) -> list[Transport]:
    dev_sha = trunk_sha()
    rows: list[Transport] = []
    for ref, sha in ref_rows("refs/dispatch"):
        record_id = ref.removeprefix("refs/dispatch/")
        exit_file = dispatch_root() / record_id / "exit"
        closed = exit_file.is_file()
        if closed:
            closed_at = datetime.fromtimestamp(exit_file.stat().st_mtime, UTC)
            age = max(0, (datetime.now(UTC) - closed_at).days)
        else:
            age = age_days(sha)
        rows.append(
            Transport(
                record_id,
                ref,
                sha,
                age,
                patch_present(sha, dev_sha),
                closed,
            )
        )
    return rows


def candidates(args: argparse.Namespace) -> list[Transport]:
    window = positive(args.window)
    return [row for row in transports(window) if row.landed and row.closed and row.age_days > window]


def transport(args: argparse.Namespace) -> int:
    window = positive(args.window)
    rows = transports(window)
    retired = [row for row in rows if row.landed and row.closed and row.age_days > window]
    print(
        f"transport total={len(rows)} landed={sum(row.landed for row in rows)} "
        f"closed={sum(row.closed for row in rows)} candidates={len(retired)}"
    )
    for row in retired:
        print(f"{row.ref}\tsha={row.sha}\tage={row.age_days}\tclosed={str(row.closed).lower()}")
    return 0


def retire(args: argparse.Namespace) -> int:
    if not args.dry_run:
        identity()
    rows = candidates(args)
    retired = 0
    finding = False
    for row in rows:
        archive = f"refs/archive/dispatch/{row.record_id}"
        if args.dry_run:
            print(f"PLAN archive {row.ref} -> {archive} sha={row.sha}")
            continue
        written = git("update-ref", archive, row.sha, "")
        if resolves(archive) != row.sha:
            detail = written.stderr.strip() or "archive ref did not resolve to source tip"
            print(f"finding: kept {row.ref}; archive failed: {' '.join(detail.split())}", file=sys.stderr)
            finding = True
            continue
        deleted = git("update-ref", "-d", row.ref, row.sha)
        if deleted.returncode != 0:
            detail = deleted.stderr.strip() or f"git exited {deleted.returncode}"
            print(f"finding: kept {row.ref}; delete failed: {' '.join(detail.split())}", file=sys.stderr)
            finding = True
            continue
        retired += 1
        print(f"RETIRED {row.ref} -> {archive} sha={row.sha}")
    print(f"retired n={retired}")
    return 1 if finding else 0


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        refuse("arguments", "a supported verb and options", message, "run branches.py --help")


def parser() -> Parser:
    root = Parser(description=__doc__)
    commands = root.add_subparsers(dest="verb", required=True)
    census_parser = commands.add_parser("census")
    census_parser.add_argument("--json", action="store_true")
    census_parser.add_argument("--window", default="14")
    commands.add_parser("stacked")
    transport_parser = commands.add_parser("transport")
    transport_parser.add_argument("--window", default="30")
    retire_parser = commands.add_parser("retire")
    retire_parser.add_argument("--window", default="30")
    retire_parser.add_argument("--dry-run", action="store_true")
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        require_repo()
        handlers = {
            "census": census,
            "stacked": stacked,
            "transport": transport,
            "retire": retire,
        }
        return handlers[args.verb](args)
    except Refusal as error:
        return emit(error)


if __name__ == "__main__":
    raise SystemExit(main())
