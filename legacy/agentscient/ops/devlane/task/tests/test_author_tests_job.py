"""The author-tests job's prompt names the kit (plan slice 18).

docs/tests.md rules 1, 2 and 6 reach the tests worker through its
prompt: every world, command and fake through the kit; a new test module
imports the kit or declares its exemption; a regression is a row with
its origin; the note's `reused:` names the invariant each table joins.
Reads jobs.json as the job catalogue, not the launcher.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World  # noqa: F401 - the import rule (docs/tests.md rule 1)

JOBS_PATH = support.APP / "jobs.json"

THROUGH_THE_KIT = "Build every world, command and fake through the kit at ops/testkit as docs/tests.md says"
IMPORT_OR_EXEMPT = "a new test module imports the kit or declares `# testkit: exempt <reason>`"
ROW_WITH_ORIGIN = "a regression is a row with its origin"
REUSED_NAMES_THE_INVARIANT = "your note's `reused:` names the kit invariant each table joins"
REUSED_EXAMPLE = "reused: testkit.refusal-wire"


def _squish(text):
    return " ".join(text.split())


class _AuthorTests(unittest.TestCase):
    def prompt(self):
        self.assertTrue(JOBS_PATH.is_file(), f"jobs.json missing: {JOBS_PATH}")
        jobs = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        self.assertIn("author-tests", jobs)
        prompt = jobs["author-tests"].get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertTrue(prompt.strip(), "author-tests prompt is empty")
        return prompt


class ThePromptNamesTheKit(_AuthorTests):
    def test_every_world_command_and_fake_goes_through_the_kit(self):
        self.assertIn(THROUGH_THE_KIT, _squish(self.prompt()))

    def test_a_new_test_module_imports_the_kit_or_declares_its_exemption(self):
        self.assertIn(IMPORT_OR_EXEMPT, _squish(self.prompt()))

    def test_a_regression_is_a_row_with_its_origin(self):
        self.assertIn(ROW_WITH_ORIGIN, _squish(self.prompt()))

    def test_the_note_names_the_invariant_joined(self):
        words = _squish(self.prompt())
        self.assertIn(REUSED_NAMES_THE_INVARIANT, words)
        self.assertIn(REUSED_EXAMPLE, words)

    def test_the_placeholders_and_the_red_rule_stay(self):
        prompt = self.prompt()
        for token in ("{scope}", "{ref}"):
            self.assertIn(token, prompt)
        self.assertIn("Prove each test red for its own assertion before making it green", prompt)
        self.assertIn("Do not read the implementation", prompt)
