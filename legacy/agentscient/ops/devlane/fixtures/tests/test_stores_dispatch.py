"""Shape of build_dispatch_store: a job directory in the launcher's shape.

Authored from the Unit A plan (the meter sees dispatched runs): the
builder writes record.json, state.json, exit, raw.out from a record
dict, deterministic under base_timestamp, with the leak marker in
prompt.txt. The record dict comes from record.build so the fixture
cannot drift from record.py's validation.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import unittest
from pathlib import Path

from ops.testkit import World

HERE = Path(__file__).resolve()
STORES = HERE.parents[1] / "stores.py"
RECORD = HERE.parents[2] / "dispatch" / "record.py"

BASE_EPOCH = 1_787_306_400
SECOND_BASE_EPOCH = BASE_EPOCH + 86_400
MARKER = "FIXTUREPROMPTMARKER"
JOB_ID = "20260821T100000Z-plan-grok-abc123"
LAUNCHER_ARTIFACTS = {
    f"{JOB_ID}/record.json",
    f"{JOB_ID}/state.json",
    f"{JOB_ID}/exit",
    f"{JOB_ID}/raw.out",
    f"{JOB_ID}/prompt.txt",
}

def load_module(testcase, path, name):
    testcase.assertTrue(
        path.is_file(),
        f"{path} is missing; the contract requires this module to exist",
    )
    spec = importlib.util.spec_from_file_location(name, path)
    testcase.assertIsNotNone(
        spec,
        f"{path} could not be given an import specification",
    )
    testcase.assertIsNotNone(
        spec.loader,
        f"{path} has no loader and cannot be exercised",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def minimum_payload(job_id=JOB_ID):
    return {
        "id": job_id,
        "lane": "dev",
        "stage": "plan",
        "unit": "work",
        "lineage": {"branch": "work", "base_sha": "a" * 40},
        "follows": [],
        "job": "plan",
        "role": "read",
        "dispatched_by": "Launch Test <noreply@example.invalid>",
        "at": {"launched": "2026-08-21T10:00:00Z", "closed": None},
        "snapshot": {
            "mode": "whole",
            "ref_name": "HEAD",
            "ref_sha": "b" * 40,
            "behind_tip": 0,
            "root": f"/tmp/jobs/{job_id}/snapshot",
        },
        "harness": {
            "name": "grok",
            "version": "1.0.5",
            "isolation": {
                "mechanism": "home",
                "env": {"GROK_HOME": f"/tmp/jobs/{job_id}/home/grok"},
                "home": f"/tmp/jobs/{job_id}/home/grok",
                "auth_files": ["auth.json"],
                "store": f"/tmp/jobs/{job_id}/home/grok/sessions",
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
            "spend": {
                "input": 70,
                "cached": 15,
                "output": 15,
                "total": 100,
                "source": "stream",
                "cost_usd_ticks": 9,
            },
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


class DispatchStoreBuilderShape(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="stores-dispatch-")
        self.addCleanup(self.world.close)
        self.world.enter()
        self.root = self.world.cwd
        self.assertTrue(STORES.is_file(), f"{STORES} is missing; the contract requires this module to exist")
        self.assertTrue(RECORD.is_file(), f"{RECORD} is missing; the contract requires this module to exist")
        self.stores_path = self.world.stage(STORES, "stores.py")
        self.record_path = self.world.stage(RECORD, "record.py")

    def stores(self):
        return load_module(self, self.stores_path, "agentscient_fixture_stores")

    def recmod(self):
        return load_module(self, self.record_path, "agentscient_dispatch_record")

    def built_record(self, **overrides):
        payload = dict(minimum_payload())
        payload.update(overrides)
        record = self.recmod()
        try:
            built = record.build(payload)
        except TypeError:
            built = record.build(**payload)
        self.assertIsInstance(built, dict)
        self.assertEqual(built["id"], payload["id"])
        return built

    def builder(self):
        stores = self.stores()
        fn = getattr(stores, "build_dispatch_store", None)
        self.assertIsNotNone(
            fn,
            f"{STORES}:build_dispatch_store is missing",
        )
        return fn

    def call_builder(self, fn, root, **kwargs):
        kwargs.setdefault("marker", MARKER)
        signature = inspect.signature(fn)
        accepted = {
            key: value
            for key, value in kwargs.items()
            if key in signature.parameters
        }
        # marker is part of the leak-channel contract even when the
        # builder takes it as a constant rather than a parameter.
        return fn(root, **accepted)

    def test_the_builder_is_keyword_only_and_requires_base_timestamp(self):
        fn = self.builder()
        signature = inspect.signature(fn)
        params = signature.parameters
        self.assertIn("record", params)
        self.assertEqual(
            params["record"].kind,
            inspect.Parameter.KEYWORD_ONLY,
            "record must be keyword-only so a positional mix-up cannot drift",
        )
        self.assertIn("streams", params)
        self.assertEqual(
            params["streams"].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )
        timestamp = params.get("base_timestamp")
        self.assertIsNotNone(
            timestamp,
            f"{STORES}:build_dispatch_store has no explicit "
            "base_timestamp parameter",
        )
        self.assertIs(
            timestamp.default,
            inspect.Parameter.empty,
            (
                f"{STORES}:build_dispatch_store permits an implicit clock; "
                "base_timestamp must be required"
            ),
        )

    def test_the_builder_writes_the_launcher_job_directory_shape(self):
        fn = self.builder()
        record = self.built_record()
        planted = self.call_builder(
            fn,
            self.root,
            record=record,
            streams={"raw.out": b'{"job":"plan"}\n'},
            base_timestamp=BASE_EPOCH,
        )
        job_dir = self.root / JOB_ID
        self.assertTrue(job_dir.is_dir(), f"job directory missing: {job_dir}")
        if planted is not None:
            self.assertEqual(Path(planted), job_dir)

        record_path = job_dir / "record.json"
        state_path = job_dir / "state.json"
        exit_path = job_dir / "exit"
        raw_path = job_dir / "raw.out"
        prompt_path = job_dir / "prompt.txt"
        for path in (record_path, state_path, exit_path, raw_path, prompt_path):
            self.assertTrue(path.is_file(), f"launcher artifact missing: {path}")

        on_disk = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["id"], JOB_ID)
        self.assertEqual(on_disk["job"], "plan")
        self.assertEqual(on_disk["harness"]["name"], "grok")
        self.assertEqual(
            on_disk["session"]["spend"]["total"],
            100,
            "the planted record's session.spend must survive the write",
        )

        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIsInstance(state, dict)
        for key in ("pid", "pgid", "session", "attempt"):
            self.assertIn(key, state, f"state.json lacks {key}")

        self.assertRegex(
            exit_path.read_text(encoding="utf-8").strip(),
            r"^-?\d+$",
            "exit must carry the harness exit status",
        )
        self.assertEqual(raw_path.read_bytes(), b'{"job":"plan"}\n')
        self.assertIn(MARKER, prompt_path.read_text(encoding="utf-8"))

    def test_repeated_builds_with_identical_arguments_are_byte_identical(self):
        fn = self.builder()
        record = self.built_record()
        kwargs = {
            "record": record,
            "streams": {"raw.out": b"envelope\n"},
            "base_timestamp": BASE_EPOCH,
        }
        self.call_builder(fn, self.root, **kwargs)
        first = snapshot(self.root)
        self.assertGreater(
            len(first),
            0,
            f"{self.root} remained empty after the first fixture build",
        )
        self.call_builder(fn, self.root, **kwargs)
        second = snapshot(self.root)
        self.assertEqual(
            second,
            first,
            (
                f"rebuilding the dispatch store under {self.root} "
                "changed paths or bytes"
            ),
        )

    def test_two_base_timestamps_produce_distinct_fixture_bytes(self):
        fn = self.builder()
        first_root = self.root / "first"
        second_root = self.root / "second"
        first_root.mkdir()
        second_root.mkdir()
        record = self.built_record()
        self.call_builder(
            fn,
            first_root,
            record=record,
            streams={},
            base_timestamp=BASE_EPOCH,
        )
        self.call_builder(
            fn,
            second_root,
            record=record,
            streams={},
            base_timestamp=SECOND_BASE_EPOCH,
        )
        first = snapshot(first_root)
        second = snapshot(second_root)
        self.assertEqual(
            set(first),
            LAUNCHER_ARTIFACTS,
            (
                "the builder wrote paths other than the launcher job "
                f"artifacts under {first_root}: {sorted(first)}"
            ),
        )
        self.assertEqual(
            set(second),
            LAUNCHER_ARTIFACTS,
            (
                "the builder wrote paths other than the launcher job "
                f"artifacts under {second_root}: {sorted(second)}"
            ),
        )
        differing = [
            path
            for path in LAUNCHER_ARTIFACTS
            if first[path] != second[path]
        ]
        self.assertGreater(
            len(differing),
            0,
            (
                "distinct base_timestamp plants left every launcher "
                "artifact unchanged"
            ),
        )


if __name__ == "__main__":
    unittest.main()
