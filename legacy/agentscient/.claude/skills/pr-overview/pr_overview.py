#!/usr/bin/env python3
"""Every open pull request, and the one thing each is waiting on.

    python3 .claude/skills/pr-overview/pr_overview.py [--repo O/N] [--json]
    python3 .claude/skills/pr-overview/pr_overview.py --selftest

A pull request's state lives in four different API objects and no single
one of them answers "can this merge". Reading a subset and reporting
anyway is how a session spends forty minutes on the wrong PR, so this
reads all of them and says which is blocking.

SIX THINGS IT REFUSES TO GUESS, each because guessing one cost a
session or a review finding:

  checks        `statusCheckRollup` is APPEND-ONLY: a re-run appends
                beside the attempt it supersedes, so
                `any(conclusion == "FAILURE")` answers "did anything
                ever fail here", which reads exactly like "is it red
                now". This uses `gh pr checks`, which collapses to the
                latest attempt per name, as the web UI does.

  completeness  `gh pr checks` lists what HAS reported, and a nonempty
                all-pass list reads exactly like "CI is green" while
                the matrix jobs are still registering: one passing
                check was read as all of them. What SHOULD report is
                asked of the one place in the tree that already knows:
                this checkout's trusted `ci_contexts.py` derives the
                check-run names a branch ruleset would list, from the
                two workflow files and the check registry -- including
                the names a templated job name hides -- and refuses
                rather than returning a short list. This script reads
                none of that itself; a second reader would agree with
                the first right up to the workflow edit that made them
                disagree.

  whose tree    that derivation is run against EACH PULL REQUEST'S OWN
                HEAD, fetched from the forge -- never this checkout,
                and never through whatever repository the caller's cwd
                happens to sit in. One set shared across every row
                waits forever for a check a later PR added and never
                reports one a later PR removed; running the overview
                from a branch that adds a check imposed it on every
                open PR. A head this reader cannot fetch or derive is
                `cannot tell`, never a quiet fall back to the caller's
                set.

  skipping      `gh pr checks` buckets a run as pass, fail, pending,
                skipping or cancel. A SKIPPED check HAS reported -- so
                the completeness comparison counts it present -- and
                it has not passed. WHICH name skipped decides it: "an
                extra skipped" and "an expected one skipped while an
                extra passed" have identical bucket counts and
                opposite verdicts, so the per-name buckets are kept
                rather than only totalled.

  review age    a review is pinned to the commit it read. Compare its
                commit against the head or you will report a pass that
                was true two pushes ago -- and a review that looks
                stale may simply have been ANSWERED, which is the
                opposite conclusion. Both are shown.

  threads       "no comments" is not "nothing to do": automated
                reviewers answer with inline comments on diff lines,
                and a thread stays open until someone resolves it.

Nothing here writes. It reads, and refuses when it cannot read: no
`gh`, no auth, or an API error exits non-zero rather than printing an
overview it could not produce.
"""

from __future__ import annotations

import argparse
import atexit
import functools
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

#: The checkout this script lives in: the tree `blocking` derives
#: from when no caller names one, and a fallback `collect` never
#: takes. The set comes from a tree at all because the forge cannot
#: supply it -- the forge lists what has reported, which is exactly
#: the number that cannot answer "and is that all of them" -- but
#: WHICH tree is per pull request, not per session (`head_root`).
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ops" / "conductor" / "tools"))
import laneconf  # noqa: E402

#: Under the root: the derivation CI's own parser -- the check-run names
#: GitHub renders for every job in the always-on workflow, refusing any
#: shape it cannot read. Asked, not copied.
CI_CONTEXTS = Path("ops/devlane/workflow/checks/ci_contexts.py")
#: The only paths a head is fetched for: everything the derivation
#: reads lives under one of them, and the rest of a repository archive
#: is dropped rather than written out.
WORKFLOW_DATA = (".github/workflows/ci.yml",)
GATE_KIND_DATA = "ops/devlane/workflow/gate-kinds/"

