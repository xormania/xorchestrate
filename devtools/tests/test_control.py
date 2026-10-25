import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from fixtures.control import ControlFixture
from support import until

from devtools.api import API
from devtools.control import Control


class ReadinessContextTests(ControlFixture, unittest.TestCase):
    def test_completed_work_co_receives_only_authorized_standby_readiness(self):
        from devtools import processes, standby
        from devtools.delivery import Delivery

        team_path = self.state / "team.json"
        team = json.loads(team_path.read_text())
        team["co"] = "owner"
        for member in team["members"]:
            if member["name"] == "idle":
                member.update(harness="codex", binary="/bin/true")
        team_path.write_text(json.dumps(team))
        parent = API(self.state, "owner")
        # Conclude releases the execution team through Delivery.release while
        # CO retains its completed work binding and conversation.
        scope = parent.control.snapshot()
        with self.store.connect() as db:
            db.execute("UPDATE members SET harness='codex' WHERE name='idle'")
            for child in parent.commands._descendants(db, scope):
                Delivery.release(db, child["member"])
            db.execute("UPDATE work_orders SET status='done' WHERE id=?", (self.order,))
        with patch("devtools.readiness.startup_preflight", return_value={"status": "preflight_passed", "checks": []}):
            member = parent.call("roll_call", {})["xo"]["member"]
        with self.store.connect() as db:
            revision = standby.binding(db, member)["revision"]
        generation = self.store.begin_session(member, os.getpid(), standby_revision=revision)
        processes.register(self.store, member, generation, os.getpid())
        self.store.session(member, generation=generation, native_id="standby-native", state="idle")
        child = API(self.state, member)
        onboard = child.call("onboard", {})
        ready = child.call("readiness_report", {key: onboard["readiness"][key]
                           for key in ("challenge", "onboarding_revision")})
        self.assertEqual("ready", ready["status"])
        for arguments, field in (({}, "incoming"), ({"section": "messages"}, "messages")):
            self.assertIn(ready["receipt"], [row["reference"] for row in parent.call("work_context", arguments)[field]])
        self.assertTrue(parent.call("work_context", {"reference": ready["receipt"]})["readiness_current"])
        self.assertEqual(int(ready["receipt"][8:]), self.store.take_delivery("owner", scoped=True)["id"])

        # A null work scope and readiness label alone grant no visibility.
        unrelated = self.store.send(member, "owner", "Unverified null-work readiness", key="unverified",
                                    work_order=None, job=None)
        with self.store.connect() as db:
            db.execute("UPDATE messages SET category='readiness' WHERE id=?", (unrelated["id"],))
        reference = "message:" + str(unrelated["id"])
        with self.assertRaisesRegex(ValueError, "not available"):
            parent.call("work_context", {"reference": reference})
        self.assertNotIn(reference, [row["reference"] for row in parent.call("work_context", {"section": "messages"})["messages"]])
        self.assertIsNone(self.store.take_delivery("owner", scoped=True))

    def test_live_context_excludes_obsolete_ready_but_explicit_history_is_marked(self):
        from devtools import duty, processes

        with self.store.connect() as db:
            db.execute("UPDATE members SET harness='codex' WHERE name='coder'")
        generation = self.store.begin_session("coder", os.getpid())
        processes.register(self.store, "coder", generation, os.getpid())
        self.store.session("coder", generation=generation, native_id="fixture-native", state="idle")
        child, parent = API(self.state, "coder"), API(self.state, "xo")
        onboard = child.call("onboard", {})
        ready = child.call("readiness_report", {
            "challenge": onboard["readiness"]["challenge"], "onboarding_revision": onboard["revision"],
        })
        ordinary = self.store.send("coder", "xo", "Ordinary retained report", key="ordinary",
                                   work_order=self.order, job=self.job)
        self.assertIn(ready["receipt"], [row["reference"] for row in parent.call("work_context", {})["incoming"]])
        self.assertTrue(parent.call("work_context", {"reference": ready["receipt"]})["readiness_current"])

        # No watcher tick: every live read must derive freshness itself.
        self.store.session("coder", generation=generation, state="stopped", desired="stopped", pid=None)
        for arguments, field in (({}, "incoming"), ({"section": "messages"}, "messages")):
            visible = [row["reference"] for row in parent.call("work_context", arguments)[field]]
            self.assertNotIn(ready["receipt"], visible)
            self.assertIn("message:" + str(ordinary["id"]), visible)
        historical = parent.call("work_context", {"reference": ready["receipt"]})
        self.assertFalse(historical["readiness_current"])
        self.assertIn("startup checks passed", historical["text"])
        duty.tick(self.store)
        messages = parent.call("work_context", {"section": "messages"})["messages"]
        self.assertTrue(any("unable to report ready" in row["text"] for row in messages))
        self.assertNotIn(ready["receipt"], [row["reference"] for row in messages])


