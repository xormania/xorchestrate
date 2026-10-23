"""Shared completion arrangements without test methods."""

import io
import json
from unittest.mock import patch

from devtools.api import API
from devtools.completion import Completion
from devtools.store import canonical, stamp
from fixtures.workspaces import WorkspaceFixture


def answer(state, questions, model):
    return {"model": model, "answers": {name: {"type": "noul", "noul": 0.97} for name in questions},
            "usage": {"input_tokens": 800, "output_tokens": 40}}


def completion_rule(reject_below, accept_at):
    return {"decisions": {"rules": {"completion": {"noul": {"reject_below": reject_below, "accept_at": accept_at}}}}}


class CompletionFixture:

    def setUp(self):
        WorkspaceFixture.setUp(self)
        self.xo = API(self.state, "xo")
        self.job = self.api.control.snapshot()["job"]
        self.order = self.xo.control.snapshot()["work_order"]
        self.gate = Completion(self.store, "xo")
        self.configure({})
        self.requests = []

        def evaluate(state, questions, model):
            self.requests.append((state, questions, model))
            return answer(state, questions, model)

        provider = patch("devtools.advice.Jev.evaluate", side_effect=evaluate)
        self.provider = provider.start()
        self.addCleanup(provider.stop)

    def configure(self, value, *, roles=None, jobs=None):
        config = json.loads((self.state / "team.json").read_text())
        path = self.state / "configuration.json"
        if value is None:
            config["project"].pop("configuration_file", None)
        else:
            config["project"]["configuration_file"] = path.name
            project = {"features": {"completion": True}, **value}
            path.write_text(json.dumps({"version": 1, "project": project, "roles": roles or {}, "jobs": jobs or {}}))
        (self.state / "team.json").write_text(json.dumps(config))

    def report(self, content="Measured requested behavior: expected Works, observed Works."):
        return self.api.call("work_report", {"kind": "result", "text": "Feature ready", "evidence": {
            "name": "proof.txt", "content": content}})

    def operation(self, passed=True, revision="a" * 40, name="behavior", *, job=None, state="completed", checks=True, uncommitted=None, arguments=None):
        scope = self.api.control.snapshot()
        saved = self.api.artifacts.ingest("measure-" + name + ".txt", io.BytesIO(b"Expected Works; observed Works"), "text/plain", work_order=self.order, job=job or self.job)
        result = {"passed": passed, "revision": revision, "uncommitted": uncommitted or [],
                  "checks": [{"check": name, "exit_code": 0 if passed else 1, "evidence": "artifact:" + str(saved["artifact"]["id"])}] if checks else []}
        with self.store.connect() as db:
            return db.execute("INSERT INTO operations(member,work_order,job,binding,action,arguments,state,result,error,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                "coder", self.order, job or self.job, canonical(scope), "work_check", canonical(arguments or {}), state,
                canonical(result), "Worker disappeared" if state == "uncertain" else None, stamp(), stamp())).lastrowid

    def assignment(self):
        brief = {"name": "Feature implementation", "role": "coder", "objective": "Implement behavior", "done_when": "Behavior demonstrated", "constraints": "Product only"}
        with self.store.connect() as db:
            ident = db.execute("INSERT INTO role_assignments(work_order,job,name,role,brief,details,created_by,created,updated) VALUES (?,?,?,?,?,?,?,?,?)", (
                self.order, self.job, brief["name"], "coder", canonical(brief), "{}", "xo", stamp(), stamp())).lastrowid
            db.execute("UPDATE member_bindings SET assignment_id=? WHERE member='coder'", (ident,))
        return ident

    def capture(self, report, revision, clean=True):
        provenance = {"provider": "work_report", "reference": report["reference"],
                      "source_revision": revision, "workspace_clean": clean, "revision_kind": "report_capture"}
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO artifact_sources(artifact,provider,reference,metadata) VALUES (?,?,?,?)", (
                int(report["evidence"].split(":")[1]), "work_report", report["reference"], canonical(provenance)))
