"""A machine with fake release toolchains, for upgrade tests.

``UpgradeMachine`` provisions one project through the operator workflow with a
private home and companion, puts the fake ``uv`` (``fixtures/fake_uv.py``) first
on ``PATH`` and runs the real launcher in a subprocess. Toolchains it builds run
the current xorchestrate as their release (``FAKE_UV_REAL=release``); a
toolchain built with ``data_version`` 2 reads data version 2 and migrates older
stores by marking every ``work`` record.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from xorchestrate.instance import operations
from xorchestrate.instance.home import Home, read_document, write_document

from .projects import ROOT, provision

FAKE_UV = Path(__file__).resolve().parents[1] / "fixtures" / "fake_uv.py"


def fake_uv(directory, home):
    """Install the fake ``uv`` in ``directory``; return the environment that selects it and its toolchains.

    Toolchains then come from ``fake-release=<release>`` and run the current
    code as that release (``FAKE_UV_REAL=release``).
    """
    programs = Path(directory)
    programs.mkdir(parents=True, exist_ok=True)
    uv = programs / "uv"
    uv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_UV}" "$@"\n')
    uv.chmod(0o755)
    Home(home).update_settings(toolchain_source="fake-release={release}")
    return {"PATH": os.pathsep.join([str(programs), os.environ.get("PATH", "")]), "FAKE_UV_REAL": "release"}


def pin_release(repository, home, release, *, approve=True):
    """Change the tracked pin in ``project.json`` (a tracked change) and, by default, approve it."""
    path = Path(repository) / ".xorchestrate" / "project.json"
    write_document(path, {**json.loads(path.read_text()), "release": release}, 0o644)
    if approve:
        operations.approve(repository, {"XORCHESTRATE_HOME": str(home)})


class UpgradeMachine:
    def __init__(self, base, *, release, config=None, name="app", overlay=None):
        self.base = Path(base)
        self.home = self.base / "home"
        self.name = name
        self.provisioned = provision(self.base / "repo", config or {"version": 1}, name=name, home=self.home,
                                     companion=self.base / "companion", release=release, overlay=overlay)
        self.repo = self.provisioned.repository
        self.environment = {
            **{key: value for key, value in os.environ.items()
               if key not in ("XORCHESTRATE_HANDOFF", "XORCHESTRATE_RUNTIME_DIR", "XDG_RUNTIME_DIR")},
            "XORCHESTRATE_HOME": str(self.home), "FAKE_UV_LOG": str(self.base / "uv.log"),
            "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")]),
            **fake_uv(self.base / "bin", self.home)}

    # -- paths and records -------------------------------------------------------

    @property
    def data_dir(self):
        return self.home / "projects" / self.name

    @property
    def store(self):
        return self.data_dir / "state.sqlite3"

    def instance(self):
        return read_document(self.data_dir / "instance.json")

    def journal(self):
        return read_document(self.data_dir / "upgrade.json")

    def backups(self):
        directory = self.data_dir / "backups"
        return sorted(path for path in directory.iterdir() if path.is_dir()) if directory.is_dir() else []

    def bundles(self, prefix=""):
        exports = self.data_dir / "telemetry" / "exports"
        return sorted(path for path in exports.iterdir() if path.name.startswith(prefix)) if exports.is_dir() else []

    def uv_syncs(self):
        log = self.base / "uv.log"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [call for call in calls if call[0] == "sync"]

    def pin(self, release, *, approve=True):
        pin_release(self.repo, self.home, release, approve=approve)

    # -- the launcher ------------------------------------------------------------

    def run(self, *arguments, timeout=180, **extra):
        return subprocess.run([sys.executable, "-P", "-m", "xorchestrate", "--project", str(self.repo), *arguments],
                              capture_output=True, text=True, cwd=self.base, env={**self.environment, **extra},
                              timeout=timeout)

    def json(self, *arguments, expect=0, **extra):
        result = self.run(*arguments, **extra)
        assert result.returncode == expect, (result.returncode, result.stdout, result.stderr)
        stream = result.stdout if result.stdout.strip() else result.stderr
        return json.loads(stream.strip().splitlines()[-1])


def raw_store(path):
    """Data version and work records read through SQLite directly, independent of the store adapter."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        work = [json.loads(row[0]) for row in connection.execute("SELECT data FROM records WHERE kind = 'work'")]
        return {"user_version": connection.execute("PRAGMA user_version").fetchone()[0], "work": work}
    finally:
        connection.close()
