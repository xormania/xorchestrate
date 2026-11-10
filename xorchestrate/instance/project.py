"""A project's tracked configuration and the git worktree it is read from.

```
<repo>/.xorchestrate/project.json   {"schema": 1, "name": "app", "release": "v0.3.0", "requires": ["codex"]}
<repo>/.xorchestrate/config.json    product configuration with placeholders (see render.py)
<repo>/.xorchestrate/policies/*.json optional fragments merged beneath config.json
```

Nothing in it is machine-specific, so the whole directory is tracked. A
project is identified on a machine by its git common directory, which every
worktree of a repository shares (see instance.py).
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..model import OrchestrationError, canonical_digest
from .home import read_document, write_document

TRACKED_DIRECTORY = ".xorchestrate"
PROJECT_FILE = "project.json"
CONFIG_FILE = "config.json"
POLICIES_DIRECTORY = "policies"
PROJECT_SCHEMA = 1
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
RELEASE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.+-]+)?$")
STARTER_CONFIG = {"version": 1}
GIT_TIMEOUT = 30


def git(*arguments, cwd, check=True):
    """Run git with literal arguments; repository-selecting variables are removed."""
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        result = subprocess.run(["git", *arguments], cwd=cwd, env=environment, capture_output=True, text=True,
                                timeout=GIT_TIMEOUT, check=False)
    except FileNotFoundError as exc:
        raise OrchestrationError("git is required to identify a project.", "git_unavailable") from exc
    if check and result.returncode != 0:
        return None
    return result.stdout.strip()


@dataclass(frozen=True)
class Worktree:
    """One git worktree: its root, its git directories, its name and branch."""

    root: Path
    git_dir: Path
    common_dir: Path
    branch: str | None

    @property
    def linked(self):
        return self.git_dir != self.common_dir

    @property
    def name(self):
        """Git's worktree name for a linked worktree; the directory name of the main one."""
        return self.git_dir.name if self.linked else self.root.name

    @property
    def tracked(self):
        return self.root / TRACKED_DIRECTORY

    def identity(self, config_hash=None):
        """The worktree identity a connection carries (see ``model.WORKTREE_FIELDS``)."""
        value = {"root": str(self.root), "name": self.name}
        if self.branch:
            value["branch"] = self.branch
        if config_hash:
            value["config_hash"] = config_hash
        return value


def locate_worktree(path=None):
    """The git worktree containing ``path`` (default: the working directory), or ``None``."""
    start = Path(path or Path.cwd()).expanduser().resolve()
    if not start.exists():
        raise OrchestrationError("The project path does not exist.", "project_required", {"path": str(start)})
    directory = start if start.is_dir() else start.parent
    root = git("rev-parse", "--show-toplevel", cwd=directory)
    if not root:
        return None
    git_dir = git("rev-parse", "--absolute-git-dir", cwd=directory)
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=directory)
    if not git_dir or not common:
        return None
    common_dir = Path(common)
    branch = git("symbolic-ref", "--short", "-q", "HEAD", cwd=directory) or None
    return Worktree(Path(root).resolve(), Path(git_dir).resolve(), common_dir.resolve(), branch)


def tracked_digests(directory):
    """SHA-256 of every regular file under ``directory``, keyed by its relative POSIX path.

    Symbolic links are refused: what is approved must be exactly what is read.
    """
    directory = Path(directory)
    digests = {}
    for current, directories, files in os.walk(directory, followlinks=False):
        for name in sorted(directories):
            if (Path(current) / name).is_symlink():
                raise OrchestrationError("Tracked project configuration may not contain symbolic links.",
                                         "project_invalid", {"path": str(Path(current) / name)})
        for name in files:
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                raise OrchestrationError("Tracked project configuration may contain only regular files.",
                                         "project_invalid", {"path": str(path)})
            digests[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(digests.items()))


def configuration_hash(digests):
    """The approval hash of a tracked configuration: a digest of its file digests."""
    return canonical_digest({"schema": PROJECT_SCHEMA, "files": digests})


def differing_files(expected, actual):
    """Relative paths whose content differs, or that exist on only one side."""
    return sorted(path for path in set(expected) | set(actual) if expected.get(path) != actual.get(path))


@dataclass(frozen=True)
class TrackedProject:
    """The tracked ``.xorchestrate/`` of one worktree, as read now."""

    worktree: Worktree
    name: str
    release: str
    requires: tuple
    digests: dict = field(default_factory=dict)

    @property
    def directory(self):
        return self.worktree.tracked

    @property
    def config_hash(self):
        return configuration_hash(self.digests)

    def identity(self):
        return self.worktree.identity(self.config_hash)


def validate_project_document(document, path):
    details = {"path": str(path)}
    if document.get("schema") != PROJECT_SCHEMA:
        raise OrchestrationError("project.json must have schema 1.", "project_invalid", details)
    unknown = set(document) - {"schema", "name", "release", "requires"}
    if unknown:
        raise OrchestrationError("project.json has unknown fields.", "project_invalid",
                                 {**details, "fields": sorted(unknown)})
    name, release, requires = document.get("name"), document.get("release"), document.get("requires", [])
    if not isinstance(name, str) or not NAME.match(name):
        raise OrchestrationError("project.json name must be lowercase letters, digits, '.', '_' or '-'.",
                                 "project_invalid", details)
    if not isinstance(release, str) or not RELEASE.match(release):
        raise OrchestrationError("project.json release must be a release tag such as v0.3.0.", "project_invalid",
                                 details)
    if not isinstance(requires, list) or any(not isinstance(item, str) or not item for item in requires):
        raise OrchestrationError("project.json requires must list program names.", "project_invalid", details)
    return name, release, tuple(requires)


def read_tracked(worktree):
    """Read and check the worktree's tracked project; ``None`` when it has no ``project.json``."""
    path = worktree.tracked / PROJECT_FILE
    document = read_document(path)
    if document is None:
        return None
    name, release, requires = validate_project_document(document, path)
    return TrackedProject(worktree, name, release, requires, tracked_digests(worktree.tracked))


def write_starter(worktree, name, release):
    """Create a starter ``.xorchestrate/`` (project.json and config.json) in a worktree without one."""
    if not NAME.match(name):
        raise OrchestrationError("Project names are lowercase letters, digits, '.', '_' or '-'.", "invalid_arguments",
                                 {"name": name})
    worktree.tracked.mkdir(exist_ok=False)
    written = [write_document(worktree.tracked / PROJECT_FILE,
                              {"schema": PROJECT_SCHEMA, "name": name, "release": release, "requires": []}, 0o644),
               write_document(worktree.tracked / CONFIG_FILE, dict(STARTER_CONFIG), 0o644)]
    return [str(path) for path in written]


def default_name(worktree):
    """A starter project name derived from the repository directory name."""
    candidate = re.sub(r"[^a-z0-9._-]+", "-", worktree.common_dir.parent.name.lower()).strip("-._") or "project"
    return candidate[:64] if NAME.match(candidate[:64]) else "project"
