"""record.py answers field for the uncited-correction gate.

Written from scope-gate-20260910.md item 8 and Dispatch §The record:
answers sits after follows, build writes [], validate accepts
[{key, disposition, disposed_at, citation {item, path}}] when present,
and a record minted before the field landed is not read as "none
answered". record.py is the subject and is not read here.

A missing answers key on FIELDS or a build that omits the list fails
the assertion below, not collection.
"""

from __future__ import annotations

import copy
import unittest

import launch_support as ls

from ops.testkit import World

SCOPE_REL = ".dev/ladder-inputs/scope-gate-20260910.md"


class _AnswersRecord(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="record-answers-")
        self.addCleanup(self.world.close)
        self.world.enter()

    def recmod(self):
        if getattr(self, "record", None) is None:
            self.record = ls.require_module(self, "record")
        return self.record

    def _minimum(self):
        return {
            "id": "20260826T000000Z-plan-grok-abc123",
            "lane": "dev",
            "stage": "plan",
            "unit": "work",
            "lineage": {"branch": "work", "base_sha": "a" * 40},
            "follows": [],
            "job": "plan",
            "role": "read",
            "dispatched_by": ls.AGENT,
            "at": {"launched": "2026-08-26T00:00:00Z", "closed": None},
            "snapshot": {
                "mode": "whole",
                "ref_name": "HEAD",
                "ref_sha": "b" * 40,
                "behind_tip": 0,
                "root": "/tmp/jobs/id/snapshot",
            },
            "harness": {
                "name": "grok",
                "version": "1.0.5",
                "isolation": {
                    "mechanism": "home",
                    "env": {"GROK_HOME": "/tmp/jobs/id/home/grok"},
                    "home": "/tmp/jobs/id/home/grok",
                    "auth_files": ["auth.json"],
                    "store": "/tmp/jobs/id/home/grok/sessions",
                    "observed": {"unresolved": "behavioural probe has not run"},
                },
                "sandbox": "plan",
                "containment": "policy",
                "argv": ["grok", "-s", "uuid"],
            },
            "model": {
                "requested": "alias-requested",
                "effort_requested": None,
                "effort_argv": [],
                "ran": None,
                "read_from": None,
            },
            "session": {
                "id": "00000000-0000-4000-8000-000000000001",
                "stream": None,
                "stream_sha256_at_close": None,
            },
            "brief": {
                "template": {
                    "path": "ops/devlane/task/jobs.json",
                    "sha256": "c" * 64,
                },
                "scope": "pin",
                "inputs": [],
                "sha256": "d" * 64,
                "bytes": 3,
            },
            "caps": {"cap-out": 500000, "source": "wires.py"},
            "overrides": [],
            "attempts": [],
            "result": None,
            "status": "launched",
        }

    def built(self, **overrides):
        payload = dict(self._minimum())
        payload.update(overrides)
        record = self.recmod()
        try:
            return record.build(payload)
        except TypeError:
            return record.build(**payload)

    def _raises_naming(self, rec, field):
        with self.assertRaises(Exception) as caught:
            self.recmod().validate(rec)
        self.assertIn(field, str(caught.exception).lower())
        return caught.exception

    def _entry(self, **overrides):
        row = {
            "key": "20260901T000000Z-review-grok-aaaaaa#1",
            "disposition": "fixed",
            "disposed_at": "2026-09-10T11:00:00Z",
            "citation": {"item": 3, "path": SCOPE_REL},
        }
        row.update(overrides)
        return row


class BuildSetsEmptyAnswers(_AnswersRecord):
    """Item 8 — build writes answers [] when the caller names none."""

    def test_build_sets_answers_to_an_empty_list(self):
        payload = self._minimum()
        self.assertNotIn("answers", payload)
        rec = self.built()
        self.assertIn("answers", rec)
        self.assertEqual(rec["answers"], [])
        self.assertIsInstance(rec["answers"], list)

    def test_fields_places_answers_immediately_after_follows(self):
        fields = tuple(getattr(self.recmod(), "FIELDS", ()) or ())
        self.assertEqual(fields, ls.RECORD_FIELDS)
        self.assertIn("follows", fields)
        self.assertIn("answers", fields)
        self.assertEqual(
            fields[fields.index("follows") + 1],
            "answers",
            "item 8: answers follows follows in the contracted order",
        )


class ValidateAnswersShape(_AnswersRecord):
    """Item 8 — the declared shape is accepted; a malformed entry is
    refused naming the field; absence is historical, not empty."""

    def test_validate_accepts_the_declared_shape(self):
        rec = self.built()
        rec["answers"] = [self._entry()]
        out = self.recmod().validate(rec)
        self.assertEqual(len(out["answers"]), 1)
        row = out["answers"][0]
        self.assertEqual(row["key"], self._entry()["key"])
        self.assertEqual(row["disposition"], "fixed")
        self.assertEqual(row["disposed_at"], "2026-09-10T11:00:00Z")
        self.assertEqual(str(row["citation"]["item"]), "3")
        self.assertEqual(row["citation"]["path"], SCOPE_REL)

    def test_validate_refuses_answers_that_is_not_a_list(self):
        rec = self.built()
        rec["answers"] = self._entry()["key"]
        self.assertIsInstance(rec["answers"], str)
        self._raises_naming(rec, "answers")

    def test_validate_refuses_an_entry_missing_citation(self):
        rec = self.built()
        row = self._entry()
        del row["citation"]
        rec["answers"] = [row]
        self.assertNotIn("citation", rec["answers"][0])
        self._raises_naming(rec, "answers")

    def test_validate_refuses_citation_missing_item(self):
        rec = self.built()
        rec["answers"] = [self._entry(citation={"path": SCOPE_REL})]
        self.assertNotIn("item", rec["answers"][0]["citation"])
        self._raises_naming(rec, "answers")

    def test_validate_refuses_citation_missing_path(self):
        rec = self.built()
        rec["answers"] = [self._entry(citation={"item": 3})]
        self.assertNotIn("path", rec["answers"][0]["citation"])
        self._raises_naming(rec, "answers")

    def test_validate_tolerates_absence_of_the_answers_key(self):
        rec = self.built()
        rec.pop("answers", None)
        self.assertNotIn("answers", rec)
        out = self.recmod().validate(copy.deepcopy(rec))
        self.assertNotIn(
            "answers",
            out,
            "item 8: absence is a dated fact, not answers: [] "
            "(none answered)",
        )
        self.assertEqual(out["id"], rec["id"])


if __name__ == "__main__":
    unittest.main()
