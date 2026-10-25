"""Operator-owned role policy and vendor availability, resolved behind MCP."""

import json
import shutil
from pathlib import Path

from .adapters import names as adapter_names
from .adapters import registry as adapter_registry
from .harnesses import get as harness_adapter
from .role_packages import adapter, catalog, default_role, names_for_adapter, runtime_profile
from .store import canonical, digest, stamp

DEFAULTS = Path(__file__).with_name("runtime-defaults.json")


def co_role(scope=None):
    """Use the appointed interface's bound identity, or its initial catalog default."""
    role = scope["role"] if scope else default_role("co")
    if adapter(role) != "co":
        raise ValueError("The appointed CO's current binding must use the CO behavior.")
    return role


def vendor_defaults(policy):
    """Availability is local consent, never enabled by a repository model choice."""
    profiles = policy.get("profiles", {}) if isinstance(policy, dict) else {}
    rows = profiles.values() if isinstance(profiles, dict) else ()
    vendors = {row["vendor"] for row in rows
               if isinstance(row, dict) and isinstance(row.get("vendor"), str)}
    return {"version": 1, "vendors": {vendor: {"available": False, "reason": "Enable after login"}
                                     for vendor in sorted(vendors)}}


def default_policy():
    """Load maintained initial choices through the same validator as private policy."""
    policy = json.loads(DEFAULTS.read_text())
    validate(policy, vendor_defaults(policy))
    return policy


def defaults(directory):
    from .profiles import write_json

    policy = default_policy()
    directory = Path(directory)
    if any((directory / name).exists() for name in ("roles.json", "vendors.json")):
        raise ValueError("Runtime policy already exists; edit its roles.json and vendors.json to change configuration.")
    write_json(directory / "roles.json", policy)
    write_json(directory / "vendors.json", vendor_defaults(policy))


def load(directory):
    directory = Path(directory)
    policy = json.loads((directory / "roles.json").read_text())
    availability = json.loads((directory / "vendors.json").read_text())
    return validate(policy, availability)


def validate(policy, availability):
    """Validate maintained and operator policy without starting a harness."""
    if not isinstance(policy, dict) or set(policy) != {"version", "max_sessions", "profiles", "roles"}:
        raise ValueError("roles.json needs version, max_sessions, profiles and roles")
    if not isinstance(availability, dict) or set(availability) != {"version", "vendors"}:
        raise ValueError("vendors.json needs version and vendors")
    if policy["version"] != 1 or availability["version"] != 1:
        raise ValueError("Unsupported runtime config version")
    if type(policy["max_sessions"]) is not int or not 1 <= policy["max_sessions"] <= 100:
        raise ValueError("max_sessions must be between 1 and 100")
    profiles, roles, vendors = policy["profiles"], policy["roles"], availability["vendors"]
    if not isinstance(profiles, dict) or not profiles or not isinstance(vendors, dict):
        raise ValueError("profiles and vendors must be objects")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("roles.json needs a nonempty map of configured role preferences")
    unknown = set(roles) - set(catalog()["roles"])
    if unknown:
        raise ValueError("roles.json references unknown catalog roles: " + ", ".join(sorted(map(str, unknown))))
    for vendor, row in vendors.items():
        if not isinstance(row, dict) or set(row) - {"available", "reason", "reset_at"}:
            raise ValueError("Unknown vendor availability field")
        if type(row.get("available")) is not bool or any(
            key in row and not isinstance(row[key], str) for key in ("reason", "reset_at")
        ):
            raise ValueError("Vendor availability needs a boolean and optional reason/reset_at text")
    for name, row in profiles.items():
        if not isinstance(row, dict) or set(row) - {"harness", "vendor", "model", "reasoning", "binary", "provider"}:
            raise ValueError("Unknown runtime profile field")
        if row.get("harness") not in adapter_names("harness") or row.get("vendor") not in vendors:
            raise ValueError("Every profile needs a supported harness and a declared vendor")
        for key in ("model", "reasoning", "binary", "provider"):
            if row.get(key) is not None and (not isinstance(row[key], str) or not row[key] or "\0" in row[key]):
                raise ValueError(f"{name}.{key} must be a nonempty string or null")
        if row.get("provider") and not harness_adapter(row["harness"]).supports_provider:
            raise ValueError("provider is not supported by this harness adapter")
    for role, choices in roles.items():
        if (
            not isinstance(choices, list)
            or not choices
            or any(not isinstance(c, str) or c not in profiles for c in choices)
        ):
            raise ValueError(f"{role} needs an ordered nonempty list of known profiles")
        if len(set(choices)) != len(choices):
            raise ValueError("Duplicate runtime preference")
        for choice in choices:
            runtime_profile(role, profiles[choice])
    revision = digest([policy, availability, catalog(), adapter_registry()])
    return policy, availability, revision


