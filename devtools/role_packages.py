"""Trusted role-package projections; CUE is needed only for maintainer generation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from . import adapters
from .failures import Failure
from .role_behaviors import RoleBehavior
from .store import canonical, digest

SOURCE = Path(__file__).with_name("role-packages.cue")
GENERATED = SOURCE.with_suffix(".json")
GROUPS = {"bound", "session", "command", "advice", "job", "job_framework", "work_order", "work_order_framework"}
HOOK_EVENTS = ("PreToolUse", "PostToolUse", "SessionStart", "Stop")


def behavior(name):
    value = adapters.factory("role", name)()
    if (not isinstance(value, RoleBehavior) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", value.id)
            or type(value.editor) is not bool or type(value.interface) is not bool
            or value.interface and value.editor):
        raise ValueError("Role adapters must return a valid RoleBehavior contract")
    return value


def behavior_ids():
    return tuple(sorted({behavior(name).id for name in adapters.names("role")}))


def _object(value, fields, where):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"Invalid {where} fields")
    return value


def _text(value, where):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{where} must be nonempty text")


def _strings(value, where, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{where} must be a {'nonempty ' if nonempty else ''}list")
    for item in value:
        _text(item, where)
    if len(set(value)) != len(value):
        raise ValueError(f"Duplicate {where} value")


def validate(value, source_sha256):
    """Inspect the raw export; do not infer missing observed configuration fields."""
    _object(value, {"source_sha256", "catalog"}, "role export")
    if value["source_sha256"] != source_sha256:
        raise ValueError("Role export is stale; regenerate devtools/role-packages.json from role-packages.cue")
    catalog = _object(value["catalog"], {"version", "framework_skills", "roles", "defaults"}, "role catalog")
    if type(catalog["version"]) is not int or catalog["version"] != 2:
        raise ValueError("Unsupported role catalog version")
    _strings(catalog["framework_skills"], "framework skills")
    if not isinstance(catalog["roles"], dict) or not catalog["roles"]:
        raise ValueError("Role catalog needs role definitions")
    for name, package in catalog["roles"].items():
        if not isinstance(name, str) or not adapters.NAME.fullmatch(name):
            raise ValueError("Invalid role name")
        _object(package, {"version", "behavior", "responsibility", "skills", "runtime", "hooks", "mcp", "workspace"}, name)
        if type(package["version"]) is not int or package["version"] != 2:
            raise ValueError("Unsupported role package version")
        _text(package["behavior"], name + ".behavior")
        semantics = behavior(package["behavior"])
        _text(package["responsibility"], name + ".responsibility")
        skills = _object(package["skills"], {"shared", "role", "framework"}, name + ".skills")
        _strings(skills["framework"], name + ".skills.framework")
        for skill in [skills["shared"], skills["role"], *skills["framework"]]:
            if not isinstance(skill, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", skill):
                raise ValueError("Invalid role skill reference")
            if not (SOURCE.parent.parent / ".agents" / "skills" / skill / "SKILL.md").is_file():
                raise ValueError("Role package references a missing installed skill")
        runtime = _object(package["runtime"], {"harnesses", "model", "reasoning", "profiles"}, name + ".runtime")
        _strings(runtime["harnesses"], name + ".runtime.harnesses", nonempty=True)
        if set(runtime["harnesses"]) - set(adapters.names("harness")) or runtime["profiles"] != "operator":
            raise ValueError("Role runtime needs supported harnesses and operator profiles")
        for key in ("model", "reasoning"):
            option = _object(runtime[key], {"default", "allowed"}, name + ".runtime." + key)
            if option["default"] is not None:
                _text(option["default"], key + " default")
            if option["allowed"] is not None:
                _strings(option["allowed"], key + " allowed", nonempty=True)
                if option["default"] is not None and option["default"] not in option["allowed"]:
                    raise ValueError(f"{name}.{key} default is outside its allowed options")
        hooks = _object(package["hooks"], {"events", "policy", "native_delegation"}, name + ".hooks")
        if hooks["events"] != list(HOOK_EVENTS) or hooks["policy"] != "mcp_only" or hooks["native_delegation"] is not False:
            raise ValueError("Role hooks must preserve MCP-only standalone sessions")
        mcp = _object(package["mcp"], GROUPS, name + ".mcp")
        for group, names in mcp.items():
            _strings(names, name + ".mcp." + group)
            if any(not re.fullmatch(r"[a-z][a-z_]{0,63}", tool) for tool in names):
                raise ValueError("Invalid role MCP tool reference")
        if semantics.interface and (mcp["command"] != ["direct", "monitor", "roll_call"] or
                             any(mcp[group] for group in GROUPS - {"bound", "session", "command"})):
            raise ValueError("CO package must remain an interface")
        workspace = _object(package["workspace"], {"editor", "paths", "serena"}, name + ".workspace")
        if type(workspace["editor"]) is not bool or type(workspace["serena"]) is not bool:
            raise ValueError("Role workspace permissions need booleans")
        if not semantics.editor and workspace["editor"]:
            raise ValueError("Command and read-only roles cannot become job editors")
        if semantics.interface and (workspace["serena"] or skills["framework"]):
            raise ValueError("CO package cannot enable execution providers or skills")
        _strings(workspace["paths"], name + ".workspace.paths", nonempty=workspace["editor"])
        if not workspace["editor"] and workspace["paths"]:
            raise ValueError("Read-only roles cannot have editable paths")
        for path in workspace["paths"]:
            if path.startswith(("/", "\\")) or "\\" in path or ":" in path or any(p in {"", ".", ".."} for p in path.rstrip("/").split("/")):
                raise ValueError("Role paths must stay relative to the project")
    defaults = catalog["defaults"]
    if not isinstance(defaults, dict):
        raise ValueError("Role defaults must map behavior IDs to configured roles")
    for selected, name in defaults.items():
        if (not isinstance(selected, str) or not isinstance(name, str) or name not in catalog["roles"]
                or behavior(catalog["roles"][name]["behavior"]).id != selected):
            raise ValueError("Role defaults must select a matching configured behavior")
    return catalog


@lru_cache(maxsize=4)
def _validated_catalog(source_sha256, export, implementations):
    # The implementation manifest is a cache key even though validate obtains
    # the factories through the shared registry.
    return validate(json.loads(export), source_sha256)


def _catalog():
    try:
        export = GENERATED.read_text()
        source_sha256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    except (OSError, ValueError) as error:
        raise ValueError("Read a valid generated devtools/role-packages.json before starting services") from error
    return _validated_catalog(source_sha256, export, canonical(adapters.registry()))


def catalog():
    return copy.deepcopy(_catalog())


def role(name):
    try:
        return copy.deepcopy(_catalog()["roles"][name])
    except (KeyError, TypeError) as error:
        raise ValueError("Unknown role package") from error


def adapter(name):
    return behavior(role(name)["behavior"]).id


def names_for_adapter(selected):
    return tuple(sorted(name for name, package in _catalog()["roles"].items()
                        if behavior(package["behavior"]).id == selected))


def assignable_roles(coordinator=None):
    """Configured identities eligible for ordinary coordinator assignments.

    Shared by tool schemas, onboarding and semantic selection. Work, path,
    capacity and lifecycle checks remain the assignment service's responsibility.
    """
    behavior = adapter(coordinator) if coordinator is not None else None
    if behavior not in {None, "xo", "nco"}:
        return ()
    excluded = {"co", "xo", "arbitrator"}
    if behavior == "nco":
        excluded |= {"planner", "nco"}
    return tuple(name for name in _catalog()["roles"] if adapter(name) not in excluded)


def default_role(selected):
    configured = _catalog()["defaults"].get(selected)
    if configured is not None:
        return configured
    matches = names_for_adapter(selected)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Configure a role for the {selected} behavior before requesting it")
    raise ValueError(f"Configure an explicit default role for the {selected} behavior")


def descriptor(name):
    package = role(name)
    implementation = adapters.implementation("role", package["behavior"])
    return {"role": name, "behavior": adapter(name), "version": package["version"],
            "revision": digest([name, package, implementation])}


def require_capability(name, capability):
    """Apply a package ceiling before a backing service performs an action.

    Bindings, provider prerequisites and action-specific restrictions remain
    the caller's responsibility; a declared tool never grants those by itself.
    """
    package = role(name)
    if not any(capability in tools for tools in package["mcp"].values()):
        raise Failure(f"{name} role package does not permit {capability}",
                      category="not_permitted", reason="capability_not_permitted")


def validate_tools(names):
    known = set(names)
    for name, package in _catalog()["roles"].items():
        declared = {tool for group in package["mcp"].values() for tool in group}
        if declared - known:
            raise ValueError(f"{name} package references unknown MCP tools: " + ", ".join(sorted(declared - known)))


def runtime_profile(name, profile):
    """An operator profile may narrow a package, never enlarge its execution options."""
    execution = role(name)["runtime"]
    if profile.get("harness") not in execution["harnesses"]:
        raise ValueError(f"{name} package does not permit this harness")
    result = dict(profile)
    for key in ("model", "reasoning"):
        option = execution[key]
        value = profile.get(key)
        if value is None:
            value = option["default"]
        if value is not None:
            _text(value, key)
        if option["allowed"] is not None and value not in option["allowed"]:
            raise ValueError(f"{name} package needs an explicit permitted {key}; inherited values cannot satisfy its allowlist")
        result[key] = value
    result["role_package"] = descriptor(name)
    return result


def generate(cue):
    # Validate the entire source before selecting the concrete export expression.
    subprocess.run([cue, "vet", "-c", str(SOURCE)], check=True, capture_output=True, text=True, timeout=30)
    exported = subprocess.run([cue, "export", str(SOURCE), "-e", "packages"], check=True,
                              capture_output=True, text=True, timeout=30)
    value = {"source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "catalog": json.loads(exported.stdout)}
    validate(value, value["source_sha256"])
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--cue", default="cue", help="Maintainer CUE executable (never an MCP argument)")
    args = parser.parse_args()
    try:
        value = generate(args.cue)
        if args.write:
            GENERATED.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
            print("Regenerated devtools/role-packages.json")
        elif json.loads(GENERATED.read_text()) != value:
            raise ValueError("Role export differs from CUE source; regenerate devtools/role-packages.json")
        else:
            print("Role CUE source and generated export agree")
    except subprocess.CalledProcessError as error:
        parser.exit(1, error.stderr or str(error))
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
