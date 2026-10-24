"""The same fifteen lifecycle procedures execute against active composition."""
import json
import uuid
from types import SimpleNamespace

from scripts.verification.checker import PEERS
from scripts.verification.lifecycle import _probe, run_lifecycle
from xorchestrate.composition import build


def test_all_lifecycle_cases_use_real_application_and_external_processes(tmp_path):
    config = {"store": {"adapter": "sqlite", "options": {"path": str(tmp_path / "owned.sqlite3")}},
        "sessions": {peer: {"adapter": "scripts.verification.fixture:SyntheticSessionDriver",
            "options": {"peer": peer, "application_tools": True}} for peer in PEERS}}
    with build(config) as runtime:
        app = runtime.application
        suite = SimpleNamespace(application=app, directory=tmp_path, mode="fixture", timeout=10,
            run_id=uuid.uuid4().hex, memories={peer: uuid.uuid4().hex for peer in PEERS}, sessions={},
            lifecycle_events=[], executed=[])
        suite.work_id = app.call("create_work", {"goal": "Verify lifecycle through the active application"})["work"]["id"]
        for peer in PEERS:
            app.call("add_actor", {"actor_id": peer})
            session = app.call("start_session", {"driver": peer, "specification": {}, "actor_id": peer,
                "work_id": suite.work_id, "idempotency_key": uuid.uuid4().hex})["session"]
            suite.sessions[peer] = {"session_id": session["id"], "native_id": session["backend"]["native_id"],
                "incarnation": session["backend"]["incarnation"]}
            _probe(suite, app, peer, session, "initialize-" + peer, initialize=True)
        from pathlib import Path
        run_lifecycle(suite, config, Path.cwd())
        expected = {f"lifecycle-{peer}-{operation}" for peer in PEERS for operation in ("resume", "attach", "detach", "reconnect", "replace")}
        assert set(suite.executed) == expected
        assert len(suite.lifecycle_events) == 15
        for peer in PEERS:
            directory = tmp_path / "lifecycle" / peer
            cleanup = json.loads((directory / "cleanup.json").read_text())
            assert cleanup["host_returncode"] == 0
            assert cleanup["endpoint_removed"] is True
            saved = next(row["records"] for row in suite.lifecycle_records if row["peer"] == peer)
            operations = {row["id"]: row for row in saved["operation"]}
            observations = {row["id"]: row for row in saved["native_observation"]}
            evidence = {row["id"]: row for row in saved["evidence"]}
            for event in suite.lifecycle_events:
                if event["peer"] != peer or event["operation"] == "resume":
                    continue
                assert operations[event["operation_id"]]["status"] == "completed"
                if event["operation"] == "detach":
                    assert event["process_alive_after"] is True
                    continue
                retained = event["retained_observation"]
                native = observations[retained["native_observation_id"]]
                callback = observations[retained["callback_observation_id"]]
                assert native["observation"]["call_id"] == callback["observation"]["call_id"] == retained["call_id"]
                assert native["session_id"] == callback["session_id"] == retained["session_id"]
                assert evidence[retained["evidence_id"]]["result"]["verification"]["memory"] == suite.memories[peer]
