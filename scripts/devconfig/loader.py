"""Resolve developer settings by precedence: flag, environment, repository file, user file, default."""

from __future__ import annotations

import json
import math
import struct
import os
import pwd
import shlex
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from scripts.devconfig.keys import SETTINGS
from scripts.devconfig.model import DevConfigError, Setting, Source
from scripts.devconfig.parser import parse_env_file

REPO_FILE = ".devtools.env"
USER_FILE = ("xorchestrate", "devtools.env")
PREFIX = "XORCHESTRATE_DEV_"
XDG_FALLBACKS = {"XDG_CONFIG_HOME": ".config", "XDG_DATA_HOME": ".local/share",
                 "XDG_STATE_HOME": ".local/state", "XDG_CACHE_HOME": ".cache"}

Git = Callable[[list[str], Path], str]
GIT_LOCATION_VARIABLES = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")
"""Variables that would point git at another repository than the one asked about."""


def _run_git(args: list[str], cwd: Path) -> str:
    env = {name: value for name, value in os.environ.items() if name not in GIT_LOCATION_VARIABLES}
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False, env=env)
    except OSError as error:
        raise DevConfigError("NOT_A_REPOSITORY", f"{cwd}: cannot run git: {error}") from None
    if result.returncode:
        raise DevConfigError("NOT_A_REPOSITORY", f"{cwd}: {result.stderr.strip() or 'git failed'}")
    return result.stdout.strip()


def _ask(git: Git | None, args: list[str], start: Path) -> str:
    start = Path(start)
    if not start.is_dir():
        raise DevConfigError("NOT_A_REPOSITORY", f"{start} is not a directory")
    try:
        return (git or _run_git)(args, start)
    except DevConfigError:
        raise
    except Exception as error:  # noqa: BLE001 - an injected git reports failures its own way
        raise DevConfigError("NOT_A_REPOSITORY", f"{start}: {error}") from None


def find_main_checkout(start: Path, git: Git | None = None) -> Path:
    """The main checkout of the repository containing ``start``.

    A checkout whose git directory is the repository's own (a main checkout or a
    submodule) is its own main checkout; a linked tree defers to the first registered
    work tree. A bare main repository has no checkout to read settings from and is refused.
    """
    git_dir, common_dir = _ask(git, ["rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir"],
                               start).splitlines()[:2]
    if Path(git_dir).resolve() == Path(common_dir).resolve() and _ask(
            git, ["rev-parse", "--is-bare-repository"], start).strip() != "true":
        return find_checkout(start, git)
    listing = _ask(git, ["worktree", "list", "--porcelain"], start)
    first = listing.strip().split("\n\n")[0].splitlines() if listing.strip() else []
    if not first or not first[0].startswith("worktree ") or "bare" in first:
        raise DevConfigError("NOT_A_REPOSITORY", f"{start}: the repository has no main checkout")
    return Path(first[0].removeprefix("worktree ")).resolve()


def find_checkout(start: Path, git: Git | None = None) -> Path:
    """The work tree containing ``start`` (a linked tree is its own checkout here)."""
    return Path(_ask(git, ["rev-parse", "--show-toplevel"], start)).resolve()


