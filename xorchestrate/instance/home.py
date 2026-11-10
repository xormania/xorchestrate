"""The per-user home: ``$XORCHESTRATE_HOME`` (absolute) or ``~/.xorchestrate``.

```
<home>/settings.json        {"schema": 1, "companion": {"adapter": "git-directory", "options": {"path": "/abs"}}}
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

import errno
import json
import os
import pwd
from dataclasses import dataclass
from pathlib import Path

from ..adapters.local_socket import check_private_location, open_private_file
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


def read_document(path, default=None, *, private=False):
    """Read a JSON object, or return ``default`` when the file does not exist.

    ``private`` files (the home's settings, registry and instance records) are
    opened without following symbolic links and must be regular files owned
    by this user with no group or other permissions.
    """
    path = Path(path)
    try:
        if private:
            with os.fdopen(open_private_file(path, os.O_RDONLY), "rb") as source:
                text = source.read().decode("utf-8")
        else:
            text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    except UnicodeError as exc:
        raise OrchestrationError("A required file is not valid JSON.", "invalid_configuration",
                                 {"path": str(path)}) from exc
    except OSError as exc:
        if private and exc.errno == errno.ELOOP:
            raise OrchestrationError("A home file must be a private regular file, not a symbolic link.",
                                     "invalid_configuration", {"path": str(path)}) from exc
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
        """The home, refused unless it is (or will be created as) a private directory no other user can replace.

        Its settings choose programs xorchestrate runs (for example the
        version-control adapter), so the private-directory rule of the server
        sockets applies on every open, not only on creation.
        """
        root = xorchestrate_home(environ)
        try:
            check_private_location(root)
        except OrchestrationError as error:
            raise OrchestrationError(f"The xorchestrate home {root} is unsafe: {error.details.get('reason')}.",
                                     "invalid_configuration", {"path": str(root), **error.details}) from error
        return cls(root)

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
        settings = read_document(self.settings_path, {"schema": SCHEMA}, private=True)
        if settings.get("schema") != SCHEMA:
            raise OrchestrationError("Unsupported settings schema.", "invalid_configuration",
                                     {"path": str(self.settings_path)})
        return settings

    def update_settings(self, **values):
        self.ensure()
        settings = {**self.settings(), **values, "schema": SCHEMA}
        write_document(self.settings_path, settings)
        return settings

    def registry(self):
        registry = read_document(self.registry_path, {"schema": SCHEMA, "instances": {}}, private=True)
        if registry.get("schema") != SCHEMA or not isinstance(registry.get("instances"), dict):
            raise OrchestrationError("Unsupported registry schema.", "invalid_configuration",
                                     {"path": str(self.registry_path)})
        return registry

    def register(self, instance_id, entry):
        self.ensure()
        registry = self.registry()
        registry["instances"][instance_id] = entry
        write_document(self.registry_path, registry)
