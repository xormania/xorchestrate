"""Maintenance: judge every tree and scratch directory, remove only what is measured safe.

The report changes nothing. Prune is a dry run unless asked to apply, re-checks each
tree just before removing it, backs up a registration before dropping it, and never
deletes an orphan directory or an unmarked entry.
"""

from __future__ import annotations

import json
import posixpath
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from scripts.worktrees.core import Repository, SerenaService, TreeService
from scripts.worktrees.model import Config, Outcome, Proc, Refusal, Tree
from scripts.worktrees.ports import Clock, DiskUsage, FileSystem, GitError, Github, ProcessTable, StateStore

DAY = 86_400.0
HOUR = 3_600.0
GIB = 1024.0 ** 3
SCRATCH_MARK = ".scratch.json"
RUN_MARK = "plan.json"
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
VENV_DIRS = (".venv", "venv")
BUILD_DIRS = ("build", "dist", ".pytest_cache")
CACHE_DIRS = frozenset({"__pycache__"})
REPORT_HINT = "see python3 -m scripts.worktrees report"


def _inside(path: str, root: str) -> bool:
    path, root = posixpath.normpath(path), posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


@dataclass
class TreeRow:
    path: str
    branch: str | None
    head: str | None
    last_commit_age_s: float | None
    last_use: float | None
    size: dict[str, int]
    merged: bool
    on_origin: bool
    clean: bool | None
    stash: bool
    locked: bool
    in_use: bool
    serena: str
    verdict: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class DirRow:
    path: str
    kind: str
    size: int
    age_s: float | None
    marked: bool | None
    recognised: bool | None
    in_use: bool
    expired: bool


@dataclass
class Report:
    trees: list[TreeRow]
    dirs: list[DirRow]

    def totals(self) -> dict[str, Any]:
        # Reclaimable space only: not the main checkout, not report-only extra roots.
        return {"size": sum(row.size["total"] for row in self.trees if row.verdict != "MAIN")
                + sum(row.size for row in self.dirs if row.kind != "extra"),
                "removable": sum(row.verdict == "REMOVABLE" for row in self.trees),
                "needs_look": sum(row.verdict == "NEEDS_LOOK" for row in self.trees),
                "expired": sum(row.expired for row in self.dirs)}

    def as_json(self) -> dict[str, Any]:
        return {"trees": [vars(row) for row in self.trees], "dirs": [vars(row) for row in self.dirs],
                "totals": self.totals()}


