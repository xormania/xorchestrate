"""Planted faults for dispatch_actions.py (sessions-lever.md, slice 3).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. The rows named in
each note must go red on its mutant and stay green on the clean subject:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/dispatch_actions.py \\
        --mutants ops/conductor/tests/dispatch_actions_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_dispatch_actions.py"

Anchored on agentscient-10's subject at bfa82f5b; calibrated over the
whole module at 02436b3c (clean green, every mutant caught). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/dispatch_actions.py"
# The role and holder check moved into dispatch_intents.the_dispatcher at
# f465f0d9; dispatch_actions calls it from there.
INTENTS = "ops/conductor/tools/dispatch_intents.py"

MUTANTS = [
    Mutant(
        "M-role-unchecked",
        INTENTS,
        '    coordination.require_role(part, ("dispatcher",))\n',
        "    pass\n",
        "the role check dropped: a worker reaches the holder check and "
        "refuses holder, not role (worker-role)",
    ),
    Mutant(
        "M-holder-unchecked",
        INTENTS,
        "    if not held or held != mine:\n",
        "    if False:\n",
        "any dispatcher acts without holding the machine (not-holder)",
    ),
    Mutant(
        "M-refusal-swallowed",
        SUBJECT,
        "    except Refusal as refusal:\n        return emit(refusal)\n",
        "    except Refusal as refusal:\n        return 0\n",
        "a refusal from the core is swallowed and the verb exits 0; "
        "the core refuses an unwritable store before the subject acts "
        "(store-unwritable; the contract's store is the core's store)",
    ),
]
