"""Shared fixtures of the PR overview's tests: the stubbed gh, the scratch repository, the loader.

Moved out of two test modules copied over from the previous repository that
never ran here; the fixtures are the part the real tests import. Worlds,
commands and the planted gh ride on the kit (docs/tests.md).
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import shutil
from pathlib import Path

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[4]
OVERVIEW = ROOT / ".claude" / "skills" / "pr-overview" / "pr_overview.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GH_STUB_PATH = FIXTURES / "gh_stub.py"
KIT_DIR = str((ROOT / "ops" / "testkit").resolve())
REPO = "uscient/agentscient"
RUN_CAP = 60


def load_overview(root: Path, name: str):
    path = root / OVERVIEW.relative_to(ROOT)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gh_stub_text() -> str:
    text = GH_STUB_PATH.read_text(encoding="utf-8")
    if "check_errors" not in text:
        raise AssertionError("plant: check_errors missing from gh stub fixture")
    if "review_errors" not in text:
        raise AssertionError("plant: review_errors missing from gh stub fixture")
    if "nameWithOwner" not in text:
        raise AssertionError("plant: repo view hook missing from gh stub fixture")
    return text


def ready_pr(number, title, branch, oid, names, buckets=None):
    buckets = buckets or {name: "pass" for name in names}
    return {
        "number": number,
        "title": title,
        "headRefName": branch,
        "baseRefName": "dev",
        "headRefOid": oid,
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "isDraft": False,
        "updatedAt": "2026-08-26T12:00:00Z",
        "checks": [{"name": name, "bucket": buckets[name]} for name in names],
    }


def plant_gh(world: World, prs, trees, check_errors=None, review_errors=None) -> dict[str, str]:
    """Plant the gh fixture on the world's bin. Returns env extras the stub reads."""
    missing = [pr["headRefOid"] for pr in prs if pr["headRefOid"] not in trees]
    if missing:
        raise AssertionError(f"gh stub has no tree for head(s) {missing}")
    payload = {
        "prs": prs,
        "trees": trees,
        "check_errors": {str(k): v for k, v in (check_errors or {}).items()},
        "review_errors": {str(k): v for k, v in (review_errors or {}).items()},
        "repo": REPO,
    }
    state = world.plant_text(world.tmp / "gh-state.json", json.dumps(payload))
    landed = json.loads(state.read_text(encoding="utf-8"))
    if landed["check_errors"] != payload["check_errors"]:
        raise AssertionError("plant: check_errors did not land in stub state")
    if landed["review_errors"] != payload["review_errors"]:
        raise AssertionError("plant: review_errors did not land in stub state")
    world.tools.plant("gh", script=gh_stub_text())
    return {
        "GH_STUB_STATE": str(state),
        "GH_STUB_LOG": str(world.tools.log_path("gh")),
        "TESTKIT_DIR": KIT_DIR,
    }


@contextlib.contextmanager
def in_world(world: World, extra: dict[str, str] | None = None):
    """Hermetic os.environ and cwd for an in-process subject.

    World.enter does not take extra (kit gap); extras the stub and the
    subject read are applied after enter.
    """
    world.require("git")
    env = world.env(extra=extra)
    probe = run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=world.cwd,
        env=env,
        timeout=RUN_CAP,
    )
    if probe.exit == 0:
        raise AssertionError(
            f"collect cwd is a git repo: {probe.stdout.strip()}"
        )
    world.enter()
    if extra:
        os.environ.update(extra)
    old = os.getcwd()
    os.chdir(world.cwd)
    try:
        which = shutil.which("gh")
        planted = world.bin / "gh"
        if which is None or Path(which).resolve() != planted.resolve():
            raise AssertionError(f"stub gh not first on PATH: {which}")
        yield world
    finally:
        os.chdir(old)


@contextlib.contextmanager
def non_git_cwd():
    with World() as world:
        world.require("git")
        probe = run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=world.cwd,
            env=world.env(),
            timeout=RUN_CAP,
        )
        if probe.exit == 0:
            raise AssertionError(
                f"collect cwd is a git repo: {probe.stdout.strip()}"
            )
        old = os.getcwd()
        os.chdir(world.cwd)
        try:
            yield world.cwd
        finally:
            os.chdir(old)
