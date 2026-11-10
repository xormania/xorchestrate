"""One instance per project per machine (D11), shared by every worktree.

```
<home>/projects/<project>/instance.json        provenance: instance id, git common directory, release, approval
<home>/projects/<project>/config.rendered.json the configuration the server runs (render.py)
<home>/projects/<project>/state.sqlite3        application state (the rendered store default)
<home>/projects/<project>/telemetry/           the live telemetry corpus
<home>/projects/<project>/launchers/           launchers copied from the companion
<home>/projects/<project>/backups/             upgrade backups
```

A project is identified by its git common directory, so every worktree maps
to the same instance. ``init`` in a separate clone of the same project is
refused and names the existing instance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .. import __version__
from ..composition import ProjectContext
from ..model import OrchestrationError
from . import companion as records
from . import snapshots
from .home import Home, private_directory, read_document, write_document
from .project import differing_files, locate_worktree, read_tracked
from .render import copy_launchers, render
from .services import Services

INSTANCE_FILE = "instance.json"
SCHEMA = 1
SUBDIRECTORIES = ("telemetry", "launchers", "backups")
INIT_HINT = "run `xorchestrate init` in the project's repository, or pass --project PATH"


def project_required(message, **details):
    return OrchestrationError(f"{message}; {INIT_HINT}.", "project_required", details)


@dataclass(frozen=True)
class Instance:
    home: Home
    name: str
    instance_id: str
    record: dict

    @property
    def data_dir(self):
        return self.home.project_dir(self.name)

    @property
    def record_path(self):
        return self.data_dir / INSTANCE_FILE

    def update(self, **values):
        record = {**self.record, **values}
        write_document(self.record_path, record)
        return Instance(self.home, self.name, self.instance_id, record)


def load_instance(home, name):
    record = read_document(home.project_dir(name) / INSTANCE_FILE, private=True)
    if record is None:
        return None
    if record.get("schema") != SCHEMA or not isinstance(record.get("instance_id"), str):
        raise OrchestrationError("Unsupported instance record.", "invalid_configuration",
                                 {"path": str(home.project_dir(name) / INSTANCE_FILE)})
    return Instance(home, name, record["instance_id"], record)


def _same_project(instance, tracked, version_control):
    """Refuse a repository other than the instance's, saying which case it is.

    - The recorded repository is gone: it was probably moved or renamed
      (``project_moved``; ``init --relocate`` re-points the instance).
    - Both repositories share a root commit: a separate clone of the same
      project (``project_clone_refused``; use a worktree instead).
    - Otherwise: an unrelated repository that uses the same project name
      (``project_name_conflict``; rename one of them in its project.json).
    """
    recorded = instance.record.get("git_common_dir")
    if recorded == str(tracked.worktree.common_dir):
        return instance
    details = {"project": tracked.name, "instance_id": instance.instance_id,
               "repository": instance.record.get("repository"), "git_common_dir": recorded}
    if not recorded or not Path(recorded).exists():
        raise OrchestrationError(
            f"The repository of project {tracked.name} is no longer at {instance.record.get('repository')}. If this "
            "is the same repository, moved or renamed, run `xorchestrate init --relocate` here.", "project_moved",
            details)
    here = version_control.root_commits(str(tracked.worktree.root))
    there = version_control.root_commits(instance.record.get("repository") or recorded)
    if here & there:
        raise OrchestrationError(
            f"Project {tracked.name} is already set up on this machine from another clone; use a worktree of "
            "that clone (git worktree add) instead of a separate clone.", "project_clone_refused", details)
    raise OrchestrationError(
        f"Another repository ({instance.record.get('repository')}) already uses the project name {tracked.name} on "
        "this machine; if they are different projects, rename one in its .xorchestrate/project.json.",
        "project_name_conflict", details)


def relocate(home, tracked):
    """Re-point an instance whose recorded repository no longer exists to this repository."""
    instance = load_instance(home, tracked.name)
    if instance is None:
        raise project_required(f"Project {tracked.name} is not set up on this machine", project=tracked.name)
    recorded = instance.record.get("git_common_dir")
    if recorded == str(tracked.worktree.common_dir):
        return instance, False
    if recorded and Path(recorded).exists():
        raise OrchestrationError("The instance's recorded repository still exists; --relocate only re-points an "
                                 "instance whose repository was moved or removed.", "project_not_moved",
                                 {"repository": instance.record.get("repository"), "git_common_dir": recorded})
    instance = instance.update(git_common_dir=str(tracked.worktree.common_dir), repository=str(tracked.worktree.root))
    home.register(instance.instance_id, {"project": tracked.name, "repository": str(tracked.worktree.root),
                                         "git_common_dir": str(tracked.worktree.common_dir)})
    return instance, True


def register(home, tracked, version_control=None):
    """Register the project's instance, or return the existing one; refuse a separate clone."""
    existing = load_instance(home, tracked.name)
    if existing is not None:
        return _same_project(existing, tracked, version_control or Services(home).version_control), False
    home.ensure()
    private_directory(home.projects)
    data_dir = private_directory(home.project_dir(tracked.name))
    for name in SUBDIRECTORIES:
        private_directory(data_dir / name)
    instance_id = uuid4().hex
    record = {"schema": SCHEMA, "instance_id": instance_id, "project": tracked.name,
              "git_common_dir": str(tracked.worktree.common_dir), "repository": str(tracked.worktree.root),
              "release": tracked.release, "created_at": datetime.now(timezone.utc).isoformat(),
              "versions": {"xorchestrate": __version__}}
    write_document(data_dir / INSTANCE_FILE, record)
    home.register(instance_id, {"project": tracked.name, "repository": str(tracked.worktree.root),
                                "git_common_dir": str(tracked.worktree.common_dir)})
    return Instance(home, tracked.name, instance_id, record), True


