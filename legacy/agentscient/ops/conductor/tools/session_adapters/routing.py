"""Same-harness routing, the select side: the route each member of one run
takes, and the refusal when two members would share what one instance owns.

Contract: ops/conductor/sessions-lever.md, slice 2, "A route" (one live
route per participant, `stdout` or `native`) and "Required of the wall"
(4): two members of one harness compose two profiles with nothing shared.
Plan §6, "Multiple sessions": adapter selection uses the harness; routing,
workspaces, logs, profiles, tokens and pending calls use the instance
identity. profiles.py composes the profiles (wall); sessions.py selects,
through `select` here, once per member after its profile is prepared and
before its launch. Nothing here reads the store or starts a process: the
caller passes the run's other live members and their bindings.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The instrument module sits in the tools directory above this one.
sys.path.append(str(HERE.parent))
from instrument import refuse  # noqa: E402

# The route each transport takes: a protocol member's bridge submits through
# its own channel; a tui member's courier prints into its own session.
ROUTES = {"protocol": "native", "tui": "stdout"}


def route_for(transport: str) -> str:
    """`native` for a protocol member, `stdout` for a tui member."""
    route = ROUTES.get(transport)
    if route is None:
        refuse("transport", f"a member transport of {', '.join(sorted(ROUTES))}",
               str(transport), "a harness whose adapter reads protocol or tui")
    return route


def instance(member: dict, binding: dict) -> dict:
    """What one member instance owns, by kind: its profile, its evidence
    directory and its native home (the binding's `home`, where the harness
    keeps its sessions and credential leaves) as absolute paths, and its
    capability as a digest, never the value. The authority environment is
    not read: its HOME and XDG roots are the operator's, the same for every
    member by design (measured 2026-09-23 on eleven interactive bindings
    under ~/.local/state/<LANE_NAME>/native-profiles)."""
    paths = {}
    for kind, value in (("profile", member.get("profile")),
                        ("evidence", member.get("evidence")),
                        ("native home", binding.get("home"))):
        if isinstance(value, str) and value:
            paths[kind] = Path(value).absolute()
    capability = binding.get("coordination_capability")
    digest = (hashlib.sha256(capability.encode("utf-8")).hexdigest()
              if isinstance(capability, str) and capability else None)
    return {"paths": paths, "capability": digest}


def overlap(one: Path, two: Path) -> bool:
    """The same directory, or one inside the other."""
    return one == two or one in two.parents or two in one.parents


def shared(mine: dict, theirs: dict) -> str | None:
    """The first thing two instances share, named; None when nothing. The
    same directory is named before one inside another."""
    if mine["capability"] and mine["capability"] == theirs["capability"]:
        return "the coordination capability"
    pairs = [(kind, path, other_kind, other)
             for kind, path in mine["paths"].items()
             for other_kind, other in theirs["paths"].items()]
    for kind, path, other_kind, other in pairs:
        if path == other:
            return f"{kind} {path}" if kind == other_kind else f"{kind} and {other_kind} {path}"
    for kind, path, other_kind, other in pairs:
        if overlap(path, other):
            return f"{kind} {path} within {other_kind} {other}" if other in path.parents \
                else f"{other_kind} {other} within {kind} {path}"
    return None


def label(member: dict) -> str:
    ident = member.get("id")
    return f"{member.get('name')} SM-{ident}" if ident is not None else str(member.get("name"))


def select(member: dict, binding: dict, transport: str, others=()) -> dict:
    """The route this member takes, keyed by its instance, never its harness.

    `others` is the run's other live members, each as `(member, binding)`;
    a member may be a dict or the store's sqlite3.Row.
    Two members of one run under one name refuse `team`; a member that
    shares a profile, an evidence directory, a native home or the
    coordination capability with another refuses `profile`, naming both
    members and what they share. The reading is `{"member", "name",
    "harness", "route"}`; the route row itself is the courier's to take.
    """
    route = route_for(transport)
    member = dict(member)
    mine = instance(member, binding)
    for other, other_binding in others:
        other = dict(other)
        if member.get("id") is not None and other.get("id") == member.get("id"):
            continue
        if other.get("name") == member.get("name"):
            refuse("team", f"one live member named {member.get('name')} in the run",
                   f"{label(member)} and {label(other)}",
                   "name each member of one harness apart in the team file")
        both = shared(mine, instance(other, other_binding))
        if both is not None:
            refuse("profile", f"a profile for {label(member)} that shares nothing with "
                   f"{label(other)}", f"both hold {both}",
                   "prepare each member's profile in its own directory; profiles.py composes "
                   "one profile per member")
    ident = member.get("id")
    return {"member": f"SM-{ident}" if ident is not None else None,
            "name": member.get("name"), "harness": member.get("harness"), "route": route}