class Maintenance:
    def __init__(self, config: Config, repo: Repository, processes: ProcessTable, store: StateStore,
                 files: FileSystem, disk: DiskUsage, github: Github, clock: Clock, trees: TreeService,
                 serena: SerenaService) -> None:
        self.config = config
        self.repo = repo
        self.processes = processes
        self.store = store
        self.files = files
        self.disk = disk
        self.github = github
        self.clock = clock
        self.trees = trees
        self.serena = serena

    # Measuring

    def unsafe_root(self) -> Refusal | None:
        """A scratch, run or extra root that is "/", the home directory, or overlaps a checkout or the state."""
        real = self.files.real_path
        protected = [real(tree.path) for tree in self.repo.trees()] + [real(self.config.state_dir)]
        home = real(self.config.home) if self.config.home else None
        roots = [("XORCHESTRATE_DEV_SCRATCH_ROOT", self.config.scratch_root)]
        roots += [("XORCHESTRATE_DEV_RUN_ROOTS", root) for root in self.config.run_roots]
        roots += [("XORCHESTRATE_DEV_EXTRA_ROOTS", root) for root in self.config.extra_roots]
        for key, root in roots:
            resolved = real(root)
            if resolved == "/" or resolved == home:
                return Refusal("CONFIG_INVALID", f"{key}: {root} is the filesystem root or the home directory")
            for other in protected:
                if _inside(resolved, other) or _inside(other, resolved):
                    return Refusal("CONFIG_INVALID", f"{key}: {root} overlaps {other}")
        return None

    def measure(self, offline: bool = False) -> Report:
        table = self.processes.processes()
        base = self.config.base or self.repo.default_base()
        registered = self.repo.trees()
        rows = [self._tree_row(tree, base, table, offline) for tree in registered]
        rows += [self._orphan_row(path) for path in self._orphans({tree.path for tree in registered})]
        dirs = [self._dir_row(path, "scratch", table) for path in self._entries(self.config.scratch_root)]
        dirs += [self._dir_row(path, "run", table) for root in self.config.run_roots for path in self._entries(root)]
        dirs += [self._dir_row(path, "extra", table) for root in self.config.extra_roots
                 for path in self._entries(root)]
        return Report(rows, dirs)

    def _entries(self, root: str) -> list[str]:
        """Real paths of the directories directly under ``root`` (itself resolved); links are skipped."""
        root = self.files.real_path(root)
        return [posixpath.join(root, name) for name in self.files.list_dir(root)
                if self.files.is_dir(posixpath.join(root, name)) and not self.files.is_symlink(posixpath.join(root, name))]

    def _users(self, path: str, table: list[Proc], exclude: set[int] = frozenset()) -> list[Proc]:
        return [proc for proc in table if proc.pid not in exclude
                and (_inside(proc.cwd, path) or any(_inside(item, path) for item in proc.open_paths))]

    def _size(self, path: str) -> dict[str, int]:
        if not self.files.is_dir(path):
            return {"total": 0, "serena": 0, "venv": 0, "build": 0}
        counted = [posixpath.join(path, name) for name in (*VENV_DIRS, *BUILD_DIRS, ".serena")]
        build = sum(self.disk.size(posixpath.join(path, name)) for name in BUILD_DIRS)
        build += sum(self.disk.size(found) for found in self.files.find_dirs(path, CACHE_DIRS)
                     if not any(_inside(found, prefix) for prefix in counted))
        return {"total": self.disk.size(path), "serena": self.disk.size(posixpath.join(path, ".serena")),
                "venv": sum(self.disk.size(posixpath.join(path, name)) for name in VENV_DIRS), "build": build}

    def _admin_dir(self, tree: Tree) -> str | None:
        common = self.repo.out(["rev-parse", "--path-format=absolute", "--git-common-dir"])
        if not common:
            return None
        worktrees = posixpath.join(common, "worktrees")
        target = posixpath.join(posixpath.normpath(tree.path), ".git")
        for name in self.files.list_dir(worktrees):
            pointer = (self.files.read_text(posixpath.join(worktrees, name, "gitdir")) or "").strip()
            if pointer and posixpath.normpath(pointer) == target:
                return posixpath.join(worktrees, name)
        return None

    def _own_commits(self, branch: str | None, tip: str) -> bool:
        """Whether ``branch`` has commits of its own since it was created (from its reflog); unknown means no."""
        if not branch:
            return False
        created = (self.repo.out(["reflog", "show", "--format=%H", f"refs/heads/{branch}", "--"]) or "").splitlines()
        return bool(created) and created[-1] != tip

    def _on_first_parent_history(self, tip: str, base: str) -> bool:
        """Whether ``tip`` is on the base's own line (a fast-forward or no work at all), not merged-in work.

        Work merged through a merge commit is reached only through its second parent. A tip on the
        base's first-parent line may be a fresh or merely advanced branch, so it never counts as merged.
        Unknown counts as on the line (fail safe).
        """
        history = self.repo.out(["rev-list", "--first-parent", base])
        return history is None or tip in history.split()

    def _merged(self, tree: Tree, base: str | None, offline: bool) -> bool:
        """Merged into the base with work of its own, or a pull request merged into the default branch."""
        if not tree.head:
            return False
        if (base and self.repo.is_ancestor(tree.head, base) and self._own_commits(tree.branch, tree.head)
                and not self._on_first_parent_history(tree.head, base)):
            return True
        if offline or not tree.branch:
            return False
        name = (base or "").removeprefix("refs/remotes/").removeprefix("origin/") or None
        try:
            return self.github.merged_pr_head(tree.branch, base=name) == tree.head
        except Exception:  # noqa: BLE001 - GitHub evidence is optional; any failure means none
            return False

    def _tree_row(self, tree: Tree, base: str | None, table: list[Proc], offline: bool) -> TreeRow:
        now = self.clock.now()
        exists = self.files.is_dir(tree.path)
        state = None if tree.main or not exists else self.store.load(self.serena.key(tree.path))
        server = self.serena.ours(state, tree.path, table) if state else None
        admin = None if tree.main else self._admin_dir(tree)
        uses = [self.files.mtime(posixpath.join(admin, name)) for name in ("index", "logs/HEAD")] if admin else []
        uses.append(state.last_call if state else None)
        last_use = max((value for value in uses if value is not None), default=None)
        committed = self.repo.out(["log", "-1", "--format=%ct", tree.head]) if tree.head else None
        last_commit_age = now - float(committed) if committed else None
        size = self._size(tree.path)
        clean = (self.repo.out(["--no-optional-locks", "status", "--porcelain"], tree.path) == "") if exists else None
        merged = self._merged(tree, base, offline)
        on_origin = bool(tree.head) and self.repo.on_origin(tree.head)
        try:
            stash = bool(tree.branch) and self.repo.stashed_on(tree.branch)
        except GitError:
            stash = True  # an unreadable stash is a reason to look, never a reason to remove
        own = self.serena.family_of(tree.path, table) if server else set()
        in_use = exists and bool(self._users(tree.path, table, own))
        serena = "up" if server else "down"
        reasons: list[str] = []
        if tree.main:
            verdict = "MAIN"
        else:
            if not exists:
                reasons.append("MISSING_DIR")
            if clean is False:
                reasons.append("DIRTY")
            if exists and tree.head and not merged and not on_origin:
                reasons.append("UNPUSHED")
            if stash:
                reasons.append("STASH")
            if tree.locked:
                reasons.append("LOCKED")
            if merged and in_use:
                reasons.append("IN_USE_MERGED")
            if (exists and not merged and last_use is not None
                    and now - last_use > self.config.idle_days * DAY):
                reasons.append("IDLE")
            if reasons:
                verdict = "NEEDS_LOOK"
            elif merged and not in_use:
                verdict = "REMOVABLE"
            else:
                verdict = "KEEP"
        return TreeRow(path=tree.path, branch=tree.branch, head=tree.head, last_commit_age_s=last_commit_age,
                       last_use=last_use, size=size, merged=merged, on_origin=on_origin, clean=clean, stash=stash,
                       locked=tree.locked, in_use=in_use, serena=serena, verdict=verdict, reasons=reasons)

    def _orphans(self, registered: set[str]) -> list[str]:
        registered = {posixpath.normpath(path) for path in registered}
        found, pending = [], [self.config.root]
        while pending:
            current = pending.pop()
            for name in self.files.list_dir(current):
                path = posixpath.join(current, name)
                if not self.files.is_dir(path) or self.files.is_symlink(path) or name == ".git":
                    continue
                if posixpath.normpath(path) in registered:
                    continue
                if self.files.exists(posixpath.join(path, ".git")):
                    found.append(path)
                else:
                    pending.append(path)
        return sorted(found)

    def _orphan_row(self, path: str) -> TreeRow:
        return TreeRow(path=path, branch=None, head=None, last_commit_age_s=None, last_use=self.files.mtime(path),
                       size=self._size(path), merged=False, on_origin=False, clean=None, stash=False, locked=False,
                       in_use=False, serena="down", verdict="NEEDS_LOOK", reasons=["ORPHAN_DIR"])

    def _dir_row(self, path: str, kind: str, table: list[Proc]) -> DirRow:
        now = self.clock.now()
        marked = recognised = None
        age_from = self.files.mtime(path)
        if kind == "scratch":
            mark = self._mark(path)
            marked = mark is not None
            if mark is not None and isinstance(mark.get("created"), (int, float)):
                age_from = float(mark["created"])
        elif kind == "run":
            # A run directory holds a plan; one that is also a git checkout is never treated as a run.
            recognised = (self.files.exists(posixpath.join(path, RUN_MARK))
                          and not self.files.exists(posixpath.join(path, ".git")))
            if recognised:
                age_from = self.files.mtime(posixpath.join(path, RUN_MARK))
        age = now - age_from if age_from is not None else None
        in_use = bool(self._users(path, table))
        limit = {"scratch": self.config.scratch_max_age_hours * HOUR,
                 "run": self.config.run_max_age_days * DAY}.get(kind)
        expired = bool(limit is not None and age is not None and age > limit and not in_use
                       and (marked if kind == "scratch" else recognised))
        return DirRow(path=path, kind=kind, size=self.disk.size(path), age_s=age, marked=marked,
                      recognised=recognised, in_use=in_use, expired=expired)

    def _mark(self, path: str) -> dict[str, Any] | None:
        text = self.files.read_text(posixpath.join(path, SCRATCH_MARK))
        if text is None:
            return None
        try:
            mark = json.loads(text)
        except ValueError:
            return {}
        return mark if isinstance(mark, dict) else {}

    # Commands

    def report(self, offline: bool = False) -> Outcome:
        data = self.measure(offline)
        outcome = Outcome()
        for row in data.trees:
            detail = ",".join(row.reasons)
            outcome.out.append(f"{row.verdict:<10} {row.path}  {row.branch or '-'}  {_human(row.size['total'])}"
                               + (f"  {detail}" if detail else ""))
        for row in data.dirs:
            state = "expired" if row.expired else ("in use" if row.in_use else "")
            outcome.out.append(f"{row.kind:<10} {row.path}  {_human(row.size)}  {_age(row.age_s)}  {state}".rstrip())
        totals = data.totals()
        outcome.out.append(f"total {_human(totals['size'])}: {totals['removable']} removable, "
                           f"{totals['needs_look']} need a look, {totals['expired']} expired")
        return outcome

    def report_json(self, offline: bool = False) -> dict[str, Any]:
        return self.measure(offline).as_json()

    def brief(self) -> str | None:
        """Always offline: a session-start hook never waits on the network."""
        totals = self.measure(offline=True).totals()
        large = totals["size"] > self.config.brief_size_gb * GIB
        if not (totals["removable"] or totals["needs_look"] or totals["expired"] or large):
            return None
        return (f"worktrees: {totals['removable']} removable, {totals['needs_look']} need a look, "
                f"{totals['expired']} expired dirs, {_human(totals['size'])}; {REPORT_HINT}")

    def prune(self, apply: bool = False, offline: bool = False) -> Outcome:
        outcome = Outcome()
        if not offline and not self.repo.ok(["fetch", "origin"]):
            return outcome.refuse(Refusal("FETCH_FAILED", "git fetch origin failed; nothing was changed "
                                          "(use --offline to judge without the remote)"))
        plan = self.measure(offline)
        review = [row for row in plan.trees if row.verdict == "NEEDS_LOOK"]
        verb = "removing" if apply else "would remove"
        for row in plan.trees:
            if row.verdict == "REMOVABLE":
                outcome.out.append(f"{verb} {row.path} ({row.branch})")
                if apply:
                    self._remove(row, outcome, review)
        for row in plan.trees:
            if "MISSING_DIR" in row.reasons:
                self._drop_registration(row, apply, outcome)
        if apply:
            reaped = self.serena.reap()
            outcome.out += reaped.out
            outcome.err += reaped.err
        for row in plan.dirs:
            # Only what the tool marked (scratch) or recognises as a run; extra roots are report-only.
            if row.kind not in ("scratch", "run") or not row.expired:
                continue
            if not apply:
                outcome.out.append(f"would delete {row.path}")
            elif self._users(row.path, self.processes.processes()):
                outcome.err.append(f"kept {row.path}: in use")
            else:
                try:
                    self.files.remove_tree(row.path)
                    outcome.out.append(f"deleted {row.path}")
                except OSError as error:
                    outcome.code = max(outcome.code, 1)
                    outcome.err.append(f"could not delete {row.path}: {error}")
        for row in review:
            if "MISSING_DIR" not in row.reasons or row.locked:
                outcome.err.append(f"left for review: {row.path} ({','.join(row.reasons) or 'changed'})")
        if review:
            outcome.code = max(outcome.code, 1)
        return outcome

    def _remove(self, row: TreeRow, outcome: Outcome, review: list[TreeRow]) -> None:
        head = self.repo.out(["rev-parse", "HEAD"], row.path)
        if head != row.head:
            outcome.err.append(f"kept {row.path}: it moved since planning")
            review.append(row)
            return
        removed = self.trees.remove(row.path)
        outcome.out += removed.out
        outcome.err += removed.err
        if removed.code:
            review.append(row)

    def _drop_registration(self, row: TreeRow, apply: bool, outcome: Outcome) -> None:
        tree = Tree(path=row.path, branch=row.branch, head=row.head, locked=row.locked)
        admin = self._admin_dir(tree)
        safe = (admin is not None and not row.locked and row.branch is not None and row.head is not None
                and self.repo.ok(["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{row.branch}"])
                and self.repo.is_ancestor(row.head, f"refs/remotes/origin/{row.branch}"))
        if not safe:
            reason = "locked" if row.locked else "its branch tip is not on its origin branch"
            outcome.err.append(f"kept registration of {row.path}: {reason}")
            return
        if not apply:
            outcome.out.append(f"would drop the registration of missing {row.path}")
            return
        name = posixpath.basename(admin)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(self.clock.now()))
        backup = posixpath.join(self.config.state_dir, "backups", f"{name}-{stamp}.tar")
        try:
            self.files.archive(admin, backup, name)
        except OSError as error:
            outcome.code = max(outcome.code, 1)
            outcome.err.append(f"kept registration of {row.path}: the backup failed: {error}")
            return
        try:
            self.files.remove_tree(admin)
        except OSError as error:
            outcome.code = max(outcome.code, 1)
            outcome.err.append(f"kept registration of {row.path}: could not delete it: {error}")
            return
        outcome.out.append(f"dropped the registration of missing {row.path}; backup {backup}")

    # Scratch

    def scratch_new(self, label: str, owner: str | None) -> Outcome:
        outcome = Outcome()
        if not LABEL.fullmatch(label or ""):
            outcome.err.append(f"BAD_LABEL: {label!r} must match {LABEL.pattern}")
            outcome.code = 2
            return outcome
        now = self.clock.now()
        path = posixpath.join(self.config.scratch_root, f"{label}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))}")
        if self.files.exists(path):
            return outcome.refuse(Refusal("SCRATCH_EXISTS", f"{path} already exists"))
        self.files.make_dirs(path)
        self.files.write_text(posixpath.join(path, SCRATCH_MARK),
                              json.dumps({"owner": owner, "created": now, "purpose": label}, indent=2) + "\n")
        outcome.out.append(path)
        return outcome

    def scratch_rm(self, target: str) -> Outcome:
        outcome = Outcome()
        path = posixpath.normpath(target)
        root = self.files.real_path(self.config.scratch_root)
        if (self.files.is_symlink(path) or self.files.real_path(posixpath.dirname(path)) != root
                or self._mark(path) is None):
            return outcome.refuse(Refusal("NOT_SCRATCH", f"{path} is not a marked directory directly under {root}"))
        users = self._users(self.files.real_path(path), self.processes.processes())
        if users:
            return outcome.refuse(Refusal("IN_USE", f"{path} is in use by " + ", ".join(str(p.pid) for p in users)))
        self.files.remove_tree(path)
        outcome.out.append(f"deleted {path}")
        return outcome

    def scratch_ls(self) -> Outcome:
        outcome = Outcome()
        table = self.processes.processes()
        for path in self._entries(self.config.scratch_root):
            row = self._dir_row(path, "scratch", table)
            outcome.out.append(f"{path}  {_human(row.size)}  {_age(row.age_s)}  marked {'yes' if row.marked else 'no'}"
                               + ("  expired" if row.expired else ""))
        return outcome


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "age ?"
    if seconds >= DAY:
        return f"{seconds / DAY:.1f} d"
    return f"{seconds / HOUR:.1f} h"


def parse_roots(values: Any, home: str, key: str) -> list[str]:
    """Absolute directory entries of a list setting; ``~/`` expands with ``home``."""
    roots = []
    for entry in values or []:
        if not isinstance(entry, str) or not (entry.startswith("/") or entry.startswith("~/")):
            raise ValueError(f"{key}: entry {entry!r} must be an absolute path or start with ~/")
        roots.append(posixpath.normpath(home + entry[1:] if entry.startswith("~/") else entry))
    return roots


def owner_from(env: Mapping[str, str]) -> str | None:
    return env.get("XORCHESTRATE_DEV_OWNER") or None
