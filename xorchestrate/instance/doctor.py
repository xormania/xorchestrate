"""Readiness checks for this machine and, when one is found, the current project.

Each check reports ``ok``, ``warning`` or ``error`` with a short detail. The
report is ``ok`` only when no check is an error. Nothing is launched: harness
binaries are only resolved on ``PATH``.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from ..model import OrchestrationError
from .companion import exposure
from .home import Home
from .instance import approval_refusal, context_for, locate_project, require_companion, resolve_instance
from .render import launched_commands, render, repository_references

MINIMUM_PYTHON = (3, 12)


def _check(name, status, detail, **extra):
    return {"check": name, "status": status, "detail": detail, **extra}


def machine_checks():
    from ..adapters.telemetry_collector.domain import TelemetryError
    from ..adapters.telemetry_collector.sqlite import require_sqlite

    checks = []
    version = ".".join(map(str, sys.version_info[:3]))
    checks.append(_check("python", "ok" if sys.version_info[:2] >= MINIMUM_PYTHON else "error",
                         f"Python {version} at {sys.executable}"))
    import sqlite3
    try:
        require_sqlite()
        checks.append(_check("sqlite", "ok", f"SQLite {sqlite3.sqlite_version} has the WAL fix"))
    except TelemetryError:
        checks.append(_check("sqlite", "error", f"SQLite {sqlite3.sqlite_version} lacks the WAL fix; telemetry "
                             "is unavailable. Use a Python whose SQLite is 3.51.3 or later (uv python install)."))
    # uv is needed only when a project pins another release; git is needed for every project.
    for program, purpose, missing in (("uv", "builds release toolchains", "warning"),
                                      ("git", "identifies projects and worktrees", "error")):
        found = shutil.which(program)
        checks.append(_check(program, "ok" if found else missing,
                             f"{found} ({purpose})" if found else f"{program} is not on PATH; it {purpose}"))
    return checks


def _resolves(command):
    program = command[0] if command else ""
    if "${repo}" in program:
        return True
    return Path(program).is_file() if os.path.isabs(program) else shutil.which(program) is not None


def project_checks(path=None, environ=None):
    """Approval, overrides, harness binaries and ``pass_env`` of the project at ``path``."""
    checks = []
    try:
        home = Home.locate(environ)
        tracked = locate_project(path)
        instance = resolve_instance(home, tracked)
        companion = require_companion(home)
    except OrchestrationError as error:
        return [_check("project", "error", str(error), code=error.code)], None
    checks.append(_check("project", "ok", f"{tracked.name} instance {instance.instance_id}",
                         worktree=tracked.identity()))
    exposed = exposure(companion.root)
    inside = companion.root.resolve().is_relative_to(tracked.worktree.root)
    if exposed is not None or inside:
        checks.append(_check("companion", "error", f"The companion {companion.root} is inside a project repository"
                             f" ({exposed or tracked.worktree.root}); agents could write its approvals. Move it "
                             "outside every project and run `xorchestrate companion use`."))
    else:
        checks.append(_check("companion", "ok", f"{companion.root} is outside the project"))
    refusal = approval_refusal(companion, tracked)
    if refusal is not None:
        checks.append(_check("approval", "error", str(refusal), **refusal.details))
    else:
        checks.append(_check("approval", "ok", f"configuration {tracked.config_hash[:12]} is approved"))
    for program in tracked.requires:
        checks.append(_check("requires", "ok" if shutil.which(program) else "error",
                             f"{program} {'resolves' if shutil.which(program) else 'is not on PATH'}"))
    try:
        rendered = render(tracked, companion, context_for(instance, tracked), write=False)
    except OrchestrationError as error:
        checks.append(_check("configuration", "error", str(error), code=error.code))
        return checks, None
    checks.append(_check("configuration", "warning" if rendered.warnings else "ok",
                         "renders and validates", warnings=list(rendered.warnings)))
    checks.append(_check("overrides", "ok", f"{len(rendered.overrides)} setting(s) overridden by the companion",
                         overrides=rendered.overrides))
    for reference in repository_references(rendered.config):
        checks.append(_check("repository_code", "warning",
                             f"{reference['where']} runs code from outside the approved .xorchestrate/ "
                             "(the repository or the Python path); approval does not cover it.", **reference))
    for item in launched_commands(rendered.config):
        label = f"{item['kind']}.{item['name']}"
        checks.append(_check("harness", "ok" if _resolves(item["command"]) else "error",
                             f"{label} {item['option']} {item['command'][0]!r} "
                             + ("resolves" if _resolves(item["command"]) else "is not on PATH")))
        missing = [name for name in item["pass_env"] if name not in os.environ]
        if item["pass_env"]:
            checks.append(_check("pass_env", "warning" if missing else "ok",
                                 f"{label} passes {', '.join(item['pass_env'])}"
                                 + (f"; unset: {', '.join(missing)}" if missing else ""), missing=missing))
    return checks, rendered


def doctor(path=None, environ=None, *, project=True):
    checks = machine_checks()
    if project:
        checks += project_checks(path, environ)[0]
    return {"ok": all(item["status"] != "error" for item in checks), "checks": checks}
