#!/usr/bin/env python3
"""The courier: carries queued coordination messages into a session.

    courier.py watch [--tick N] [--once] [--route stdout]

A session arms this as a Monitor. Each pass it asks the coordination core
for its own inbox, prints one line for every message no transport has
carried yet, and records that message delivered through the core's own
verb in the same breath — so the line a session reads and the stamp the
store keeps are one event, and the tick bounds how long a message waits.

It is a transport adapter and it lives outside the core on purpose:
`coordination-lever.md:272` says "The core never sends. Transports are
adapters outside it; the core records intent and receipt." The core keeps
queued, delivered, acknowledged and resolved apart, and this closes
exactly one of those gaps. Printing a line is delivery. It is not
acknowledgment: only the recipient's own `ack` says a session read it,
and this never writes that.

The participant is this process's own, resolved by the core from the
session id in the environment. The courier names no participant and takes
none: a courier that could choose whose messages to carry could deliver
another participant's inbox into this session.

Slice 2 (sessions-lever.md, "The shapes"): a courier holds its
participant's route in the core's `delivery_routes` by its own pid and
start ticks, stamps it every pass and gives it up when it stops, so a
second courier of either route refuses `route` while this one lives.
`watch` is the stdout route. The native route is the bridge's in-process
call, `carry(module, submit=...)`, never a second process: from this CLI
`--route native` refuses `transport`. A native pass reserves an attempt
before each submission and settles it after; a reservation left
unsettled past the ttl is reconciled through `observe` before anything
new is submitted, and no transaction is held across a submission.
"""

import sys

sys.dont_write_bytecode = True

import os  # noqa: E402
import time  # noqa: E402

try:
    from instrument import Refusal, refuse, stamp
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# The core's own names, settled against coordination.py at b1d868b0. The
# read is `inbox` until package B lands `undelivered`, which answers the
# same question without this filter; nothing waits on it, because a
# message already carries `delivered_at`.
CORE_TABLE = "CORE"
CORE_RESOLVE = "bound_participant"
CORE_WRITER = "writer"
INBOX_VERB = "inbox"
# Each read answers under its own key, and they are not the same word:
# `inbox` returns `messages`, `undelivered` returns `undelivered`. Read
# at 73e47041; assuming one for the other carried nothing at all.
INBOX_KEY = "messages"
UNDELIVERED_KEY = "undelivered"
DELIVER_VERB = "deliver"
ROUTE_VERB = "route"
ATTEMPT_VERB = "attempt"
ATTEMPTED_VERB = "attempted"
OUTCOMES = ("written", "accepted", "unsupported", "failed", "uncertain")
UNDELIVERED_VERB = "undelivered"
TICK_DEFAULT = 30
REF_PREFIX = "courier"


def core():
    """The coordination core beside this tool, or a reading that it is not."""
    try:
        import coordination
    except ModuleNotFoundError:
        refuse("state", "the coordination core beside this tool",
               "coordination.py absent",
               "land ops/conductor/tools/coordination.py, package B")
    return coordination


def verb(module, wire: str):
    """One core verb by its wire name, never by a lookup on the module."""
    table = getattr(module, CORE_TABLE, None)
    if not isinstance(table, dict):
        refuse("state", f"the core's verb table `{CORE_TABLE}`",
               "absent from coordination.py",
               "land the core's verb table, keyed by each verb's wire name")
    found = table.get(wire)
    if not callable(found):
        refuse("state", f"the core verb `{wire}`", "absent from the verb table",
               f"land the verb `{wire}` in the core's table")
    return found


def undelivered(module, conn, part) -> list:
    """Every message queued to this participant that no transport carried.

    `undelivered` when the core carries it, `inbox` filtered otherwise:
    a message names its own `delivered_at`, so the filter is a reading and
    not a guess.
    """
    table = getattr(module, CORE_TABLE, {})
    if isinstance(table, dict) and callable(table.get(UNDELIVERED_VERB)):
        answer = table[UNDELIVERED_VERB](conn, part)
        rows = answer.get(UNDELIVERED_KEY)
        if not isinstance(rows, list):
            refuse("state", f"a list under `{UNDELIVERED_KEY}` from the core's "
                   f"`{UNDELIVERED_VERB}`",
                   f"{sorted(answer) if isinstance(answer, dict) else type(answer).__name__}",
                   "settle the courier against the core's own answer")
        return [row for row in rows if isinstance(row, dict)]
    rows = verb(module, INBOX_VERB)(conn, part).get(INBOX_KEY, [])
    return [row for row in rows
            if isinstance(row, dict) and row.get("delivered_at") is None]


