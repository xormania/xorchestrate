"""Planted faults for the native courier pass and the attempt cap
(sessions-lever.md, slice 2): courier.py and coordination.py.

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. The rows the
command names must stay green on the clean subject, and the rows named in
each note must go red on its mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/courier.py \\
        --mutants ops/conductor/tests/courier_native_faults.py \\
        --command "python3 -m unittest ops.conductor.tests.test_courier_native.TheCrash ops.conductor.tests.test_courier_native.TheAttempts.test_a_fifth_reservation_refuses_place ops.conductor.tests.test_courier_native.TheAttempts.test_four_settled_attempts_leave_a_fifth_admitted"

Anchored on the subjects at bf924ee7 (#277); M-settled-counted
re-anchored at 8ab649dd, where the count keys on the member's kin (020d470e). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

COURIER = "ops/conductor/tools/courier.py"
CORE = "ops/conductor/tools/coordination.py"

MUTANTS = [
    Mutant(
        "M-observe-never-asked",
        COURIER,
        'reading = observe(stale["message"]) if observe is not None else "unseen: no observer"',
        'reading = "unseen: no observer"',
        "the pass never asks observe about a stale attempt (both crash rows, "
        "20260923T020912Z-review-codex-9d8b8c #6, #7)",
    ),
    Mutant(
        "M-seen-ignored",
        COURIER,
        'if str(reading).startswith("seen"):',
        "if False:",
        "found evidence is not honoured: the stale attempt reads uncertain "
        "and is carried again (#6)",
    ),
    Mutant(
        "M-absent-read-as-seen",
        COURIER,
        'if str(reading).startswith("seen"):',
        "if True:",
        "absent evidence is read as seen: no uncertain, no new attempt (#7)",
    ),
    Mutant(
        "M-cap-admits-a-fifth",
        CORE,
        "if held >= ATTEMPTS_OUTSTANDING:",
        "if held > ATTEMPTS_OUTSTANDING:",
        "a fifth unsettled reservation is admitted (outstanding-cap, #5)",
    ),
    Mutant(
        "M-settled-counted",
        CORE,
        '"AND outcome IS NULL", kin).fetchone()[0]',
        '"", kin).fetchone()[0]',
        "settled attempts count as outstanding (settled-not-outstanding, #5)",
    ),
]
