"""Git reads the chain depends on, and (build step 7) the export and commits.

Two things here are load-bearing.

**The manifest is read from git, not from the working tree**, whenever the
tree is clean.  Red-time and verify-time then hash the same bytes from the
same source, so the question of whether a checkout filter sits between the
blob and the file never arises.  ``--allow-dirty`` falls back to the working
tree and the receipt records ``tree_dirty``, which is exactly the case D3
says can only be re-verified against its recorded output hashes.

**wf's own export is excluded from the dirty check.**  A ``commit.landed``
event can only land in the commit *after* the one it names (PLAN §3.6), so
between commands the export always carries one uncommitted line.  The dirty
check is about the tree the command under test ran against, not about wf's
bookkeeping being one bind behind by construction.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from errors import Integrity, Refusal

# Paths wf writes itself. Modifications here are the recorder's own trail.
STATE_SUBDIRS = ("chains", "attempts", "objects")
STATE_FILES = ("meta.json", "wf.db")


def git(repo, *args, check=True, binary=False):
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=not binary, check=False)
    if check and proc.returncode != 0:
        detail = proc.stderr if not binary else proc.stderr.decode("utf-8", "replace")
        raise Refusal(
            "the git command succeeds",
            actual=f"git {' '.join(args)} exited {proc.returncode}",
            needed=detail.strip()[:200],
        )
    return proc.stdout


def head_sha(repo):
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def state_prefixes(repo: Path, root: Path):
    """Repo-relative paths that belong to wf's own export."""
    rel = Path(root).resolve().relative_to(Path(repo).resolve())
    prefixes = [f"{rel.as_posix()}/{name}/" for name in STATE_SUBDIRS]
    exact = [f"{rel.as_posix()}/{name}" for name in STATE_FILES]
    return prefixes, exact


def is_wf_state_path(path: str, prefixes, exact) -> bool:
    return any(path.startswith(p) for p in prefixes) or path in exact


def dirty_paths(repo: Path, root: Path):
    """Repo-relative paths that differ from HEAD, excluding wf's own export."""
    prefixes, exact = state_prefixes(repo, root)
    raw = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=normal")
    out = []
    fields = [f for f in raw.split("\0") if f]
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        status, path = entry[:2], entry[3:]
        if status[0] in ("R", "C"):  # rename/copy carries a second NUL field
            index += 1
        if not is_wf_state_path(path, prefixes, exact):
            out.append(path)
    return sorted(out)


def ls_tree(repo: Path, commit: str) -> dict:
    """``{repo-relative path: blob sha}`` for every blob in ``commit``."""
    raw = git(repo, "ls-tree", "-r", "-z", commit)
    entries = {}
    for record in raw.split("\0"):
        if not record:
            continue
        meta, path = record.split("\t", 1)
        _mode, kind, sha = meta.split(" ", 2)
        if kind == "blob":
            entries[path] = sha
    return entries


def cat_blobs(repo: Path, shas) -> dict:
    """Blob contents by sha, in one ``git cat-file --batch``."""
    shas = list(dict.fromkeys(shas))
    if not shas:
        return {}
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch"],
        input=("\n".join(shas) + "\n").encode(),
        capture_output=True, check=False)
    if proc.returncode != 0:
        raise Integrity(
            "git cat-file could not read blobs recorded in the chain",
            detail=proc.stderr.decode("utf-8", "replace").strip()[:200],
        )
    out, buffer = {}, proc.stdout
    offset = 0
    for _ in shas:
        newline = buffer.index(b"\n", offset)
        header = buffer[offset:newline].decode()
        offset = newline + 1
        parts = header.split()
        if len(parts) != 3 or parts[1] != "blob":
            raise Integrity("git cat-file returned a non-blob", detail=header)
        sha, size = parts[0], int(parts[2])
        out[sha] = buffer[offset:offset + size]
        offset += size + 1  # trailing newline
    return out


def is_ancestor(repo: Path, older: str, newer: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer],
        capture_output=True, text=True, check=False)
    return proc.returncode == 0


def commit_exists(repo: Path, sha: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True, text=True, check=False)
    return proc.returncode == 0


