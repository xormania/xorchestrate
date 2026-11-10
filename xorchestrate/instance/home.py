"""The per-user home: ``$XORCHESTRATE_HOME`` (absolute) or ``~/.xorchestrate``.

```
<home>/settings.json        {"schema": 1, "companion": "/abs/path"}
<home>/registry.json        instance id -> project name, repository, git common directory
<home>/toolchains/<release>/ release-keyed environments (see handoff.py)
<home>/projects/<project>/  one instance per project per machine, shared by all worktrees
<home>/servers/             project server locks, sockets and discovery records
```

``~/.xorchestrate`` and a repository's ``.xorchestrate/`` share a name and hold
different things: this one is machine-local state, that one is the project's
tracked configuration.
"""
from __future__ import annotations

import json
import os
import pwd
from dataclasses import dataclass
from pathlib import Path

from ..composition import read_json
from ..model import OrchestrationError

HOME_VARIABLE = "XORCHESTRATE_HOME"
SCHEMA = 1


def absolute_setting(environ, name):
    """An absolute path from ``environ[name]``, ``None`` when unset; a relative one is refused."""
    value = environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise OrchestrationError(f"{name} must be an absolute path.", "invalid_configuration", {name: value})
    return path


def account_home():
    """The account's home directory from the user database, not ``$HOME``."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def xorchestrate_home(environ=None):
    """``$XORCHESTRATE_HOME`` if set (absolute), else ``.xorchestrate`` in the account's home.

    The account home comes from the user database, not ``$HOME``, so clients
    with different shells or sudo settings agree.
    """
    environ = os.environ if environ is None else environ
    configured = absolute_setting(environ, HOME_VARIABLE)
    return configured if configured is not None else account_home() / ".xorchestrate"


def private_directory(path):
    """Create ``path`` (mode 0700) if needed and refuse it unless it is this user's private directory."""
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not path.is_dir() or path.is_symlink() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise OrchestrationError("Xorchestrate home directories must be private directories owned by this user.",
                                 "invalid_configuration", {"path": str(path)})
    return path


def read_document(path, default=None):
    """Read a JSON object, or return ``default`` when the file does not exist."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except OSError as exc:
        raise OrchestrationError("Cannot read a required file.", "io_error", {"path": str(path)}) from exc
    try:
        value = read_json(text)
    except OrchestrationError as exc:
        raise OrchestrationError("A required file is not valid JSON.", "invalid_configuration",
                                 {"path": str(path)}) from exc
    if not isinstance(value, dict):
        raise OrchestrationError("A required file must hold a JSON object.", "invalid_configuration",
                                 {"path": str(path)})
    return value


def write_document(path, value, mode=0o600):
    """Write a JSON object atomically: a temporary file beside it, then a rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path


@dataclass(frozen=True)
class Home:
    """Paths and documents under one xorchestrate home."""

    root: Path

    @classmethod
    def locate(cls, environ=None):
        return cls(xorchestrate_home(environ))

    def ensure(self):
        private_directory(self.root)
        return self

    @property
    def settings_path(self):
        return self.root / "settings.json"

    @property
    def registry_path(self):
        return self.root / "registry.json"

    @property
    def toolchains(self):
        return self.root / "toolchains"

    @property
    def projects(self):
        return self.root / "projects"

    @property
    def servers(self):
        return self.root / "servers"

    def project_dir(self, name):
        return self.projects / name

    def settings(self):
        settings = read_document(self.settings_path, {"schema": SCHEMA})
        if settings.get("schema") != SCHEMA:
            raise OrchestrationError("Unsupported settings schema.", "invalid_configuration",
                                     {"path": str(self.settings_path)})
        return settings

    def update_settings(self, **values):
        self.ensure()
        settings = {**self.settings(), **values, "schema": SCHEMA}
        write_document(self.settings_path, settings)
        return settings

    def companion_path(self):
        """The registered companion, or ``None`` before ``setup``."""
        value = self.settings().get("companion")
        if value is None:
            return None
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise OrchestrationError("settings.json companion must be an absolute path.", "invalid_configuration",
                                     {"path": str(self.settings_path)})
        return Path(value)

    def registry(self):
        registry = read_document(self.registry_path, {"schema": SCHEMA, "instances": {}})
        if registry.get("schema") != SCHEMA or not isinstance(registry.get("instances"), dict):
            raise OrchestrationError("Unsupported registry schema.", "invalid_configuration",
                                     {"path": str(self.registry_path)})
        return registry

    def register(self, instance_id, entry):
        self.ensure()
        registry = self.registry()
        registry["instances"][instance_id] = entry
        write_document(self.registry_path, registry)
