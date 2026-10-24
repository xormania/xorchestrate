"""Run the retained-context exchange/work suite through active composition.

Native mode launches authenticated harnesses only when explicitly invoked by the
operator. Fixture mode uses a declared synthetic session port and never produces
a native pass. Raw records and markers are private run artifacts.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path

from .capture import (
    candidate_identity,
    canonical,
    digest,
    file_identity,
    now,
    run_command,
    verify_candidate,
    write_json,
)
from .checker import PEERS, VerificationFailure, check_case, native_inventory, verify
from .normalize import normalize

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("build-log.json")


def snapshot(application):
    with application.store.transaction() as tx:
        def records(kind):
            return [{**deepcopy(r.data), "id": r.key, "version": r.version} for r in tx.list(kind)]
        return {"sessions": records("session"), "messages": records("message"), "attempts": records("delivery_attempt"),
                "observations": records("native_observation"), "evidence": records("evidence"),
                "operations": records("operation"), "receipts": records("delivery_receipt"),
                "native_calls": records("native_call")}


def protocol_config(root, state):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = {"sessions": {}}
    for peer in ("codex", "grok"):
        config["sessions"][peer] = {"adapter": peer, "options": {
            "command": [sys.executable, str(root / "tests/hexagonal/fixtures/native_progress_peer.py"), peer],
            "application_tools": True, "resume_supported": True, "timeout": 30, "turn_timeout": 300,
            "env": {"PEER_STATE_FILE": str(state / (peer + ".json"))}}}
    config["sessions"]["claude"] = {"adapter": "claude", "options": {
        "command": [sys.executable, str(root / "tests/hexagonal/fixtures/claude_native.py")],
        "application_tools": True, "terminal": "discard", "startup_timeout": 30, "timeout": 30,
        "env": {"CLAUDE_STATE_DIR": str(state / "claude")}}}
    return config


def prepare_config(config, base, directory, mode, fixture=FIXTURE, candidate_root=ROOT):
    config = deepcopy(config)
    if mode == "protocol-fixture" and not config:
        config = protocol_config(candidate_root, directory / "peer-state")
    for category in ("sessions", "providers"):
        for spec in config.get(category, {}).values():
            if spec.get("options", {}).get("cwd") is not None:
                spec["options"]["cwd"] = str((base / spec["options"]["cwd"]).resolve())
    if mode == "fixture":
        config["sessions"] = {peer: {"adapter": "scripts.verification.fixture:SyntheticSessionDriver",
            "options": {"peer": peer, "application_tools": True}} for peer in PEERS}
    for peer in PEERS:
        if peer not in config.get("sessions", {}):
            raise ValueError("Configure a session driver named " + peer)
        if config["sessions"][peer].get("options", {}).get("application_tools") is not True:
            raise ValueError("Native application_tools must be enabled for " + peer)
    config["store"] = {"adapter": "sqlite", "options": {"path": str(directory / "state.sqlite3")}}
    config.setdefault("providers", {})["verification"] = {
        "adapter": "scripts.verification.fixture:VerificationProvider", "options": {"fixture": str(fixture), "hold_timeout": 300}}
    # Scheduling is exercised explicitly through the public application action.
    config["delivery_scheduler"] = {"adapter": "manual"}
    return config


class ComposedSuite:
    def __init__(self, runtime, directory, mode, criteria, bindings, fixture, timeout=180, config=None, base=None):
        self.runtime, self.application = runtime, runtime.application
        self.directory, self.mode, self.timeout = Path(directory), mode, timeout
        self.criteria, self.bindings, self.fixture = criteria, bindings, fixture
        self.provider = self.application.providers["verification"]
        self.run_id = uuid.uuid4().hex
        self.memories = {peer: secrets.token_hex(16) for peer in PEERS}
        self.nonces = {case["id"]: secrets.token_hex(12) for case in criteria["required_cases"]
                       if case["kind"] in {"exchange", "busy", "work_exchange"}}
        self.sessions = {}
        self.executed = []
        self.phase = "setup"
        self.failures = []
        self.lifecycle_events = []
        self.work_id = None
        self.started = now()
        self.control_at = {}
        self.config, self.base = config or {}, Path(base or ROOT)
        self.lifecycle_records = []
        self.tool_versions = {}
        self.product_modules = {}

    def call(self, action, arguments):
        from xorchestrate.model import Conflict
        # Autonomous callbacks may change the authorization snapshot between
        # reading and reserving an effect. Retry only the application's explicit
        # pre-effect signal, with the same request and idempotency key. Unknown
        # effects and unqualified conflicts must remain visible to the operator.
        retryable = {"create_work", "add_actor", "start_session", "send_session",
                     "dispatch_deliveries", "verify_exchange"}
        for attempt in range(8):
            try:
                return self.application.call(action, arguments, actor="owner")
            except Conflict as error:
                if action not in retryable or not error.details.get("retry_safe") or attempt == 7:
                    raise
                time.sleep(.01)

    def raw_run(self):
        raw = snapshot(self.application)
        provider = self.provider.snapshot()
        result = {"mode": self.mode, "run_id": self.run_id, "started_at": self.started, "cutoff": now(),
                  "bindings": self.bindings, "raw_records": raw, "provider_events": provider,
                  "sessions": self.sessions, "memories": self.memories, "nonces": self.nonces,
                  "executed_cases": list(self.executed), "discovered_cases": [c["id"] for c in native_inventory()],
                  "phase": self.phase, "failures": deepcopy(self.failures)}
        result["tool_versions"] = deepcopy(self.tool_versions)
        result["product_modules"] = deepcopy(self.product_modules)
        result["cleanup"] = {"status": "pending"}
        result["events"] = normalize(raw, self.sessions, provider, self.mode)
        result["lifecycle_records"] = deepcopy(self.lifecycle_records)
        result["lifecycle_events"] = deepcopy(self.lifecycle_events)
        for event in self.lifecycle_events:
            result["events"].append({**deepcopy(event), "seq": len(result["events"]) + 1})
        return result

    def save(self):
        run = self.raw_run()
        write_json(self.directory / "run.json", run)
        write_json(self.directory / "report.json", verify(run, self.criteria, self.bindings, self.fixture))
        return run

    def wait(self, predicate, code):
        deadline = time.monotonic() + self.timeout
        diagnostic = code
        while time.monotonic() < deadline:
            self.call("dispatch_deliveries", {})
            run = self.raw_run()
            try:
                result = predicate(run)
                if result:
                    return result
            except VerificationFailure as exc:
                diagnostic = exc.assertion
            # Callback progress is autonomous; this loop does not call poll_session.
            time.sleep(.025)
        self.save()
        raise VerificationFailure(diagnostic)

    def instruction(self, peer, command, explanation):
        self.control_at[peer] = now()
        self.call("send_session", {"session_id": self.sessions[peer]["session_id"],
            "message": {"text": explanation + "\nVERIFICATION_COMMAND=" + json.dumps(command, separators=(",", ":"))},
            "idempotency_key": uuid.uuid4().hex})

    def start(self):
        self.work_id = self.call("create_work", {"goal": "Verify persistent three-harness communication and separate work correctness."})["work"]["id"]
        for peer in PEERS:
            self.call("add_actor", {"actor_id": peer})
            session = self.call("start_session", {"driver": peer, "specification": {}, "actor_id": peer,
                                "work_id": self.work_id, "idempotency_key": "startup-" + peer})["session"]
            backend = session["backend"]
            self.sessions[peer] = {"session_id": session["id"], "native_id": backend["native_id"],
                                   "incarnation": backend["incarnation"]}
        for peer in PEERS:
            self.phase = "startup-" + peer
            self.executed.append(self.phase)
            instructions = (
                "You are peer " + peer + " in a persistent communication acceptance run. Keep PRIVATE_MEMORY privately in this conversation. "
                "When a delivered message has body.verification.kind=challenge, call send_message to its sender using destination.actor, "
                "body.verification with the same run_id/case_id/nonce, kind=reply and memory=your PRIVATE_MEMORY. "
                "When it has kind=reply, call record_result with result.verification={run_id,case_id,nonce,memory,phase:'received',peer:message.sender}. "
                "Never echo these messages as prose or only acknowledge an inbox item. Work_id is supplied by the application. "
                "For startup, retain the following memory and call record_result with result.verification={run_id,phase:'ready',memory}. "
                "PRIVATE_MEMORY=" + self.memories[peer] + ". End the turn after that callback.")
            self.instruction(peer, {"kind": "startup", "run_id": self.run_id, "memory": self.memories[peer]}, instructions)
        for peer in PEERS:
            case = next(c for c in self.criteria["required_cases"] if c["id"] == "startup-" + peer)
            self.wait(lambda run, case=case: check_case(case, run, self.fixture), "startup_missing")
        self.settled()

    def settled(self):
        def idle(run):
            for peer in PEERS:
                observed = [e for e in run["events"] if e["peer"] == peer and e["event"] == "activity"]
                if (not observed or observed[-1].get("state") != "idle" or
                        observed[-1]["at"] < self.control_at.get(peer, "")):
                    return False
            return True
        return self.wait(idle, "native_completion_missing")

    def send_case(self, case):
        source, target = case["source"], case["target"]
        payload = {"run_id": self.run_id, "case_id": case["id"], "kind": "challenge",
                   "nonce": self.nonces[case["id"]], "memory": ""}
        self.instruction(source, {"kind": "send", "target": target, "payload": payload},
            "Call send_message exactly once with destination.actor=" + json.dumps(target) + " and body.verification=" + json.dumps(payload) +
            ". End this turn; handle the eventual reply using the retained instructions.")

    def exchange(self, case):
        self.phase = case["id"]
        self.executed.append(case["id"])
        self.settled()
        busy = case["kind"] == "busy"
        if busy:
            self.instruction(case["target"], {"kind": "hold", "case_id": case["id"]},
                'Call invoke with capability="verification/hold", arguments=' + json.dumps({"case_id": case["id"]}) +
                ". The controller releases the hold after native delivery submission.")
            self.wait(lambda run: any(e["event"] == "hold_started" and e.get("case_id") == case["id"]
                                      for e in run["events"]), "outstanding_tool_hold_missing")
        try:
            self.send_case(case)
            if busy:
                def submitted(run):
                    ids = {e["message"]["id"] for e in run["events"] if e["event"] == "queued" and
                           e["message"].get("case_id") == case["id"] and e["message"].get("kind") == "challenge"}
                    return any(e["event"] == "submitted" and e.get("message_id") in ids for e in run["events"])
                self.wait(submitted, "busy_native_submission_missing")
        finally:
            if busy:
                self.provider.release_hold(case["target"], case["id"])
        self.wait(lambda run: check_case(case, run, self.fixture), "exchange_incomplete")
        self.verify_product_receipt(case)

    def verify_product_receipt(self, case):
        from xorchestrate.model import Conflict
        messages = snapshot(self.application)["messages"]
        challenge = next(m for m in messages if m.get("body", {}).get("verification", {}).get("case_id") == case["id"]
                         and m["body"]["verification"].get("kind") == "challenge")
        arguments = {"message_id": challenge["id"], "recipient": case["target"],
            "expectation": {"run_id": self.run_id, "case_id": case["id"], "nonce": self.nonces[case["id"]],
                            "memory": self.memories[case["target"]], "sender": case["source"], "recipient": case["target"]}}
        def decide(run):
            try:
                return self.call("verify_exchange", arguments)
            except Conflict as exc:
                # This action has no external effect, and its optimistic check
                # precedes receipt creation. Re-read and reassess current facts.
                if str(exc) == "Exchange evidence changed during verification.":
                    return False
                raise
        result = self.wait(decide, "exchange_evidence_not_stable")
        if not result["receipt"]["decision"]["verified"]:
            raise VerificationFailure("active_receipt_policy:" + ",".join(result["receipt"]["decision"]["diagnostics"]))

    def work(self):
        work_exchanges = [c for c in self.criteria["required_cases"] if c["kind"] == "work_exchange"]
        for batch in range(3):
            self.phase = "work-batch-" + str(batch)
            if batch:
                self.provider.release_batch(batch)
            for peer in PEERS:
                self.executed.append(f"work-batch-{peer}-{batch}")
                self.instruction(peer, {"kind": "work", "batch": batch},
                    "Use invoke capability verification/work_next with empty arguments. For each job consider only its highest attempt. "
                    "Report jobs whose latest status is FAIL, and their total failed_tests using verification/work_submit "
                    "arguments {batch,failed_jobs:[sorted IDs],failed_tests:sum}. Do exactly one batch, then end the turn. "
                    "Do not read fixture files. Handle incoming peer messages while later batches remain unfinished.")
            for peer in PEERS:
                # Communication continues to be measured even if a work answer
                # is wrong; retain correctness as a separate final verdict.
                self.wait(lambda run, peer=peer, batch=batch: any(e["event"] == "work_submitted" and
                          e["peer"] == peer and e["batch"] == batch for e in run["events"]), "work_batch_not_exercised")
            self.settled()
            if batch == 0:
                for peer in PEERS:
                    messages = []
                    for case in [c for c in work_exchanges if c["source"] == peer]:
                        self.executed.append(case["id"])
                        messages.append({"target": case["target"], "payload": {"run_id": self.run_id, "case_id": case["id"],
                            "kind": "challenge", "nonce": self.nonces[case["id"]], "memory": ""}})
                    self.instruction(peer, {"kind": "send_many", "messages": messages},
                        "Call send_message once for each item below using destination.actor=item.target, body.verification=item.payload. "
                        "Then end the turn and handle replies normally. Items: " + json.dumps(messages))
                def receipts(run):
                    for case in work_exchanges:
                        expected = {"phase": "received", "case_id": case["id"], "nonce": self.nonces[case["id"]],
                                    "memory": self.memories[case["target"]], "peer": case["target"]}
                        if not any(e["event"] == "report" and e["peer"] == case["source"] and e["report"] == expected for e in run["events"]):
                            return False
                    return True
                self.wait(receipts, "unfinished_work_exchange_receipt_missing")
                self.settled()
        for case in work_exchanges:
            self.wait(lambda run, case=case: check_case(case, run, self.fixture), "unfinished_work_exchange_incomplete")
            self.verify_product_receipt(case)

    def run(self):
        self.start()
        for case in self.criteria["required_cases"]:
            if case["kind"] in {"exchange", "busy"}:
                self.exchange(case)
        self.work()
        self.phase = "completion-and-native-audit"
        for case in self.criteria["required_cases"]:
            if case["kind"] in {"completion", "audit"}:
                self.executed.append(case["id"])
                self.wait(lambda run, case=case: check_case(case, run, self.fixture), "final_native_evidence_missing")
        from .lifecycle import run_lifecycle
        run_lifecycle(self, self.config, self.base)
        return self.save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "protocol-fixture", "native"), required=True)
    parser.add_argument("--candidate", type=Path, default=ROOT,
                        help="Candidate checkout; checker code can run from a separate reviewed checkout")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--criteria", type=Path, required=True)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    candidate_root = args.candidate.resolve()
    # Product imports happen only after selecting the candidate. The already
    # loaded scripts.verification package remains the separately selected checker.
    sys.path.insert(0, str(candidate_root))
    import xorchestrate.composition as selected_composition
    from xorchestrate.composition import build, load_config
    imported = Path(selected_composition.__file__).resolve()
    if candidate_root not in imported.parents:
        parser.error("Loaded product module is outside the selected candidate: " + str(imported))
    directory = args.out.resolve()
    if directory == candidate_root or candidate_root in directory.parents:
        parser.error("Run output must be outside the measured candidate")
    for selected in (args.criteria.resolve(), args.brief.resolve()):
        if selected == candidate_root or candidate_root in selected.parents:
            parser.error("Owner brief and selected criteria must be preserved outside the candidate")
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    criteria = json.loads(args.criteria.read_text())
    if criteria.get("owner_basis_sha256") != digest(args.brief.read_bytes()):
        parser.error("Criteria are not bound to the selected owner brief")
    fixture = json.loads(args.fixture.read_text())
    config, base = load_config(args.config)
    config = prepare_config(config, base, directory, args.mode, args.fixture.resolve(), candidate_root)
    candidate = candidate_identity(candidate_root)
    checker_files = {str(p.relative_to(ROOT)): file_identity(p) for p in sorted(Path(__file__).parent.glob("*.py"))}
    bindings = {"candidate_sha256": candidate["content_sha256"], "criteria_sha256": digest(canonical(criteria)),
                "checker_sha256": digest(canonical(checker_files)), "fixture_sha256": digest(canonical(fixture)),
                "configuration_sha256": digest(canonical(config)), "owner_basis_sha256": digest(args.brief.read_bytes())}
    for name, value in (("candidate.json", candidate), ("bindings.json", bindings), ("criteria.json", criteria),
                        ("fixture.json", fixture), ("configuration.json", config), ("checker.json", checker_files)):
        write_json(directory / name, value)
    runtime = None
    suite = None
    try:
        runtime = build(config, base)
        suite = ComposedSuite(runtime, directory, args.mode, criteria, bindings, fixture, args.timeout, config, base)
        suite.product_modules = {name: {"path": module.__file__, **file_identity(module.__file__)}
            for name, module in list(sys.modules.items()) if name.startswith("xorchestrate.") and getattr(module, "__file__", None)}
        if any(candidate_root not in Path(item["path"]).resolve().parents for item in suite.product_modules.values()):
            raise VerificationFailure("product_module_outside_selected_candidate")
        for peer in PEERS:
            command = config["sessions"][peer]["options"].get("command")
            suite.tool_versions[peer] = (run_command([command[0], "--version"], base, directory / "versions" / peer, timeout=15)
                if command else {"status": "not_applicable", "basis": "synthetic_session_fixture"})
        suite.run()
    except BaseException as exc:
        if suite:
            suite.failures.append({"phase": suite.phase, "type": type(exc).__name__, "diagnostic": str(exc)})
            suite.save()
        else:
            write_json(directory / "setup-failure.json", {"type": type(exc).__name__, "diagnostic": str(exc)})
    finally:
        cleanup = {"status": "passed", "finished_at": now()}
        if runtime:
            # Release owned holds before closing drivers; never strand a native
            # callback behind cleanup of its provider.
            try:
                runtime.application.providers["verification"].close()
                runtime.close()
            except BaseException as exc:
                cleanup = {"status": "failed", "type": type(exc).__name__, "diagnostic": str(exc), "finished_at": now()}
        after = verify_candidate(candidate_root, candidate)
        write_json(directory / "candidate-after.json", after)
        write_json(directory / "cleanup.json", cleanup)
    report_file = directory / "report.json"
    if not report_file.exists():
        return 1
    run_file = directory / "run.json"
    run = json.loads(run_file.read_text())
    run.update(cleanup=cleanup, candidate_integrity=after)
    write_json(run_file, run)
    report = verify(run, criteria, bindings, fixture)
    if not after["passed"]:
        report.update(status="invalid_candidate", candidate_integrity=after)
    write_json(report_file, report)
    print(json.dumps({"report": str(report_file), "status": report["status"], "native_acceptance": report["native_acceptance"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