def resolve(directory, role, *, installed=True, profile=None):
    policy, availability, revision = load(directory)
    if role not in policy["roles"]:
        raise ValueError(f"Configure {role} preferences in roles.json before assigning that role.")
    if profile is not None and profile not in policy["roles"][role]:
        raise ValueError("Select a profile from this role's configured preferences")
    skipped = []
    for name in [profile] if profile is not None else policy["roles"][role]:
        profile = policy["profiles"][name]
        vendor = availability["vendors"][profile["vendor"]]
        reason = None
        binary = profile.get("binary") or profile["harness"]
        if not vendor["available"]:
            reason = vendor.get("reason") or "Vendor disabled"
        elif installed and not shutil.which(binary):
            reason = "Harness executable is not installed"
        if reason:
            skipped.append({"profile": name, "vendor": profile["vendor"], "reason": reason})
            continue
        return {
            **runtime_profile(role, profile),
            "binary": binary,
            "selection": {
                "role": role,
                "profile": name,
                "revision": revision,
                "skipped": skipped,
            },
        }
    raise ValueError(f"No runtime available for {role}; inspect operator runtime_status and vendor availability.")



def _binding(scope):
    """Selection follows the assignment, not its transient coordination state."""
    if not isinstance(scope, dict) or not scope.get("member") or not scope.get("role"):
        raise ValueError("A current work binding is required for runtime selection")
    return {key: scope.get(key) for key in
            ("member", "role", "work_order", "job", "assignment_id")}


