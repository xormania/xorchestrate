"""C2 job-output declarations: schema, omission, overlaps, reservations.

Written from ops/devlane/task/CONTRACT.md §Declared job outputs and
§C2 standing and declaration schema, plus dispatch CONTRACT.md §C2
transport reservations. Implementation (envelope.py internals,
launch.py, jobs.json) was not read beyond the existing public
envelope load used by test_envelope.py.

Each test names the contract rule it pins:

  O1  optional outputs is an array; omission is []; explicit null refuses
  O2  file and scratch are the only kinds; extra members refuse
  O3  paths and artifact keys match the grammar over the whole string
  O4  duplicate members, paths, artifact keys, overlaps and two scratches refuse;
      JSON-text duplicates are load_outputs, not validate_outputs
  O5  no case folding or path normalization turns invalid into a grant
  O6  transport leaves last-message.json and COMMIT_MSG are reserved;
      further adapter transport leaves may join that default;
      declared scratch tmp is not a transport reservation
  O7  a renamed job with the same declarations is the same grant
  O8  prompt prose is never parsed to authorize writes
"""

from __future__ import annotations

import inspect
import json
import unittest
from pathlib import Path

import outputs_support as osu
import support

from ops.testkit import World

envelope = support.load("envelope")

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "job_outputs"
DUP_MEMBER = FIXTURES / "duplicate-path-member.json"


def _type_list(node):
    if not isinstance(node, dict):
        return []
    raw = node.get("type")
    if raw is None:
        types = []
    elif isinstance(raw, list):
        types = list(raw)
    else:
        types = [raw]
    for alt in node.get("anyOf") or node.get("oneOf") or ():
        types.extend(_type_list(alt))
    return types


