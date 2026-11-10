"""Operator workflows behind ``setup``, ``companion``, ``init`` and ``approve``.

Each returns a JSON-ready report; the CLI prints it.
"""
from __future__ import annotations

from pathlib import Path

from ..composition import RELEASE
from ..model import OrchestrationError
from .companion import exposure, init_companion, is_companion
from .doctor import doctor
from .home import Home, read_document, write_document
from .instance import locate_project, prepare, register, relocate, require_companion
from .project import default_name, locate_worktree, read_tracked, write_starter
from .render import launched_commands, merged, repository_references

MCP_FILE = ".mcp.json"
MCP_SERVER = "xorchestrate"
MCP_ENTRY = {"command": "xorchestrate", "args": ["connect"]}


def _refuse_exposed(path):
    reason = exposure(path)
    if reason is not None:
        raise OrchestrationError(f"The companion must not be inside a project repository: {reason}. Approvals "
                                 "there could be written by the agents the approvals guard against.",
                                 "companion_exposed", {"path": str(path)})


def companion_use(path, environ=None):
    path = Path(path).expanduser().resolve()
    _refuse_exposed(path)
    if not is_companion(path):
        raise OrchestrationError("Not a companion (it needs overlays/ and approvals/); use `companion init`.",
                                 "companion_required", {"path": str(path)})
    home = Home.locate(environ)
    home.update_settings(companion=str(path))
    return {"companion": str(path), "settings": str(home.settings_path)}


def companion_init(path, environ=None):
    _refuse_exposed(Path(path).expanduser().resolve())
    path, created = init_companion(path)
    return {**companion_use(path, environ), "created": created}


def setup(path=None, environ=None):
    """Create or link the companion, then run the machine checks of ``doctor``."""
    home = Home.locate(environ)
    if path is not None:
        target = Path(path).expanduser().resolve()
        _refuse_exposed(target)
        if is_companion(target):
            report = {**companion_use(target, environ), "action": "linked"}
        elif not target.exists() or (target.is_dir() and not any(target.iterdir())):
            report = {**companion_init(target, environ), "action": "created"}
        else:
            raise OrchestrationError("The path exists but is not a companion; choose an empty or new directory, "
                                     "or an existing companion clone.", "invalid_arguments", {"path": str(target)})
    elif home.companion_path() is not None:
        report = {"companion": str(home.companion_path()), "action": "unchanged"}
    else:
        raise OrchestrationError("No companion is registered; run `xorchestrate setup PATH` with a new directory "
                                 "or an existing companion clone.", "companion_required")
    return {**report, "doctor": doctor(environ=environ, project=False)}


def summary(tracked, companion):
    """What approving would allow: the tracked files, their hash and the commands that would be launched."""
    config, overrides = merged(tracked, companion)
    return {"project": tracked.name, "release": tracked.release, "config_hash": tracked.config_hash,
            "files": tracked.digests, "commands": launched_commands(config),
            # Approval covers .xorchestrate/ only; these run code from the repository itself.
            "repository_code": repository_references(config), "overrides": overrides}


def _mcp_entry(worktree, may_write):
    path = worktree.root / MCP_FILE
    snippet = {"mcpServers": {MCP_SERVER: MCP_ENTRY}}
    try:
        document = read_document(path)
    except OrchestrationError:
        return {"path": str(path), "state": "invalid", "snippet": snippet,
                "note": "The existing .mcp.json is not a valid JSON object; it was left unchanged. Add the entry "
                        "by hand."}
    if document is None and may_write:
        write_document(path, snippet, 0o644)
        state = "written"
    elif document is None:
        state = "absent"
    elif isinstance(document.get("mcpServers"), dict) and MCP_SERVER in document["mcpServers"]:
        state = "present"
    else:
        state = "unchanged"
    return {"path": str(path), "state": state, "snippet": snippet,
            "note": "Harnesses that keep MCP servers in user-level configuration need this entry added there."}


def init(path=None, *, name=None, approve=False, relocate_instance=False, environ=None):
    """Set up the project at ``path`` on this machine (see ``docs/projects.md``)."""
    worktree = locate_worktree(path)
    if worktree is None:
        raise OrchestrationError("`xorchestrate init` needs a git repository.", "project_required",
                                 {"path": str(Path(path or Path.cwd()).resolve())})
    home = Home.locate(environ)
    companion = require_companion(home)
    created = []
    tracked = read_tracked(worktree)
    starter = tracked is None
    if starter:
        created += write_starter(worktree, name or default_name(worktree), RELEASE)
        tracked = read_tracked(worktree)
    elif name is not None and name != tracked.name:
        raise OrchestrationError("This project is already named in its tracked project.json.", "invalid_arguments",
                                 {"name": tracked.name})
    if companion.ensure_overlay(tracked.name):
        created.append(str(companion.overlay_path(tracked.name)))
    if relocate_instance:
        instance, relocated = relocate(home, tracked)
        registered = False
    else:
        instance, registered = register(home, tracked)
        relocated = False
    # Tracked files of a project that already had .xorchestrate/ are never edited.
    mcp = _mcp_entry(worktree, may_write=starter)
    if mcp["state"] == "written":
        created.append(mcp["path"])
    report = {"project": tracked.name, "instance_id": instance.instance_id, "registered": registered,
              "relocated": relocated,
              "worktree": tracked.identity(), "created": created, "mcp": mcp}
    if starter:
        report["next"] = "Review and commit the starter .xorchestrate/ and .mcp.json, then run `xorchestrate approve`."
    approved = companion.is_approved(tracked.name, tracked.config_hash)
    first = not companion.approved_hashes(tracked.name)
    if not approved and approve and not first:
        companion.approve(tracked.name, tracked.config_hash, tracked.digests, tracked.identity())
        approved = True
    if approved:
        prepared = prepare(worktree.root, environ=environ)
        report["approval"] = {"state": "approved", "config_hash": tracked.config_hash}
        report["rendered"] = str(prepared.rendered.path)
    else:
        report["approval"] = {"state": "required", "config_hash": tracked.config_hash,
                              "reason": "The first approval of a project is a separate `xorchestrate approve`."
                              if first else "Run `xorchestrate approve`, or `init --approve`.",
                              "summary": summary(tracked, companion)}
    report["doctor"] = doctor(worktree.root, environ)
    return report


def approve(path=None, environ=None, *, announce=None):
    """Approve the tracked configuration of the project at ``path`` (several hashes may be approved).

    ``announce(summary)`` is called with what is being approved before the
    approval is recorded; without it the summary is part of the report.
    """
    tracked = locate_project(path)
    companion = require_companion(Home.locate(environ))
    described = summary(tracked, companion)
    if announce is not None:
        announce(described)
    new = companion.approve(tracked.name, tracked.config_hash, tracked.digests, tracked.identity())
    report = {"project": tracked.name, "config_hash": tracked.config_hash, "newly_approved": new,
              "approvals": str(companion.approvals_path(tracked.name))}
    return report if announce is not None else {**report, "summary": described}
