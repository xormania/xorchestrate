"""Merge a project's configuration layers, substitute placeholders and validate.

Layers, later winning (D12):

1. the project's tracked ``policies/*.json`` (sorted by file name);
2. the project's tracked ``config.json``;
3. the companion's ``policies/*.json``;
4. the user's overlay ``<companion>/overlays/<project>.json``.

Each layer is applied as a JSON merge patch (RFC 7386): objects merge key by
key, any other value replaces, and ``null`` removes a setting. An explicit
override therefore wins for every setting, guardrails included.

The closed placeholder set is ``${repo}``, ``${data}``, ``${launchers}``,
``${home}`` and ``${instance}``. ``${repo}`` stays unresolved in the rendered
file: one server serves every worktree, so each call resolves it to its own
worktree root. Any other ``${...}`` is refused.
"""
from __future__ import annotations

import hashlib
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from ..composition import ProjectContext, validate_config
from ..model import OrchestrationError
from .home import account_home, read_document, write_document
from .project import CONFIG_FILE, POLICIES_DIRECTORY

PLACEHOLDERS = frozenset({"repo", "data", "launchers", "home", "instance"})
PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")
RENDERED_FILE = "config.rendered.json"
ABSENT = object()


def merge_patch(target, patch):
    """Apply ``patch`` to ``target`` (RFC 7386) without mutating either."""
    if not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(target) if isinstance(target, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        else:
            result[key] = merge_patch(result.get(key), value)
    return result


def _fragments(directory, source):
    directory = Path(directory)
    if not directory.is_dir():
        return []
    layers = []
    for path in sorted(directory.glob("*.json")):
        layers.append((f"{source}:{path.name}", read_document(path)))
    return layers


def layers(tracked, companion):
    """The ordered ``(source, document)`` layers of a project's configuration."""
    config = read_document(tracked.directory / CONFIG_FILE)
    if config is None:
        raise OrchestrationError("The project has no .xorchestrate/config.json.", "project_invalid",
                                 {"path": str(tracked.directory / CONFIG_FILE)})
    return [
        *_fragments(tracked.directory / POLICIES_DIRECTORY, "project_policy"),
        ("project_config", config),
        *_fragments(companion.policies, "companion_policy"),
        ("overlay", companion.overlay(tracked.name)),
    ]


def _leaves(patch, prefix=()):
    for key, value in patch.items():
        path = (*prefix, key)
        if isinstance(value, dict) and value:
            yield from _leaves(value, path)
        else:
            yield path, value


def _lookup(document, path):
    for key in path:
        if not isinstance(document, dict) or key not in document:
            return ABSENT
        document = document[key]
    return document


def _pointer(path):
    return "/" + "/".join(str(key).replace("~", "~0").replace("/", "~1") for key in path)


def overrides(ordered):
    """Every setting the companion layers change relative to the project's own layers."""
    project = {}
    index = 0
    for index, (source, document) in enumerate(ordered):
        if not source.startswith("project_"):
            break
        project = merge_patch(project, document)
    else:
        index = len(ordered)
    found = []
    for source, document in ordered[index:]:
        for path, value in _leaves(document):
            before = _lookup(project, path)
            if (before is ABSENT and value is None) or before == value:
                continue
            item = {"path": _pointer(path), "source": source}
            if before is not ABSENT:
                item["project"] = before
            item.update({"removed": True} if value is None else {"value": value})
            found.append(item)
    return found


def substitute(value, values):
    """Replace placeholders in every string; ``${repo}`` is kept for per-call resolution."""
    if isinstance(value, dict):
        return {key: substitute(item, values) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, values) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match):
        name = match.group(1)
        if name not in PLACEHOLDERS:
            raise OrchestrationError(f"Unknown placeholder ${{{name}}}; use one of "
                                     + ", ".join(f"${{{item}}}" for item in sorted(PLACEHOLDERS)) + ".",
                                     "configuration_error", {"placeholder": name})
        return match.group(0) if name == "repo" else values[name]

    return PLACEHOLDER.sub(replace, value)