# --------------------------------------------------------------------------
# export (D1) — the tracked evidence
#
# The chain's export is one canonical-bytes JSON line per event: append-only
# files whose git diffs are pure appends, self-verifying because every line
# carries its own hash, and merge-trivial. A committed SQLite binary is none
# of those. The export *is* the evidence, byte-for-byte the hashed payloads,
# so rebuild is deterministic.
#
# Not exported, by design: claims and sessions. They are transient
# coordination, not evidence, and losing them on rebuild is correct.


def write_if_changed(path: Path, data: bytes) -> bool:
    """Write only on a real change, so an export does not churn mtimes."""
    path = Path(path)
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def chain_bytes(store, wo_id: str) -> bytes:
    rows = store.list_events(wo_id)
    return "".join(r["payload"] + "\n" for r in rows).encode("utf-8")


def attempts_bytes(store, wo_id: str) -> bytes:
    import canonical

    lines = []
    for row in store.list_attempts(wo_id):
        lines.append(canonical.dumps(
            # sqlite3.Row iterates VALUES, not keys, so the
            # SIM118 rewrite turns every lookup into IndexError —
            # measured: it broke 305 workflow tests.
            {k: row[k] for k in row.keys()}))  # noqa: SIM118
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def export(ctx) -> list:
    """Regenerate the tracked export. Returns the repo-relative paths written."""
    import canonical

    ctx.ensure_dirs()
    written = []
    # Preserve keys this serializer does not own — commit_identity lives here
    # and rewriting the file from a literal would silently delete it.
    existing = {}
    if ctx.meta_path.exists():
        try:
            loaded = canonical.loads(ctx.meta_path.read_bytes())
            existing = loaded if isinstance(loaded, dict) else {}
        except ValueError:
            existing = {}
    meta = canonical.dumpb({**existing, "format_version": ctx.EXPORT_FORMAT_VERSION})
    if write_if_changed(ctx.meta_path, meta):
        written.append(ctx.meta_path)
    for row in ctx.store.list_work_orders():
        wo = row["wo_id"]
        if write_if_changed(ctx.chains_dir / f"{wo}.jsonl", chain_bytes(ctx.store, wo)):
            written.append(ctx.chains_dir / f"{wo}.jsonl")
        body = attempts_bytes(ctx.store, wo)
        if body and write_if_changed(ctx.attempts_dir / f"{wo}.jsonl", body):
            written.append(ctx.attempts_dir / f"{wo}.jsonl")
    repo = ctx.repo
    return [str(Path(p).resolve().relative_to(repo)) for p in written]


def export_paths(ctx) -> list:
    """The repo-relative paths a wf auto-commit is allowed to stage."""
    rel = Path(ctx.root).resolve().relative_to(Path(ctx.repo).resolve()).as_posix()
    return [f"{rel}/{name}" for name in (*STATE_SUBDIRS, "meta.json")]


def stage_export(ctx) -> list:
    """Stage only wf's own export paths, and report what that staged."""
    paths = [p for p in export_paths(ctx) if (Path(ctx.repo) / p).exists()]
    if paths:
        git(ctx.repo, "add", "--", *paths)
    staged = git(ctx.repo, "diff", "--cached", "--name-only", "-z", "--", *paths)
    return sorted(p for p in staged.split("\0") if p)


def commit_message(wo_id, stage, seq, head, summary, agent, sources) -> str:
    """PLAN §5's message format.

    ``Source: generated: wf`` covers the serialization only. Content
    provenance rides in the events and is re-emitted here as additional
    Source: trailers — the export embeds owner text, external artifacts and
    other tools' output, and the serializer is not their origin.
    """
    lines = [
        f"wf({wo_id}/{stage}): {summary}",
        "",
        f"WO: {wo_id}",
        f"Stage: {stage}",
        f"Event: {seq} {head[:8]}",
        "",
        "Source: generated: wf",
    ]
    for source in sources or []:
        lines.append(f"Source: {source}")
    if agent:
        lines.append(f"Co-Authored-By: {agent}")
    return "\n".join(lines) + "\n"