#: PAGED, both connections. `first: 100` on reviewThreads meant a PR
#: with more threads than that reported fewer unresolved ones than it
#: has — and could report "ready to merge" while the ones past the
#: boundary sat open. `last: 20` on reviews could hide the substantive
#: review behind twenty reply records. Both bounds failed toward
#: "nothing to worry about", in the tool whose only job is saying what
#: to worry about (Codex, PR #43).
THREADS_QUERY = """
query($owner: String!, $name: String!, $pr: Int!,
      $threadCursor: String, $reviewCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $threadCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { isResolved isOutdated path line }
      }
      reviews(first: 100, after: $reviewCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { submittedAt commit { oid } author { login } state body }
      }
    }
  }
}
"""


class Unavailable(Exception):
    """The forge could not be read. Never rendered as an empty overview."""


def gh(*argv, parse=True):
    proc = subprocess.run(("gh",) + argv, capture_output=True, text=True,
                          check=False)
    if proc.returncode != 0:
        raise Unavailable(f"gh {' '.join(argv[:2])}: "
                          f"{(proc.stderr or proc.stdout).strip()[:200]}")
    if not parse:
        return proc.stdout
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise Unavailable(f"gh {' '.join(argv[:2])}: not JSON") from exc


def buckets(number, repo):
    """{bucket: count, "names": [every check name]} from the LATEST
    attempt per check name.

    `gh pr checks` exits non-zero when anything is failing or pending,
    which is a verdict rather than an error, so its status is ignored
    and the rows are read instead.

    The names are kept, not just counted: `blocking` compares them
    with the set that should report, and a count cannot be compared
    with anything. The per-name BUCKETS are kept too, as `runs`: a
    skipped check has reported, so the names alone call it present,
    and the totals alone cannot say whether the one that skipped was
    an expected check or a stranger. Empty rows stay `{}` -- "no
    checks" keeps its shape.
    """
    proc = subprocess.run(
        ("gh", "pr", "checks", str(number), "--repo", repo,
         "--json", "name,bucket"),
        capture_output=True, text=True, check=False)
    if not proc.stdout.strip():
        # No checks configured is a real answer; an unreadable one is
        # not, and the two must not print the same.
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()
            if "no checks reported" in detail.lower():
                return {}
            raise Unavailable(f"gh pr checks {number}: "
                              f"{detail[:200]}")
        return {}
    try:
        rows = json.loads(proc.stdout)
    except ValueError as exc:
        raise Unavailable(f"gh pr checks {number}: not JSON") from exc
    counts = {}
    for row in rows:
        counts[row["bucket"]] = counts.get(row["bucket"], 0) + 1
    counts["names"] = sorted(row["name"] for row in rows)
    counts["runs"] = sorted(
        ({"name": row["name"], "bucket": row["bucket"]} for row in rows),
        key=lambda run: (run["name"], run["bucket"]))
    return counts


def review_state(reviews, head):
    """(latest reviewer, current | stale | none).

    A review with an EMPTY BODY is not a verdict. Replying to one
    inline comment creates a review record of its own, so a PR whose
    threads have been answered accumulates empty reviews authored by
    whoever answered them -- and taking the latest of those reported
    the PR as freshly reviewed BY ITS OWN AUTHOR. Measured on this repo:
    five such records sat above the reviewer's real one, turning "the
    review predates the head" into "ready to merge".
    """
    # By STATE, not by body. An approval with no summary is a real
    # verdict with an empty body, and dropping every empty-bodied review
    # reported it as "never reviewed" — the fix for reply-shaped records
    # reaching one field too far (Codex, PR #43). A bare COMMENTED with
    # no body is the reply artifact; APPROVED and CHANGES_REQUESTED are
    # verdicts whether or not anyone typed a summary.
    VERDICTS = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")
    dated = [r for r in reviews
             if r.get("submittedAt")
             and ((r.get("body") or "").strip() or r.get("state") in VERDICTS)]
    if not dated:
        return None, "none"
    latest = max(dated, key=lambda r: r["submittedAt"])
    oid = (latest.get("commit") or {}).get("oid") or ""
    who = (latest.get("author") or {}).get("login") or "?"
    return who, ("current" if oid == head else "stale")


