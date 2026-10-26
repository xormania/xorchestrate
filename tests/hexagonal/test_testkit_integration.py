"""Faults at composed runner boundaries must not strand owned resources."""

import io
import json
from types import SimpleNamespace

import pytest

from scripts.verification import lifecycle, multi_harness
from scripts.verification.capture import digest
from xorchestrate import composition


def test_suite_setup_and_provider_failure_still_close_runtime(tmp_path, monkeypatch):
    calls = []

    def provider_close():
        calls.append("provider")
        raise RuntimeError("provider-close-fault")

    runtime = SimpleNamespace(
        application=SimpleNamespace(providers={"verification": SimpleNamespace(close=provider_close)}),
        close=lambda: calls.append("runtime"),
    )

    def fail_setup(*args, **kwargs):
        raise RuntimeError("suite-setup-fault")

    monkeypatch.setattr(composition, "build", lambda *args: runtime)
    monkeypatch.setattr(multi_harness, "ComposedSuite", fail_setup)
    monkeypatch.setattr(multi_harness, "candidate_identity", lambda root: {"content_sha256": "candidate"})
    monkeypatch.setattr(multi_harness, "verify_candidate", lambda *args: {"passed": True})
    brief = tmp_path / "brief.md"
    brief.write_text("Exercise runner cleanup, including partial setup.\n")
    criteria = tmp_path / "criteria.json"
    criteria.write_text(json.dumps({"owner_basis_sha256": digest(brief.read_bytes())}))
    output = tmp_path / "run"

    assert multi_harness.main([
        "--mode", "fixture", "--brief", str(brief), "--criteria", str(criteria), "--out", str(output),
    ]) == 1

    assert calls == ["provider", "runtime"]
    cleanup = json.loads((output / "cleanup.json").read_text())
    assert cleanup["status"] == "failed"
    assert [error["name"] for error in cleanup["errors"]] == ["verification_holds"]
    assert cleanup["errors"][0]["diagnostic"] == "provider-close-fault"
    assert json.loads((output / "setup-failure.json").read_text())["diagnostic"] == "suite-setup-fault"


@pytest.mark.parametrize("faults", [{"runtime"}, {"records"}, {"runtime", "records"}])
def test_lifecycle_cleanup_drains_host_and_streams_after_capture_or_runtime_failure(tmp_path, monkeypatch, faults):
    calls = []
    outer_application, inner_application = object(), object()
    session = {"id": "session", "backend": {"native_id": "native", "incarnation": "connection"}}
    suite = SimpleNamespace(application=outer_application, sessions={"codex": {"session_id": "session"}},
                            work_id="work", directory=tmp_path, timeout=1, lifecycle_events=[], executed=[])

    class Process:
        pid = 123
        returncode = None

        def __init__(self):
            self.stdout = io.StringIO('{"ready":true}\n')

        def poll(self):
            return self.returncode

        def terminate(self):
            calls.append("host-terminate")
            self.returncode = 0

        def wait(self, timeout):
            calls.append("host-wait")
            return self.returncode

    process = Process()
    streams = []

    def launch(*args, **kwargs):
        streams.append(kwargs["stderr"])
        return process

    def records(application):
        if application is inner_application:
            calls.append("capture")
            if "records" in faults:
                raise RuntimeError("capture-fault")
        return {"session": [session]}

    def call(application, action, **arguments):
        if application is inner_application:
            raise RuntimeError("scenario-fault")
        return {"session": session, "operation_id": "operation"}

    def close_runtime():
        calls.append("runtime-close")
        if "runtime" in faults:
            raise RuntimeError("runtime-close-fault")

    monkeypatch.setattr(lifecycle, "PEERS", ("codex",))
    monkeypatch.setattr(lifecycle, "_records", records)
    monkeypatch.setattr(lifecycle, "_call", call)
    monkeypatch.setattr(lifecycle, "_probe", lambda *args, **kwargs: {})
    monkeypatch.setattr(lifecycle, "_stale", lambda *args: {})
    monkeypatch.setattr(lifecycle, "build", lambda *args: SimpleNamespace(application=inner_application, close=close_runtime))
    monkeypatch.setattr(lifecycle.subprocess, "Popen", launch)
    monkeypatch.setattr(lifecycle.select, "select", lambda readers, *args: (readers, [], []))

    with pytest.raises(RuntimeError, match="scenario-fault") as caught:
        lifecycle.run_lifecycle(suite, {}, tmp_path)

    assert caught.value.args == ("scenario-fault",)
    assert calls == ["capture", "runtime-close", "host-terminate", "host-wait"]
    assert process.stdout.closed and streams[0].closed
    cleanup = json.loads((tmp_path / "lifecycle/codex/cleanup.json").read_text())
    assert cleanup["status"] == "failed"
    assert cleanup["host_returncode"] == 0 and cleanup["endpoint_removed"]
    expected = {"lifecycle_" + ("records" if fault == "records" else "runtime") for fault in faults}
    assert {error["name"] for error in cleanup["errors"]} == expected
    assert any("external_host_cleanup_failed" in note for note in caught.value.__notes__)
