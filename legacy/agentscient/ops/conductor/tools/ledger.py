#!/usr/bin/env python3
"""Every appended register as one file per row, so parallel sessions' rows never touch.

    ledger.py ledgers                         the ledgers this lever knows, one per line
    ledger.py next LEDGER [--archive MANIFEST --sha256 HASH]   reserve this owner's id
    ledger.py check LEDGER                    findings: shared or unfiled numbers
    ledger.py add LEDGER --field VALUE ...    write the row file, print "ID path"
    ledger.py complete LEDGER ID --path PATH --sha256 HASH   file an existing row
    ledger.py show LEDGER ID                  print the row
    ledger.py list LEDGER                     one line per row: id, observed, first field

Owner, 2026-09-13: the pitfalls page and anything similar, because agents
need to work in parallel. lane-defects.md conflicted on three of five
merge-forwards since 2026-09-11 and pitfalls-20260911.md on one, each
session appending its row at the same line of one table. A ledger here
is a page whose table stops at a row, a directory of one file per row
after it, an id shape and a field list. IDs are reserved transactionally
in the lane state store before their git row is written, while the row
files remain the record plane. PR264's reader counts every retained ref,
including historical additions (2026-09-16, CO ruling M-155).
defects.py is a shim over the defects ledger.

Follows instrument-conventions.md: the lever works on the repository of
its cwd and refuses `repository` outside one; writing verbs need an
identity, reads never do; refusals are one stderr line, exit 2, stdout empty.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    import machine
    from instrument import (
        Refusal,
        conf,
        emit,
        git,
        identity,
        now,
        parse_stamp,
        refuse,
        session,
        stamp,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# name -> page (table rows stop there), rows directory, id prefix, heading
# word, fields in order. The table's id column is matched as "| <prefix><n> |".
LEDGERS: dict[str, dict[str, object]] = {
    "defects": {
        "page": Path("ops/conductor/handoff/lane-defects.md"),
        "rows": Path("ops/conductor/handoff/lane-defects"),
        "prefix": "LD-",
        "heading": "",
        "fields": ("where", "defect", "repair", "tests"),
    },
    "pitfalls": {
        "page": Path("ops/conductor/handoff/pitfalls-20260911.md"),
        "rows": Path("ops/conductor/handoff/pitfalls"),
        "prefix": "",
        "heading": "pitfall ",
        "fields": ("what-bit", "do-instead"),
    },
    # LD-53: two sessions minted I-029 on two branches because there was
    # no reader to ask. An idea is prose in the owner's words in the
    # shape ops/conductor/ideas/README.md gives, never a field table, so
    # this register is handwritten: the lever mints its number and
    # reports the numbers already shared, and writes nothing.
    "ideas": {
        "page": None,
        "rows": Path("ops/conductor/ideas"),
        "prefix": "I-",
        "heading": "",
        "fields": (),
        # The record's file name carries a slug after its number, so the
        # entry pattern is the directory's own, not the default one row
        # per id.
        "entry": r"I-(\d+)-.+\.md",
        "width": 3,
        "handwritten": True,
    },
}
FIELD_LINE = re.compile(r"^\| ([\w-]+) \| (.*) \|$", re.MULTILINE)
RESERVATIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_reservations(
    ledger TEXT NOT NULL,
    number INTEGER NOT NULL,
    owner TEXT NOT NULL,
    agent TEXT NOT NULL,
    reserved_at TEXT NOT NULL,
    filed_at TEXT,
    path TEXT,
    UNIQUE(ledger, number)
)
"""


def ledger(name: str) -> dict[str, object]:
    if name not in LEDGERS:
        refuse("ledger", "one of " + ", ".join(LEDGERS), name or "nothing", "name a ledger this lever knows")
    return LEDGERS[name]


def repo_root() -> Path:
    proc = git("rev-parse", "--show-toplevel")
    if proc.returncode != 0 or not proc.stdout.strip():
        refuse("repository", "a git repository at the working directory", os.getcwd(),
               "run the lever from inside the repository whose ledger it writes")
    return Path(proc.stdout.strip())


