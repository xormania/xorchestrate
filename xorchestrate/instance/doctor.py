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
from .instance import approval_refusal, context_for, locate_project, resolve_instance, upgrade_refusal
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


def project_terminal_check(services, placement, tracked=()):
    """What the project's terminal host holds, from ``inventory()`` at ``placement``; nothing is started or stopped."""
    try:
        active = services.terminal_host(placement)
    except OrchestrationError as error:
        return _check("project_terminals", "warning", f"The project's terminal host cannot be inspected: {error}",
                      code=error.code)
    try:
        items = active.host.inventory()
    except OrchestrationError as error:
        return _check("project_terminals", "warning", f"The project's terminal host cannot be inspected: {error}",
                      code=error.code)
    finally:
        active.host.close()
    running = sorted(item.name for item in items if item.running)
    exited = sorted(item.name for item in items if not item.running)
    untracked = sorted(item.name for item in items if item.name not in set(tracked))
    details = {"adapter": active.adapter, "running": running, "exited": exited, "untracked": untracked}
    if not items:
        return _check("project_terminals", "ok", f"the project's {active.adapter} terminal host holds no terminals",
                      **details)
    advice = []
    if exited:
        advice.append(f"{len(exited)} exited ({', '.join(exited)}): `xorchestrate down` or `status` sweeps them "
                      "while no server runs")
    if running:
        advice.append(f"{len(running)} running ({', '.join(running)}): `xorchestrate down` stops a running server; "
                      "other running terminals stay until they are stopped where they run")
    if untracked:
        advice.append(f"untracked by any record: {', '.join(untracked)}")
    return _check("project_terminals", "warning" if exited or untracked else "ok",
                  f"the project's {active.adapter} terminal host holds " + "; ".join(advice), **details)


def recovery_check(rendered, data_dir, state, *, approved):
    """Sessions a hard restart interrupted, read from the project's store while no server runs; nothing is changed.

    The store is read only for an ``approved`` configuration that selects a
    built-in store adapter: doctor never imports or constructs an adapter that
    unapproved configuration, or a ``module:factory``, chose.
    """
    if state.get("running"):
        return _check("recovery", "ok", "the server is running; `xorchestrate status` shows its recovery")
    from ..composition import BUILTINS, factory_for, validate_config

    if not approved:
        return _check("recovery", "warning", "not inspected: the configuration is not approved, so its store is not "
                      "opened; approve it, then run doctor again", inspected=False)
    try:
        selection = validate_config(rendered.config, data_dir).adapters["store"]
        if selection.adapter not in BUILTINS["store"]:
            return _check("recovery", "ok", f"not inspected: the store adapter {selection.adapter!r} is not built in, "
                          "so doctor does not construct it; a running server's `xorchestrate status` shows its "
                          "recovery", inspected=False)
        path = selection.options.get("path")
        if path is not None and not Path(path).exists():
            sessions, report, operations = [], None, []
        else:
            store = factory_for("store", selection.adapter)(dict(selection.options))
            try:
                with store.transaction() as tx:
                    sessions = [r for r in tx.list("session") if r.data.get("status") in {"starting", "active", "unknown"}]
                    # No server runs, so nothing carries these: their outcome is unknown until reconciled.
                    operations = [r.key for r in tx.list("operation") if r.data.get("session_id")
                                  and r.data.get("status") in {"pending", "unknown", "completed_unapplied"}]
                    record = tx.get("recovery", "current")
                    report = record.data.get("report") if record is not None else None
            finally:
                store.close()
    except OrchestrationError as error:
        return _check("recovery", "warning", f"Recovery state cannot be read: {error}", code=error.code)
    interrupted = sorted(r.key for r in sessions)
    details = {"unclean_stop": bool(state.get("unclean_stop")), "interrupted": interrupted,
               "needs_reconciliation": sorted(operations),
               "last_report": None if report is None else {key: len(report.get(key, [])) for key in
                                                           ("recovered", "disconnected", "uncertain", "failed")}}
    if not interrupted and not state.get("unclean_stop") and not operations:
        return _check("recovery", "ok", "no hard restart detected and no interrupted sessions", **details)
    return _check("recovery", "warning",
                  f"the last server did not stop cleanly; {len(interrupted)} session(s) persisted as live: "
                  "`xorchestrate up` recovers them as the project's recovery_policy selects (default reconnect), "
                  "and `xorchestrate recover` recovers the rest"
                  + (f"; {len(operations)} interrupted operation(s) need reconciliation (get_operation, then "
                     "resolve_operation): their outcome is unknown and they are never replayed" if operations else ""),
                  **details)


