"""``xorchestrate upgrade``: move this machine's instance to the release ``project.json`` pins.

The pinned release is part of the tracked configuration, so changing it is an
ordinary tracked change: edit ``release`` in ``.xorchestrate/project.json``,
commit it and ``xorchestrate approve`` it. ``upgrade`` never edits tracked files
and never approves anything. Until the instance is upgraded, project commands
are refused with ``upgrade_required`` (``down`` and ``status`` keep running with
the instance's installed release, so its server can still be stopped).

The procedure (each step prints into one JSON report):

1. **Preconditions.** The tracked configuration, with its pin, is approved;
   ``--to`` (when given) equals the pin. ``down`` stops the project server,
   refused while sessions are live unless ``--detach``; the server's
   single-instance lock is then held until the new release is recorded, so no
   server starts in between.
2. **Snapshot.** The ``before_upgrade`` telemetry trigger (selected by default)
   snapshots the running configuration's corpus, and archives it when
   ``archive`` is set.
3. **Backup.** ``backups/<UTC time>/`` in the instance directory receives an
   online SQLite backup of the state store, ``instance.json`` and
   ``config.rendered.json``, with a manifest of SHA-256 digests. Backups are
   never pruned.
4. **Toolchain.** The target release's toolchain is built with the release
   handoff's builder (the installed launcher itself when it is that release).
5. **Dry run.** The target release checks, without changing anything, that it
   reads the configuration (``config``), the state store's data version and
   pending migration steps (``data``), and the telemetry corpus
   (``telemetry``). Anything it cannot read blocks the upgrade
   (``upgrade_blocked``). ``--plan-only`` stops after this step, before ``down``.
6. **Migrate.** The target release runs the explicit data migration chain on
   the store, then the dry run is repeated to confirm the store is current.
7. **Flip.** ``instance.json`` records the new ``release``, ``data_version``,
   ``config_version`` and ``toolchain`` and appends an ``upgrades`` entry.
8. **Verify.** ``describe`` runs through the new release; ``doctor``'s report is
   included.
9. **Up.** A server that was running is started again.

Progress is kept in ``upgrade.json`` in the instance directory, so an
interrupted or failed upgrade resumes where it stopped when it is run again;
finished steps are not repeated. Rollback: before the flip, the instance still
runs the old release and its data is unchanged unless a migration step
committed (each step is one transaction); after a migration, restore
``state.sqlite3`` from the backup. To go back, restore the pin in
``project.json`` (the earlier configuration is still approved) and, if data was
migrated, copy the backup's ``state.sqlite3`` over the instance's store.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..adapters.local_socket import InstanceLock, lock_is_held
from ..adapters.storage import SQLiteStore
from ..adapters.storage_migrations import DATA_VERSION
from ..composition import RELEASE, SUPPORTED_CONFIG_VERSIONS, build_telemetry, sqlite_store_path
from ..model import OrchestrationError, canonical_digest
from . import snapshots
from .handoff import build_toolchain
from .home import Home, private_directory, read_document, write_document
from .instance import (
    UPGRADE_JOURNAL,
    approval_refusal,
    archive_journal,
    context_for,
    installed_release,
    load_instance,
    locate_project,
    resolve_instance,
)
from .render import RENDERED_FILE, render
from .services import Services

JOURNAL = UPGRADE_JOURNAL
LOCK = "upgrade.lock"
TOKEN_VARIABLE = "XORCHESTRATE_UPGRADE_TOKEN"
STEP_TIMEOUT = 600
STEP_COMMAND = "upgrade-step"


def _now():
    return datetime.now(timezone.utc)


def _stamp(moment):
    return moment.strftime("%Y%m%dT%H%M%S%fZ")


# -- steps run by the target release ---------------------------------------------

def _store_path(config, data_dir):
    """The SQLite file a rendered configuration's store opens, or ``None`` for another adapter.

    Resolved with the runtime's own rule (``composition.sqlite_store_path``,
    relative paths against the instance directory), never against the current
    directory, so the backup, the dry run and the migration address the store
    the project actually uses.
    """
    store = config.get("store") if isinstance(config, dict) else None
    if store is None:
        return Path(data_dir) / "state.sqlite3"
    if isinstance(store, dict) and store.get("adapter") == "sqlite":
        options = store.get("options") or {}
        if not isinstance(options, dict) or "path" not in options:
            return Path(data_dir) / "state.sqlite3"
        path = sqlite_store_path(options, data_dir)
        return None if path == ":memory:" else Path(path)
    return None


def _data_report(path):
    if path is None:
        return {"status": "not_applicable", "reason": "the store is not a SQLite file"}
    if not path.is_file():
        return {"status": "absent", "path": str(path)}
    store = SQLiteStore({"path": str(path)}, read_only=True)
    try:
        return {**store.data_version_info(), "path": str(path)}
    finally:
        store.close()


def _telemetry_report(config, context):
    try:
        telemetry = build_telemetry(config, project=context)
    except OrchestrationError as error:
        return {"status": "unreadable", "code": error.code, "cause": error.details.get("cause")}
    if telemetry is None:
        return {"status": "disabled"}
    try:
        status = telemetry.status()
    finally:
        telemetry.close()
    return {"status": "readable" if status.get("status") != "unavailable" else "unreadable",
            "schema_version": status.get("schema_version"), "event_count": status.get("event_count")}


BLOCKING_DATA = {"unsupported", "unrecognized", "migration_unavailable"}


def _guard_migration(instance, environ):
    """Migrate only for the upgrade in progress: its lock is held, it targets this release and passed its token."""
    environ = os.environ if environ is None else environ
    journal = read_document(instance.data_dir / JOURNAL, private=True) or {}
    token = journal.get("token")
    if (not lock_is_held(instance.data_dir / LOCK) or journal.get("to") != RELEASE or not token
            or environ.get(TOKEN_VARIABLE) != token):
        raise OrchestrationError("`upgrade-step migrate` runs only inside `xorchestrate upgrade` to this release.",
                                 "upgrade_required", {"release": RELEASE, "upgrade_target": journal.get("to")})


def upgrade_step(step, path=None, environ=None):
    """Run one step of an upgrade with this release; called by the upgrading launcher.

    ``check`` changes nothing and reports whether this release reads the
    project's configuration, state and telemetry; ``migrate`` runs the data
    migration chain. The project's tracked configuration must be approved.
    """
    if step not in {"check", "migrate"}:
        raise OrchestrationError("Unknown upgrade step.", "invalid_arguments", {"step": step})
    home = Home.locate(environ)
    services = Services(home)
    tracked = locate_project(path, services.version_control)
    instance = resolve_instance(home, tracked, services.version_control)
    companion = services.companion(instance)
    refusal = approval_refusal(companion, tracked)
    if refusal is not None:
        raise refusal
    if step == "migrate":
        _guard_migration(instance, environ)
    context = context_for(instance, tracked)
    report = {"release": RELEASE, "config_versions": sorted(SUPPORTED_CONFIG_VERSIONS), "data_version": DATA_VERSION}
    try:
        rendered = render(tracked, companion, context, write=False)
    except OrchestrationError as error:
        status = {"configuration_migration_required": "migration_required",
                  "configuration_version_unsupported": "unsupported"}.get(error.code, "invalid")
        report["config"] = {"status": status, "code": error.code, "message": str(error), "details": error.details}
        rendered = None
    else:
        report["config"] = {"status": "ok", "version": rendered.config.get("version", 1),
                            "warnings": list(rendered.warnings)}
    config = rendered.config if rendered is not None else None
    store = _store_path(config, instance.data_dir) if config is not None else instance.data_dir / "state.sqlite3"
    if step == "migrate":
        if rendered is None:
            raise OrchestrationError("This release cannot read the project's configuration.", "upgrade_blocked",
                                     {"config": report["config"]})
        if store is not None and store.is_file():
            SQLiteStore({"path": str(store)}, migrate=True).close()
        report["data"] = _data_report(store)
        return report
    report["data"] = _data_report(store)
    report["telemetry"] = _telemetry_report(config, context) if config is not None else {"status": "not_checked"}
    blockers = []
    if report["config"]["status"] != "ok":
        blockers.append({"area": "config", "status": report["config"]["status"], "code": report["config"]["code"]})
    if report["data"].get("status") in BLOCKING_DATA:
        blockers.append({"area": "data", "status": report["data"]["status"]})
    if report["telemetry"]["status"] == "unreadable":
        blockers.append({"area": "telemetry", "status": "unreadable"})
    report["blockers"] = blockers
    report["ready"] = not blockers
    return report


# -- the upgrading launcher -------------------------------------------------------

class Toolchain:
    """The interpreter of one release and how to run its commands for a project."""

    def __init__(self, release, python, built, *, root, home, runner=subprocess.run):
        self.release, self.python, self.built = release, Path(python), built
        self.root, self.home, self.runner = root, home, runner

    def run(self, *arguments, check=True, environment=None):
        """Run ``xorchestrate --project ROOT ARGUMENTS`` with this release; return ``(exit code, JSON, error)``."""
        environment = {**os.environ, "XORCHESTRATE_HOME": str(self.home.root), **(environment or {})}
        environment.pop("XORCHESTRATE_HANDOFF", None)
        result = self.runner([str(self.python), "-P", "-m", "xorchestrate", "--project", str(self.root), *arguments],
                             capture_output=True, text=True, timeout=STEP_TIMEOUT, check=False, env=environment,
                             cwd=str(self.home.root))
        lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
        try:
            value = json.loads(lines[-1]) if lines else None
        except ValueError:
            value = None
        try:
            error = json.loads((result.stderr or "").strip().splitlines()[-1]).get("error")
        except (ValueError, IndexError, AttributeError):
            error = None
        return result.returncode, value, error

    def step(self, name, token=None):
        code, value, error = self.run(STEP_COMMAND, name, environment={TOKEN_VARIABLE: token} if token else None)
        if code != 0 or not isinstance(value, dict):
            if isinstance(error, dict) and isinstance(error.get("code"), str):
                raise OrchestrationError(f"The {self.release} upgrade {name} failed: {error.get('message')}",
                                         error["code"], {"release": self.release, "step": name,
                                                         **(error.get("details") or {})})
            raise OrchestrationError(f"The {self.release} toolchain cannot run the upgrade {name} step.",
                                     "upgrade_step_failed", {"release": self.release, "step": name, "exit_code": code})
        if value.get("release") != self.release:
            raise OrchestrationError(f"The {self.release} toolchain runs release {value.get('release')}.",
                                     "toolchain_mismatch", {"release": self.release, "running": value.get("release")})
        return value


def toolchain(home, release, root, *, runner=subprocess.run):
    """The target release's toolchain: the installed launcher when it is that release, else a built one."""
    if release == RELEASE:
        return Toolchain(release, sys.executable, False, root=root, home=home, runner=runner)
    return Toolchain(release, build_toolchain(home, release, runner=runner), True, root=root, home=home,
                     runner=runner)


