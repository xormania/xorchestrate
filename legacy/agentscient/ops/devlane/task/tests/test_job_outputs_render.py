"""C2 declared-output rendering: exact lines, JSON literals, prompt append.

Written from ops/devlane/task/CONTRACT.md §Native declarations and
rendered deliverables. Implementation was not read. Rendering
communicates an admitted declaration; it cannot authorize a grant.

Each test names the contract rule it pins:

  R1  the check-tests example renders byte-for-byte at /jobs/example/out
  R2  substitutions are JSON string literals: double-quoted, no slash
      escaping, lowercase hex for non-ASCII
  R3  omission and [] render the none-grant line
  R4  declaration order is visit order; a renamed job is identical
  R5  append strips trailing CR/LF then adds two LFs and the section
  R6  a rendered absolute path that cannot be a handle is refused
"""

from __future__ import annotations

import unittest

import outputs_support as osu
import support

from ops.testkit import World

envelope = support.load("envelope")

CHECK_TESTS_SECTION = (
    "Declared job outputs:\n"
    'Required file: "/jobs/example/out/SKEPTIC.md". '
    "Write a non-empty document; set artifacts"
    '["skeptic"] to "/jobs/example/out/SKEPTIC.md".\n'
    'Scratch subtree: "/jobs/example/out/tmp". '
    "Temporary work only; no artifact is required; "
    'TMPDIR is "/jobs/example/out/tmp".\n'
)

PLAN_SECTION = (
    "Declared job outputs:\n"
    'Required file: "/jobs/example/out/PLAN.md". '
    "Write a non-empty document; set artifacts"
    '["plan"] to "/jobs/example/out/PLAN.md".\n'
)

ADJUDICATE_SECTION = (
    "Declared job outputs:\n"
    'Required file: "/jobs/example/out/RULING.md". '
    "Write a non-empty document; set artifacts"
    '["ruling"] to "/jobs/example/out/RULING.md".\n'
    'Scratch subtree: "/jobs/example/out/tmp". '
    "Temporary work only; no artifact is required; "
    'TMPDIR is "/jobs/example/out/tmp".\n'
)

NONE_SECTION = (
    "Declared job outputs:\n"
    "None. No additional worker output write grant.\n"
)

ORDERED_SECTION = (
    "Declared job outputs:\n"
    'Required file: "/jobs/example/out/B.md". '
    "Write a non-empty document; set artifacts"
    '["b"] to "/jobs/example/out/B.md".\n'
    'Required file: "/jobs/example/out/A.md". '
    "Write a non-empty document; set artifacts"
    '["a"] to "/jobs/example/out/A.md".\n'
)


class _WorldCase(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="outputs-render-")
        self.addCleanup(self.world.close)
        self.world.enter()


class RenderExactNativeExamples(_WorldCase):
    """R1 / R4 — native jobs and a renamed equivalent share exact text."""

    def test_check_tests_renders_the_contract_example_exactly(self):
        text = osu.render_declared_outputs(
            osu.copy_native("check-tests"), osu.OUT_ROOT,
        )
        self.assertEqual(text, CHECK_TESTS_SECTION)
        self.assertTrue(text.endswith("\n"))

    def test_plan_renders_one_required_file_line(self):
        text = osu.render_declared_outputs(
            osu.copy_native("plan"), osu.OUT_ROOT,
        )
        self.assertEqual(text, PLAN_SECTION)

    def test_adjudicate_renders_file_then_scratch(self):
        text = osu.render_declared_outputs(
            osu.copy_native("adjudicate"), osu.OUT_ROOT,
        )
        self.assertEqual(text, ADJUDICATE_SECTION)

    def test_a_renamed_job_with_the_same_array_renders_identically(self):
        check_spec = {
            "name": "check-tests",
            "outputs": osu.copy_native("check-tests"),
        }
        renamed_spec = {
            "name": "skeptic-renamed",
            "outputs": osu.copy_native("check-tests"),
        }
        self.assertNotEqual(
            check_spec["name"], renamed_spec["name"],
            "plant: job identity differs",
        )
        self.assertEqual(
            check_spec["outputs"], renamed_spec["outputs"],
            "plant: same outputs array",
        )
        check = osu.outputs_from_job(check_spec)
        renamed = osu.outputs_from_job(renamed_spec)
        self.assertEqual(check, osu.NATIVE["check-tests"])
        self.assertEqual(renamed, check)
        native_text = osu.render_declared_outputs(check, osu.OUT_ROOT)
        renamed_text = osu.render_declared_outputs(renamed, osu.OUT_ROOT)
        self.assertEqual(native_text, CHECK_TESTS_SECTION)
        self.assertEqual(renamed_text, native_text)

    def test_declaration_order_is_the_visit_order(self):
        declared = [
            {"kind": "file", "path": "B.md", "artifact": "b"},
            {"kind": "file", "path": "A.md", "artifact": "a"},
        ]
        text = osu.render_declared_outputs(declared, osu.OUT_ROOT)
        self.assertEqual(text, ORDERED_SECTION)
        self.assertLess(text.find("/B.md"), text.find("/A.md"))


