"""Helpers for Graft adapter tests. Import ``support`` first in callers."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

from ops.testkit import World, plant_text, run

HARNESS = Path(__file__).resolve().parents[1]
GRAFT_PY = HARNESS / "graft.py"
PIN_PATH = HARNESS / "graft" / "pin.json"
FAKE_PY = Path(__file__).resolve().parent / "fakes" / "graft_bridge.py"
AGENT = "Graft Test <noreply@example.invalid>"
REFUSAL_EXIT = 2
ABORT_EXIT = 3

CONF_DEFAULTS = {
    "LANE_NAME": "agentscient",
    "TRUNK_BRANCH": "dev",
    "RELEASE_BRANCH": "master",
    "REPO_SLUG": "uscient/agentscient",
    "CHECK_NAMES": "lane app",
    "WF_AGENT_DEFAULT": AGENT,
    "LANE_AUTHOR": "Lane Owner <lane-owner@example.invalid>",
    "MODEL_GROK": "model-grok",
    "MODEL_CODEX": "model-codex",
    "MODEL_CLAUDE_READ": "model-claude-read",
    "MODEL_CLAUDE_PLAN": "model-claude-plan",
    "EFFORT_GROK": "high",
    "EFFORT_CODEX": "high",
    "EFFORT_CLAUDE": "high",
    "WALL_DEFAULT": "900",
    "SCOPE_CAP": "1024",
    "GRAFT_INSTALL": "",
}


def load_fake():
    spec = importlib.util.spec_from_file_location("graft_bridge_fake", FAKE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_graft():
    spec = importlib.util.spec_from_file_location("graft_adapter", GRAFT_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_pin() -> dict:
    return json.loads(PIN_PATH.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_conf(path: Path, **overrides: object) -> Path:
    keys = dict(CONF_DEFAULTS)
    omit = tuple(overrides.pop("omit", ()) or ())
    for name, value in overrides.items():
        keys[name] = str(value)
    for name in omit:
        keys.pop(name, None)
    lines = ["# scratch lane conf\n"]
    for name, value in keys.items():
        lines.append(f"{name}='{value}'\n")
    return plant_text(path, "".join(lines), recognisable="# scratch lane conf")


def git(repo: Path, *args: str):
    with World(prefix="graft-git-") as world:
        return world.git(*args, cwd=repo, timeout=60)


def plant_ops_root(path: Path, files: dict[str, str] | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    files = files or {"ops/lane.md": "ops source\n"}
    git(path, "init", "-b", "work")
    git(path, "config", "user.name", "graft-test")
    git(path, "config", "user.email", "graft-test@example.invalid")
    git(path, "config", "commit.gpgsign", "false")
    for rel, body in files.items():
        dest = path / rel
        plant_text(dest, body)
    git(path, "add", "-A")
    git(path, "commit", "-m", "ops")
    return path


def run_cli(argv, *, env=None, cwd=None) -> tuple[int, str, str]:
    result = run(
        [sys.executable, str(GRAFT_PY), *list(argv)],
        cwd=cwd or Path.cwd(),
        env=dict(os.environ if env is None else env),
        timeout=60,
    )
    return result.exit, result.stdout, result.stderr


def call_public(test, name, *args, **kwargs):
    graft = load_graft()
    test.assertTrue(
        hasattr(graft, name),
        f"{name} is a public seam on ops/devlane/harness/graft.py",
    )
    fn = getattr(graft, name)
    test.assertTrue(callable(fn), f"{name} is callable")
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        test.fail(f"{name}() raised {type(exc).__name__}: {exc}")


def assert_refusal(test, code, out, err, ident, *, phrases=(), named=()):
    test.assertEqual(
        code, REFUSAL_EXIT,
        f"{ident} refuses with exit {REFUSAL_EXIT}, got {code}: {(out or '') + (err or '')!r}",
    )
    test.assertEqual(
        (out or "").strip(), "",
        f"{ident} refusal text belongs on stderr, not stdout: {out!r}",
    )
    lines = [ln for ln in (err or "").splitlines() if ln.strip()]
    test.assertEqual(
        len(lines), 1,
        f"{ident} refusal must be one stderr line: {err!r}",
    )
    line = lines[0]
    lower = line.lower()
    token = ident.lower().replace("_", "-")
    test.assertIn(token, lower, f"{ident} class token missing: {line!r}")
    for word in ("expected", "found", "needed"):
        test.assertIn(word, lower, f"{ident} names {word}: {line!r}")
    test.assertLess(lower.index("expected"), lower.index("found"), line)
    test.assertLess(lower.index("found"), lower.index("needed"), line)
    test.assertRegex(
        line,
        rf"{re.escape(token)}:\s*expected",
        f"{ident}: expected is the refusal class token: {line!r}",
    )
    for item in named:
        test.assertIn(str(item), line, f"{ident} names {item!r}: {line!r}")
    for phrase in phrases:
        test.assertIn(
            phrase.lower(), lower,
            f"{ident} names {phrase!r}: {line!r}",
        )
    return line


class GraftCase:
    """Temp HOME, scratch lane conf, optional fake install and ops root."""

    def setUp(self):
        self.world = World(prefix="graft-case-")
        self.world.enter()
        self.home = self.world.home
        os.environ["WF_AGENT"] = AGENT
        self.conf = write_conf(self.world.xdg_config / "lane.conf")
        os.environ["LANE_CONF"] = str(self.conf)
        os.environ["GRAFT_INSTALL"] = ""
        self.fake = load_fake()
        self.pin = load_pin()

    def tearDown(self):
        self.world.close()

    def set_install(self, value: str) -> None:
        os.environ["GRAFT_INSTALL"] = value
        write_conf(self.conf, GRAFT_INSTALL=value)

    def plant_fake_install(self, *, mode: str = "ok") -> Path:
        dest = self.home / "graft-install"
        self.fake.plant_install(dest, pin=self.pin, mode=mode)
        self.set_install(str(dest))
        return dest

    def plant_root(self, files=None) -> Path:
        return plant_ops_root(self.home / "src", files)

    def cli(self, argv, *, identity=True):
        env = dict(os.environ)
        if not identity:
            env.pop("WF_AGENT", None)
        return run_cli(argv, env=env, cwd=self.home)