def _digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def store_fingerprint(path):
    """A logical fingerprint of a SQLite store's content (record count and versions, last event), or ``None``.

    Any write changes it, so a backup taken before it changed no longer describes the store.
    """
    if path is None or not Path(path).is_file():
        return None
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        records = connection.execute("SELECT COUNT(*), COALESCE(SUM(version), 0) FROM records").fetchone()
        events = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        version = connection.execute("PRAGMA user_version").fetchone()
        return canonical_digest({"records": list(records), "events": events[0], "user_version": version[0]})
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def instance_fingerprint(record):
    """What an upgrade changes in ``instance.json``: a journal is valid only while it is unchanged."""
    return canonical_digest({key: record.get(key) for key in ("release", "data_version", "upgrades")})


def backup(instance, running, *, source, target, snapshot):
    """Back up the instance's state store, records and rendered configuration; return the backup report."""
    moment = _now()
    directory = private_directory(instance.data_dir / "backups" / _stamp(moment))
    files = {}
    store = _store_path(running, instance.data_dir) if running is not None else instance.data_dir / "state.sqlite3"
    if store is not None and store.is_file():
        # The online backup API copies a consistent snapshot, WAL included, without writing the source.
        reader = sqlite3.connect(store.resolve().as_uri() + "?mode=ro", uri=True)
        writer = sqlite3.connect(directory / "state.sqlite3")
        try:
            reader.backup(writer)
        finally:
            writer.close()
            reader.close()
        files["state.sqlite3"] = _digest(directory / "state.sqlite3")
    for name in ("instance.json", RENDERED_FILE):
        if (instance.data_dir / name).is_file():
            shutil.copyfile(instance.data_dir / name, directory / name)
            files[name] = _digest(directory / name)
    manifest = {"schema": 1, "created_at": moment.isoformat(), "from": source, "to": target,
                "store": None if store is None else str(store), "files": files,
                "telemetry_snapshot": (snapshot or {}).get("bundle")}
    write_document(directory / "manifest.json", manifest)
    return {"path": str(directory), "files": files, "store": manifest["store"],
            "store_fingerprint": store_fingerprint(store)}


