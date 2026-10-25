"""Harnesses whose argv carries no schema get it appended to the brief.

Written from CONTRACT.md §Template values and the lane-fix batch plan
§3.6: after render, for every harness whose argv does not carry
``--json-schema`` (codex and grok), the launcher appends the envelope
schema to ``prompt.txt``. ``brief.sha256`` digests the bytes handed
over, appended section included. Claude keeps ``--json-schema`` and
gets no appended section.

The schema oracle is envelope.ENVELOPE_SCHEMA, not a launcher export.
A launcher that appends its own (possibly stale) ENVELOPE_SCHEMA_JSON
must not agree with this suite by construction. launch.py was not read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import launch_support as ls

from ops.testkit import World  # noqa: F401 - _TempLaunch owns the world

ENVELOPE_PY = ls.APP.parents[0] / "task" / "envelope.py"

SCHEMA_SECTION_LEAD = (
    "The envelope shape, as JSON Schema. Every key in `required` is "
    "present; no other top-level key; the types are exact (findings "
    "are objects, counts are integers, artifacts is an object of "
    "strings):"
)


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


class _BriefSchema(ls._TempLaunch):
    def independent_schema(self):
        envmod = ls.load_path(self, ENVELOPE_PY, "brief_schema_envelope")
        schema = getattr(envmod, "ENVELOPE_SCHEMA", None)
        self.assertIsInstance(
            schema, dict,
            "envelope.ENVELOPE_SCHEMA is the oracle for the appended shape",
        )
        findings = (schema.get("properties") or {}).get("findings") or {}
        items = findings.get("items") if isinstance(findings, dict) else None
        self.assertIn(
            "object", _type_list(items) if isinstance(items, dict) else [],
            "oracle findings items are objects, not strings",
        )
        counts = (schema.get("properties") or {}).get("counts") or {}
        self.assertIn(
            "object", _type_list(counts) if isinstance(counts, dict) else [],
            "oracle counts is an object of integers",
        )
        return schema

    def handed_brief(self, rec, witness, *, harness):
        job_dir = Path(rec["snapshot"]["root"]).parent
        path = job_dir / "prompt.txt"
        self.assertTrue(path.is_file(), f"prompt.txt missing under {job_dir}")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text, "prompt.txt is empty")
        if harness == "grok":
            received = witness.get("prompt_text") or ""
        else:
            received = witness.get("stdin") or ""
        self.assertTrue(
            received,
            f"{harness} must receive the rendered brief "
            f"(stdin/prompt_text empty; prompt.txt has {len(text)} bytes)",
        )
        return text, received, path

    def schema_from_brief(self, text):
        self.assertIn(SCHEMA_SECTION_LEAD, text)
        rest = text[text.rindex(SCHEMA_SECTION_LEAD) + len(SCHEMA_SECTION_LEAD):]
        start = rest.find("{")
        self.assertGreaterEqual(
            start, 0,
            "the envelope schema JSON follows the lead-in, "
            f"got tail={rest[:240]!r}",
        )
        obj, end = json.JSONDecoder().raw_decode(rest[start:])
        self.assertIsInstance(obj, dict, obj)
        self.assertFalse(
            rest[start + end:].strip(),
            "the envelope schema is the end of the handed brief; "
            f"trailing={rest[start + end:]!r}",
        )
        return obj

    def assert_brief_ends_with_independent_schema(self, received, text, schema):
        for blob, label in ((received, "received"), (text, "prompt.txt")):
            got = self.schema_from_brief(blob)
            self.assertEqual(
                got, schema,
                f"{label} schema must equal envelope.ENVELOPE_SCHEMA, "
                "not a launcher-local copy",
            )
            findings = (got.get("properties") or {}).get("findings") or {}
            items = findings.get("items") if isinstance(findings, dict) else None
            self.assertIn(
                "object", _type_list(items) if isinstance(items, dict) else [],
                f"{label} findings items must be objects: {items!r}",
            )

    def assert_digest_matches_file(self, rec, path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(
            rec["brief"]["sha256"], digest,
            "brief.sha256 digests the bytes handed over, schema section "
            "included",
        )


class CodexAndGrokBriefsEndWithTheEnvelopeSchema(_BriefSchema):
    """Codex (stdin) and grok (prompt_text) carry envelope.ENVELOPE_SCHEMA."""

    def test_codex_brief_ends_with_the_envelope_schema(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="codex", stage="plan",
        )
        schema = self.independent_schema()
        text, received, path = self.handed_brief(
            rec, witness, harness="codex",
        )
        self.assert_brief_ends_with_independent_schema(received, text, schema)
        self.assert_digest_matches_file(rec, path)
        argv = [str(a) for a in witness["argv"]]
        self.assertNotIn("--json-schema", argv)

    def test_grok_brief_ends_with_the_envelope_schema(self):
        rec, witness, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        schema = self.independent_schema()
        text, received, path = self.handed_brief(
            rec, witness, harness="grok",
        )
        self.assert_brief_ends_with_independent_schema(received, text, schema)
        self.assert_digest_matches_file(rec, path)
        argv = [str(a) for a in witness["argv"]]
        self.assertNotIn("--json-schema", argv)


class ClaudeIsExcludedFromTheAppendedSchemaSection(_BriefSchema):
    """Claude keeps --json-schema. The exclusion is pinned in the same
    test that requires grok to receive the section, so a no-op (nobody
    gets the section) and an append-to-all are both red.
    """

    def _schema_from_json_schema_flag(self, argv):
        argv = [str(a) for a in argv]
        self.assertIn("--json-schema", argv)
        value = argv[argv.index("--json-schema") + 1]
        if str(value).lstrip().startswith("{"):
            return json.loads(value)
        path = Path(value)
        self.assertTrue(path.is_file(), f"--json-schema path missing: {value!r}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _record_for_harness(self, harness):
        matched = []
        for path in self.record_files():
            rec = json.loads(path.read_text(encoding="utf-8"))
            if (rec.get("harness") or {}).get("name") == harness:
                matched.append(rec)
        self.assertEqual(
            len(matched), 1,
            f"expected one {harness} record, got "
            f"{[m.get('id') for m in matched]}",
        )
        return matched[0]

    def test_claude_does_not_receive_the_schema_section_grok_does(self):
        grok_rec, grok_w, *_ = self.launch_ok(
            job="plan", harness="grok", stage="plan",
        )
        self.witness.unlink(missing_ok=True)
        self.start_witness.unlink(missing_ok=True)
        code, out, err = self.dispatch(
            self.argv_for(job="plan", harness="claude", stage="plan"),
        )
        self.assertEqual(code, 0, self.combined(out, err))
        claude_w = self.read_witness()
        claude_rec = self._record_for_harness("claude")
        self.assertEqual(claude_rec.get("status"), "closed", claude_rec)
        schema = self.independent_schema()
        grok_text, grok_received, grok_path = self.handed_brief(
            grok_rec, grok_w, harness="grok",
        )
        claude_text, claude_received, claude_path = self.handed_brief(
            claude_rec, claude_w, harness="claude",
        )
        self.assert_brief_ends_with_independent_schema(
            grok_received, grok_text, schema,
        )
        self.assert_digest_matches_file(grok_rec, grok_path)
        grok_argv = [str(a) for a in grok_w["argv"]]
        self.assertNotIn("--json-schema", grok_argv)

        self.assertNotIn(SCHEMA_SECTION_LEAD, claude_received)
        self.assertNotIn(SCHEMA_SECTION_LEAD, claude_text)
        claude_argv = [str(a) for a in claude_w["argv"]]
        self.assertIn("--json-schema", claude_argv)
        self.assertEqual(
            self._schema_from_json_schema_flag(claude_argv), schema,
            "claude --json-schema is envelope.ENVELOPE_SCHEMA",
        )
        self.assert_digest_matches_file(claude_rec, claude_path)
