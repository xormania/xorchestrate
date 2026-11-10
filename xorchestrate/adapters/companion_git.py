"""``Companion`` in a local directory, normally a private git repository.

```
<path>/<kind>/<project>/<key>   a project's records, for example overlay/app/overlay.json
<path>/<kind>/<key>             project-independent records, for example policies/team.json
```

A key with ``/`` separators is a path below that directory, for example
``telemetry/app/<instance>/<bundle>/telemetry.sqlite3``. No component may be a
symbolic link.

Options: ``path``, absolute. The location is unsafe inside a working tree
that holds a project's ``.xorchestrate/``: agents and other people's commits
could then write the approvals meant to guard against them. ``remote()`` is
the ``origin`` URL of the repository the directory is in.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..model import OrchestrationError
from .local_socket import check_private_location

LAYOUT = ("overlay", "approvals", "policies", "launchers")
REQUIRED = ("overlay", "approvals")
TRACKED_DIRECTORY = ".xorchestrate"
README = """# Xorchestrate companion

This private repository holds your own xorchestrate records for every project:

- `overlay/<project>/overlay.json`: your overrides of a project's
  configuration. They win over the project's tracked configuration,
  guardrails included.
- `approvals/<project>/approvals.json`: the hashes of tracked project
  configurations you approved with `xorchestrate approve`.
- `policies/*.json`: fragments merged above every project's `config.json`.
- `launchers/`: executables your configurations reference as `${launchers}`.
- `telemetry/<project>/<instance>/<bundle>/`: telemetry snapshot bundles
  archived with `xorchestrate telemetry archive`. Their `*.sqlite3` files are
  tracked with Git LFS (see `.gitattributes`); run `git lfs install` once.

Xorchestrate writes these files but never commits them; review and commit them
yourself. Keep it under version control and private.
"""
GITATTRIBUTES = "telemetry/**/*.sqlite3 filter=lfs diff=lfs merge=lfs -text\n"


NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _name(value, what):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise OrchestrationError(f"Invalid companion record {what}.", "invalid_arguments", {what: value})
    return value


def _key(value):
    """A key: one or more names separated by ``/``; returns its components."""
    if not isinstance(value, str):
        raise OrchestrationError("Invalid companion record key.", "invalid_arguments", {"key": value})
    parts = value.split("/")
    if not all(NAME.fullmatch(part) for part in parts):
        raise OrchestrationError("Invalid companion record key.", "invalid_arguments", {"key": value})
    return parts


class DirectoryRecords:
    """One project's records as ``<kind>/<project>/<key>``, or project-independent ones as ``<kind>/<key>``."""

    def __init__(self, root, project):
        self.root = root
        self.project = None if project is None else _name(project, "project")

    def _directory(self, kind):
        directory = self.root / _name(kind, "kind")
        return directory / self.project if self.project is not None else directory

    def _path(self, kind, key):
        """The record's path, refusing a symbolic link or a record where a directory is needed."""
        kind_directory = self.root / _name(kind, "kind")
        path = self._directory(kind)
        for parent in (kind_directory, path) if path != kind_directory else (path,):
            if parent.is_symlink():
                raise OrchestrationError("A companion record path crosses a symbolic link.", "invalid_arguments",
                                         {"key": key})
        for part in _key(key):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise OrchestrationError("A companion record path crosses a symbolic link or another record.",
                                         "invalid_arguments", {"key": key})
            path = path / part
        return path

    def keys(self, kind, *, recursive=False):
        directory = self._directory(kind)
        if not directory.is_dir() or directory.is_symlink() or (self.root / kind).is_symlink():
            return []
        if not recursive:
            return sorted(path.name for path in directory.iterdir()
                          if path.is_file() and not path.is_symlink() and NAME.fullmatch(path.name))
        found = []
        for current, directories, files in os.walk(directory, followlinks=False):
            relative = Path(current).relative_to(directory)
            if any(not NAME.fullmatch(part) for part in relative.parts):
                directories[:] = []
                continue
            found += [(relative / name).as_posix() for name in files
                      if NAME.fullmatch(name) and not (Path(current) / name).is_symlink()]
        return sorted(found)

    def read(self, kind, key):
        path = self._path(kind, key)
        return None if path.is_symlink() or not path.is_file() else path.read_bytes()

    def write(self, kind, key, data, *, create_only=False):
        path = self._path(kind, key)
        if not isinstance(data, bytes):
            raise OrchestrationError("Companion records are bytes.", "invalid_arguments")
        if path.is_symlink() or path.is_dir():
            raise OrchestrationError("A companion record cannot replace a link or a directory.", "invalid_arguments",
                                     {"key": key})
        path.parent.mkdir(parents=True, exist_ok=True)
        if create_only:
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            except FileExistsError:
                return False
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
            return True
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return True


class GitDirectoryCompanion:
    def __init__(self, options, *, version_control):
        if not isinstance(options, dict) or options.keys() - {"path"}:
            raise OrchestrationError("The git-directory companion accepts path.", "invalid_configuration")
        path = options.get("path")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise OrchestrationError("The git-directory companion path must be absolute.", "invalid_configuration")
        self.root = Path(path)
        self.location = str(self.root)
        self.version_control = version_control

    def validate_location(self):
        """Refuse a location others could write to, or that lies inside any project.

        - Ownership: the companion directory must be this user's and not
          writable by group or others; every enclosing directory, as given and
          resolved, must be owned by this user or root and not writable by
          group or others unless sticky (the private-directory rule of the
          project server's sockets). Before the companion exists, its nearest
          existing directory and the ones above it are checked the same way.
        - Projects: no enclosing directory may hold a project
          (``.xorchestrate/project.json``), however many repositories are
          nested in between, and the innermost git worktree must not have a
          ``.xorchestrate/``.
        """
        existing = self.root
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        try:
            check_private_location(self.root)
        except OrchestrationError as error:
            return f"{error.details.get('directory')} is unsafe: {error.details.get('reason')}"
        for directory in dict.fromkeys([self.root, *self.root.parents, *existing.resolve().parents]):
            if (directory / TRACKED_DIRECTORY / "project.json").is_file():
                return f"it is inside the project at {directory}"
        worktree = self.version_control.worktree(str(existing))
        if worktree is not None and (Path(worktree.root) / TRACKED_DIRECTORY).exists():
            return f"it is inside the project worktree {worktree.root}"
        return None

    def exists(self):
        return self.root.is_dir() and all((self.root / name).is_dir() for name in REQUIRED)

    def initialize(self):
        if self.root.exists() and not self.root.is_dir():
            raise OrchestrationError("The companion path is not a directory.", "invalid_arguments",
                                     {"path": self.location})
        created = []
        if not self.root.exists():
            self.root.mkdir(mode=0o700, parents=True)
            created.append(str(self.root))
        for name in LAYOUT:
            directory = self.root / name
            if not directory.exists():
                directory.mkdir(parents=True)
                created.append(str(directory))
        for name, text in (("README.md", README), (".gitattributes", GITATTRIBUTES)):
            target = self.root / name
            if not target.exists():
                target.write_text(text, encoding="utf-8")
                created.append(str(target))
        return created

    def records(self, project):
        return DirectoryRecords(self.root, project)

    def remote(self):
        worktree = self.version_control.worktree(str(self.root)) if self.root.is_dir() else None
        return None if worktree is None else self.version_control.remote_url(worktree.root)

    def revision(self):
        worktree = self.version_control.worktree(str(self.root)) if self.root.is_dir() else None
        return None if worktree is None else self.version_control.revision(worktree.root)
