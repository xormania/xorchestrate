"""Every dispatched worker gets the lane's read plane and a measured
list of what exists; a write envelope must say what it reused.

Owner, 2026-09-12: "every work-order works in isolation from the rest of
the code - its doesn't use any previous work, it works blind"; "enforce
usage - not doc it"; "it has to be made impossible"; "the whole purpose
between the hooks and mcp (and skills) is to have a common framework to
stop you or other agents from going outside the architecture,
duplicating, or worse - ignoring".

Measured that day: lane MCP v2 (admitted 2026-09-05) had zero references
in launch.py; isolation stripped MCP from every worker on purpose. Live
probes with the exact wiring below answered JOBS=9 on all three
harnesses before this was written (session record, 2026-09-12).

One producer (Claude Fable 5.1) under hand grant 158; the second
producer is absent by the owner's word.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
from pathlib import Path

import launch_support as ls

from ops.testkit import (
    World,  # noqa: F401 - _TempLaunch owns the world
    run,
)

REPO = Path(__file__).resolve().parents[4]
LANE_MCP = REPO / "ops" / "conductor" / "tools" / "lane_mcp.py"
SERVER = "agentscient-lane"
SERVER_KEY = "agentscient_lane"
TOOLS = ("lane_brief", "lane_jobs", "lane_changes", "lane_findings", "lane_record")
BRIEF_MARK = "== what exists (measured at launch)"
CLAUSE = "Before building anything, look up what exists"
REUSE_KEY = "reused:"


class _Wired(ls._TempLaunch):
    """A snapshot that carries the lane MCP tool and a few capabilities."""

    def setUp(self):
        super().setUp()
        self._write("ops/conductor/tools/lane_mcp.py",
                    '"""Lane MCP stub for the fixture."""\n')
        self._write("ops/conductor/tools/census.py",
                    '"""Count things the fixture owns.\n\nMore prose.\n"""\n')
        self._write("ops/conductor/ideas/I-900-fixture-idea.md",
                    "# I-900 — a fixture idea nobody should rebuild\n\nbody\n")
        self._write("ops/conductor/handoff/HANDOFF-2026-01-01-fixture.md",
                    "# Handoff\n\n## Corrections to retain\n\n- keep this exact line\n\n## Next\n\nother\n")
        self._write("ops/lane.conf", "LANE_NAME='fixture'\nMODEL_GROK='m'\n")
        self._write("docs/tests.md", "# Tests\n\n## Invariants\n\n- `testkit.world` — hermetic\n- `testkit.refusal-wire` — the wire\n")
        self.ref = self._commit("plant capabilities")

        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        os.environ["TASK_LAUNCH_MCP_CALLS"] = "1"
        self.addCleanup(os.environ.pop, "TASK_LAUNCH_MCP_CALLS", None)

    def job_dir(self, rec):
        return Path(rec["snapshot"]["root"]).parent

    def prompt_seen(self):
        w = self.read_witness()
        return (w.get("prompt_text") or "") + "\n" + (w.get("stdin") or "")


class TheConfigIsWrittenAndValid(_Wired):
    def test_lane_mcp_json_checks_clean_against_the_real_server(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        cfg = self.job_dir(rec) / "lane-mcp.json"
        self.assertTrue(cfg.is_file(), "lane-mcp.json not written beside the snapshot")
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["consumer"], rec["id"])
        self.assertEqual(data["job_registry"], str(self.jobs_file))
        self.assertEqual(Path(data["dispatch_store"]), self.job_dir(rec).parent)
        self.assertIsInstance(data["job_ids"], list)
        proc = run(
            [sys.executable, "-B", str(LANE_MCP), "--config", str(cfg), "check"],
            cwd=self.repo, env=self.world.env(), timeout=60,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"tools": 7, "valid": True})
        proc = run(
            [sys.executable, "-B", str(LANE_MCP), "--config", str(cfg),
             "query", "lane_jobs", "{}"],
            cwd=self.repo, env=self.world.env(), timeout=60,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        names = {j["name"] for j in json.loads(proc.stdout)["jobs"]}
        self.assertIn("plan", names)
        mcp = rec["brief"]["mcp"]
        self.assertEqual(mcp["config"], str(cfg))
        self.assertEqual(mcp["server"], ["python3", "-B",
                                         str(Path(rec["snapshot"]["root"]) / "ops/conductor/tools/lane_mcp.py"),
                                         "--config", str(cfg), "serve"])

    def test_follows_and_same_unit_records_are_the_job_ids(self):
        first, *_ = self.launch_ok(job="plan", harness="grok", stage="plan",
                                   extra=("--unit", "fixture-unit"))
        code, out, err = self.dispatch(self.argv_for(job="plan", harness="grok", stage="plan",
                                                     extra=("--unit", "fixture-unit",
                                                            "--follows", first["id"])))
        self.assertEqual(code, 0, out + err)
        records = sorted(self.repo.glob(".dev/records/dispatches/*.json"))
        self.assertEqual(len(records), 2, records)
        second = next(json.loads(r.read_text()) for r in records if r.stem != first["id"])
        ids = json.loads((self.job_dir(second) / "lane-mcp.json").read_text())["job_ids"]
        self.assertEqual(ids[0], first["id"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(ids), 20)


class EachHarnessCarriesTheServer(_Wired):
    def test_claude_gets_mcp_config_and_the_tool_rule(self):
        rec, *_ = self.launch_ok(job="plan", harness="claude", stage="plan")
        argv = self.read_witness()["argv"]
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn("--mcp-config", argv)
        path = Path(argv[argv.index("--mcp-config") + 1])
        self.assertEqual(path, self.job_dir(rec) / "mcp.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["mcpServers"][SERVER]["command"], "python3")
        self.assertEqual(data["mcpServers"][SERVER]["args"], rec["brief"]["mcp"]["server"][1:])
        allowed = argv[argv.index("--allowedTools") + 1: argv.index("--disallowedTools")]
        self.assertIn(f"mcp__{SERVER}", allowed)

    def test_codex_gets_the_two_config_overrides(self):
        rec, *_ = self.launch_ok(job="plan", harness="codex", stage="plan")
        argv = self.read_witness()["argv"]
        flags = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-c"]
        self.assertIn(f'mcp_servers.{SERVER_KEY}.command="python3"', flags)
        args_flag = next(f for f in flags if f.startswith(f"mcp_servers.{SERVER_KEY}.args="))
        self.assertEqual(json.loads(args_flag.split("=", 1)[1]), rec["brief"]["mcp"]["server"][1:])

    def test_grok_gets_config_toml_in_its_isolated_home(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        home = Path(self.read_witness()["env"]["GROK_HOME"])
        self.assertEqual(home, Path(rec["harness"]["isolation"]["env"]["GROK_HOME"]))
        toml = (home / "config.toml").read_text(encoding="utf-8")
        self.assertIn(f"[mcp_servers.{SERVER_KEY}]", toml)
        self.assertIn('command = "python3"', toml)
        for token in rec["brief"]["mcp"]["server"][1:]:
            self.assertIn(json.dumps(token), toml)
        self.assertEqual(rec["brief"]["mcp"]["grok_config"], str(home / "config.toml"))
        self.assertNotIn("--mcp-config", self.read_witness()["argv"])


class ARelocatedJobIsRewiredOnResume(_Wired):
    """Codex review of PR #158 (2026-09-12): a retained job moved to another
    dispatch store and resumed kept a lane-mcp.json naming the old store,
    and grok's config.toml the old server and config paths, so the resumed
    server reported prior records missing while any call still counted."""

    def relocate(self, rec):
        old_job = self.job_dir(rec)
        new_store = self.home / "jobs-moved"
        new_store.mkdir()
        shutil.move(str(old_job), str(new_store / rec["id"]))
        self.assertFalse(old_job.exists(), "plant: the job left the old store")
        self.assertTrue((new_store / rec["id"] / "lane-mcp.json").is_file(),
                        "plant: the config moved with the job")
        os.environ["DISPATCH_JOBS"] = str(new_store)
        self.addCleanup(os.environ.__setitem__, "DISPATCH_JOBS", str(self.jobs_root))
        self.witness.unlink(missing_ok=True)
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(code, 0, self.combined(out, err))
        return old_job, new_store / rec["id"]

    def test_grok_config_and_lane_mcp_json_name_the_new_store(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        old_job, new_job = self.relocate(rec)
        cfg = new_job / "lane-mcp.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        self.assertEqual(Path(data["dispatch_store"]).resolve(), new_job.parent.resolve())
        proc = run(
            [sys.executable, "-B", str(LANE_MCP), "--config", str(cfg), "check"],
            cwd=self.repo, env=self.world.env(), timeout=60,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"tools": 7, "valid": True})
        home = Path(self.read_witness()["env"]["GROK_HOME"])
        self.assertEqual(home, new_job / "home" / "grok")
        toml = (home / "config.toml").read_text(encoding="utf-8")
        self.assertIn(json.dumps(str(cfg.resolve())), toml)
        self.assertNotIn(str(old_job), toml)
        mcp = self.read_record()["brief"]["mcp"]
        self.assertEqual(mcp["config"], str(cfg.resolve()))
        self.assertEqual(mcp["sha256"], ls.sha256_file(cfg))
        self.assertEqual(mcp["grok_config"], str(home / "config.toml"))
        self.assertNotIn(str(old_job), json.dumps(mcp))

    def test_claude_mcp_json_names_the_new_store(self):
        rec, *_ = self.launch_ok(job="plan", harness="claude", stage="plan")
        old_job, new_job = self.relocate(rec)
        argv = self.read_witness()["argv"]
        path = Path(argv[argv.index("--mcp-config") + 1])
        self.assertEqual(path, new_job / "mcp.json")
        args = json.loads(path.read_text(encoding="utf-8"))["mcpServers"][SERVER]["args"]
        self.assertIn(str((new_job / "lane-mcp.json").resolve()), args)
        self.assertNotIn(str(old_job), json.dumps(args))


class ASnapshotWithoutTheServerDeclaresIt(ls._TempLaunch):
    def test_absence_is_declared_and_nothing_is_wired(self):
        rec, *_ = self.launch_ok(job="plan", harness="claude", stage="plan")
        self.assertEqual(rec["brief"]["mcp"],
                         {"unresolved": "snapshot has no ops/conductor/tools/lane_mcp.py"})
        argv = self.read_witness()["argv"]
        self.assertNotIn("--mcp-config", argv)
        self.assertFalse((Path(rec["snapshot"]["root"]).parent / "lane-mcp.json").exists())


class ThePromptCarriesWhatExists(_Wired):
    def test_levers_ideas_corrections_jobs_and_tools_are_named(self):
        self.launch_ok(job="plan", harness="grok", stage="plan")
        text = self.prompt_seen()
        self.assertIn(BRIEF_MARK, text)
        self.assertIn("census.py — Count things the fixture owns.", text)
        self.assertIn("I-900-fixture-idea", text)
        self.assertIn("keep this exact line", text)
        self.assertIn("LANE_NAME", text)
        self.assertIn("MODEL_GROK", text)
        for tool in TOOLS:
            self.assertIn(tool, text)
        self.assertIn(SERVER, text)
        self.assertIn(CLAUSE, text)
        self.assertIn(REUSE_KEY, text)
        self.assertIn("test kit (ops/testkit): World, run, Tools, cases", text)
        self.assertIn("testkit.refusal-wire", text)
        self.assertLess(len(text.split(BRIEF_MARK, 1)[1]), 8192, "the brief is bounded")

    def test_absent_sections_are_declared_not_silent(self):
        plain = ls._TempLaunch.setUp
        # a bare fixture: no ideas, no handoff, no lane.conf
        self.tearDown()
        plain(self)
        self.launch_ok(job="plan", harness="grok", stage="plan")
        text = self.prompt_seen()
        self.assertIn(BRIEF_MARK, text)
        self.assertIn("ideas: absent", text)
        self.assertIn("corrections: absent", text)
        self.assertIn("lane.conf: absent", text)
        self.assertIn("test kit: absent", text)


class AWriteEnvelopeMustDeclareReuse(_Wired):
    def test_undeclared_reuse_invalidates_a_write_envelope(self):
        os.environ["TASK_LAUNCH_NOTE"] = "built it"
        self.addCleanup(os.environ.pop, "TASK_LAUNCH_NOTE", None)
        rec, *_ = self.launch_ok(job="author-tests", harness="grok", stage="tests")
        env = rec["result"]["envelope"]
        self.assertEqual(env["status"], "invalid")
        self.assertIn("reuse-undeclared", env["note"])
        self.assertEqual(rec["result"]["reuse"], "undeclared")

    def test_declared_reuse_is_recorded(self):
        os.environ["TASK_LAUNCH_NOTE"] = "reused: census.py; nothing else applied"
        self.addCleanup(os.environ.pop, "TASK_LAUNCH_NOTE", None)
        rec, *_ = self.launch_ok(job="author-tests", harness="grok", stage="tests")
        self.assertEqual(rec["result"]["envelope"]["status"], "ok")
        self.assertEqual(rec["result"]["reuse"], "census.py; nothing else applied")

    def test_read_roles_are_not_gated(self):
        os.environ["TASK_LAUNCH_NOTE"] = "reviewed"
        self.addCleanup(os.environ.pop, "TASK_LAUNCH_NOTE", None)
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertEqual(rec["result"]["envelope"]["status"], "ok")
        self.assertNotIn("reuse", rec["result"])


class EveryDispatchCarriesProofOfLooking(_Wired):
    """Owner, 2026-09-12: "not just proof at PR - proof for every dispatch".
    The record's result.proof counts the worker's lane MCP calls from its
    own store; a wired worker that never called the server has its ok
    envelope invalidated as framework-unused."""

    def test_a_worker_that_never_looked_is_invalid(self):
        os.environ["TASK_LAUNCH_MCP_CALLS"] = "0"
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        proof = rec["result"]["proof"]
        self.assertTrue(proof["mcp_wired"])
        self.assertEqual(proof["mcp_calls"], 0)
        self.assertEqual(rec["result"]["envelope"]["status"], "invalid")
        self.assertIn("framework-unused", rec["result"]["envelope"]["note"])

    def test_calls_are_counted_from_each_store(self):
        for harness, n in (("grok", 2), ("codex", 3), ("claude", 1)):
            with self.subTest(harness=harness):
                self.tearDown()
                _Wired.setUp(self)
                os.environ["TASK_LAUNCH_MCP_CALLS"] = str(n)
                rec, *_ = self.launch_ok(job="plan", harness=harness, stage="plan")
                proof = rec["result"]["proof"]
                self.assertEqual(proof["mcp_calls"], n, proof)
                self.assertEqual(proof["mcp_tools"], ["lane_jobs"])
                self.assertEqual(rec["result"]["envelope"]["status"], "ok")
                # the record keeps paths portable; readers invert them
                self.assertTrue(proof["store"].endswith((".jsonl", "summary.json")), proof)

    def test_an_unwired_snapshot_records_no_gate(self):
        plain = ls._TempLaunch
        self.tearDown()
        plain.setUp(self)
        os.environ["TASK_LAUNCH_WRITE_STREAM"] = "1"
        os.environ["TASK_LAUNCH_MCP_CALLS"] = "0"
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        proof = rec["result"]["proof"]
        self.assertFalse(proof["mcp_wired"])
        self.assertIsNone(proof["mcp_calls"])
        self.assertEqual(rec["result"]["envelope"]["status"], "ok")


if __name__ == "__main__":
    unittest.main()
