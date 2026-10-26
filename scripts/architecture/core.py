"""Pure architecture evaluation; extraction and enforcement are separate adapters.

Relations describe source dependencies, not runtime calls through injected ports.
Policy and repository names live in profiles, not in this evaluator.
"""
from __future__ import annotations

import hashlib
import json
from collections import deque
from fnmatch import fnmatchcase


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate_profile(profile):
    """Reject incomplete or misspelled policy instead of weakening enforcement."""
    if not isinstance(profile, dict):
        raise ValueError("Architecture profile must be an object.")
    fields = {"schema_version", "id", "version", "source_roots", "roles", "description"}
    if profile.keys() - fields or profile.get("schema_version") != 1:
        raise ValueError("Unsupported profile fields or schema_version (expected 1).")
    if not isinstance(profile.get("id"), str) or not profile["id"].strip():
        raise ValueError("Profile id must be nonempty text.")
    version = profile.get("version")
    if not ((isinstance(version, str) and version.strip()) or (type(version) is int and version > 0)):
        raise ValueError("Profile version must be nonempty text or a positive integer.")
    roots = profile.get("source_roots")
    if not isinstance(roots, list) or not roots or any(
        not isinstance(root, str) or not root or root.startswith("/")
        or "\\" in root or any(part in {"", ".", ".."} for part in root.split("/"))
        or any(not part.isidentifier() for part in root.split("/")) for root in roots
    ) or len(set(roots)) != len(roots):
        raise ValueError("source_roots must contain distinct relative Python package directories.")
    roles = profile.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("Profile must declare at least one role.")
    for name, role in roles.items():
        if not isinstance(name, str) or not name or not isinstance(role, dict):
            raise ValueError("Roles must map nonempty names to objects.")
        if role.keys() - {"modules", "allowed_roles", "allowed_external", "allow_dynamic", "forbidden_calls", "forbidden_literals", "description"}:
            raise ValueError(f"Unknown fields in role {name}.")
        for field in ("modules", "allowed_roles", "allowed_external"):
            values = role.get(field)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
                raise ValueError(f"{name}.{field} must be a list of nonempty strings.")
            if len(values) != len(set(values)):
                raise ValueError(f"{name}.{field} contains duplicate entries.")
        if not role["modules"]:
            raise ValueError(f"Role {name} needs at least one module selector.")
        if set(role["allowed_roles"]) - roles.keys():
            raise ValueError(f"Role {name} references an undeclared allowed role.")
        if type(role.get("allow_dynamic")) is not bool:
            raise ValueError(f"{name}.allow_dynamic must be explicitly true or false.")
        calls = role.get("forbidden_calls", [])
        if not isinstance(calls, list) or any(not isinstance(call, str) or not call for call in calls):
            raise ValueError(f"{name}.forbidden_calls must be a list of nonempty strings.")
        literals = role.get("forbidden_literals", [])
        if not isinstance(literals, list) or any(not isinstance(value, str) for value in literals):
            raise ValueError(f"{name}.forbidden_literals must be a list of strings.")


def _allowed_external(module, patterns, stdlib_modules):
    top = module.split(".")[0]
    return any((pattern == "stdlib:*" and top in stdlib_modules)
               or (pattern != "stdlib:*" and (fnmatchcase(module, pattern)
                   or module.startswith(pattern + "."))) for pattern in patterns)


