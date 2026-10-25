#!/usr/bin/env python3
# testkit: exempt planted PATH stub of gh; copied onto a World's bin, not a suite module
"""PATH-stub gh for the PR overview tests. State is GH_STUB_STATE JSON."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# The planted copy lives on a World's bin, so the kit is named by
# TESTKIT_DIR (support sets it) rather than by this file's original path.
_kit = os.environ.get("TESTKIT_DIR")
if _kit:
    sys.path.insert(0, _kit)
    from fake_program import log_call

    _log = os.environ.get("GH_STUB_LOG")
    if _log:
        log_call(_log, sys.argv[1:], "")

state = json.loads(Path(os.environ["GH_STUB_STATE"]).read_text())
args = sys.argv[1:]
prs = state["prs"]
trees = state.get("trees", {})

def git_blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324

blobs = {}
for files in trees.values():
    for text in files.values():
        data = text.encode("utf-8")
        blobs[git_blob_sha(data)] = data

def pr_by_number(n):
    for pr in prs:
        if int(pr["number"]) == int(n):
            return pr
    sys.stderr.write(f"unknown pr {n}\n")
    sys.exit(2)

def dump(obj):
    jq = None
    if "--jq" in args:
        jq = args[args.index("--jq") + 1]
    if jq:
        cur = obj
        for part in jq.strip(".").split("."):
            if part == "":
                continue
            cur = cur[part]
        if isinstance(cur, (dict, list)):
            json.dump(cur, sys.stdout)
        else:
            sys.stdout.write("" if cur is None else str(cur))
    else:
        json.dump(obj, sys.stdout)
    sys.exit(0)

if "repo" in args and "view" in args:
    repo = state.get("repo") or "uscient/agentscient"
    json.dump({"nameWithOwner": repo}, sys.stdout)
    sys.exit(0)

if "pr" in args and "list" in args:
    keys = (
        "number", "title", "headRefName", "baseRefName", "headRefOid",
        "mergeable", "mergeStateStatus", "isDraft", "updatedAt",
    )
    json.dump([{k: pr[k] for k in keys} for pr in prs], sys.stdout)
    sys.exit(0)

if "pr" in args and "checks" in args:
    number = None
    after = args[args.index("checks") + 1 :]
    for a in after:
        if a.isdigit():
            number = a
            break
    if number is None:
        sys.stderr.write("pr checks: no number\n")
        sys.exit(2)
    errors = state.get("check_errors") or {}
    if str(number) in errors:
        err = errors[str(number)]
        stderr = err.get("stderr") or ""
        if stderr and not stderr.endswith("\n"):
            stderr = stderr + "\n"
        sys.stderr.write(stderr)
        sys.stdout.write(err.get("stdout") or "")
        sys.exit(int(err["rc"]))
    pr = pr_by_number(number)
    json.dump(
        [{"name": c["name"], "bucket": c["bucket"]} for c in pr["checks"]],
        sys.stdout,
    )
    sys.exit(0)

if "api" in args and "graphql" in args:
    number = None
    for i, a in enumerate(args):
        if a.startswith("pr="):
            number = a.split("=", 1)[1]
        if a == "-F" and i + 1 < len(args) and args[i + 1].startswith("pr="):
            number = args[i + 1].split("=", 1)[1]
    if number is None:
        sys.stderr.write("graphql: no pr=\n")
        sys.exit(2)
    errors = state.get("review_errors") or {}
    if str(number) in errors:
        err = errors[str(number)]
        stderr = err.get("stderr") or ""
        if stderr and not stderr.endswith("\n"):
            stderr = stderr + "\n"
        sys.stderr.write(stderr)
        sys.stdout.write(err.get("stdout") or "")
        sys.exit(int(err["rc"]))
    pr = pr_by_number(number)
    json.dump(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [],
                        },
                        "reviews": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [{
                                "submittedAt": "2026-08-26T00:00:00Z",
                                "commit": {"oid": pr["headRefOid"]},
                                "author": {"login": "reviewer"},
                                "state": "APPROVED",
                                "body": "lgtm",
                            }],
                        },
                    }
                }
            }
        },
        sys.stdout,
    )
    sys.exit(0)

def api_token():
    for a in args:
        if a.startswith("repos/") or a.startswith("/repos/"):
            return a
    return None

token = api_token() if "api" in args else None
if token:
    parsed = urlparse(token if "://" in token else "https://api.github.com/" + token.lstrip("/"))
    path = parsed.path.lstrip("/")
    qs = parse_qs(parsed.query)
    for i, a in enumerate(args):
        if a in ("-f", "-F") and i + 1 < len(args) and "=" in args[i + 1]:
            k, v = args[i + 1].split("=", 1)
            qs.setdefault(k, []).append(v)
        if a.startswith("ref="):
            qs.setdefault("ref", []).append(a.split("=", 1)[1])
    parts = path.split("/")
    rest = parts[3:] if parts and parts[0] == "repos" else parts

    def die(msg, code=1):
        sys.stderr.write(msg + "\n")
        sys.exit(code)

    def tree_files(sha):
        files = trees.get(sha)
        if files is None:
            die(f"no tree {sha}", 1)
        return files

    if rest[:2] == ["git", "trees"] and len(rest) >= 3:
        sha = rest[2]
        files = tree_files(sha)
        tree = []
        for p, text in sorted(files.items()):
            data = text.encode("utf-8")
            tree.append({
                "path": p,
                "mode": "100644",
                "type": "blob",
                "sha": git_blob_sha(data),
                "size": len(data),
            })
        dump({"sha": sha, "tree": tree, "truncated": False})

    if rest[:2] == ["git", "blobs"] and len(rest) >= 3:
        bsha = rest[2]
        data = blobs.get(bsha)
        if data is None:
            die(f"no blob {bsha}", 1)
        dump({
            "sha": bsha,
            "content": base64.b64encode(data).decode("ascii"),
            "encoding": "base64",
            "size": len(data),
        })

    if rest[:2] == ["git", "commits"] and len(rest) >= 3:
        sha = rest[2]
        tree_files(sha)
        dump({"sha": sha, "tree": {"sha": sha}})

    if rest[:1] == ["tarball"] and len(rest) >= 2:
        sha = rest[1]
        files = tree_files(sha)
        buf = io.BytesIO()
        prefix = f"agentscient-{sha[:7]}/"
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for p, text in files.items():
                data = text.encode("utf-8")
                info = tarfile.TarInfo(name=prefix + p)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        sys.stdout.buffer.write(buf.getvalue())
        sys.exit(0)

    if rest[:1] == ["contents"]:
        rel = "/".join(rest[1:])
        ref = (qs.get("ref") or [None])[0]
        if not ref:
            die("contents: no ref", 2)
        files = tree_files(ref)
        if rel in files:
            data = files[rel].encode("utf-8")
            dump({
                "name": rel.rsplit("/", 1)[-1],
                "path": rel,
                "sha": git_blob_sha(data),
                "size": len(data),
                "type": "file",
                "content": base64.b64encode(data).decode("ascii"),
                "encoding": "base64",
            })
        prefix = (rel.rstrip("/") + "/") if rel else ""
        names = {}
        for p in files:
            if prefix and not p.startswith(prefix):
                continue
            rest_p = p[len(prefix):] if prefix else p
            if not rest_p:
                continue
            first = rest_p.split("/", 1)[0]
            names[first] = "/" not in rest_p
        if not names:
            die(f"no content {rel}", 1)
        listing = []
        for name in sorted(names):
            full = prefix + name
            if names[name]:
                data = files[full].encode("utf-8")
                listing.append({
                    "name": name,
                    "path": full,
                    "type": "file",
                    "sha": git_blob_sha(data),
                    "size": len(data),
                })
            else:
                listing.append({
                    "name": name,
                    "path": full,
                    "type": "dir",
                    "sha": "0" * 40,
                })
        dump(listing)

sys.stderr.write("stub gh: unhandled " + " ".join(args[:6]) + "\n")
sys.exit(2)
