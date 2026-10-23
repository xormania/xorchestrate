import base64
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from support import Client, initialize

from devtools.api import API
from devtools.artifacts import Artifacts
from devtools.store import SCHEMA, Store


class WorkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.directory = self.root / "state"
        initialize(self.directory, self.workspace, ["alpha:external", "beta:external"])
        self.store = Store(self.directory)

    def client(self, member="alpha"):
        client = Client(self.directory, member, operator=True)
        self.addCleanup(client.close)
        return client

    def order(self, client, title="Artifact support", key=None):
        args = dict(
            title=title,
            objective="Keep work and its evidence together",
            done_when="The MCP use case passes",
            constraints="Do not change Symfony application code",
        )
        if key is not None:
            args["key"] = key
        return client.invoke("work_order_create", **args)["work_order"]["id"]

    def job(self, client, **args):
        return client.invoke(
            "job_create",
            title="Save evidence",
            objective="Publish a managed snapshot",
            done_when="The PR passes its storage tests",
            **args,
        )["job"]["id"]

    def test_mcp_work_order_jobs_prs_evidence_and_live_activity_survive_restart(self):
        alpha, beta = self.client(), self.client("beta")
        self.assertEqual(3, len(alpha.call("tools/list")["tools"]))
        order = self.order(alpha, key="first")
        self.assertEqual(order, self.order(alpha, key="first"))
        with self.assertRaisesRegex(ValueError, "different work"):
            self.order(alpha, title="Other work", key="first")
        job = self.job(alpha, repository="uscient/agentscient", branch="artifact-save", key="save")
        peer_job = self.job(beta, work_order=order)
        self.assertNotEqual(job, peer_job)
        alpha.invoke("job_update", status="in_progress", pull_request="https://github.com/uscient/agentscient/pull/999")
        message = alpha.invoke("message_send", to="beta", body="The evidence is ready for review", key="evidence")
        self.assertEqual((order, job), (message["work_order"], message["job"]))
        reply = beta.invoke("message_send", to="alpha", body="Looking now", key="reply", reply_to=message["id"])
        self.assertEqual((order, job), (reply["work_order"], reply["job"]))
        beta.invoke("message_ack", message=message["id"])
        ack = alpha.invoke("telemetry_events", work_order=order, job=job, kind="message_acknowledged")["events"]
        self.assertEqual(1, len(ack))
        self.assertEqual("beta", ack[0]["actor"])
        artifact = alpha.invoke("artifact_save", name="results.md", content="# Evidence\nTests passed")
        self.assertEqual((order, job), (artifact["work_order"], artifact["job"]))
        self.assertFalse({"location", "sha256"} & artifact["artifact"].keys())
        shared = alpha.invoke("artifact_save", name="scope.md", content="Shared scope", work_order=order)
        self.assertIsNone(shared["job"])
        self.assertEqual(1, len(alpha.invoke("artifact_list", job=job)["artifacts"]))
        self.assertEqual(2, len(alpha.invoke("artifact_list", work_order=order)["artifacts"]))
        current = alpha.invoke("work_order_get")
        self.assertEqual(2, len(current["jobs"]))
        self.assertEqual(2, len(current["links"]))
        self.assertTrue(current["jobs"][0]["pull_request"].endswith("/999"))
        self.assertEqual({"alpha", "beta"}, set(current["members"]))
        self.assertEqual(2, current["message_count"])
        alpha.invoke("job_update", status="done")
        self.assertEqual("open", alpha.invoke("work_order_get")["work_order"]["status"])
        alpha.invoke("job_update", status="open")  # Descriptive status, no transition/review gate.
        self.store.session("alpha", native_id="fixture-conversation")
        self.store.event("alpha", "native_usage", observation={"inputTokens": 123})
        usage = alpha.invoke("telemetry_usage", job=job)["events"][0]
        self.assertEqual(order, usage["work_order"])
        self.assertEqual("fixture-conversation", usage["data"]["native_id"])
        generation = self.store.begin_session("alpha", os.getpid())
        starting = alpha.invoke("telemetry_events", job=job, kind="session_starting")["events"][0]
        self.assertEqual(generation, starting["data"]["generation"])
        self.assertNotIn("native_id", starting["data"])
        self.assertIsNone(self.store.member("alpha")["native_id"])
        alpha.close()
        restarted = self.client()
        recovered = restarted.invoke("job_get")
        self.assertEqual(job, recovered["job"]["id"])
        self.assertEqual("Do not change Symfony application code", recovered["work_order"]["constraints"])
        self.assertEqual(
            "# Evidence\nTests passed",
            restarted.invoke("artifact_read", artifact=artifact["artifact"]["id"])["content"],
        )
        self.assertEqual(
            [reply["id"]], [m["id"] for m in restarted.invoke("message_inbox", work_order=order, job=job)["messages"]]
        )
        restarted.invoke("job_select")
        next_message = restarted.invoke("message_send", to="beta", body="Overall progress", key="overall")
        self.assertEqual((order, None), (next_message["work_order"], next_message["job"]))
        restarted.invoke("work_order_select")
        unbound = restarted.invoke("message_send", to="beta", body="Hello", key="unbound")
        self.assertIsNone(unbound["work_order"])

    def test_parent_consistency_references_and_pagination(self):
        client = self.client()
        first = self.order(client)
        job = self.job(client)
        second = self.order(client, title="Second work order")
        with self.assertRaisesRegex(ValueError, "different work order"):
            client.invoke("artifact_save", name="wrong.txt", content="wrong", work_order=second, job=job)
        with self.assertRaisesRegex(ValueError, "repository"):
            client.invoke("job_update", job=job, branch="feature")
        link = client.invoke("work_link", job=job, kind="commit", reference="a" * 40, repository="uscient/agentscient")[
            "link"
        ]
        self.assertEqual((first, job), (link["work_order"], link["job"]))
        again = client.invoke(
            "work_link", job=job, kind="commit", reference="a" * 40, repository="uscient/agentscient"
        )["link"]
        self.assertEqual(link["id"], again["id"])
        artifact = client.invoke("artifact_save", name="reusable.md", content="Reusable", work_order=first)["artifact"][
            "id"
        ]
        client.invoke("work_link", kind="artifact", reference=str(artifact))
        self.assertEqual(2, len(client.invoke("artifact_read", artifact=artifact)["work"]))
        first_page = client.invoke("work_order_list", limit=1)
        self.assertEqual(first, first_page["work_orders"][0]["id"])
        self.assertEqual(
            second, client.invoke("work_order_list", after=first_page["next_after"])["work_orders"][0]["id"]
        )
        self.assertEqual([job], [j["id"] for j in client.invoke("job_list", work_order=first)["jobs"]])
        self.assertTrue(client.invoke("work_unlink", job=job, link=link["id"])["removed"])
        self.assertFalse(client.invoke("work_unlink", job=job, link=link["id"])["removed"])

    def test_file_snapshots_binary_text_and_bounded_reads(self):
        client = self.client()
        self.order(client)
        for name, payload in (
            ("report.md", ("Measured 🐍 result\n" * 16000).encode()),
            ("image.bin", bytes(range(256)) * 1000),
        ):
            with self.subTest(name=name):
                source = self.workspace / name
                source.write_bytes(payload)
                saved = client.invoke("artifact_save", name=name, source=name)["artifact"]
                source.unlink()
                offset, pieces = 0, []
                while offset is not None:
                    page = client.invoke("artifact_read", artifact=saved["id"], offset=offset, limit=32768)
                    pieces.append(
                        page["content"].encode() if page["encoding"] == "utf8" else base64.b64decode(page["content"])
                    )
                    offset = page["next_offset"]
                self.assertEqual(payload, b"".join(pieces))
        inline = client.invoke(
            "artifact_save", name="tiny.bin", content=base64.b64encode(b"\x00\xff").decode(), encoding="base64"
        )
        self.assertEqual("AP8=", client.invoke("artifact_read", artifact=inline["artifact"]["id"])["content"])
        # A worst-case escaped inline value still fits the MCP request frame.
        unicode = "🦉" * 8000
        saved = client.invoke("artifact_save", name="unicode.txt", content=unicode)
        self.assertEqual(len(unicode.encode()), saved["artifact"]["size"])

    def test_failed_publication_retry_integrity_and_source_boundary(self):
        service = Artifacts(self.store, "alpha")
        with patch.object(self.store, "_event", side_effect=OSError("interrupted database write")):
            with self.assertRaises(OSError):
                service.save(name="../../evidence.txt", content="complete")
        self.assertEqual([], service.list()["artifacts"])
        self.assertEqual([], list(service.root.glob(".save-*")))
        saved = service.save(name="../../evidence.txt", content="complete")["artifact"]
        retried = service.save(name="../../evidence.txt", content="complete")
        self.assertEqual(saved["id"], retried["artifact"]["id"])
        self.assertTrue(retried["reused"])
        changed = service.save(name="../../evidence.txt", content="new result")["artifact"]
        self.assertNotEqual(saved["id"], changed["id"])
        self.assertEqual("complete", service.read(saved["id"])["content"])
        self.assertFalse((self.root / "evidence.txt").exists())
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        (self.workspace / "escape").symlink_to(outside)
        os.mkfifo(self.workspace / "pipe")
        for source in ("../outside.txt", str(outside), "escape", "pipe"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                service.save(name="unsafe", source=source)
        with self.assertRaisesRegex(ValueError, "base64"):
            service.save(name="invalid", content="not base64!", encoding="base64")
        with self.store.connect() as db:
            location = db.execute("SELECT location FROM artifacts WHERE id=?", (saved["id"],)).fetchone()[0]
        payload = self.directory / location
        payload.write_text("damaged")
        with self.assertRaisesRegex(ValueError, "integrity"):
            service.read(saved["id"])
        payload.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            service.read(saved["id"])
        service.save(name="../../evidence.txt", content="complete")
        self.assertEqual("complete", service.read(saved["id"])["content"])

    def test_inflight_tool_keeps_context_during_concurrent_selection(self):
        api = API(self.directory, "alpha", operator=True)

        def invoke(capability, **args):
            return api.call("capability_invoke", {"capability": capability, "arguments": args})

        first = invoke("work_order_create", title="First", objective="One", done_when="Done")["work_order"]["id"]
        second = invoke("work_order_create", title="Second", objective="Two", done_when="Done")["work_order"]["id"]
        invoke("work_order_select", work_order=first)
        entered, resume = threading.Event(), threading.Event()
        save = api.artifacts.save

        def held(**args):
            entered.set()
            if not resume.wait(3):
                raise TimeoutError("Selection test did not release the save")
            return save(**args)

        with patch.object(api.artifacts, "save", held), ThreadPoolExecutor() as pool:
            pending = pool.submit(invoke, "artifact_save", name="concurrent.txt", content="Original work")
            self.assertTrue(entered.wait(3))
            try:
                invoke("work_order_select", work_order=second)
            finally:
                resume.set()
            self.assertEqual(first, pending.result()["work_order"])
        event = invoke("telemetry_events", kind="artifact_saved")["events"][0]
        self.assertEqual(first, event["work_order"])
        self.assertEqual(second, invoke("work_order_get")["work_order"]["id"])

    def test_existing_database_upgrades_without_losing_live_records(self):
        directory = self.root / "old-state"
        directory.mkdir()
        with sqlite3.connect(directory / "live.sqlite3") as db:
            db.executescript(SCHEMA)
            for member in ("alpha", "beta"):
                db.execute(
                    "INSERT INTO members(name,harness,workspace,updated) VALUES (?,'external',?,'before')",
                    (member, str(self.workspace)),
                )
            db.execute(
                "INSERT INTO messages(id,sender,recipient,body,request_key,created) VALUES (7,'alpha','beta','Keep me','old','before')"
            )
            db.execute(
                "INSERT INTO events(id,at,actor,kind,data) VALUES (9,'before','alpha','native_usage',?)",
                (json.dumps({"observation": {"inputTokens": 10}}),),
            )
        api = API(directory, "alpha", operator=True)
        self.assertEqual(7, api.store.inbox("beta")["messages"][0]["id"])
        event = api.store.events()["events"][0]
        self.assertEqual(9, event["id"])
        self.assertIsNone(event["work_order"])
        created = api.call(
            "capability_invoke",
            {
                "capability": "work_order_create",
                "arguments": {"title": "New", "objective": "Track work", "done_when": "Ready"},
            },
        )
        self.assertEqual(created["work_order"]["id"], Store(directory).member("alpha")["selected_work_order"])


if __name__ == "__main__":
    unittest.main()
