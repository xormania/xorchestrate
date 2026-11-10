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
from .companion import Companion
from .home import Home, private_directory, read_document, write_document
from .project import differing_files, locate_worktree, read_tracked, root_commits
from .render import copy_launchers, render

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
    record = read_document(home.project_dir(name) / INSTANCE_FILE)
    if record is None:
        return None
    if record.get("schema") != SCHEMA or not isinstance(record.get("instance_id"), str):
        raise OrchestrationError("Unsupported instance record.", "invalid_configuration",
                                 {"path": str(home.project_dir(name) / INSTANCE_FILE)})
    return Instance(home, name, record["instance_id"], record)


def _same_project(instance, tracked):
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
    here, there = root_commits(tracked.worktree.root), root_commits(instance.record.get("repository") or recorded)
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


def register(home, tracked):
    """Register the project's instance, or return the existing one; refuse a separate clone."""
    existing = load_instance(home, tracked.name)
    if existing is not None:
        return _same_project(existing, tracked), False
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


def locate_project(path=None):
    """The worktree and tracked project at ``path`` (default: the working directory)."""
    worktree = locate_worktree(path)
    if worktree is None:
        raise project_required("Not inside a git repository", path=str(Path(path or Path.cwd()).resolve()))
    tracked = read_tracked(worktree)
    if tracked is None:
        raise project_required("This repository has no .xorchestrate/project.json", repository=str(worktree.root))
    return tracked


def resolve_instance(home, tracked):
    instance = load_instance(home, tracked.name)
    if instance is None:
        raise project_required(f"Project {tracked.name} is not set up on this machine", project=tracked.name)
    return _same_project(instance, tracked)


def require_companion(home):
    path = home.companion_path()
    if path is None:
        raise OrchestrationError("No companion is registered; run `xorchestrate setup`.", "companion_required")
    return Companion.open(path)


def approval_refusal(companion, tracked):
    """``None`` when the tracked configuration is approved, else the refusal naming the changed files."""
    if companion.is_approved(tracked.name, tracked.config_hash):
        return None
    hashes = companion.approved_hashes(tracked.name)
    previous = companion.approved_files(tracked.name, hashes[-1]) if hashes else None
    details = {"project": tracked.name, "config_hash": tracked.config_hash,
               "files": differing_files(previous or {}, tracked.digests)}
    return OrchestrationError(
        f"The tracked configuration of {tracked.name} is not approved; review it, then run `xorchestrate approve`.",
        "approval_required", details)


def context_for(instance, tracked, dataset_id="default"):
    return ProjectContext(tracked.name, instance.instance_id, instance.data_dir, tracked.identity(), dataset_id)


@dataclass(frozen=True)
class Prepared:
    """A project ready to run: approved, rendered, with its explicit context for ``build``."""

    home: Home
    tracked: object
    instance: Instance
    companion: Companion
    context: ProjectContext
    rendered: object

    @property
    def config(self):
        return self.rendered.config

    @property
    def worktree(self):
        return self.context.worktree


def prepare(path=None, *, environ=None):
    """Find the project at ``path``, check its approval and render it.

    The approval is checked before every render, so a running server's
    configuration can only be replaced by an approved one.
    """
    home = Home.locate(environ)
    tracked = locate_project(path)
    instance = resolve_instance(home, tracked)
    companion = require_companion(home)
    refusal = approval_refusal(companion, tracked)
    if refusal is not None:
        raise refusal
    context = context_for(instance, tracked)
    launchers = copy_launchers(companion, instance.data_dir)
    rendered = render(tracked, companion, context)
    instance = instance.update(release=tracked.release, approval_hash=tracked.config_hash,
                               rendered_at=datetime.now(timezone.utc).isoformat(), launchers=launchers,
                               versions={"xorchestrate": __version__})
    return Prepared(home, tracked, instance, companion, context, rendered)
