"""Compile current source bytes without executing code or producing bytecode."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def check_manifest(manifest):
    """Record the precise bytes passed to the interpreter's syntax compiler."""
    root = Path(manifest["root"]).resolve()
    files, findings = [], []
    for name in manifest["sources"]:
        path = root / name
        try:
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError("Source resolves outside the selected repository")
            raw = path.read_bytes()
            sha256 = hashlib.sha256(raw).hexdigest()
            files.append({"path": name, "sha256": sha256})
            if sha256 != manifest["files"][name]["content_sha256"]:
                raise ValueError("Source changed after effective-input capture")
            # A code object is discarded; no source is executed and no .pyc is used.
            compile(raw, name, "exec", dont_inherit=True)
        except (OSError, SyntaxError, ValueError) as error:
            findings.append({"path": name, "message": str(error),
                             "line": getattr(error, "lineno", None),
                             "offset": getattr(error, "offset", None)})
    if not files:
        findings.append({"path": None, "message": "Syntax selection contains no source files"})
    return {"schema_version": 1, "status": "failed" if findings else "passed",
            "effective_input_sha256": manifest["content_sha256"],
            "files": files, "findings": findings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = check_manifest(json.loads(args.manifest.read_text()))
    encoded = json.dumps(report, sort_keys=True, indent=2) + "\n"
    args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
