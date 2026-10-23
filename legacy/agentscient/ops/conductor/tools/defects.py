#!/usr/bin/env python3
"""The lane defect ledger: a shim over ledger.py's defects ledger.

    defects.py next                                          print the next id, LD-NN
    defects.py add --where W --defect D --repair R --tests T   write the row file, print "LD-NN path"
    defects.py show LD-NN                                    print the row
    defects.py list                                          one line per row: id, observed, where

Owner, 2026-09-13 ("fix issue #1 - PR and merge"): rows from LD-36 are one
file each under ops/conductor/handoff/lane-defects/; the page's table is
frozen at LD-35. The same day ("the pitfalls page - in fact anything
similar") the shape moved into ledger.py, one lever over every appended
register; this file keeps the spelling the skills and records name.

Follows instrument-conventions.md through ledger.py: the same wire, the
same identity rule, the same refusal classes.
"""

from __future__ import annotations

import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import ledger
    from instrument import Refusal, emit
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py and ledger.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/ beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None


def main(argv: list[str]) -> int:
    if argv[:1] in (["next"], ["list"]) and len(argv) == 1:
        return ledger.main([argv[0], "defects"])
    if argv[:1] == ["add"]:
        return ledger.main(["add", "defects", *argv[1:]])
    if len(argv) == 2 and argv[0] == "show":
        return ledger.main(["show", "defects", argv[1]])
    ledger.refuse("arguments", "next | add --where W --defect D --repair R --tests T | show LD-NN | list",
                  " ".join(argv) or "nothing", "choose one verb with exactly its arguments")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Refusal as refusal:
        sys.exit(emit(refusal))
