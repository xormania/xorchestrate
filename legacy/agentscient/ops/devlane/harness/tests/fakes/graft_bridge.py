#!/usr/bin/env python3
"""Verified fake Node for graft/CONTRACT.md bridge requests.

Invoked as ``[absolute_node, absolute_bridge, absolute_graft_directory]``.
One UTF-8 JSON object on stdin; one JSON object on stdout. No network.
Transcript lands at ``<install>/fake-transcript.jsonl`` so a suite can
prove the build and query arms actually ran.

Helpers import the kit (World, run, plant_text/plant_bytes). The planted
node copy of this file is executed as argv[0] and must stay stdlib-only
at import, so those imports live inside the helpers, not at module level.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ADMITTED = (
    "graft_find_code",
    "graft_file_api",
    "graft_trace_calls",
    "graft_find_all",
    "graft_repo_map",
)
BUILD_KEYS = frozenset({"op", "root", "cache"})
QUERY_KEYS = frozenset({"op", "root", "cache", "tool", "arguments"})
PIN_PATH = Path(__file__).resolve().parents[2] / "graft" / "pin.json"
INSTALLED_AT = "2026-01-01T00:00:00Z"


def load_pin() -> dict:
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def transcript_path(install: Path) -> Path:
    return Path(install) / "fake-transcript.jsonl"


def _install_from_node(node: Path) -> Path:
    return node.resolve().parents[2]


def _mode(install: Path) -> str:
    path = install / "fake-mode"
    if not path.is_file():
        return "ok"
    return path.read_text(encoding="utf-8").strip() or "ok"


def _fail(error: str) -> dict:
    return {"ok": False, "error": error}


def _is_absolute(value: object) -> bool:
    return isinstance(value, str) and value.startswith("/") and len(value) > 1


def _under(root: Path, other: Path) -> bool:
    try:
        other.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _ops_files(root: Path) -> list[Path]:
    ops = root / "ops"
    if not ops.is_dir():
        return []
    found = []
    for path in sorted(ops.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found.append(path)
    return found


def _handle(req: object, *, graft_dir: Path) -> dict:
    if not isinstance(req, dict):
        return _fail("request must be a JSON object")
    op = req.get("op")
    if op == "build":
        if set(req) != BUILD_KEYS:
            return _fail("build admits only op, root, cache")
        return _build(req, graft_dir=graft_dir)
    if op == "query":
        if set(req) != QUERY_KEYS:
            return _fail("query admits only op, root, cache, tool, arguments")
        return _query(req, graft_dir=graft_dir)
    return _fail(f"unknown op {op!r}")


def _build(req: dict, *, graft_dir: Path) -> dict:
    root_s, cache_s = req.get("root"), req.get("cache")
    if not _is_absolute(root_s) or not _is_absolute(cache_s):
        return _fail("root and cache must be absolute")
    root, cache = Path(root_s), Path(cache_s)
    if _under(root, cache) or root.resolve() == cache.resolve():
        return _fail("cache must be outside the source root")
    install = graft_dir.parent
    mode = _mode(install)
    if mode == "truncated":
        return {"__truncated__": True}
    if mode == "wrong-type":
        return {"ok": True, "result": "not-an-object"}
    if mode == "provider-error":
        return _fail("synthetic provider failure")
    if (root / ".graft" / "config.json").is_file():
        return _fail("ambient graft config")
    files = _ops_files(root)
    if mode == "empty-graph" or not files:
        return _fail("unavailable: zero files")
    nodes = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if mode == "outside-ops":
            rel = "../outside.py"
        nodes.append({"path": rel})
        if mode == "outside-ops":
            break
    if any(
        not str(node["path"]).replace("\\", "/").startswith("ops/")
        for node in nodes
    ):
        return _fail("node path escaped ops/")
    cache.mkdir(parents=True, exist_ok=True)
    graph = {
        "errors": [],
        "nodes": nodes,
        "edges": [],
        "files": len(files),
    }
    graph_path = cache / ".graph" / "wiring.json"
    graph_path.parent.mkdir()
    graph_path.write_text(
        json.dumps(graph, sort_keys=True) + "\n", encoding="utf-8",
    )
    return {"ok": True, "result": {
        "contextDir": str(cache), "graphPath": str(graph_path),
        "cards": 0, "files": len(files), "parsed": len(files), "reused": 0,
        "nodes": len(nodes), "edges": 0, "byKind": {"file": len(files)},
        "byRelation": {}, "languages": [],
        "meaning": {"cached": 0, "computed": 0, "stale": 0,
                    "pending": len(nodes), "errors": [], "failedFiles": 0, "skippedFiles": 0},
        "errors": [],
    }}


def _query(req: dict, *, graft_dir: Path) -> dict:
    root_s, cache_s = req.get("root"), req.get("cache")
    if not _is_absolute(root_s) or not _is_absolute(cache_s):
        return _fail("root and cache must be absolute")
    tool = req.get("tool")
    arguments = req.get("arguments")
    if tool not in ADMITTED:
        return _fail(f"unknown tool {tool!r}")
    if not isinstance(arguments, dict):
        return _fail("arguments must be an object")
    if _mode(graft_dir.parent) == "query-iserror":
        return {"ok": True, "result": {"text": "synthetic isError", "isError": True}}
    for key, value in arguments.items():
        if key in {"path", "file", "dir", "root"} and isinstance(value, str):
            posix = value.replace("\\", "/")
            escaped = (
                posix.startswith("/") or posix.startswith("..") or "/../" in posix
            )
            if escaped and not posix.startswith("ops/"):
                return _fail("path escaped ops/")
    text = f"navigation {tool} {json.dumps(arguments, sort_keys=True)}"
    return {"ok": True, "result": {"text": text, "isError": False}}


def _runtime_files(graft: Path) -> list[Path]:
    found = []
    for rel in ("dist", "node_modules"):
        top = graft / rel
        if not top.exists():
            continue
        for path in sorted(top.rglob("*")):
            if path.is_file() and not path.is_symlink():
                found.append(path)
    return found


def plant_install(destination: Path, *, pin: dict | None = None, mode: str = "ok") -> Path:
    """Write a completed fake install tree. Does not download or run npm."""
    from ops.testkit import World, make_executable

    dest = Path(destination)
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"nonempty destination: {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    pin = json.loads(json.dumps(pin or load_pin()))
    node = dest / "node" / "bin" / "node"
    graft = dest / "graft"
    with World(prefix="graft-plant-") as world:
        world.plant_bytes(node, Path(__file__).resolve().read_bytes())
        make_executable(node)
        world.plant_text(
            graft / "bridge.mjs",
            "export {}; // fake stub; the Python fake Node does not eval this\n",
        )
        world.plant_text(
            graft / "package.json",
            json.dumps({
                "name": pin["graft"]["package"],
                "version": pin["graft"]["version"],
            }) + "\n",
        )
        world.plant_text(
            graft / "package-lock.json",
            json.dumps({"lockfileVersion": 3, "name": "fake"}) + "\n",
        )
        world.plant_text(
            graft / "dist" / "graph" / "build.js",
            "export function buildGraph() {}\n",
        )
        world.plant_text(
            graft / "dist" / "mcp" / "tools.js",
            "export function callTool() {}\n",
        )
        world.plant_text(
            graft / "node_modules" / "fake-mod" / "index.js",
            "export {}\n",
        )
        if mode != "ok":
            world.plant_text(dest / "fake-mode", mode + "\n")
        files = {
            "node/bin/node": sha256_file(node),
            "graft/package.json": sha256_file(graft / "package.json"),
            "graft/package-lock.json": sha256_file(graft / "package-lock.json"),
        }
        for path in _runtime_files(graft):
            files[path.relative_to(dest).as_posix()] = sha256_file(path)
        receipt = {
            "schema": "graft-install/1",
            "pin": pin,
            "installed_at": INSTALLED_AT,
            "files": dict(sorted(files.items())),
        }
        world.plant_text(
            dest / "receipt.json",
            json.dumps(receipt, sort_keys=True, indent=2) + "\n",
        )
    return dest


def invoke(install: Path, request: dict) -> tuple[int, str, str]:
    from ops.testkit import World, run

    install = Path(install)
    node = install / "node" / "bin" / "node"
    graft_dir = install / "graft"
    bridge = graft_dir / "bridge.mjs"
    with World(prefix="graft-bridge-") as world:
        result = run(
            [str(node), str(bridge), str(graft_dir)],
            cwd=world.cwd,
            env=world.env(),
            stdin=json.dumps(request),
            timeout=30,
        )
    return result.exit, result.stdout, result.stderr


def _append_transcript(install: Path, payload: dict) -> None:
    path = transcript_path(install)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3:
        sys.stdout.write(json.dumps(_fail("argv is node, bridge, graft_dir")) + "\n")
        return 0
    bridge = Path(argv[1])
    graft_dir = Path(argv[2])
    install = graft_dir.parent if graft_dir.name == "graft" else _install_from_node(Path(argv[0]))
    raw = sys.stdin.read()
    try:
        req = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError as exc:
        req = None
        response = _fail(f"malformed json: {exc}")
    else:
        if req is None:
            response = _fail("empty stdin")
        else:
            response = _handle(req, graft_dir=graft_dir)
    record = {
        "argv": [str(Path(argv[0]).resolve()), str(bridge), str(graft_dir)],
        "request": req,
        "response": None if response.get("__truncated__") else response,
    }
    _append_transcript(install, record)
    if response.get("__truncated__"):
        sys.stdout.write('{"ok":true,"res')
        return 0
    sys.stdout.write(json.dumps(response, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
