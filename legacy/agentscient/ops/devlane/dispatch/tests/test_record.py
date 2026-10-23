"""record.py: one implementation per rule, the way envelope.py is.

Written from CONTRACT.md §Dispatch The record, before the module
existed. Each test names the contract rule it pins:

  Rec1  fields land in the contracted order (input order is not enough)
  Rec2  status launched carries no result
  Rec3  model.ran is the stream value, or null with a note — never the alias
  Rec4  observed is the probe's result or {"unresolved": ...}, never false
  Rec5  lane is "dev"
  Rec6  validate names the field it refuses
  Rec7  follows is a list of ids; unit is a string
  Rec8  a record that fails validation is never "valid"
  Rec9  schema-complete: lineage, role, snapshot.mode, containment, status
  Rec10 optional brief.policy: absent is legacy; string/null/empty object refuse
  Rec11 required binding fields, types, schema/path, Git-id and digest shapes
        (full SHA-1 or SHA-256 Git ids)
  Rec12 template_sha256 equals brief.template.sha256
  Rec13 maintenance null or exactly standing/per_job sorted unique rows
  Rec14 profile syntax [a-z][a-z0-9-]* including digits; no membership lookup
"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import launch_support as ls

from ops.testkit import World


class _RecordCase(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="record-")
        self.world.enter()
        self.addCleanup(self.world.close)

    def recmod(self):
        # Load inside the test method so a missing file is an empty
        # stub, not a setUp ERROR, and the assertion below is the red.
        if getattr(self, "record", None) is None:
            self.record = ls.require_module(self, "record")
        return self.record

    def built(self, **overrides):
        record = self.recmod()
        payload = dict(self._minimum())
        payload.update(overrides)
        try:
            return record.build(payload)
        except TypeError:
            return record.build(**payload)

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
                "template": {"path": "ops/devlane/task/jobs.json", "sha256": "c" * 64},
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

    def _raises_naming(self, rec, field):
        with self.assertRaises(Exception) as caught:
            self.recmod().validate(rec)
        self.assertIn(field, str(caught.exception).lower())
        return caught.exception


class FieldOrderIsTheContract(_RecordCase):
    """Rec1 — a caller diffing two records sees the same fields in the
    same places. envelope.py already treats key order as shape.
    Input is deliberately reversed so an echo-the-payload stub fails."""

    def test_built_record_keys_match_the_contracted_order(self):
        payload = self._minimum()
        scrambled = {k: payload[k] for k in reversed(list(payload))}
        self.assertNotEqual(tuple(scrambled), ls.RECORD_FIELDS)
        record = self.recmod()
        try:
            rec = record.build(scrambled)
        except TypeError:
            rec = record.build(**scrambled)
        self.assertEqual(tuple(rec), ls.RECORD_FIELDS)

    def test_module_declares_the_same_order(self):
        declared = getattr(self.recmod(), "FIELDS", None)
        self.assertIsNotNone(declared, "FIELDS is the order contract")
        self.assertEqual(tuple(declared), ls.RECORD_FIELDS)


class LaunchedCarriesNoResult(_RecordCase):
    """Rec2 — written at launch with status: launched and no result."""

    def test_a_launched_record_has_result_none(self):
        rec = self.built(
            status="launched",
            result={
                "head": "b" * 40,
                "changed_paths": [],
                "residual_paths": [],
                "envelope": {},
            },
        )
        self.assertEqual(rec["status"], "launched")
        self.assertIsNone(rec["result"])

    def test_validate_refuses_a_launched_record_that_already_has_a_result(self):
        rec = self.built()
        rec["result"] = {
            "head": "b" * 40,
            "changed_paths": [],
            "residual_paths": [],
            "envelope": {},
        }
        rec["status"] = "launched"
        self._raises_naming(rec, "result")


class ModelRanIsNeverTheAliasCopiedOver(_RecordCase):
    """Rec3 — requested is the alias; ran is the stream; no stream →
    null with a note, never the alias copied over."""

    def test_ran_null_is_not_silently_filled_with_requested(self):
        rec = self.built()
        rec["model"]["ran"] = None
        rec["model"]["requested"] = "alias-requested"
        rec["model"]["read_from"] = None
        rec["model"].pop("note", None)
        rec.pop("note", None)
        rec = self.recmod().validate(rec)
        self.assertIsNone(rec["model"]["ran"])
        self.assertEqual(rec["model"]["requested"], "alias-requested")
        self.assertNotEqual(rec["model"]["ran"], rec["model"]["requested"])
        note = rec.get("note") or rec["model"].get("note")
        self.assertTrue(
            str(note or "").strip(),
            "null ran must carry a note, not silence",
        )

    def test_copying_the_alias_into_ran_without_a_stream_is_refused(self):
        rec = self.built()
        rec["model"]["ran"] = rec["model"]["requested"]
        rec["model"]["read_from"] = None
        rec["session"]["stream"] = None
        self._raises_naming(rec, "ran")


class ObservedIsNeverAManufacturedFalse(_RecordCase):
    """Rec4 / plan (u) — observed is verbatim or {"unresolved": why}."""

    def test_unresolved_is_the_honest_absent_observation(self):
        payload = self._minimum()
        del payload["harness"]["isolation"]["observed"]
        record = self.recmod()
        try:
            rec = record.build(payload)
        except TypeError:
            rec = record.build(**payload)
        isolation = rec["harness"]["isolation"]
        self.assertIn("observed", isolation)
        observed = isolation["observed"]
        self.assertIsInstance(observed, dict)
        self.assertIn("unresolved", observed)
        self.assertNotIn(observed.get("unresolved"), (None, "", False))

    def test_observed_false_is_refused(self):
        rec = self.built()
        rec["harness"]["isolation"]["observed"] = False
        self._raises_naming(rec, "observed")

    def test_observed_true_without_evidence_is_refused(self):
        rec = self.built()
        rec["harness"]["isolation"]["observed"] = True
        self._raises_naming(rec, "observed")


class LaneIsDev(_RecordCase):
    """Rec5 — every record is lane: "dev"; the launcher refuses main."""

    def test_built_lane_is_dev(self):
        rec = self.built(lane="prod")
        self.assertEqual(rec["lane"], "dev")

    def test_a_product_lane_is_refused(self):
        rec = self.built()
        rec["lane"] = "prod"
        self._raises_naming(rec, "lane")


class ValidateNamesTheField(_RecordCase):
    """Rec6 — a record that fails validation is never committed; the
    launcher exits non-zero naming the field."""

    def test_a_missing_field_is_named(self):
        rec = self.built()
        del rec["job"]
        self._raises_naming(rec, "job")


class FollowsAndUnitAreShaped(_RecordCase):
    """Rec7 / plan (q) — follows [ids], unit a string defaulting to the
    branch. Neither is context.prior."""

    def test_follows_is_a_list_and_unit_is_a_string(self):
        rec = self.built(
            follows=["20260826T000000Z-plan-grok-000001"],
            unit="",
        )
        self.assertIsInstance(rec["follows"], list)
        self.assertEqual(len(rec["follows"]), 1)
        self.assertIsInstance(rec["unit"], str)
        self.assertTrue(rec["unit"].strip(), "unit defaults to the lineage branch")
        self.assertEqual(rec["unit"], rec["lineage"]["branch"])

    def test_follows_default_is_an_empty_list_not_absent(self):
        payload = self._minimum()
        del payload["follows"]
        record = self.recmod()
        try:
            rec = record.build(payload)
        except TypeError:
            rec = record.build(**payload)
        self.assertIn("follows", rec)
        self.assertEqual(rec["follows"], [])


class StatusIsTheEnum(_RecordCase):
    """Rec8 — status is launched | closed | died."""

    def test_unknown_status_is_refused(self):
        rec = self.built()
        rec["status"] = "finished"
        self._raises_naming(rec, "status")


class SchemaIsComplete(_RecordCase):
    """Rec9 — a shallow validator that accepts lineage=None, role=execute,
    or snapshot.mode=shared-and-wrong is not the contract."""

    def test_lineage_none_is_refused(self):
        rec = self.built()
        rec["lineage"] = None
        self._raises_naming(rec, "lineage")

    def test_lineage_missing_branch_is_refused(self):
        rec = self.built()
        rec["lineage"] = {"base_sha": "a" * 40}
        self._raises_naming(rec, "lineage")

    def test_role_execute_is_refused(self):
        rec = self.built()
        rec["role"] = "execute"
        self._raises_naming(rec, "role")

    def test_snapshot_mode_shared_is_refused(self):
        rec = self.built()
        rec["snapshot"]["mode"] = "shared-and-wrong"
        self._raises_naming(rec, "mode")

    def test_containment_must_be_os_or_policy(self):
        rec = self.built()
        rec["harness"]["containment"] = "hope"
        self._raises_naming(rec, "containment")

    def test_follows_must_be_a_list(self):
        rec = self.built()
        rec["follows"] = "20260826T000000Z-plan-grok-000001"
        self._raises_naming(rec, "follows")


class EffortArgvCarriesTheDial(_RecordCase):
    """model.effort_argv is the exact tokens that reached the command
    line: a list; when effort_requested is set, non-empty and one token
    equals the requested value; every token appears, in order, in
    harness.argv; when unset, []. Missing is a refusal, not silence.
    Findings D3 and G4: a substring check accepted high inside xhigh
    and never compared effort_argv with harness.argv."""

    def test_effort_requested_carried_in_effort_argv_validates(self):
        rec = self.built()
        rec["model"]["effort_requested"] = "high"
        rec["model"]["effort_argv"] = ["--reasoning-effort", "high"]
        rec["harness"]["argv"] = [
            "grok", "-s", "uuid", "--reasoning-effort", "high",
        ]
        rec = self.recmod().validate(rec)
        self.assertEqual(rec["model"]["effort_requested"], "high")
        self.assertEqual(
            rec["model"]["effort_argv"], ["--reasoning-effort", "high"],
        )
        argv = rec["harness"]["argv"]
        self.assertTrue(
            any(argv[i:i + 2] == ["--reasoning-effort", "high"]
                for i in range(len(argv) - 1)),
            argv,
        )

    def test_effort_requested_not_carried_refuses_naming_effort_argv(self):
        rec = self.built()
        rec["model"]["effort_requested"] = "high"
        rec["model"]["effort_argv"] = []
        self._raises_naming(rec, "model.effort_argv")

    def test_effort_argv_without_effort_requested_is_refused(self):
        rec = self.built()
        rec["model"]["effort_requested"] = None
        rec["model"]["effort_argv"] = ["--reasoning-effort", "high"]
        self._raises_naming(rec, "model.effort_argv")

    def test_effort_argv_that_is_not_a_list_is_refused(self):
        rec = self.built()
        rec["model"]["effort_requested"] = "high"
        rec["model"]["effort_argv"] = "--reasoning-effort high"
        self.assertIsInstance(rec["model"]["effort_argv"], str)
        self.assertNotIsInstance(rec["model"]["effort_argv"], list)
        self.assertIn("high", rec["model"]["effort_argv"])
        self._raises_naming(rec, "model.effort_argv")

    def test_high_inside_xhigh_is_not_an_exact_token_and_is_refused(self):
        rec = self.built()
        rec["model"]["effort_requested"] = "high"
        rec["model"]["effort_argv"] = ["--reasoning-effort", "xhigh"]
        rec["harness"]["argv"] = [
            "grok", "--reasoning-effort", "xhigh",
        ]
        self.assertIn("high", "xhigh")
        self.assertNotIn("high", rec["model"]["effort_argv"])
        self._raises_naming(rec, "model.effort_argv")

    def test_a_token_that_merely_contains_xhigh_is_refused(self):
        rec = self.built()
        rec["model"]["effort_requested"] = "xhigh"
        rec["model"]["effort_argv"] = ["not-xhigh"]
        rec["harness"]["argv"] = ["codex", "exec", "--model", "M"]
        self.assertIn("xhigh", "not-xhigh")
        self.assertNotIn("xhigh", rec["model"]["effort_argv"])
        self._raises_naming(rec, "model.effort_argv")

    def test_effort_argv_tokens_missing_from_harness_argv_are_refused(self):
        rec = self.built()
        rec["model"]["effort_requested"] = "xhigh"
        rec["model"]["effort_argv"] = ["--reasoning-effort", "xhigh"]
        rec["harness"]["argv"] = ["codex", "exec", "--model", "M"]
        self.assertNotIn("--reasoning-effort", rec["harness"]["argv"])
        self.assertNotIn("xhigh", rec["harness"]["argv"])
        self._raises_naming(rec, "argv")

    def test_effort_argv_tokens_out_of_order_in_harness_argv_are_refused(self):
        rec = self.built()
        rec["model"]["effort_requested"] = "high"
        rec["model"]["effort_argv"] = ["--reasoning-effort", "high"]
        rec["harness"]["argv"] = ["grok", "high", "--reasoning-effort"]
        self.assertIn("--reasoning-effort", rec["harness"]["argv"])
        self.assertIn("high", rec["harness"]["argv"])
        self.assertLess(
            rec["harness"]["argv"].index("high"),
            rec["harness"]["argv"].index("--reasoning-effort"),
        )
        self._raises_naming(rec, "argv")


class CollectedResultRequiresAGreenVerify(_RecordCase):
    """A present result.collected must carry at, by, head, and verify.exit == 0."""

    def _closed_with_collected(self, collected):
        rec = self.built()
        rec["status"] = "closed"
        rec["at"]["closed"] = "2026-09-03T00:00:01Z"
        rec["result"] = {
            "head": rec["snapshot"]["ref_sha"],
            "changed_paths": [],
            "residual_paths": [],
            "envelope": {
                "job": "implement",
                "status": "invalid",
                "verdict": None,
                "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
                "findings": [],
                "artifacts": {},
                "spend": {"harness": "codex", "total": 0, "out": 0, "runs": 1},
                "stamp": {
                    "ref": rec["snapshot"]["ref_sha"],
                    "started": None,
                    "ended": None,
                },
                "note": "timeout",
            },
            "collected": collected,
        }
        return rec

    def _green_collected(self, **overrides):
        payload = {
            "at": "2026-09-03T00:00:02Z",
            "by": ls.AGENT,
            "head": "c" * 40,
            "verify": {"command": "true", "exit": 0},
            "reason": None,
        }
        payload.update(overrides)
        return payload

    def test_a_collected_result_without_a_green_verify_is_refused(self):
        rec = self._closed_with_collected(self._green_collected(
            verify={"command": "false", "exit": 1},
        ))
        self.assertEqual(rec["result"]["collected"]["verify"]["exit"], 1)
        self.assertNotEqual(rec["result"]["collected"]["verify"]["exit"], 0)
        self._raises_naming(rec, "result.collected")

    def test_a_collected_result_missing_at_is_refused(self):
        collected = self._green_collected()
        del collected["at"]
        rec = self._closed_with_collected(collected)
        self.assertNotIn("at", rec["result"]["collected"])
        self._raises_naming(rec, "result.collected")

    def test_a_collected_result_missing_by_is_refused(self):
        collected = self._green_collected()
        del collected["by"]
        rec = self._closed_with_collected(collected)
        self.assertNotIn("by", rec["result"]["collected"])
        self._raises_naming(rec, "result.collected")

    def test_a_collected_result_missing_head_is_refused(self):
        collected = self._green_collected()
        del collected["head"]
        rec = self._closed_with_collected(collected)
        self.assertNotIn("head", rec["result"]["collected"])
        self._raises_naming(rec, "result.collected")

    def test_a_collected_result_with_a_non_string_head_is_refused(self):
        rec = self._closed_with_collected(self._green_collected(head=0))
        self.assertEqual(rec["result"]["collected"]["head"], 0)
        self._raises_naming(rec, "result.collected")

    def test_a_collected_result_with_json_false_verify_exit_is_refused(self):
        """Security finding 5: JSON false equals 0 in Python; a forged
        verify.exit must still be refused."""
        rec = self._closed_with_collected(self._green_collected(
            verify={"command": "true", "exit": False},
        ))
        exit_value = rec["result"]["collected"]["verify"]["exit"]
        self.assertIs(exit_value, False)
        self.assertEqual(exit_value, 0)
        self.assertIs(type(exit_value), bool)
        self._raises_naming(rec, "result.collected")


class PolicyPrimitives(_RecordCase):
    """Exercise shared mechanics once; the binding test checks every caller."""

    def test_match_rejects_non_strings_with_the_field_name(self):
        record = self.recmod()
        with self.assertRaisesRegex(record.RecordError, "receipt.sha256"):
            record._policy_match(["ab"], r"[a-z]+", "receipt.sha256", "must match")

    def test_match_requires_the_whole_value(self):
        record = self.recmod()
        with self.assertRaisesRegex(record.RecordError, "receipt.record"):
            record._policy_match("ab!", r"[a-z]+", "receipt.record", "must match")

    def test_match_accepts_a_matching_string(self):
        self.recmod()._policy_match("ab", r"[a-z]+", "field", "must match")

    def test_receipt_requires_an_object(self):
        record = self.recmod()
        with self.assertRaisesRegex(record.RecordError, "receipt must be an object"):
            record._policy_receipt("D-LANES-1", "receipt")

    def test_receipt_requires_a_record(self):
        record = self.recmod()
        with self.assertRaisesRegex(record.RecordError, r"receipt\.record"):
            record._policy_receipt({"sha256": "a" * 64}, "receipt")

    def test_receipt_requires_a_digest(self):
        record = self.recmod()
        with self.assertRaisesRegex(record.RecordError, r"receipt\.sha256"):
            record._policy_receipt({"record": "D-LANES-1"}, "receipt")


class BriefPolicyBindingShape(_RecordCase):
    """Keep policy composition here, shared match/receipt mechanics above.

    The binding check observes real validators (wraps, not replacement
    results). Every field must reach the shared boundary with its value,
    grammar and qualified refusal name. Repeating the same primitive's
    negative examples through each caller adds no separate contract.
    """

    _SCHEMA = "agentscient.lane-policy/v1"
    _PATH = "ops/lane-policy.json"
    _VALID_REF = "c" * 40
    _REQUIRED = (
        "schema", "path", "ref", "sha256", "admission", "profile",
        "template_sha256", "grants_sha256", "maintenance",
    )

    def _binding(self, **overrides):
        policy = {
            "schema": self._SCHEMA,
            "path": self._PATH,
            "ref": self._VALID_REF,
            "sha256": "e" * 64,
            "admission": {"record": "D-LANES-1", "sha256": "f" * 64},
            "profile": "fixture-only",
            "template_sha256": self._minimum()["brief"]["template"]["sha256"],
            "grants_sha256": "1" * 64,
            "maintenance": None,
        }
        policy.update(overrides)
        return policy

    def _with_policy(self, policy):
        rec = self.built()
        rec["brief"]["policy"] = copy.deepcopy(policy)
        return rec

    def _row(self, record_id, digest_char):
        return {"record": record_id, "sha256": digest_char * 64}

    def _maintenance(self, standing=None, per_job=None):
        return {"standing": standing or [], "per_job": per_job or []}

    def test_all_policy_fields_use_the_shared_contracts(self):
        record = self.recmod()
        policy = self._binding(maintenance=self._maintenance(
            standing=[self._row("D-LANES-1", "2"), self._row("D-LANES-2", "3")],
            per_job=[self._row("D-JOB-1", "4"), self._row("D-JOB-2", "5")],
        ))
        rec = self._with_policy(policy)
        with patch.object(record, "_policy_match", wraps=record._policy_match) as match, \
                patch.object(record, "_policy_receipt", wraps=record._policy_receipt) as receipt:
            self.assertEqual(record.validate(rec)["brief"]["policy"], policy)
        expected = {
            "brief.policy.ref": (policy["ref"], r"(?:[0-9a-f]{40}|[0-9a-f]{64})"),
            "brief.policy.profile": (policy["profile"], r"[a-z][a-z0-9-]*"),
        }
        for key in ("sha256", "template_sha256", "grants_sha256"):
            expected[f"brief.policy.{key}"] = (policy[key], r"[0-9a-f]{64}")
        receipts = {"brief.policy.admission": policy["admission"]}
        for collection, rows in policy["maintenance"].items():
            for i, row in enumerate(rows):
                receipts[f"brief.policy.maintenance.{collection}[{i}]"] = row
        self.assertCountEqual(
            [(call.args[1], call.args[0]) for call in receipt.call_args_list],
            list(receipts.items()),
        )
        for field, row in receipts.items():
            expected[field + ".record"] = (row["record"], r"D-[A-Z0-9]+-[0-9]+")
            expected[field + ".sha256"] = (row["sha256"], r"[0-9a-f]{64}")
        self.assertCountEqual(
            [(call.args[2], (call.args[0], call.args[1])) for call in match.call_args_list],
            list(expected.items()),
        )

    def test_sha256_git_object_ids_also_validate(self):
        policy = self._binding(ref="a" * 64)
        self.assertEqual(self.recmod().validate(self._with_policy(policy))["brief"]["policy"], policy)

    def test_a_string_policy_is_refused(self):
        rec = self._with_policy("agentscient.lane-policy/v1")
        self.assertEqual(rec["brief"]["policy"], "agentscient.lane-policy/v1")
        self.assertIsInstance(rec["brief"]["policy"], str)
        self._raises_naming(rec, "policy")


    def test_a_null_policy_is_refused(self):
        rec = self._with_policy(None)
        self.assertIsNone(rec["brief"]["policy"])
        self.assertIn("policy", rec["brief"])
        self._raises_naming(rec, "policy")


    def test_each_required_binding_field_missing_is_refused(self):
        for field in self._REQUIRED:
            with self.subTest(field=field):
                policy = self._binding()
                del policy[field]
                rec = self._with_policy(policy)
                self.assertNotIn(field, rec["brief"]["policy"])
                self._raises_naming(rec, "brief.policy." + field)


    def test_wrong_schema_value_is_refused(self):
        policy = self._binding(schema="agentscient.lane-policy/v0")
        rec = self._with_policy(policy)
        self.assertEqual(rec["brief"]["policy"]["schema"], "agentscient.lane-policy/v0")
        self.assertNotEqual(rec["brief"]["policy"]["schema"], self._SCHEMA)
        self._raises_naming(rec, "schema")


    def test_wrong_path_is_refused(self):
        policy = self._binding(path="ops/conductor/lane-policy.json")
        rec = self._with_policy(policy)
        self.assertEqual(
            rec["brief"]["policy"]["path"], "ops/conductor/lane-policy.json",
        )
        self.assertNotEqual(rec["brief"]["policy"]["path"], self._PATH)
        self._raises_naming(rec, "policy.path")


    def test_template_sha256_unequal_to_brief_template_is_refused(self):
        policy = self._binding(template_sha256="a" * 64)
        rec = self._with_policy(policy)
        self.assertEqual(rec["brief"]["policy"]["template_sha256"], "a" * 64)
        self.assertEqual(len(rec["brief"]["policy"]["template_sha256"]), 64)
        self.assertNotEqual(
            rec["brief"]["policy"]["template_sha256"],
            rec["brief"]["template"]["sha256"],
        )
        self._raises_naming(rec, "template_sha256")


    def test_maintenance_list_is_refused(self):
        rec = self._with_policy(self._binding(maintenance=[]))
        self.assertEqual(rec["brief"]["policy"]["maintenance"], [])
        self._raises_naming(rec, "maintenance")


    def test_maintenance_missing_standing_is_refused(self):
        rec = self._with_policy(self._binding(maintenance={"per_job": []}))
        self.assertNotIn("standing", rec["brief"]["policy"]["maintenance"])
        self._raises_naming(rec, "maintenance")


    def test_maintenance_missing_per_job_is_refused(self):
        rec = self._with_policy(self._binding(maintenance={"standing": []}))
        self.assertNotIn("per_job", rec["brief"]["policy"]["maintenance"])
        self._raises_naming(rec, "maintenance")


    def test_maintenance_extra_key_is_refused(self):
        maint = self._maintenance()
        maint["purpose"] = "widen"
        rec = self._with_policy(self._binding(maintenance=maint))
        self.assertEqual(
            set(rec["brief"]["policy"]["maintenance"]),
            {"standing", "per_job", "purpose"},
        )
        self._raises_naming(rec, "maintenance")


    def test_standing_that_is_not_a_list_is_refused(self):
        rec = self._with_policy(self._binding(maintenance={
            "standing": self._row("D-LANES-1", "2"),
            "per_job": [],
        }))
        self.assertIsInstance(rec["brief"]["policy"]["maintenance"]["standing"], dict)
        self._raises_naming(rec, "maintenance")


    def test_per_job_that_is_not_a_list_is_refused(self):
        rec = self._with_policy(self._binding(maintenance={
            "standing": [],
            "per_job": self._row("D-JOB-1", "3"),
        }))
        self.assertIsInstance(rec["brief"]["policy"]["maintenance"]["per_job"], dict)
        self._raises_naming(rec, "maintenance")


    def test_standing_not_sorted_by_record_id_is_refused(self):
        standing = [self._row("D-LANES-2", "2"), self._row("D-LANES-1", "3")]
        rec = self._with_policy(self._binding(maintenance=self._maintenance(
            standing=standing,
        )))
        ids = [row["record"] for row in rec["brief"]["policy"]["maintenance"]["standing"]]
        self.assertEqual(ids, ["D-LANES-2", "D-LANES-1"])
        self.assertNotEqual(ids, sorted(set(ids)))
        self._raises_naming(rec, "maintenance")


    def test_duplicate_standing_record_id_is_refused(self):
        standing = [self._row("D-LANES-1", "2"), self._row("D-LANES-1", "3")]
        rec = self._with_policy(self._binding(maintenance=self._maintenance(
            standing=standing,
        )))
        ids = [row["record"] for row in rec["brief"]["policy"]["maintenance"]["standing"]]
        self.assertEqual(ids, ["D-LANES-1", "D-LANES-1"])
        self.assertNotEqual(len(ids), len(set(ids)))
        self._raises_naming(rec, "maintenance")


    def test_per_job_not_sorted_by_record_id_is_refused(self):
        per_job = [self._row("D-JOB-2", "4"), self._row("D-JOB-1", "5")]
        rec = self._with_policy(self._binding(maintenance=self._maintenance(
            per_job=per_job,
        )))
        ids = [row["record"] for row in rec["brief"]["policy"]["maintenance"]["per_job"]]
        self.assertEqual(ids, ["D-JOB-2", "D-JOB-1"])
        self.assertNotEqual(ids, sorted(set(ids)))
        self._raises_naming(rec, "maintenance")


    def test_duplicate_per_job_record_id_is_refused(self):
        per_job = [self._row("D-JOB-1", "4"), self._row("D-JOB-1", "5")]
        rec = self._with_policy(self._binding(maintenance=self._maintenance(
            per_job=per_job,
        )))
        ids = [row["record"] for row in rec["brief"]["policy"]["maintenance"]["per_job"]]
        self.assertEqual(ids, ["D-JOB-1", "D-JOB-1"])
        self.assertNotEqual(len(ids), len(set(ids)))
        self._raises_naming(rec, "maintenance")


    def test_a_legacy_record_with_policy_absent_validates(self):
        rec = self.built()
        self.assertNotIn("policy", rec["brief"])
        out = self.recmod().validate(rec)
        self.assertNotIn("policy", out["brief"])
        self.assertEqual(out["id"], rec["id"])


    def test_build_then_validate_accepts_a_complete_valid_binding(self):
        payload = self._minimum()
        brief = dict(payload["brief"])
        brief["policy"] = self._binding()
        rec = self.built(brief=brief)
        out = self.recmod().validate(rec)
        self.assertEqual(out["brief"]["policy"]["schema"], self._SCHEMA)
        self.assertEqual(out["brief"]["policy"]["profile"], "fixture-only")
        self.assertEqual(
            out["brief"]["policy"]["template_sha256"],
            out["brief"]["template"]["sha256"],
        )


    def test_empty_maintenance_lists_validate(self):
        rec = self._with_policy(self._binding(maintenance=self._maintenance()))
        self.assertEqual(rec["brief"]["policy"]["maintenance"]["standing"], [])
        self.assertEqual(rec["brief"]["policy"]["maintenance"]["per_job"], [])
        out = self.recmod().validate(rec)
        self.assertEqual(out["brief"]["policy"]["maintenance"]["standing"], [])
        self.assertEqual(out["brief"]["policy"]["maintenance"]["per_job"], [])


    def test_sorted_unique_maintenance_rows_validate(self):
        maint = self._maintenance(
            standing=[self._row("D-LANES-1", "2"), self._row("D-LANES-2", "3")],
            per_job=[self._row("D-JOB-1", "4")],
        )
        rec = self._with_policy(self._binding(maintenance=maint))
        standing_ids = [
            row["record"] for row in rec["brief"]["policy"]["maintenance"]["standing"]
        ]
        self.assertEqual(standing_ids, ["D-LANES-1", "D-LANES-2"])
        self.assertEqual(standing_ids, sorted(set(standing_ids)))
        out = self.recmod().validate(rec)
        self.assertEqual(
            [row["record"] for row in out["brief"]["policy"]["maintenance"]["standing"]],
            ["D-LANES-1", "D-LANES-2"],
        )


    def test_a_digit_bearing_syntactically_valid_synthetic_profile_validates(self):
        rec = self._with_policy(self._binding(profile="fixture-only2"))
        self.assertEqual(rec["brief"]["policy"]["profile"], "fixture-only2")
        out = self.recmod().validate(rec)
        self.assertEqual(out["brief"]["policy"]["profile"], "fixture-only2")


    def test_template_digest_equality_holds_when_both_digests_change_together(self):
        rec = self.built()
        rec["brief"]["template"]["sha256"] = "a" * 64
        rec["brief"]["policy"] = self._binding(template_sha256="a" * 64)
        self.assertEqual(
            rec["brief"]["policy"]["template_sha256"],
            rec["brief"]["template"]["sha256"],
        )
        out = self.recmod().validate(rec)
        self.assertEqual(out["brief"]["policy"]["template_sha256"], "a" * 64)
        self.assertEqual(
            out["brief"]["policy"]["template_sha256"],
            out["brief"]["template"]["sha256"],
        )
