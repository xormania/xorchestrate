"""Planted faults for the onboard brief's LD-146 repair (onboard-lever.md).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. Every row of
test_onboard_lever.py must stay green on the clean subject, and the rows
named in each note must go red on its mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/onboard.py \\
        --mutants ops/conductor/tests/onboard_lever_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_onboard_lever.py"

Anchored on the subject at 243a17d8 (LD-146). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/onboard.py"
XDG_OR_HOME = 'Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))'

MUTANTS = [
    Mutant(
        "M-lock-root-from-env",
        SUBJECT,
        "    state_home = state_root()\n    lock = state_home",
        f"    state_home = {XDG_OR_HOME}\n    lock = state_home",
        "the lock is looked for under the private home and reads none "
        "(onboard-state-roots-follow-binding)",
    ),
    Mutant(
        "M-dispatch-root-from-env",
        SUBJECT,
        '    state_home = state_root()\n    store = state_home / lane_name() / "dispatch"',
        f'    state_home = {XDG_OR_HOME}\n    store = state_home / lane_name() / "dispatch"',
        "the dispatch store is looked for under the private home "
        "(onboard-state-roots-follow-binding)",
    ),
    Mutant(
        "M-handoff-root-from-env",
        SUBJECT,
        '    state_home = state_root()\n    store = state_home / lane_name() / "state.db"',
        f'    state_home = {XDG_OR_HOME}\n    store = state_home / lane_name() / "state.db"',
        "the handoff store is looked for under the private home "
        "(onboard-state-roots-follow-binding)",
    ),
    Mutant(
        "M-lock-measured",
        SUBJECT,
        '        [sys.executable, str(tool), "show", "--unmeasured"], root, "lock"\n',
        '        [sys.executable, str(tool), "show"], root, "lock"\n',
        "the brief asks for the measured lock and waits on every measure "
        "(onboard-lock-unmeasured, onboard-lock-unmeasured-argv)",
    ),
]
