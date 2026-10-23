"""Live vs terminal usage separation from the 2026-09-08 final ruling.

Two guarantees only. Existing contradictory-repeat refuse is already
measured green on this tree, so it is not restated as a never-red test.
Synthetic native frames through usage.agy_read and launch._envelope.
No shape matrix, no new CLI, no implementation-body reads.
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

import agy_envelope_support as frames
import launch_support as ls

from ops.testkit import World

HERE = Path(__file__).resolve().parent

USAGE_PATH = HERE.parents[1] / "telemetry" / "usage.py"
SID = frames.CONVERSATION_ID
CWD = "/synthetic"

# Native extra keys are valid; incomplete means no usable output_tokens.
PREFIX_USAGE = {
    "input_tokens": 1,
    "output_tokens": 10,
    "thinking_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 11,
}
INCOMPLETE_USAGE = {"input_tokens": 1}
TERMINAL_USAGE = {
    "input_tokens": 12571,
    "output_tokens": 6450,
    "thinking_tokens": 6121,
    "cache_read_tokens": 24431,
    "total_tokens": 19021,
}
MISMATCH_LIVE = (
    "malformed step output usage",
    "invalid native step_index",
)
def load_usage(testcase):
    testcase.assertTrue(USAGE_PATH.is_file(), f"missing {USAGE_PATH}")
    spec = importlib.util.spec_from_file_location(
        "agy_live_terminal_usage", USAGE_PATH,
    )
    testcase.assertIsNotNone(spec)
    testcase.assertIsNotNone(spec.loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    testcase.assertTrue(callable(getattr(module, "agy_read", None)))
    return module


def init_frame():
    body = frames.init_frame()
    body["conversation_id"] = SID
    body["init"] = dict(body["init"])
    body["init"]["cwd"] = CWD
    body["init"]["model"] = "gemini-3.8-flash-high"
    return body


def done_response(index, usage):
    return frames.step_update_frame(
        state="DONE",
        step_type="agent_response",
        extra={
            "conversation_id": SID,
            "step_index": index,
            "usage": usage,
        },
    )


def success_result():
    nested = frames.canonical_envelope(
        note="agy-live-terminal-ok",
        stamp={"ref": frames.LAUNCHER_REF, "started": None, "ended": None},
    )
    return frames.result_frame(
        frames.published_result(
            nested,
            status="SUCCESS",
            extra={"conversation_id": SID, "usage": TERMINAL_USAGE},
        )
    )


class AgyLiveTerminalSeparation(unittest.TestCase):
    def setUp(self):
        self.usage = load_usage(self)
        self.world = World(prefix="agy-live-term-")
        self.addCleanup(self.world.close)
        self.raw = self.world.root / "raw.out"
        self.launch = ls.require_module(self, "launch")

    def read_raw(self, rows):
        frames.plant_ndjson(self.world, self.raw, *rows)
        planted = self.raw.read_text()
        for row in rows:
            self.assertIn(json.dumps(row["event"])[1:-1], planted)
        try:
            return self.usage.agy_read(self.raw)
        except ValueError as exc:
            return {"_raised": str(exc)}

    def envelope_of(self, rows):
        frames.plant_ndjson(self.world, self.raw, *rows)
        fn = getattr(self.launch, "_envelope", None)
        self.assertTrue(callable(fn), "launch._envelope parses stdout")
        got = fn(self.raw.read_bytes(), "plan", frames.LAUNCHER_REF, harness="agy")
        self.assertIsInstance(got, dict, got)
        return got

    def test_incomplete_done_usage_after_valid_prefix_hides_live_output_without_mismatch(
            self):
        rows = [
            init_frame(),
            done_response(1, PREFIX_USAGE),
            done_response(2, INCOMPLETE_USAGE),
        ]
        reading = self.read_raw(rows)
        self.assertNotIn(
            "_raised", reading,
            f"incomplete live usage must not refuse as a mismatch: {reading}",
        )
        self.assertIsNone(
            reading.get("live_output"),
            f"prefix live_output must not fail-open: {reading}",
        )
        issues = list(reading.get("issues") or [])
        for needle in MISMATCH_LIVE:
            self.assertNotIn(
                needle, issues,
                f"live-incomplete must not be a supervision mismatch: {reading}",
            )
        blob = " ".join(issues).lower()
        self.assertNotIn("malformed", blob, reading)
        self.assertNotIn("invalid native", blob, reading)

    def test_complete_terminal_restores_output_total_and_accepts_envelope(self):
        rows = [
            init_frame(),
            done_response(1, PREFIX_USAGE),
            done_response(2, INCOMPLETE_USAGE),
            success_result(),
        ]
        reading = self.read_raw(rows)
        self.assertNotIn("_raised", reading, reading)
        tokens = reading.get("tokens")
        self.assertIsInstance(tokens, dict, reading)
        self.assertEqual(tokens.get("total"), TERMINAL_USAGE["total_tokens"], reading)
        self.assertEqual(tokens.get("output"), TERMINAL_USAGE["output_tokens"], reading)
        self.assertEqual(
            reading.get("live_output"), TERMINAL_USAGE["output_tokens"], reading,
        )
        env = self.envelope_of(rows)
        self.assertEqual(env.get("status"), "ok", env)
        self.assertEqual(env.get("verdict"), "approve", env)
        note = str(env.get("note") or "")
        self.assertNotIn("malformed", note.lower(), env)
        self.assertNotEqual(env.get("status"), "invalid", env)


if __name__ == "__main__":
    unittest.main()
