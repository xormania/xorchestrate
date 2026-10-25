"""Planted faults for the dispatch wrapper's jobs-root resolution.

Each mutant is a literal edit to a copy of ops/conductor/tools/dispatch.sh,
never the tree; the kit's Mutant applies the anchor in an archive copy.
The hermetic-store rows in test_dispatch_wrapper.py must go red on the
mutant named in their origin, and stay green on the clean wrapper:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/dispatch.sh \\
        --mutants ops/conductor/tests/dispatch_wrapper_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p 'test_dispatch_wrapper.py' -k RowHermeticStore"
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/dispatch.sh"

MUTANTS = [
    Mutant(
        "M-ignore-jobs-env",
        SUBJECT,
        'jobs_root=${DISPATCH_JOBS:-"${XDG_STATE_HOME:-$HOME/.local/state}/$LANE_NAME/dispatch"}',
        'jobs_root="${XDG_STATE_HOME:-$HOME/.local/state}/$LANE_NAME/dispatch"',
        "the DISPATCH_JOBS honour removed; the record lands under the "
        "XDG default instead of the fixture jobs_root (S5, LD-69)",
    ),
    Mutant(
        "M-host-home",
        SUBJECT,
        'jobs_root=${DISPATCH_JOBS:-"${XDG_STATE_HOME:-$HOME/.local/state}/$LANE_NAME/dispatch"}',
        "jobs_root=${DISPATCH_JOBS:-~/.local/state/$LANE_NAME/dispatch}",
        "the default jobs root reads ~ instead of HOME/XDG_STATE_HOME "
        "(S5, issue 6)",
    ),
]
