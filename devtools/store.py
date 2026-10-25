"""SQLite state access, schema and durable session records."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

REQUEST_WORK = ContextVar("request_work", default=None)
UNSET = object()
SCHEMA_VERSION = 18


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    """Fingerprint canonical state with the same bytes across service boundaries."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("Use a member name of 1–64 letters, digits, underscores or hyphens.")
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
 name TEXT PRIMARY KEY, harness TEXT NOT NULL, workspace TEXT NOT NULL,
 native_id TEXT, state TEXT NOT NULL DEFAULT 'stopped', desired TEXT NOT NULL DEFAULT 'stopped',
 pid INTEGER, generation TEXT, updated TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY, sender TEXT NOT NULL REFERENCES members(name),
 recipient TEXT NOT NULL REFERENCES members(name), body TEXT NOT NULL,
 reply_to INTEGER REFERENCES messages(id), request_key TEXT, created TEXT NOT NULL,
 delivery TEXT NOT NULL DEFAULT 'queued', attempt TEXT, submitted TEXT, acknowledged TEXT,
 error TEXT, UNIQUE(sender, request_key));
CREATE INDEX IF NOT EXISTS message_inbox ON messages(recipient,id);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
 kind TEXT NOT NULL, data TEXT NOT NULL);
"""

WORK_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS work_orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, objective TEXT NOT NULL,
        done_when TEXT NOT NULL, constraints TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open',
        next_action TEXT NOT NULL DEFAULT '', blocker TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL REFERENCES members(name), created TEXT NOT NULL, updated TEXT NOT NULL,
        request_key TEXT, request_payload TEXT NOT NULL, UNIQUE(created_by,request_key))""",
    """CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order INTEGER NOT NULL REFERENCES work_orders(id),
        title TEXT NOT NULL, objective TEXT NOT NULL, done_when TEXT NOT NULL, constraints TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'open', next_action TEXT NOT NULL DEFAULT '', blocker TEXT NOT NULL DEFAULT '',
        repository TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL DEFAULT '', pull_request TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL REFERENCES members(name), created TEXT NOT NULL, updated TEXT NOT NULL,
        request_key TEXT, request_payload TEXT NOT NULL, UNIQUE(created_by,request_key))""",
    """CREATE TABLE IF NOT EXISTS artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, media_type TEXT NOT NULL,
        size INTEGER NOT NULL, sha256 TEXT NOT NULL, location TEXT NOT NULL,
        created_by TEXT NOT NULL REFERENCES members(name), created TEXT NOT NULL,
        UNIQUE(created_by,name,media_type,sha256))""",
    """CREATE TABLE IF NOT EXISTS work_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, work_order INTEGER NOT NULL REFERENCES work_orders(id),
        job INTEGER REFERENCES jobs(id),
        kind TEXT NOT NULL, reference TEXT NOT NULL, repository TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL REFERENCES members(name), created TEXT NOT NULL,
        artifact INTEGER REFERENCES artifacts(id))""",
    "CREATE UNIQUE INDEX IF NOT EXISTS work_link_identity ON work_links(work_order,coalesce(job,0),kind,reference,repository)",
)


class State:
    """State location and SQLite lifetime, read-only unless explicitly writable.

    Opening a view never creates or upgrades a team's schema. Runtime services
    use Store, which owns initialization and migration in addition to this access.
    """

    def __init__(self, directory, *, read_only=True):
        self.directory = Path(directory).expanduser().resolve()
        self.path = self.directory / "live.sqlite3"
        self.read_only = read_only

    @contextmanager
    def connect(self):
        target = self.path.as_uri() + "?mode=ro" if self.read_only else self.path
        connection = sqlite3.connect(target, timeout=10, uri=self.read_only)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()