def trunk_ref(root: Path) -> str | None:
    """Read the trunk ref already refreshed by the historical discovery."""
    ref = f"origin/{conf('TRUNK_BRANCH')}"
    return ref if git("rev-parse", "--verify", "-q", ref, cwd=root, timeout=20).returncode == 0 else None


def entry_re(spec: dict[str, object]) -> re.Pattern:
    """A row file's name to its number. A register whose file names carry
    more than the id (the ideas directory's slug) declares its own."""
    named = spec.get("entry")
    if named:
        return re.compile(str(named))
    return re.compile(rf"{re.escape(str(spec['prefix']))}(\d+)\.md$")


def ident(spec: dict[str, object], number: int) -> str:
    """The id as the register spells it. The ideas directory writes its
    number three digits wide, so I-32 would be a second spelling of one
    id and the reader would have taught a session to mint it."""
    return f"{spec['prefix']}{number:0{int(spec.get('width') or 0)}d}"


def reservation_ledger(spec: dict[str, object]) -> str:
    """The stable register name stored beside a reserved number."""
    for name, candidate in LEDGERS.items():
        if candidate is spec or candidate == spec:
            return name
    refuse("ledger", "a declared ledger specification", spec, "reserve an id for a ledger this lever knows")


def reservation_owner(root: Path) -> str:
    """The harness session, or the worktree when no harness named one."""
    named = session()
    return named if named != "unknown" else str(root.resolve())


def reservation_agent() -> str:
    """Attribute a reservation, retaining the former identity-free `next`.

    `next` was a read before it became the reservation verb. Existing
    callers may therefore have no identity; new callers are attributed
    through the shared identity reader, while that one legacy absence is
    recorded explicitly rather than inferred from another environment key.
    """
    try:
        return identity()
    except Refusal as error:
        if error.kind == "identity" and error.found == "unset":
            return "unknown"
        raise


def reservation_writer() -> sqlite3.Connection:
    conn = machine.connect(create=True)
    try:
        with instrument.transaction(conn):
            conn.execute(RESERVATIONS_SCHEMA)
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(ledger_reservations)")}
            for name, kind in (("history_floor", "INTEGER NOT NULL DEFAULT 0"), ("history_source", "TEXT"),
                               ("intent_path", "TEXT"), ("intent_sha256", "TEXT")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE ledger_reservations ADD COLUMN {name} {kind}")
    except BaseException:
        conn.close()
        raise
    return conn


def reservation_reader() -> sqlite3.Connection | None:
    """Open the register without creating either the store or its table."""
    if not machine.store_path().is_file():
        return None
    conn = machine.connect(create=False)
    present = conn.execute("PRAGMA table_info(ledger_reservations)").fetchone()
    if present is None:
        conn.close()
        return None
    return conn


def _reserve_locked(
    conn: sqlite3.Connection,
    root: Path,
    spec: dict[str, object],
    owner: str,
    agent: str,
    observed: int,
    history_source: str | None = None,
) -> int:
    name = reservation_ledger(spec)
    row = conn.execute(
        "SELECT MAX(MAX(number, history_floor)) AS number FROM ledger_reservations WHERE ledger = ?",
        (name,),
    ).fetchone()
    reserved = int(row["number"] or 0)
    number = max(observed, reserved, max(by_number(root, spec), default=0)) + 1
    conn.execute(
        "INSERT INTO ledger_reservations"
        "(ledger, number, owner, agent, reserved_at, history_floor, history_source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, number, owner, agent, stamp(), observed, history_source),
    )
    return number


def reserve(
    conn: sqlite3.Connection,
    root: Path,
    spec: dict[str, object],
    owner: str,
    agent: str,
    *, observed: int | None = None, history_source: str | None = None,
) -> int:
    """Reserve the next number in one immediate transaction.

    `BEGIN IMMEDIATE` serializes same-host minters. The unique key remains
    the second wall: if another SQLite client wins independently, re-read
    the register and retry once rather than returning a shared number.
    """
    if observed is None:
        observed = highest(root, spec)
    winner = 0
    for attempt in range(2):
        try:
            with instrument.transaction(conn):
                number = _reserve_locked(conn, root, spec, owner, agent, observed, history_source)
            return number
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT MAX(number) AS number FROM ledger_reservations WHERE ledger = ?",
                (reservation_ledger(spec),),
            ).fetchone()
            winner = int(row["number"] or 0)
            if attempt == 0:
                continue
    refuse(
        "reservation",
        f"one unused number after {ident(spec, winner)}",
        "the same number won twice",
        "retry after the competing reservation commits",
    )


