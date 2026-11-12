"""Read immutable base blobs and both sides of a Git comparison without execution."""
from __future__ import annotations

import re
import subprocess


def _git(root, arguments, *, text=True):
    result = subprocess.run(["git", "--literal-pathspecs", *arguments], cwd=root,
                            capture_output=True, text=text, timeout=10, check=False)
    if result.returncode:
        raise ValueError("The diagnostic Git comparison is unavailable.")
    return result.stdout


def comparison(root, base_ref):
    """Resolve the base once and return old/new hunk coordinates for each path."""
    if not isinstance(base_ref, str) or not base_ref or base_ref.startswith("-"):
        raise ValueError("base_ref must be a Git reference.")
    commit = _git(root, ["rev-parse", "--verify", base_ref + "^{commit}"]).strip()
    command = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames"]
    paths = _git(root, [*command, "--name-only", "-z", commit, "--"]).split("\0")
    changes = []
    for path in filter(None, paths):
        old, new = [], []
        for line in _git(root, [*command, "--unified=0", commit, "--", path]).splitlines():
            match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if not match:
                continue
            for spans, offset in ((old, 1), (new, 3)):
                start, count = int(match[offset]), int(match[offset + 1] or 1)
                if count:
                    spans.append({"path": path, "start_line": start, "end_line": start + count - 1})
        changes.append({"path": path, "before": old, "after": new})
    return commit, changes


def base_blob(root, commit, path):
    """Return an exact regular-file Git blob, or None when the path was added."""
    listing = _git(root, ["ls-tree", "-z", commit, "--", path], text=False)
    if not listing:
        return None
    entries = listing.rstrip(b"\0").split(b"\0")
    if len(entries) != 1:
        raise ValueError("Ambiguous diagnostic base path.")
    metadata, listed_path = entries[0].split(b"\t", 1)
    mode, kind, blob = metadata.split(b" ")
    if listed_path.decode("utf-8") != path or mode not in {b"100644", b"100755"} or kind != b"blob":
        raise ValueError("Diagnostic base source is not a regular file.")
    return _git(root, ["cat-file", "blob", blob.decode("ascii")], text=False)
