"""Planted faults for dispatch_events.py (sessions-lever.md, slice 3, fixture 9).

Each mutant is a literal edit to a copy of the subject, never the tree.
The rows named in each note must go red on its mutant and stay green on
the clean subject:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/dispatch_events.py \\
        --mutants ops/conductor/tests/dispatch_events_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_dispatch_events.py"

Anchored on agentscient-10's subject at bfa82f5b; calibrated over the
whole module at 0ae638fb, with slice 2's `send --key` merged (clean
green, every mutant caught).

Declared gap: collected-before-router-informs-once (0e's F1 at
d0cbc6b0) has no mutant yet. Its fault is the pass reading `launched`
only, which is the subject as it stands; the mutant is anchored on
agentscient-10's fix when it lands, whichever shape the fix takes.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/dispatch_events.py"

MUTANTS = [
    Mutant(
        "M-completion-unsent",
        SUBJECT,
        'INFORM = ("completion", "no-op")\n',
        'INFORM = ("no-op",)\n',
        "a completion is acked and never sent (completion-informs, "
        "early-finish-one-inform, later-launch-repeats-nothing)",
    ),
    Mutant(
        "M-death-unsent",
        SUBJECT,
        'FINDING = ("invalid", "death")\n',
        'FINDING = ("invalid",)\n',
        "a death is acked and never sent (death-finds)",
    ),
]
