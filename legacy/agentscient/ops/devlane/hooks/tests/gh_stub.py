#!/usr/bin/env python3
"""PATH-stub `gh` for pr-feedback tests.

Serves fixture JSON through real jq (the same `--jq` the real client uses).
No network. Failure, per-endpoint failure, and GraphQL `first:` paging are
driven by environment so the test can prove the plant before the tool runs.

Env:
  GH_FIXTURES       directory of fixture files (required)
  GH_MODE_FILE      file whose contents are `ok` or `fail` (default ok)
  GH_FAIL           `1` forces every call to fail
  GH_FAIL_RC        exit status when failing (default 1)
  GH_FAIL_STDERR    stderr body when failing (default auth-failed text)
  GH_FAIL_ENDPOINT  substring of the API path that should fail
  GH_THREAD_COUNT   if set, synthesize N unresolved reviewThreads
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import types
from pathlib import Path


def _kit_dir() -> Path | None:
    """ops/testkit, named by TESTKIT_DIR when this file is copied onto PATH."""
    env = os.environ.get("TESTKIT_DIR")
    if env and (Path(env) / "fake_program.py").is_file():
        return Path(env)
    here = Path(__file__).resolve()
    if here.parent.name == "tests":
        candidate = here.parents[3] / "testkit"
        if (candidate / "fake_program.py").is_file():
            return candidate
    return None


def _load_kit():
    """(run, log_call) from the kit. Suites run from the repo root import the
    package; a copy on PATH loads the same modules by file, no sys.path insert.
    Kit gap: Tools.plant does not put ops.testkit on a copied stub's path."""
    try:
        from ops.testkit import run as kit_run
        from ops.testkit.fake_program import log_call as kit_log

        return kit_run, kit_log
    except ImportError:
        pass
    kit = _kit_dir()
    if kit is None:
        sys.stderr.write(
            "arguments: expected TESTKIT_DIR naming ops/testkit; "
            "found unset; needed the kit directory isolated_env sets\n"
        )
        sys.exit(2)
    ops = sys.modules.setdefault("ops", types.ModuleType("ops"))
    if not hasattr(ops, "__path__"):
        ops.__path__ = [str(kit.parent)]
    if "ops.testkit" not in sys.modules:
        tk = types.ModuleType("ops.testkit")
        tk.__path__ = [str(kit)]
        sys.modules["ops.testkit"] = tk

    def load(name: str, filename: str):
        full = f"ops.testkit.{name}"
        if full in sys.modules:
            return sys.modules[full]
        spec = importlib.util.spec_from_file_location(full, kit / filename)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
        return mod

    load("world", "world.py")
    command = load("command", "command.py")
    fake = load("fake_program", "fake_program.py")
    return command.run, fake.log_call


run, log_call = _load_kit()


def _fail(fix: Path) -> None:
    msg = os.environ.get("GH_FAIL_STDERR", "gh: authentication failed")
    if msg:
        sys.stderr.write(msg + "\n")
    log(fix, f"FAIL rc={os.environ.get('GH_FAIL_RC', '1')}")
    sys.exit(int(os.environ.get("GH_FAIL_RC", "1")))