def locate_project(path=None, version_control=None):
    """The worktree and tracked project at ``path`` (default: the working directory)."""
    worktree = locate_worktree(path, version_control)
    if worktree is None:
        raise project_required("Not inside a git repository", path=str(Path(path or Path.cwd()).resolve()))
    tracked = read_tracked(worktree)
    if tracked is None:
        raise project_required("This repository has no .xorchestrate/project.json", repository=str(worktree.root))
    return tracked


def resolve_instance(home, tracked, version_control=None):
    instance = load_instance(home, tracked.name)
    if instance is None:
        raise project_required(f"Project {tracked.name} is not set up on this machine", project=tracked.name)
    return _same_project(instance, tracked, version_control or Services(home).version_control)


def require_companion(home, instance=None):
    """The companion selected for ``instance`` (its own, else the machine default)."""
    return Services(home).companion(instance)


def approval_refusal(companion, tracked):
    """``None`` when the tracked configuration is approved, else the refusal naming the changed files."""
    if records.is_approved(companion, tracked.name, tracked.config_hash):
        return None
    hashes = records.approved_hashes(companion, tracked.name)
    previous = records.approved_files(companion, tracked.name, hashes[-1]) if hashes else None
    details = {"project": tracked.name, "config_hash": tracked.config_hash,
               "files": differing_files(previous or {}, tracked.digests)}
    return OrchestrationError(
        f"The tracked configuration of {tracked.name} is not approved; review it, then run `xorchestrate approve`.",
        "approval_required", details)


def installed_release(instance, tracked=None):
    """The release this machine's instance was set up or last upgraded for."""
    return instance.record.get("release", None if tracked is None else tracked.release)


def upgrade_refusal(instance, tracked):
    """``None`` when the instance runs the release ``project.json`` pins, else the refusal naming ``upgrade``.

    A changed pin is a tracked change: it is approved like any other, and it
    takes effect only through ``xorchestrate upgrade``, which stops the
    server, backs up and migrates the instance's data before recording the new
    release. Until then project commands are refused, so no release runs
    against data or a configuration it was not upgraded for.
    """
    installed = installed_release(instance, tracked)
    if installed == tracked.release:
        return None
    return OrchestrationError(
        f"Project {tracked.name} pins release {tracked.release}, but this machine's instance runs {installed}; run "
        f"`xorchestrate upgrade` to upgrade it, or restore the pin to {installed}.", "upgrade_required",
        {"project": tracked.name, "pinned": tracked.release, "installed": installed})


def context_for(instance, tracked, dataset_id="default"):
    return ProjectContext(tracked.name, instance.instance_id, instance.data_dir, tracked.identity(), dataset_id)


@dataclass(frozen=True)
class Prepared:
    """A project ready to run: approved, rendered, with its explicit context for ``build``."""

    home: Home
    tracked: object
    instance: Instance
    companion: object
    context: ProjectContext
    rendered: object
    snapshots: tuple = ()
    """Reports of the telemetry snapshots this render triggered (``before_approve``, ``before_sync``)."""

    @property
    def config(self):
        return self.rendered.config

    @property
    def worktree(self):
        return self.context.worktree


