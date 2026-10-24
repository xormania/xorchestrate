"""Seal or verify a completed evidence directory, without invoking providers."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def inventory(directory):
    root = Path(directory).resolve()
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Evidence must contain retained files, not external symlinks")
        if path.is_file() and path.name != "ARTIFACTS.sha256.json":
            result[str(path.relative_to(root))] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("seal", "verify"))
    p.add_argument("directory", type=Path)
    args = p.parse_args()
    target = args.directory / "ARTIFACTS.sha256.json"
    actual = inventory(args.directory)
    if args.command == "seal":
        with target.open("x") as f:
            json.dump(actual, f, indent=2, sort_keys=True)
            f.write("\n")
    elif json.loads(target.read_text()) != actual:
        raise ValueError("Evidence file set, byte count or digest changed")
    print(json.dumps({"command": args.command, "files": len(actual), "integrity_only": True}))


if __name__ == "__main__":
    main()