def own_reservation(
    conn: sqlite3.Connection,
    root: Path,
    spec: dict[str, object],
    owner: str,
    agent: str,
    *, archived: set[int] | None = None, history_source: str | None = None,
) -> int:
    """Read history before the short shared-store allocation transaction."""
    name = reservation_ledger(spec)
    occupied = observed_numbers(root, spec)
    occupied.update(archived or ())
    observed = max(occupied, default=0)
    try:
        with instrument.transaction(conn):
            row = conn.execute(
                "SELECT * FROM ledger_reservations "
                "WHERE ledger = ? AND owner = ? AND filed_at IS NULL "
                "ORDER BY number LIMIT 1",
                (name, owner),
            ).fetchone()
            if row is not None:
                number = int(row["number"])
                conn.execute("UPDATE ledger_reservations SET history_floor = MAX(history_floor, ?) "
                             "WHERE ledger = ? AND number = ?", (observed, name, number))
                if history_source:
                    previous = json.loads(row["history_source"] or "[]")
                    conn.execute("UPDATE ledger_reservations SET history_source = ? WHERE ledger = ? AND number = ?",
                                 (json.dumps(sorted(set([*previous, *json.loads(history_source)]))), name, number))
                # A completed exact intended publication can be retried after the
                # receipt write failed. A same-number foreign file cannot be adopted.
                taken = number in occupied or number in by_number(root, spec)
                matching = False
                if row["intent_path"] and row["intent_sha256"]:
                    path = root / row["intent_path"]
                    contain(root, path, Path(row["intent_path"]))
                    matching = (path.is_file() and not path.is_symlink()
                                and hashlib.sha256(path.read_bytes()).hexdigest() == row["intent_sha256"])
            else:
                number = _reserve_locked(conn, root, spec, owner, agent, observed, history_source)
    except sqlite3.IntegrityError:
        return reserve(conn, root, spec, owner, agent, observed=observed, history_source=history_source)
    if row is not None and taken and not matching:
        refuse("reservation", f"unoccupied {ident(spec, number)} reserved by {owner}",
               "that number now has a record", "preserve both records; explicitly complete the owned row or reconcile the collision")
    return number


def file_reservation(
    conn: sqlite3.Connection,
    spec: dict[str, object],
    number: int,
    owner: str,
    path: Path,
) -> None:
    changed = conn.execute(
        "UPDATE ledger_reservations SET filed_at = ?, path = ? "
        "WHERE ledger = ? AND number = ? AND owner = ? AND filed_at IS NULL",
        (stamp(), path.as_posix(), reservation_ledger(spec), number, owner),
    ).rowcount
    if changed != 1:
        row = conn.execute("SELECT filed_at, path FROM ledger_reservations "
                           "WHERE ledger = ? AND number = ? AND owner = ?",
                           (reservation_ledger(spec), number, owner)).fetchone()
        if row is not None and row["filed_at"] and row["path"] == path.as_posix():
            return
        refuse(
            "reservation",
            f"one open reservation for {owner} and {ident(spec, number)}",
            f"{changed} rows",
            "run next for this owner before filing the row",
        )


def unfiled_reservations(name: str) -> list[sqlite3.Row]:
    conn = reservation_reader()
    if conn is None:
        return []
    try:
        return conn.execute(
            "SELECT number, owner, reserved_at FROM ledger_reservations "
            "WHERE ledger = ? AND filed_at IS NULL ORDER BY number",
            (name,),
        ).fetchall()
    finally:
        conn.close()