class RenderOmissionAndEmpty(_WorldCase):
    """R3 — omission and [] are the none-grant line, not inferred keys."""

    def test_omission_renders_the_none_grant_line(self):
        text = osu.render_declared_outputs([], osu.OUT_ROOT)
        self.assertEqual(text, NONE_SECTION)

    def test_validated_omission_renders_the_same_none_grant_line(self):
        text = osu.render_declared_outputs(osu.validate_outputs(), osu.OUT_ROOT)
        self.assertEqual(text, NONE_SECTION)

    def test_no_key_is_inferred_from_a_filename(self):
        text = osu.render_declared_outputs([], osu.OUT_ROOT)
        self.assertEqual(text, NONE_SECTION)
        self.assertNotIn("PLAN.md", text)
        self.assertNotIn("SKEPTIC.md", text)
        self.assertNotIn("RULING.md", text)
        self.assertNotIn("artifacts[", text)


class RenderJsonStringLiterals(_WorldCase):
    """R2 — path and artifact substitutions are JSON string literals."""

    def test_paths_and_keys_are_double_quoted_json_strings(self):
        text = osu.render_declared_outputs(
            osu.copy_native("check-tests"), osu.OUT_ROOT,
        )
        self.assertIn('Required file: "/jobs/example/out/SKEPTIC.md".', text)
        self.assertIn('artifacts["skeptic"]', text)
        self.assertIn('to "/jobs/example/out/SKEPTIC.md".', text)
        self.assertIn('Scratch subtree: "/jobs/example/out/tmp".', text)
        self.assertIn('TMPDIR is "/jobs/example/out/tmp".', text)

    def test_forward_slashes_are_not_optionally_escaped(self):
        text = osu.render_declared_outputs(
            osu.copy_native("plan"), osu.OUT_ROOT,
        )
        self.assertIn('"/jobs/example/out/PLAN.md"', text)
        self.assertNotIn(r"\/", text)

    def test_non_ascii_in_the_absolute_root_uses_lowercase_hex(self):
        # Relative declaration paths are ASCII; the canonical out/ root
        # is an absolute handle and may need JSON escaping.
        root = "/jobs/examplé/out"
        text = osu.render_declared_outputs(osu.copy_native("plan"), root)
        self.assertIn(
            r'"/jobs/exampl\u00e9/out/PLAN.md"',
            text,
        )
        self.assertNotIn(r"\u00E9", text)
        self.assertNotIn("examplé", text)


class AppendDeclaredOutputsToPrompt(_WorldCase):
    """R5 — strip trailing CR/LF, then two LFs, then the section."""

    def test_a_prompt_without_trailing_newline_gets_two_lfs_then_section(self):
        prompt = "Write the skeptic report."
        got = osu.append_declared_outputs(
            prompt, osu.copy_native("check-tests"), osu.OUT_ROOT,
        )
        self.assertEqual(got, prompt + "\n\n" + CHECK_TESTS_SECTION)

    def test_trailing_lfs_are_stripped_before_the_two_lfs(self):
        prompt = "Write the skeptic report.\n\n"
        got = osu.append_declared_outputs(
            prompt, osu.copy_native("check-tests"), osu.OUT_ROOT,
        )
        self.assertEqual(
            got,
            "Write the skeptic report." + "\n\n" + CHECK_TESTS_SECTION,
        )
        self.assertFalse(
            got.startswith(prompt + "\n"),
            "unstripped trailing newlines remain before the two LFs",
        )

    def test_trailing_crlf_is_stripped(self):
        prompt = "Write the skeptic report.\r\n"
        got = osu.append_declared_outputs(
            prompt, osu.copy_native("plan"), osu.OUT_ROOT,
        )
        self.assertEqual(
            got,
            "Write the skeptic report." + "\n\n" + PLAN_SECTION,
        )
        self.assertNotIn("\r", got)

    def test_omission_still_appends_the_none_grant_section(self):
        prompt = "Review the diff."
        got = osu.append_declared_outputs(prompt, [], osu.OUT_ROOT)
        self.assertEqual(got, prompt + "\n\n" + NONE_SECTION)

    def test_the_section_is_not_the_envelope_schema(self):
        prompt = "Review the diff."
        got = osu.append_declared_outputs(prompt, [], osu.OUT_ROOT)
        self.assertIn("Declared job outputs:", got)
        self.assertNotIn('"status"', got)
        self.assertNotIn("JSON Schema", got)


class RenderedHandleMustBeRepresentable(_WorldCase):
    """R6 — a bound path that cannot be a handle refuses before use."""

    def test_an_overlong_absolute_handle_is_refused(self):
        root = "/" + ("a" * (envelope.HANDLE_MAX + 8))
        self.assertGreater(len(root + "/PLAN.md"), envelope.HANDLE_MAX)
        with self.assertRaises(osu.outputs_error()):
            osu.render_declared_outputs(osu.copy_native("plan"), root)

    def test_a_representable_absolute_handle_renders(self):
        text = osu.render_declared_outputs(
            osu.copy_native("plan"), osu.OUT_ROOT,
        )
        handle = osu.OUT_ROOT + "/PLAN.md"
        self.assertLessEqual(len(handle), envelope.HANDLE_MAX)
        self.assertIn('"' + handle + '"', text)


if __name__ == "__main__":
    unittest.main()
