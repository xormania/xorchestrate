"""C2 output acceptance: exact decoded handles, history, inline exception.

Written from ops/devlane/task/CONTRACT.md §Output acceptance and the
C2 clarity ruling: newly declared file handles equal the rendered
absolute path by exact decoded-string equality. Historical records
are not retroactively rejected. The named inline planning
reassignment stays a narrow exception, not a new outputs kind.

Implementation was not read. Filesystem custody is dispatch-owned and
is not asserted here.

Each test names the contract rule it pins:

  A1  artifacts[artifact] equals the rendered absolute path exactly
  A2  a different spelling that would resolve to the same file refuses
  A3  scratch is not a report and does not require an artifact
  A4  validate() without outputs= still accepts historical handles
  A5  the inline planning exception accepts one plan artifact string
      and does not generalize to skeptic or ruling jobs
"""

from __future__ import annotations

import inspect
import unittest

import outputs_support as osu

from ops.testkit import World

envelope = osu.envelope

PLAN_INLINE = (
    "# Plan\n"
    "\n"
    "Slice the outputs declaration tests from the accepted contract.\n"
)
SKEPTIC_ABS = osu.OUT_ROOT + "/SKEPTIC.md"
PLAN_ABS = osu.OUT_ROOT + "/PLAN.md"
RULING_ABS = osu.OUT_ROOT + "/RULING.md"


class _AcceptCase(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="outputs-accept-")
        self.addCleanup(self.world.close)
        self.world.enter()

    def declared_validate_params(self):
        return inspect.signature(envelope.validate).parameters

    def require_outputs_param(self):
        params = self.declared_validate_params()
        sig = inspect.signature(envelope.validate)
        self.assertIn(
            "outputs", params,
            "missing public seam envelope.validate(..., outputs=, out_root=); "
            f"found signature {sig}",
        )
        self.assertIn(
            "out_root", params,
            "missing public seam envelope.validate(..., outputs=, out_root=); "
            f"found signature {sig}",
        )

    def require_inline_param(self):
        params = self.declared_validate_params()
        self.assertIn(
            "inline_planning_exception", params,
            "missing public seam envelope.validate("
            "..., inline_planning_exception=); "
            f"found signature {inspect.signature(envelope.validate)}",
        )

    def ok_env(self, job, artifacts):
        return envelope.build(
            job, status="ok", verdict="approve",
            artifacts=artifacts, stamp=osu.STAMP,
        )


class ExactDecodedHandle(_AcceptCase):
    """A1 / A2 / A3 — newly declared files use exact rendered absolute handles."""

    def test_the_exact_rendered_absolute_handle_is_accepted(self):
        self.require_outputs_param()
        env = self.ok_env("check-tests", {"skeptic": SKEPTIC_ABS})
        got = osu.validate_envelope(
            env,
            outputs=osu.copy_native("check-tests"),
            out_root=osu.OUT_ROOT,
        )
        self.assertEqual(got["artifacts"]["skeptic"], SKEPTIC_ABS)

    def test_a_relative_handle_is_refused_when_outputs_are_declared(self):
        self.require_outputs_param()
        env = self.ok_env("check-tests", {"skeptic": "SKEPTIC.md"})
        self.assertEqual(env["artifacts"]["skeptic"], "SKEPTIC.md")
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
            )

    def test_a_dot_segment_spelling_is_refused(self):
        self.require_outputs_param()
        spelled = osu.OUT_ROOT + "/./SKEPTIC.md"
        env = self.ok_env("check-tests", {"skeptic": spelled})
        self.assertEqual(env["artifacts"]["skeptic"], spelled)
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
            )

    def test_a_trailing_slash_spelling_is_refused(self):
        self.require_outputs_param()
        spelled = SKEPTIC_ABS + "/"
        env = self.ok_env("check-tests", {"skeptic": spelled})
        self.assertEqual(env["artifacts"]["skeptic"], spelled)
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
            )

    def test_a_json_encoded_handle_is_not_the_decoded_path(self):
        self.require_outputs_param()
        encoded = '"' + SKEPTIC_ABS + '"'
        env = self.ok_env("check-tests", {"skeptic": encoded})
        self.assertEqual(env["artifacts"]["skeptic"], encoded)
        self.assertNotEqual(encoded, SKEPTIC_ABS)
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
            )

    def test_a_missing_declared_key_is_refused(self):
        self.require_outputs_param()
        env = self.ok_env("check-tests", {"raw": "/tmp/t/raw.txt"})
        self.assertNotIn("skeptic", env["artifacts"])
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
            )

    def test_a_wrong_artifact_key_is_refused(self):
        self.require_outputs_param()
        env = self.ok_env("check-tests", {"plan": SKEPTIC_ABS})
        self.assertIn("plan", env["artifacts"])
        self.assertNotIn("skeptic", env["artifacts"])
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
            )

    def test_pointing_the_file_handle_at_scratch_is_refused(self):
        self.require_outputs_param()
        scratch = osu.OUT_ROOT + "/tmp"
        env = self.ok_env("check-tests", {"skeptic": scratch})
        self.assertEqual(env["artifacts"]["skeptic"], scratch)
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
            )

    def test_scratch_does_not_require_an_artifact(self):
        self.require_outputs_param()
        env = self.ok_env("check-tests", {"skeptic": SKEPTIC_ABS})
        self.assertNotIn("tmp", env["artifacts"])
        got = osu.validate_envelope(
            env,
            outputs=osu.copy_native("check-tests"),
            out_root=osu.OUT_ROOT,
        )
        self.assertEqual(got["artifacts"]["skeptic"], SKEPTIC_ABS)
        self.assertNotIn("tmp", got["artifacts"])

    def test_other_evidence_handles_may_sit_beside_the_declared_file(self):
        self.require_outputs_param()
        env = self.ok_env(
            "check-tests",
            {"skeptic": SKEPTIC_ABS, "raw": "/tmp/t/raw.txt"},
        )
        got = osu.validate_envelope(
            env,
            outputs=osu.copy_native("check-tests"),
            out_root=osu.OUT_ROOT,
        )
        self.assertEqual(got["artifacts"]["skeptic"], SKEPTIC_ABS)
        self.assertEqual(got["artifacts"]["raw"], "/tmp/t/raw.txt")

    def test_plan_and_adjudicate_use_their_own_exact_handles(self):
        self.require_outputs_param()
        plan = self.ok_env("plan", {"plan": PLAN_ABS})
        ruling = self.ok_env("adjudicate", {"ruling": RULING_ABS})
        got_plan = osu.validate_envelope(
            plan, outputs=osu.copy_native("plan"), out_root=osu.OUT_ROOT,
        )
        got_ruling = osu.validate_envelope(
            ruling,
            outputs=osu.copy_native("adjudicate"),
            out_root=osu.OUT_ROOT,
        )
        self.assertEqual(got_plan["artifacts"]["plan"], PLAN_ABS)
        self.assertEqual(got_ruling["artifacts"]["ruling"], RULING_ABS)