def reservation_age(reserved_at: str) -> str:
    try:
        seconds = max(0, int((now() - parse_stamp(reserved_at)).total_seconds()))
    except (TypeError, ValueError):
        return "unknown"
    return f"{seconds}s"


def by_number(root: Path, spec: dict[str, object]) -> dict[int, list[str]]:
    """The rows directory read as number -> file names, in the working
    tree only: a file on the trunk and the same file here are one row,
    and counting both would report every row as shared with itself."""
    entry = entry_re(spec)
    found: dict[int, list[str]] = {}
    rows = root / spec["rows"]
    if rows.is_dir():
        for name in sorted(os.listdir(rows)):
            m = entry.fullmatch(name) or entry.search(name)
            if m:
                found.setdefault(int(m.group(1)), []).append(name)
    return found


def archive_tips(root: Path) -> list[str]:
    """Traverse immutable tips named by existing committed custody indexes."""
    listed = git("ls-files", "-z", "--",
                 "ops/conductor/handoff/branch-archive-*.tsv",
                 "ops/conductor/handoff/local-branches-merged-*.tsv", cwd=root, timeout=20)
    if listed.returncode != 0:
        refuse("refs", "readable committed archive indexes", "git could not list them",
               "restore the retained index before allocating")
    tips = set()
    for name in filter(None, listed.stdout.split("\0")):
        shown = git("show", f"HEAD:{name}", cwd=root, timeout=20)
        if shown.returncode != 0:
            refuse("refs", "a committed archive index", name,
                   "commit or restore the archive custody index before allocating")
        for line in shown.stdout.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split("\t")
            found = {f for f in fields if re.fullmatch(r"[0-9a-f]{40}", f)}
            if not found and fields[0] not in {"name", "branch", "id"}:
                refuse("refs", "an immutable commit id in each archive row", name,
                       "repair the index from its preserved custody evidence")
            tips.update(found)
    return sorted(tips)


def minted_anywhere(root: Path, spec: dict[str, object]) -> tuple[set[int], bool]:
    """PR264 historical additions, including explicit remote and archived tips.

    Discovery runs before BEGIN IMMEDIATE. The declared origin namespaces are
    heads, tags and dispatch results regardless of configured narrow refspecs.
    Local mode neither fetches nor claims knowledge of unpublished remote work.
    Archived tips remain operands even after their refs disappear; unreadable
    tips are a refusal, never an empty history. S2 persists the observed floor.
    """
    if os.environ.get("LEDGER_REFS") == "local":
        print("LEDGER_REFS=local: retained local refs and committed archive tips only", file=sys.stderr)
    elif git("remote", "get-url", "origin", cwd=root, timeout=20).returncode == 0:
        fetched = git("fetch", "-q", "origin",
                      "refs/heads/*:refs/remotes/origin/*",
                      "refs/tags/*:refs/tags/*",
                      "refs/dispatch/*:refs/remotes/origin-dispatch/*", cwd=root, timeout=60)
        if fetched.returncode != 0:
            return set(), False
        print(f"fetched origin/{conf('TRUNK_BRANCH')}; origin heads, tags and dispatch history", file=sys.stderr)
    tips = archive_tips(root)
    listed = git("log", "--all", *tips, "--no-renames", "--diff-filter=A", "--format=",
                 "--name-only", "--", f"{spec['rows'].as_posix()}/", cwd=root, timeout=30)
    if listed.returncode != 0:
        return set(), False
    entry = entry_re(spec)
    return {int(m.group(1)) for line in listed.stdout.splitlines()
            if (m := entry.fullmatch(Path(line.strip()).name))}, True


