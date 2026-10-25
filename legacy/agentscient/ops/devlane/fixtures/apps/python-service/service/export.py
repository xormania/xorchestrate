"""Archive and checksum subprocess seams."""

from __future__ import annotations

import subprocess
from pathlib import Path


def export_archive(root: Path, name: str) -> subprocess.CompletedProcess[str]:
    target = root / f"{name}.tgz"
    return subprocess.run(
        f"tar -czf {target} -C {root} {name}",
        capture_output=True,
        check=False,
        shell=True,
        text=True,
    )


def checksum_file(path: Path) -> str:
    result = subprocess.run(
        ["sha256sum", str(path)],
        capture_output=True,
        check=False,
        text=True,
    )
    return result.stdout.partition(" ")[0]
