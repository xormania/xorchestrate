"""The ladder driver's planted faults, as data (docs/tests.md rule 3;
plan §4.1). Each mutant is a literal edit to a copy of
ops/conductor/tools/ladder.py, never the tree; the anchor is the
function's own text and a mutant whose anchor no longer matches refuses
`anchor` in the verbs. The tables in test_ladder_tables.py,
test_ladder_refusals.py and test_ladder_stops.py must go red on the
mutant named in their rows' origin, and stay green on the clean driver:

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/ladder.py \\
        --mutants ops/conductor/tests/ladder_faults.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_ladder_tables.py"
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/ladder.py"

MUTANTS = [
    # LD-15: an absolute artifact path used verbatim, never rewritten under snapshot/
    Mutant(
        "M-derive-abs", SUBJECT,
        '                if path.is_absolute():\n'
        '                    parts = path.parts\n'
        '                    if "snapshot" not in parts[:-1]:\n',
        '                if False:\n'
        '                    parts = path.parts\n'
        '                    if "snapshot" not in parts[:-1]:\n',
        "LD-15's defect: the command names a worker's snapshot",
    ),
    # LD-19: every .py under tests/ becomes a command, support modules included
    Mutant(
        "M-derive-support", SUBJECT,
        '                    or not path.name.startswith("test")\n',
        '                    or False\n',
        "LD-19's defect: a discover over a support module",
    ),
    # a `..` component accepted
    Mutant(
        "M-derive-dotdot", SUBJECT,
        '                if ".." in path.parts:\n',
        '                if False and ".." in path.parts:\n',
        "a path that escapes the tree becomes a command",
    ),
    # LD-17: the wall never comes from the record copy or the job cap
    Mutant(
        "M-wall-default", SUBJECT,
        '        cap = (launched.get("caps") or {}).get("timeout")\n'
        '        if cap is not None:\n'
        '            return cap\n',
        '        return None\n'
        '        cap = (launched.get("caps") or {}).get("timeout")\n'
        '        if cap is not None:\n'
        '            return cap\n',
        "LD-17's defect: WALL_DEFAULT for every stage",
    ),
    # LD-19: the retry is granted on every call, never spent
    Mutant(
        "M-retry-unspent", SUBJECT,
        '        if row.get("retry"):\n'
        '            return False\n'
        '        row["retry"] = {\n',
        '        row["retry"] = {\n',
        "the recorded extra attempt is granted again and again",
    ),
    # --retry without --because accepted
    Mutant(
        "M-resume-options", SUBJECT,
        '    if (stage is None) != (because is None):\n'
        '        refuse("arguments", "--retry STAGE together with --because TEXT", " ".join(args),\n'
        '               "name both the stage and the reason")\n',
        '    if stage is not None and because is None:\n'
        '        because = "unstated"\n',
        "a retry with no reason, or a reason with no stage, is taken",
    ),
    # ruled rank 5 (record 20260915T025600Z-adjudicate-codex-68d3d9): only the
    # first test module of each tests/ directory reaches the implement command
    Mutant(
        "M-derive-first-per-directory", SUBJECT,
        '                commands.append(\n'
        '                    "python3 -m unittest discover "\n',
        '                if any(c.startswith(f"python3 -m unittest discover -s {shlex.quote(path.parent.as_posix())} ") for c in commands):\n'
        '                    continue\n'
        '                commands.append(\n'
        '                    "python3 -m unittest discover "\n',
        "the second test module in a directory is never run",
    ),
]