def _source(db, scope):
    """Fence actual scope changes while allowing pause, guidance and NCO transfer."""
    def record(table, identity, fields):
        if identity is None:
            return None
        row = db.execute("SELECT " + ",".join(fields) + " FROM " + table + " WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise ValueError("Runtime selection source is no longer available")
        return dict(row)

    return {
        "binding": _binding(scope),
        "work": record("work_orders", scope["work_order"], ("title", "objective", "done_when", "constraints", "context")),
        "job": record("jobs", scope.get("job"), ("title", "objective", "done_when", "constraints", "assignment", "dependencies")),
        "assignment": record("role_assignments", scope.get("assignment_id"), ("revision", "brief", "details")),
    }


def _validated(directory, role, choice):
    """An earlier decision never authorizes changed settings or an unavailable vendor."""
    selection = choice.get("selection") if isinstance(choice, dict) else None
    if not isinstance(selection, dict) or selection.get("role") != role or not isinstance(selection.get("profile"), str):
        raise ValueError("Runtime selection must name the configured profile for this role")
    current = resolve(directory, role, profile=selection["profile"])
    if selection.get("revision") != current["selection"]["revision"]:
        raise ValueError("Runtime policy changed; select a current profile before launching this assignment")
    fields = set(current) - {"selection"}
    if any(choice.get(key) != current[key] for key in fields):
        raise ValueError("Runtime profile changed; select its current settings before launching this assignment")
    return {**current, "selection": dict(selection)}


def retain(store, db, scope, choice):
    """Bind a trusted resolved choice atomically with its assignment."""
    from .control import binding

    identity = _binding(scope)
    current = binding(db, scope["member"])
    if not current or _binding(current) != identity:
        raise ValueError("Work binding changed before runtime selection could be retained")
    choice = _validated(store.directory, scope["role"], choice)
    db.execute(
        "INSERT INTO runtime_selections(member,binding,profile) VALUES (?,?,?) "
        "ON CONFLICT(member) DO UPDATE SET binding=excluded.binding,profile=excluded.profile",
        (scope["member"], canonical(_source(db, current)), canonical(choice)),
    )
    return choice


def selected(store, scope, *, required=False):
    """Reuse this assignment's decision; never replace it with the first preference."""
    from .control import binding

    identity = _binding(scope)
    with store.connect() as db:
        current = binding(db, scope["member"])
        if not current or _binding(current) != identity:
            raise ValueError("Work binding changed before its runtime selection could be loaded")
        row = db.execute("SELECT binding,profile FROM runtime_selections WHERE member=?", (scope["member"],)).fetchone()
        source = _source(db, current)
    if not row:
        details = json.loads(source["assignment"]["details"]) if source["assignment"] else {}
        coordination = {}
        if adapter(current["role"]) == "nco":
            try:
                coordination = json.loads(current.get("brief", ""))
            except (TypeError, ValueError):
                pass
        marked = ("selection_decision" in details or "decision" in details or
                  isinstance(coordination, dict) and "selection_decision" in coordination)
        if required or marked:
            raise ValueError("This assignment needs a retained runtime selection before launching")
        # Existing operator bindings predate per-assignment decisions. Their
        # explicit role policy remains usable until a decision is retained.
        return resolve(store.directory, scope["role"])
    if row["binding"] != canonical(source):
        raise ValueError("Work binding changed; select a current runtime profile for this assignment")
    return _validated(store.directory, scope["role"], json.loads(row["profile"]))


def allocate(store, db, config, role, existing=None, selected=None):
    """Called inside the work allocation transaction; failed batches leave no seats."""
    if selected is not None:
        selected = _validated(store.directory, role, selected)
    if existing:
        row = dict(db.execute("SELECT * FROM members WHERE name=?", (existing["member"],)).fetchone())
        return {**row, **selected} if selected is not None else row
    policy, _, _ = load(store.directory)
    interfaces = names_for_adapter("co")
    query = "SELECT count(*) FROM member_bindings"
    if interfaces:
        query += " WHERE role NOT IN (" + ",".join("?" for _ in interfaces) + ")"
    count = db.execute(query, interfaces).fetchone()[0]
    count += db.execute("SELECT count(*) FROM standby_bindings").fetchone()[0]
    if count >= policy["max_sessions"]:
        raise ValueError("No available collaborator capacity; stop/release finished work or ask the operator.")
    if adapter(role) != "arbitrator":
        from .configuration import arbitration_reservations

        reserved = max(arbitration_reservations(store).values(), default=0)
        arbitrators = names_for_adapter("arbitrator")
        occupied = db.execute("SELECT count(*) FROM member_bindings WHERE role IN (" +
                              ",".join("?" for _ in arbitrators) + ")", arbitrators).fetchone()[0] if arbitrators else 0
        if count - occupied >= policy["max_sessions"] - reserved:
            raise ValueError("No ordinary collaborator capacity remains; reserved sessions are required for Arbitrator decisions.")
    selected = selected if selected is not None else resolve(store.directory, role)
    workspace = config["members"][0]["workspace"]
    # Reuse an unbound, stopped dynamic seat. Roles are not fixed seat names.
    row = db.execute(
        "SELECT m.* FROM members m LEFT JOIN member_bindings b ON m.name=b.member "
        "WHERE m.name LIKE 'runtime-%' AND b.member IS NULL AND m.pid IS NULL AND m.state IN ('stopped','failed') "
        "AND NOT EXISTS (SELECT 1 FROM standby_bindings s WHERE s.member=m.name) "
        "ORDER BY m.name LIMIT 1"
    ).fetchone()
    if row:
        name = row["name"]
        db.execute("UPDATE members SET harness=?,updated=? WHERE name=?", (selected["harness"], stamp(), name))
    else:
        index = 1
        while db.execute("SELECT 1 FROM members WHERE name=?", (f"runtime-{index}",)).fetchone():
            index += 1
        name = f"runtime-{index}"
        db.execute(
            "INSERT INTO members(name,harness,workspace,updated) VALUES (?,?,?,?)",
            (name, selected["harness"], workspace, stamp()),
        )
    return {"name": name, "workspace": workspace, **selected}


def status(store):
    policy, vendors, revision = load(store.directory)
    choices = {}
    for role in catalog()["roles"]:
        try:
            choices[role] = resolve(store.directory, role)
        except ValueError as error:
            choices[role] = {"error": str(error)}
    return {
        "revision": revision,
        "policy": policy,
        "availability": vendors,
        "resolved": choices,
        "sessions": store.members(),
        "note": "Live sessions keep their launch snapshot. reset_at is advisory.",
    }