def _journal_path(instance):
    return instance.data_dir / JOURNAL


def _save(instance, journal):
    write_document(_journal_path(instance), journal)


def _running_config(instance):
    try:
        return read_document(instance.data_dir / RENDERED_FILE)
    except OrchestrationError:
        return None


def _before_upgrade(instance, tracked, companion, running):
    if running is None:
        return {"status": "skipped", "reason": "no_running_configuration"}
    if "before_upgrade" not in snapshots.policy(running)[0]:
        return {"status": "skipped", "reason": "trigger_not_selected"}
    return snapshots.snapshot(running, context_for(instance, tracked), ["before_upgrade"], companion=companion,
                              instance=instance, tracked=tracked)


def upgrade(path=None, *, to=None, plan_only=False, detach=False, server=None, environ=None,
            runner=subprocess.run, skip_snapshots=False):
    """Upgrade the instance of the project at ``path`` to its pinned release (see the module docstring).

    ``server`` stops, holds and starts the project's server (``stop(detach)``,
    ``hold()``, ``start()``); the command line supplies it.
    """
    home = Home.locate(environ)
    services = Services(home)
    tracked = locate_project(path, services.version_control)
    instance = resolve_instance(home, tracked, services.version_control)
    lock = InstanceLock(instance.data_dir / LOCK)
    if not lock.acquire():
        raise OrchestrationError("Another `xorchestrate upgrade` of this project is running.", "upgrade_in_progress",
                                 {"project": tracked.name})
    try:
        return _upgrade(home, services, tracked, instance, to=to, plan_only=plan_only, detach=detach, server=server,
                        runner=runner, skip_snapshots=skip_snapshots)
    finally:
        lock.release()


