#!/usr/bin/env python3
"""Inspect reported capability gaps and record operator reviews."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from devtools.capability_gaps import (  # noqa: E402
    CATEGORIES,
    STATUSES,
    list_gaps,
    review_gap,
    show_gap,
    validate_review,
)
from devtools.store import State, Store  # noqa: E402


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def nonnegative(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("must be zero or a positive integer")
    return result


def page_limit(value):
    result = positive(value)
    if result > 100:
        raise argparse.ArgumentTypeError("must not exceed 100")
    return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state", required=True, type=Path, help="Existing private team state directory")
    result.add_argument("--json", action="store_true", help="Print structured output including pagination cursors")
    actions = result.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="List reported capability gaps")
    listing.add_argument("--status", choices=("active", "all", *STATUSES), default="active")
    showing = actions.add_parser(
        "show", help="Show one gap and its occurrence/review history",
        epilog="Resume both histories with the --after and --review-after positions in JSON next_page. "
               "A null next_page means both histories are exhausted in this view.",
    )
    showing.add_argument("gap_id", type=positive)
    showing.add_argument("--review-after", type=nonnegative, default=0)
    for action in (listing, showing):
        action.add_argument("--after", type=nonnegative, default=0)
        action.add_argument("--limit", type=page_limit, default=50)
    reviewing = actions.add_parser("review", help="Record an operator classification and disposition")
    reviewing.add_argument("gap_id", type=positive)
    reviewing.add_argument("--expected-revision", type=positive, required=True)
    reviewing.add_argument("--status", choices=STATUSES, required=True)
    reviewing.add_argument("--classification", choices=CATEGORIES, required=True)
    reviewing.add_argument("--note", required=True)
    reviewing.add_argument("--evidence", help="Resolution evidence; required for resolved status")
    reviewing.add_argument("--owner", help="Follow-up owner; an empty string clears the owner")
    return result


def quoted(value):
    """Keep task content readable without interpreting terminal control characters."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True)


def gap_summary(gap):
    return (
        f"Gap {gap['id']} | {gap['status']} | revision {gap['revision']} | "
        f"occurrences {gap['occurrence_count']} | {quoted(gap['capability'])}\n"
        f"  repository={quoted(gap['repository'])} classification={gap['classification']} "
        f"owner={quoted(gap['owner'])}"
    )


def human(result, action):
    if action == "list":
        rows = result["gaps"]
        print("\n".join(gap_summary(row) for row in rows) if rows else "No reported gaps in this view.")
    else:
        print(gap_summary(result["gap"]))
    if action == "show":
        print("Occurrences:")
        for row in result["occurrences"]:
            print(f"  {quoted(row)}")
        if not result["occurrences"]:
            print("  None on this page.")
        print("Reviews:")
        for row in result["reviews"]:
            print(f"  {quoted(row)}")
        if not result["reviews"]:
            print("  None on this page.")
    elif action == "review":
        print("Recorded review: " + quoted(result["review"]))
    if action == "show" and result.get("next_page") is not None:
        cursor = result["next_page"]
        print(f"Continue with --after {cursor['after']} --review-after {cursor['review_after']}.")
    elif action == "list" and result.get("next_after") is not None:
        print(f"Continue with --after {result['next_after']}.")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.action == "review":
            # Invalid review requests must not trigger Store's schema migration.
            review = validate_review(
                expected_revision=args.expected_revision,
                status=args.status,
                classification=args.classification,
                note=args.note,
                evidence=args.evidence,
                owner=args.owner,
            )
            # Store creates SQLite files on a writable connection. Establish that
            # this is initialized runtime state before permitting a migration.
            with State(args.state).connect() as db:
                for statement in ("SELECT 1 FROM members LIMIT 0", "SELECT 1 FROM messages LIMIT 0",
                                  "SELECT 1 FROM events LIMIT 0"):
                    db.execute(statement)
            result = review_gap(Store(args.state), args.gap_id, **review)
        else:
            state = State(args.state)
            if args.action == "list":
                result = list_gaps(state, status=args.status, after=args.after, limit=args.limit)
            else:
                result = show_gap(state, args.gap_id, after=args.after, limit=args.limit,
                                  review_after=args.review_after)
    except (OSError, sqlite3.Error, ValueError) as error:
        if args.json:
            print(json.dumps({"error": str(error)}, ensure_ascii=True))
        else:
            print("Capability-gap operation failed: " + quoted(str(error)), file=sys.stderr)
        return 2
    if result.get("capture") == "suppressed":
        if args.json:
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        else:
            prefix = "Review committed; " if args.action == "review" else "Inspection unavailable; "
            print(prefix + "result capture suppressed: " + quoted(result.get("reason")))
        return 0 if args.action == "review" else 2
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    else:
        human(result, args.action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
