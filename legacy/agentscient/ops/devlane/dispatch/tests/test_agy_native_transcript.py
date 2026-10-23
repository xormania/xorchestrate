"""Native agy transcript-seam regressions from the dispatch contract.

Authored from ops/devlane/dispatch/CONTRACT.md (raw.out owns
init.model/cwd; brain rows need source/type and a supported payload;
MODEL/PLANNER_RESPONSE thinking+tool_calls without content is accepted)
plus the producer explanation of that planner payload and the cold F1
ruling: an unfinished final JSON row without a newline, and trailing
blank lines, must not void a binding raw.out already supplied.

launch.py was not read. Synthetic payloads only. The public 4-tuple
(path, ran, stream_id, mismatch) and raw.out ownership stay as they are.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import agy_dispatch_support as agy
import launch_support as ls

from ops.testkit import World  # noqa: F401 - _TempLaunch owns the world

SYNTHETIC_THINKING = "synthetic-planner-thinking"
SYNTHETIC_CONTENT = "synthetic-user-content"
MALFORMED_PHRASE = "malformed native transcript"


def _user_row():
    return {
        "step_index": 1,
        "source": "USER",
        "type": "USER_INPUT",
        "status": "DONE",
        "created_at": "2026-09-08T00:00:00Z",
        "content": SYNTHETIC_CONTENT,
    }


def _planner_row():
    return {
        "step_index": 2,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": "2026-09-08T00:00:00Z",
        "thinking": SYNTHETIC_THINKING,
        "tool_calls": [],
    }


def _malformed_completed_row():
    return {
        "step_index": 1,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "status": "DONE",
        "created_at": "2026-09-08T00:00:00Z",
    }


def _ndjson(rows):
    return "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")


class AgyNativeTranscriptSeam(ls._TempLaunch):
    """Pins for launch._agy_stream; _stream must keep the same tuple."""

    def setUp(self):
        super().setUp()
        self.launch = self.load_launch()
        self.job_dir = self.home / "job"
        self.agy_home = self.job_dir / "home" / "agy"
        self.job_dir.mkdir()
        self.agy_home.mkdir(parents=True)
        self.snap = self.repo
        self.raw_out = self.job_dir / "raw.out"
        self.transcript = agy.transcript_path(self.agy_home, agy.CONVERSATION_ID)

    def plant_auth(self):
        path = agy.auth_path(self.agy_home)
        marker = "agy-synthetic-auth\n"
        self.world.plant_text(path, marker)
        self.assertEqual(path.read_text(encoding="utf-8"), marker, "plant: auth")
        return path

    def plant_raw_out(self):
        cwd = str(self.snap.resolve())
        raw = agy.framed_stdout(cwd=cwd, permission_mode="always-proceed")
        lines = raw.decode("utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 1, "plant: framed stdout")
        init = json.loads(lines[0])
        body = init.setdefault("init", {})
        body["model"] = agy.MODEL
        body["cwd"] = cwd
        lines[0] = json.dumps(init)
        data = ("".join(line + "\n" for line in lines)).encode("utf-8")
        self.world.plant_bytes(self.raw_out, data)
        landed = self.raw_out.read_bytes()
        self.assertEqual(landed, data, "plant: raw.out bytes")
        self.assertIn(agy.MODEL.encode("utf-8"), landed, "plant: init.model")
        self.assertIn(cwd.encode("utf-8"), landed, "plant: init.cwd")
        self.assertIn(b'"event": "result"', landed, "plant: result frame")
        return self.raw_out

    def plant_transcript(self, rows):
        data = _ndjson(rows)
        self.world.plant_bytes(self.transcript, data)
        landed = self.transcript.read_bytes()
        self.assertEqual(landed, data, "plant: transcript bytes")
        parsed = [json.loads(line) for line in landed.decode("utf-8").splitlines()
                  if line.strip()]
        self.assertEqual(len(parsed), len(rows), "plant: transcript row count")
        return landed

    def read_agy(self):
        fn = getattr(self.launch, "_agy_stream", None)
        self.assertTrue(
            callable(fn),
            "launch._agy_stream is the native transcript seam",
        )
        try:
            got = fn(self.job_dir, agy.CONVERSATION_ID, self.snap)
        except Exception as exc:
            self.fail(
                f"_agy_stream raised {type(exc).__name__}: {exc}; "
                "the seam must return the existing 4-tuple"
            )
        self.assertIsInstance(got, (tuple, list), got)
        self.assertEqual(len(got), 4, got)
        via_stream = agy.call_stream(
            self.launch, self.job_dir, agy.HARNESS, agy.CONVERSATION_ID,
            self.snap, self.ref, "read",
        )
        self.assertEqual(
            tuple(via_stream), tuple(got),
            "_stream must preserve the _agy_stream 4-tuple",
        )
        return got

    def assert_raw_binding(self, got, *, ran=None):
        path, got_ran, stream_id, _mismatch = got
        self.assertTrue(path, f"raw.out binding must remain: {got!r}")
        self.assertEqual(
            Path(path).resolve(), self.raw_out.resolve(),
            f"path must be raw.out, not the brain transcript: {got!r}",
        )
        self.assertNotEqual(
            Path(path).resolve(), self.transcript.resolve(),
            f"brain rows do not own the stream path: {got!r}",
        )
        self.assertEqual(stream_id, agy.CONVERSATION_ID, got)
        if ran is not None:
            self.assertEqual(got_ran, ran, got)
        return got

    def test_planner_thinking_and_tool_calls_without_content_is_accepted(self):
        self.plant_auth()
        self.plant_raw_out()
        planner = _planner_row()
        self.assertNotIn("content", planner)
        self.assertIsInstance(planner["thinking"], str)
        self.assertIsInstance(planner["tool_calls"], list)
        self.plant_transcript([_user_row(), planner])
        landed = json.loads(self.transcript.read_text(encoding="utf-8").splitlines()[-1])
        self.assertNotIn("content", landed, "plant: planner has no content")
        self.assertEqual(landed.get("thinking"), SYNTHETIC_THINKING)
        self.assertEqual(landed.get("tool_calls"), [])
        self.assertNotIn(SYNTHETIC_THINKING, self.raw_out.read_text(encoding="utf-8"))

        got = self.read_agy()
        self.assert_raw_binding(got)
        _path, _ran, _sid, mismatch = got
        self.assertTrue(
            mismatch is None or mismatch == "",
            f"measured planner thinking/tool_calls without content "
            f"must not be treated as malformed: {got!r}",
        )
        if isinstance(mismatch, str):
            self.assertNotIn(MALFORMED_PHRASE, mismatch, got)

    def test_unfinished_final_transcript_line_retains_raw_out_binding(self):
        self.plant_auth()
        self.plant_raw_out()
        before = self.plant_transcript([_user_row(), _user_row()])
        control = self.read_agy()
        self.assert_raw_binding(control)
        _cpath, control_ran, _csid, control_mismatch = control
        self.assertTrue(
            control_mismatch is None or control_mismatch == "",
            f"complete transcript is the control: {control!r}",
        )

        def cut_final(data):
            lines = data.decode("utf-8").splitlines(keepends=True)
            last = lines[-1].rstrip("\n")
            keep = last[: max(18, len(last) // 2)]
            return ("".join(lines[:-1]) + keep).encode("utf-8")

        ls.plant_bytes(
            self.transcript, cut_final, expect="shrink",
            recognisable=lambda after: (
                SYNTHETIC_CONTENT.encode("utf-8") in after
                and after.startswith(before.splitlines(True)[0])
                and not after.endswith(b"\n")
            ),
        )
        planted = self.transcript.read_bytes()
        self.assertFalse(
            planted.endswith(b"\n"),
            "plant: unfinished final row has no newline",
        )
        torn = planted.decode("utf-8").splitlines()[-1]
        with self.assertRaises(json.JSONDecodeError):
            json.loads(torn)
        self.assertIn("source", torn, "plant: torn line still the fixture")

        got = self.read_agy()
        self.assert_raw_binding(got, ran=control_ran)
        _path, _ran, _sid, mismatch = got
        self.assertTrue(
            mismatch is None or mismatch == "",
            f"unfinished final row without a newline is not a mismatch: "
            f"{got!r}",
        )

    def test_blank_transcript_tail_retains_raw_out_binding(self):
        self.plant_auth()
        self.plant_raw_out()
        before = self.plant_transcript([_user_row()])
        control = self.read_agy()
        self.assert_raw_binding(control)
        _cpath, control_ran, _csid, control_mismatch = control
        self.assertTrue(
            control_mismatch is None or control_mismatch == "",
            f"complete transcript is the control: {control!r}",
        )

        ls.plant_bytes(
            self.transcript, lambda data: data + b"\n", expect="grow",
            recognisable=lambda after: after.startswith(before),
        )
        text = self.transcript.read_text(encoding="utf-8")
        self.assertTrue(text.endswith("\n\n"), "plant: blank tail")
        self.assertEqual(json.loads(text.splitlines()[0])["content"], SYNTHETIC_CONTENT)

        got = self.read_agy()
        self.assert_raw_binding(got, ran=control_ran)
        _path, _ran, _sid, mismatch = got
        self.assertTrue(
            mismatch is None or mismatch == "",
            f"trailing blank lines are ignored: {got!r}",
        )

    def test_malformed_completed_row_is_refused(self):
        self.plant_auth()
        self.plant_raw_out()
        row = _malformed_completed_row()
        self.assertNotIn("content", row)
        self.assertNotIn("thinking", row)
        self.assertNotIn("tool_calls", row)
        self.plant_transcript([row])
        landed = json.loads(self.transcript.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(landed.get("source"), "MODEL", "plant: completed row")
        self.assertEqual(landed.get("type"), "PLANNER_RESPONSE", "plant: completed row")
        self.assertEqual(landed.get("status"), "DONE", "plant: completed row")
        self.assertNotIn("content", landed)
        self.assertNotIn("thinking", landed)
        self.assertNotIn("tool_calls", landed)

        got = self.read_agy()
        self.assert_raw_binding(got)
        _path, _ran, _sid, mismatch = got
        self.assertIsInstance(mismatch, str, got)
        self.assertIn(
            MALFORMED_PHRASE, mismatch,
            f"confirmed malformed completed row must refuse: {got!r}",
        )


if __name__ == "__main__":
    unittest.main()
