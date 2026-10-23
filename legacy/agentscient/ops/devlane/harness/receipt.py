#!/usr/bin/env python3
"""Receipts: a claim anyone can re-derive, written per ``evidence.cue``.

Verbs:
    receipt.py record --claim TEXT [--cwd REL] [--allow-dirty] -- ARGV...
    receipt.py verify RECEIPT
    receipt.py show RECEIPT

``record`` runs ARGV at CWD (relative to the repository root), captures
exit code, both outputs and their digests, the head the tree was at and
whether it was dirty, and writes a ``#Receipt`` to
``.dev/records/receipts/<digest>.json`` whose name is the canonical
digest of the receipt itself. Exit 0 when the command passed, 1 when it
failed (the receipt is written either way: a red receipt is evidence),
2 on a refusal, one stderr line on the wire.

``verify`` re-runs a receipt's command at the recorded head and compares
exit code and output digests: 0 when they agree, 1 naming each field that
differs, 2 when the tree is not at the recorded head.

Adapted from the previous repository's workflow engine (``wf red``,
``wf green``, ``wf verify``): the receipt shape is the contract's closed
struct, not the engine's event; the store is a directory of files, not a
chain. The modules under ``receipt/`` come from that engine; ``receipts.run``
is adapted here to preserve raw output bytes and omit argv from refusals.

Adaptations declared in the extraction checklist
(``.dev/ladder-inputs/extraction-checklist-receipts-20260911.md``):
``head_sha`` is the sha256 of the commit id's hex, since the contract wants
a 64-hex digest and git here gives 40; ``cwd`` is repository-relative;
``actor`` is ``WF_AGENT``; the refusal wire is this lane's.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "receipt"))
import canonical  # noqa: E402
import receipts  # noqa: E402
from errors import Refusal  # noqa: E402

RECEIPTS_DIR = Path(".dev") / "records" / "receipts"
FIELDS = ("kind", "claim", "argv", "cwd", "head_sha", "tree_dirty", "exit_code",
          "duration_ms", "stdout_sha256", "stderr_sha256", "actor", "at")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Refused(Exception):
    pass


def clean(value):
    return " ".join(str(value).split())[:500]


def refuse(kind, expected, found, needed):
    raise Refused(f"{kind}: expected {clean(expected)}; found {clean(found)}; needed {clean(needed)}")


def identity():
    value = os.environ.get("WF_AGENT", "").strip()
    match = re.fullmatch(r"(?P<name>[^<>]+?)\s+<(?P<address>[^<>\s]+)>", value)
    if match is None or not match.group("name").strip():
        refuse("identity", "WF_AGENT as Name <address> for a writing verb", value or "absent identity",
               "export WF_AGENT='Name <address>'")
    return value


def repo_root(start: Path) -> Path:
    proc = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        refuse("repository", "a git work tree", str(start), "run inside a clone")
    return Path(proc.stdout.strip())


def head_digest(repo: Path) -> str:
    proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        refuse("head", "a commit at HEAD", "no commit", "commit once before recording a receipt")
    return hashlib.sha256(proc.stdout.strip().encode("ascii")).hexdigest()


def dirty_paths(repo: Path) -> list[str]:
    """Paths that differ from HEAD, the receipts directory itself excluded."""
    proc = subprocess.run(["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        refuse("repository", "git status to read the working tree", f"git status exited {proc.returncode}",
               "repair the repository index, then retry")
    own = RECEIPTS_DIR.as_posix()
    raw = proc.stdout
    fields = [f for f in raw.split("\0") if f]
    out, index = [], 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        status, path = entry[:2], entry[3:]
        if status[0] in ("R", "C"):
            index += 1
        normalized = path.rstrip("/")
        is_receipt_state = normalized == own or normalized.startswith(own + "/")
        if not is_receipt_state:
            out.append(path)
    return sorted(out)


def relative_cwd(repo: Path, cwd: str, refusal_kind: str = "cwd") -> str:
    candidate = Path(cwd)
    if candidate.is_absolute():
        refuse(refusal_kind, "a path relative to the repository root", cwd,
               "give the path relative to the root")
    target = (repo / candidate).resolve()
    if not target.is_relative_to(repo.resolve()) or not target.is_dir():
        refuse(refusal_kind, "a directory inside the repository", cwd, "give a directory under the root")
    normalized = target.relative_to(repo.resolve()).as_posix() or "."
    if cwd != normalized:
        refuse(refusal_kind, "a normalized path relative to the repository root", cwd,
               f"use {normalized}")
    return normalized


def receipt_directory(repo: Path) -> Path:
    root = repo.resolve()
    directory = repo / RECEIPTS_DIR
    candidate = repo
    for part in RECEIPTS_DIR.parts:
        candidate /= part
        if candidate.is_symlink():
            refuse("receipt", "a receipt directory with no symlink components",
                   candidate.relative_to(repo), "replace the symlink with a directory inside the repository")
    try:
        resolved = directory.resolve()
    except OSError as error:
        refuse("receipt", "a resolvable receipt directory", error, "repair the receipt directory path")
    if not resolved.is_relative_to(root):
        refuse("receipt", "a receipt directory inside the repository", resolved,
               "use .dev/records/receipts inside the repository")
    return directory


def write_receipt(repo: Path, body: dict) -> tuple[str, Path]:
    digest = canonical.digest(body)
    directory = receipt_directory(repo)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        refuse("receipt", "a writable receipt directory", error, "repair .dev/records/receipts")
    path = directory / f"{digest}.json"
    try:
        path.write_text(canonical.dumps(body) + "\n", encoding="utf-8")
    except OSError as error:
        refuse("receipt", "a writable receipt file", error, "repair .dev/records/receipts")
    return digest, path


def load_receipt(repo: Path, ref: str) -> tuple[dict, Path]:
    directory = receipt_directory(repo).resolve()
    path = directory / f"{ref}.json" if DIGEST.fullmatch(ref) else Path(ref)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        refuse("receipt", "a receipt file or digest under " + RECEIPTS_DIR.as_posix(), ref, "name a recorded receipt")
    if not resolved.is_file():
        refuse("receipt", "a receipt file or digest under " + RECEIPTS_DIR.as_posix(), ref,
               "name a recorded receipt")
    in_receipts_directory = resolved.is_relative_to(directory)
    path = resolved
    try:
        body = canonical.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        refuse("receipt", "canonical JSON", f"{path}: {error}", "restore the receipt bytes")
    if not isinstance(body, dict):
        refuse("receipt", "a JSON object containing #Receipt", type(body).__name__,
               "use a receipt written by this lever")
    missing = [f for f in FIELDS if f not in body]
    extra = [k for k in body if k not in FIELDS]
    if body.get("kind") != "receipt" or missing or extra:
        refuse("receipt", "the twelve fields of #Receipt", f"kind={body.get('kind')!r} missing={missing} extra={extra}",
               "a receipt written by this lever")
    digests = (body["head_sha"], body["stdout_sha256"], body["stderr_sha256"])
    valid = (
        isinstance(body["claim"], str) and body["claim"] != ""
        and isinstance(body["argv"], list) and bool(body["argv"])
        and all(isinstance(item, str) and item != "" for item in body["argv"])
        and isinstance(body["cwd"], str) and body["cwd"] != ""
        and all(isinstance(value, str) and DIGEST.fullmatch(value) for value in digests)
        and type(body["tree_dirty"]) is bool
        and type(body["exit_code"]) is int
        and type(body["duration_ms"]) is int and body["duration_ms"] >= 0
        and isinstance(body["actor"], str) and body["actor"] != ""
        and isinstance(body["at"], str) and body["at"] != ""
    )
    if not valid:
        refuse("receipt", "field types and values conforming to #Receipt", "an invalid receipt field",
               "use a receipt written by this lever")
    relative_cwd(repo, body["cwd"], "receipt" if in_receipts_directory else "cwd")
    if not in_receipts_directory:
        refuse("receipt", "a receipt file or digest under " + RECEIPTS_DIR.as_posix(), ref,
               "name a recorded receipt")
    digest = canonical.digest(body)
    if path.stem != digest:
        refuse("receipt", f"canonical digest {path.stem}", f"canonical digest {digest}",
               "restore the receipt body named by the file")
    return body, path


def cmd_record(args) -> int:
    if not isinstance(args.claim, str) or args.claim == "":
        refuse("arguments", "a non-empty --claim", "an empty or absent claim", "record --claim TEXT -- ARGV...")
    if not args.argv or any(not isinstance(item, str) or item == "" for item in args.argv):
        refuse("arguments", "a command with non-empty argv elements after --", "an empty command argument",
               "record --claim TEXT -- ARGV...")
    actor = identity()
    repo = repo_root(Path.cwd())
    cwd = relative_cwd(repo, args.cwd)
    dirty = dirty_paths(repo)
    if dirty and not args.allow_dirty:
        refuse("dirty", "a clean tree, so the receipt names a commit", f"{len(dirty)} changed path(s): {' '.join(dirty[:5])}",
               "commit first, or --allow-dirty to record tree_dirty true (not admissible)")
    head = head_digest(repo)
    receipt_directory(repo)
    code, out, err, duration = receipts.run(list(args.argv), repo / cwd, binary=True)
    body = {
        "kind": "receipt",
        "claim": args.claim,
        "argv": list(args.argv),
        "cwd": cwd,
        "head_sha": head,
        "tree_dirty": bool(dirty),
        "exit_code": int(code),
        "duration_ms": int(duration),
        "stdout_sha256": canonical.sha256_hex(out),
        "stderr_sha256": canonical.sha256_hex(err),
        "actor": actor,
        "at": canonical.now(),
    }
    digest, path = write_receipt(repo, body)
    print(f"receipt {digest} exit={code} head={head[:8]} dirty={str(bool(dirty)).lower()} "
          f"path={path.relative_to(repo).as_posix()}")
    return 0 if code == 0 else 1


def cmd_verify(args) -> int:
    if not args.receipt:
        refuse("arguments", "a receipt file or digest", "no receipt", "verify RECEIPT")
    repo = repo_root(Path.cwd())
    body, path = load_receipt(repo, args.receipt)
    cwd = relative_cwd(repo, body["cwd"])
    head = head_digest(repo)
    if head != body["head_sha"]:
        refuse("state", f"the tree at the recorded head {body['head_sha'][:8]}", f"head {head[:8]}",
               "check out the recorded commit, then verify")
    code, out, err, _duration = receipts.run(list(body["argv"]), repo / cwd, binary=True)
    differences = []
    if int(code) != body["exit_code"]:
        differences.append(f"exit_code recorded {body['exit_code']} now {code}")
    for name, output in (("stdout_sha256", out), ("stderr_sha256", err)):
        now = canonical.sha256_hex(output)
        if now != body[name]:
            differences.append(f"{name} recorded {body[name][:8]} now {now[:8]}")
    if differences:
        print(f"differs {path.stem}: " + "; ".join(differences))
        return 1
    print(f"verified {path.stem} exit={code}")
    return 0


def cmd_show(args) -> int:
    if not args.receipt:
        refuse("arguments", "a receipt file or digest", "no receipt", "show RECEIPT")
    repo = repo_root(Path.cwd())
    body, _path = load_receipt(repo, args.receipt)
    print(canonical.dumps(body))
    return 0


class WireParser(argparse.ArgumentParser):
    def error(self, _message):
        refuse("arguments", "valid receipt arguments", "invalid command arguments",
               "follow the documented receipt syntax")


def parser():
    p = WireParser(add_help=False)
    verbs = p.add_subparsers(dest="verb")
    rec = verbs.add_parser("record", add_help=False)
    rec.add_argument("--claim")
    rec.add_argument("--cwd", default=".")
    rec.add_argument("--allow-dirty", action="store_true")
    rec.add_argument("argv", nargs=argparse.REMAINDER)
    rec.set_defaults(run=cmd_record)
    ver = verbs.add_parser("verify", add_help=False)
    ver.add_argument("receipt", nargs="?")
    ver.set_defaults(run=cmd_verify)
    show = verbs.add_parser("show", add_help=False)
    show.add_argument("receipt", nargs="?")
    show.set_defaults(run=cmd_show)
    return p


def main(argv=None) -> int:
    try:
        args, unknown = parser().parse_known_args(argv)
        if unknown or not getattr(args, "run", None):
            refuse("arguments", "record, verify or show", "unsupported arguments" if unknown else "no verb",
                   "choose one documented verb")
        if args.verb == "record" and args.argv and args.argv[0] == "--":
            args.argv = args.argv[1:]
        return args.run(args)
    except Refused as error:
        print(str(error), file=sys.stderr)
        return 2
    except Refusal as error:
        print(f"command: expected {clean(error.precondition)}; found {clean(error.actual)}; "
              f"needed {clean(error.needed)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
