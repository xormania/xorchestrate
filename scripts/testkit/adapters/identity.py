"""Versioned identities for explicit checker inputs, independent of Git ignores."""

from __future__ import annotations

import os
from pathlib import Path

from .evidence import canonical, digest, file_identity


def repository_path(root, name):
    """Return a lexical repository path whose resolved target stays inside it."""
    root = Path(root).resolve()
    path = Path(name)
    if not path.is_absolute():
        path = root / path
    relative = path.relative_to(root)
    if ".." in relative.parts:
        raise ValueError("Input path escapes the selected repository: " + str(name))
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Input resolves outside the selected repository: " + str(name))
    if not resolved.is_file():
        raise ValueError("Input is not a regular file: " + str(name))
    return path, relative.as_posix(), resolved


def effective_inputs(root, paths, *, scope=None, sources=()):
    """Bind membership, bytes and followed links without changing candidate hashes.

    ``paths`` is the checker's resolved selection plus its applicable config/tool
    source. Callers re-resolve that selection before verification, so additions
    and removals are measured as well as modifications to existing paths.
    """
    root = Path(root).resolve()
    files = {}
    for name in sorted(set(str(item) for item in paths)):
        path, relative, resolved = repository_path(root, name)
        links = []
        component = root
        for part in Path(relative).parts:
            component /= part
            if component.is_symlink():
                links.append({"path": component.relative_to(root).as_posix(),
                              "target": os.readlink(component)})
        identity = file_identity(path)
        actual = file_identity(resolved)
        files[relative] = {**identity, "content_sha256": actual["sha256"],
                           "resolved_path": resolved.relative_to(root).as_posix(),
                           "resolved_identity": actual, "links": links}
    source_names = sorted(set(str(item) for item in sources))
    if any(name not in files for name in source_names):
        raise ValueError("A selected source has no effective-input identity")
    payload = {"identity_schema": "testkit.effective-inputs.v1", "scope": scope or {},
               "sources": source_names, "files": files}
    return {**payload, "root": str(root), "content_sha256": digest(canonical(payload))}


def verify_effective_inputs(root, expected, paths=None, *, actual=None):
    """Compare a freshly resolved manifest, retaining changed membership details."""
    if actual is None:
        actual = effective_inputs(root, expected["files"] if paths is None else paths,
                                  scope=expected["scope"], sources=expected["sources"])
    names = set(expected["files"]) | set(actual["files"])
    return {"passed": expected["content_sha256"] == actual["content_sha256"],
            "expected": expected["content_sha256"], "actual": actual["content_sha256"],
            "changed_paths": sorted(name for name in names
                                    if expected["files"].get(name) != actual["files"].get(name)),
            "added": sorted(set(actual["files"]) - set(expected["files"])),
            "removed": sorted(set(expected["files"]) - set(actual["files"]))}