def evaluate(profile, modules, *, stdlib_modules=(), extraction_findings=()):
    """Evaluate extracted modules; return a reproducible, JSON-compatible report.

    Each module record contains path, sha256, imports (target/line/kind), named
    operations (name/line), and string literals (value/line). Inputs are source
    observations, never imported code; docstrings are not behavioral literals.
    """
    findings = list(extraction_findings)
    try:
        validate_profile(profile)
    except (ValueError, TypeError) as error:
        return {"schema_version": 1, "profile_id": profile.get("id") if isinstance(profile, dict) else None,
                "status": "error", "summary": {"modules": 0, "findings": 1},
                "findings": [{"rule_id": "profile.valid", "kind": "configuration", "message": str(error)}],
                "coverage": {}, "subject": {}, "files": [], "relationships": []}
    roles = profile["roles"]
    assigned = {}
    counts = dict.fromkeys(roles, 0)

    def finding(kind, rule_id, message, module=None, line=None, chain=None, **extra):
        item = {"rule_id": rule_id, "kind": kind, "message": message, **extra}
        if module is not None:
            item.update(module=module, path=modules[module]["path"])
        if line is not None:
            item["line"] = line
        if chain is not None:
            item["chain"] = list(chain)
        findings.append(item)

    for role_name, role in roles.items():
        for selector in role["modules"]:
            matches = [name for name in modules if fnmatchcase(name, selector)]
            if not matches:
                finding("missing_selector", "coverage.selectors",
                        f"Required selector {selector!r} for role {role_name!r} matched no modules.",
                        role=role_name, selector=selector)
    for name in sorted(modules):
        matches = [role for role, value in roles.items()
                   if any(fnmatchcase(name, selector) for selector in value["modules"])]
        if len(matches) == 1:
            assigned[name] = matches[0]
            counts[matches[0]] += 1
        elif not matches:
            finding("unclassified", "coverage.classification", "Module has no declared architectural role.", name)
        else:
            finding("ambiguous", "coverage.classification", "Module matches multiple architectural roles.",
                    name, roles=matches)

    local_roots = {root.split("/")[0] for root in profile["source_roots"]}
    namespace_modules = {".".join(name.split(".")[:i]) for name in modules
                         for i in range(1, len(name.split(".")))} - modules.keys()
    dynamic_count = sum(len(record.get("dynamic", ())) for record in modules.values())
    external = set()
    emitted = set()
    # Follow every allowed local edge from every classified module. The initial
    # role remains the authority for the whole path: a permissive helper cannot
    # launder infrastructure into a more constrained role.
    for origin in sorted(assigned):
        origin_role = assigned[origin]
        rule = roles[origin_role]
        queue, visited = deque([(origin, (origin,))]), set()
        while queue:
            name, chain = queue.popleft()
            if name in visited:
                continue
            visited.add(name)
            record = modules[name]
            for literal in record.get("literals", ()):
                if literal["value"] in rule.get("forbidden_literals", ()):
                    key = ("literal", origin_role, name, literal["value"], literal["line"])
                    if key not in emitted:
                        emitted.add(key)
                        finding("literal", "literals.allowed", "Literal is prohibited in this architectural role.",
                                name, literal["line"], chain, role=origin_role, value=literal["value"])
            for operation in record.get("calls", ()):
                if operation["name"] in rule.get("forbidden_calls", ()):
                    key = ("operation", origin_role, name, operation["name"], operation["line"])
                    if key not in emitted:
                        emitted.add(key)
                        finding("operation", "operations.allowed", "Call is prohibited in this architectural role.",
                                name, operation["line"], chain, role=origin_role, operation=operation["name"])
            if not rule["allow_dynamic"]:
                for operation in record.get("dynamic", ()):
                    key = ("dynamic", origin_role, name, operation["name"], operation["line"])
                    if key not in emitted:
                        emitted.add(key)
                        finding("dynamic", "dependencies.dynamic", "Dynamic loading/evaluation in a constrained dependency path.",
                                name, operation["line"], chain, role=origin_role, operation=operation["name"])
            for edge in record.get("imports", ()):
                target, line = edge["target"], edge["line"]
                path = (*chain, target)
                kind = None
                if target in modules:
                    target_role = assigned.get(target)
                    if target_role is None:
                        continue  # Classification already reports this input.
                    if target_role not in rule["allowed_roles"]:
                        kind, rule_id = "dependency", "dependencies.allowed"
                        message = f"Role {origin_role!r} cannot depend on role {target_role!r}."
                    else:
                        queue.append((target, path))
                elif target in namespace_modules:
                    continue  # Namespace package has no initializer to execute.
                elif target.split(".")[0] in local_roots:
                    kind, rule_id = "unresolved", "coverage.dependencies"
                    message = "Local dependency is not present in the examined source."
                else:
                    external.add(target)
                    if not _allowed_external(target, rule["allowed_external"], stdlib_modules):
                        kind, rule_id = "external", "dependencies.external"
                        message = f"External dependency {target!r} is not allowed for role {origin_role!r}."
                if kind:
                    key = (kind, origin_role, name, target, line)
                    if key not in emitted:
                        emitted.add(key)
                        finding(kind, rule_id, message, name, line, path, role=origin_role, target=target)

    files = [{"module": name, "path": record["path"], "sha256": record["sha256"],
              "role": assigned.get(name)} for name, record in sorted(modules.items())]
    relationships = [{"source": name, **edge} for name, record in sorted(modules.items())
                     for edge in record.get("imports", ())]
    status = "error" if any(f["kind"] in {"configuration", "extraction"} for f in findings) else "violation" if findings else "pass"
    return {"schema_version": 1, "profile_id": profile["id"], "profile_version": profile["version"],
            "profile": profile, "status": status,
            "summary": {"modules": len(modules), "findings": len(findings)},
            "findings": findings, "files": files, "relationships": relationships,
            "subject": {"source_sha256": digest([{k: f[k] for k in ("path", "sha256")} for f in files]),
                        "contract_sha256": digest(profile)},
            "coverage": {"modules": len(modules), "classified": len(assigned), "roles": counts,
                         "unclassified": sum(f["kind"] == "unclassified" for f in findings),
                         "ambiguous": sum(f["kind"] == "ambiguous" for f in findings),
                         "dynamic_operations": dynamic_count, "external_dependencies": sorted(external),
                         "limitations": ["Static Python imports, named operations and declared string literals only; not a hostile-code sandbox.",
                                         "External package internals and runtime-selected implementations are not traversed.",
                                         "Behavioral port contracts and composition require executable tests."]}}