def project_checks(path=None, environ=None, terminal_placement=None, server_state=None):
    """Approval, overrides, harness binaries and ``pass_env`` of the project at ``path``.

    ``terminal_placement(instance_id)`` gives ``{"placement", "tracked"}``: where
    the project's terminal host is (the recorded placement, else the one the
    launcher would compute) and the terminal names a record tracks. Its
    terminals are reported from ``inventory()``; any other is untracked.
    """
    checks = []
    try:
        services = Services(Home.locate(environ))
        tracked = locate_project(path, services.version_control)
        instance = resolve_instance(services.home, tracked, services.version_control)
        companion = services.companion(instance, validate=False)
    except OrchestrationError as error:
        return [_check("project", "error", str(error), code=error.code)], None
    checks.append(_check("project", "ok", f"{tracked.name} instance {instance.instance_id}",
                         worktree=tracked.identity()))
    if terminal_placement is not None:
        try:
            located = terminal_placement(instance.instance_id)
            checks.append(project_terminal_check(services, located["placement"], located.get("tracked", ())))
        except OrchestrationError as error:
            checks.append(_check("project_terminals", "warning", str(error), code=error.code))
    checks += companion_checks(services, companion)
    refusal = approval_refusal(companion, tracked)
    if refusal is not None:
        checks.append(_check("approval", "error", str(refusal), **refusal.details))
    else:
        checks.append(_check("approval", "ok", f"configuration {tracked.config_hash[:12]} is approved"))
    pending = upgrade_refusal(instance, tracked)
    if pending is not None:
        checks.append(_check("release", "error", str(pending), code=pending.code, **pending.details))
    else:
        checks.append(_check("release", "ok", f"the instance runs the pinned release {tracked.release}"))
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
    if server_state is not None:
        checks.append(recovery_check(rendered, instance.data_dir, server_state(instance.instance_id),
                                     approved=refusal is None))
    return checks, rendered


def terminal_host_check(environ=None):
    """Which terminal host local work runs in, why, and whether its terminals survive and are interactive.

    Probes readiness at a placement in the home's server directory; nothing is started.
    """
    try:
        home = Home.locate(environ)
        servers = home.root / "servers"
        placement = {"name": "doctor", "location": str(servers / "doctor.terminals"),
                     "socket": str(servers / "doctor.terminals.sock")}
        active = Services(home).terminal_host(placement)
    except OrchestrationError as error:
        return _check("terminal_host", "error", str(error), code=error.code, **error.details)
    report = active.report()
    active.host.close()
    traits = (f"terminals {'survive' if report['survives_controller'] else 'do not survive'} the controller and "
              f"{'are' if report['interactive'] else 'are not'} interactive")
    if report["fallback_reason"] is not None:
        return _check("terminal_host", "warning", f"{report['selected']} is not ready ({report['fallback_reason']}); "
                      f"using the fallback {report['adapter']}: {traits}", **report)
    return _check("terminal_host", "ok", f"{report['adapter']}: {traits}", **report)


def doctor(path=None, environ=None, *, project=True, terminal_placement=None, server_state=None):
    checks = machine_checks() + [terminal_host_check(environ)]
    if project:
        checks += project_checks(path, environ, terminal_placement, server_state)[0]
    return {"ok": all(item["status"] != "error" for item in checks), "checks": checks}
