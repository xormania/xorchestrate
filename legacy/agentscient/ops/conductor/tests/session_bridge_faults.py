"""Planted faults for sessions.py's Bridge (sessions-lever.md, slice 2).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. Every row of
test_session_bridge.py must stay green on the clean subject, and the rows
named in each note must go red on its mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/sessions.py \\
        --mutants ops/conductor/tests/session_bridge_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_session_bridge.py"

Anchored on the subject at bf924ee7 (#277). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/sessions.py"

MUTANTS = [
    Mutant(
        "M-hears-every-frame-first",
        SUBJECT,
        "            while heard < FRAMES_PER_PASS:\n"
        "                frame = self.rpc.next_frame(POLL if heard == 0 else 0)\n",
        "            while True:\n"
        "                frame = self.rpc.next_frame(POLL)\n",
        "the loop reads the channel until it is quiet before it carries, so "
        "a streaming turn gets no pass (carry-while-streaming, 11's review "
        "of fed6a4ce)",
    ),
    Mutant(
        "M-locked-store-uncaught",
        SUBJECT,
        "        except sqlite3.OperationalError as error:\n"
        "            # A store another session holds",
        "        except ZeroDivisionError as error:\n"
        "            # A store another session holds",
        "a locked store escapes the pass and would end the bridge "
        "(locked-store-backs-off)",
    ),
]
