"""Deferred removal of recorded, merged worktrees; retained work and evidence stay in SQLite."""

from __future__ import annotations

import fcntl
import hashlib
import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from .role_packages import names_for_adapter
from .store import canonical, stamp
from .workspaces import Workspaces, git


def key(root):
    return hashlib.sha256(str(root).encode()).hexdigest()


@contextmanager
def lease(store, root, *, exclusive=False):
    directory = store.directory / "work-locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    with (directory / ("workspace-" + key(root) + ".lock")).open("a") as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A process is still using this workspace.") from None
        yield


def register(db, workspace, work_order):
    db.execute(
        "INSERT OR IGNORE INTO managed_worktrees(root,work_order,job,branch,revision,updated) VALUES (?,?,?,?,?,?)",
        (
            workspace["root"],
            work_order,
            workspace.get("job"),
            None if workspace.get("review_revision") else workspace["branch"],
            workspace.get("review_revision"),
            stamp(),
        ),
    )


def queue(db, work_order, job, revision):
    # CO stays connected through its stable interface checkout after delivery;
    # only execution conversations can hold this managed worktree open.
    sessions = {
        row["name"]: row["generation"]
        for row in db.execute(
            "SELECT m.name,m.generation FROM members m JOIN member_bindings b ON b.member=m.name "
            "WHERE b.work_order=? AND b.job IS ? AND b.role NOT IN (SELECT value FROM json_each(?))",
            (work_order, job, canonical(names_for_adapter("co"))),
        )
    }
    db.execute(
        "UPDATE managed_worktrees SET state='pending',revision=coalesce(revision,?),sessions=?,updated=? "
        "WHERE work_order=? AND job IS ? AND state='active'",
        (revision, canonical(sessions), stamp(), work_order, job),
    )


class Retirement:
    def __init__(self, store, member):
        self.store, self.member = store, member
        self.spaces = Workspaces(store, member)

    def tick(self):
        with self.store.connect() as db:
            rows = list(
                db.execute(
                    "SELECT * FROM managed_worktrees WHERE state='pending' AND retry_after<=? ORDER BY updated LIMIT 10",
                    (time.time(),),
                )
            )
        for row in rows:
            try:
                scope = {"job": row["job"], "work_order": row["work_order"]}
                with (
                    self.spaces.lock(self.spaces.scope_lock(scope)),
                    self.spaces.lock("repository"),
                    lease(self.store, row["root"], exclusive=True),
                ):
                    with self.store.connect() as db:
                        current = db.execute(
                            "SELECT state FROM managed_worktrees WHERE root=?", (row["root"],)
                        ).fetchone()
                    if current["state"] != "pending":
                        continue
                    self.remove(row)
                    self.result(row, "removed", "Worktree retired; work records and evidence retained.")
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                self.result(row, "pending", str(error)[:1000])

    def result(self, row, state, detail):
        with self.store.connect() as db:
            db.execute(
                "UPDATE managed_worktrees SET state=?,detail=?,retry_after=?,updated=? WHERE root=? AND state='pending'",
                (state, detail, time.time() + 30, stamp(), row["root"]),
            )
            if row["state"] != state or row["detail"] != detail:
                self.store._event(
                    db,
                    self.member,
                    "worktree_cleanup",
                    {
                        "work_order": row["work_order"],
                        "job": row["job"],
                        "state": state,
                        "detail": detail,
                    },
                )

    def remove(self, row):
        with self.store.connect() as db:
            table, column, ident = (
                ("deliveries", "job", row["job"])
                if row["job"]
                else ("order_deliveries", "work_order", row["work_order"])
            )
            delivery = db.execute(f"SELECT merge_commit FROM {table} WHERE {column}=?", (ident,)).fetchone()
            if not delivery or not delivery["merge_commit"]:
                raise ValueError("No retained merge result; workspace preserved.")
            if db.execute(
                "SELECT 1 FROM member_bindings WHERE work_order=? AND job IS ? AND role NOT IN (SELECT value FROM json_each(?))",
                (row["work_order"], row["job"], canonical(names_for_adapter("co")))
            ).fetchone():
                raise ValueError("Collaborators are still bound to this work.")
            if db.execute(
                "SELECT 1 FROM operations WHERE work_order=? AND job IS ? AND state IN ('queued','running')",
                (row["work_order"], row["job"]),
            ).fetchone():
                raise ValueError("A development operation is still active.")
            for member, generation in json.loads(row["sessions"]).items():
                session = db.execute("SELECT generation,pid FROM members WHERE name=?", (member,)).fetchone()
                if session["generation"] == generation and session["pid"]:
                    raise ValueError("Waiting for collaborator shutdown.")
        if row["provider"]:
            docker = self.spaces.config.get("serena", {}).get("docker", "docker")
            result = subprocess.run(
                [docker, "ps", "--all", "--quiet", "--filter", "label=agentscient.workspace=" + key(row["root"])],
                capture_output=True,
                timeout=10,
            )
            if result.returncode or result.stdout.strip():
                raise ValueError("Waiting for Serena container shutdown or an available Docker daemon.")
        checkout, root = self.spaces.checkout(), Path(row["root"])
        if (
            root == checkout
            or checkout.is_relative_to(root)
            or self.store.directory.is_relative_to(root)
            or root.is_symlink()
            or root.resolve() != root
        ):
            raise ValueError("Managed workspace path was replaced or is not isolated.")
        if root.exists():
            workspace = {
                "root": str(root),
                "branch": row["branch"],
                "review_revision": row["revision"] if not row["branch"] else None,
            }
            self.spaces._verify(workspace, checkout)
            if git(root, "rev-parse", "HEAD").stdout.decode().strip() != row["revision"]:
                raise ValueError("Workspace has commits beyond the accepted revision; preserved.")
        listing = git(checkout, "worktree", "list", "--porcelain").stdout.decode()
        registered = any(block.splitlines()[0] == "worktree " + str(root) for block in listing.strip().split("\n\n"))
        if registered:
            # Git refuses dirty, untracked, locked and submodule worktrees. Never force removal.
            git(checkout, "worktree", "remove", str(root))
        elif root.exists():
            raise ValueError("Path is no longer a registered worktree; preserved.")
        if (
            row["branch"]
            and "branch refs/heads/" + row["branch"]
            not in git(checkout, "worktree", "list", "--porcelain").stdout.decode().splitlines()
        ):
            ref = "refs/heads/" + row["branch"]
            actual = git(checkout, "rev-parse", "--verify", ref, check=False)
            if actual.returncode == 0:
                # Compare-and-delete: never discard a branch advanced outside this service.
                git(checkout, "update-ref", "-d", ref, row["revision"])