SNAPSHOT_HISTORY = 20
UPGRADE_JOURNAL = "upgrade.json"


def archive_journal(instance, reason):
    """Move an upgrade journal aside as ``upgrade.<UTC time>.abandoned.json``; return its new path."""
    path = instance.data_dir / UPGRADE_JOURNAL
    if not path.exists():
        return None
    journal = read_document(path, private=True) or {}
    target = path.with_name(f"upgrade.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.abandoned.json")
    write_document(target, {**journal, "abandoned": reason})
    path.unlink()
    return str(target)


def abandon_upgrade(instance, reason):
    """Archive an unfinished upgrade journal once the instance runs normally without it.

    Called when the instance runs the pinned release: an attempt that never
    recorded its release was abandoned (for example by restoring the pin), so a
    later upgrade must not resume its old snapshot and backup. A journal whose
    release was recorded is kept for ``upgrade`` to finish verifying.
    """
    path = instance.data_dir / UPGRADE_JOURNAL
    if not path.exists():
        return None
    journal = read_document(path, private=True) or {}
    if journal.get("flipped") and journal.get("to") == installed_release(instance):
        return None
    return archive_journal(instance, reason)


def _triggered_snapshots(instance, tracked, companion, context, rendered, skip=False):
    """Take the snapshots the running configuration selects before this render replaces it.

    ``before_approve`` fires when the tracked configuration's hash differs from
    the one last rendered (a newly approved configuration takes effect);
    ``before_sync`` when the rendered configuration itself changes. The
    running (previous) configuration's corpus and ``snapshots`` option are
    used, and a trigger fires only while the new configuration still selects
    it, so deselecting a failing trigger takes effect. A selected snapshot that
    fails refuses the render (``snapshot_failed``), so the previous rendered
    file stays in place; ``skip`` records the snapshot as skipped instead.
    """
    try:
        previous = read_document(rendered.path)
    except OrchestrationError:
        previous = None
    if previous is None:
        return ()
    moments = []
    if instance.record.get("approval_hash") not in (None, tracked.config_hash):
        moments.append("before_approve")
    if previous != rendered.config:
        moments.append("before_sync")
    configured = set(snapshots.policy(previous)[0]) & set(snapshots.policy(rendered.config)[0]) if moments else ()
    selected = [moment for moment in moments if moment in configured]
    if not selected:
        return ()
    if skip:
        return ({"status": "skipped", "triggers": selected, "reason": "skip_requested"},)
    return (snapshots.snapshot(previous, context, selected, companion=companion, instance=instance,
                               tracked=tracked),)


def prepare(path=None, *, environ=None, skip_snapshots=False):
    """Find the project at ``path``, check its approval and release, and render it.

    The approval is checked before every render, so a running server's
    configuration can only be replaced by an approved one, and the instance
    must run the pinned release (``upgrade_required`` otherwise). Snapshots
    the running configuration selects for this moment are taken before the
    rendered file is replaced, unless ``skip_snapshots``. An upgrade
    abandoned by restoring the pin is archived (see ``upgrade.py``).
    """
    home = Home.locate(environ)
    services = Services(home)
    tracked = locate_project(path, services.version_control)
    instance = resolve_instance(home, tracked, services.version_control)
    companion = services.companion(instance)
    for refusal in (approval_refusal(companion, tracked), upgrade_refusal(instance, tracked)):
        if refusal is not None:
            raise refusal
    context = context_for(instance, tracked)
    rendered = render(tracked, companion, context, write=False)
    abandon_upgrade(instance, "the instance runs the pinned release again")
    taken = _triggered_snapshots(instance, tracked, companion, context, rendered, skip_snapshots)
    launchers = copy_launchers(companion, instance.data_dir)
    write_document(rendered.path, rendered.config)
    history = [*instance.record.get("snapshots", []), *taken][-SNAPSHOT_HISTORY:]
    instance = instance.update(approval_hash=tracked.config_hash, rendered_at=datetime.now(timezone.utc).isoformat(),
                               launchers=launchers, versions={"xorchestrate": __version__},
                               overlay_sha256=snapshots.overlay_sha256(companion, tracked.name),
                               **({"snapshots": history} if taken else {}))
    return Prepared(home, tracked, instance, companion, context, rendered, taken)