class Store(State):
    def __init__(self, directory):
        super().__init__(directory, read_only=False)
        if self.path.is_file():
            self.upgrade()

    def upgrade(self):
        """Upgrade retained state atomically without rebuilding its records."""
        with self.connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
                return
            db.execute("BEGIN IMMEDIATE")
            for statement in WORK_SCHEMA:
                db.execute(statement)
            for table, column, target in (
                ("members", "selected_work_order", "work_orders"),
                ("members", "selected_job", "jobs"),
                ("messages", "work_order", "work_orders"),
                ("messages", "job", "jobs"),
                ("events", "work_order", "work_orders"),
                ("events", "job", "jobs"),
            ):
                columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} INTEGER REFERENCES {target}(id)")
            for table in ("events", "messages"):
                db.execute(f"CREATE INDEX IF NOT EXISTS {table}_work ON {table}(work_order,job,id)")
            db.execute("""CREATE TABLE IF NOT EXISTS artifact_sources (
                artifact INTEGER NOT NULL REFERENCES artifacts(id), provider TEXT NOT NULL,
                reference TEXT NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(artifact,provider,reference))""")
            db.execute("""CREATE TABLE IF NOT EXISTS github_watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT, member TEXT NOT NULL REFERENCES members(name),
                work_order INTEGER REFERENCES work_orders(id), job INTEGER REFERENCES jobs(id),
                repository TEXT NOT NULL, pr INTEGER, run INTEGER, revision TEXT NOT NULL,
                expires REAL NOT NULL, next_poll REAL NOT NULL, state TEXT NOT NULL DEFAULT 'active',
                signature TEXT NOT NULL DEFAULT '', poll_pid INTEGER, updated TEXT NOT NULL)""")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS github_watch_identity "
                "ON github_watches(member,repository,coalesce(pr,0),coalesce(run,0),revision)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS member_bindings (
                member TEXT PRIMARY KEY REFERENCES members(name), role TEXT NOT NULL,
                work_order INTEGER NOT NULL REFERENCES work_orders(id), job INTEGER REFERENCES jobs(id),
                reports_to TEXT REFERENCES members(name), brief TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL, updated TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS standby_bindings (
                member TEXT PRIMARY KEY REFERENCES members(name), role TEXT NOT NULL,
                reports_to TEXT NOT NULL UNIQUE REFERENCES members(name), revision INTEGER NOT NULL,
                launch_pending INTEGER NOT NULL DEFAULT 1, launch_error TEXT,
                preflight TEXT NOT NULL DEFAULT '{}', deadline REAL NOT NULL,
                created TEXT NOT NULL, updated TEXT NOT NULL)""")
            if "runtime" in {row[1] for row in db.execute("PRAGMA table_info(standby_bindings)")}:
                db.execute("ALTER TABLE standby_bindings DROP COLUMN runtime")
            db.execute("""CREATE TABLE IF NOT EXISTS duty_readiness (
                id INTEGER PRIMARY KEY AUTOINCREMENT, member TEXT NOT NULL REFERENCES members(name),
                parent TEXT REFERENCES members(name), role TEXT NOT NULL, generation TEXT NOT NULL,
                binding TEXT NOT NULL, challenge TEXT NOT NULL UNIQUE, deadline REAL NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('starting','ready','blocked','timed_out','superseded')),
                checks TEXT NOT NULL DEFAULT '[]', onboarding_revision TEXT,
                challenge_message INTEGER REFERENCES messages(id), receipt_message INTEGER REFERENCES messages(id),
                created TEXT NOT NULL, updated TEXT NOT NULL)""")
            db.execute("CREATE INDEX IF NOT EXISTS duty_readiness_member ON duty_readiness(member,id)")
            db.execute("CREATE INDEX IF NOT EXISTS duty_readiness_parent ON duty_readiness(parent,id)")
            db.execute("""CREATE TABLE IF NOT EXISTS work_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL REFERENCES members(name),
                kind TEXT NOT NULL, text TEXT NOT NULL, work_order INTEGER NOT NULL REFERENCES work_orders(id),
                job INTEGER REFERENCES jobs(id), binding_revision INTEGER NOT NULL, request_key TEXT NOT NULL,
                artifact INTEGER REFERENCES artifacts(id), message INTEGER REFERENCES messages(id), created TEXT NOT NULL,
                UNIQUE(actor,request_key))""")
            db.execute("CREATE INDEX IF NOT EXISTS work_updates_scope ON work_updates(work_order,job,id)")
            additions = {
                "members": {"binding_revision": "INTEGER NOT NULL DEFAULT 0"},
                "work_updates": {"evaluation": "TEXT", "binding": "TEXT", "mission": "TEXT", "capability_gap": "TEXT"},
                "jobs": {"assignment": "TEXT NOT NULL DEFAULT '{}'", "dependencies": "TEXT NOT NULL DEFAULT '[]'"},
                "github_watches": {"reviews": "INTEGER NOT NULL DEFAULT 0", "sequence": "INTEGER NOT NULL DEFAULT 0"},
                "member_bindings": {
                    "paused": "INTEGER NOT NULL DEFAULT 0",
                    "launch_pending": "INTEGER NOT NULL DEFAULT 0",
                    "launch_error": "TEXT",
                    "assignment_id": "INTEGER",
                },
                "messages": {"category": "TEXT NOT NULL DEFAULT 'peer'"},
                "work_orders": {
                    "repository": "TEXT NOT NULL DEFAULT ''",
                    "branch": "TEXT NOT NULL DEFAULT ''",
                    "pull_request": "TEXT NOT NULL DEFAULT ''",
                    "context": "TEXT NOT NULL DEFAULT '[]'",
                    "plan_revision": "INTEGER NOT NULL DEFAULT 0",
                },
            }
            for table, fields in additions.items():
                present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
                for name, definition in fields.items():
                    if name not in present:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            db.execute("""CREATE TABLE IF NOT EXISTS capability_gaps (
                id INTEGER PRIMARY KEY AUTOINCREMENT, repository TEXT NOT NULL, identity TEXT NOT NULL UNIQUE,
                capability TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open','reviewing','deferred','resolved')),
                classification TEXT NOT NULL DEFAULT 'unknown'
                    CHECK(classification IN ('missing_tool','discovery','permission','tool_failure','unknown')),
                owner TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
                occurrence_count INTEGER NOT NULL DEFAULT 1, capture TEXT NOT NULL,
                created TEXT NOT NULL, updated TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS capability_gap_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT, gap INTEGER NOT NULL REFERENCES capability_gaps(id),
                update_id INTEGER NOT NULL UNIQUE REFERENCES work_updates(id),
                actor TEXT NOT NULL REFERENCES members(name), binding_key TEXT NOT NULL,
                occurrence_key TEXT NOT NULL, UNIQUE(actor,binding_key,occurrence_key))""")
            db.execute("CREATE INDEX IF NOT EXISTS capability_gap_occurrences_gap ON capability_gap_occurrences(gap,id)")
            db.execute("""CREATE TABLE IF NOT EXISTS capability_gap_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT, gap INTEGER NOT NULL REFERENCES capability_gaps(id),
                revision INTEGER NOT NULL, actor TEXT NOT NULL CHECK(actor='operator'),
                prior_status TEXT NOT NULL, status TEXT NOT NULL, classification TEXT NOT NULL,
                owner TEXT NOT NULL, note TEXT NOT NULL, evidence TEXT, capture TEXT NOT NULL,
                created TEXT NOT NULL, UNIQUE(gap,revision))""")
            db.execute("CREATE INDEX IF NOT EXISTS capability_gap_reviews_gap ON capability_gap_reviews(gap,id)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS job_workspaces (job INTEGER PRIMARY KEY REFERENCES jobs(id), "
                "root TEXT NOT NULL UNIQUE,branch TEXT NOT NULL,target TEXT NOT NULL,base_revision TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints (id INTEGER PRIMARY KEY,job INTEGER NOT NULL REFERENCES jobs(id), "
                "revision TEXT NOT NULL,note TEXT NOT NULL,created TEXT NOT NULL,UNIQUE(job,revision))"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT, member TEXT NOT NULL REFERENCES members(name),
                work_order INTEGER NOT NULL REFERENCES work_orders(id), job INTEGER NOT NULL REFERENCES jobs(id),
                binding TEXT NOT NULL, action TEXT NOT NULL, arguments TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL)""")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS active_job_operation ON operations(job) WHERE state IN ('queued','running')"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS review_views (
                member TEXT NOT NULL REFERENCES members(name), job INTEGER NOT NULL REFERENCES jobs(id),
                root TEXT NOT NULL, revision TEXT NOT NULL, base_revision TEXT NOT NULL,
                PRIMARY KEY(member,job))""")
            db.execute("""CREATE TABLE IF NOT EXISTS inspections (
                member TEXT NOT NULL REFERENCES members(name), job INTEGER NOT NULL REFERENCES jobs(id),
                revision TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(member,job))""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT, job INTEGER NOT NULL REFERENCES jobs(id),
                actor TEXT NOT NULL REFERENCES members(name), revision TEXT NOT NULL,
                conclusion TEXT NOT NULL, summary TEXT NOT NULL, response_to INTEGER REFERENCES reviews(id),
                request_key TEXT NOT NULL UNIQUE, state TEXT NOT NULL, remote_url TEXT,
                created TEXT NOT NULL, updated TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
                job INTEGER PRIMARY KEY REFERENCES jobs(id), accepted_by TEXT NOT NULL REFERENCES members(name),
                revision TEXT, merge_commit TEXT, assessment TEXT NOT NULL, outcome TEXT NOT NULL, created TEXT NOT NULL)""")
            if "result_update" not in {row["name"] for row in db.execute("PRAGMA table_info(deliveries)")}:
                db.execute("ALTER TABLE deliveries ADD COLUMN result_update INTEGER REFERENCES work_updates(id)")
            db.execute("""CREATE TABLE IF NOT EXISTS order_workspaces (
                work_order INTEGER PRIMARY KEY REFERENCES work_orders(id), root TEXT NOT NULL UNIQUE,
                branch TEXT NOT NULL, target TEXT NOT NULL, base_revision TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS workspace_merges (
                job INTEGER PRIMARY KEY REFERENCES jobs(id), head TEXT NOT NULL,
                revision TEXT NOT NULL, entries TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS order_inspections (
                member TEXT NOT NULL REFERENCES members(name), work_order INTEGER NOT NULL REFERENCES work_orders(id),
                revision TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(member,work_order))""")
            db.execute("""CREATE TABLE IF NOT EXISTS order_deliveries (
                work_order INTEGER PRIMARY KEY REFERENCES work_orders(id), revision TEXT, merge_commit TEXT,
                assessment TEXT NOT NULL, created TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS managed_worktrees (
                root TEXT PRIMARY KEY, work_order INTEGER NOT NULL REFERENCES work_orders(id),
                job INTEGER REFERENCES jobs(id), branch TEXT, revision TEXT,
                state TEXT NOT NULL DEFAULT 'active', sessions TEXT NOT NULL DEFAULT '{}',
                provider INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
                retry_after REAL NOT NULL DEFAULT 0, updated TEXT NOT NULL)""")
            db.execute(
                "INSERT OR IGNORE INTO managed_worktrees(root,work_order,job,branch,updated) "
                "SELECT w.root,j.work_order,w.job,w.branch,? FROM job_workspaces w JOIN jobs j ON j.id=w.job",
                (stamp(),),
            )
            db.execute(
                "INSERT OR IGNORE INTO managed_worktrees(root,work_order,job,revision,updated) "
                "SELECT v.root,j.work_order,v.job,v.revision,? FROM review_views v JOIN jobs j ON j.id=v.job",
                (stamp(),),
            )
            if next(r for r in db.execute("PRAGMA table_info(operations)") if r["name"] == "job")["notnull"]:
                db.execute("ALTER TABLE operations RENAME TO previous_operations")
                db.execute("""CREATE TABLE operations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, member TEXT NOT NULL REFERENCES members(name),
                    work_order INTEGER NOT NULL REFERENCES work_orders(id), job INTEGER REFERENCES jobs(id),
                    binding TEXT NOT NULL, action TEXT NOT NULL, arguments TEXT NOT NULL,
                    state TEXT NOT NULL, result TEXT, error TEXT, created TEXT NOT NULL, updated TEXT NOT NULL)""")
                db.execute("INSERT INTO operations SELECT * FROM previous_operations")
                db.execute("DROP TABLE previous_operations")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS active_job_operation ON operations(job) WHERE state IN ('queued','running')"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS active_order_operation ON operations(work_order) "
                "WHERE job IS NULL AND state IN ('queued','running')"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS codex_requests (
                repository TEXT NOT NULL, pr INTEGER NOT NULL, revision TEXT NOT NULL, kind TEXT NOT NULL,
                state TEXT NOT NULL, url TEXT, PRIMARY KEY(repository,pr,revision,kind))""")
            db.execute("""CREATE TABLE IF NOT EXISTS role_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, work_order INTEGER NOT NULL REFERENCES work_orders(id),
                job INTEGER REFERENCES jobs(id), name TEXT NOT NULL, role TEXT NOT NULL,
                brief TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active',
                revision INTEGER NOT NULL DEFAULT 1, result_update INTEGER REFERENCES work_updates(id),
                created_by TEXT NOT NULL REFERENCES members(name), created TEXT NOT NULL, updated TEXT NOT NULL)""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS assignment_identity "
                       "ON role_assignments(work_order,coalesce(job,0),name COLLATE NOCASE,role)")
            db.execute("""CREATE TABLE IF NOT EXISTS runtime_selections (
                member TEXT PRIMARY KEY REFERENCES members(name), binding TEXT NOT NULL,
                profile TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL,
                member TEXT NOT NULL REFERENCES members(name), work_order INTEGER NOT NULL REFERENCES work_orders(id),
                job INTEGER REFERENCES jobs(id), purpose TEXT NOT NULL, binding TEXT NOT NULL,
                context TEXT NOT NULL, identity TEXT, policy TEXT NOT NULL, request TEXT NOT NULL,
                answers TEXT NOT NULL DEFAULT '{}', result TEXT NOT NULL DEFAULT '{}',
                unresolved TEXT NOT NULL DEFAULT '[]', evidence TEXT, failure TEXT,
                state TEXT NOT NULL, assignment INTEGER REFERENCES role_assignments(id),
                arbitrator TEXT REFERENCES members(name), resolution TEXT, continuation TEXT,
                resumed TEXT, created TEXT NOT NULL, updated TEXT NOT NULL)""")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS current_decision ON decisions(fingerprint) WHERE state!='stale'")
            from .role_packages import names_for_adapter

            invalid = db.execute(
                "SELECT member FROM member_bindings WHERE job IS NULL AND role IN "
                "(SELECT value FROM json_each(?)) LIMIT 1", (canonical(names_for_adapter("nco")),),
            ).fetchone()
            if invalid:
                raise ValueError("Rebind or retire the job coordinator without a job before upgrading this team: " + invalid["member"])
            duplicate = db.execute(
                "SELECT job FROM member_bindings WHERE job IS NOT NULL AND role IN "
                "(SELECT value FROM json_each(?)) GROUP BY job HAVING count(*)>1 LIMIT 1",
                (canonical(names_for_adapter("nco")),),
            ).fetchone()
            if duplicate:
                raise ValueError("Resolve multiple coordinator bindings for job " + str(duplicate["job"]) + " before upgrading this team.")
            # Coordinator membership comes from the role registry. Bindings are
            # checked in the same immediate transaction as their insertion.
            db.execute("DROP INDEX IF EXISTS job_nco")
            db.execute("""CREATE TABLE IF NOT EXISTS work_plans (
                work_order INTEGER NOT NULL REFERENCES work_orders(id), revision INTEGER NOT NULL,
                result_update INTEGER NOT NULL REFERENCES work_updates(id), jobs TEXT NOT NULL,
                mission TEXT NOT NULL, adopted_by TEXT NOT NULL REFERENCES members(name), created TEXT NOT NULL,
                PRIMARY KEY(work_order,revision))""")
            # Retire every retained incarnation, including unbound members, but
            # preserve live snapshots and the validity of their current proof.
            for table, owner, revision in (
                ("member_bindings", "member", "revision"),
                ("work_updates", "actor", "binding_revision"),
                ("operations", "member", "json_extract(binding,'$.revision')"),
                ("decisions", "member", "json_extract(binding,'$.revision')"),
            ):
                db.execute(
                    "UPDATE members SET binding_revision=max(binding_revision,coalesce((SELECT max(" + revision + ") "
                    "FROM " + table + " WHERE " + owner + "=members.name),0))"
                )
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def initialize(self, members, *, runtime=False):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            for member in members:
                name = identifier(member["name"])
                root = Path(member["workspace"]).expanduser().resolve(strict=True)
                if self.directory == root or self.directory.is_relative_to(root):
                    raise ValueError("Keep live state outside the managed source worktree.")
                old = db.execute("SELECT * FROM members WHERE name=?", (name,)).fetchone()
                managed_runtime = runtime and member["harness"] != "external" and old and old["harness"] != "external"
                if old and ((old["harness"] != member["harness"] and not managed_runtime) or old["workspace"] != str(root)):
                    raise ValueError(f"Member {name} already has a different harness or worktree.")
                db.execute(
                    "INSERT OR IGNORE INTO members(name,harness,workspace,updated) VALUES (?,?,?,?)",
                    (name, member["harness"], str(root), stamp()),
                )
        self.path.chmod(0o600)
        self.upgrade()

    @staticmethod
    def next_binding_revision(db, member):
        """Allocate an incarnation inside the binding change's transaction."""
        changed = db.execute(
            "UPDATE members SET binding_revision=max(binding_revision,coalesce((SELECT revision "
            "FROM member_bindings WHERE member=members.name),0))+1 WHERE name=?",
            (member,),
        ).rowcount
        if not changed:
            raise ValueError("Unknown member")
        return db.execute("SELECT binding_revision FROM members WHERE name=?", (member,)).fetchone()[0]

    @staticmethod
    def current_work(db, actor, work_order=UNSET, job=UNSET):
        context = REQUEST_WORK.get()
        if context and context[0] == actor:
            selected = context[1:]
        else:
            row = db.execute("SELECT selected_work_order,selected_job FROM members WHERE name=?", (actor,)).fetchone()
            selected = tuple(row) if row else (None, None)
        if job is not UNSET and job is not None:
            row = db.execute("SELECT work_order FROM jobs WHERE id=?", (job,)).fetchone()
            if not row:
                raise ValueError(f"Unknown job: {job}")
            if work_order is not UNSET and work_order != row[0]:
                raise ValueError("That job belongs to a different work order.")
            work_order = row[0]
        else:
            if job is UNSET:
                job = selected[1] if work_order is UNSET else None
            if work_order is UNSET:
                work_order = selected[0]
        if work_order is not None and not db.execute("SELECT 1 FROM work_orders WHERE id=?", (work_order,)).fetchone():
            raise ValueError(f"Unknown work order: {work_order}")
        return work_order, job

    @contextmanager
    def work_context(self, actor, work_order=UNSET, job=UNSET):
        with self.connect() as db:
            work_order, job = self.current_work(db, actor, work_order, job)
        token = REQUEST_WORK.set((actor, work_order, job))
        try:
            yield work_order, job
        finally:
            REQUEST_WORK.reset(token)

    def _event(self, db, actor, kind, data):
        from .telemetry import clean_for_state

        data = dict(data)
        work_order, job = Store.current_work(db, actor, data.pop("work_order", UNSET), data.pop("job", UNSET))
        member = db.execute("SELECT generation,native_id FROM members WHERE name=?", (actor,)).fetchone()
        if member:
            for field in ("generation", "native_id"):
                if member[field] is not None:
                    data.setdefault(field, member[field])
        return db.execute(
            "INSERT INTO events(at,actor,kind,data,work_order,job) VALUES (?,?,?,?,?,?)",
            (stamp(), actor, kind, canonical(clean_for_state(self.directory, data)), work_order, job),
        ).lastrowid

    def event(self, actor, kind, **data):
        with self.connect() as db:
            return self._event(db, actor, kind, data)

    def member(self, name):
        with self.connect() as db:
            row = db.execute("SELECT * FROM members WHERE name=?", (name,)).fetchone()
        if not row:
            raise ValueError(f"Unknown member: {name}")
        result = dict(row)
        result["detail"] = json.loads(result["detail"])
        return result

    def members(self):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM members ORDER BY name")]
        for row in rows:
            row["detail"] = json.loads(row["detail"])
        return rows

    def session(self, name, *, generation=None, **fields):
        allowed = {"native_id", "state", "desired", "pid", "detail"}
        if not fields or set(fields) - allowed:
            raise ValueError("Unknown session update")
        if "detail" in fields:
            fields["detail"] = canonical(fields["detail"])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation FROM members WHERE name=?", (name,)).fetchone()
            if not row:
                raise ValueError("Unknown member")
            if generation is not None and row["generation"] != generation:
                raise ValueError("This session generation has been superseded.")
            fields["updated"] = stamp()
            db.execute(
                "UPDATE members SET " + ",".join(k + "=?" for k in fields) + " WHERE name=?", (*fields.values(), name)
            )
            self._event(db, name, "session", {k: v for k, v in fields.items() if k != "updated"})

    def begin_session(self, name, pid, *, binding_revision=None, standby_revision=None, expected_binding=UNSET, settings=None):
        from .sessions import lock

        generation = uuid.uuid4().hex
        with lock(self, name, exclusive=True), self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM members WHERE name=?", (name,)).fetchone()
            if not row:
                raise ValueError("Unknown member")
            if row["state"] in {"starting", "active", "idle"} and row["pid"]:
                try:
                    import os

                    os.kill(row["pid"], 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError("This member already has a running session.")
            if expected_binding is not UNSET:
                scope = db.execute("SELECT * FROM member_bindings WHERE member=?", (name,)).fetchone()
                current_binding = dict(scope) if scope else None
                if current_binding != expected_binding:
                    raise ValueError("Work binding changed before the session started.")
            if binding_revision is not None:
                scope = db.execute("SELECT * FROM member_bindings WHERE member=?", (name,)).fetchone()
                if not scope or scope["revision"] != binding_revision or scope["paused"] or not scope["launch_pending"]:
                    raise ValueError("Launch direction was superseded before the session started.")
                db.execute("UPDATE member_bindings SET launch_pending=0,launch_error=NULL WHERE member=?", (name,))
            if standby_revision is not None:
                from .standby import authorized
                from .standby import binding as standby_binding

                scope = standby_binding(db, name)
                if (binding_revision is not None or not scope or scope["revision"] != standby_revision
                        or not scope["launch_pending"] or time.time() >= scope["deadline"]
                        or not authorized(self, scope, db=db)
                        or db.execute("SELECT 1 FROM member_bindings WHERE member=?", (name,)).fetchone()):
                    raise ValueError("Standby launch was superseded before the session started.")
                db.execute("UPDATE standby_bindings SET launch_pending=0,launch_error=NULL WHERE member=?", (name,))
            db.execute(
                "UPDATE members SET generation=?,native_id=NULL,pid=?,state='starting',desired='running',updated=? WHERE name=?",
                (generation, pid, stamp(), name),
            )
            # A crashed transport may have delivered before recording success. Preserve uncertainty.
            db.execute(
                "UPDATE messages SET delivery='uncertain',error='Transport stopped before recording delivery' "
                "WHERE recipient=? AND delivery='delivering'",
                (name,),
            )
            self._event(db, name, "session_starting", {"generation": generation, "pid": pid, "settings": settings})
            if row["harness"] != "external":
                from .duty import begin_in

                begin_in(self, db, name, generation)
        return generation

    def send(self, sender, recipient, body, *, key, reply_to=None, work_order=UNSET, job=UNSET):
        self.member(sender)
        self.member(recipient)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._send(db, sender, recipient, body, key=key, reply_to=reply_to, work_order=work_order, job=job)

    def _send(self, db, sender, recipient, body, *, key, reply_to=None, work_order=UNSET, job=UNSET):
        """Reuse the message service inside a caller's transaction."""
        old = db.execute("SELECT * FROM messages WHERE sender=? AND request_key=?", (sender, key)).fetchone()
        values = (recipient, body, reply_to)
        if old:
            if tuple(old[k] for k in ("recipient", "body", "reply_to")) != values or (
                job is not UNSET and job != old["job"] or work_order is not UNSET and work_order != old["work_order"]
            ):
                raise ValueError("That message key already identifies a different payload.")
            return dict(old)
        reply = None
        if reply_to:
            reply = db.execute(
                "SELECT * FROM messages WHERE id=? AND (sender=? OR recipient=?)", (reply_to, sender, sender)
            ).fetchone()
        if reply_to and not reply:
            raise ValueError("Reply to a message involving this member.")
        if reply and work_order is UNSET and job is UNSET:
            work_order, job = reply["work_order"], reply["job"]
        work_order, job = self.current_work(db, sender, work_order, job)
        ident = db.execute(
            "INSERT INTO messages(sender,recipient,body,reply_to,request_key,created,work_order,job) VALUES (?,?,?,?,?,?,?,?)",
            (sender, *values, key, stamp(), work_order, job),
        ).lastrowid
        self._event(
            db,
            sender,
            "message_queued",
            {"message": ident, "recipient": recipient, "work_order": work_order, "job": job},
        )
        return dict(db.execute("SELECT * FROM messages WHERE id=?", (ident,)).fetchone())

    def inbox(self, member, after=0, limit=20, unacknowledged=False, work_order=None, job=None):
        with self.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM messages WHERE recipient=? AND id>?"
                    + (" AND acknowledged IS NULL" if unacknowledged else "")
                    + (" AND work_order=?" if work_order is not None else "")
                    + (" AND job=?" if job is not None else "")
                    + " ORDER BY id LIMIT ?",
                    (
                        member,
                        after,
                        *((work_order,) if work_order is not None else ()),
                        *((job,) if job is not None else ()),
                        limit + 1,
                    ),
                )
            ]
        more = len(rows) > limit
        rows = rows[:limit]
        return {"messages": rows, "next_after": rows[-1]["id"] if more else None}

    def acknowledge(self, member, message):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._acknowledge(db, member, message)

    def _acknowledge(self, db, member, message):
        row = db.execute("SELECT * FROM messages WHERE id=? AND recipient=?", (message, member)).fetchone()
        if not row:
            raise ValueError("Only the recipient can acknowledge this message.")
        if not row["acknowledged"]:
            db.execute("UPDATE messages SET acknowledged=? WHERE id=?", (stamp(), message))
            self._event(
                db,
                member,
                "message_acknowledged",
                {"message": message, "work_order": row["work_order"], "job": row["job"]},
            )
        return {"message": message, "acknowledged": True}

    def take_delivery(self, member, *, scoped=False, generation=UNSET):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if generation is not UNSET:
                current = db.execute("SELECT generation,desired FROM members WHERE name=?", (member,)).fetchone()
                if not current or current["generation"] != generation or current["desired"] != "running":
                    return None
            clause, args = "1", ()
            if scoped:
                from .control import binding, message_sql

                clause, args = message_sql(self, db, member, binding(db, member))
            row = db.execute(
                "SELECT * FROM messages WHERE recipient=? AND delivery='queued' AND acknowledged IS NULL AND "
                + "(" + clause + ")"
                + " ORDER BY id LIMIT 1",
                (member, *args),
            ).fetchone()
            if not row:
                return None
            attempt = uuid.uuid4().hex
            db.execute("UPDATE messages SET delivery='delivering',attempt=? WHERE id=?", (attempt, row["id"]))
            result = dict(row)
            result["attempt"] = attempt
            self._event(db, member, "message_delivering", {
                "message": row["id"], "attempt": attempt, "sender": row["sender"],
                "body": row["body"], "work_order": row["work_order"], "job": row["job"],
            })
            return result

    def delivered(self, member, message, attempt, *, error=None, transport=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE messages SET delivery=?,submitted=?,error=? "
                "WHERE id=? AND recipient=? AND attempt=? AND delivery='delivering'",
                (
                    "uncertain" if error else "submitted",
                    stamp() if not error else None,
                    error,
                    message,
                    member,
                    attempt,
                ),
            ).rowcount
            if not changed:
                raise ValueError("Delivery attempt no longer current.")
            binding = db.execute("SELECT work_order,job FROM messages WHERE id=?", (message,)).fetchone()
            self._event(
                db,
                member,
                "delivery_uncertain" if error else "message_submitted",
                {"message": message, "attempt": attempt, "transport": transport, "error": error, **dict(binding)},
            )

    def retry(self, member, message):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM messages WHERE id=? AND sender=?", (message, member)).fetchone()
            if not row or row["acknowledged"] or row["delivery"] != "uncertain":
                raise ValueError("Only the sender can explicitly retry an uncertain, unacknowledged delivery.")
            db.execute("UPDATE messages SET delivery='queued',attempt=NULL,error=NULL WHERE id=?", (message,))
            self._event(
                db, member, "message_retry", {"message": message, "work_order": row["work_order"], "job": row["job"]}
            )
        return {"message": message, "delivery": "queued"}

    def events(self, after=0, limit=30, actor=None, kind=None, work_order=None, job=None):
        sql, args = "SELECT * FROM events WHERE id>?", [after]
        for key, value in (("actor", actor), ("kind", kind), ("work_order", work_order), ("job", job)):
            if value:
                sql += f" AND {key}=?"
                args.append(value)
        with self.connect() as db:
            rows = [dict(r) for r in db.execute(sql + " ORDER BY id LIMIT ?", (*args, limit + 1))]
        for row in rows:
            row["data"] = json.loads(row["data"])
        return {"events": rows[:limit], "next_after": rows[limit - 1]["id"] if len(rows) > limit else None}

    def wait(self, member, after=0, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            result = self.inbox(member, after)
            if result["messages"] or time.monotonic() >= deadline:
                return result
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
