"""Dispatch follow-through: one pass turns a monitor digest into messages.

Contract: `ops/conductor/sessions-lever.md` slice 3, "Slice 3 — Dispatcher
integration", the follow-through and fixture 9. The route is
`dispatch_events.py route --consumer dispatch --once`. It is withheld, so
the world stages whatever is on disk and an absent module is the kit's
stand-in: the row then fails on its own assertion.

`completion` informs the assignment's holder, carrying the record id and
the exit, acks once, and the per-pass config names that record.
`death` is a finding to the Dispatcher and to the holder. `running` is
acked and never sent. The same digest served twice sends nothing twice,
counted by `send --key event:<event id>:<recipient>`. A send that cannot
take the store's write lock leaves the cursor unacked and the messages
absent. An event whose record has no intent is acked and never sent.
`--once` returns after one `run --once`. A job waited and collected
before any router pass still reaches its requester as one inform with
the record id (slice 3's exit; 0e's F1 at d0cbc6b0).

The monitor is `fakes/dispatch_digest.py`, planted through the registry
at `DISPATCH_MONITOR`. An intent is planted through `dispatch_intents.py`
(reserve, the `launch` transition, `mint`), never by a hand write of the
store. The join between those calls is the intent's correlation.

The per-intent pass config (sessions-lever.md, "The per-intent pass
config"): each pass names one monitor, one condition and one route per
launched intent, never one shared `ids` list. The fake logs each
config it is handed, and serves the events above its acked cursor, as
monitor.py's digest does.

Planted faults (docs/tests.md rule 3, 20260923T020916Z-review-codex-5a41cd#6):
dispatch_events_faults.py beside this module, anchored on agentscient-10's
subject at bfa82f5b; its docstring names the calibrate command.
Calibrated over the whole module at 0ae638fb. Its two faults reach
completion-informs, early-finish-one-inform, later-launch-repeats-nothing
and death-finds; a fault for each other row is a declared gap.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path

from ops.testkit import RefusalCase, World, refusal_wire, run

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
EVENTS = TOOLS / "dispatch_events.py"
ACTIONS = TOOLS / "dispatch_actions.py"
LAUNCH_FAKE = Path(__file__).resolve().parent / "fakes" / "dispatch_launch.py"
VERIFICATIONS = REPO / "ops" / "conductor" / "verifications.json"
INTENTS = TOOLS / "dispatch_intents.py"
CORE = TOOLS / "coordination.py"
DISPATCHER = TOOLS / "dispatcher.py"
DELIVERABLE = TOOLS / "deliverable.py"
FAKE = Path(__file__).resolve().parent / "fakes" / "dispatch_digest.py"
LANE_CONF = REPO / "ops" / "lane.conf"
SIBLINGS = ("instrument.py", "laneconf.py", "machine.py", "coordination.py", "dispatcher.py")

TIMEOUT = 30.0
AGENT = "Grok Test <noreply@example.test>"
LEAD = "sess-events-lead"
HOLDER = "sess-events-holder"
DISPATCHER_SESSION = "sess-events-dispatcher"
BRANCH = "lane/session-20260923-events"
ORIGIN = "sessions-lever.md#slice-3 9"
PASS_CONFIG = "sessions-lever.md#slice-3 the per-intent pass config"
EXIT_CLAUSE = "sessions-lever.md#slice-3 exit; 0e F1 at d0cbc6b0"
EXIT_ALONE = re.compile(r"(?<![0-9])0(?![0-9])")


@dataclass(frozen=True)
class Row:
    """One follow-through row, addressed by id, with where it came from."""

    id: str
    origin: str
    word: str
    record: str


def _by_id(rows: tuple[Row, ...]) -> dict[str, Row]:
    table = {row.id: row for row in rows}
    if len(table) != len(rows):
        raise RuntimeError("row ids are unique")
    return table


ROWS = _by_id((
    Row("completion-informs", ORIGIN, "completion", "rec-completion"),
    Row("death-finds", ORIGIN, "death", "rec-death"),
    Row("running-acks", ORIGIN, "running", "rec-running"),
    Row("digest-replayed", ORIGIN, "completion", "rec-replay"),
    Row("send-fails-unacked", ORIGIN, "completion", "rec-locked"),
    Row("record-without-intent", "sessions-lever.md#slice-3 clean", "completion", "rec-stranger"),
    Row("once-returns", ORIGIN, "running", "rec-once"),
    Row("early-finish-one-inform", PASS_CONFIG, "completion", "rec-early"),
    Row("later-launch-repeats-nothing", PASS_CONFIG, "completion", "rec-first"),
    Row("collected-before-router-informs-once", EXIT_CLAUSE, "completion", "rec-collected"),
))


class DispatchFollowThrough(unittest.TestCase):
    """Fixture 9 and the clean neighbour, each row over its own world."""

    def setUp(self):
        self.world = World(prefix="dispatch-events-")
        self.addCleanup(self.world.close)
        self.lane = self._lane_name()
        self.jobs = self.world.root / "jobs"
        self.store = self.world.xdg_state / self.lane / "state.db"
        self.events = self.world.stage(EVENTS, siblings=SIBLINGS)
        self.intents_tool = self.world.stage(INTENTS, siblings=SIBLINGS)
        self.monitor = self.world.tools.plant_executable(
            self.world.bin / "dispatch-monitor", FAKE.read_text(encoding="utf-8"))
        self.spec_path = Path(self.monitor).with_suffix(".spec.json")
        self.wrapper = self.world.tools.plant("dispatch-wrapper")
        text = LAUNCH_FAKE.read_text(encoding="utf-8")
        if text.startswith("#!"):
            text = text.split("\n", 1)[1]
        self.launcher = self.world.tools.plant("dispatch-launcher",
                                               script=f"#!{sys.executable}\n" + text)
        self._held = None
        self.addCleanup(self.release_store)
        self._bind_cast()

    def _lane_name(self) -> str:
        for line in LANE_CONF.read_text(encoding="utf-8").splitlines():
            if line.startswith("LANE_NAME="):
                return line.split("=", 1)[1].strip().strip("'\"")
        raise AssertionError("ops/lane.conf names LANE_NAME")

    def env(self, session: str) -> dict[str, str]:
        return self.world.env(extra={
            "LANE_CONF": str(LANE_CONF),
            "WF_AGENT": AGENT,
            "CLAUDE_CODE_SESSION_ID": session,
            "COORDINATION_CAPABILITY": hashlib.sha256(session.encode()).hexdigest(),
            "DISPATCH_WRAPPER": str(self.wrapper),
            "DISPATCH_LAUNCHER": str(self.launcher),
            "DISPATCH_MONITOR": str(self.monitor),
            "DISPATCH_JOBS": str(self.jobs),
        })

    def tool(self, program, *argv, session: str):
        return run([sys.executable, str(program), *argv],
                   cwd=self.world.cwd, env=self.env(session), timeout=TIMEOUT)

    def core(self, *argv, session: str):
        result = self.tool(CORE, *argv, session=session)
        self.assertEqual(result.exit, 0, f"{argv}: {result.stderr}")
        return result

    def _bind_cast(self):
        """A lead, the assignment's holder, and the Dispatcher who holds the role."""
        lead = self.core("bind", "--roles", "lead", session=LEAD)
        self.lead_pt = lead.stdout.split()[1]
        holder = self.core("bind", "--roles", "worker", session=HOLDER)
        self.holder_pt = holder.stdout.split()[1]
        dispatcher = self.core("bind", "--roles", "worker", session=DISPATCHER_SESSION)
        self.dispatcher_pt = dispatcher.stdout.split()[1]
        self.core("grant", self.dispatcher_pt, "--roles", "dispatcher", session=LEAD)
        named = self.tool(DISPATCHER, "set", "--why", "the follow-through trial",
                          session=DISPATCHER_SESSION)
        self.assertEqual(named.exit, 0, named.stderr)
        locked = self.tool(DELIVERABLE, "add", BRANCH, "--why", "the follow-through trial",
                           session=LEAD)
        self.assertEqual(locked.exit, 0, locked.stderr)
        drafted = self.core("draft", "a dispatch follow-through", "--outcome",
                            "the requester hears the record", session=LEAD)
        self.assignment = drafted.stdout.split()[-1]
        generation = self.generation(self.assignment)
        self.core("offer", self.assignment, "--to", self.holder_pt,
                  "--generation", generation, session=LEAD)
        generation = self.generation(self.assignment)
        self.core("claim", self.assignment, "--branch", BRANCH,
                  "--generation", generation, session=HOLDER)

    def generation(self, assignment: str) -> str:
        shown = self.core("show", assignment, session=LEAD).stdout
        found = re.search(r"generation (\d+)", shown)
        self.assertIsNotNone(found, f"show printed no generation: {shown!r}")
        return found.group(1)

    def plant_job(self, record: str, word: str) -> None:
        """A job directory whose reading agrees with the digest word."""
        root = self.jobs / record
        if word == "completion":
            body = {
                "status": "closed", "role": "read",
                "attempts": [{"n": 1, "exit": 0}],
                "result": {"envelope": {"status": "ok"}, "changed_paths": ["README"]},
                "at": {"launched": "2026-09-23T00:00:00Z"},
            }
            self.world.plant_text(root / "exit", "0\n")
        elif word == "death":
            body = {
                "status": "died", "role": "read",
                "attempts": [{"n": 1, "exit": 137}],
                "at": {"launched": "2026-09-23T00:00:00Z"},
            }
        else:
            body = {
                "status": "running", "role": "read",
                "attempts": [{"n": 1}],
                "at": {"launched": "2026-09-23T00:00:00Z"},
            }
        self.world.plant_text(root / "record.json", json.dumps(body) + "\n")

    def plant_launched(self, row: Row) -> None:
        """A launched intent through dispatch_intents.py, joined by correlation."""
        correlation = str(uuid.uuid4())
        inputs = self.world.plant_text(self.world.root / "inputs" / f"{row.record}.txt",
                                       "the assignment's full text\n")
        self.plant_job(row.record, row.word)
        common = [
            "--requester", self.holder_pt,
            "--dispatcher", self.dispatcher_pt,
            "--assignment", self.assignment,
            "--key", f"row-{row.record}",
            "--job", "author-tests",
            "--harness", "grok",
            "--branch", BRANCH,
            "--scope-ref", "ops/conductor/sessions-lever.md",
            "--inputs", str(inputs),
            "--correlation", correlation,
        ]
        reserved = self.tool(self.intents_tool, "reserve", *common, session=DISPATCHER_SESSION)
        self.assertEqual(reserved.exit, 0, reserved.stderr)
        launching = self.tool(self.intents_tool, "launch", "--correlation", correlation,
                              session=DISPATCHER_SESSION)
        self.assertEqual(launching.exit, 0, launching.stderr)
        minted = self.tool(self.intents_tool, "mint", "--correlation", correlation,
                           "--record", row.record, session=DISPATCHER_SESSION)
        self.assertEqual(minted.exit, 0, minted.stderr)

    def serve(self, row: Row, *, replay: bool = False, event_id: int = 1,
              observed: bool = False) -> None:
        """The digest this pass returns. Summary is the monitor's status field."""
        event = {
            "id": event_id,
            "route": "dispatch-status",
            "consumer": "dispatch",
            "condition": "dispatch-status",
            "monitor": "dispatch",
            "subject": row.record,
            "state": "ok",
            "summary": f'status="{row.word}"',
            "evidence": str(self.jobs / row.record / "record.json"),
            "observed_at": "2026-09-23T00:00:00Z",
            "delivery": "queued",
        }
        self.world.plant_text(self.spec_path, json.dumps(
            {"replay": replay, "observed": observed, "events": [event]}) + "\n")

    def route(self):
        return run(
            [sys.executable, str(self.events), "route", "--consumer", "dispatch", "--once"],
            cwd=self.world.cwd, env=self.env(DISPATCHER_SESSION), timeout=TIMEOUT,
        )

    def calls(self) -> list[dict]:
        log = Path(self.monitor).with_suffix(".calls.jsonl")
        if not log.is_file():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def acks(self) -> list[dict]:
        return [call for call in self.calls() if call.get("ack")]

    def configs(self) -> list[dict]:
        """The pass config each `run --once` was handed, as the fake read it."""
        return [call.get("body") for call in self.calls()
                if "run" in call.get("argv", []) and "--once" in call.get("argv", [])]

    def per_intent(self, config, records: tuple[str, ...], row: Row) -> dict[str, tuple]:
        """Each record's own monitor, condition and route in one pass config.

        One monitor per launched intent, its `params.ids` naming that
        record alone; one condition on that monitor; one route on that
        condition. A shared `ids` list fails here."""
        self.assertIsInstance(config, dict, f"{row.id}: the pass config: {config!r}")
        monitors = config.get("monitors") or {}
        conditions = config.get("conditions") or {}
        routes = config.get("routes") or {}
        named = {name: tuple((body.get("params") or {}).get("ids") or ())
                 for name, body in monitors.items()}
        self.assertEqual(sorted(named.values()), sorted((record,) for record in records),
                         f"{row.id}: one monitor per intent, each naming its record: {named!r}")
        own: dict[str, tuple] = {}
        for name, ids in named.items():
            watching = [c for c, body in conditions.items() if body.get("monitor") == name]
            self.assertEqual(len(watching), 1, f"{row.id}: conditions on {name}: {conditions!r}")
            routed = [r for r, body in routes.items() if body.get("condition") == watching[0]]
            self.assertEqual(len(routed), 1, f"{row.id}: routes on {watching[0]}: {routes!r}")
            own[ids[0]] = (
                (name, monitors[name]),
                (watching[0], conditions[watching[0]]),
                (routed[0], routes[routed[0]]),
            )
        return own

    def ids_logged(self) -> list[str]:
        found: list[str] = []
        for call in self.calls():
            found.extend(call.get("ids") or [])
        return found

    def inbox(self, session: str) -> list[str]:
        result = self.core("inbox", session=session)
        text = result.stdout.strip()
        if text in ("", "no messages"):
            return []
        return [line for line in result.stdout.splitlines() if line.strip()]

    def about(self, session: str, purpose: str, record: str) -> list[str]:
        found = []
        for line in self.inbox(session):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == purpose and record in line:
                found.append(line)
        return found

    @staticmethod
    def payload(line: str) -> str:
        return line.split(": ", 1)[1] if ": " in line else line

    def hold_store(self) -> None:
        """The world holds the store's write lock across the pass."""
        self._held = sqlite3.connect(self.store, isolation_level=None, timeout=5)
        self._held.execute("BEGIN IMMEDIATE")

    def release_store(self) -> None:
        held = self._held
        self._held = None
        if held is None:
            return
        try:
            held.execute("ROLLBACK")
        finally:
            held.close()

    def test_completion_informs_the_holder(self):
        """One completion: one inform carrying the record and the exit, one ack,
        and the config the fake received names that record."""
        row = ROWS["completion-informs"]
        self.plant_launched(row)
        self.serve(row)
        self.route()
        lines = self.about(HOLDER, "inform", row.record)
        self.assertEqual(len(lines), 1, f"{row.id}: {lines!r}")
        body = self.payload(lines[0])
        self.assertIn(row.record, body, body)
        self.assertRegex(body, EXIT_ALONE, body)
        self.assertEqual(len(self.acks()), 1, self.calls())
        self.assertIn(row.record, self.ids_logged(), self.calls())

    def test_death_finds_the_dispatcher_and_the_holder(self):
        """A death is a finding to the Dispatcher and a finding to the holder."""
        row = ROWS["death-finds"]
        self.plant_launched(row)
        self.serve(row)
        self.route()
        holder = self.about(HOLDER, "finding", row.record)
        dispatcher = self.about(DISPATCHER_SESSION, "finding", row.record)
        self.assertEqual(len(holder), 1, f"{row.id} holder: {holder!r}")
        self.assertEqual(len(dispatcher), 1, f"{row.id} dispatcher: {dispatcher!r}")
        self.assertIn(row.record, self.payload(holder[0]))
        self.assertIn(row.record, self.payload(dispatcher[0]))
        # Both sends, then the ack (20260923T020916Z-review-codex-5a41cd#6).
        self.assertEqual(len(self.acks()), 1, self.calls())

    def test_running_is_acked_and_not_sent(self):
        """A running event is acked and never sent."""
        row = ROWS["running-acks"]
        self.plant_launched(row)
        self.serve(row)
        self.route()
        self.assertEqual(len(self.acks()), 1, self.calls())
        self.assertEqual(self.about(HOLDER, "inform", row.record), [])
        self.assertEqual(self.about(HOLDER, "finding", row.record), [])
        self.assertEqual(self.about(DISPATCHER_SESSION, "finding", row.record), [])

    def test_a_replayed_digest_sends_nothing_twice(self):
        """The same digest twice is one message, counted by send --key."""
        row = ROWS["digest-replayed"]
        self.plant_launched(row)
        self.serve(row, replay=True)
        self.route()
        self.route()
        key = f"event:1:{self.holder_pt}"
        probe = self.tool(
            CORE, "send", "--key", key, "--to", self.holder_pt, "--purpose", "inform",
            "--body", "probe body the route did not write", "--assignment", self.assignment,
            session=DISPATCHER_SESSION,
        )
        refusal_wire(
            RefusalCase(id=row.id, argv=("send", "--key", key), cls="idempotency",
                        named=("M-",), origin=row.origin),
            probe,
        )
        lines = self.about(HOLDER, "inform", row.record)
        self.assertEqual(len(lines), 1, f"{row.id}: {lines!r}")

    def test_a_locked_store_is_not_acked(self):
        """A send that cannot write leaves the cursor unacked and no message."""
        row = ROWS["send-fails-unacked"]
        self.plant_launched(row)
        self.serve(row)
        self.hold_store()
        try:
            self.route()
        finally:
            self.release_store()
        digested = [call for call in self.calls() if "digest" in call.get("argv", [])]
        self.assertEqual(len(digested), 1, self.calls())
        self.assertEqual(self.acks(), [], self.calls())
        self.assertEqual(self.about(HOLDER, "inform", row.record), [])
        self.assertEqual(self.about(DISPATCHER_SESSION, "inform", row.record), [])

    def test_a_record_with_no_intent_is_acked_and_not_sent(self):
        """An event for a record with no intent is acked and never sent."""
        row = ROWS["record-without-intent"]
        self.serve(row)
        result = self.route()
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(len(self.acks()), 1, self.calls())
        self.assertEqual(self.about(HOLDER, "inform", row.record), [])
        self.assertEqual(self.about(HOLDER, "finding", row.record), [])
        self.assertEqual(self.about(DISPATCHER_SESSION, "finding", row.record), [])

    def test_once_returns_after_one_pass(self):
        """`--once` returns after one `run --once`."""
        row = ROWS["once-returns"]
        self.plant_launched(row)
        self.serve(row)
        result = self.route()
        self.assertEqual(result.exit, 0, result.stderr)
        runs = [call for call in self.calls()
                if "run" in call.get("argv", []) and "--once" in call.get("argv", [])]
        self.assertEqual(len(runs), 1, self.calls())
        self.assertTrue(any("digest" in call.get("argv", []) for call in self.calls()), self.calls())

    def test_an_early_finish_gives_exactly_one_inform(self):
        """A job that finished before the router's first pass is reported by
        the monitor's first event and by nothing else: there is no seed.
        origin: the per-intent pass config; 20260923T020916Z-review-codex-5a41cd#7"""
        row = ROWS["early-finish-one-inform"]
        self.plant_launched(row)
        self.serve(row)
        self.route()
        self.route()
        lines = self.about(HOLDER, "inform", row.record)
        self.assertEqual(len(lines), 1, f"{row.id}: {lines!r}")
        configs = self.configs()
        self.assertEqual(len(configs), 2, self.calls())
        for config in configs:
            self.per_intent(config, (row.record,), row)

    def test_a_later_launch_repeats_nothing_for_the_first(self):
        """A later launch joins the pass config beside the first intent. The
        first keeps its own monitor, condition and route unchanged, so its
        baseline holds, and it is not informed again.
        origin: the per-intent pass config; 20260923T020916Z-review-codex-5a41cd#7"""
        row = ROWS["later-launch-repeats-nothing"]
        later = Row(row.id, row.origin, "completion", "rec-later")
        self.plant_launched(row)
        self.serve(row)
        self.route()
        self.plant_launched(later)
        self.serve(later, event_id=2)
        self.route()
        configs = self.configs()
        self.assertEqual(len(configs), 2, self.calls())
        first = self.per_intent(configs[0], (row.record,), row)
        second = self.per_intent(configs[1], (row.record, later.record), row)
        self.assertEqual(second[row.record], first[row.record],
                         f"{row.id}: the first intent's entries changed when the later one joined")
        self.assertEqual(len(self.about(HOLDER, "inform", row.record)), 1,
                         self.about(HOLDER, "inform", row.record))
        self.assertEqual(len(self.about(HOLDER, "inform", later.record)), 1,
                         self.about(HOLDER, "inform", later.record))

    def test_a_job_collected_before_any_pass_informs_its_requester_once(self):
        """A job that ends and is collected before the router's first pass
        (the router down, or inside one tick) still reaches its requester as
        one inform carrying the record id, whether the pass reports it or
        collect sends it itself.
        origin: slice 3's exit, "the launch's outcome reaches the requester
        as a message with the record id, once"; 0e's F1 at d0cbc6b0"""
        row = ROWS["collected-before-router-informs-once"]
        with self.subTest(id=row.id, origin=row.origin):
            self.plant_launched(row)
            table = json.loads(VERIFICATIONS.read_text(encoding="utf-8"))
            self.assertTrue(table, f"the tracked table names no verification: {VERIFICATIONS}")
            collected = run(
                [sys.executable, str(ACTIONS), "collect", "DI-1", "--verify", min(table)],
                cwd=REPO, env=self.env(DISPATCHER_SESSION), timeout=TIMEOUT,
            )
            self.assertEqual(collected.exit, 0, collected.stderr)
            self.assertEqual(self.calls(), [], "a router pass ran before collect")
            self.serve(row, observed=True)
            self.route()
            lines = self.about(HOLDER, "inform", row.record)
            self.assertEqual(len(lines), 1, f"{row.id}: {lines!r}")
            self.assertIn(row.record, self.payload(lines[0]))


if __name__ == "__main__":
    unittest.main()