class HistoricalRecordsRemainValid(_AcceptCase):
    """A4 — C2 does not retroactively reject envelopes without declarations."""

    def test_a_relative_handle_still_validates_without_outputs(self):
        env = self.ok_env("plan", {"plan": "PLAN.md"})
        got = envelope.validate(env)
        self.assertEqual(got["artifacts"]["plan"], "PLAN.md")

    def test_an_absolute_non_canonical_handle_still_validates_without_outputs(self):
        env = self.ok_env("plan", {"plan": "/tmp/t/raw.txt"})
        got = envelope.validate(env)
        self.assertEqual(got["artifacts"]["plan"], "/tmp/t/raw.txt")

    def test_a_dot_segment_handle_still_validates_without_outputs(self):
        spelled = osu.OUT_ROOT + "/./PLAN.md"
        env = self.ok_env("plan", {"plan": spelled})
        got = envelope.validate(env)
        self.assertEqual(got["artifacts"]["plan"], spelled)

    def test_omission_does_not_require_declared_file_keys(self):
        self.require_outputs_param()
        env = self.ok_env("adversarial-review", {"raw": "/tmp/t/raw.txt"})
        got = osu.validate_envelope(env, outputs=[], out_root=osu.OUT_ROOT)
        self.assertEqual(got["artifacts"]["raw"], "/tmp/t/raw.txt")
        self.assertNotIn("plan", got["artifacts"])


class NarrowInlinePlanningException(_AcceptCase):
    """A5 — one named exception; not a standing inlined-report mode."""

    def _inlined_env(self, job, key, body):
        env = dict(self.ok_env(job, {key: "/tmp/t/placeholder"}))
        env["artifacts"] = {key: body}
        self.assertIn("\n", env["artifacts"][key], "plant: inlined document")
        self.assertEqual(env["artifacts"][key], body)
        return env

    def test_without_the_flag_an_inlined_plan_is_still_refused(self):
        env = self._inlined_env("plan", "plan", PLAN_INLINE)
        with self.assertRaises(envelope.EnvelopeError):
            envelope.validate(env)

    def test_the_flag_accepts_the_plan_as_an_artifact_string(self):
        self.require_outputs_param()
        self.require_inline_param()
        env = self._inlined_env("plan", "plan", PLAN_INLINE)
        got = osu.validate_envelope(
            env,
            outputs=osu.copy_native("plan"),
            out_root=osu.OUT_ROOT,
            inline_planning_exception=True,
        )
        self.assertEqual(got["artifacts"]["plan"], PLAN_INLINE)
        self.assertIn("\n", got["artifacts"]["plan"])

    def test_the_exception_does_not_allow_an_inlined_skeptic_report(self):
        self.require_outputs_param()
        self.require_inline_param()
        env = self._inlined_env("check-tests", "skeptic", PLAN_INLINE)
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("check-tests"),
                out_root=osu.OUT_ROOT,
                inline_planning_exception=True,
            )

    def test_the_exception_does_not_allow_an_inlined_ruling(self):
        self.require_outputs_param()
        self.require_inline_param()
        env = self._inlined_env("adjudicate", "ruling", PLAN_INLINE)
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("adjudicate"),
                out_root=osu.OUT_ROOT,
                inline_planning_exception=True,
            )

    def test_the_exception_is_not_an_outputs_kind(self):
        row = {"kind": "inline", "path": "PLAN.md", "artifact": "plan"}
        self.assertEqual(row["kind"], "inline", "plant: non-file kind")
        with self.assertRaises(osu.outputs_error()):
            osu.validate_outputs([row])

    def test_permission_failure_does_not_fall_back_to_inline_without_the_flag(self):
        self.require_outputs_param()
        env = self._inlined_env("plan", "plan", PLAN_INLINE)
        with self.assertRaises(envelope.EnvelopeError):
            osu.validate_envelope(
                env,
                outputs=osu.copy_native("plan"),
                out_root=osu.OUT_ROOT,
            )


if __name__ == "__main__":
    unittest.main()