def line(row, at: str) -> str:
    """The one line a session reads, and the only thing this prints."""
    return (f"{at} {row.get('message')} {row.get('purpose')} "
            f"from {row.get('sender')}: {row.get('body')}")


def opened(module):
    """The store for writing and this process's own participant."""
    opener = getattr(module, CORE_WRITER, None)
    resolver = getattr(module, CORE_RESOLVE, None)
    if not callable(opener) or not callable(resolver):
        refuse("state", f"the core's `{CORE_WRITER}` and `{CORE_RESOLVE}`",
               "absent from coordination.py",
               "land the store opener and the participant resolver")
    conn = opener()
    return conn, resolver(conn)


def carriable(module, conn, part) -> list:
    """What this participant's courier carries now. With slice 2's core, a
    role-addressed message only when this participant is the single live
    holder of the role; before it, every undelivered message addressed here."""
    reading = getattr(module, "carriable", None)
    if callable(reading):
        return [row for row in reading(conn, part)["carry"] if isinstance(row, dict)]
    return undelivered(module, conn, part)


def holder() -> tuple[int, str | None]:
    """This process's pid and start ticks: what a route is held by."""
    pid = os.getpid()
    ticks = None
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
            ticks = handle.read().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        pass
    return pid, ticks


def carry(module, submit=None, *, observe=None, generation=1):
    """One pass. Without `submit`, the stdout route: every waiting message
    printed and recorded delivered, and the count returned. With it, the
    native route: see `carry_native`.

    The store is opened for writing because recording the receipt is a
    write; the read alone would not be enough to close the gap it finds.
    """
    if submit is not None:
        return carry_native(module, submit, observe=observe, generation=generation)
    conn, part = opened(module)
    try:
        return carry_stdout(module, conn, part)
    finally:
        conn.close()


def carry_stdout(module, conn, part) -> int:
    deliver = verb(module, DELIVER_VERB)
    carried = 0
    for row in carriable(module, conn, part):
        message = row.get("message")
        if not isinstance(message, str):
            continue
        at = stamp()
        # Printed first, then recorded, which makes this at-least-once and
        # not exactly-once: a courier stopped between the two leaves a line
        # a session has seen and a store that does not know it. The next
        # pass carries it again, because the filter is `delivered_at is
        # None` — a repeated line, never a silent loss. Measured
        # 2026-09-16: a live run terminated the courier in that window and
        # the store held queued and no delivered stamp, which is the
        # property working rather than a defect. The other order would
        # record a delivery no session ever saw, and nothing would carry
        # it again.
        print(line(row, at), flush=True)
        deliver(conn, part, message=message, ref=f"{REF_PREFIX}:{at}:{message}")
        carried += 1
    return carried


def settle_outcome(outcome: str) -> dict:
    """An adapter's submission reading as `attempted`'s arguments."""
    word, _, rest = str(outcome).partition(" ")
    if word not in OUTCOMES:
        return {"outcome": "uncertain", "error": f"unread submission outcome: {outcome}"}
    if word == "written":
        return {"outcome": "written"}
    if word in ("accepted", "unsupported"):
        return {"outcome": word, "ref": rest or None, "error": None if rest else outcome}
    return {"outcome": word, "error": rest or outcome}


