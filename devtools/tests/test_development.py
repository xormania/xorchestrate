import json
import os
import sys
import unittest
from unittest.mock import patch

from fixtures.development import DevelopmentFixture
from support import Client, fixture_work, held_check, until

from devtools.api import API
from devtools.development import Development
from devtools.operations import Operations, execute
from devtools.workspaces import git


class DevelopmentTests(DevelopmentFixture, unittest.TestCase):

    def test_checks_leave_application_environment_to_project_configuration(self):
        program = (
            "import json, os\n"
            "print(json.dumps({name: os.environ.get(name) for name in "
            "('APP_ENV', 'APP_DEBUG', 'COMPOSER_NO_INTERACTION')}))\n"
        )
        self.configure({"environment": [sys.executable, "-c", program]})
        with patch.dict(os.environ, {"APP_ENV": "prod", "APP_DEBUG": "0", "COMPOSER_NO_INTERACTION": "0"}):
            result = Development(self.store, "coder").check()
        self.assertTrue(result["passed"], result)
        self.assertEqual(
            {"APP_ENV": None, "APP_DEBUG": None, "COMPOSER_NO_INTERACTION": "1"},
            json.loads(result["checks"][0]["output"]),
        )

    def test_checks_complete_after_client_disconnect_and_route_evidence(self):
        argv, started, release = held_check(self.root)
        self.configure({"fixture": argv})
        client = Client(self.state, "coder")
        try:
            receipt = client.tool("work_check", {})
            self.assertIn(receipt["state"], {"queued", "running"})
            until(started.exists)
            client.close()
            self.assertEqual("running", self.api.operations.inspect()["operations"][0]["state"])
        finally:
            release.touch()
            client.close()
        result = until(
            lambda: next((r for r in self.api.operations.inspect()["operations"] if r["state"] == "completed"), None)
        )
        result = json.loads(self.api.call("work_context", {"reference": result["reference"]})["content"])
        self.assertTrue(result["result"]["passed"])
        evidence = self.api.call("work_context", {"reference": result["result"]["checks"][0]["evidence"]})
        self.assertIn("measured", evidence["content"])
        for member in ("coder", "xo"):
            messages = self.store.inbox(member)["messages"]
            notice = next(json.loads(row["body"]) for row in messages if '"state":"completed"' in row["body"])
            reference = notice["checks"]["evidence"][0]["reference"]
            evidence = API(self.state, member).call("work_context", {"reference": reference})
            self.assertIn("measured", evidence["content"])

    def test_completion_notices_preserve_outcomes_without_replaying_output(self):
        self.configure()
        for failed in (False, True):
            result = {
                "passed": not failed, "revision": "a" * 40,
                "checks": [{"check": "fixture", "exit_code": int(failed),
                            "output": "UNIQUE DIAGNOSTIC " * 1500, "evidence": "artifact:1"}],
            }
            with patch.object(Operations, "launch"), patch.object(Development, "check", return_value=result):
                receipt = self.api.call("work_check", {})
                ident = int(receipt["reference"].split(":")[1])
                execute(self.state, "coder", ident)
            retained = self.api.operations.get(ident)["result"]
            self.assertEqual(result, {key: value for key, value in retained.items()
                                      if key not in {"diagnostic_guidance", "failure"}})
            self.assertEqual(failed, "diagnostic_guidance" in retained)
            self.assertEqual(failed, "failure" in retained)
            if failed:
                self.assertEqual("check_failed", retained["failure"]["reason"])
            for member in ("coder", "xo"):
                notice = json.loads(self.store.inbox(member)["messages"][-1]["body"])
                self.assertEqual(receipt["reference"], notice["reference"])
                self.assertEqual(not failed, notice["passed"])
                self.assertEqual(int(failed), notice["checks"]["failed"])
                self.assertEqual([{"check": "fixture", "reference": "artifact:1"}], notice["checks"]["evidence"])
                self.assertNotIn("UNIQUE DIAGNOSTIC", json.dumps(notice))
                self.assertEqual(failed and member == "coder", "diagnostic_guidance" in notice)
                self.assertLess(len(json.dumps(notice)), 4000 if member == "coder" else 1000)
        with patch.object(Operations, "launch"):
            receipt = self.api.call("work_check", {})
            ident = int(receipt["reference"].split(":")[1])
        from devtools.workspaces import Workspaces

        blocked = {"state": "needs_attention", "message": "Validation changed files", "validation": result}
        with self.store.connect() as db:
            db.execute("UPDATE operations SET action='work_submit',arguments=? WHERE id=?",
                       (json.dumps({"summary": "Review"}), ident))
        with patch.object(Workspaces, "submit", return_value=blocked):
            execute(self.state, "coder", ident)
        notice = json.loads(self.store.inbox("xo")["messages"][-1]["body"])
        self.assertEqual("needs_attention", notice["outcome"])
        self.assertFalse(notice["passed"])
        self.assertEqual(1, notice["checks"]["failed"])
        self.assertEqual([{"check": "fixture", "reference": "artifact:1"}], notice["checks"]["evidence"])
        with patch.object(Operations, "launch"), patch.object(Development, "check", side_effect=ValueError("failure " * 400)):
            receipt = self.api.call("work_check", {})
            ident = int(receipt["reference"].split(":")[1])
            execute(self.state, "coder", ident)
        notice = json.loads(self.store.inbox("coder")["messages"][-1]["body"])
        self.assertEqual("failed", notice["state"])
        self.assertEqual(500, len(notice["error"]))
        self.assertEqual(2000, len(self.api.operations.get(ident)["error"]))

    def test_history_summaries_page_stably_and_full_results_remain_scoped(self):
        self.configure()
        scope = self.api.control.current()
        payload = {"preview": "diagnostic 🦉" * 2500, "result": None, "evidence": "artifact:1"}
        def retain():
            with self.store.connect() as db:
                return db.execute(
                    "INSERT INTO operations(member,work_order,job,binding,action,arguments,state,result,created,updated) "
                    "VALUES (?,?,?,?,?,?,'completed',?,'fixture','fixture')",
                    ("coder", scope["work_order"], scope["job"], json.dumps(scope), "framework_inspect", "{}",
                     json.dumps(payload)),
                ).lastrowid
        ids = [retain() for _ in range(12)]
        first = self.api.call("work_inspect", {"section": "operations"})
        self.assertEqual(10, len(first["operations"]))
        self.assertNotIn("diagnostic", json.dumps(first))
        self.assertLess(len(json.dumps(first)), 7000)
        retain()  # New completions must not shift the continuation cursor.
        second = self.api.call("work_inspect", {"section": "operations", "offset": first["next_offset"]})
        refs = [r["reference"] for r in first["operations"] + second["operations"]]
        self.assertEqual([f"operation:{i}" for i in reversed(ids)], refs)
        self.assertIsNone(second["next_offset"])
        for api in (self.api, API(self.state, "xo")):
            parts, offset = [], 0
            while offset is not None:
                page = api.call("work_context", {"reference": refs[0], "offset": offset})
                self.assertLessEqual(len(page["content"]), 8000)
                parts.append(page["content"])
                offset = page["next_offset"]
            self.assertEqual(payload, json.loads("".join(parts))["result"])
        with self.assertRaises(ValueError):
            self.api.call("work_report", {"text": "Reply", "responding_to": refs[0]})
        fixture_work(API(self.state, "xo"), [
            {"name": "Other", "role": "docs", "objective": "Other", "done_when": "Done", "constraints": "Docs"}
        ])
        with self.assertRaisesRegex(ValueError, "not available"):
            API(self.state, "docs").call("work_context", {"reference": refs[0]})

    def test_active_retry_reuses_operation_and_worker_rechecks_binding(self):
        self.configure()
        with patch.object(Operations, "launch"):
            first = self.api.call("work_check", {})
            second = self.api.call("work_check", {})
            self.assertEqual(first["reference"], second["reference"])
            with self.assertRaisesRegex(ValueError, "already using"):
                self.api.call("framework_inspect", {"kind": "mate"})
            API(self.state, "xo").call("steer", {"action": "pause", "work": "Feature"})
            execute(self.state, "coder", int(first["reference"].split(":")[1]))
        # Even diagnostic/check calls must not start new execution after pause.
        value = self.api.operations.inspect()["operations"][0]
        self.assertEqual("failed", value["state"])
        self.assertIn("paused", value["error"])

    def test_crashed_check_retains_signal_and_output_without_retry(self):
        program = (
            "import os, resource, signal\n"
            "from pathlib import Path\n"
            "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
            "with Path('executions').open('a') as output: output.write('started\\n')\n"
            "print('last event before crash', flush=True)\n"
            "os.kill(os.getpid(), signal.SIGSEGV)\n"
        )
        root = self.configure({"crash": [sys.executable, "-c", program]})
        result = Development(self.store, "coder").check()
        self.assertFalse(result["passed"])
        check = result["checks"][0]
        self.assertEqual(-11, check["exit_code"])
        self.assertEqual("SIGSEGV", check["signal"])
        self.assertIn("last event before crash", check["output"])
        self.assertEqual("started\n", (root / "executions").read_text())
        artifact = self.api.call("work_context", {"reference": check["evidence"]})
        self.assertEqual("SIGSEGV", json.loads(artifact["content"])["signal"])

    def test_dead_worker_is_uncertain_without_automatic_replay(self):
        self.configure()
        with patch.object(Operations, "launch"):
            receipt = self.api.call("work_check", {})
        ident = int(receipt["reference"].split(":")[1])
        with self.store.connect() as db:
            db.execute("UPDATE operations SET state='running' WHERE id=?", (ident,))
        self.api.operations.tick()
        self.assertEqual("uncertain", self.api.operations.get(ident)["state"])

    def test_maker_metadata_controls_arguments_without_shell_or_global_options(self):
        metadata = {
            "name": "make:command",
            "definition": {
                "arguments": {"name": {"is_required": True, "is_array": False}},
                "options": {
                    "option": {"accept_value": True, "is_multiple": True},
                    "env": {"accept_value": True, "is_multiple": False},
                },
            },
        }
        payload = "name; $(touch marker)"
        argv = Development.maker_argv(metadata, {"name": payload}, {"option": ["one:bool", "two:string"]})
        self.assertEqual(payload, argv[-1])
        self.assertEqual("--", argv[-2])
        self.assertIn("--option=one:bool", argv)
        with self.assertRaisesRegex(ValueError, "managed"):
            Development.maker_argv(metadata, {"name": "Valid"}, {"env": "prod"})
        with self.assertRaisesRegex(ValueError, "metadata"):
            Development.maker_argv(metadata, {"name": "Valid", "command": "shell"}, {})

    def test_failed_checks_preserve_checkpoint_and_prevent_publication(self):
        root = self.configure({"broken": [sys.executable, "-c", "raise SystemExit(2)"]})
        (root / ".gitignore").write_text("/vendor/\n")
        git(root, "add", ".gitignore")
        git(root, "commit", "-m", "Fixture ignores")
        with patch("devtools.github.GitHub") as provider:
            value = self.api.workspaces.submit("Measured validation failure")
        self.assertEqual("needs_attention", value["state"])
        self.assertFalse(value["validation"]["passed"])
        provider.assert_not_called()
        self.assertTrue(value["checkpoint"].startswith("checkpoint:"))

    def test_test_writer_cannot_use_non_test_maker(self):
        self.configure()
        scope = self.api.control.snapshot()
        self.api.control.bind("coder", "test_writer", scope["work_order"], scope["job"], "xo")
        with self.assertRaisesRegex(ValueError, "make:test"):
            Development(self.store, "coder").generate("make:command", {"name": "app:wrong"})


if __name__ == "__main__":
    unittest.main()
