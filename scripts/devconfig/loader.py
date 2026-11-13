"""Resolve developer settings by precedence: flag, environment, repository file, user file, default."""

from __future__ import annotations

import json
import os
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


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        raise DevConfigError("NOT_A_REPOSITORY", f"{cwd}: {result.stderr.strip() or 'git failed'}")
    return result.stdout.strip()


def find_main_checkout(start: Path, git: Git | None = None) -> Path:
    """The main checkout of the repository containing ``start``, also from inside a linked tree."""
    common = Path((git or _run_git)(["rev-parse", "--path-format=absolute", "--git-common-dir"], Path(start)))
    return common.parent.resolve()


def xdg_dir(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if value:
        return value
    return str(Path(_home(env)) / XDG_FALLBACKS[name])


def user_file(env: Mapping[str, str]) -> Path:
    return Path(xdg_dir(env, "XDG_CONFIG_HOME")).joinpath(*USER_FILE)


def _home(env: Mapping[str, str]) -> str:
    return env.get("HOME") or str(Path.home())


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


def load(repo: Path, *, env: Mapping[str, str], flags: Mapping[str, str | None] | None = None,
         settings: Iterable[Setting] = SETTINGS, git: Git | None = None) -> DevConfig:
    """Resolve every declared setting for the repository containing ``repo``.

    Only ``env`` is consulted, never the process environment, and from it only names
    in the developer namespace.
    """
    settings = tuple(settings)
    declared = {setting.key for setting in settings}
    main = find_main_checkout(Path(repo), git)
    warnings: list[tuple[str, str | None, int | None, str]] = []

    layers: list[tuple[str, dict[str, tuple[str, Source]]]] = []
    layers.append(("flag", {key: (value, Source("flag")) for key, value in (flags or {}).items()
                            if value is not None}))
    from_env: dict[str, tuple[str, Source]] = {}
    for name, value in env.items():
        if not name.startswith(PREFIX):
            continue
        if name in declared:
            from_env[name] = (value, Source("env"))
        else:
            warnings.append(("UNKNOWN_KEY", None, None, name))
    layers.append(("env", from_env))
    for kind, path in (("repo_file", main / REPO_FILE), ("user_file", user_file(env))):
        layers.append((kind, _read_file(kind, path, declared, warnings)))

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


def _read_file(kind: str, path: Path, declared: set[str],
               warnings: list[tuple[str, str | None, int | None, str]]) -> dict[str, tuple[str, Source]]:
    if not path.is_file():
        return {}
    values, parse_warnings = parse_env_file(path.read_text(encoding="utf-8"), str(path))
    warnings.extend(parse_warnings)
    found: dict[str, tuple[str, Source]] = {}
    for key, (value, line) in values.items():
        if key in declared:
            found[key] = (value, Source(kind, str(path), line))
        else:
            warnings.append(("UNKNOWN_KEY", str(path), line, key))
    return found


def _typed(setting: Setting, raw: str | None, source: Source, env: Mapping[str, str], main: Path) -> Any:
    if raw is None:
        return None
    if source.kind == "default" and setting.kind in ("path", "str"):
        raw = _expand_default(raw, env, main)
    try:
        if setting.kind == "str":
            return raw
        if setting.kind == "path":
            return _path(raw, source, env)
        if setting.kind == "int":
            return int(raw)
        if setting.kind == "float":
            return float(raw)
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
    values = {name: xdg_dir(env, name) for name in XDG_FALLBACKS}
    return template.format(repo_name=main.name, **values)


def _path(raw: str, source: Source, env: Mapping[str, str]) -> Path:
    if not raw:
        raise ValueError("empty path")
    if raw == "~" or raw.startswith("~/"):
        raw = _home(env) + raw[1:]
    path = Path(raw)
    if not path.is_absolute():
        base = Path(source.path).parent if source.path else Path(os.getcwd())
        path = base / path
    return path.resolve(strict=False)
