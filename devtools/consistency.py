"""Read-only closure checks for installed dev-lane adapters, roles, tools and policy.

This checks the running framework's references and interfaces without contacting a
provider or loading a credential. Maintainer CUE export checks remain separate.
"""

from __future__ import annotations

import argparse
import json

from . import adapters, configuration, role_packages
from .store import State


def _tools():
    from .api import CAPABILITIES, OPERATOR_TOOLS, TOOLS

    namespaces = {"managed": TOOLS, "operator": OPERATOR_TOOLS, "capabilities": CAPABILITIES}
    for namespace, rows in namespaces.items():
        names = [row["name"] for row in rows]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate MCP tool names in the " + namespace + " namespace")
    role_packages.validate_tools(row["name"] for row in TOOLS)
    from .api import validate
    from .recovery_actions import validate_templates

    validate_templates(configuration.defaults()["guidance"]["recovery_actions"], TOOLS, validate)
    return {name: len(rows) for name, rows in namespaces.items()}


def _skills(catalog):
    from .onboarding import document

    names = set(catalog["framework_skills"])
    for package in catalog["roles"].values():
        names.update([package["skills"]["shared"], package["skills"]["role"], *package["skills"]["framework"]])
    for name in sorted(names):
        document(name)  # The same source, containment and metadata validation used by onboarding.
    return {"installed_skills": len(names)}


def _implementations(registry):
    from .harnesses import get as harness
    from .jev import Jev

    settings = configuration.defaults()
    for kind, entries in registry.items():
        for name in entries:
            adapters.factory(kind, name)  # Every declared implementation must be installed and callable.
            if kind == "role":
                role_packages.behavior(name)
            elif kind == "harness":
                harness(name)
            elif kind == "evaluation":
                Jev(settings["transport"], {**settings["connection"], "adapter": name})
    return {kind: len(entries) for kind, entries in registry.items()}


def _team(directory):
    from .launch import team_config
    from .runtime import load as runtime

    state = State(directory)
    team = team_config(state.directory)
    variants = [configuration.resolve(state, **selection) for selection in configuration.selections(state)]
    profiles = runtime(state.directory)[0]["profiles"] if team.get("runtime") else None
    return {"configuration_variants": len(variants),
            "configuration_revisions": sorted({value["revision"] for value in variants}),
            "runtime_profiles": len(profiles) if profiles is not None else None,
            "fixed_members": len(team["members"]) if profiles is None else None}


def _runtime_defaults():
    from .runtime import default_policy

    policy = default_policy()
    return {"roles": len(policy["roles"]), "profiles": len(policy["profiles"])}


def check(directory=None):
    """Return an inspectable result; fail closed on any dangling or invalid reference."""
    checks = []

    def inspect(name, action):
        try:
            detail = action()
        except Exception as error:
            checks.append({"name": name, "status": "failed", "error_type": type(error).__name__,
                           "detail": str(error)[:1000]})
            return None
        checks.append({"name": name, "status": "passed", "detail": detail})
        return detail

    registry = inspect("adapter_manifest", adapters.registry)
    if registry is not None:
        checks[-1]["detail"] = {kind: sorted(entries) for kind, entries in registry.items()}
        inspect("adapter_interfaces", lambda: _implementations(registry))
    catalog = inspect("role_catalog", role_packages.catalog)
    if catalog is not None:
        checks[-1]["detail"] = {"roles": len(catalog["roles"]), "defaults": len(catalog["defaults"])}
        inspect("skills", lambda: _skills(catalog))
    inspect("runtime_defaults", _runtime_defaults)
    policy = inspect("configuration_defaults", lambda: configuration.validate(configuration.defaults()))
    if policy is not None:
        checks[-1]["detail"] = {"version": policy["version"], "features": policy["features"]}
    inspect("mcp_tools", _tools)
    if directory is not None:
        inspect("team_configuration", lambda: _team(directory))
    return {"status": "failed" if any(row["status"] == "failed" for row in checks) else "passed",
            "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", help="Also inspect every private team configuration variant without changing its state")
    args = parser.parse_args()
    result = check(args.state)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "passed":
        parser.exit(1)


if __name__ == "__main__":
    main()
