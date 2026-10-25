"""Receipts and the frozen set — the TDD gate (PLAN §3.3).

The manifest is the canonical JSON of sorted ``(path, sha256(content))``
pairs over the stage's declared **test scope and harness scope** — the
inputs that select and drive tests, not only the tests.  Deselecting a
frozen test by editing ``Cargo.toml`` therefore changes the frozen hash,
which was the point of including the harness.

The manifest binds what is *declared*.  Whether the declaration is adequate
is merit, and merit belongs to the review stage — as does whether a red
receipt's nonzero exit came from an assertion or a compile error.  The
receipt records exit code and output so a reviewer can judge; the engine
does not try to.
"""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import canonical
import gitio
from errors import Refusal

# Full outputs go to the CAS; only a short tail rides inline in the event.
EVENT_TAIL_BYTES = 400


def glob_to_regex(pattern: str):
    """Translate one scope glob.

    ``*`` and ``?`` stay inside a path segment; ``**`` spans segments.  Doing
    this by hand rather than through ``Path.glob`` is what lets the identical
    predicate run over a filesystem walk and over a ``git ls-tree`` listing —
    and those two must agree, or ``verify --deep`` would disagree with the
    receipt it is checking.
    """
    segments = pattern.split("/")
    parts = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        if segment == "**":
            parts.append(".*" if last else "(?:[^/]+/)*")
            continue
        rendered = ""
        for char in segment:
            if char == "*":
                rendered += "[^/]*"
            elif char == "?":
                rendered += "[^/]"
            else:
                rendered += re.escape(char)
        parts.append(rendered if last else rendered + "/")
    return re.compile("^" + "".join(parts) + "$")


class Scope:
    """The declared test and harness scope of one gate-kind spec."""

    def __init__(self, patterns):
        self.patterns = list(patterns)
        self._matchers = [glob_to_regex(p) for p in self.patterns]

    def matches(self, path: str) -> bool:
        return any(m.match(path) for m in self._matchers)

    def __bool__(self):
        return bool(self.patterns)


def manifest_from_commit(repo: Path, commit: str, scope: Scope):
    """Sorted ``[path, sha256]`` pairs for the blobs in ``commit`` that are in scope."""
    entries = {p: s for p, s in gitio.ls_tree(repo, commit).items() if scope.matches(p)}
    blobs = gitio.cat_blobs(repo, entries.values())
    return sorted(
        [path, canonical.sha256_hex(blobs[sha])] for path, sha in entries.items()
    )


def manifest_from_worktree(repo: Path, scope: Scope):
    """Sorted ``[path, sha256]`` pairs for in-scope files in the working tree."""
    repo = Path(repo)
    out = []
    for path in repo.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(repo).as_posix()
        if rel.startswith(".git/"):
            continue
        if scope.matches(rel):
            out.append([rel, canonical.sha256_hex(path.read_bytes())])
    return sorted(out)


def manifest_digest(manifest) -> str:
    return canonical.digest(manifest)


def compute_manifest(repo: Path, spec, *, commit=None, worktree=False):
    scope = Scope(spec.test_scope + spec.harness_scope)
    if worktree:
        manifest = manifest_from_worktree(repo, scope)
    else:
        if commit is None:
            raise Refusal(
                "the repository has a commit to hash the test scope against",
                needed="at least one commit, or --allow-dirty",
            )
        manifest = manifest_from_commit(repo, commit, scope)
    return manifest, manifest_digest(manifest)


def run(argv, cwd, *, binary=False):
    """Run a wrapped command, capturing everything it said."""
    started = time.monotonic()
    try:
        options = {"text": not binary}
        if not binary:
            options["errors"] = "replace"
        proc = subprocess.run(list(argv), cwd=str(cwd), capture_output=True,
                              stdin=subprocess.DEVNULL, check=False, **options)
    except FileNotFoundError:
        raise Refusal(
            "the command exists",
            actual="command was not found",
            needed="install it or correct the command name",
        ) from None
    except PermissionError:
        raise Refusal(
            "the command is executable", actual="command was not executable",
            needed="correct its permissions or choose an executable command",
        ) from None
    duration = int((time.monotonic() - started) * 1000)
    return proc.returncode, proc.stdout, proc.stderr, duration


def toolchain_string(spec, cwd):
    """The toolchain version a receipt records, or ``None`` when the spec
    declares none.  A declared-but-unrunnable toolchain refuses: recording a
    blank where a version was promised would be worse than stopping."""
    argv = spec.toolchain_argv
    if not argv:
        return None
    code, out, err, _ = run(argv, cwd)
    if code != 0:
        raise Refusal(
            "the declared toolchain command succeeds",
            actual=f"{' '.join(argv)} exited {code}",
            needed=(err or out).strip()[:200] or "a runnable toolchain",
        )
    return out.strip() or err.strip()


def tail(text: str) -> str:
    return (text or "")[-EVENT_TAIL_BYTES:]
