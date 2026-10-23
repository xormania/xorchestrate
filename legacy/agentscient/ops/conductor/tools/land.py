#!/usr/bin/env python3
"""Land a completed dispatch onto its lineage branch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import (
        IDENTITY,
        Refusal,
        conf,
        emit,
        git,
        identity_parts,
        refuse,
        state_dir,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None


def git_value(kind: str, expected: str, needed: str, *args: str) -> str:
    result = git(*args)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        refuse(kind, expected, detail, needed)
    return result.stdout.strip()


def dispatch_root() -> Path:
    return state_dir() / "dispatch"


def option_identity(value: str, kind: str) -> tuple[str, str]:
    """Parse an optional CLI identity on the refusal wire."""
    match = IDENTITY.fullmatch(value)
    if match is None:
        refuse(
            kind,
            f"--{kind} in the Name <address> form",
            value or "empty",
            f"pass --{kind} 'Name <address>'",
        )
    return match.group("name"), match.group("address")


def record_exit(record: Path) -> tuple[bool, str]:
    exit_file = record / "exit"
    try:
        value = exit_file.read_text().strip()
    except OSError:
        value = "missing"
    try:
        successful = int(value) == 0
    except ValueError:
        successful = False
    return successful, value or "empty"


def refuse_exit(found: str, needed: str) -> NoReturn:
    refuse("exit-status", "record exit file containing 0", found, needed)


def read_envelope(record_id: str) -> tuple[str, dict]:
    path = Path(".dev/records/dispatches") / f"{record_id}.json"
    if not path.is_file():
        refuse(
            "envelope",
            f"working-tree envelope {path}",
            "absent",
            "record the dispatch envelope on the lineage branch",
        )
    try:
        data = json.loads(path.read_text())
        branch = data["lineage"]["branch"]
        if not isinstance(branch, str) or not branch:
            raise TypeError
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        refuse(
            "envelope",
            "an envelope with a non-empty lineage.branch",
            f"invalid {path}",
            "repair the recorded dispatch envelope",
        )
    return branch, data


def collection_marker(record_id: str) -> bool:
    path = Path(".dev/records/dispatches") / f"{record_id}.json"
    try:
        data = json.loads(path.read_text())
        result = data.get("result") or {}
    except (OSError, json.JSONDecodeError, AttributeError):
        return False
    return isinstance(result, dict) and "collected" in result


def validated_collection(data: dict, ref_head: str) -> bool:
    result = data.get("result") or {}
    collected = result.get("collected") if isinstance(result, dict) else None
    if collected is None:
        return False
    verify = collected.get("verify") if isinstance(collected, dict) else None
    verify_exit = verify.get("exit") if isinstance(verify, dict) else None
    if type(verify_exit) is not int or verify_exit != 0:
        refuse_exit(
            f"result.collected.verify.exit {verify_exit!r}",
            "rerun launch.py collect with a verification that exits 0",
        )
    head = collected.get("head")
    if head != ref_head:
        refuse(
            "ref",
            f"result.collected.head matching dispatch ref {ref_head}",
            repr(head),
            "restore the collected dispatch ref or recollect the result",
        )
    return True


def preconditions(
    record_id: str, no_fetch: bool
) -> tuple[list[str], str, str, str, str, str]:
    lane_author = conf("LANE_AUTHOR")
    match = IDENTITY.fullmatch(lane_author)
    if match is None:
        refuse(
            "conf-value",
            "LANE_AUTHOR in the Name <address> form",
            repr(lane_author),
            "correct LANE_AUTHOR in the lane conf",
        )
    author_name, author_email = match.group("name"), match.group("address")
    name, email = identity_parts()
    record = dispatch_root() / record_id
    if not record.is_dir():
        refuse(
            "record",
            f"dispatch record directory {record}",
            "absent",
            "close or choose an existing dispatch record",
        )
    successful_exit, exit_value = record_exit(record)
    if not successful_exit and not collection_marker(record_id):
        refuse_exit(
            exit_value,
            f"run launch.py collect {record_id} --verify COMMAND before landing it",
        )

    ref = f"refs/dispatch/{record_id}"
    ref_head = git_value(
        "ref", f"resolvable {ref}", "restore the dispatch ref", "rev-parse", "--verify", ref
    )
    lineage, data = read_envelope(record_id)
    collected = validated_collection(data, ref_head)
    if not successful_exit and not collected:
        refuse_exit(
            exit_value,
            f"run launch.py collect {record_id} --verify COMMAND before landing it",
        )
    env_status = ((data.get("result") or {}).get("envelope") or {}).get("status")
    if env_status != "ok" and not collected:
        refuse(
            "envelope-status",
            "a dispatch envelope recorded ok",
            repr(env_status),
            f"run launch.py collect {record_id} --verify COMMAND before landing it",
        )
    branch = git_value(
        "lineage",
        f"current branch {lineage}",
        f"check out lineage branch {lineage}",
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
    )
    if branch != lineage:
        refuse(
            "lineage",
            f"current branch {lineage}",
            branch,
            f"check out lineage branch {lineage}",
        )
    dirty = git_value(
        "dirty", "a readable clean working tree", "make git status succeed", "status", "--porcelain"
    )
    if dirty:
        refuse("dirty", "a clean working tree", "local changes", "commit or stash the changes")

    # Freshness is a three-way ruling. With a remote and a fetch: probe
    # the upstream branch first — an upstream that lacks the branch is
    # fresh evidence (a first landing on an unpushed branch), any other
    # probe or fetch failure refuses (an unperformed freshness check is
    # not freshness). With --no-fetch: the last-known origin ref is the
    # comparison, and its absence refuses — skipping the fetch presumes
    # prior evidence. With no remote at all: a planted origin ref
    # compares, otherwise there is nothing to be behind.
    has_remote = git("remote", "get-url", "origin").returncode == 0
    compare = False
    if has_remote and not no_fetch:
        probe = git(
            "ls-remote", "--exit-code", "origin", f"refs/heads/{lineage}"
        )
        if probe.returncode == 0:
            fetched = git("fetch", "origin", lineage)
            if fetched.returncode != 0:
                detail = " ".join(
                    (fetched.stderr.strip()
                     or f"git fetch exited {fetched.returncode}").split()
                )
                refuse(
                    "stale",
                    f"a completed fetch of origin {lineage}",
                    detail,
                    "restore the remote or pass --no-fetch after verifying freshness",
                )
            compare = True
        elif probe.returncode != 2:
            detail = " ".join(
                (probe.stderr.strip()
                 or f"git ls-remote exited {probe.returncode}").split()
            )
            refuse(
                "stale",
                f"a reachable origin to probe for {lineage}",
                detail,
                "restore the remote or pass --no-fetch after verifying freshness",
            )
    else:
        origin_ref = git(
            "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{lineage}"
        )
        if origin_ref.returncode == 0:
            compare = True
        elif has_remote:
            refuse(
                "stale",
                f"a last-known origin/{lineage} to compare against",
                "no such ref under --no-fetch",
                "run without --no-fetch so freshness can be measured",
            )
    if compare:
        behind_text = git_value(
            "stale",
            f"origin/{lineage} available for comparison",
            f"fetch origin branch {lineage}",
            "rev-list",
            "--count",
            f"HEAD..origin/{lineage}",
        )
        try:
            behind = int(behind_text)
        except ValueError:
            refuse("stale", "an integer behind count", repr(behind_text), "repair the git repository")
        if behind:
            refuse(
                "stale",
                f"branch not behind origin/{lineage}",
                f"behind by {behind} commit(s)",
                f"update {lineage} from origin before landing",
            )

    commits_text = git_value(
        "ref",
        f"commits in HEAD...{ref}",
        "repair the dispatch ref",
        "rev-list",
        "--reverse",
        "--right-only",
        "--cherry-pick",
        f"HEAD...{ref}",
    )
    commits = commits_text.splitlines() if commits_text else []
    for commit in commits:
        trailers = git_value(
            "attribution",
            f"a git-parseable trailer on {commit}",
            "add the required attribution trailer to the producer commit",
            "show",
            "-s",
            "--format=%(trailers:only,unfold)",
            commit,
        )
        if not trailers:
            refuse(
                "attribution",
                f"a git-parseable trailer on {commit}",
                "none",
                "add the required attribution trailer to the producer commit",
            )
    return commits, ref, name, email, author_name, author_email


def land(args: argparse.Namespace) -> int:
    commits, ref, name, email, author_name, author_email = preconditions(
        args.record_id, args.no_fetch
    )
    lane_author = f"{author_name} <{author_email}>"
    if args.author is not None:
        author_name, author_email = option_identity(args.author, "author")
    co_author = None
    if args.co_author is not None:
        co_name, co_email = option_identity(args.co_author, "co-author")
        co_author = f"{co_name} <{co_email}>"
    if not commits:
        print("landed n=0")
        return 0

    identity_args = ["-c", f"user.name={name}", "-c", f"user.email={email}"]
    for index, commit in enumerate(commits):
        subject = git_value(
            "ref", f"subject for {commit}", "repair the dispatch ref", "show", "-s", "--format=%s", commit
        )
        result = git(*identity_args, "cherry-pick", commit)
        if result.returncode != 0:
            git("cherry-pick", "--abort")
            remainder = len(commits) - index
            # The outcome line is a result, not a diagnostic: stdout.
            print(f"abort conflict sha={commit} remainder={remainder}")
            return 3
        landed_author = git_value(
            "ref", "the landed author", "repair the repository", "show", "-s",
            "--format=%an <%ae>", "HEAD",
        )
        amend_args = ["commit", "--amend", "--no-edit"]
        needs_amend = False
        if (landed_author != lane_author
                and landed_author != f"{author_name} <{author_email}>"):
            amend_args.append(f"--author={author_name} <{author_email}>")
            needs_amend = True
        if co_author is not None:
            message = git_value(
                "ref", "the landed message", "repair the repository", "show",
                "-s", "--format=%B", "HEAD",
            )
            trailer = f"Co-Authored-By: {co_author}"
            if trailer not in message.splitlines():
                amend_args.extend(["--trailer", trailer])
                needs_amend = True
        if needs_amend:
            amended = git(
                *identity_args,
                *amend_args,
            )
            if amended.returncode != 0:
                detail = (
                    amended.stderr.strip()
                    or f"git commit exited {amended.returncode}"
                )
                refuse(
                    "author",
                    f"landed commit author {author_name} <{author_email}>",
                    detail,
                    "repair the repository and retry the landing",
                )
        landed = git_value(
            "ref", "the landed commit", "repair the repository", "rev-parse", "HEAD"
        )
        print(f"LANDED\t{landed}\t{subject}")
    print(f"landed n={len(commits)} ref={ref}")

    if args.push:
        result = git("push", "origin", "HEAD")
        if result.returncode != 0:
            detail = result.stderr.strip() or f"git push exited {result.returncode}"
            print(f"finding: landing kept; push failed: {detail}", file=sys.stderr)
            return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record_id", metavar="RECORD_ID")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--author")
    parser.add_argument("--co-author")
    return parser.parse_args()


def main() -> int:
    try:
        return land(parse_args())
    except Refusal as refusal:
        return emit(refusal)


if __name__ == "__main__":
    raise SystemExit(main())
