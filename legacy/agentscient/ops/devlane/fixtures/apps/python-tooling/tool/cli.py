"""A two-verb fixture lever that speaks the refusal wire."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from tool.service import read_records, record_path, write_records

IDENTITY = re.compile(r"^.+ <[^<>\s]+>$")


def run_helper(name: str) -> int:
    return os.system(f"printf '%s\\n' {name} >/dev/null")


def run_helper_safely(name: str) -> int:
    return subprocess.run(["printf", "%s\n", name], capture_output=True, check=False).returncode


def render_count(count: int | None) -> str:
    if not count:
        return "no record"
    return f"count={count}"


def render_optional_label(label: str | None) -> str:
    if label is None:
        return "no label"
    return label


def write_failure_log(path: Path) -> None:
    path.write_text(f"write failed: environment={dict(os.environ)}\n", encoding="utf-8")


def write_safe_log(path: Path) -> None:
    path.write_text("write failed: token=[REDACTED]\n", encoding="utf-8")


def read_store(path: Path) -> list[dict[str, object]]:
    try:
        return read_records(path)
    except OSError:
        raise


def refuse(kind: str, expected: str, found: str, needed: str) -> int:
    print(f"{kind}: expected {expected}; found {found}; needed {needed}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in {"read", "write"}:
        return refuse("arguments", "read or write", repr(args), "choose one verb")
    if args[0] == "read":
        if len(args) != 3:
            return refuse("arguments", "read ROOT NAME", repr(args), "supply root and name")
        root, name = Path(args[1]), args[2]
        run_helper(name)
        try:
            records = read_records(record_path(root, name))
        except OSError as error:
            return refuse("store", "a readable record", str(error), "repair the store")
        count = records[-1].get("count") if records else None
        print(render_count(count if isinstance(count, int) else None))
        return 0
    if len(args) != 4:
        return refuse("arguments", "write ROOT NAME COUNT", repr(args), "supply root, name, count")
    agent = os.environ.get("WF_AGENT", "")
    if IDENTITY.fullmatch(agent) is None:
        return refuse("identity", "Name <address>", agent or "unset", "export WF_AGENT")
    root, name = Path(args[1]), args[2]
    try:
        write_records(record_path(root, name), [{"count": int(args[3])}])
    except (OSError, ValueError) as error:
        write_failure_log(root / "write-error.log")
        return refuse("store", "a writable record", str(error), "repair the store")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