def log(fix: Path, line: str) -> None:
    """One line appended to calls.log with a single write, as the kit's
    recorder appends its JSON line: never a read-and-rewrite (LD-8)."""
    fd = os.open(str(fix / "calls.log"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
    finally:
        os.close(fd)


def bump(fix: Path) -> int:
    """The call's ordinal: the kit's recorder appends the JSON line to
    calls.jsonl, and the count is the log's length. callcount is written
    for the tests that read it, from that length, never from itself."""
    log_call(str(fix / "calls.jsonl"), sys.argv[1:], "")
    n = sum(1 for ln in (fix / "calls.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip())
    (fix / "callcount").write_text(str(n) + "\n", encoding="utf-8")
    return n


def mode_is_fail(fix: Path) -> bool:
    if os.environ.get("GH_FAIL") == "1":
        return True
    mode_file = os.environ.get("GH_MODE_FILE")
    if mode_file:
        p = Path(mode_file)
        if p.is_file() and p.read_text(encoding="utf-8").strip() == "fail":
            return True
    return False


def parse_api_argv(argv: list[str]) -> tuple[str, str | None, dict, str, bool]:
    """Return endpoint, jq expr, -f variables, joined tail, --paginate."""
    jqexpr = None
    variables: dict[str, str] = {}
    paginate = False
    endpoint = ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--jq", "-q") and i + 1 < len(argv):
            jqexpr = argv[i + 1]
            i += 2
            continue
        if a.startswith("--jq="):
            jqexpr = a.split("=", 1)[1]
            i += 1
            continue
        if a in ("-f", "-F") and i + 1 < len(argv):
            kv = argv[i + 1]
            if "=" in kv:
                k, v = kv.split("=", 1)
                variables[k] = v
            i += 2
            continue
        if a == "--paginate":
            paginate = True
            i += 1
            continue
        if not a.startswith("-") and not endpoint:
            endpoint = a
            i += 1
            continue
        i += 1
    return endpoint, jqexpr, variables, " ".join(argv), paginate


def thread_node(i: int) -> dict:
    return {
        "id": f"TH{i}",
        "isResolved": False,
        "isOutdated": False,
        "path": f"thread-{i}-unique.py",
        "line": i,
        "comments": {
            "nodes": [
                {
                    "id": f"TC{i}",
                    "body": f"THREAD-{i}-UNIQUE",
                    "author": {"login": "bot"},
                }
            ]
        },
    }


def threads_page(total: int, first: int | None, after: str, paginate: bool) -> dict:
    if total <= 0:
        nodes: list[dict] = []
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {
                                "hasNextPage": False,
                                "endCursor": None,
                            },
                            "nodes": nodes,
                        }
                    }
                }
            }
        }
    start = 1
    if after.startswith("c") and after[1:].isdigit():
        start = int(after[1:]) + 1
    # Live GraphQL demands a bound; first:50 is the finding. Omitting
    # first must not dump the whole set and look like paging.
    if paginate:
        end = total
    else:
        bound = 50 if first is None else first
        end = min(total, start + bound - 1)
    if start < 1:
        start = 1
    if start > total:
        nodes = []
        end = start - 1
    else:
        nodes = [thread_node(i) for i in range(start, end + 1)]
    has_next = bool(nodes) and end < total
    cursor = f"c{end}" if nodes else None
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {
                            "hasNextPage": has_next,
                            "endCursor": cursor,
                        },
                        "nodes": nodes,
                    }
                }
            }
        }
    }


def route(fix: Path, endpoint: str) -> Path:
    # Origin globbed `*/issues/*/comments*` so query strings still match.
    ep = endpoint.split("?", 1)[0]
    if ep == "graphql":
        return fix / "threads.json"
    if re.search(r"issues/comments/[^/]+/reactions", ep):
        ident = ep.split("issues/comments/", 1)[1].split("/", 1)[0]
        return fix / f"react-{ident}.json"
    if re.search(r"/issues/[^/]+/comments", ep):
        return fix / "conv.json"
    if re.search(r"/pulls/[^/]+/reviews", ep):
        return fix / "reviews.json"
    if re.search(r"/pulls/[^/]+/comments", ep):
        return fix / "inline.json"
    return fix / "empty.json"


def main() -> None:
    fix = Path(os.environ["GH_FIXTURES"])
    n = bump(fix)
    argv = sys.argv[1:]
    log(fix, f"call {n} argv={argv!r}")
    if not argv or argv[0] != "api":
        sys.exit(0)
    endpoint, jqexpr, variables, joined, paginate = parse_api_argv(argv[1:])
    fail_ep = os.environ.get("GH_FAIL_ENDPOINT", "")
    if mode_is_fail(fix) or (fail_ep and fail_ep in endpoint):
        _fail(fix)

    count_raw = os.environ.get("GH_THREAD_COUNT", "").strip()
    if endpoint == "graphql" and count_raw.isdigit():
        total = int(count_raw)
        query = variables.get("query", joined)
        first = None
        m = re.search(r"reviewThreads\s*\(\s*first:\s*(\d+)", query)
        if m:
            first = int(m.group(1))
        after = (
            variables.get("threadCursor")
            or variables.get("after")
            or ""
        )
        if after in ("null", "None", "none"):
            after = ""
        inline = re.search(r'after:\s*"([^"]+)"', query)
        if not after and inline:
            after = inline.group(1)
        payload = threads_page(total, first, after, paginate)
        text = json.dumps(payload)
    else:
        path = route(fix, endpoint)
        if not path.is_file():
            path = fix / "empty.json"
        text = "[]" if not path.is_file() else path.read_text(encoding="utf-8")

    if jqexpr is None:
        sys.stdout.write(text)
        if text and not text.endswith("\n"):
            sys.stdout.write("\n")
        sys.exit(0)
    result = run(
        ["jq", "-r", jqexpr],
        cwd=os.getcwd(),
        env=dict(os.environ),
        stdin=text,
        timeout=60,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.exit(result.exit)


if __name__ == "__main__":
    main()
