"""Planted faults for dispatch_mcp.py (sessions-lever.md, slice 3, fixture 11).

Each mutant is a literal edit to a copy of the subject, never the tree.
The rows named in each note must go red on its mutant and stay green on
the clean subject:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/dispatch_mcp.py \\
        --mutants ops/conductor/tests/dispatch_mcp_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_dispatch_mcp.py"

Anchored on agentscient-10's subject at bfa82f5b; calibrated over the
whole module at 02436b3c (clean green, every mutant caught). An anchor that no longer
matches once refuses class anchor; re-measure it against the subject.
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/dispatch_mcp.py"

MUTANTS = [
    Mutant(
        "M-status-mutates",
        SUBJECT,
        'base.Tool("coord_status", "status", False,',
        'base.Tool("coord_status", "status", True,',
        "coord_status marked as a mutation (mcp-tools-list)",
    ),
    Mutant(
        "M-collect-reads",
        SUBJECT,
        'base.Tool("coord_collect", "collect", True,',
        'base.Tool("coord_collect", "collect", False,',
        "coord_collect marked read-only (mcp-tools-list)",
    ),
    Mutant(
        "M-mcp-role-unchecked",
        "ops/conductor/tools/dispatch_intents.py",
        '    coordination.require_role(part, ("dispatcher",))\n',
        "    pass\n",
        "the shared role check dropped: coord_launch by a worker returns "
        "holder, not role (mcp-role, cli-role)",
    ),
]
