"""Planted faults for the refusal table (test_ladder_refusals.py): each
turns a precondition off in a copy of the driver. Calibrate with

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/ladder.py \\
        --mutants ops/conductor/tests/ladder_faults_refusals.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_ladder_refusals.py"
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/ladder.py"

MUTANTS = [
    Mutant("M-arguments", SUBJECT,
           '        if not args:\n            refuse(\n                "arguments",\n',
           '        if False:\n            refuse(\n                "arguments",\n',
           "an empty argv is not refused"),
    Mutant("M-scope-input", SUBJECT,
           '    if required:\n        refuse(label, f"a readable {label} file", str(candidate), needed)\n    return None\n',
           '    if required:\n        return candidate\n    return None\n',
           "an absent scope or input path is returned as if readable"),
    Mutant("M-dirty", SUBJECT,
           '    if unexpected:\n        refuse(\n            "dirty",\n',
           '    if False:\n        refuse(\n            "dirty",\n',
           "a dirty worktree is accepted"),
    Mutant("M-gh", SUBJECT,
           '    if shutil.which("gh") is None:\n        refuse(\n            "gh-missing",\n',
           '    if False:\n        refuse(\n            "gh-missing",\n',
           "gh is not required"),
    Mutant("M-set-floor", SUBJECT,
           '        for needed in ("tests", "check-tests"):\n            if needed not in chosen:\n',
           '        for needed in ():\n            if needed not in chosen:\n',
           "the floor's tests and check-tests are not checked"),
    Mutant("M-set-absent", SUBJECT,
           '    except OSError as error:\n        refuse("stage-set", f"a readable stage-set table at {path}", str(error),\n               "restore ops/conductor/stage-sets.json")\n',
           '    except OSError:\n        raw = {"lane": ["tests", "check-tests", "tests-fix", "code", "security", "adversarial", "fix-tests", "fix-code", "security-re"]}\n',
           "an absent table falls to a built-in seed"),
    Mutant("M-plan-class", SUBJECT,
           '        if name not in table:\n            refuse("plan", "a slice class of the table: " + ", ".join(table), name,\n                   "choose a declared class or add it to the table")\n',
           '        if name not in table:\n            name = DEFAULT_STAGE_SET\n',
           "an unknown plan class runs the default set"),
    Mutant("M-custody", SUBJECT,
           '        except Refused as refusal:\n            if refusal.kind != "custody":\n                raise\n            raise Stopped("tests-fix", reason="custody", exit_code=2, wire=refusal.line()) from refusal\n',
           '        except Refused as refusal:\n            if refusal.kind != "custody":\n                raise\n            return record_path\n',
           "a symlink at the custody name is followed past"),
]
