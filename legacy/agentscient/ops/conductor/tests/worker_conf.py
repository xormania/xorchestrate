"""Provisional helpers for configured-worker tests.

Contract review is running; treat these pins as provisional until it
settles. Source of the promises: ops/devlane/dispatch/CONTRACT.md
(Configured workers and agy installation) and the laneconf module
contract's worker amendment. Tests import this module; they do not
import the implementation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ops.testkit import hermetic_env, make_executable, plant_text

DEFAULT_WORKERS = ("claude", "codex", "grok")
WORKER_NAME_RE = r"^[a-z][a-z0-9]*$"
WORKER_OBJECT_KEYS = frozenset({"name", "executable", "sources"})
WORKER_SOURCE_KEYS = frozenset({"workers", "executable"})


def executable_key(name: str) -> str:
    return f"EXECUTABLE_{name.upper()}"


def path_with(bindir: Path) -> str:
    # Callers plant their own bindir; World.env() prepends world.bin the
    # same way. The home is unused: only PATH is taken.
    return hermetic_env(Path("."), bin=bindir)["PATH"]


def plant_executable(test, path: Path, body: str | None = None) -> Path:
    """Write a regular executable and prove the plant landed.

    Returns the realpath. Override and agy expectations use that
    resolved target; legacy PATH lookup uses the PATH spelling
    (bindir / name) from plant_named_bins, not this return value.
    """
    text = body if body is not None else "#!/bin/sh\nexit 0\n"
    path = plant_text(path, text)
    make_executable(path)
    test.assertEqual(path.read_text(encoding="utf-8"), text, f"plant did not land intact: {path}")
    test.assertTrue(os.access(path, os.X_OK), f"plant not executable: {path}")
    return path.resolve()


def plant_named_bins(test, root: Path, names: tuple[str, ...]) -> tuple[Path, dict[str, Path]]:
    """Plant NAME bins. Returned paths are the PATH-lookup spelling."""
    bindir = root / "bin"
    planted: dict[str, Path] = {}
    test.assertGreater(len(names), 0, names)
    for name in names:
        path = bindir / name
        plant_executable(test, path)
        planted[name] = path
    test.assertEqual(len(planted), len(names), planted)
    return bindir, planted


def file_source(path: Path, key: str) -> str:
    return f"file:{path.resolve()}#{key}"


def env_source(key: str) -> str:
    return f"env:{key}"


def parse_worker_object(test, proc, *, expect_absolute: bool | None = None) -> dict:
    """Exact worker JSON on stdout, empty stderr, exit 0.

    Absolute-path checking is opt-in. Legacy PATH-miss fallback is the
    bare name, which is not absolute; override and agy cases pass
    expect_absolute=True.

    ``proc`` is a kit ``Result`` (``exit``) or a CompletedProcess
    (``returncode``); unmigrated callers still pass the latter.
    """
    code = proc.exit if hasattr(proc, "exit") else proc.returncode
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = f"{stdout}{stderr}"
    test.assertEqual(code, 0, combined)
    test.assertEqual(
        stderr.strip(),
        "",
        f"worker success has empty stderr: {proc.stderr!r}",
    )
    text = stdout
    test.assertTrue(text.endswith("\n"), f"JSON plus one newline; got {text!r}")
    lines = text.splitlines()
    test.assertEqual(len(lines), 1, f"one JSON object line; got {text!r}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        test.fail(f"worker stdout is not JSON ({exc}); got {text!r}")
    test.assertIsInstance(payload, dict, payload)
    test.assertEqual(set(payload), WORKER_OBJECT_KEYS, payload)
    test.assertIsInstance(payload["name"], str, payload)
    test.assertIsInstance(payload["executable"], str, payload)
    sources = payload["sources"]
    test.assertIsInstance(sources, dict, payload)
    test.assertEqual(set(sources), WORKER_SOURCE_KEYS, payload)
    test.assertIsInstance(sources["workers"], str, payload)
    test.assertIsInstance(sources["executable"], str, payload)
    if expect_absolute is True:
        test.assertTrue(
            Path(payload["executable"]).is_absolute(),
            f"executable must be absolute: {payload['executable']!r}",
        )
    elif expect_absolute is False:
        test.assertFalse(
            Path(payload["executable"]).is_absolute(),
            f"legacy PATH-miss fallback is the bare name: {payload['executable']!r}",
        )
    return payload
