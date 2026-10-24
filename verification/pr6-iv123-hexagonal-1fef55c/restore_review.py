#!/usr/bin/env python3
"""Verify publication parts and safely restore all regular evidence files.

Usage: python restore_review.py --package /path/to/downloaded/files --out /fresh/path
Only standard-library Python is required. Symlinks and transient IPC objects are
recorded in MANIFEST.json; they are deliberately not materialized on restoration.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def sha256_file(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_name(name):
    parsed = PurePosixPath(name)
    if (not name or not parsed.parts or name.startswith("/") or "\\" in name or "\x00" in name
            or any(part in {".", ".."} or ":" in part for part in parsed.parts)
            or str(parsed) != name):
        raise ValueError("Unsafe archive path: " + repr(name))
    return parsed


def restore(package, output):
    metadata = json.loads((package / "PACKAGE.json").read_text())
    manifest_bytes = (package / "MANIFEST.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != metadata["manifest_sha256"]:
        raise ValueError("Manifest checksum mismatch")
    manifest = json.loads(manifest_bytes)
    output = output.absolute()
    if output.exists():
        raise ValueError("Restoration destination must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    archive_hash = hashlib.sha256()
    archive_size = 0
    # The concatenated archive is temporary; publication only needs its parts.
    with tempfile.TemporaryFile(dir=output.parent) as joined:
        for part in metadata["parts"]:
            name = str(safe_name(part["name"]))
            if "/" in name:
                raise ValueError("Parts must be direct children of the package")
            path = package / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("Part is not a regular file: " + name)
            if path.stat().st_size != part["bytes"] or sha256_file(path) != part["sha256"]:
                raise ValueError("Part checksum mismatch: " + name)
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    joined.write(block)
                    archive_hash.update(block)
                    archive_size += len(block)
        if (archive_hash.hexdigest() != metadata["archive_sha256"]
                or archive_size != metadata["archive_bytes"]):
            raise ValueError("Combined archive checksum mismatch")
        joined.seek(0)
        expected = manifest["files"]
        with zipfile.ZipFile(joined) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or set(names) != set(expected) | {"MANIFEST.json"}:
                raise ValueError("Archive inventory differs from the complete manifest")
            for entry in entries:
                safe_name(entry.filename)
                mode = entry.external_attr >> 16
                if entry.is_dir() or not stat.S_ISREG(mode):
                    raise ValueError("Archive contains a non-regular entry: " + entry.filename)
                if entry.filename != "MANIFEST.json" and entry.file_size != expected[entry.filename]["bytes"]:
                    raise ValueError("Entry size mismatch: " + entry.filename)
            if archive.read("MANIFEST.json") != manifest_bytes:
                raise ValueError("Embedded manifest differs from published manifest")
            output.mkdir()
            # If extraction fails, leave the partial result for diagnosis. A
            # later run refuses that existing destination instead of replacing it.
            for entry in entries:
                destination = output / entry.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Fresh output plus exclusive creation avoids overwriting
                # earlier evidence or following a pre-existing link.
                digest = hashlib.sha256()
                with archive.open(entry) as source, destination.open("xb") as target:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(block)
                        digest.update(block)
                if entry.filename != "MANIFEST.json":
                    if digest.hexdigest() != expected[entry.filename]["sha256"]:
                        raise ValueError("Restored payload checksum mismatch: " + entry.filename)
                    # Preserve executable source fixtures without restoring
                    # setuid, group-write, owner IDs, or other special bits.
                    os.chmod(destination, 0o755 if expected[entry.filename]["mode"] & 0o111 else 0o644)
    return {"verified": True, "files": len(expected), "payload_bytes": sum(x["bytes"] for x in expected.values()),
            "archive_sha256": metadata["archive_sha256"], "output": str(output),
            "recorded_symlinks": len(manifest["symlinks"]), "excluded_special_files": len(manifest["special_files"])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(restore(args.package.resolve(), args.out), indent=2))
