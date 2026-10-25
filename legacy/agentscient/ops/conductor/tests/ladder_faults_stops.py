"""Planted faults for the stop tables (test_ladder_stops.py). Calibrate with

    python3 ops/testkit/cases.py calibrate --subject ops/conductor/tools/ladder.py \\
        --mutants ops/conductor/tests/ladder_faults_stops.py \\
        --command "python3 -m unittest discover -s ops/conductor/tests -p test_ladder_stops.py"
"""

from ops.testkit import Mutant

SUBJECT = "ops/conductor/tools/ladder.py"

MUTANTS = [
    Mutant("M-envelope-artifacts", SUBJECT,
           '    if artifacts != {}:\n        raise EnvelopeRefused("artifacts",',
           '    if False:\n        raise EnvelopeRefused("artifacts",',
           "a non-empty artifacts object is accepted"),
    Mutant("M-envelope-counts", SUBJECT,
           '    if envelope.get("counts") != tally:\n',
           '    if False:\n',
           "counts are not compared with the tally"),
    Mutant("M-envelope-verdict", SUBJECT,
           '    if verdict != wanted:\n',
           '    if False:\n',
           "approve with findings, or changes without, is accepted"),
    Mutant("M-answers", SUBJECT,
           '        if name == "tests-fix":\n            for key in self.skeptic_answers:\n                argv.extend(("--answers", key))\n',
           '        if False:\n            for key in self.skeptic_answers:\n                argv.extend(("--answers", key))\n',
           "LD-23's defect: tests-fix carries no --answers"),
    Mutant("M-cited", SUBJECT,
           '        if undisposed:\n            raise Stopped(\n                stage,\n                reason="undisposed",\n',
           '        if False:\n            raise Stopped(\n                stage,\n                reason="undisposed",\n',
           "an undisposed finding does not stop the ladder"),
]
