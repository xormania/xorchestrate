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
import stat
from dataclasses import dataclass, field
from pathlib import Path

from ..composition import read_json
from ..model import OrchestrationError, canonical_digest
from .home import write_document

TRACKED_DIRECTORY = ".xorchestrate"
PROJECT_FILE = "project.json"
CONFIG_FILE = "config.json"
POLICIES_DIRECTORY = "policies"
PROJECT_SCHEMA = 1
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
RELEASE = re.compile(r"^v\d+\.\d+\.\d+(?:[-+.][0-9A-Za-z.+-]+)?$")
STARTER_CONFIG = {"version": 1}


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


def locate_worktree(path=None, version_control=None):
    """The worktree containing ``path`` (default: the working directory), or ``None``.

    ``version_control`` is the ``VersionControl`` port; by default the one the
    user's home selects.
    """
    start = Path(path or Path.cwd()).expanduser().resolve()
    if not start.exists():
        raise OrchestrationError("The project path does not exist.", "project_required", {"path": str(start)})
    if version_control is None:
        from .services import services
        version_control = services().version_control
    location = version_control.worktree(str(start))
    if location is None:
        return None
    return Worktree(Path(location.root), Path(location.private_dir), Path(location.common_dir), location.branch)


STATE_SUFFIXES = (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal")


def read_tracked_files(directory):
    """Read every regular file under ``directory`` exactly once, keyed by its relative POSIX path.

    The approval hash and the rendered configuration are both computed from
    these bytes, so a file changed after it was hashed can never be rendered
    as approved. Symbolic links, including the directory itself, are refused,
    and so are SQLite state files left by earlier releases.
    """
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise OrchestrationError("The tracked .xorchestrate must be a directory, not a symbolic link.",
                                 "project_invalid", {"path": str(directory)})
    contents = {}
    for current, directories, files in os.walk(directory, followlinks=False):
        for name in sorted(directories):
            if (Path(current) / name).is_symlink():
                raise OrchestrationError("Tracked project configuration may not contain symbolic links.",
                                         "project_invalid", {"path": str(Path(current) / name)})
        for name in files:
            path = Path(current) / name
            if name.endswith(STATE_SUFFIXES):
                raise OrchestrationError(
                    "Application state from an earlier release is inside the tracked .xorchestrate/; move it to "
                    "the instance directory (~/.xorchestrate/projects/<project>/state.sqlite3) or delete it.",
                    "project_invalid", {"path": str(path)})
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            except OSError as exc:
                raise OrchestrationError("Tracked project configuration may contain only regular files.",
                                         "project_invalid", {"path": str(path)}) from exc
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise OrchestrationError("Tracked project configuration may contain only regular files.",
                                             "project_invalid", {"path": str(path)})
                contents[path.relative_to(directory).as_posix()] = source.read()
    return dict(sorted(contents.items()))


def digests_of(contents):
    return {path: hashlib.sha256(data).hexdigest() for path, data in contents.items()}


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
    contents: dict = field(default_factory=dict)
    """The bytes of every tracked file, read once; hashing and rendering both use them."""

    @property
    def directory(self):
        return self.worktree.tracked

    @property
    def digests(self):
        return digests_of(self.contents)

    def document(self, relative):
        """The JSON object in the tracked file ``relative``, parsed from the hashed bytes; ``None`` if absent."""
        if relative not in self.contents:
            return None
        return parse_document(self.contents[relative], self.directory / relative)

    def fragments(self, directory):
        """The ``*.json`` files directly in the tracked ``directory``, sorted by name, as ``(name, object)``."""
        prefix = directory.rstrip("/") + "/"
        names = sorted(path for path in self.contents
                       if path.startswith(prefix) and "/" not in path[len(prefix):] and path.endswith(".json"))
        return [(name[len(prefix):], self.document(name)) for name in names]

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


def parse_document(data, path):
    try:
        value = read_json(data.decode("utf-8"))
    except (OrchestrationError, UnicodeError) as exc:
        raise OrchestrationError("A tracked file is not valid JSON.", "project_invalid", {"path": str(path)}) from exc
    if not isinstance(value, dict):
        raise OrchestrationError("A tracked file must hold a JSON object.", "project_invalid", {"path": str(path)})
    return value


def read_tracked(worktree):
    """Read and check the worktree's tracked project once.

    ``None`` when the worktree has no ``.xorchestrate/``; ``project_invalid``
    when it has one without ``project.json``.
    """
    if not worktree.tracked.exists() and not worktree.tracked.is_symlink():
        return None
    contents = read_tracked_files(worktree.tracked)
    if PROJECT_FILE not in contents:
        raise OrchestrationError("The repository has .xorchestrate/ but no .xorchestrate/project.json; add one "
                                 "(see docs/projects.md) or remove the directory and run `xorchestrate init`.",
                                 "project_invalid", {"path": str(worktree.tracked / PROJECT_FILE)})
    path = worktree.tracked / PROJECT_FILE
    name, release, requires = validate_project_document(parse_document(contents[PROJECT_FILE], path), path)
    return TrackedProject(worktree, name, release, requires, contents)


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