def carry_native(module, submit, *, observe=None, generation=1) -> list:
    """One native pass: reconcile, then reserve, submit and settle each message.

    A reservation unsettled past the core's ttl is a crash between the
    submission and its settlement. `observe(message)` reads the native
    session's evidence for it: `seen` settles it `accepted reconciled`, and
    anything else settles it `uncertain`, after which the message is
    carried again below with one new attempt. `submit(row)` returns the
    adapter's reading. Each core write is its own transaction; none is
    open while `submit` or `observe` runs.
    """
    conn, part = opened(module)
    attempt = verb(module, ATTEMPT_VERB)
    attempted = verb(module, ATTEMPTED_VERB)
    passed = []
    try:
        for stale in module.stale_attempts(conn, part)["stale"]:
            reading = observe(stale["message"]) if observe is not None else "unseen: no observer"
            if str(reading).startswith("seen"):
                shown = attempted(conn, part, attempt=stale["attempt"], outcome="accepted",
                                  ref="reconciled")
            else:
                shown = attempted(conn, part, attempt=stale["attempt"], outcome="uncertain",
                                  error=str(reading))
            passed.append({"message": stale["message"], "attempt": stale["attempt"],
                           "outcome": shown["outcome"]})
        for row in carriable(module, conn, part):
            try:
                reserved = attempt(conn, part, message=row["message"], route="native",
                                   generation=generation)
            except Refusal as refusal:
                if refusal.kind != "place":
                    raise
                passed.append({"message": row["message"], "attempt": None,
                               "outcome": f"held {refusal.found}"})
                continue
            outcome = submit(row)
            shown = attempted(conn, part, attempt=reserved["attempt"],
                              **settle_outcome(outcome))
            passed.append({"message": row["message"], "attempt": reserved["attempt"],
                           "outcome": shown["outcome"]})
    finally:
        conn.close()
    return passed


def take_route(module, route: str, *, member=None) -> dict | None:
    """Hold this participant's route, or refuse `route` naming the live holder.
    A core without routes (before slice 2) holds none and says so by None."""
    table = getattr(module, CORE_TABLE, {})
    if not (isinstance(table, dict) and callable(table.get(ROUTE_VERB))):
        return None
    pid, ticks = holder()
    conn, part = opened(module)
    try:
        return table[ROUTE_VERB](conn, part, action="take", route=route, pid=pid,
                                 start_ticks=ticks, member=member)
    finally:
        conn.close()


def route_step(module, action: str) -> dict | None:
    """`pass` stamps the route this process holds; `release` gives it up."""
    table = getattr(module, CORE_TABLE, {})
    if not (isinstance(table, dict) and callable(table.get(ROUTE_VERB))):
        return None
    pid, ticks = holder()
    conn, part = opened(module)
    try:
        return table[ROUTE_VERB](conn, part, action=action, pid=pid, start_ticks=ticks)
    finally:
        conn.close()


def watch(tick: int, once: bool) -> int:
    module = core()
    held = take_route(module, "stdout")
    try:
        while True:
            if held is not None:
                route_step(module, "pass")
            carry(module)
            if once:
                return 0
            time.sleep(tick)
    finally:
        if held is not None:
            route_step(module, "release")


def cli(argv: list[str]) -> tuple[int, bool]:
    if not argv or argv[0] != "watch":
        refuse("arguments", "watch [--tick N] [--once] [--route stdout]",
               repr(argv) if argv else "no verb", "use the declared verb")
    rest, tick, once = argv[1:], TICK_DEFAULT, False
    while rest:
        if rest[0] == "--once":
            once, rest = True, rest[1:]
            continue
        if rest[0] == "--route":
            named = rest[1] if len(rest) > 1 else "no value"
            if named == "native":
                refuse("transport", "the stdout route from this CLI", "--route native",
                       "the native route is the bridge's own call: sessions.py bridge")
            if named != "stdout":
                refuse("arguments", "--route stdout or native", named, "pass --route stdout")
            rest = rest[2:]
            continue
        if rest[0] == "--tick":
            if len(rest) < 2 or not rest[1].isdigit() or int(rest[1]) < 1:
                refuse("arguments", "--tick as whole seconds, one or more",
                       rest[1] if len(rest) > 1 else "no value",
                       "pass --tick 30")
            tick, rest = int(rest[1]), rest[2:]
            continue
        refuse("arguments", "watch [--tick N] [--once] [--route stdout]", repr(rest[0]),
               "use the declared options")
    return tick, once


def main() -> int:
    try:
        tick, once = cli(sys.argv[1:])
        return watch(tick, once)
    except Refusal as exc:
        print(exc.line(), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
