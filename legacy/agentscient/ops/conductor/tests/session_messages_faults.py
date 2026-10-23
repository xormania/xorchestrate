"""Planted faults for sessions.py's stop of a joined member
(sessions-lever.md, slice 2, fixture 10).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. The row the
command names must stay green on the clean subject and go red on the
mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/sessions.py \\
        --mutants ops/conductor/tests/session_messages_faults.py \\
        --command "python3 -m unittest ops.conductor.tests.test_session_messages.SessionMessages.test_join_then_stop_is_unresolved_unmanaged"

The codex and grok member rows carry no mutant while LD-134 keeps every
such member from starting (a declared gap, named in the module).

Anchored on the subject at bf924ee7 (#277). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/sessions.py"

MUTANTS = [
    Mutant(
        "M-joined-with-pid-signalled",
        SUBJECT,
        "        if joined(row):\n            # A joined participant",
        '        if joined(row) and row["pid"] is None:\n            # A joined participant',
        "stop signals a joined member whose row carries a pid (join-unmanaged, "
        "20260923T020912Z-review-codex-9d8b8c #3)",
    ),
]
