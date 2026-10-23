"""Planted faults for session_adapters/channel.py's reader
(sessions-lever.md, the bridge: a stray response is dropped, never fatal).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. Every row of
test_session_channel.py and the grok stray row must stay green on the
clean subject, and the rows named in each note must go red on its mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/session_adapters/channel.py \\
        --mutants ops/conductor/tests/session_channel_faults.py \\
        --command "python3 -m unittest ops.conductor.tests.test_session_channel ops.conductor.tests.test_session_adapters_grok.TheGrokAdapter.test_a_stray_response_mid_turn_is_dropped_and_logged"

Anchored on the subject at 7952a9a6 (aa's e8cae56c). An anchor that no
longer matches once refuses class anchor; re-measure it against the
subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/session_adapters/channel.py"

MUTANTS = [
    Mutant(
        "M-stray-fatal",
        SUBJECT,
        "                        if not refused.stray:\n"
        "                            raise\n",
        "                        if True:\n"
        "                            raise\n",
        "the drop removed: a stray response ends the channel again, and the "
        "turn's later frames are lost (channel-stray-dropped, "
        "channel-stray-leaves-pending, grok-stray-response-dropped; A-527, "
        "aa's e8cae56c)",
    ),
    Mutant(
        "M-stray-unlogged",
        SUBJECT,
        "        line = f\"channel: dropped {reading}\\n\"\n",
        "        return\n",
        "the stray is kept but never written to the log (channel-stray-dropped, "
        "grok-stray-response-dropped)",
    ),
    Mutant(
        "M-every-refusal-stray",
        SUBJECT,
        "                        if not refused.stray:\n"
        "                            raise\n",
        "                        if False:\n"
        "                            raise\n",
        "every framing refusal is dropped as a stray, so a non-JSON line and a "
        "duplicate request id leave the channel alive (channel-non-json-dead, "
        "channel-duplicate-request-dead)",
    ),
]