def _valid_journal(instance, source, target):
    """The journal of an upgrade that can resume, or ``None``; a stale journal is archived.

    A journal is stale when it targets another release, or when ``instance.json``
    no longer is what the attempt left (another upgrade, or a rollback, came
    in between). Unchanged ``instance.json`` with a changed store (written
    while the pin was restored) only invalidates the snapshot and backup.
    """
    journal = read_document(instance.data_dir / JOURNAL, private=True)
    if journal is None:
        return None, None
    fingerprint = instance_fingerprint(instance.record)
    if journal.get("flipped"):
        valid = source == journal.get("to") == target and journal.get("flipped_fingerprint") == fingerprint
    else:
        valid = journal.get("to") == target and journal.get("from") == source and \
            journal.get("fingerprint") == fingerprint
    if not valid:
        return None, archive_journal(instance, "superseded: the instance changed since this attempt")
    steps = journal.get("steps", {})
    backed_up = steps.get("backup")
    if not journal.get("migrating") and backed_up is not None:
        current = store_fingerprint(Path(backed_up["store"])) if backed_up.get("store") else None
        if current != backed_up.get("store_fingerprint"):
            # The store changed after the backup: take the snapshot and backup again.
            steps.pop("snapshot", None)
            steps.pop("backup", None)
    return journal, None