def observed_numbers(root: Path, spec: dict[str, object]) -> set[int]:
    """One availability reading, complete before taking the state write lock."""
    found, looked = minted_anywhere(root, spec)
    if not looked:
        refuse("refs", "readable origin namespaces and every indexed retained tip",
               "history could not be refreshed or traversed",
               "restore origin/archive custody, or explicitly use LEDGER_REFS=local for local remote coverage")
    table = re.compile(rf"^\| {re.escape(str(spec['prefix']))}(\d+) \|", re.MULTILINE)
    if spec['page']:
        page = root / spec['page']
        if page.is_file():
            found.update(int(n) for n in table.findall(page.read_text(encoding="utf-8")))
        ref = trunk_ref(root)
        if ref:
            shown = git("show", f"{ref}:{spec['page'].as_posix()}", cwd=root, timeout=20)
            if shown.returncode == 0:
                found.update(int(n) for n in table.findall(shown.stdout))
    found.update(by_number(root, spec))
    return found


def highest(root: Path, spec: dict[str, object]) -> int:
    return max(observed_numbers(root, spec), default=0)


def archived_numbers(spec, manifest, expected):
    """Verify the existing recovery custody format without extracting records."""
    path = Path(manifest).resolve(strict=True)
    raw = path.read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or hashlib.sha256(raw).hexdigest() != expected:
        refuse("refs", "the measured recovery manifest digest", str(path), "use the intact preserved manifest and its measured SHA256")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("dirty"), list):
        refuse("refs", "the existing version-1 recovery custody manifest", str(path), "name the retained recovery manifest")
    found = set()
    for entry in data["dirty"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("files"), list):
            refuse("refs", "typed file entries in the recovery manifest", str(path), "restore the existing custody manifest")
        matches = []
        for item in entry["files"]:
            if not isinstance(item, dict):
                refuse("refs", "a typed recovery file entry", str(path), "restore the existing custody manifest")
            named = Path(item["path"])
            match = entry_re(spec).fullmatch(named.name)
            if named.parent == spec["rows"] and match:
                matches.append((item, int(match.group(1))))
        if not matches:
            continue
        payload = (path.parent / entry["directory"] / "files.tar").resolve(strict=True)
        if not payload.is_relative_to(path.parent):
            refuse("refs", "archive payload inside its custody directory", str(payload), "restore the original custody layout")
        packed = payload.read_bytes()
        if hashlib.sha256(packed).hexdigest() != entry["artifacts"]["files.tar"]:
            refuse("refs", "the archive payload digest named by the manifest", str(payload), "restore the checksum-verified archive")
        with tarfile.open(fileobj=io.BytesIO(packed)) as archive:
            members = archive.getmembers()
            for item, number in matches:
                named = [m for m in members if m.name == item["path"]]
                if len(named) != 1 or not named[0].isfile():
                    refuse("refs", "one regular archived ledger row", item["path"], "restore the intact record payload")
                with archive.extractfile(named[0]) as stream:
                    digest = hashlib.sha256(stream.read()).hexdigest()
                if digest != item["sha256"]:
                    refuse("refs", "the preserved row digest", item["path"], "restore the checksum-verified row")
                found.add(number)
    return found, json.dumps([f"{path} sha256:{expected}"])


def parse_fields(spec: dict[str, object], args: list[str]) -> dict[str, str]:
    fields = tuple(spec["fields"])
    got: dict[str, str] = {}
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("--") and a[2:] in fields + ("observed",):
            if i + 1 >= len(args):
                refuse("arguments", f"a value after {a}", "none", f"give {a} its text")
            got[a[2:]] = args[i + 1]
            i += 2
            continue
        refuse("arguments", "add LEDGER " + " ".join(f"--{f} TEXT" for f in fields) + " [--observed DATE]",
               a, "name each field by its flag")
    # A line break in a value writes a field line of its own, which list then
    # reads as the row's (security audit 20260913T104919Z-review-codex-9126d3,
    # finding 5): every value stays on its one line.
    broken = [f for f, value in got.items() if "\n" in value or "\r" in value]
    if broken:
        refuse("arguments", "each field's text on one line", "a line break in " + " ".join("--" + f for f in broken),
               "join the text onto one line")
    missing = [f for f in fields if not got.get(f, "").strip()]
    if missing:
        refuse("arguments", "every field non-empty: " + " ".join("--" + f for f in fields),
               "missing or empty: " + " ".join("--" + f for f in missing), "say each field's text")
    return got


