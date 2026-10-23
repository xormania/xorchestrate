"""Live agy step_update output-cap from measured native stdout and the cold ruling.

Two bounded cases only. Synthetic values; no native prose. Uses existing
launch fixtures, wires.budget, the baked start-witness, and the measured
init/session/cwd plus DONE agent_response usage carrier. Does not invent
a CLI, aggregate counters, or read launch.py.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from pathlib import Path

import agy_dispatch_support as agy
import launch_support as ls

from ops.testkit import World

GRACE_S = 1.0
OVER_SLEEP_S = 8.0
ABSENT_SLEEP_S = 6.0
TRIP_DEADLINE_S = 4.0

STEP_CHILD = (
    Path(__file__).resolve().parent / "fixtures" / "agy_step_child.py"
)


class _AgyStepCapLaunch(ls._TempLaunch):
    """Existing launcher fixture plus a synthetic agy executable."""

    def setUp(self):
        super().setUp()
        self.assertIsInstance(self.world, World)
        self._install_cli("agy")
        os.environ["WORKER_HARNESSES"] = "claude codex grok agy"
        os.environ["EXECUTABLE_AGY"] = str(self.bin / "agy")
        self.launch = self.load_launch()

    def tearDown(self):
        os.environ.pop("WORKER_HARNESSES", None)
        os.environ.pop("EXECUTABLE_AGY", None)
        os.environ.pop("AGY_FAKE_START", None)
        os.environ.pop("AGY_FAKE_DONE", None)
        os.environ.pop("AGY_FAKE_SLEEP", None)
        os.environ.pop("AGY_FAKE_STATUS", None)
        os.environ.pop("AGY_FAKE_OMIT_USAGE", None)
        os.environ.pop("AGY_FAKE_USAGE", None)
        os.environ.pop("AGY_FAKE_OUTPUT", None)
        os.environ.pop("AGY_FAKE_CONVERSATION", None)
        os.environ.pop("AGY_FAKE_RESPONSE", None)
        super().tearDown()

    def plant_auth(self, home=None):
        home = Path(home or self.home)
        path = agy.auth_path(home)
        self.world.plant_text(path, "agy-synthetic-auth\n")
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        landed = path.read_text(encoding="utf-8")
        self.assertEqual(landed, "agy-synthetic-auth\n", "plant: auth leaf")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        return path

    def install_step_child(self):
        self.assertTrue(STEP_CHILD.is_file(), f"missing {STEP_CHILD}")
        body = STEP_CHILD.read_text(encoding="utf-8")
        self.assertIn("step_update", body)
        self.assertIn("agent_response", body)
        self.assertIn("AGY_FAKE_SLEEP", body)
        self.assertIn("@@START_WITNESS@@", body)
        script = (
            f"#!{sys.executable}\n"
            + body.replace(
                '"@@START_WITNESS@@"', json.dumps(str(self.start_witness)),
            )
        )
        dest = self.world.tools.plant("agy", script=script)
        landed = dest.read_text(encoding="utf-8")
        self.assertIn("step_update", landed)
        self.assertIn("agent_response", landed)
        self.assertIn("AGY_FAKE_SLEEP", landed)
        self.assertIn(str(self.start_witness), landed)
        self.assertNotIn("@@START_WITNESS@@", landed)
        return dest

    def dispatch_agy(self, argv=None, **kwargs):
        kwargs.setdefault("harness", agy.HARNESS)
        kwargs.setdefault("model", agy.MODEL)
        if argv is None:
            argv = self.argv_for(**kwargs)
        try:
            return self.dispatch(argv)
        except Exception as exc:
            self.fail(
                f"agy dispatch raised {type(exc).__name__}: {exc}"
            )

    def budget_out(self):
        wires = ls.load_path(self, ls.WIRES_PATH, "agy_step_cap_wires")
        budget = wires.budget("read")
        self.assertIsInstance(budget, int)
        self.assertGreater(budget, 0)
        return budget

    def trip_text(self, rec, out, err):
        parts = [self.combined(out, err), json.dumps(rec)]
        dirs = self.job_dirs()
        if len(dirs) == 1:
            tripped = dirs[0] / "TRIPPED.md"
            if tripped.is_file():
                parts.append(tripped.read_text(encoding="utf-8"))
            raw = dirs[0] / "raw.out"
            if raw.is_file():
                parts.append(raw.read_text(encoding="utf-8"))
        return "\n".join(parts)

    def raw_out(self):
        raw = self.the_job_dir() / "raw.out"
        self.assertTrue(raw.is_file(), "child stdout must land on raw.out")
        body = raw.read_text(encoding="utf-8")
        self.assertTrue(body.strip(), "raw.out is empty")
        return body


class AgyLiveStepUpdateOutputCap(_AgyStepCapLaunch):
    """Live cap-out reads the DONE agent_response carrier, then fail-closes."""

    def test_over_budget_live_step_update_trips_before_any_terminal_result(self):
        self.plant_auth()
        self.install_step_child()
        budget = self.budget_out()
        over = budget + 1
        self.assertGreater(over, budget)
        done = self.home / "agy-step-over-done.marker"
        start = self.home / "agy-step-over-start.marker"
        os.environ["AGY_FAKE_DONE"] = str(done)
        os.environ["AGY_FAKE_START"] = str(start)
        os.environ["AGY_FAKE_SLEEP"] = str(OVER_SLEEP_S)
        os.environ["AGY_FAKE_OUTPUT"] = str(over)
        os.environ["AGY_FAKE_STATUS"] = "SUCCESS"
        os.environ["AGY_FAKE_CONVERSATION"] = agy.CONVERSATION_ID
        os.environ["AGY_FAKE_RESPONSE"] = json.dumps({
            "job": "plan",
            "status": "ok",
            "verdict": "approve",
            "counts": {"p1": 0, "p2": 0, "p3": 0, "opinions": 0},
            "findings": [],
            "artifacts": {},
            "spend": {"harness": "agy", "total": 0, "out": 0, "runs": 0},
            "stamp": {"ref": "synthetic-step-cap-ref"},
            "note": "synthetic",
        })
        os.environ["DISPATCH_TIMEOUT"] = "30"
        started = time.monotonic()
        code, out, err = self.dispatch_agy(self.argv_for(
            job="plan", harness=agy.HARNESS, stage="plan", model=agy.MODEL,
        ))
        elapsed = time.monotonic() - started
        self.assertTrue(
            start.is_file() or self.start_witness.is_file(),
            "plant: child started so live step_update can be observed: "
            f"exit={code} {self.combined(out, err)!r}",
        )
        witness = self.read_start_witness()
        child_cwd = Path(witness["cwd"]).resolve()
        self.assertTrue(child_cwd.is_dir(), witness)
        raw = self.raw_out()
        self.assertIn(agy.CONVERSATION_ID, raw)
        self.assertIn(str(child_cwd), raw)
        self.assertIn('"event": "init"', raw)
        self.assertIn('"event": "step_update"', raw)
        self.assertIn('"state": "DONE"', raw)
        self.assertIn('"step_type": "agent_response"', raw)
        self.assertIn(f'"output_tokens": {over}', raw)
        self.assertNotIn('"event": "result"', raw)
        self.assertFalse(
            done.is_file(),
            "over-budget live step_update must trip before COMPLETED",
        )
        self.assertLess(
            elapsed, TRIP_DEADLINE_S,
            f"live cap-out must not wait out the {OVER_SLEEP_S}s result: "
            f"elapsed={elapsed}",
        )
        self.assertFalse(ls.pid_is_alive(witness["pid"]))
        rec = self.read_record()
        self.assertEqual(rec["caps"].get("cap-out"), budget, rec.get("caps"))
        self.assertIn("wires", str(rec["caps"].get("source", "")))
        envelope = (rec.get("result") or {}).get("envelope") or {}
        self.assertIn(
            envelope.get("status"), ("tripped", "invalid"),
            f"over-budget live output must not certify: {rec!r}",
        )
        tripped = self.the_job_dir() / "TRIPPED.md"
        self.assertTrue(
            tripped.is_file(),
            f"inline supervisor stop must write TRIPPED.md: {rec!r}",
        )
        reason = tripped.read_text(encoding="utf-8").lower()
        self.assertTrue(
            "output cap" in reason or "cap-out" in reason,
            f"TRIPPED.md reason must identify the output cap: {reason!r}",
        )

    def test_absent_usage_still_fail_closes_after_stream_grace(self):
        self.plant_auth()
        self.install_step_child()
        budget = self.budget_out()
        done = self.home / "agy-step-absent-done.marker"
        start = self.home / "agy-step-absent-start.marker"
        os.environ["AGY_FAKE_DONE"] = str(done)
        os.environ["AGY_FAKE_START"] = str(start)
        os.environ["AGY_FAKE_SLEEP"] = str(ABSENT_SLEEP_S)
        os.environ["AGY_FAKE_OMIT_USAGE"] = "1"
        os.environ["AGY_FAKE_CONVERSATION"] = agy.CONVERSATION_ID
        os.environ["DISPATCH_TIMEOUT"] = "30"
        self.set_grace(GRACE_S)
        started = time.monotonic()
        code, out, err = self.dispatch_agy(self.argv_for(
            job="plan", harness=agy.HARNESS, stage="plan", model=agy.MODEL,
        ))
        elapsed = time.monotonic() - started
        self.assertTrue(
            start.is_file() or self.start_witness.is_file(),
            "plant: child started with no usage carrier: "
            f"exit={code} {self.combined(out, err)!r}",
        )
        witness = self.read_start_witness()
        raw = self.raw_out()
        self.assertIn(agy.CONVERSATION_ID, raw)
        self.assertIn(str(Path(witness["cwd"]).resolve()), raw)
        self.assertIn('"event": "init"', raw)
        self.assertIn('"event": "step_update"', raw)
        self.assertIn('"state": "DONE"', raw)
        self.assertIn('"step_type": "agent_response"', raw)
        self.assertNotIn("output_tokens", raw)
        self.assertNotIn('"usage"', raw)
        self.assertNotIn('"event": "result"', raw)
        self.assertFalse(
            done.is_file(),
            "truly absent usage must fail-close; COMPLETED must not land",
        )
        self.assertGreaterEqual(
            elapsed, GRACE_S * 0.5,
            f"missing usage is allowed for stream grace={GRACE_S}s; "
            f"elapsed={elapsed}",
        )
        self.assertLess(
            elapsed, TRIP_DEADLINE_S,
            f"fail-closed after grace must not wait out the "
            f"{ABSENT_SLEEP_S}s child: elapsed={elapsed}",
        )
        self.assertFalse(ls.pid_is_alive(witness["pid"]))
        rec = self.read_record()
        self.assertEqual(rec["caps"].get("cap-out"), budget, rec.get("caps"))
        envelope = (rec.get("result") or {}).get("envelope") or {}
        self.assertIn(
            envelope.get("status"), ("tripped", "invalid"),
            f"absent live output must fail closed: {rec!r}",
        )
        self.assertNotEqual(envelope.get("verdict"), "approve", envelope)
        self.assertNotEqual(envelope.get("status"), "ok", envelope)


if __name__ == "__main__":
    import unittest
    unittest.main()
