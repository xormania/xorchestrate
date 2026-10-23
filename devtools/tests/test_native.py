import json
import sys
import unittest
from pathlib import Path

from support import Client, initialize, require_tmux, retain_native, runtime_policy, terminal_team, until

from devtools.control import Control
from devtools.store import Store


class NativeProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_tmux()

    def test_xo_replacement_changes_vendor_in_tmux_and_retains_running_specialist(self):
        with terminal_team() as temporary:
            root = Path(temporary)
            workspace, home, state = root / "workspace", root / "home", root / "state"
            workspace.mkdir()
            home.mkdir()
            initialize(state, workspace, ["owner:external"], co="owner", runtime=True)
            fixture = Path(__file__).with_name("native_fixture.py")
            fixture.chmod(0o755)
            policy = runtime_policy()
            for row in policy["profiles"].values():
                row["binary"] = str(fixture)
            (state / "roles.json").write_text(json.dumps(policy))
            vendors = json.loads((state / "vendors.json").read_text())
            vendors["vendors"]["openai"]["available"] = True
            vendors["vendors"]["xai"]["available"] = True
            (state / "vendors.json").write_text(json.dumps(vendors))
            env = {"HOME": str(home), "CODEX_HOME": str(home / ".codex"), "GROK_HOME": str(home / ".grok")}
            store = Store(state)
            co = Client(state, "owner", env=env)
            operator = Client(state, "owner", env=env, operator=True)
            stale = None
            try:
                co.tool("direct", {"title": "Replace XO", "objective": "Retain the team across vendor turnover",
                                   "done_when": "Replacement recovers its specialist", "constraints": "Fixture only"})
                until(lambda: any("Collaborator receipt observed" in row["body"] for row in store.inbox("owner")["messages"]), timeout=12)
                with store.connect() as db:
                    chief = db.execute("SELECT member FROM member_bindings WHERE role='xo'").fetchone()[0]
                    worker = db.execute("SELECT member FROM member_bindings WHERE role='coder'").fetchone()[0]
                before, worker_before = store.member(chief), store.member(worker)
                binding = Control(store, chief).snapshot()
                stale = Client(state, chief, env=env, generation=before["generation"])
                vendors["vendors"]["openai"]["available"] = False
                (state / "vendors.json").write_text(json.dumps(vendors))
                with self.assertRaisesRegex(ValueError, "not available"):
                    co.tool("restart_xo", {"reason": "Vendor quota unavailable; recover current team"})
                self.assertEqual(before["generation"], store.member(chief)["generation"])
                operator.invoke("session_stop", member=chief)
                operator.invoke("session_start", member=chief)
                operator.invoke("message_send", to=chief, work_order=binding["work_order"],
                                body="Operator repaired this transport; recover your retained work context",
                                key="operator-repair")
                until(lambda: store.member(chief)["generation"] != before["generation"], timeout=15)
                until(lambda: store.member(chief)["state"] in {"idle", "active"}, timeout=8)
                self.assertEqual("grok", store.member(chief)["harness"])
                self.assertNotEqual(before["native_id"], store.member(chief)["native_id"])
                self.assertEqual(worker_before["generation"], store.member(worker)["generation"])
                current = Control(store, chief).snapshot()
                for key in ("member", "role", "work_order", "reports_to"):
                    self.assertEqual(binding[key], current[key])
                with self.assertRaisesRegex(ValueError, "replaced"):
                    stale.tool("steer", {"action": "stop"})
                until(lambda: any(row["data"].get("generation") == store.member(chief)["generation"]
                                  for row in store.events(actor=chief, kind="onboarding_delivered")["events"]), timeout=8)
            finally:
                for row in store.members():
                    if row["name"] != "owner":
                        store.session(row["name"], desired="stopped")
                until(lambda: all(row["state"] in {"stopped", "failed"} for row in store.members()), timeout=10)
                if stale:
                    stale.close()
                operator.close()
                co.close()
                retain_native(state, "xo-vendor-replacement")

    def test_co_direction_launches_xo_and_collaborator_and_pause_retains_live_session(self):
        with terminal_team() as temporary:
            root = Path(temporary)
            workspace, home = root / "workspace", root / "login-home"
            workspace.mkdir()
            home.mkdir()
            state = root / "state"
            initialize(state, workspace, ["owner:external", "chief:codex", "worker:codex"], co="owner")
            fixture = Path(__file__).with_name("native_fixture.py")
            fixture.chmod(0o755)
            config = json.loads((state / "team.json").read_text())
            for row in config["members"][1:]:
                row["binary"] = str(fixture)
            (state / "team.json").write_text(json.dumps(config))
            env = {"HOME": str(home), "CODEX_HOME": str(home / "codex")}
            store = Store(state)
            co, xo = Client(state, "owner", env=env), None
            try:
                co.tool(
                    "direct",
                    {
                        "title": "Command path",
                        "objective": "Organize live collaborators",
                        "done_when": "Reported and steered",
                        "constraints": "Fixture only",
                    },
                )
                until(
                    lambda: any(
                        "Collaborator receipt observed" in row["body"] for row in store.inbox("owner")["messages"]
                    ),
                    timeout=10,
                )
                before = store.member("worker")
                self.assertIn(before["state"], {"idle", "active"})
                xo = Client(state, "chief", env=env)
                paused = xo.tool("steer", {"action": "pause", "work": "Fixture implementation"})["work"][0]
                paused_id = int(paused["direction"]["reference"].split(":")[1])
                until(
                    lambda: any(
                        row["id"] == paused_id and row["acknowledged"] for row in store.inbox("worker")["messages"]
                    )
                )
                current = store.member("worker")
                self.assertEqual(
                    (before["native_id"], before["generation"], before["pid"]),
                    (current["native_id"], current["generation"], current["pid"]),
                )
                self.assertEqual("running", current["desired"])
                progress = xo.tool("monitor", {})["collaborators"][0]
                self.assertEqual("paused", progress["work_state"])
                self.assertEqual(23, progress["latest_usage"]["observation"]["last"]["inputTokens"])
                resumed = xo.tool("steer", {"action": "resume", "work": "Fixture implementation"})
                self.assertFalse(resumed["work"][0]["session"]["launch_pending"])
                self.assertEqual(before["generation"], store.member("worker")["generation"])
                xo.tool("steer", {"action": "stop", "work": "Fixture implementation"})
                until(lambda: store.member("worker")["state"] == "stopped", timeout=8)
                self.assertEqual("paused", xo.tool("monitor", {})["collaborators"][0]["work_state"])
            except Exception:
                for member in ("chief", "worker"):
                    log = state / "logs" / (member + ".log")
                    if log.exists():
                        print(log.read_text(), file=sys.stderr)
                raise
            finally:
                for member in ("chief", "worker"):
                    store.session(member, desired="stopped")
                until(
                    lambda: all(
                        store.member(member)["state"] in {"stopped", "failed"} for member in ("chief", "worker")
                    ),
                    timeout=8,
                )
                if xo:
                    xo.close()
                co.close()
                retain_native(state, "co-xo-collaborator")

    def test_live_messages_during_unfinished_work_keep_native_identity(self):
        for harness in ("codex", "grok", "claude"):
            with self.subTest(harness=harness), terminal_team() as temporary:
                root = Path(temporary)
                workspace = root / "workspace"
                workspace.mkdir()
                gates = workspace / "fixture-turns"
                gates.mkdir()
                state = root / "state"
                home = root / "login-home"
                home.mkdir()
                initialize(state, workspace, ["owner:external", "worker:" + harness])
                fixture = Path(__file__).with_name("claude_fixture.py" if harness == "claude" else "native_fixture.py")
                fixture.chmod(0o755)
                config = json.loads((state / "team.json").read_text())
                config["members"][1]["binary"] = str(fixture)
                (state / "team.json").write_text(json.dumps(config))
                store = Store(state)
                env = {
                    "HOME": str(home),
                    "CODEX_HOME": str(home / "codex"),
                    "GROK_HOME": str(home / "grok"),
                }
                client = Client(state, "owner", env=env, operator=True)
                try:
                    if harness == "codex":
                        from devtools.terminals import command

                        command(state, "new-session", "-d", "-s", "environment-seed", "--", "sleep", "30")
                        stale = root / "stale-python"
                        stale.mkdir()
                        marker = root / "stale-imported"
                        (stale / "sitecustomize.py").write_text(f"open({str(marker)!r}, 'w').write('stale')\n")
                        for name, value in {"PYTHONHOME": str(stale), "PYTHONPATH": str(stale),
                                            "LD_PRELOAD": "/missing/stale.so", "LD_LIBRARY_PATH": str(stale),
                                            "LD_TRACE_LOADED_OBJECTS": "1"}.items():
                            command(state, "set-environment", "-g", name, value)
                    order = client.invoke(
                        "work_order_create", title="Protocol test", objective="Live work", done_when="Received"
                    )
                    client.invoke("member_bind", member="owner", role="co", work_order=order["work_order"]["id"])
                    client.invoke(
                        "member_bind",
                        member="worker",
                        role="coder",
                        work_order=order["work_order"]["id"],
                        reports_to="owner",
                    )
                    started = client.invoke("session_start", member="worker")
                    self.assertEqual("running", started["requested"])
                    if harness == "codex":
                        self.assertFalse(marker.exists())
                    self.assertTrue(client.invoke("terminal_status", member="worker")["exists"])
                    until(lambda: store.member("worker")["state"] == "idle"
                          and store.events(actor="worker", kind="readiness_ready")["events"])
                    original_id = store.member("worker")["native_id"]
                    runtime = until(lambda: store.events(kind="runtime_observed", actor="worker")["events"])
                    self.assertEqual("fixture-model", runtime[-1]["data"]["settings"]["model"])
                    self.assertTrue(client.invoke("session_start", member="worker")["already_running"])

                    def work_messages():
                        return [row for row in store.inbox("worker")["messages"] if row["category"] != "readiness"]

                    def work_submissions():
                        identifiers = {row["id"] for row in work_messages()}
                        return [row for row in store.events(kind="message_submitted", actor="worker")["events"]
                                if row["data"]["message"] in identifiers]

                    first = client.invoke(
                        "message_send", to="worker", body="Keep working while a turn is held", key="first"
                    )
                    until(lambda: work_messages()[0]["acknowledged"])
                    self.assertEqual("coder", store.events(kind="onboarding_delivered", actor="worker")["events"][-1]["data"]["role"])
                    self.assertFalse(store.events(kind="skill_loaded", actor="worker")["events"])
                    until(lambda: (gates / ("started-" + str(first["id"]))).exists())
                    self.assertFalse((gates / ("completed-" + str(first["id"]))).exists())
                    second = client.invoke(
                        "message_send", to="worker", body="New message during unfinished work", key="second"
                    )
                    until(lambda: work_messages()[1]["acknowledged"], timeout=4)
                    rows = work_messages()
                    self.assertEqual([first["id"], second["id"]], [row["id"] for row in rows])
                    self.assertEqual(original_id, store.member("worker")["native_id"])
                    self.assertEqual("active", store.member("worker")["state"])
                    until(lambda: len(work_submissions()) == 2)
                    if harness == "grok":
                        submissions = work_submissions()
                        self.assertEqual("grok/_x.ai/interject", submissions[-1]["data"]["transport"])
                    else:
                        until(lambda: store.events(kind="native_usage")["events"])
                    (gates / "release").touch()
                    until(lambda: (gates / ("completed-" + str(first["id"]))).exists())
                    until(lambda: store.member("worker")["state"] == "idle")
                    third = client.invoke("message_send", to="worker", body="Begin work after the previous turn completed", key="third")
                    until(lambda: (gates / ("completed-" + str(third["id"]))).exists())
                    until(lambda: store.member("worker")["state"] == "idle")
                    self.assertTrue(work_messages()[2]["acknowledged"])
                    self.assertEqual(original_id, store.member("worker")["native_id"])
                    submissions = until(lambda: rows if len(rows := work_submissions()) == 3 else None)
                    if harness == "grok":
                        self.assertEqual(["grok/session/prompt", "grok/_x.ai/interject", "grok/session/prompt"],
                                         [row["data"]["transport"] for row in submissions])
                except Exception:
                    log = state / "logs/worker.log"
                    if log.exists():
                        print(log.read_text(), file=sys.stderr)
                    raise
                finally:
                    try:
                        client.invoke("session_stop", member="worker")
                        until(lambda: store.member("worker")["state"] in {"stopped", "failed"}, timeout=8)
                    finally:
                        client.close()
                        if harness == "codex":
                            command(state, "kill-session", "-t", "=environment-seed", check=False)
                        retain_native(state, harness)
                self.assertEqual("stopped", store.member("worker")["state"])