def contain(root: Path, target: Path, shown: Path) -> None:
    """Refuse a write whose target resolves outside the repository. A symlink
    at the rows directory sent a row file anywhere the link pointed (security
    audit 20260913T104919Z-review-codex-9126d3, finding 3)."""
    inside = root.resolve()
    resolved = target.resolve()
    if not resolved.is_relative_to(inside):
        refuse("containment", f"{shown} inside the repository {inside}", f"it resolves to {resolved}",
               f"replace the symlink at {shown} with a directory of the repository")


def handwritten(name: str, spec: dict[str, object], verb: str) -> None:
    """A register the lever reads and never writes refuses the verbs that
    would write one or read a field table out of prose."""
    if spec.get("handwritten"):
        refuse("ledger", f"a ledger this lever writes for {verb}", name,
               f"{name} is recorded by hand in the shape its README gives; "
               f"this lever answers next {name} and check {name}")


def do_check(root: Path, name: str) -> int:
    """Findings, not a refusal: the verb ran to completion and the result
    carries what it found (docs/wire.md). A number minted twice in the
    past says nothing about the next one, so `next` keeps answering and
    this is where the collision is reported.
    """
    spec = ledger(name)
    shared = {n: names for n, names in by_number(root, spec).items() if len(names) > 1}
    unfiled = unfiled_reservations(name)
    if not shared and not unfiled:
        print(f"{name}: no shared numbers")
        return 0
    for number in sorted(shared):
        print(f"{ident(spec, number)} shared by " + ", ".join(shared[number]))
    for row in unfiled:
        print(
            f"{ident(spec, int(row['number']))} reserved and unfiled by "
            f"{row['owner']} age={reservation_age(row['reserved_at'])}"
        )
    print(
        f"{name}: {len(shared)} shared number(s), "
        f"{len(unfiled)} reserved-and-unfiled number(s)"
    )
    return 1


def prepare_intent(conn, root, spec, number, owner, relative, sha256):
    """Commit the intended publication before touching the filesystem."""
    with instrument.transaction(conn):
        row = conn.execute("SELECT * FROM ledger_reservations WHERE ledger = ? AND number = ? AND owner = ?",
                           (reservation_ledger(spec), number, owner)).fetchone()
        if row is None or (row["intent_sha256"] and
                           (row["intent_sha256"] != sha256 or row["intent_path"] != str(relative))):
            refuse("reservation", "this owner's unchanged intended record", "absent or different intent",
                   "retry with the original bytes or explicitly reconcile the reservation")
        path = root / relative
        contain(root, path, relative)
        if ((path.exists() or path.is_symlink()) and
                (not row["intent_sha256"] or path.is_symlink() or not path.is_file()
                 or hashlib.sha256(path.read_bytes()).hexdigest() != sha256)):
            refuse("reservation", "an unused target or this exact intended publication", str(relative),
                   "preserve the existing record and reconcile the reservation")
        conn.execute("UPDATE ledger_reservations SET intent_path = ?, intent_sha256 = ? "
                     "WHERE ledger = ? AND number = ? AND owner = ?",
                     (str(relative), sha256, reservation_ledger(spec), number, owner))


def publish_exclusive(root, relative, content):
    path = root / relative
    contain(root, path, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish a complete fsynced inode with link's exclusive target semantics.
    # The temporary inode is apparatus, not a second reservation/state file.
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".ledger-", delete=True) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        try:
            os.link(temporary.name, path, follow_symlinks=False)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                refuse("reservation", "an exclusive unused target or identical intended bytes", str(relative),
                       "preserve the existing row and reconcile the reservation")


