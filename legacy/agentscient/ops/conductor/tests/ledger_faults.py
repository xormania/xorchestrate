"""S2's three declared faults; calibration mutates an archive, never the tree."""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/ledger.py"
MUTANTS = [
    Mutant(
        "M-no-reserve", SUBJECT,
        '    conn.execute(\n'
        '        "INSERT INTO ledger_reservations"\n'
        '        "(ledger, number, owner, agent, reserved_at, history_floor, history_source) VALUES (?, ?, ?, ?, ?, ?, ?)",\n'
        '        (name, number, owner, agent, stamp(), observed, history_source),\n'
        '    )\n',
        '    conn.execute("SELECT ?", (number,))\n',
        "next-reserves must detect a number returned without its reservation",
    ),
    Mutant(
        "M-owner-blind", SUBJECT,
        '            "WHERE ledger = ? AND owner = ? AND filed_at IS NULL "\n',
        '            "WHERE ledger = ? AND ? IS NOT NULL AND filed_at IS NULL "\n',
        "another owner's open reservation cannot be consumed",
    ),
    Mutant(
        "M-add-mints", SUBJECT,
        '        number = own_reservation(conn, root, spec, owner, by)\n',
        '        number = highest(root, spec) + 1\n',
        "add-after-other-next must detect bypass of the reservation store",
    ),
]
