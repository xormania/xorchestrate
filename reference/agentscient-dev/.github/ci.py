"""File selection and the required lane gate; Python stdlib and Git only."""

import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

RULES = json.loads(Path(__file__).with_name("ci-paths.json").read_text())
JOBS = tuple(RULES["jobs"])


def select(paths):
    """Union matching jobs. Unclassified files conservatively select everything."""
    selected = set()
    for path in paths:
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in RULES["ignore"]):
            continue
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in RULES["all"]):
            return set(JOBS)
        matches = {
            job for job, patterns in RULES["jobs"].items()
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
            and not any(fnmatch.fnmatchcase(path, pattern) for pattern in RULES.get("exclude", {}).get(job, []))
        }
        selected.update(matches or JOBS)
    return selected


def changed_paths(event_name, event, revision, root):
    """Diff the tested revision, including deletions and both sides of renames.

    PR checkouts are GitHub's merge commit: comparing to the event's base SHA
    excludes unrelated base-branch changes. Pushes use the entire before/after
    range; merge groups compare their base and synthetic head. None means all.
    """
    if event_name == "workflow_dispatch":
        return None
    if event_name == "pull_request":
        base = event["pull_request"]["base"]["sha"]
        head = revision
    elif event_name == "push":
        base, head = event["before"], event["after"]
    elif event_name == "merge_group":
        base = event["merge_group"]["base_sha"]
        head = event["merge_group"]["head_sha"]
    else:
        raise ValueError("Unsupported CI event: " + event_name)
    if any(not re.fullmatch(r"[0-9a-f]{40}", sha or "") or sha == "0" * 40 for sha in (base, head)):
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", base, head, "--"],
            cwd=root, check=True, capture_output=True,
        )
    except subprocess.CalledProcessError:
        # A force-push can make its previous tip unavailable even with a full
        # checkout. Never interpret an unavailable comparison as no changes.
        print("::warning::Change comparison unavailable; running all CI jobs.")
        return None
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def gate(needs):
    """Only a successful selector can authorize a skipped job."""
    changes = needs.get("changes", {})
    if changes.get("result") != "success":
        raise ValueError("File selection did not succeed")
    outputs = changes.get("outputs", {})
    for job in JOBS:
        selected = outputs.get(job)
        if selected not in {"true", "false"}:
            raise ValueError("Missing or invalid selection for " + job)
        expected = "success" if selected == "true" else "skipped"
        actual = needs.get(job, {}).get("result")
        if actual != expected:
            raise ValueError(f"{job}: expected {expected}, got {actual}")
        print(f"{job}: {actual}")


def main():
    if sys.argv[1:] == ["gate"]:
        gate(json.loads(os.environ["NEEDS_JSON"]))
        return
    if sys.argv[1:] != ["select"]:
        raise ValueError("Usage: python3 .github/ci.py select|gate")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    paths = changed_paths(os.environ["GITHUB_EVENT_NAME"], event, os.environ["GITHUB_SHA"], Path.cwd())
    selected = set(JOBS) if paths is None else select(paths)
    outputs = "".join(f"{job}={str(job in selected).lower()}\n" for job in JOBS)
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(outputs)
    detail = "full suite (manual run or unavailable comparison)" if paths is None else f"{len(paths)} changed paths"
    summary = f"CI selection: {detail}\n\n" + "\n".join(
        f"- `{job}`: {'run' if job in selected else 'skip'}" for job in JOBS
    ) + "\n"
    print(summary)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
        output.write(summary)


if __name__ == "__main__":
    main()