def _upgrade(home, services, tracked, instance, *, to, plan_only, detach, server, runner, skip_snapshots):
    companion = services.companion(instance)
    refusal = approval_refusal(companion, tracked)
    if refusal is not None:
        raise refusal
    source, target = installed_release(instance, tracked), tracked.release
    root = tracked.worktree.root
    if to is not None and to != target:
        if not plan_only:
            raise OrchestrationError(
                f"project.json pins {target}, not {to}. Change its release to {to}, commit and approve it "
                "(`xorchestrate approve`), then run `xorchestrate upgrade`; upgrade never edits tracked files.",
                "upgrade_target_mismatch", {"pinned": target, "requested": to})
        target = to
    report = {"project": tracked.name, "instance_id": instance.instance_id, "from": source, "to": target}
    if plan_only:
        chain = toolchain(home, target, root, runner=runner)
        return {**report, "plan_only": True, "toolchain": str(chain.python), "plan": chain.step("check")}
    journal, abandoned = _valid_journal(instance, source, target)
    if abandoned:
        report["abandoned_journal"] = abandoned
    if source == target and journal is None:
        return {**report, "status": "current"}
    if journal is None:
        stopped = server.stop(detach) if server is not None else {"was_running": False}
        journal = {"schema": 1, "from": source, "to": target, "started_at": _now().isoformat(), "steps": {},
                   "server": stopped, "flipped": False, "fingerprint": instance_fingerprint(instance.record),
                   "token": uuid4().hex}
        _save(instance, journal)
    elif not journal.get("flipped") and server is not None:
        stopped = server.stop(detach)
        journal["server"]["was_running"] = journal["server"].get("was_running") or stopped.get("was_running")
    steps = journal["steps"]
    report["resumed"] = bool(steps)
    try:
        if not journal.get("flipped"):
            with (server.hold() if server is not None else _nothing()):
                _procedure(home, instance, tracked, companion, journal, source, target, root, runner,
                           skip_snapshots)
                instance = load_instance(home, tracked.name)
        if "verify" not in steps:
            chain = toolchain(home, target, root, runner=runner)
            code, described, error = chain.run("describe")
            if code != 0:
                raise OrchestrationError(
                    f"Release {target} was recorded but `describe` failed with it; see the details, fix the cause "
                    "and run `xorchestrate upgrade` again, or roll back (docs/upgrade.md).",
                    "upgrade_verification_failed", {"error": error, "backup": steps.get("backup", {}).get("path")})
            _code, doctor_report, _error = chain.run("doctor")
            steps["verify"] = {"describe": "ok", "operations": len((described or {}).get("operations", [])),
                               "doctor_ok": (doctor_report or {}).get("ok")}
            _save(instance, journal)
        started = None
        if journal["server"].get("was_running") and server is not None:
            started = server.start()
            if not isinstance(started, dict) or "error" in started:
                # The release is recorded and verified; only the restart is left for a re-run.
                raise OrchestrationError(
                    f"Release {target} is recorded and verified, but the server that was running did not start "
                    "again; fix the cause and run `xorchestrate upgrade` again to restart it, or run "
                    "`xorchestrate up`.", "upgrade_restart_failed", {"start": started})
        _journal_path(instance).unlink(missing_ok=True)
        return {**report, "status": "upgraded", "steps": steps, "server": {**journal["server"], "started": started}}
    except OrchestrationError as error:
        if _journal_path(instance).exists():
            journal["status"] = {"upgrade_blocked": "blocked", "upgrade_verification_failed": "verify_failed",
                                 "upgrade_restart_failed": "restart_failed"}.get(error.code, "failed")
            journal["error"] = {"code": error.code, "message": str(error)}
            _save(instance, journal)
        raise


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _procedure(home, instance, tracked, companion, journal, source, target, root, runner, skip_snapshots=False):
    """Snapshot, backup, toolchain, dry run, migrate and flip; each finished step is kept in the journal."""
    steps = journal["steps"]
    running = _running_config(instance)
    if "snapshot" not in steps:
        steps["snapshot"] = ({"status": "skipped", "reason": "skip_requested"} if skip_snapshots
                             else _before_upgrade(instance, tracked, companion, running))
        _save(instance, journal)
    if "backup" not in steps:
        steps["backup"] = backup(instance, running, source=source, target=target, snapshot=steps["snapshot"])
        _save(instance, journal)
    chain = toolchain(home, target, root, runner=runner)
    steps["toolchain"] = {"release": target, "python": str(chain.python), "built": chain.built}
    plan = chain.step("check")
    steps["plan"] = plan
    _save(instance, journal)
    blockers = list(plan.get("blockers") or [])
    planned_store = (plan.get("data") or {}).get("path")
    if planned_store != steps["backup"].get("store") and (planned_store or steps["backup"].get("store")):
        # The approved change moves the store: the backup and the migration would not be the same store.
        blockers.append({"area": "store", "status": "moved", "from": steps["backup"].get("store"),
                         "to": planned_store})
    had_data = "state.sqlite3" in steps["backup"].get("files", {}) or instance.record.get("data_version") is not None
    if (plan.get("data") or {}).get("status") == "absent" and had_data:
        # The instance has had state: a store the target cannot find is a wrong path, not a new project.
        blockers.append({"area": "data", "status": "absent", "path": planned_store})
    if not plan.get("ready") or blockers:
        raise OrchestrationError(f"Release {target} cannot run this project as it is; nothing was migrated. See "
                                 "the blockers.", "upgrade_blocked", {"blockers": blockers})
    journal["migrating"] = True
    _save(instance, journal)
    migrated = chain.step("migrate", journal["token"])
    confirmed = chain.step("check")
    data = confirmed.get("data", {})
    if data.get("status") not in {"current", "absent", "not_applicable"}:
        raise OrchestrationError("The state store is not at the target data version after migrating.",
                                 "data_migration_failed", {"data": data})
    steps["migrate"] = {"before": plan.get("data"), "after": migrated.get("data")}
    _save(instance, journal)
    entry = {"from": source, "to": target, "at": _now().isoformat(), "backup": steps["backup"]["path"],
             "snapshot": steps["snapshot"].get("bundle"), "migrations": (plan.get("data") or {}).get("pending_steps", []),
             "companion_commit": companion.revision()}
    upgrades = list(instance.record.get("upgrades", []))
    last = upgrades[-1] if upgrades else {}
    if (last.get("from"), last.get("to"), last.get("backup")) != (source, target, entry["backup"]):
        upgrades.append(entry)  # recording again after a crash at this point adds nothing
    instance = instance.update(release=target, data_version=data.get("data_version"),
                               config_version=(confirmed.get("config") or {}).get("version"),
                               toolchain=str(chain.python) if chain.built else None, upgrades=upgrades)
    journal["flipped"] = True
    journal["flipped_fingerprint"] = instance_fingerprint(instance.record)
    journal.pop("status", None)
    journal.pop("error", None)
    _save(instance, journal)