class _WorldCase(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.world.enter()


class OutputsSchemaIsExported(_WorldCase):
    """O2 — the standalone JSON Schema is the declaration vocabulary."""

    def test_outputs_schema_is_exported_as_an_array_schema(self):
        schema = osu.outputs_schema()
        self.assertIsInstance(
            schema, dict,
            "missing public seam envelope.OUTPUTS_SCHEMA",
        )
        self.assertEqual(schema.get("type"), "array")
        self.assertIs(schema.get("uniqueItems"), True)

    def test_file_and_scratch_are_the_only_item_kinds(self):
        schema = osu.outputs_schema()
        self.assertIsInstance(
            schema, dict,
            "missing public seam envelope.OUTPUTS_SCHEMA",
        )
        items = schema.get("items") or {}
        alts = items.get("oneOf") or []
        self.assertGreaterEqual(
            len(alts), 2,
            "items.oneOf must name file and scratch objects",
        )
        kinds = []
        for alt in alts:
            props = (alt or {}).get("properties") or {}
            kind = (props.get("kind") or {}).get("const")
            kinds.append(kind)
        self.assertEqual(set(kinds), {"file", "scratch"})

    def test_file_requires_kind_path_and_artifact(self):
        schema = osu.outputs_schema()
        self.assertIsInstance(
            schema, dict,
            "missing public seam envelope.OUTPUTS_SCHEMA",
        )
        file_alt = None
        for alt in (schema.get("items") or {}).get("oneOf") or []:
            props = (alt or {}).get("properties") or {}
            if (props.get("kind") or {}).get("const") == "file":
                file_alt = alt
                break
        self.assertIsInstance(file_alt, dict, "file alternative missing")
        self.assertEqual(
            set(file_alt.get("required") or []),
            {"kind", "path", "artifact"},
        )
        self.assertIs(file_alt.get("additionalProperties"), False)
        artifact = (file_alt.get("properties") or {}).get("artifact") or {}
        self.assertEqual(artifact.get("maxLength"), 64)
        self.assertEqual(artifact.get("pattern"), "^[a-z][a-z0-9_-]*$")

    def test_scratch_requires_kind_and_path_only(self):
        schema = osu.outputs_schema()
        self.assertIsInstance(
            schema, dict,
            "missing public seam envelope.OUTPUTS_SCHEMA",
        )
        scratch_alt = None
        for alt in (schema.get("items") or {}).get("oneOf") or []:
            props = (alt or {}).get("properties") or {}
            if (props.get("kind") or {}).get("const") == "scratch":
                scratch_alt = alt
                break
        self.assertIsInstance(scratch_alt, dict, "scratch alternative missing")
        self.assertEqual(
            set(scratch_alt.get("required") or []),
            {"kind", "path"},
        )
        self.assertIs(scratch_alt.get("additionalProperties"), False)
        self.assertNotIn(
            "artifact",
            (scratch_alt.get("properties") or {}),
            "scratch must not take an artifact key",
        )

    def test_path_grammar_and_length_are_pinned(self):
        schema = osu.outputs_schema()
        self.assertIsInstance(
            schema, dict,
            "missing public seam envelope.OUTPUTS_SCHEMA",
        )
        defs = schema.get("$defs") or schema.get("definitions") or {}
        path_def = defs.get("path")
        self.assertIsInstance(path_def, dict, f"$defs.path missing: {defs!r}")
        self.assertIn("string", _type_list(path_def))
        self.assertEqual(path_def.get("maxLength"), 240)
        self.assertEqual(
            path_def.get("pattern"),
            "^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$",
        )


class OmissionEmptyAndNull(_WorldCase):
    """O1 / O8 — omission is []; null is invalid; prose grants nothing."""

    def test_omission_normalizes_to_an_empty_list(self):
        got = osu.validate_outputs()
        self.assertEqual(got, [])
        self.assertIsInstance(got, list)

    def test_an_empty_array_is_the_same_as_omission(self):
        got = osu.validate_outputs([])
        self.assertEqual(got, [])

    def test_explicit_null_is_refused(self):
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(None)

    def test_outputs_from_job_treats_a_missing_key_as_omission(self):
        spec = {"adapter": "harness", "role": "read", "prompt": "review {ref}"}
        self.assertNotIn("outputs", spec)
        self.assertEqual(osu.outputs_from_job(spec), [])

    def test_outputs_from_job_refuses_an_explicit_null_member(self):
        spec = {"role": "read", "outputs": None}
        self.assertIn("outputs", spec)
        self.assertIsNone(spec["outputs"])
        with self.assertRaises(osu.outputs_error()):
            osu.outputs_from_job(spec)

    def test_prompt_prose_naming_a_document_does_not_grant_writes(self):
        spec = {
            "adapter": "harness",
            "role": "read",
            "prompt": "Write the plan to {out}/PLAN.md and name artifacts.plan.",
        }
        self.assertIn("PLAN.md", spec["prompt"])
        self.assertNotIn("outputs", spec)
        self.assertEqual(
            osu.outputs_from_job(spec), [],
            "runtime authorization never searches prompt prose",
        )


class NativeAndRenamedDeclarations(_WorldCase):
    """O7 — native arrays validate; a renamed job with the same array matches."""

    def test_native_plan_declaration_is_accepted_in_order(self):
        got = osu.validate_outputs(osu.copy_native("plan"))
        self.assertEqual(got, osu.NATIVE["plan"])

    def test_native_check_tests_declaration_is_accepted_in_order(self):
        got = osu.validate_outputs(osu.copy_native("check-tests"))
        self.assertEqual(got, osu.NATIVE["check-tests"])
        self.assertEqual([row["kind"] for row in got], ["file", "scratch"])

    def test_native_adjudicate_declaration_is_accepted_in_order(self):
        got = osu.validate_outputs(osu.copy_native("adjudicate"))
        self.assertEqual(got, osu.NATIVE["adjudicate"])

    def test_a_renamed_job_with_the_same_array_is_the_same_declaration(self):
        check = osu.outputs_from_job(
            {"name": "check-tests", "outputs": osu.copy_native("check-tests")},
        )
        renamed = osu.outputs_from_job(
            {
                "name": "skeptic-renamed",
                "outputs": osu.copy_native("check-tests"),
            },
        )
        self.assertEqual(check, osu.NATIVE["check-tests"])
        self.assertEqual(renamed, check)

    def test_validate_outputs_does_not_take_a_job_name(self):
        sig = osu.validate_outputs_signature()
        self.assertIsNotNone(
            sig,
            "missing public seam envelope.validate_outputs",
        )
        self.assertNotIn(
            "job", sig.parameters,
            "authorization never branches on job name",
        )

    def test_a_review_spec_with_omission_or_empty_gains_no_writes(self):
        omitted = osu.outputs_from_job(
            {"name": "adversarial-review", "role": "read"},
        )
        empty = osu.outputs_from_job(
            {"name": "adversarial-review", "role": "read", "outputs": []},
        )
        self.assertEqual(omitted, [])
        self.assertEqual(empty, [])

    def test_normalized_declarations_preserve_array_order(self):
        declared = [
            {"kind": "file", "path": "B.md", "artifact": "b"},
            {"kind": "file", "path": "A.md", "artifact": "a"},
        ]
        got = osu.validate_outputs(declared)
        self.assertEqual(got, declared)
        self.assertEqual([row["path"] for row in got], ["B.md", "A.md"])


class MalformedUnknownAndGrammar(_WorldCase):
    """O2 / O3 / O5 — unknown members, traversal and whole-string grammar."""

    def _refuse(self, value, plant):
        self.assertEqual(value, plant, "INVALID fixture: not planted")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_an_unknown_member_on_a_file_is_refused(self):
        row = {
            "kind": "file",
            "path": "PLAN.md",
            "artifact": "plan",
            "mode": "write",
        }
        self._refuse([row], [row])

    def test_an_unknown_kind_is_refused(self):
        row = {"kind": "dir", "path": "out"}
        self._refuse([row], [row])

    def test_a_file_without_artifact_is_refused(self):
        row = {"kind": "file", "path": "PLAN.md"}
        self._refuse([row], [row])

    def test_scratch_must_not_carry_an_artifact(self):
        row = {"kind": "scratch", "path": "tmp", "artifact": "scratch"}
        self._refuse([row], [row])

    def test_an_empty_path_is_refused(self):
        row = {"kind": "file", "path": "", "artifact": "plan"}
        self._refuse([row], [row])

    def test_an_empty_artifact_is_refused(self):
        row = {"kind": "file", "path": "PLAN.md", "artifact": ""}
        self._refuse([row], [row])

    def test_an_uppercase_artifact_is_refused(self):
        row = {"kind": "file", "path": "PLAN.md", "artifact": "Plan"}
        self._refuse([row], [row])

    def test_a_leading_hyphen_artifact_is_refused(self):
        row = {"kind": "file", "path": "PLAN.md", "artifact": "-plan"}
        self._refuse([row], [row])

    def test_a_leading_dot_path_is_refused(self):
        row = {"kind": "file", "path": ".hidden", "artifact": "hidden"}
        self._refuse([row], [row])

    def test_a_backslash_path_is_refused(self):
        row = {"kind": "file", "path": "foo\\bar", "artifact": "foo"}
        self._refuse([row], [row])

    def test_a_whitespace_path_is_refused(self):
        row = {"kind": "file", "path": "PLAN md", "artifact": "plan"}
        self._refuse([row], [row])

    def test_a_glob_path_is_refused(self):
        row = {"kind": "file", "path": "PLAN*", "artifact": "plan"}
        self._refuse([row], [row])

    def test_a_substitution_path_is_refused(self):
        row = {"kind": "file", "path": "{out}/PLAN.md", "artifact": "plan"}
        self._refuse([row], [row])

    def test_an_absolute_path_is_refused(self):
        row = {
            "kind": "file",
            "path": "/jobs/example/out/PLAN.md",
            "artifact": "plan",
        }
        self._refuse([row], [row])

    def test_a_dot_segment_is_refused_not_normalized(self):
        row = {"kind": "file", "path": "./PLAN.md", "artifact": "plan"}
        self._refuse([row], [row])

    def test_a_parent_segment_is_refused_not_normalized(self):
        row = {"kind": "file", "path": "tmp/../PLAN.md", "artifact": "plan"}
        self._refuse([row], [row])

    def test_a_trailing_slash_is_refused(self):
        row = {"kind": "file", "path": "PLAN.md/", "artifact": "plan"}
        self._refuse([row], [row])

    def test_a_trailing_newline_on_the_path_is_refused(self):
        row = {"kind": "file", "path": "PLAN.md\n", "artifact": "plan"}
        self.assertTrue(row["path"].endswith("\n"), "plant: trailing newline")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs([row])

    def test_a_trailing_newline_on_the_artifact_is_refused(self):
        row = {"kind": "file", "path": "PLAN.md", "artifact": "plan\n"}
        self.assertTrue(row["artifact"].endswith("\n"), "plant: trailing newline")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs([row])

    def test_a_non_array_declaration_is_refused(self):
        value = {"kind": "file", "path": "PLAN.md", "artifact": "plan"}
        self._refuse(value, value)

    def test_path_length_over_240_is_refused(self):
        path = "P" + ("a" * 237) + ".md"
        self.assertEqual(len(path), 241)
        row = {"kind": "file", "path": path, "artifact": "plan"}
        self._refuse([row], [row])

    def test_artifact_length_over_64_is_refused(self):
        artifact = "p" + ("a" * 64)
        self.assertEqual(len(artifact), 65)
        row = {"kind": "file", "path": "PLAN.md", "artifact": artifact}
        self._refuse([row], [row])


class DuplicatesOverlapsAndScratch(_WorldCase):
    """O4 / O5 — duplicates, ancestor overlap, one scratch, case-sensitive paths."""

    def test_identical_items_are_refused(self):
        row = {"kind": "file", "path": "PLAN.md", "artifact": "plan"}
        value = [row, dict(row)]
        self.assertEqual(value[0], value[1], "plant: identical items")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_duplicate_paths_across_kinds_are_refused(self):
        value = [
            {"kind": "file", "path": "tmp", "artifact": "report"},
            {"kind": "scratch", "path": "tmp"},
        ]
        self.assertEqual(value[0]["path"], value[1]["path"])
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_repeated_artifact_keys_are_refused(self):
        value = [
            {"kind": "file", "path": "A.md", "artifact": "plan"},
            {"kind": "file", "path": "B.md", "artifact": "plan"},
        ]
        self.assertEqual(value[0]["artifact"], value[1]["artifact"])
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_ancestor_descendant_overlap_is_refused(self):
        value = [
            {"kind": "scratch", "path": "tmp"},
            {"kind": "file", "path": "tmp/SKEPTIC.md", "artifact": "skeptic"},
        ]
        self.assertTrue(
            value[1]["path"].startswith(value[0]["path"] + "/"),
            "plant: descendant path",
        )
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_more_than_one_scratch_entry_is_refused(self):
        value = [
            {"kind": "scratch", "path": "tmp"},
            {"kind": "scratch", "path": "work"},
        ]
        scratches = [row for row in value if row["kind"] == "scratch"]
        self.assertEqual(len(scratches), 2, "plant: two scratch entries")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_case_sensitive_paths_are_not_folded_into_duplicates(self):
        value = [
            {"kind": "file", "path": "PLAN.md", "artifact": "plan"},
            {"kind": "file", "path": "plan.md", "artifact": "plan2"},
        ]
        self.assertNotEqual(value[0]["path"], value[1]["path"])
        got = osu.validate_outputs(value)
        self.assertEqual(got, value)
        self.assertEqual([row["path"] for row in got], ["PLAN.md", "plan.md"])


class LoadOutputsJsonText(_WorldCase):
    """O4 — JSON text is a distinct load_outputs seam, not validate_outputs."""

    def test_load_outputs_is_the_json_text_seam(self):
        fn = getattr(envelope, "load_outputs", None)
        self.assertTrue(
            callable(fn),
            "missing public seam envelope.load_outputs(text, reserved=...)",
        )
        params = inspect.signature(fn).parameters
        self.assertIn("reserved", params)

    def test_well_formed_json_array_text_loads_to_the_native_list(self):
        native = osu.copy_native("check-tests")
        text = json.dumps(native)
        self.assertIsInstance(text, str, "plant: JSON text, not a list")
        self.assertEqual(json.loads(text), osu.NATIVE["check-tests"])
        got = osu.load_outputs(text)
        self.assertIsInstance(got, list)
        self.assertEqual(got, osu.NATIVE["check-tests"])

    def test_empty_json_array_text_loads_to_omission(self):
        text = "[]"
        self.assertEqual(json.loads(text), [])
        got = osu.load_outputs(text)
        self.assertEqual(got, [])

    def test_duplicate_json_object_members_are_refused(self):
        raw = DUP_MEMBER.read_text(encoding="utf-8")
        self.assertIn('"path":"A.md"', raw, "plant: first path member")
        self.assertIn('"path":"B.md"', raw, "plant: duplicate path member")
        self.assertGreater(raw.count('"path"'), 1)
        parsed = json.loads(raw)
        self.assertEqual(
            parsed[0]["path"], "B.md",
            "json.loads keeps the last member; load_outputs must still refuse",
        )
        with self.assertRaises(osu.outputs_error()):
            osu.load_outputs(raw)

    def test_json_null_text_is_refused(self):
        text = "null"
        self.assertIsNone(json.loads(text), "plant: JSON null")
        with self.assertRaises(osu.outputs_error()):
            osu.load_outputs(text)

    def test_malformed_json_text_is_refused(self):
        raw = '[{"kind":"file","path":"A.md","artifact":"a"'
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw)
        with self.assertRaises(osu.outputs_error()):
            osu.load_outputs(raw)

    def test_load_outputs_honours_reserved_after_decode(self):
        native = [
            {
                "kind": "file",
                "path": "adapter-state.json",
                "artifact": "state",
            },
        ]
        text = json.dumps(native)
        self.assertEqual(json.loads(text), native, "plant: well-formed JSON")
        with self.assertRaises(osu.outputs_error()):
            osu.load_outputs(text, reserved=("adapter-state.json",))