class ControlTests(ControlFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.operator = self.client("owner", operator=True)

    def test_default_surface_supplies_brief_and_rejects_bypasses(self):
        coder = self.client("coder")
        self.assertEqual(
            ["work_context", "work_report", "guidance_request", "readiness_report", "onboard", "skill_read"],
            [r["name"] for r in coder.call("tools/list")["tools"]]
        )
        self.assertNotIn("Implement contract", coder.initialization["instructions"])
        self.assertIn("Call onboard", coder.initialization["instructions"])
        self.assertIn("Implement contract", json.dumps(coder.tool("onboard", {})))
        self.assertNotIn("SECRET SIBLING", coder.initialization["instructions"])
        context = coder.tool("work_context", {})
        self.assertEqual("coder", context["role"])
        self.assertNotIn("id", context["job"])
        for name, args in (
            ("capability_invoke", {"capability": "job_select", "arguments": {"job": self.other}}),
            ("capability_describe", {"capability": "pr_merge"}),
            ("job_select", {"job": self.other}),
            ("member_bind", {"member": "coder", "role": "co", "work_order": self.order}),
            ("work_context", {"job": self.other}),
            ("work_report", {"text": "Override", "to": "owner"}),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                coder.tool(name, args)
        idle = self.client("idle")
        self.assertEqual(["work_context"], [r["name"] for r in idle.call("tools/list")["tools"]])
        self.assertEqual("awaiting_binding", idle.tool("work_context", {})["state"])
        with self.assertRaises(ValueError):
            idle.tool("work_report", {"text": "No scope"})

    def test_operation_references_require_matching_jobs_or_covering_command_scope(self):
        def retain(member):
            scope = Control(self.store, member).snapshot()
            with self.store.connect() as db:
                ident = db.execute(
                    "INSERT INTO operations(member,work_order,job,binding,action,arguments,state,result,created,updated) "
                    "VALUES (?,?,?,?,?,?,'completed',?,'fixture','fixture')",
                    (member, scope["work_order"], scope["job"], json.dumps(scope), "work_check", "{}",
                     json.dumps({"diagnostic": member})),
                ).lastrowid
            return f"operation:{ident}"

        parent, own, sibling = (retain(member) for member in ("xo", "coder", "audit"))
        clients = {member: self.client(member) for member in ("owner", "xo", "coder", "audit", "idle")}

        def read(member, reference, allowed):
            with self.subTest(member=member, reference=reference, allowed=allowed):
                if allowed:
                    page = clients[member].tool("work_context", {"reference": reference})
                    self.assertEqual(reference, json.loads(page["content"])["reference"])
                else:
                    with self.assertRaisesRegex(ValueError, "not available"):
                        clients[member].tool("work_context", {"reference": reference})

        for reference in (parent, own, sibling):
            read("coder", reference, reference == own)
            read("audit", reference, reference == sibling)
            read("owner", reference, True)
            read("xo", reference, True)
        control = Control(self.store, "owner")
        control.bind("audit", "audit", self.order, self.job, "xo")
        read("audit", own, True)  # Another role assigned to the same job can inspect its result.
        control.bind("idle", "research", self.order, reports_to="xo")
        for reference in (parent, own, sibling):
            read("idle", reference, False)  # No job is not command authority.
        control.bind("xo", "xo", self.order, self.job, "owner")
        for reference in (parent, own, sibling):
            read("xo", reference, reference == own)  # A job-scoped XO does not cover its parent or siblings.
        foreign = self.operator.invoke(
            "work_order_create", title="Other order", objective="Separate", done_when="Independent"
        )["work_order"]["id"]
        control.bind("owner", "co", foreign)
        control.bind("xo", "xo", foreign, reports_to="owner")
        for member in ("owner", "xo"):
            for reference in (parent, own, sibling):
                read(member, reference, False)
        read("coder", "operation:999999", False)

    def test_routed_reports_evidence_retries_and_guidance_response_survive_restart(self):
        coder, xo = self.client("coder"), self.client("xo")
        args = {
            "text": "Which interface is intended?",
            "evidence": {"name": "question.md", "content": "Observed interface"},
        }
        question = coder.tool("guidance_request", args)
        self.assertEqual("queued", question["delivery"])
        again = coder.tool("guidance_request", args)
        self.assertEqual(question["reference"], again["reference"])
        self.assertTrue(again["reused"])
        messages = self.store.inbox("xo")["messages"]
        self.assertEqual(1, len(messages))
        self.assertEqual(self.job, messages[0]["job"])
        self.assertEqual("Observed interface", xo.tool("work_context", {"reference": question["evidence"]})["content"])
        answer = xo.tool("work_report", {"text": "Use the existing contract", "responding_to": question["reference"]})
        self.assertEqual("queued", answer["delivery"])
        self.assertTrue(self.store.inbox("xo")["messages"][0]["acknowledged"])
        self.assertEqual(self.job, self.store.inbox("coder")["messages"][0]["job"])
        coder.close()
        restarted = self.client("coder")
        self.assertIn("Use the existing contract", json.dumps(restarted.tool("work_context", {})))
        self.assertEqual(question["reference"], restarted.tool("guidance_request", args)["reference"])

    def test_scope_filters_context_references_and_live_delivery(self):
        audit, coder = self.client("audit"), self.client("coder")
        secret = audit.tool(
            "work_report", {"text": "SECRET NOTE", "evidence": {"name": "private.txt", "content": "SECRET CONTENT"}}
        )
        self.assertNotIn("SECRET", json.dumps(coder.tool("work_context", {"section": "activity"})))
        self.assertNotIn("private.txt", json.dumps(coder.tool("work_context", {"section": "evidence"})))
        for reference in (secret["reference"], secret["evidence"], "artifact:999999"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                coder.tool("work_context", {"reference": reference})
        self.store.send("owner", "coder", "Other job", key="other", work_order=self.order, job=self.other)
        allowed = self.store.send("owner", "coder", "Relevant job", key="mine", work_order=self.order, job=self.job)
        selected = self.store.take_delivery("coder", scoped=True)
        self.assertEqual(allowed["id"], selected["id"])
        self.assertIsNone(self.store.take_delivery("coder", scoped=True))
        coder.tool("work_report", {"text": "Received", "responding_to": f"message:{allowed['id']}"})
        self.assertTrue(self.store.inbox("coder")["messages"][1]["acknowledged"])

    def test_active_connection_loses_tools_after_unbind_and_channel_is_scoped(self):
        self.store.session("coder", state="idle", desired="running")
        coder = self.client("coder", channel=True)
        self.store.send("owner", "coder", "Hidden", key="hidden", work_order=self.order, job=self.other)
        message = self.store.send("owner", "coder", "Visible", key="visible", work_order=self.order, job=self.job)
        packet = until(
            lambda: next((p for p in coder.notifications if p["method"] == "notifications/claude/channel"), None)
        )
        self.assertIn("Visible", packet["params"]["content"])
        self.assertNotIn("Hidden", packet["params"]["content"])
        self.assertEqual(str(message["id"]), packet["params"]["meta"]["message_id"])
        # Ordinary live messages do not repeat the brief.
        self.assertNotIn("Updated brief", packet["params"]["content"])
        self.operator.invoke("member_unbind", member="coder")
        until(lambda: any(p["method"] == "notifications/tools/list_changed" for p in coder.notifications))
        with self.assertRaises(ValueError):
            coder.tool("work_report", {"text": "Stale session"})
        self.assertEqual(["work_context"], [r["name"] for r in coder.call("tools/list")["tools"]])

    def test_command_discussion_and_evidence_do_not_spill_into_specialist_context(self):
        owner, xo, coder = self.client("owner"), self.client("xo"), self.client("coder")
        question = xo.tool(
            "guidance_request",
            {
                "text": "PRIVATE INTENT question",
                "evidence": {"name": "private-intent.md", "content": "PRIVATE INTENT evidence"},
            },
        )
        answer = owner.tool("work_report", {"text": "PRIVATE INTENT answer", "responding_to": question["reference"]})
        for section in ("brief", "activity", "messages", "evidence"):
            self.assertNotIn("PRIVATE INTENT", json.dumps(coder.tool("work_context", {"section": section})))
            self.assertNotIn("private-intent.md", json.dumps(coder.tool("work_context", {"section": section})))
        for reference in (question["reference"], question["evidence"], answer["reference"]):
            with self.assertRaises(ValueError):
                coder.tool("work_context", {"reference": reference})
        self.assertIn("PRIVATE INTENT answer", xo.tool("work_context", {"reference": answer["reference"]})["text"])
        self.assertEqual(
            "PRIVATE INTENT evidence", owner.tool("work_context", {"reference": question["evidence"]})["content"]
        )
        api = API(self.state, "xo")
        with patch.object(api.store, "_send", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                api.call(
                    "work_report",
                    {"text": "Fail safely", "evidence": {"name": "partial-private.md", "content": "PRIVATE INTENT"}},
                )
        self.assertNotIn("partial-private.md", json.dumps(coder.tool("work_context", {"section": "evidence"})))

    def test_report_and_message_commit_atomically_and_concurrent_retries_deduplicate(self):
        api = API(self.state, "coder")
        with patch.object(api.store, "_send", side_effect=OSError("delivery record unavailable")):
            with self.assertRaises(OSError):
                api.call("work_report", {"text": "A finding"})
        self.assertEqual([], api.call("work_context", {"section": "activity"})["activity"])
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(lambda _: api.call("work_report", {"text": "A finding"}), range(4)))
        self.assertEqual(1, len({r["reference"] for r in rows}))
        self.assertEqual(1, len(self.store.inbox("xo")["messages"]))

    def test_large_unicode_evidence_and_retry_after_route_change(self):
        coder = self.client("coder")
        args = {"text": "🦉" * 3000, "evidence": {"name": "🦉" * 240, "content": "🦉" * 6000}}
        saved = coder.tool("work_report", args)
        self.operator.invoke("member_unbind", member="xo")
        retried = coder.tool("work_report", args)
        self.assertTrue(retried["reused"])
        self.assertEqual(saved["reference"], retried["reference"])
        self.assertEqual(1, len(self.store.inbox("xo")["messages"]))

    def test_inflight_report_rejects_revoked_binding_and_next_call_uses_new_scope(self):
        api = API(self.state, "coder")
        entered, resume = threading.Event(), threading.Event()
        original = api.control.report

        def held(**args):
            entered.set()
            if not resume.wait(5):
                raise TimeoutError("Report not released")
            return original(**args)

        with patch.object(api.control, "report", held), ThreadPoolExecutor() as pool:
            pending = pool.submit(api.call, "work_report", {"text": "Original job result"})
            self.assertTrue(entered.wait(3))
            try:
                self.operator.invoke("member_bind", member="coder", role="research", job=self.other, reports_to="xo")
            finally:
                resume.set()
            with self.assertRaisesRegex(ValueError, "changed during this report"):
                pending.result()
        self.assertEqual([], self.store.inbox("xo")["messages"])
        self.assertEqual("research", api.call("work_context", {})["role"])

    def test_binding_validation_and_noop_rebind(self):
        before = Control(self.store, "coder").snapshot()
        again = self.operator.invoke(
            "member_bind",
            member="coder",
            role="coder",
            job=self.job,
            reports_to="xo",
            brief="Use established contracts",
        )
        self.assertEqual(before["revision"], again["revision"])
        for args in (
            dict(member="coder", role="xo", job=self.job, reports_to="audit"),
            dict(member="xo", role="architect", work_order=self.order, reports_to="coder"),
            dict(member="owner", role="co", work_order=self.order, reports_to="xo"),
            dict(member="owner", role="co", job=self.job),
            dict(member="coder", role="invalid", job=self.job, reports_to="xo"),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.operator.invoke("member_bind", **args)
