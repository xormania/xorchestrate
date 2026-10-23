"""An artifact catalogue backed by SQLite and private, immutable file snapshots."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import mimetypes
import os
import stat
import tempfile
from pathlib import Path

from .store import UNSET, canonical, stamp
from .work import Work, artifact_record


class Artifacts:
    def __init__(self, store, member):
        self.store, self.member = store, member
        self.root = store.directory / "artifacts"

    def _path(self, location):
        path = self.store.directory / location
        if not path.resolve().is_relative_to(self.root.resolve()) or self.root.resolve() != self.root:
            raise ValueError("Invalid managed artifact location")
        if path.is_symlink():
            raise ValueError("An artifact must be a managed file, not a symlink")
        return path

    def _source(self, source, job=UNSET):
        from .workspaces import current_workspace

        workspace = current_workspace(self.store, self.member, job=job)
        path = (workspace / source).resolve(strict=True)
        if Path(source).is_absolute() or not path.is_relative_to(workspace):
            raise ValueError("Use a file relative to this member's workspace.")
        stream = os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb")
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            stream.close()
            raise ValueError("An artifact source must be a regular file.")
        return stream

    @staticmethod
    def _digest(path):
        with path.open("rb") as stream:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            return digest.hexdigest()

    def save(self, name, content=None, source=None, encoding="utf8", media_type=None, work_order=UNSET, job=UNSET):
        if (content is None) == (source is None):
            raise ValueError("Supply artifact content or a workspace source file, exactly one.")
        if source is not None and encoding != "utf8":
            raise ValueError("Encoding applies to inline content; workspace files are copied as bytes.")
        media_type = media_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        # Resolve once so a simultaneous job selection cannot move this save to another job.
        with self.store.connect() as db:
            work_order, job = self.store.current_work(db, self.member, work_order, job)
        if source is not None:
            stream = self._source(source, job=job)
        elif encoding == "base64":
            try:
                stream = io.BytesIO(base64.b64decode(content, validate=True))
            except ValueError as error:
                raise ValueError("Artifact content is not valid base64.") from error
        elif encoding == "utf8":
            stream = io.BytesIO(content.encode("utf8"))
        else:
            raise ValueError("Use utf8 or base64 for inline content.")
        return self.ingest(name, stream, media_type, work_order=work_order, job=job, source_file=source is not None)

    def ingest(
        self, name, stream, media_type, *, work_order=UNSET, job=UNSET, source_file=False, provenance=None, maximum=None
    ):
        """Service-only stream ingestion; MCP callers cannot supply paths outside their workspace."""
        temporary = None
        try:
            with self.store.connect() as db:
                work_order, job = self.store.current_work(db, self.member, work_order, job)
            self.root.mkdir(mode=0o700, exist_ok=True)
            self._path("artifacts/check")
            before = os.fstat(stream.fileno()) if source_file else None
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".save-", delete=False) as target:
                temporary = Path(target.name)
                digest, size = hashlib.sha256(), 0
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    target.write(block)
                    digest.update(block)
                    size += len(block)
                    if maximum is not None and size > maximum:
                        raise ValueError("Artifact exceeds the configured byte limit.")
                target.flush()
                os.fsync(target.fileno())
            if before is not None:
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != after.st_size:
                    raise ValueError("The source changed while saving. Retry once the file is stable.")
            sha256 = digest.hexdigest()
            location = f"artifacts/{sha256}"
            destination = self._path(location)
            # Publish complete bytes before their catalogue record. A retry can safely reuse an
            # unreferenced blob left by an interrupted database commit; no half-file is visible.
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if self._digest(destination) != sha256:
                    raise ValueError("Stored artifact failed its integrity check.") from None
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                old = db.execute(
                    "SELECT id FROM artifacts WHERE created_by=? AND name=? AND media_type=? AND sha256=?",
                    (self.member, name, media_type, sha256),
                ).fetchone()
                if old:
                    ident = old["id"]
                else:
                    ident = db.execute(
                        "INSERT INTO artifacts(name,media_type,size,sha256,location,created_by,created) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (name, media_type, size, sha256, location, self.member, stamp()),
                    ).lastrowid
                    self.store._event(
                        db,
                        self.member,
                        "artifact_saved",
                        {"work_order": work_order, "job": job, "artifact": ident, "size": size},
                    )
                if work_order is not None:
                    Work(self.store, self.member).attach(db, work_order, job, "artifact", str(ident))
                if provenance:
                    db.execute(
                        "INSERT OR IGNORE INTO artifact_sources(artifact,provider,reference,metadata) VALUES (?,?,?,?)",
                        (ident, provenance["provider"], provenance["reference"], canonical(provenance)),
                    )
                return {
                    "artifact": artifact_record(db, ident),
                    "work_order": work_order,
                    "job": job,
                    "reused": bool(old),
                }
        finally:
            stream.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def list(self, query="", work_order=None, job=None, after=0, limit=20):
        sql, args = "SELECT a.id FROM artifacts a WHERE a.id>?", [after]
        if query:
            sql += " AND instr(lower(a.name),lower(?))>0"
            args.append(query)
        for field, value in (("work_order", work_order), ("job", job)):
            if value is not None:
                sql += f" AND EXISTS (SELECT 1 FROM work_links l WHERE l.artifact=a.id AND l.{field}=?)"
                args.append(value)
        with self.store.connect() as db:
            rows = [
                artifact_record(db, row[0]) for row in db.execute(sql + " ORDER BY a.id LIMIT ?", (*args, limit + 1))
            ]
        return {"artifacts": rows[:limit], "next_after": rows[limit - 1]["id"] if len(rows) > limit else None}

    def read(self, artifact, offset=0, limit=16384):
        with self.store.connect() as db:
            result = {"artifact": artifact_record(db, artifact)}
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact,)).fetchone()
            result["sources"] = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT metadata FROM artifact_sources WHERE artifact=? ORDER BY provider,reference", (artifact,)
                )
            ]
            result["work"] = [
                dict(r)
                for r in db.execute(
                    "SELECT work_order,job FROM work_links WHERE artifact=? ORDER BY work_order,job", (artifact,)
                )
            ]
        path = self._path(row["location"])
        try:
            if self._digest(path) != row["sha256"]:
                raise ValueError("Stored artifact failed its integrity check.")
            with path.open("rb") as stream:
                stream.seek(offset)
                content = stream.read(limit)
        except FileNotFoundError:
            raise ValueError("Artifact content is missing from managed storage.") from None
        if offset > row["size"]:
            raise ValueError("The read offset is beyond the artifact's size.")
        try:
            text = content.decode("utf8")
        except UnicodeDecodeError as error:
            if error.reason == "unexpected end of data" and error.start > 0:
                content = content[: error.start]
                text = content.decode("utf8")
            else:
                text = None
        result.update(
            content=text if text is not None else base64.b64encode(content).decode("ascii"),
            encoding="utf8" if text is not None else "base64",
            next_offset=offset + len(content) if offset + len(content) < row["size"] else None,
        )
        return result
