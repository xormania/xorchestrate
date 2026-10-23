"""Work orders bind the work; jobs are the smaller pieces delivered through PRs."""

from __future__ import annotations

from .store import UNSET, canonical, stamp

STATUSES = ("open", "in_progress", "blocked", "done", "cancelled")
LINK_KINDS = ("repository", "branch", "worktree", "commit", "pull_request", "artifact", "url")
FIELDS = {"title", "objective", "done_when", "constraints", "status", "next_action", "blocker"}
JOB_FIELDS = {"repository", "branch", "pull_request"}


def record(db, table, ident):
    row = db.execute(f"SELECT * FROM {table} WHERE id=?", (ident,)).fetchone()
    if not row:
        raise ValueError("Choose an existing work order or job, or create one first.")
    result = dict(row)
    result.pop("request_key", None)
    result.pop("request_payload", None)
    return result


def artifact_record(db, artifact):
    row = db.execute(
        "SELECT id,name,media_type,size,created_by,created FROM artifacts WHERE id=?", (artifact,)
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown artifact: {artifact}")
    return dict(row)


class Work:
    def __init__(self, store, member):
        self.store, self.member = store, member

    def resolve(self, db, work_order=UNSET, job=UNSET, require_job=False):
        work_order, job = self.store.current_work(db, self.member, work_order, job)
        if work_order is None:
            raise ValueError("Supply a work-order number or select one with work_order_select.")
        if require_job and job is None:
            raise ValueError("Supply a job ID or select one with job_select.")
        return work_order, job

    def _select(self, db, work_order, job):
        old = db.execute("SELECT selected_work_order,selected_job FROM members WHERE name=?", (self.member,)).fetchone()
        db.execute(
            "UPDATE members SET selected_work_order=?,selected_job=? WHERE name=?", (work_order, job, self.member)
        )
        if tuple(old) != (work_order, job):
            self.store._event(db, self.member, "work_selected", {"work_order": work_order, "job": job})

    def _create(self, table, title, objective, done_when, constraints="", key=None, work_order=UNSET, **bindings):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.create_in(db, table, title, objective, done_when, constraints, key, work_order, **bindings)

    def create_in(
        self,
        db,
        table,
        title,
        objective,
        done_when,
        constraints="",
        key=None,
        work_order=UNSET,
        *,
        select=True,
        **bindings,
    ):
        parent = self.resolve(db, work_order, None)[0] if table == "jobs" else None
        payload = canonical([title, objective, done_when, constraints, parent, bindings])
        old = db.execute(
            f"SELECT id,request_payload FROM {table} WHERE created_by=? AND request_key=?", (self.member, key)
        ).fetchone()
        if old:
            if old["request_payload"] != payload:
                raise ValueError("That request key already identifies different work.")
            ident = old["id"]
        else:
            now = stamp()
            fields = dict(
                title=title,
                objective=objective,
                done_when=done_when,
                constraints=constraints,
                created_by=self.member,
                created=now,
                updated=now,
                request_key=key,
                request_payload=payload,
            )
            if table == "jobs":
                self._check_branch(bindings)
                fields.update(work_order=parent, **bindings)
            ident = db.execute(
                f"INSERT INTO {table}(" + ",".join(fields) + ") VALUES (" + ",".join("?" for _ in fields) + ")",
                tuple(fields.values()),
            ).lastrowid
            self.store._event(
                db,
                self.member,
                "job_created" if table == "jobs" else "work_order_created",
                {"work_order": parent if table == "jobs" else ident, "job": ident if table == "jobs" else None},
            )
        work_order, job = (parent, ident) if table == "jobs" else (ident, None)
        if select:
            self._select(db, work_order, job)
        result = {"work_order": record(db, "work_orders", work_order), "selected": select}
        if job is not None:
            result["job"] = record(db, "jobs", job)
        return result

    def work_order_create(self, **args):
        return self._create("work_orders", **args)

    def job_create(self, **args):
        return self._create("jobs", **args)

    def work_order_select(self, work_order=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            work_order, job = self.store.current_work(db, self.member, work_order, None)
            self._select(db, work_order, job)
            return {
                "work_order": record(db, "work_orders", work_order) if work_order is not None else None,
                "job": None,
            }

    def job_select(self, job=None):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            work_order, job = self.store.current_work(db, self.member, job=job)
            self._select(db, work_order, job)
            return {
                "work_order": record(db, "work_orders", work_order) if work_order is not None else None,
                "job": record(db, "jobs", job) if job is not None else None,
            }

    def _get(self, work_order=UNSET, job=UNSET, require_job=False, after=0, limit=20):
        with self.store.connect() as db:
            work_order, job = self.resolve(db, work_order, job, require_job)
            result = {"work_order": record(db, "work_orders", work_order)}
            if require_job:
                result["job"] = record(db, "jobs", job)
            else:
                job = None
                children = [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,title,status,repository,branch,pull_request FROM jobs WHERE work_order=? ORDER BY id LIMIT 11",
                        (work_order,),
                    )
                ]
                result.update(jobs=children[:10], next_job=children[9]["id"] if len(children) > 10 else None)
            scope = "work_order=?" + (" AND job=?" if require_job else "")
            args = (work_order, job) if require_job else (work_order,)
            links = [
                dict(row)
                for row in db.execute(
                    f"SELECT * FROM work_links WHERE {scope} AND id>? ORDER BY id LIMIT ?", (*args, after, limit + 1)
                )
            ]
            result["next_after"] = links[limit - 1]["id"] if len(links) > limit else None
            result["links"] = links[:limit]
            for link in result["links"]:
                if link["artifact"] is not None:
                    link["artifact"] = artifact_record(db, link["artifact"])
            result["members"] = [
                row[0] for row in db.execute(f"SELECT DISTINCT actor FROM events WHERE {scope} ORDER BY actor", args)
            ]
            result["message_count"] = db.execute(f"SELECT count(*) FROM messages WHERE {scope}", args).fetchone()[0]
            return result

    def work_order_get(self, work_order=UNSET, **args):
        return self._get(work_order, None, **args)

    def job_get(self, job=UNSET, **args):
        return self._get(job=job, require_job=True, **args)

    def _list(self, table, query="", status=None, work_order=None, after=0, limit=20):
        sql = "SELECT id,title,status,next_action,blocker,created_by,updated"
        if table == "jobs":
            sql += ",work_order,repository,branch,pull_request"
        sql += f" FROM {table} WHERE id>?"
        args = [after]
        if status is not None:
            sql += " AND status=?"
            args.append(status)
        if work_order is not None:
            sql += " AND work_order=?"
            args.append(work_order)
        if query:
            sql += " AND instr(lower(title || ' ' || objective),lower(?))>0"
            args.append(query)
        with self.store.connect() as db:
            rows = [dict(row) for row in db.execute(sql + " ORDER BY id LIMIT ?", (*args, limit + 1))]
        return {table: rows[:limit], "next_after": rows[limit - 1]["id"] if len(rows) > limit else None}

    def work_order_list(self, **args):
        return self._list("work_orders", **args)

    def job_list(self, **args):
        return self._list("jobs", **args)

    @staticmethod
    def _check_branch(fields):
        if fields.get("branch") and not fields.get("repository"):
            raise ValueError("Include the repository when recording a branch.")

    def _update(self, table, work_order=UNSET, job=UNSET, **fields):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self.update_in(db, table, work_order, job, **fields)

    def update_in(self, db, table, work_order=UNSET, job=UNSET, **fields):
        if not fields or fields.keys() - (FIELDS | JOB_FIELDS):
            raise ValueError("Supply the fields to update.")
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError("Unknown status")
        work_order, job = self.resolve(db, work_order, job, table == "jobs")
        ident = job if table == "jobs" else work_order
        current = record(db, table, ident)
        fields = {key: value for key, value in fields.items() if current[key] != value}
        if not fields:
            return {"job" if table == "jobs" else "work_order": current}
        self._check_branch(record(db, table, ident) | fields)
        db.execute(
            f"UPDATE {table} SET " + ",".join(field + "=?" for field in fields) + ",updated=? WHERE id=?",
            (*fields.values(), stamp(), ident),
        )
        self.store._event(
            db,
            self.member,
            "job_updated" if table == "jobs" else "work_order_updated",
            {"work_order": work_order, "job": job, "fields": sorted(fields)},
        )
        return {"job" if table == "jobs" else "work_order": record(db, table, ident)}

    def work_order_update(self, work_order=UNSET, **fields):
        return self._update("work_orders", work_order, None, **fields)

    def job_update(self, job=UNSET, **fields):
        return self._update("jobs", job=job, **fields)

    def work_link(self, kind, reference, repository="", label="", work_order=UNSET, job=UNSET):
        if kind not in LINK_KINDS:
            raise ValueError("Unknown reference kind")
        if kind in {"branch", "commit"} and not repository:
            raise ValueError("Include the repository for a branch or commit reference.")
        if kind == "artifact" and (not reference.isdecimal() or repository):
            raise ValueError("Use the artifact ID as its reference, without a repository.")
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            work_order, job = self.resolve(db, work_order, job)
            return self.attach(db, work_order, job, kind, reference, repository, label)

    def attach(self, db, work_order, job, kind, reference, repository="", label=""):
        artifact = int(reference) if kind == "artifact" else None
        if artifact is not None:
            artifact_record(db, artifact)
            reference = str(artifact)
        old = db.execute(
            "SELECT * FROM work_links WHERE work_order=? AND job IS ? AND kind=? AND reference=? AND repository=?",
            (work_order, job, kind, reference, repository),
        ).fetchone()
        if old:
            return {"link": dict(old)}
        ident = db.execute(
            "INSERT INTO work_links(work_order,job,kind,reference,repository,label,created_by,created,artifact) VALUES (?,?,?,?,?,?,?,?,?)",
            (work_order, job, kind, reference, repository, label, self.member, stamp(), artifact),
        ).lastrowid
        self.store._event(
            db,
            self.member,
            "work_linked",
            {"work_order": work_order, "job": job, "link": ident, "kind": kind, "reference": reference},
        )
        return {"link": dict(db.execute("SELECT * FROM work_links WHERE id=?", (ident,)).fetchone())}

    def work_unlink(self, link, work_order=UNSET, job=UNSET):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            work_order, job = self.resolve(db, work_order, job)
            changed = db.execute(
                "DELETE FROM work_links WHERE id=? AND work_order=? AND job IS ?", (link, work_order, job)
            ).rowcount
            if changed:
                self.store._event(
                    db, self.member, "work_unlinked", {"work_order": work_order, "job": job, "link": link}
                )
            return {"work_order": work_order, "job": job, "link": link, "removed": bool(changed)}
