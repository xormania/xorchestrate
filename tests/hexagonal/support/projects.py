"""Fixture projects: a git repository with tracked ``.xorchestrate/``, a private home and a companion.

Every run of xorchestrate needs a set-up project (D15), so tests, scripts and
examples run inside one. ``ProjectFixture`` provisions it through the product's
own operator workflow (companion, init, approve), returns the explicit
``ProjectContext`` for direct ``build()`` calls and runs the real CLI inside
the project with ``XORCHESTRATE_HOME`` pointing at the private home.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from xorchestrate.composition import RELEASE
from xorchestrate.instance import operations
from xorchestrate.instance.home import write_document
from xorchestrate.instance.instance import prepare
from xorchestrate.instance.project import CONFIG_FILE, PROJECT_FILE, PROJECT_SCHEMA, TRACKED_DIRECTORY, locate_worktree

ROOT = Path(__file__).resolve().parents[3]
MINIMAL_CONFIG = {"version": 1}
GIT_IDENTITY = ("-c", "user.name=xorchestrate-tests", "-c", "user.email=tests@example.invalid",
                "-c", "commit.gpgsign=false")


def git(*arguments, cwd):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(["git", *GIT_IDENTITY, *arguments], cwd=cwd, env=environment, check=True,
                          capture_output=True, text=True, timeout=30).stdout.strip()


def provision(repository, config, *, name, home, companion, overlay=None, policies=None, release=RELEASE):
    """Create a ready-to-run project non-interactively: companion, tracked files, init and approve.

    ``home`` becomes ``XORCHESTRATE_HOME`` for every command that uses the
    project (pass the returned ``environment``). ``config`` is written as the
    tracked ``config.json`` and ``policies`` as tracked ``policies/<name>``.
    """
    repository, home, companion = (Path(item).expanduser().resolve() for item in (repository, home, companion))
    environ = {"XORCHESTRATE_HOME": str(home)}
    repository.mkdir(parents=True, exist_ok=True)
    found = locate_worktree(repository)
    if found is None or found.root != repository:
        git("init", "-q", str(repository), cwd=repository)
    tracked_dir = repository / TRACKED_DIRECTORY
    tracked_dir.mkdir(exist_ok=True)
    write_document(tracked_dir / PROJECT_FILE,
                   {"schema": PROJECT_SCHEMA, "name": name, "release": release, "requires": []}, 0o644)
    write_document(tracked_dir / CONFIG_FILE, json.loads(json.dumps(config)), 0o644)
    for filename, fragment in (policies or {}).items():
        write_document(tracked_dir / "policies" / filename, fragment, 0o644)
    operations.companion_init(companion, environ)
    if overlay is not None:
        write_document(companion / "overlays" / f"{name}.json", overlay, 0o644)
    operations.init(repository, environ=environ)
    operations.approve(repository, environ)
    return Provisioned(repository, home, companion, name, environ, prepare(repository, environ=environ))


class Provisioned:
    """A provisioned project and the environment that selects its home."""

    def __init__(self, repository, home, companion, name, environment, prepared):
        self.repository = repository
        self.home = home
        self.companion = companion
        self.name = name
        self.environment = environment
        self.prepared = prepared

    @property
    def context(self):
        return self.prepared.context


class ProjectFixture:
    """A provisioned project under ``base``: ``repo/``, ``home/`` and ``companion/``."""

    def __init__(self, base, config=None, *, name="fixture", overlay=None, policies=None, environment=None):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.provisioned = provision(self.base / "repo", MINIMAL_CONFIG if config is None else config, name=name,
                                     home=self.base / "home", companion=self.base / "companion",
                                     overlay=overlay, policies=policies)
        self.repo = self.provisioned.repository
        self.home = self.provisioned.home
        self.companion = self.provisioned.companion
        inherited = {key: value for key, value in (environment or os.environ).items()
                     if key not in ("XORCHESTRATE_RUNTIME_DIR", "XDG_RUNTIME_DIR")}
        self.environment = {**inherited, **self.provisioned.environment,
                            "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}

    # -- configuration ---------------------------------------------------------

    @property
    def tracked(self):
        return self.repo / TRACKED_DIRECTORY

    @property
    def data_dir(self):
        return self.home / "projects" / self.name

    def prepared(self, worktree=None):
        return prepare(worktree or self.repo, environ=self.provisioned.environment)

    @property
    def context(self):
        """The explicit ``ProjectContext`` for ``build(config, project=...)``."""
        return self.prepared().context

    @property
    def config(self):
        """The rendered configuration the project runs."""
        return self.prepared().config

    def write_config(self, config, *, approved=True, worktree=None):
        write_document((worktree or self.repo) / TRACKED_DIRECTORY / CONFIG_FILE, config, 0o644)
        if approved:
            self.approve(worktree)

    def write_overlay(self, overlay):
        write_document(self.companion / "overlays" / f"{self.name}.json", overlay, 0o644)

    def approve(self, worktree=None):
        return operations.approve(worktree or self.repo, self.provisioned.environment)

    # -- git -------------------------------------------------------------------

    def commit(self, message="fixture"):
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "--allow-empty", "-m", message, cwd=self.repo)

    def add_worktree(self, name="feature", branch=None):
        """Add a linked worktree beside the repository (committing the current tree first)."""
        self.commit()
        path = self.base / name
        git("worktree", "add", "-q", "-b", branch or name, str(path), cwd=self.repo)
        return path.resolve()

    # -- CLI -------------------------------------------------------------------

    def command(self, *arguments, project=True):
        command = [sys.executable, "-m", "xorchestrate"]
        if project:
            command += ["--project", str(self.repo if project is True else project)]
        return command + list(arguments)

    def run(self, *arguments, project=True, cwd=None, input=None, timeout=60, environment=None):
        return subprocess.run(self.command(*arguments, project=project), input=input, capture_output=True,
                              cwd=cwd or self.base, env=environment or self.environment, timeout=timeout)

    def json(self, *arguments, expect=0, **options):
        result = self.run(*arguments, **options)
        assert result.returncode == expect, (result.returncode, result.stdout, result.stderr)
        stream = result.stdout if result.stdout.strip() else result.stderr
        return json.loads(stream.splitlines()[-1])