class TransportReservations(_WorldCase):
    """O6 — reserved transport leaves; scratch tmp is not transport."""

    def test_default_reserved_leaves_are_commit_msg_and_last_message(self):
        reserved = osu.transport_reserved()
        self.assertTrue(
            reserved.issuperset(osu.DEFAULT_RESERVED),
            "reserved leaves must include COMMIT_MSG and last-message.json; "
            f"got {reserved!r}",
        )
        self.assertNotIn("tmp", reserved)

    def test_declared_scratch_tmp_is_not_a_transport_reservation(self):
        reserved = osu.transport_reserved()
        self.assertNotIn("tmp", reserved)
        got = osu.validate_outputs(osu.copy_native("check-tests"))
        self.assertEqual(got, osu.NATIVE["check-tests"])

    def test_a_file_named_last_message_json_is_refused(self):
        value = [
            {
                "kind": "file",
                "path": "last-message.json",
                "artifact": "message",
            },
        ]
        self.assertEqual(value[0]["path"], "last-message.json")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_a_file_named_commit_msg_is_refused(self):
        value = [{"kind": "file", "path": "COMMIT_MSG", "artifact": "commit"}]
        self.assertEqual(value[0]["path"], "COMMIT_MSG")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value)

    def test_launch_supplied_reserved_leaves_also_collide(self):
        value = [
            {
                "kind": "file",
                "path": "adapter-state.json",
                "artifact": "state",
            },
        ]
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(
                value, reserved=("adapter-state.json",),
            )

    def test_a_scratch_that_contains_a_reserved_leaf_is_refused(self):
        value = [{"kind": "scratch", "path": "tmp"}]
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(
                value, reserved=("tmp/work",),
            )

    def test_a_file_under_a_reserved_directory_is_refused(self):
        value = [
            {
                "kind": "file",
                "path": "transport/leaf.md",
                "artifact": "leaf",
            },
        ]
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs(value, reserved=("transport",))


if __name__ == "__main__":
    unittest.main()
