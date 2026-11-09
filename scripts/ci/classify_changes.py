"""Classify a CI event's changed paths as documentation-only or code.

The decision is conservative: only an explicit allow-list of documentation paths
can skip heavy validation, and every uncertainty (unsupported event, missing or
null base, failed diff, empty change set) selects the full pipeline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

NULL_SHA = "0" * 40
HEX = frozenset("0123456789abcdef")
DIFF_EVENTS = frozenset({"pull_request", "push"})
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


def classify(paths) -> dict:
    """Decide for an observed change set; an empty set is treated as code."""
    paths = sorted(set(paths))
    code_paths = [path for path in paths if not is_documentation(path)]
    if not paths:
        return {"code": True, "reason": "no changed paths observed", "paths": paths, "code_paths": []}
    if code_paths:
        return {"code": True, "reason": "non-documentation paths changed", "paths": paths,
                "code_paths": code_paths}
    return {"code": False, "reason": "documentation-only change", "paths": paths, "code_paths": []}


def comparison_base(event: str, *, pull_request_base: str = "", push_before: str = "") -> str | None:
    """Select the commit to diff against, or None when the event requires a full run."""
    if event == "pull_request":
        base = pull_request_base
    elif event == "push":
        base = push_before
    else:
        return None
    base = (base or "").strip().lower()
    if len(base) != 40 or base == NULL_SHA or not set(base) <= HEX:
        return None
    return base


def changed_paths(root: Path, base: str, head: str, *, runner=subprocess.run) -> list[str] | None:
    """List changed paths, both sides of renames; None when Git cannot answer."""
    try:
        result = runner(["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"],
                        cwd=root, capture_output=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return [item for item in result.stdout.decode("utf-8", "surrogateescape").split("\0") if item]


def decide(event: str, *, root: Path, head: str = "HEAD", pull_request_base: str = "",
           push_before: str = "", runner=subprocess.run) -> dict:
    """Full decision record for one CI event."""
    context = {"event": event, "head": head or "HEAD"}
    base = comparison_base(event, pull_request_base=pull_request_base, push_before=push_before)
    if base is None:
        reason = ("comparison base unavailable" if event in DIFF_EVENTS
                  else "event always runs full validation")
        return {"code": True, "reason": reason, "base": None, "paths": [], "code_paths": [], **context}
    paths = changed_paths(root, base, context["head"], runner=runner)
    if paths is None:
        return {"code": True, "reason": "changed paths could not be computed", "base": base, "paths": [],
                "code_paths": [], **context}
    return {**classify(paths), "base": base, **context}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Classify CI changes as documentation-only or code.")
    parser.add_argument("--event", required=True)
    parser.add_argument("--pull-request-base", default="")
    parser.add_argument("--push-before", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--github-output", type=Path, help="Append code=true|false to this GitHub output file")
    args = parser.parse_args(argv)
    decision = decide(args.event, root=args.root.resolve(), head=args.head,
                      pull_request_base=args.pull_request_base, push_before=args.push_before)
    print(json.dumps(decision, indent=2, sort_keys=True))
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as stream:
            stream.write("code=" + ("true" if decision["code"] else "false") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
