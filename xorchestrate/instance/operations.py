"""Operator workflows behind ``setup``, ``companion``, ``init`` and ``approve``.

Each returns a JSON-ready report; the CLI prints it.
"""
from __future__ import annotations

from pathlib import Path

from ..composition import RELEASE
from ..model import OrchestrationError
from . import companion as records
from .doctor import doctor
from .home import Home, read_document, write_document
from .instance import load_instance, locate_project, prepare, register, relocate, resolve_instance
from .project import default_name, locate_worktree, read_tracked, write_starter
from .render import launched_commands, merged, repository_references
from .services import Services, companion_path_selection
from .services import refuse_unsafe as _checked

MCP_FILE = ".mcp.json"
MCP_SERVER = "xorchestrate"
MCP_ENTRY = {"command": "xorchestrate", "args": ["connect"]}


def _select(services, selection, project):
    """Store ``selection`` as the machine default, or as the companion of the project at ``project``."""
    if project is None:
        services.home.update_settings(companion=selection)
        return {"scope": "machine", "settings": str(services.home.settings_path)}
    tracked = locate_project(project, services.version_control)
    instance = resolve_instance(services.home, tracked, services.version_control)
    instance.update(companion=selection)
    return {"scope": "project", "project": tracked.name, "instance": str(instance.record_path)}


def companion_use(path, environ=None, *, project=None):
    """Use an existing companion: the machine default, or one project's (``project`` is its repository)."""
    services = Services(Home.locate(environ))
    selection = companion_path_selection(path)
    companion = _checked(services.companion_for(selection))
    if not companion.exists():
        raise OrchestrationError("Not a companion (it needs overlays/ and approvals/); use `companion init`.",
                                 "companion_required", {"location": companion.location})
    return {"companion": selection, **_select(services, selection, project)}


def companion_init(path, environ=None, *, project=None):
    services = Services(Home.locate(environ))
    created = _checked(services.companion_for(companion_path_selection(path))).initialize()
    return {**companion_use(path, environ, project=project), "created": created}


def setup(path=None, environ=None):
    """Create or link the machine-default companion, then run the machine checks of ``doctor``."""
    services = Services(Home.locate(environ))
    if path is not None:
        target = Path(path).expanduser().resolve()
        companion = _checked(services.companion_for(companion_path_selection(target)))
        if companion.exists():
            report = {**companion_use(target, environ), "action": "linked"}
        elif not target.exists() or (target.is_dir() and not any(target.iterdir())):
            report = {**companion_init(target, environ), "action": "created"}
        else:
            raise OrchestrationError("The path exists but is not a companion; choose an empty or new directory, "
                                     "or an existing companion clone.", "invalid_arguments", {"path": str(target)})
    elif (selection := services.machine_companion()) is not None:
        report = {"companion": selection, "action": "unchanged"}
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
    home = Home.locate(environ)
    services = Services(home)
    worktree = locate_worktree(path, services.version_control)
    if worktree is None:
        raise OrchestrationError("`xorchestrate init` needs a git repository.", "project_required",
                                 {"path": str(Path(path or Path.cwd()).resolve())})
    created = []
    tracked = read_tracked(worktree)
    # A companion must be selected (the project's own, or the machine default) before anything is written.
    services.companion(load_instance(home, tracked.name) if tracked is not None else None)
    starter = tracked is None
    if starter:
        created += write_starter(worktree, name or default_name(worktree), RELEASE)
        tracked = read_tracked(worktree)
    elif name is not None and name != tracked.name:
        raise OrchestrationError("This project is already named in its tracked project.json.", "invalid_arguments",
                                 {"name": tracked.name})
    if relocate_instance:
        instance, relocated = relocate(home, tracked)
        registered = False
    else:
        instance, registered = register(home, tracked, services.version_control)
        relocated = False
    companion = services.companion(instance)
    if records.ensure_overlay(companion, tracked.name):
        created.append(f"{companion.location}: overlay {tracked.name}")
    # Tracked files of a project that already had .xorchestrate/ are never edited.
    mcp = _mcp_entry(worktree, may_write=starter)
    if mcp["state"] == "written":
        created.append(mcp["path"])
    report = {"project": tracked.name, "instance_id": instance.instance_id, "registered": registered,
              "relocated": relocated,
              "worktree": tracked.identity(), "created": created, "mcp": mcp}
    if starter:
        report["next"] = "Review and commit the starter .xorchestrate/ and .mcp.json, then run `xorchestrate approve`."
    approved = records.is_approved(companion, tracked.name, tracked.config_hash)
    first = not records.approved_hashes(companion, tracked.name)
    if not approved and approve and not first:
        records.approve(companion, tracked.name, tracked.config_hash, tracked.digests, tracked.identity())
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


def approve(path=None, environ=None):
    """Approve the tracked configuration of the project at ``path`` (several hashes may be approved).

    The report includes the summary of what was approved.
    """
    home = Home.locate(environ)
    services = Services(home)
    tracked = locate_project(path, services.version_control)
    companion = services.companion(load_instance(home, tracked.name))
    described = summary(tracked, companion)
    new = records.approve(companion, tracked.name, tracked.config_hash, tracked.digests, tracked.identity())
    return {"project": tracked.name, "config_hash": tracked.config_hash, "newly_approved": new,
            "companion": companion.location, "summary": described}