def do_add(root: Path, name: str, args: list[str]) -> int:
    spec = ledger(name)
    handwritten(name, spec, "add")
    by = identity()
    fields = parse_fields(spec, args)
    observed = fields.get("observed") or now().strftime("%Y-%m-%d")
    rows = root / spec["rows"]
    contain(root, rows, spec["rows"])
    owner = reservation_owner(root)
    conn = reservation_writer()
    try:
        number = own_reservation(conn, root, spec, owner, by)
    finally:
        conn.close()
    row_id = ident(spec, number)
    rows.mkdir(parents=True, exist_ok=True)
    path = rows / f"{row_id}.md"
    contain(root, path, spec["rows"] / path.name)
    lines = [f"# {spec['heading']}{row_id}", "", "| field | value |", "|---|---|", f"| observed | {observed} |"]
    lines += [f"| {f} | {fields[f]} |" for f in spec["fields"]]
    lines += [f"| by | {by} |", ""]
    content = "\n".join(lines).encode("utf-8")
    relative = spec["rows"] / path.name
    conn = reservation_writer()
    try:
        prepare_intent(conn, root, spec, number, owner, relative, hashlib.sha256(content).hexdigest())
        publish_exclusive(root, relative, content)
        file_reservation(conn, spec, number, owner, relative)
    finally:
        conn.close()
    print(f"{row_id} {relative}")
    return 0


def row_path(root: Path, name: str, row_id: str) -> Path:
    spec = ledger(name)
    handwritten(name, spec, "show")
    ident = row_id
    if not re.fullmatch(rf"{re.escape(str(spec['prefix']))}\d+", ident):
        refuse("arguments", f"an id of the form {spec['prefix']}NN", ident, "name the row by its id")
    path = root / spec["rows"] / f"{ident}.md"
    if not path.is_file():
        refuse("absent", f"a row file {spec['rows'] / (ident + '.md')}", "no such file",
               f"list the rows with ledger.py list {name}; earlier rows sit in the page's table")
    return path


def do_show(root: Path, name: str, ident: str) -> int:
    sys.stdout.write(row_path(root, name, ident).read_text(encoding="utf-8"))
    return 0


def do_list(root: Path, name: str) -> int:
    spec = ledger(name)
    handwritten(name, spec, "list")
    rows = root / spec["rows"]
    prefix = re.escape(str(spec["prefix"]))
    entries = []
    if rows.is_dir():
        for entry in os.listdir(rows):
            m = re.fullmatch(rf"{prefix}(\d+)\.md", entry)
            if m:
                entries.append((int(m.group(1)), entry))
    first = spec["fields"][0]
    for _, entry in sorted(entries):
        field = dict(FIELD_LINE.findall((rows / entry).read_text(encoding="utf-8")))
        print(f"{entry[:-3]} {field.get('observed', '?')} {field.get(first, '?')}")
    return 0


def do_ledgers() -> int:
    for name, spec in LEDGERS.items():
        line = f"{name} {spec['rows']} "
        line += ("(handwritten: next reserves, complete files, check reads)" if spec.get("handwritten")
                 else " ".join("--" + f for f in spec["fields"]))
        print(line)
    return 0