def _complete_telemetry(config, context):
    """A partial telemetry section (options without an adapter) completes the project defaults.

    This lets an overlay change only, say, the ``dataset_id`` (D18). An absent
    section stays absent: ``build`` supplies the same defaults.
    """
    section = config.get("telemetry")
    if isinstance(section, dict) and "adapter" not in section:
        defaults = context.telemetry_defaults().specification()
        options = section.get("options", {})
        completed = {**defaults, "options": {**defaults["options"], **(options if isinstance(options, dict) else {})}}
        if "enabled" in section:
            completed["enabled"] = section["enabled"]
        config["telemetry"] = completed


def _default_store(config, data_dir):
    store = config.get("store")
    if store is None:
        config["store"] = {"adapter": "sqlite", "options": {"path": str(Path(data_dir) / "state.sqlite3")}}
    elif isinstance(store, dict) and store.get("adapter") == "sqlite":
        options = store.setdefault("options", {})
        if isinstance(options, dict):
            options.setdefault("path", str(Path(data_dir) / "state.sqlite3"))


def launched_commands(config):
    """The command arrays a configuration would launch, for the approval summary and doctor."""
    commands = []
    for kind in ("providers", "sessions", "evaluators"):
        entries = config.get(kind, {})
        if not isinstance(entries, dict):
            continue
        for name, specification in entries.items():
            if not isinstance(specification, dict) or specification.get("enabled") is False:
                continue
            options = specification.get("options", {})
            if not isinstance(options, dict):
                continue
            for key in ("command", "discovery_command"):
                if isinstance(options.get(key), list):
                    commands.append({"kind": kind, "name": name, "adapter": specification.get("adapter"),
                                     "option": key, "command": list(options[key]),
                                     "pass_env": list(options.get("pass_env", []))
                                     if isinstance(options.get("pass_env"), list) else []})
    return commands


@dataclass(frozen=True)
class Rendered:
    config: dict
    path: Path
    overrides: list
    warnings: tuple


def merged(tracked, companion):
    """The merged, unsubstituted configuration and its overrides."""
    ordered = layers(tracked, companion)
    config = {}
    for _source, document in ordered:
        config = merge_patch(config, document)
    return config, overrides(ordered)


def render(tracked, companion, context, *, write=True):
    """Merge, substitute and validate; write ``config.rendered.json`` in the data directory."""
    if not isinstance(context, ProjectContext):
        raise OrchestrationError("Rendering requires a project context.", "configuration_error")
    data_dir = Path(context.data_dir)
    config, found = merged(tracked, companion)
    values = {"data": str(data_dir), "launchers": str(data_dir / "launchers"), "home": str(account_home()),
              "instance": context.instance_id}
    config = substitute(config, values)
    _default_store(config, data_dir)
    _complete_telemetry(config, context)
    resolved = validate_config(config, data_dir, telemetry_defaults=context.telemetry_defaults())
    path = data_dir / RENDERED_FILE
    if write:
        write_document(path, config)
    return Rendered(config, path, found, resolved.warnings)


def copy_launchers(companion, data_dir):
    """Copy the companion's launchers under the instance (mode 0755); return their digests."""
    source, target = companion.launchers, Path(data_dir) / "launchers"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    digests = {}
    wanted = set()
    if source.is_dir():
        for path in sorted(source.iterdir()):
            if path.is_symlink() or not path.is_file():
                continue
            data = path.read_bytes()
            temporary = target / f".{path.name}.{os.getpid()}.tmp"
            temporary.write_bytes(data)
            os.chmod(temporary, 0o755)  # noqa: S103 - launchers are executables
            os.replace(temporary, target / path.name)
            digests[path.name] = hashlib.sha256(data).hexdigest()
            wanted.add(path.name)
    for stale in target.iterdir():
        if stale.name not in wanted and stale.is_file() and not stale.name.startswith("."):
            stale.unlink()
    return digests