def xdg_dir(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value and os.path.isabs(value):
        return value
    return str(Path(_home(env)) / XDG_FALLBACKS[name])


def user_file(env: Mapping[str, str]) -> Path:
    return Path(xdg_dir(env, "XDG_CONFIG_HOME")).joinpath(*USER_FILE)


def _home(env: Mapping[str, str]) -> str:
    """HOME from the given environment, else the account's home; never the process environment."""
    return env.get("HOME") or pwd.getpwuid(os.getuid()).pw_dir


class DevConfig:
    """Resolved settings with the source of each value."""

    def __init__(self, settings: Iterable[Setting], values: Mapping[str, Any], sources: Mapping[str, Source],
                 warnings: list[tuple[str, str | None, int | None, str]]) -> None:
        self._settings = {setting.key: setting for setting in settings}
        self._values = dict(values)
        self._sources = dict(sources)
        self.warnings = tuple(warnings)

    def _setting(self, key: str) -> Setting:
        try:
            return self._settings[key]
        except KeyError:
            raise DevConfigError("UNKNOWN_SETTING", f"{key} is not a declared developer setting") from None

    def get(self, key: str) -> Any:
        self._setting(key)
        return self._values[key]

    def source(self, key: str) -> Source:
        self._setting(key)
        return self._sources[key]

    def display(self, key: str) -> str:
        """The value as text: what ``show`` and ``get`` print and runs record ("" when unset)."""
        return _display(self._setting(key), self.get(key))

    def explain(self) -> list[str]:
        return [f"{key}={_display(setting, self._values[key])}  # {self._sources[key].describe()}"
                for key, setting in self._settings.items()]

    def evidence(self) -> dict[str, dict[str, Any]]:
        return {key: {"value": _display(setting, self._values[key]), "source": self._sources[key].describe()}
                for key, setting in self._settings.items() if setting.evidence}


def _display(setting: Setting, value: Any) -> str:
    if value is None:
        return ""
    if setting.kind == "command":
        return shlex.join(value)
    if setting.kind == "json":
        return json.dumps(value, sort_keys=True)
    return str(value)


def discover_by_files(start: Path) -> tuple[Path | None, Path | None, str | None]:
    """The main checkout and git common directory for ``start`` by plain file reads, never a process.

    Returns ``(main, common, None)``, or ``(None, None, reason)`` when the layout is not
    recognised: no ``.git`` entry, an unreadable one, or a bare main repository.
    """
    current = Path(os.path.abspath(start))
    for directory in (current, *current.parents):
        entry = directory / ".git"
        if entry.is_dir():
            return directory.resolve(), entry.resolve(), None
        if not entry.is_file():
            continue
        try:
            text = entry.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            return None, None, f"cannot read {entry}: {error}"
        if not text.startswith("gitdir:"):
            return None, None, f"{entry} is not a gitdir pointer"
        gitdir = (directory / text.removeprefix("gitdir:").strip()).resolve()
        pointer = gitdir / "commondir"
        if not pointer.is_file():
            return directory.resolve(), gitdir, None  # a submodule or a separate git directory
        try:
            common = (gitdir / pointer.read_text(encoding="utf-8").strip()).resolve()
        except (OSError, UnicodeDecodeError) as error:
            return None, None, f"cannot read {pointer}: {error}"
        if common.name != ".git":
            return None, None, "the repository's main checkout is bare"
        return common.parent, common, None
    return None, None, "no git repository found"


def tracked_by_index(common: Path, name: str = REPO_FILE) -> bool | None:
    """Whether ``name`` (at the top of the checkout) is in git's index, read directly.

    True or False for index versions 2 and 3 of a SHA-1 repository; None when the index
    cannot be understood (another version, SHA-256, damaged). No index means untracked.
    """
    index = common / "index"
    if not index.exists():
        return False
    try:
        data = index.read_bytes()
        config = (common / "config").read_text(encoding="utf-8", errors="replace") if (common / "config").is_file() \
            else ""
    except OSError:
        return None
    if "objectformat" in config.replace(" ", "").lower() and "sha256" in config.lower():
        return None
    if len(data) < 12 or data[:4] != b"DIRC":
        return None
    version, count = struct.unpack(">II", data[4:12])
    if version not in (2, 3):
        return None
    wanted, offset = name.encode(), 12
    for _ in range(count):
        fixed = 62
        if offset + fixed > len(data):
            return None
        flags = struct.unpack(">H", data[offset + 60:offset + 62])[0]
        if flags & 0x4000:
            if version < 3:
                return None
            fixed += 2
        start = offset + fixed
        length = flags & 0x0FFF
        if length == 0x0FFF:
            end = data.find(b"\0", start)
            if end < 0:
                return None
        else:
            end = start + length
        if end > len(data):
            return None
        if data[start:end] == wanted:
            return True
        offset += ((fixed + (end - start) + 8) // 8) * 8
    return False


def load(repo: Path, *, env: Mapping[str, str], flags: Mapping[str, str | None] | None = None,
         settings: Iterable[Setting] = SETTINGS, git: Git | None = None, discovery: str = "git") -> DevConfig:
    """Resolve every declared setting for the repository containing ``repo``.

    Only ``env`` is consulted, never the process environment, and from it only names
    in the developer namespace. ``discovery="files"`` finds the repository with plain
    file reads and never starts a process; a layout it does not recognise skips the
    repository file with a REPO_FILE_SKIPPED warning instead of failing.
    """
    settings = tuple(settings)
    declared = {setting.key for setting in settings}
    warnings: list[tuple[str, str | None, int | None, str]] = []
    skip_reason = None
    if discovery == "files":
        main, common, skip_reason = discover_by_files(Path(repo))
        if main is None:
            main = Path(os.path.abspath(repo))
    elif discovery == "git":
        main, common = find_main_checkout(Path(repo), git), None
    else:
        raise DevConfigError("UNKNOWN_SETTING", f"unknown discovery {discovery!r}")

    layers: list[tuple[str, dict[str, tuple[str, Source]]]] = []
    layers.append(("flag", {key: (value, Source("flag")) for key, value in (flags or {}).items()
                            if value}))
    from_env: dict[str, tuple[str, Source]] = {}
    for name, value in env.items():
        if not name.startswith(PREFIX):
            continue
        if name in declared:
            if value:
                from_env[name] = (value, Source("env"))
        else:
            warnings.append(("UNKNOWN_KEY", None, None, name))
    layers.append(("env", from_env))
    repo_file = main / REPO_FILE
    read_repo_file = skip_reason is None
    if read_repo_file and repo_file.is_file():
        tracked = _tracked(git, main) if common is None else tracked_by_index(common)
        if tracked is None:
            skip_reason, read_repo_file = "git's index could not be read without running git", False
        elif tracked:
            raise DevConfigError("ENV_FILE_TRACKED", f"{repo_file} is tracked by git; local settings must stay "
                                 "untracked (git rm --cached it)")
    if skip_reason is not None:
        warnings.append(("REPO_FILE_SKIPPED", str(repo_file) if common is not None else None, None, skip_reason))
    layers.append(("repo_file", _read_file("repo_file", repo_file, declared, warnings) if read_repo_file else {}))
    layers.append(("user_file", _read_file("user_file", user_file(env), declared, warnings)))

    values: dict[str, Any] = {}
    sources: dict[str, Source] = {}
    for setting in settings:
        for _, layer in layers:
            if setting.key in layer:
                raw, source = layer[setting.key]
                break
        else:
            raw, source = setting.default, Source("default")
        values[setting.key] = _typed(setting, raw, source, env, main)
        sources[setting.key] = source
    return DevConfig(settings, values, sources, warnings)


def _tracked(git: Git | None, main: Path) -> bool:
    try:
        _ask(git, ["ls-files", "--error-unmatch", "--", REPO_FILE], main)
    except DevConfigError:
        return False
    return True


def _read_file(kind: str, path: Path, declared: set[str],
               warnings: list[tuple[str, str | None, int | None, str]]) -> dict[str, tuple[str, Source]]:
    if not path.is_file():
        return {}
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise DevConfigError("ENV_FILE_INVALID", f"{path}: cannot read it as UTF-8 text: {error}") from None
    values, parse_warnings = parse_env_file(text, str(path))
    warnings.extend(parse_warnings)
    found: dict[str, tuple[str, Source]] = {}
    for key, (value, line) in values.items():
        if key in declared and not value:
            continue
        if key in declared:
            found[key] = (value, Source(kind, str(path), line))
        else:
            warnings.append(("UNKNOWN_KEY", str(path), line, key))
    return found


def _typed(setting: Setting, raw: str | None, source: Source, env: Mapping[str, str], main: Path) -> Any:
    if raw is None:
        return None
    if source.kind == "default" and setting.kind == "path":
        raw = _expand_default(raw, env, main)
    try:
        if setting.kind == "str":
            return raw
        if setting.kind == "path":
            return _path(raw, source, env)
        if setting.kind == "int":
            return int(raw)
        if setting.kind == "float":
            number = float(raw)
            if not math.isfinite(number):
                raise ValueError("not a finite number")
            return number
        if setting.kind == "command":
            command = shlex.split(raw)
            if not command:
                raise ValueError("empty command")
            return command
        if setting.kind == "json":
            value = json.loads(raw)
            if setting.json_type is not None and not isinstance(value, setting.json_type):
                raise ValueError(f"expected a JSON {setting.json_type.__name__}")
            return value
    except ValueError as error:
        raise DevConfigError("TYPE_INVALID",
                             f"{setting.key} from {source.describe()}: not a valid {setting.kind}: {error}") from None
    raise DevConfigError("TYPE_INVALID", f"{setting.key}: unknown kind {setting.kind}")


def _expand_default(template: str, env: Mapping[str, str], main: Path) -> str:
    values = {name: xdg_dir(env, name) for name in XDG_FALLBACKS} | {"repo_name": main.name}
    for name, value in values.items():
        template = template.replace("{" + name + "}", value)
    return template


def _path(raw: str, source: Source, env: Mapping[str, str]) -> Path:
    if not raw:
        raise ValueError("empty path")
    if raw == "~" or raw.startswith("~/"):
        raw = _home(env) + raw[1:]
    path = Path(raw)
    if not path.is_absolute():
        base = Path(source.path).parent if source.path else Path(os.getcwd())
        path = base / path
    # Normalized but never resolved: a configured symbolic link (a virtualenv's python, say) stays as given.
    return Path(os.path.normpath(os.path.abspath(path)))