def do_complete(root: Path, name: str, row_id: str, args: list[str]) -> int:
    identity()
    spec = ledger(name)
    if len(args) != 4 or args[0] != "--path" or args[2] != "--sha256" or not re.fullmatch(r"[0-9a-f]{64}", args[3]):
        refuse("arguments", "complete LEDGER ID --path RELATIVE_PATH --sha256 SHA256", " ".join(args),
               "name the exact existing row and its measured digest")
    relative, expected = Path(args[1]), args[3]
    match = entry_re(spec).fullmatch(relative.name)
    if relative.is_absolute() or ".." in relative.parts or relative.parent != spec["rows"] or not match or ident(spec, int(match.group(1))) != row_id:
        refuse("reservation", "the exact ID-bearing file under this ledger's directory", str(relative),
               "name the owned row by its canonical ledger path and ID")
    number = int(match.group(1))
    path = root / relative
    contain(root, path, relative)
    if (path.is_symlink() or not path.is_file() or by_number(root, spec).get(number) != [path.name]
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
        refuse("reservation", "one regular row with the measured digest and no ID aliases", str(relative),
               "preserve the row, resolve aliases, and measure its current SHA256")
    owner = reservation_owner(root)
    conn = reservation_writer()
    try:
        with instrument.transaction(conn):
            row = conn.execute("SELECT * FROM ledger_reservations WHERE ledger = ? AND number = ? AND owner = ?",
                               (name, number, owner)).fetchone()
            if row is None or (row["intent_sha256"] and
                               (row["intent_sha256"] != expected or row["intent_path"] != str(relative))):
                refuse("reservation", "this owner's reservation and unchanged intended row", row_id,
                       "use the reserving session and its exact intended bytes")
            if row["filed_at"] and row["path"] != str(relative):
                refuse("reservation", "the original filed path", row["path"], "retain the filed receipt")
            conn.execute("UPDATE ledger_reservations SET intent_path = ?, intent_sha256 = ? WHERE ledger = ? AND number = ?",
                         (str(relative), expected, name, number))
            file_reservation(conn, spec, number, owner, relative)
    finally:
        conn.close()
    print(f"{row_id} {relative}")
    return 0


USAGE = "ledgers | next LEDGER [--archive MANIFEST --sha256 HASH] | check LEDGER | add LEDGER --field TEXT ... | complete LEDGER ID --path PATH --sha256 HASH | show LEDGER ID | list LEDGER"


def _main(argv: list[str]) -> int:
    if argv == ["ledgers"]:
        return do_ledgers()
    if len(argv) in {2, 6} and argv[0] == "next":
        spec = ledger(argv[1])
        root = repo_root()
        archived, source = set(), None
        if len(argv) == 6:
            if argv[2] != "--archive" or argv[4] != "--sha256":
                refuse("arguments", "next LEDGER --archive MANIFEST --sha256 HASH", " ".join(argv), "name exact preserved custody")
            archived, source = archived_numbers(spec, argv[3], argv[5])
        conn = reservation_writer()
        try:
            number = own_reservation(
                conn,
                root,
                spec,
                reservation_owner(root),
                reservation_agent(),
                archived=archived, history_source=source,
            )
        finally:
            conn.close()
        return print(ident(spec, number)) or 0
    if len(argv) == 2 and argv[0] == "check":
        return do_check(repo_root(), argv[1])
    if len(argv) >= 3 and argv[0] == "complete":
        return do_complete(repo_root(), argv[1], argv[2], argv[3:])
    if len(argv) >= 2 and argv[0] == "add":
        return do_add(repo_root(), argv[1], argv[2:])
    if len(argv) == 3 and argv[0] == "show":
        return do_show(repo_root(), argv[1], argv[2])
    if len(argv) == 2 and argv[0] == "list":
        return do_list(repo_root(), argv[1])
    refuse("arguments", USAGE, " ".join(argv) or "nothing", "choose one verb with exactly its arguments")


def main(argv: list[str]) -> int:
    # Progress on stderr must not turn a later refusal into a multi-line wire.
    progress = io.StringIO()
    try:
        with contextlib.redirect_stderr(progress):
            code = _main(argv)
    except sqlite3.Error as exc:
        refuse("store", "a readable writable ledger store with an available transaction",
               " ".join(str(exc).splitlines()), "repair store access or retry after its writer completes; pending reservations remain")
    except (OSError, UnicodeError) as exc:
        refuse("filesystem", "readable custody and an exclusively writable row target",
               " ".join(str(exc).splitlines()), "restore access and retry the unchanged reserved record")
    except (OverflowError, ValueError, KeyError, TypeError, tarfile.TarError) as exc:
        refuse("reservation", "valid bounded ledger numbers and record values",
               " ".join(str(exc).splitlines()), "repair the named input or retained record; do not discard reservation history")
    sys.stderr.write(progress.getvalue())
    return code


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refusal as refusal:
        sys.exit(emit(refusal))
