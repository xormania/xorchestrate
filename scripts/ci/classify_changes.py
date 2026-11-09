"""Classify a pull request's changed paths as documentation-only or code.

The decision is conservative: only regular files on an explicit allow-list of
documentation paths can skip heavy validation. Every event other than a pull
request, and every uncertainty (missing or null base, failed diff, unparsable
diff, empty change set), selects the full pipeline. Pushes always run in full so
the integrated tree is validated even when a later push cancels an earlier run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

NULL_SHA = "0" * 40
HEX = frozenset("0123456789abcdef")
DIFF_EVENT = "pull_request"
# Absent (added/deleted side) or a regular file. Symlinks (120000), gitlinks (160000)
# and anything else are code: copying or traversing them can break validation.
REGULAR_MODES = frozenset({"000000", "100644", "100755"})
# Allow-list, in order: top-level *.md, anything under docs/, *.md anywhere under examples/.
DOCUMENTATION_RULES = (
    ("top-level markdown", lambda path: "/" not in path and path.endswith(".md")),
    ("docs tree", lambda path: path.startswith("docs/")),
    ("examples markdown", lambda path: path.startswith("examples/") and path.endswith(".md")),
)
# Documentation-shaped files that validation reads as inputs; changing them is a code change.
VALIDATION_INPUTS = frozenset({"docs/implementation/owner-brief.md"})


def is_documentation(path: str) -> bool:
    """True only for allow-listed prose that no validation step reads."""
    if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        return False
    if path in VALIDATION_INPUTS:
        return False
    return any(rule(path) for _, rule in DOCUMENTATION_RULES)


def classify(paths, *, special=()) -> dict:
    """Decide for an observed change set; an empty set is treated as code.

    ``special`` names changed paths whose old or new entry is not a regular file.
    """
    paths = sorted(set(paths) | set(special))
    special = set(special)
    code_paths = [path for path in paths if path in special or not is_documentation(path)]
    if not paths:
        return {"code": True, "reason": "no changed paths observed", "paths": paths, "code_paths": []}
    if code_paths:
        return {"code": True, "reason": "non-documentation paths changed", "paths": paths,
                "code_paths": code_paths}
    return {"code": False, "reason": "documentation-only change", "paths": paths, "code_paths": []}


def comparison_base(event: str, *, pull_request_base: str = "") -> str | None:
    """Select the commit to diff against, or None when the event requires a full run."""
    if event != DIFF_EVENT:
        return None
    base = (pull_request_base or "").strip().lower()
    if len(base) != 40 or base == NULL_SHA or not set(base) <= HEX:
        return None
    return base


def parse_raw(output: bytes) -> list[tuple[str, str, str]] | None:
    """Parse ``git diff --raw -z --no-renames`` into (path, old_mode, new_mode); None if malformed."""
    fields = output.decode("utf-8", "surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        return None
    entries = []
    for meta, path in zip(fields[::2], fields[1::2]):
        parts = meta.split(" ")
        if len(parts) != 5 or not parts[0].startswith(":") or not path:
            return None
        entries.append((path, parts[0][1:], parts[1]))
    return entries


def changed_entries(root: Path, base: str, head: str, *,
                    runner=subprocess.run) -> list[tuple[str, str, str]] | None:
    """List changed entries with their modes; None when Git cannot answer."""
    try:
        result = runner(["git", "diff", "--raw", "-z", "--no-renames", "--no-abbrev", base, head, "--"],
                        cwd=root, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse_raw(result.stdout)


def decide(event: str, *, root: Path, head: str = "HEAD", pull_request_base: str = "",
           runner=subprocess.run) -> dict:
    """Full decision record for one CI event."""
    context = {"event": event, "head": head or "HEAD"}
    base = comparison_base(event, pull_request_base=pull_request_base)
    if base is None:
        reason = "comparison base unavailable" if event == DIFF_EVENT else "event always runs full validation"
        return {"code": True, "reason": reason, "base": None, "paths": [], "code_paths": [], **context}
    entries = changed_entries(root, base, context["head"], runner=runner)
    if entries is None:
        return {"code": True, "reason": "changed paths could not be computed", "base": base, "paths": [],
                "code_paths": [], **context}
    special = [path for path, old, new in entries if old not in REGULAR_MODES or new not in REGULAR_MODES]
    return {**classify([path for path, _, _ in entries], special=special), "base": base, **context}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Classify CI changes as documentation-only or code.")
    parser.add_argument("--event", required=True)
    parser.add_argument("--pull-request-base", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path, help="Append code=true|false to this GitHub output file")
    args = parser.parse_args(argv)
    decision = decide(args.event, root=args.root.resolve(), head=args.head,
                      pull_request_base=args.pull_request_base)
    print(json.dumps(decision, indent=2, sort_keys=True))
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write("code=" + ("true" if decision["code"] else "false") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
