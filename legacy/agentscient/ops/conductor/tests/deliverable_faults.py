"""Planted faults for deliverable.py's show --unmeasured (LD-146).

Each mutant is a literal edit to a copy of the subject, never the tree;
the kit's Mutant applies the anchor in an archive copy. Every row of
test_deliverable.py must stay green on the clean subject, and the rows
named in each note must go red on its mutant:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/deliverable.py \\
        --mutants ops/conductor/tests/deliverable_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_deliverable.py -k show_unmeasured"

Updated for PR-01: ordinary show is also observation; done measures explicitly. An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/deliverable.py"

MUTANTS = [
    Mutant(
        "M-unmeasured-measures",
        SUBJECT,
        "        print(target_line(branch, target))\n",
        "        reading(target)\n        print(target_line(branch, target))\n",
        "show --unmeasured runs every measure (deliverable-show-unmeasured)",
    ),
    Mutant(
        "M-any-option-admitted",
        SUBJECT,
        '    if args not in ([], ["--unmeasured"]):\n',
        "    if False:\n",
        "an unknown option to show is admitted, never refused "
        "(deliverable-show-bogus-refuses)",
    ),
    Mutant(
        "M-done-does-not-measure",
        SUBJECT,
        "    met, line = reading(target)\n",
        '    met, line = True, "fabricated reading"\n',
        "explicit done invents a pass without running its command (PR-01)",
    ),
]
