#!/usr/bin/env python3
# testkit: exempt isolated fixture program; the consuming World plants it by path
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path

STUB = Path("@@STUB@@")
REPO = Path("@@REPO@@")
argv = sys.argv[1:]
log = STUB / "land-argv.jsonl"
with log.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(argv) + "\n")
plant_path = STUB / "plant.json"
plant = json.loads(plant_path.read_text(encoding="utf-8"))
plant.setdefault("cwds", []).append(os.getcwd())
plant.setdefault("land_cwds", []).append(os.getcwd())
if plant.get("land_abort"):
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write("abort conflict sha=deadbeef remainder=1\n")
    raise SystemExit(3)
if plant.get("land_lineage_break"):
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "Unrelated"
    env["GIT_AUTHOR_EMAIL"] = "unrelated-aa32@example.invalid"
    env["GIT_COMMITTER_NAME"] = "Unrelated"
    env["GIT_COMMITTER_EMAIL"] = "unrelated-aa32@example.invalid"
    tree = subprocess.run(
        ["git", "mktree"],
        input=b"",
        cwd=str(REPO),
        capture_output=True,
        check=False,
        env=env,
        timeout=60,
    )
    tree_sha = (tree.stdout or b"").decode("utf-8").strip()
    commit = subprocess.run(
        ["git", "commit-tree", tree_sha, "-m", "unrelated-aa32"],
        cwd=str(REPO),
        capture_output=True,
        check=False,
        env=env,
        timeout=60,
    )
    sha = (commit.stdout or b"").decode("utf-8").strip()
    plant["lineage_break_sha"] = sha
    plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write("LANDED\t" + sha + "\tnot-descended\n")
    sys.stdout.write("landed n=1 ref=refs/dispatch/planted\n")
    raise SystemExit(0)
rec = argv[0] if argv and not argv[0].startswith("-") else "planted"
extra = plant.get("land_extra_path")
info = (plant.get("records") or {}).get(rec) or {}
if extra and str(info.get("name") or "").startswith("code"):
    path = REPO / extra
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("outside-bucket-aa32\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "--", extra],
        cwd=str(REPO),
        capture_output=True,
        check=False,
        timeout=60,
    )
subprocess.run(
    ["git", "-c", "commit.gpgsign=false", "commit", "--allow-empty",
     "-m", "landed-stub\n\nSource: original\n"],
    cwd=str(REPO),
    capture_output=True,
    check=False,
    timeout=60,
)
sha_proc = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=str(REPO),
    capture_output=True,
    text=True,
    check=False,
    timeout=60,
)
sha = (sha_proc.stdout or "").strip()
plant_path.write_text(json.dumps(plant, indent=2) + "\n", encoding="utf-8")
sys.stdout.write("LANDED\t" + sha + "\tlanded-stub\n")
sys.stdout.write("landed n=1 ref=refs/dispatch/" + rec + "\n")
raise SystemExit(0)
