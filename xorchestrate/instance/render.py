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
from . import companion as records
from .home import account_home, write_document
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


def layers(tracked, companion):
    """The ordered ``(source, document)`` layers of a project's configuration.

    The project's layers are parsed from the bytes that were hashed for
    approval, never re-read from disk.
    """
    config = tracked.document(CONFIG_FILE)
    if config is None:
        raise OrchestrationError("The project has no .xorchestrate/config.json.", "project_invalid",
                                 {"path": str(tracked.directory / CONFIG_FILE)})
    return [
        *((f"project_policy:{name}", document) for name, document in tracked.fragments(POLICIES_DIRECTORY)),
        ("project_config", config),
        *((f"companion_policy:{name}", document) for name, document in records.policies(companion)),
        ("overlay", records.overlay(companion, tracked.name)),
    ]


def _differences(project, effective, path=()):
    """Leaf paths where the effective configuration differs from the project's own."""
    if isinstance(effective, dict) and (isinstance(project, dict) or project is ABSENT):
        # Recurse into added objects too, so each new leaf is reported (an empty one is no change).
        before = project if isinstance(project, dict) else {}
        for key in sorted(set(before) | set(effective), key=str):
            yield from _differences(before.get(key, ABSENT), effective.get(key, ABSENT), (*path, key))
    elif project != effective:
        yield path, project, effective


def _touches(patch, path):
    """Whether a merge patch sets, replaces or removes ``path`` (or an enclosing value)."""
    for key in path:
        if not isinstance(patch, dict):
            return True
        if key not in patch:
            return False
        patch = patch[key]
    return True


def _pointer(path):
    return "/" + "/".join(str(key).replace("~", "~0").replace("/", "~1") for key in path)


def overrides(ordered):
    """Every setting whose effective value differs from the project's own layers.

    Computed from the effective difference, so a no-op patch, or a change a
    later layer reverts, is not reported. ``source`` is the last companion
    layer that touched the setting.
    """
    project = effective = {}
    companion = []
    for source, document in ordered:
        if source.startswith("project_"):
            project = merge_patch(project, document)
        else:
            companion.append((source, document))
        effective = merge_patch(effective, document)
    found = []
    for path, before, after in _differences(project, effective):
        source = next((name for name, document in reversed(companion) if _touches(document, path)), "companion")
        item = {"path": _pointer(path), "source": source}
        if before is not ABSENT:
            item["project"] = before
        item.update({"removed": True} if after is ABSENT else {"value": after})
        found.append(item)
    return found


# Where ${repo} may appear: values a launched process receives for one call.
REPO_ALLOWED_OPTIONS = frozenset({"command", "discovery_command", "cwd", "env"})


def check_repo_placeholders(value, path=()):
    """Refuse ``${repo}`` outside process commands, ``cwd`` and ``env`` of providers, sessions and evaluators.

    Only those are resolved per call; elsewhere (a store path, the telemetry
    root) it would be used literally, or shared across worktrees.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            check_repo_placeholders(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_repo_placeholders(item, (*path, index))
    elif isinstance(value, str) and "${repo}" in value:
        allowed = (len(path) >= 4 and path[0] in {"providers", "sessions", "evaluators"} and path[2] == "options"
                   and path[3] in REPO_ALLOWED_OPTIONS)
        if not allowed:
            raise OrchestrationError(
                f"${{repo}} is resolved only in process command, cwd and env options; it is not allowed at {_pointer(path)}.",
                "configuration_error", {"path": _pointer(path)})


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


ADAPTER_SECTIONS = ("state_engine", "store", "guardrail", "routing", "acceptance", "session_selection",
                    "delivery_scheduler", "retry_policy", "receipt_policy", "telemetry")


def repository_references(config):
    """Settings whose code comes from outside ``.xorchestrate/`` and so outside the approval.

    Commands that name ``${repo}`` run files from the worktree, and
    ``module:factory`` adapters outside the ``xorchestrate`` package are
    imported from wherever Python finds them, which can include the repository.
    """
    found = []

    def adapter(where, specification):
        if not isinstance(specification, dict):
            return
        target = specification.get("adapter")
        if isinstance(target, str) and ":" in target and not target.startswith("xorchestrate."):
            found.append({"where": where, "kind": "module", "adapter": target})
        options = specification.get("options")
        if isinstance(options, dict):
            for nested in ("corpus", "artifacts"):
                if isinstance(options.get(nested), dict):
                    adapter(f"{where}.options.{nested}", options[nested])

    for section in ADAPTER_SECTIONS:
        adapter(section, config.get(section))
    for item in launched_commands(config):
        if any("${repo}" in argument for argument in item["command"]):
            found.append({"where": f"{item['kind']}.{item['name']}.{item['option']}", "kind": "repository_command",
                          "command": item["command"]})
    for kind in ("providers", "sessions", "evaluators"):
        entries = config.get(kind)
        if isinstance(entries, dict):
            for name, specification in entries.items():
                if not (isinstance(specification, dict) and specification.get("enabled") is False):
                    adapter(f"{kind}.{name}", specification)
    return found


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
    check_repo_placeholders(config)
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
    target = Path(data_dir) / "launchers"
    target.mkdir(mode=0o700, parents=True, exist_ok=True)
    digests = {}
    wanted = set()
    for name, data in records.launchers(companion).items():
        temporary = target / f".{name}.{os.getpid()}.tmp"
        temporary.write_bytes(data)
        os.chmod(temporary, 0o755)  # noqa: S103 - launchers are executables
        os.replace(temporary, target / name)
        digests[name] = hashlib.sha256(data).hexdigest()
        wanted.add(name)
    for stale in target.iterdir():
        if stale.name not in wanted and stale.is_file() and not stale.name.startswith("."):
            stale.unlink()
    return digests
