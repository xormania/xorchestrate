"""The courier: what it carries, what it records, and what it must not do.

Contract: `ops/conductor/coordination-mcp-lever.md` §Package D and the
core's own `coordination-lever.md:272`, "The core never sends. Transports
are adapters outside it; the core records intent and receipt."

A case that never executes the subject's behaviour is a declared gap
wearing a pass. Every case here runs `courier.py` as a subprocess against
the real coordination core over a real store in a scratch
`XDG_STATE_HOME`, with the fixture driving the core's own verbs, so what
a case starts from is the core's behaviour and not an arrangement of
rows.

Calibrated 2026-09-16 by planting each fault in the tool and running this
file: a courier that prints without recording is caught by three cases, one
that carries the whole inbox by one, one that acknowledges while delivering
by one, and one that carries another participant's messages by four.

**The declared gap, and what it caught.** `undelivered()` has two paths:
the core's own `undelivered` verb when it carries one, and `inbox`
filtered on `delivered_at` otherwise. While the core had no such verb the
first path was unreachable and a fault planted in it was caught by
nothing, which this docstring said. The verb landed at 73e47041 and the
gap paid for itself the same hour: `core_undelivered` answers under the
key `undelivered`, not `messages`, so the courier carried nothing and
three cases here failed. The key is now read rather than assumed, and a
core that answers under neither refuses class `state` naming the keys it
did return. Both paths are exercised now — the verb's when the core
carries it, which is today.

Fixture set (docs/tests.md rule 3). Planted faults: a message queued and
never carried; a second pass over an already-carried message; a tick of
zero; an unknown option; a verb the tool does not have; a courier run by
a session with no participant. Clean neighbours: the first pass carrying
exactly one line, and the acknowledgment staying unset until the
recipient's own `ack`.
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run
from ops.testkit.cases import RefusalCase, refusal_wire, run_table

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
TOOL = TOOLS / "courier.py"
CORE_TOOL = TOOLS / "coordination.py"

TIMEOUT = 60.0
AGENT = "Claude Opus 5 (1M context) <noreply@example.test>"
LEAD = "sess-courier-lead"
WORKER = "sess-courier-worker"
STRANGER = "sess-courier-stranger"
BODY = "the worker's in-progress question"


class CourierAgainstTheCore(unittest.TestCase):
    """Every case over a real store the fixture built with the core's verbs."""

    def setUp(self):
        self.world = World(prefix="courier-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='couriertest'\n")

    def env(self, session=None):
        extra = {"LANE_CONF": str(self.conf), "WF_AGENT": AGENT}
        if session:
            extra["CLAUDE_CODE_SESSION_ID"] = session
            extra["COORDINATION_CAPABILITY"] = hashlib.sha256(session.encode()).hexdigest()
        return self.world.env(extra=extra)

    def core(self, *argv, session=LEAD, expect=0):
        result = run([sys.executable, str(CORE_TOOL), *argv],
                     cwd=self.world.cwd, env=self.env(session), timeout=TIMEOUT)
        if expect is not None:
            self.assertEqual(result.exit, expect, f"{argv}: {result.stderr}")
        return result

    def courier(self, *argv, session=None):
        return run([sys.executable, "-B", str(TOOL), *argv],
                   cwd=self.world.cwd, env=self.env(session), timeout=TIMEOUT)

    def bound(self):
        self.core("bind", "--roles", "lead,coordinator", session=LEAD)
        self.core("bind", "--roles", "worker", session=WORKER)

    def queued(self) -> str:
        """One message from the worker to the lead, through the core."""
        self.bound()
        out = self.core("send", "--to", "PT-1", "--purpose", "request",
                        "--body", BODY, session=WORKER).stdout
        return out.split()[1]

    def facts(self, message: str) -> dict:
        """The facts the core keeps for one message, read through its own
        `show` verb rather than by parsing a listing meant for people."""
        shown = self.core("show", message, session=LEAD).stdout
        return {
            "delivered": "delivered" in shown,
            "acknowledged": "acknowledged" in shown,
            "text": shown,
        }

    # -- what it carries ---------------------------------------------------

    def test_one_pass_prints_one_line_and_records_one_delivery(self):
        """Pins: a queued message is printed once and recorded delivered in
        the same pass, with the courier's own ref.

        Planted fault: a message queued through the core and never carried.
        A courier that printed without recording, or recorded without
        printing, fails one of the two assertions.
        """
        message = self.queued()
        before = self.facts(message)
        self.assertFalse(before["delivered"], before["text"])

        result = self.courier("watch", "--once", session=LEAD)
        self.assertEqual(result.exit, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, f"expected one line, got {lines}")
        self.assertIn(message, lines[0])
        self.assertIn(BODY, lines[0])

        after = self.facts(message)
        self.assertTrue(after["delivered"], after["text"])
        self.assertIn("courier:", after["text"])

    def test_a_carried_message_is_not_carried_twice(self):
        """Pins: the second pass is silent.

        Planted fault: the same store, a second run. A courier that read
        the whole inbox rather than what is undelivered would print it
        again and deliver it twice.
        """
        self.queued()
        first = self.courier("watch", "--once", session=LEAD)
        self.assertEqual(first.exit, 0, first.stderr)
        self.assertEqual(
            len([line for line in first.stdout.splitlines() if line.strip()]), 1)
        second = self.courier("watch", "--once", session=LEAD)
        self.assertEqual(second.exit, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), "",
                         f"the second pass printed: {second.stdout!r}")

    def test_delivery_is_not_acknowledgment(self):
        """Pins: the courier never writes the recipient's ack.

        Planted fault: a carried message. A courier that recorded ack while
        delivering would make the latency reading meaningless, and the
        objective forbids it by name: acknowledging transport consumption
        cannot close the underlying obligation.
        """
        message = self.queued()
        self.courier("watch", "--once", session=LEAD)
        after = self.facts(message)
        self.assertTrue(after["delivered"], after["text"])
        self.assertFalse(after["acknowledged"], after["text"])
        self.core("ack", message, session=LEAD)
        self.assertTrue(self.facts(message)["acknowledged"])

    def test_a_courier_carries_only_its_own_session_s_messages(self):
        """Pins: the participant is this process's own.

        Planted fault: a third session, bound, running the courier while a
        message waits for the lead. A courier that took a participant, or
        read the whole messages table, would carry another inbox into this
        session.
        """
        message = self.queued()
        self.core("bind", "--roles", "worker", session=STRANGER)
        result = self.courier("watch", "--once", session=STRANGER)
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "",
                         f"the stranger's courier printed: {result.stdout!r}")
        self.assertFalse(self.facts(message)["delivered"])

    # -- how it refuses ----------------------------------------------------

    def test_the_refusal_wire_on_every_row(self):
        rows = (
            RefusalCase(id="no-verb", argv=(), cls="arguments",
                        named=("watch",),
                        origin="the courier's declared command shape"),
            RefusalCase(id="unknown-verb", argv=("deliver",), cls="arguments",
                        origin="the courier's declared command shape"),
            RefusalCase(id="tick-zero", argv=("watch", "--tick", "0"),
                        cls="arguments", named=("--tick",),
                        origin="a tick of zero is a spin, not a poll"),
            RefusalCase(id="tick-absent", argv=("watch", "--tick"),
                        cls="arguments", origin="the option takes a value"),
            RefusalCase(id="unknown-option", argv=("watch", "--forever"),
                        cls="arguments", named=("--forever",),
                        origin="the options are closed"),
        )
        run_table(self, rows, lambda row: self.courier(*row.argv, session=LEAD))

    def test_a_session_with_no_participant_refuses_and_carries_nothing(self):
        """Pins: the core's own refusal reaches the caller unchanged.

        Planted fault: a session that never bound. The courier resolves
        through the core, so the refusal is the core's `participant`, not
        something this tool invented.
        """
        self.bound()
        result = self.courier("watch", "--once", session="sess-never-bound")
        refusal_wire(
            RefusalCase(id="unbound", argv=(), cls="participant",
                        origin="coordination-lever.md, resolve refuses participant"),
            result)


if __name__ == "__main__":
    unittest.main()