def registry(root):
    """The tracked check registry, parsed strictly as JSON data.

    Nothing here lists a name. A check added to a gate-kind spec with
    "ci": true reaches the matrix, and therefore this overview, with
    no edit to this file -- a name listed here instead would be the
    hand-listed matrix PLAN §7 retired.
    """
    directory = root / GATE_KIND_DATA
    if not directory.is_dir():
        raise Unavailable(
            f"{directory} is missing, so the check registry is unknown")
    checks = []
    for path in sorted(directory.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
            kind = spec["kind"]
            version = spec["version"]
            entries = spec["checks"]
            if (not isinstance(kind, str) or not isinstance(version, int)
                    or not isinstance(entries, list)):
                raise TypeError("invalid registry fields")
            for entry in entries:
                checks.append({"kind": kind, "kind_version": version,
                               "name": entry["name"], "class": entry["class"],
                               "argv": list(entry["argv"]),
                               "ci": bool(entry.get("ci", False))})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise Unavailable(f"{path}: invalid gate-kind data: {exc}") from exc
    if not checks:
        raise Unavailable(f"{directory} has no checks")
    checks.sort(key=lambda c: (c["kind"], c["kind_version"], c["name"]))
    return {"checks": checks,
            "ci_matrix": sorted({c["name"] for c in checks if c["ci"]})}


def ci_contexts():
    """The trusted local module that derives check-run names.

    Loaded by path rather than imported: the lane's apps are standalone
    scripts, not packages, and this script lives outside them.
    """
    path = ROOT / CI_CONTEXTS
    if not path.is_file():
        raise Unavailable(f"{path} is missing, so nothing says what "
                          f"should report")
    spec = importlib.util.spec_from_file_location("ci_contexts", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def expected_checks(root=ROOT):
    """CI acceptance check names for a pull request, as a frozenset.

    The trusted local ci_contexts.py reads the head's always-on workflow.
    The final acceptance job covers its conditional dependencies; those jobs
    can be intentionally skipped. Every returned context must still report.

    ci_contexts.py refuses a shape it cannot read or an empty jobs mapping.
    That refusal reaches the reader as `cannot tell`, never shortened into a
    smaller set.
    """
    contexts = ci_contexts()
    try:
        names = contexts.workflow_job_names(root=root)
    except contexts.DerivationError as exc:
        raise Unavailable(f"ci_contexts.py refused: {exc}") from exc
    if not names:
        raise Unavailable("ci_contexts.py derived no check names, so "
                          "nothing says what should report")
    return frozenset(names)


def head_archive(repo, sha):
    """The repository at one commit, as archive bytes, from the forge.

    ONE request per head. The alternative -- a recursive tree listing
    plus a blob request for every file under it -- is 372 requests for
    this repository's derivation paths alone, per pull request, and
    the whole archive compresses smaller than the paths it carries.
    """
    proc = subprocess.run(("gh", "api", f"repos/{repo}/tarball/{sha}"),
                          capture_output=True, check=False)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise Unavailable(f"gh api tarball/{sha[:7]}: {detail[:200]}")
    if not proc.stdout:
        raise Unavailable(f"gh api tarball/{sha[:7]}: empty archive")
    return proc.stdout


@functools.cache
def head_root(repo, sha):
    """A directory holding ONE head commit's derivation files.

    Not this checkout, and not ambient git: a pull request's head is
    routinely a commit this clone has never fetched, and reading it
    out of whatever repository the caller's cwd happens to be in
    answers for the wrong tree -- or for no tree at all, when the
    overview runs from outside a repository. Cached per head, so two
    pull requests on one commit cost one fetch, and removed at exit.
    """
    dest = Path(tempfile.mkdtemp(prefix="pr-overview-head-"))
    atexit.register(shutil.rmtree, dest, ignore_errors=True)
    base = dest.resolve()
    kept = 0
    with tarfile.open(fileobj=io.BytesIO(head_archive(repo, sha)),
                      mode="r:*") as tar:
        for member in tar:
            # Files only: a symlink or a device node in an archive is
            # nothing this needs and nothing it will create.
            if not member.isfile():
                continue
            # The archive is rooted at a single `<repo>-<sha7>/`.
            relative = "/".join(PurePosixPath(member.name).parts[1:])
            if relative not in WORKFLOW_DATA and not (
                    relative.startswith(GATE_KIND_DATA)
                    and relative.endswith(".json")):
                continue
            target = (base / relative).resolve()
            if base not in target.parents:
                raise Unavailable(f"tarball/{sha[:7]}: {member.name} escapes "
                                  f"the directory it would be read from")
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            target.write_bytes(b"" if extracted is None else extracted.read())
            kept += 1
    if not kept:
        raise Unavailable(f"tarball/{sha[:7]} carries none of the workflow "
                          "YAML or gate-kind JSON data, so nothing in it "
                          f"says what should report")
    return dest


def blocking(pr, expected=None, root=ROOT, unreadable=None):
    """The one sentence a triaging reader needs. Order is deliberate.

    `expected` is the set `expected_checks()` returns; None reads it
    from the tree at `root` -- this checkout, unless the selftest hands
    over an empty one. `collect` hands over the PULL REQUEST'S OWN set
    instead, and on failure hands over `unreadable`, the refusal it
    already hit fetching that head: reported here, at the point the
    comparison would have happened, so a verdict that outranks it
    still wins. Four sentences that are NOT "ready to merge" and NOT a
    fault either: "checks incomplete" (something that should report
    has not), "CI cancelled", "CI skipped" (something that should have
    run did not), and "cannot tell" (this reader could not establish
    what should report, so it does not claim to). A reader who cannot
    tell must say so, not say "ready".
    """
    checks = pr["checks"]
    if checks.get("fail"):
        return f"CI red — {checks['fail']} failing"
    if pr["unresolved"]:
        return f"answer {pr['unresolved']} unresolved thread(s)"
    if pr.get("checks_unreadable"):
        return f"cannot tell — checks could not be read: {pr['checks_unreadable']}"
    if checks.get("pending"):
        return f"waiting on CI — {checks['pending']} pending"
    # A cancelled attempt with no successor is not a pass; without this
    # line the completeness verdict below would count it as reported,
    # which it is, and read that as passed, which it is not.
    if checks.get("cancel"):
        return (f"CI cancelled — {checks['cancel']} cancelled; re-run before "
                f"reading the rest")
    if not checks:
        return "no checks reported"
    # ONE passing check is a nonempty all-pass result. So is one of
    # five. What has reported is compared with what should, by name --
    # a count is satisfied by any five checks, including four right
    # ones and a stranger.
    if "names" not in checks:
        return ("cannot tell — check names were not collected, so what "
                "reported was never compared with what should have")
    if expected is None:
        if unreadable is not None:
            return (f"cannot tell — what should report could not be read: "
                    f"{unreadable}")
        try:
            expected = expected_checks(root)
        except Unavailable as exc:
            return f"cannot tell — what should report could not be read: {exc}"
    missing = sorted(expected - set(checks["names"]))
    if missing:
        return (f"checks incomplete — {len(missing)} of {len(expected)} "
                f"expected not reported: {', '.join(missing)} "
                f"(still registering, or never ran)")
    # Everything expected has reported BY NAME -- and a skipped check
    # reported. `cancel` is refused above for that same reason and
    # `skipping` is its sibling, but this one needs the per-name
    # buckets: a skipped stranger blocks nothing, a skipped expected
    # check makes the last sentence false, and the two count alike.
    if checks.get("skipping"):
        runs = checks.get("runs")
        if not runs:
            return (f"cannot tell — {checks['skipping']} check(s) skipping "
                    f"and no per-check buckets were collected, so a skipped "
                    f"expected check cannot be told from a skipped extra")
        skipped = sorted(set(expected) & {run["name"] for run in runs
                                          if run["bucket"] == "skipping"})
        if skipped:
            return (f"CI skipped — {len(skipped)} expected check(s) skipped: "
                    f"{', '.join(skipped)}; a skipped check has not passed")
    if pr["review"] == "none":
        return "never reviewed"
    if pr["review"] == "stale":
        return "review predates the head — re-request or confirm answered"
    if pr["merge_state"] != "CLEAN":
        return f"blocked: {pr['merge_state']}"
    return f"ready to merge — all {len(expected)} expected check(s) passed"


#: `gh pr list` fetches 30 by default and says nothing when it stops.
#: A repository with more open PRs than this would have had the rest
#: silently omitted — from the one command whose purpose is answering
#: what is open (Codex, PR #43). High enough to be a real answer, and
#: hitting it is REPORTED rather than assumed not to happen.
LIST_LIMIT = 500


def review_pages(repo, number):
    """(threads, reviews) for one PR, every page of both."""
    owner, name = repo.split("/", 1)
    threads, reviews = [], []
    thread_cursor = review_cursor = None
    more_threads = more_reviews = True
    while more_threads or more_reviews:
        argv = ["api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}",
                "-F", f"pr={number}", "-f", f"query={THREADS_QUERY}"]
        if thread_cursor:
            argv += ["-F", f"threadCursor={thread_cursor}"]
        if review_cursor:
            argv += ["-F", f"reviewCursor={review_cursor}"]
        node = gh(*argv)["data"]["repository"]["pullRequest"]

        if more_threads:
            page = node["reviewThreads"]
            threads += page["nodes"]
            more_threads = page["pageInfo"]["hasNextPage"]
            thread_cursor = page["pageInfo"]["endCursor"]
        if more_reviews:
            page = node["reviews"]
            reviews += page["nodes"]
            more_reviews = page["pageInfo"]["hasNextPage"]
            review_cursor = page["pageInfo"]["endCursor"]
        # A page that reports more but hands back no cursor would loop
        # forever; stopping quietly there would be the same silence
        # this pagination exists to remove.
        if (more_threads and not thread_cursor) or \
                (more_reviews and not review_cursor):
            raise Unavailable(
                f"PR #{number}: the API reports another page and gives no "
                f"cursor for it, so the listing cannot be completed")
    return threads, reviews


def collect(repo):
    listing = gh("pr", "list", "--repo", repo, "--state", "open",
                 "--limit", str(LIST_LIMIT), "--json",
                 "number,title,headRefName,baseRefName,headRefOid,"
                 "mergeable,mergeStateStatus,isDraft,updatedAt")
    if len(listing) >= LIST_LIMIT:
        raise Unavailable(
            f"{len(listing)} open pull request(s) reached the fetch limit of "
            f"{LIST_LIMIT}; the listing may be incomplete and an incomplete "
            f"overview must not read like a complete one")
    out = []
    unreadable_rows = []
    for pr in listing:
        head = pr["headRefOid"]
        try:
            threads, reviews = review_pages(repo, pr["number"])
            who, age = review_state(reviews, head)
            checks = buckets(pr["number"], repo)
            expected = expected_checks(head_root(repo, head))
            unreadable = None
        except Unavailable as exc:
            threads, who, age, checks, expected = [], None, "unreadable", {}, None
            unreadable = str(exc)
            unreadable_rows.append(unreadable)
        row = {
            "number": pr["number"], "title": pr["title"],
            "head_branch": pr["headRefName"], "base": pr["baseRefName"],
            "head": head[:7], "draft": pr["isDraft"],
            "merge_state": pr["mergeStateStatus"],
            "mergeable": pr["mergeable"], "updated": pr["updatedAt"][:16],
            "checks": checks, "checks_unreadable": unreadable,
            "threads": len(threads),
            "unresolved": sum(1 for t in threads if not t["isResolved"]),
            "open_paths": sorted({t["path"] for t in threads
                                  if not t["isResolved"]}),
            "reviewer": who, "review": age,
        }
        # Judged against ITS OWN head's set, fetched per head:
        # `blocking(row)` would read this checkout for every row.
        row["blocking"] = blocking(row, expected, unreadable=unreadable)
        out.append(row)
    if listing and len(unreadable_rows) == len(listing):
        raise Unavailable(
            f"could read none of {len(listing)} open pull request(s); "
            f"first: {unreadable_rows[0]}"
        )
    return sorted(out, key=lambda r: r["number"])


def render(rows, repo):
    lines = [f"{repo} — {len(rows)} open pull request(s)"]
    if not rows:
        # An empty overview must say it is empty. Silence here reads as
        # "nothing to do" whether or not anything was looked at.
        lines.append("  none open.")
        return "\n".join(lines)
    for r in rows:
        # Buckets only. `names` and `runs` are what `blocking`
        # compared; printing either turns a summary into a dump.
        checks = (
            "unreadable" if r.get("checks_unreadable") else
            " ".join(f"{k}={v}" for k, v in sorted(r["checks"].items())
                     if k not in ("names", "runs")) or "no checks"
        )
        draft = " [draft]" if r["draft"] else ""
        by = f" ({r['reviewer']})" if r["reviewer"] else ""
        where = f"{r['head_branch']} -> {r['base']}  @{r['head']}"
        state = (f"checks: {checks}   threads: {r['unresolved']} unresolved "
                 f"of {r['threads']}   review: {r['review']}{by}")
        lines += [
            "",
            f"  #{r['number']}{draft}  {r['title'][:64]}",
            f"    {where}   {r['merge_state']}",
            f"    {state}",
            f"    >> {r['blocking']}",
        ]
        for path in r["open_paths"]:
            lines.append(f"       open on {path}")
    return "\n".join(lines)


#: A stand-in for `expected_checks()`: three names that must report.
#: The table is hermetic; the live tree is read once, below, so the
#: wiring is exercised too.
EXPECTED = frozenset({"verify", "gates (a)", "gates (b)"})
ALL = ["gates (a)", "gates (b)", "verify"]

SELFTEST = [
    # (row, expected blocking sentence). Each case is a state that was
    # misread at least once, by hand, on this repository.
    ({"checks": {"fail": 1, "pass": 2, "names": ALL}, "unresolved": 0,
      "review": "current", "merge_state": "CLEAN"}, "CI red"),
    ({"checks": {"pass": 3, "names": ALL}, "unresolved": 2,
      "review": "current", "merge_state": "BLOCKED"}, "answer 2 unresolved"),
    ({"checks": {"pass": 1, "pending": 2, "names": ALL}, "unresolved": 0,
      "review": "current", "merge_state": "UNSTABLE"}, "waiting on CI"),
    ({"checks": {"pass": 2, "cancel": 1, "names": ALL}, "unresolved": 0,
      "review": "current", "merge_state": "CLEAN"}, "CI cancelled"),
    # A skipped check reported and did not pass. WHICH name skipped is
    # the whole verdict, and these two rows have identical counts.
    ({"checks": {"pass": 2, "skipping": 1, "names": ALL,
                 "runs": [{"name": "gates (a)", "bucket": "pass"},
                          {"name": "gates (b)", "bucket": "skipping"},
                          {"name": "verify", "bucket": "pass"}]},
      "unresolved": 0, "review": "current", "merge_state": "CLEAN"},
     "CI skipped"),
    ({"checks": {"pass": 3, "skipping": 1, "names": ALL + ["extra"],
                 "runs": [{"name": "extra", "bucket": "skipping"},
                          {"name": "gates (a)", "bucket": "pass"},
                          {"name": "gates (b)", "bucket": "pass"},
                          {"name": "verify", "bucket": "pass"}]},
      "unresolved": 0, "review": "current", "merge_state": "CLEAN"},
     "ready to merge"),
    # ...and with the buckets discarded neither can be told from the
    # other, which is "cannot tell" rather than a coin flip.
    ({"checks": {"pass": 2, "skipping": 1, "names": ALL}, "unresolved": 0,
      "review": "current", "merge_state": "CLEAN"}, "cannot tell"),
    ({"checks": {}, "unresolved": 0, "review": "current",
      "merge_state": "CLEAN"}, "no checks reported"),
    # One passing check is a nonempty all-pass result. It was "ready".
    ({"checks": {"pass": 1, "names": ["verify"]}, "unresolved": 0,
      "review": "current", "merge_state": "CLEAN"}, "checks incomplete"),
    # ...and so is the right COUNT with a stranger in it.
    ({"checks": {"pass": 3, "names": ["gates (a)", "stranger", "verify"]},
      "unresolved": 0, "review": "current", "merge_state": "CLEAN"},
     "checks incomplete"),
    # A row whose names were never collected cannot be compared.
    ({"checks": {"pass": 3}, "unresolved": 0, "review": "current",
      "merge_state": "CLEAN"}, "cannot tell"),
    ({"checks": {"pass": 3, "names": ALL}, "unresolved": 0, "review": "none",
      "merge_state": "CLEAN"}, "never reviewed"),
    ({"checks": {"pass": 3, "names": ALL}, "unresolved": 0, "review": "stale",
      "merge_state": "CLEAN"}, "review predates the head"),
    # An extra passing check blocks nothing.
    ({"checks": {"pass": 4, "names": ALL + ["extra"]}, "unresolved": 0,
      "review": "current", "merge_state": "CLEAN"}, "ready to merge"),
]


def selftest():
    problems = []
    for row, want in SELFTEST:
        got = blocking(row, EXPECTED)
        mark = "ok  " if want in got else "FAIL"
        if want not in got:
            problems.append(f"{want!r} not in {got!r}")
        print(f"  {mark} {want:<28} -> {got}")

    # A review is stale when its commit is not the head, and CURRENT
    # when it is; an absent review is neither.
    cases = [([{"submittedAt": "2026-01-01T00:00:00Z", "commit": {"oid": "a" * 40},
                "body": "looks fine", "author": {"login": "bot"}}],
              "a" * 40, "current"),
             ([{"submittedAt": "2026-01-01T00:00:00Z", "commit": {"oid": "b" * 40},
                "body": "findings", "author": {"login": "bot"}}],
              "a" * 40, "stale"),
             ([], "a" * 40, "none")]
    # A reply-shaped record must not be mistaken for a verdict.
    cases.append(([{"submittedAt": "2026-01-01T00:00:00Z",
                    "commit": {"oid": "b" * 40}, "body": "findings",
                    "state": "COMMENTED", "author": {"login": "reviewer"}},
                   {"submittedAt": "2026-02-01T00:00:00Z",
                    "commit": {"oid": "a" * 40}, "body": "",
                    "state": "COMMENTED", "author": {"login": "me"}}],
                   "a" * 40, "stale"))
    # ...and the other direction: an approval with no summary IS a
    # verdict, and reading it as a reply reported "never reviewed".
    cases.append(([{"submittedAt": "2026-01-01T00:00:00Z",
                    "commit": {"oid": "a" * 40}, "body": "",
                    "state": "APPROVED", "author": {"login": "reviewer"}}],
                   "a" * 40, "current"))
    for reviews, head, want in cases:
        _who, got = review_state(reviews, head)
        mark = "ok  " if got == want else "FAIL"
        if got != want:
            problems.append(f"review_state wanted {want}, got {got}")
        print(f"  {mark} review {want:<21} -> {got}")

    # The live wiring: the workflow names what should report and the shared
    # conf names the same set. Printed, so a reader sees the set this checkout
    # expects rather than trusting that one exists.
    try:
        required = expected_checks()
        configured = frozenset(laneconf.get("CHECK_NAMES").split())
        got = f"{len(required)} expected: {', '.join(sorted(required))}"
        ok = bool(required) and required == configured
        if required != configured:
            problems.append(
                f"workflow jobs {sorted(required)} != CHECK_NAMES "
                f"{sorted(configured)}"
            )
    except (Unavailable, laneconf.ConfigError) as exc:
        ok, got = False, f"cannot tell — {exc}"
        problems.append(got)
    print(f"  {'ok  ' if ok else 'FAIL'} {'tree':<28} -> {got}")

    # ...and when the tree cannot say, the production path (no
    # `expected` handed in) says "cannot tell" rather than "ready". An
    # empty directory is a tree with no derivation in it.
    with tempfile.TemporaryDirectory(prefix="pr-overview-empty-") as empty:
        got = blocking({"checks": {"pass": 1, "names": ["verify"]},
                        "unresolved": 0, "review": "current",
                        "merge_state": "CLEAN"}, root=Path(empty))
    ok = got.startswith("cannot tell")
    if not ok:
        problems.append(f"unreadable tree wanted 'cannot tell', got {got!r}")
    print(f"  {'ok  ' if ok else 'FAIL'} {'tree unreadable':<28} -> {got}")

    print(f"\n{len(SELFTEST) + len(cases) + 2} case(s), "
          f"{len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=laneconf.get("REPO_SLUG"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="run the verdict table against known states")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    if shutil.which("gh") is None:
        print("gh is not on PATH: refusing rather than reporting an "
              "overview it could not read", file=sys.stderr)
        return 2
    try:
        rows = collect(args.repo)
    except Unavailable as exc:
        print(f"could not read {args.repo}: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(rows, indent=2) if args.json else render(rows, args.repo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
