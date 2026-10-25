#!/usr/bin/env python3
"""Read historical handwork records. Hand grants are retired."""

import argparse
import os
import sqlite3
import sys
from contextlib import closing

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import (
        Refusal,
        authority_home,
        emit,
        lane_name,
        now,
        parse_stamp,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

SCHEMA = """CREATE TABLE IF NOT EXISTS handwork(
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session TEXT NOT NULL,
    agent TEXT NOT NULL, action TEXT NOT NULL, why_by_hand TEXT NOT NULL,
    missing_lever TEXT, evidence TEXT, lever_class TEXT, scope TEXT,
    ttl_s INTEGER, status TEXT DEFAULT 'settled', settled_ts TEXT,
    verified INTEGER, late INTEGER DEFAULT 0, settle_note TEXT)"""


def database_path():
    return (
        authority_home()
        / ".local/state"
        / lane_name()
        / "handwork.db"
    )


def connect():
    path = database_path()
    required = {"handwork": ("id", "ts", "action", "status", "ttl_s", "lever_class",
                             "missing_lever", "verified", "late")}
    try:
        connection = instrument.connect_store(path, mode="ro", required=required)
    except (OSError, sqlite3.Error) as error:
        raise instrument.store_failure(path, error, "historical handwork records unavailable") from error
    connection.row_factory = sqlite3.Row
    return connection


def open_grants(_args):
    current = now()
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM handwork WHERE status = 'open' ORDER BY ts, id"
        ).fetchall()
    if not rows:
        print("no open grants")
        return
    for row in rows:
        age = max(0, int((current - parse_stamp(row["ts"])).total_seconds()))
        state = "late" if age > row["ttl_s"] else "within ttl"
        print(
            f"{row['id']} {row['lever_class']} {row['action']} "
            f"age={age}s ttl={row['ttl_s']}s {state}"
        )


def report(_args):
    with closing(connect()) as connection:
        statuses = connection.execute(
            "SELECT status, count(*) AS n FROM handwork GROUP BY status ORDER BY status"
        ).fetchall()
        open_rows = connection.execute(
            "SELECT * FROM handwork WHERE status = 'open' ORDER BY ts, id"
        ).fetchall()
        classes = connection.execute(
            """SELECT lever_class, count(*) AS n FROM handwork
               GROUP BY lever_class ORDER BY lever_class"""
        ).fetchall()
        levers = connection.execute(
            """SELECT missing_lever, count(*) AS n FROM handwork
               GROUP BY missing_lever ORDER BY missing_lever"""
        ).fetchall()
        debt = connection.execute(
            """SELECT
               sum(CASE WHEN status = 'open' THEN 1 ELSE 0 END),
               sum(CASE WHEN status = 'settled' AND coalesce(verified, 0) = 0
                        THEN 1 ELSE 0 END),
               sum(CASE WHEN status = 'settled' AND late = 1 THEN 1 ELSE 0 END)
               FROM handwork"""
        ).fetchone()
    print("status: " + ", ".join(f"{row['status']}={row['n']}" for row in statuses))
    print("open grants:")
    for row in open_rows:
        print(f"  {row['id']} {row['lever_class']} {row['action']}")
    print("recurrence by class:")
    for row in classes:
        print(f"  {row['lever_class'] or 'unclassified'}={row['n']}")
    print("recurrence by missing lever:")
    for row in levers:
        print(f"  {row['missing_lever'] or 'unnamed'}={row['n']}")
    print(
        f"debt: open={debt[0] or 0}, settled-unverified={debt[1] or 0}, "
        f"settled-late={debt[2] or 0}"
    )


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="verb", required=True)

    open_parser = commands.add_parser("open")
    open_parser.set_defaults(run=open_grants)
    report_parser = commands.add_parser("report")
    report_parser.set_defaults(run=report)
    return result


def main():
    args = parser().parse_args()
    try:
        args.run(args)
    except Refusal as error:
        return emit(error)
    except (OSError, sqlite3.Error) as error:
        return emit(instrument.store_failure(database_path(), error, "restore or retry the handwork store"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
