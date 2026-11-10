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
from .home import Home
from .instance import approval_refusal, context_for, locate_project, resolve_instance
from .render import launched_commands, render, repository_references
from .services import Services

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


def companion_checks(services, companion):
    """The companion adapter's own location check, and whether its remote is private."""
    checks = []
    reason = companion.validate_location()
    if reason is not None:
        checks.append(_check("companion", "error", f"The companion {companion.location} is unsafe: {reason}; agents "
                             "could write its approvals. Move it outside every project and run "
                             "`xorchestrate companion use`.", location=companion.location))
    else:
        checks.append(_check("companion", "ok", f"{companion.location} is outside every project",
                             location=companion.location))
    remote = companion.remote()
    if remote is None:
        checks.append(_check("companion_remote", "ok", "the companion has no remote", remote=None))
        return checks
    report = services.repository_hosting.visibility(remote)
    details = {"remote": remote, "repository": report.repository, "visibility": report.visibility}
    if report.visibility == "private":
        checks.append(_check("companion_remote", "ok", f"{remote} is private", **details))
    elif report.visibility == "public":
        checks.append(_check("companion_remote", "warning", f"{remote} is PUBLIC: your approvals, overlays and "
                             "captured data are visible to anyone. Make the repository private.", **details))
    else:
        checks.append(_check("companion_remote", "warning", f"Could not tell whether {remote} is private"
                             f" ({report.reason}); make sure it is.", reason=report.reason, **details))
    return checks


def project_checks(path=None, environ=None):
    """Approval, overrides, harness binaries and ``pass_env`` of the project at ``path``."""
    checks = []
    try:
        services = Services(Home.locate(environ))
        tracked = locate_project(path, services.version_control)
        instance = resolve_instance(services.home, tracked, services.version_control)
        companion = services.companion(instance)
    except OrchestrationError as error:
        return [_check("project", "error", str(error), code=error.code)], None
    checks.append(_check("project", "ok", f"{tracked.name} instance {instance.instance_id}",
                         worktree=tracked.identity()))
    checks += companion_checks(services, companion)
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
