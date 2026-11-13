"""Recovery after a hard restart: one behavioural contract on a shared durable store and a real project server."""

from pathlib import Path

import pytest

from tests.hexagonal.contracts import recovery as contract
from tests.hexagonal.support.recovery import RecoverySubject


@pytest.fixture
def subject(tmp_path):
    value = RecoverySubject(tmp_path)
    try:
        yield value
    finally:
        value.close()


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
def test_recovery_meets_the_contract(subject, check):
    check(subject)


# -- review regressions ------------------------------------------------------------------------------

def test_an_explicit_recover_weighs_the_sessions_work_binding_for_the_caller(subject):
    """A rule denying a worker reconnect_session for one work also stops that worker's recover of it."""
    before = subject.runtime()
    work = before.call("create_work", {"goal": "Recover only with authority"})["work"]["id"]
    session = before.call("start_session", {"driver": "hosted", "specification": {}, "work_id": work,
                                            "idempotency_key": "start-bound"}, "owner")["session"]["id"]
    rules = {"rules": [{"actions": ["reconnect_session"], "actors": ["worker"], "where": {"work_id": work},
                        "effect": "deny"}]}
    after = subject.runtime("manual", rules=rules)
    report = after.call("recover", {}, "worker")
    assert [item["session_id"] for item in report["not_authorised"]] == [session], "RECOVERY_RECOVER_WEIGHS_WORK_BINDING"
    assert report["recovered"] == [] and subject.reconnects == {}, "nothing was reconnected"


def _wait_for(condition, timeout=10):
    import time

    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "the condition was not reached"
        time.sleep(0.01)


def test_a_server_stopping_during_recovery_waits_for_the_session_in_hand_and_takes_no_more(subject):
    """Shutdown joins the automatic recovery before the runtime closes; sessions not taken stay interrupted."""
    import tempfile
    from pathlib import Path

    from tests.hexagonal.support.project_server import InProcessServer

    before = subject.runtime()
    first, second = subject.start(before, "hosted"), subject.start(before, "hosted")
    after = subject.runtime()
    subject.slow_reconnect(1.0)
    served = InProcessServer(after, Path(tempfile.mkdtemp(prefix="xrc-")) / "s.sock")
    try:
        thread = served.server.start_recovery(True)
        _wait_for(lambda: sum(subject.reconnect_calls.values()) == 1)  # the first reconnect is in hand
        served.server.down(detach=True)
        assert not thread.is_alive(), "the recovery finished before the server stopped"
        assert sum(subject.reconnect_calls.values()) == 1, "RECOVERY_STOPPING_TAKES_NO_MORE_SESSIONS"
        left = {item.session_id for item in after.interrupted_sessions()}
        assert len(left & {first, second}) == 1, "the session not taken stays interrupted"
    finally:
        served.stop()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_server_interrupted_during_recovery_waits_for_the_session_in_hand_before_it_returns(subject):
    """An interrupted serve loop (no drain) still joins the automatic recovery before the runtime can close."""
    import tempfile
    from pathlib import Path

    from tests.hexagonal.support.project_server import InProcessServer

    before = subject.runtime()
    first, second = subject.start(before, "hosted"), subject.start(before, "hosted")
    after = subject.runtime()
    subject.slow_reconnect(1.0)
    served = InProcessServer(after, Path(tempfile.mkdtemp(prefix="xrc-")) / "s.sock")
    try:
        thread = served.server.start_recovery(True)
        _wait_for(lambda: sum(subject.reconnect_calls.values()) == 1)

        def interrupted(timeout):
            raise RuntimeError("interrupted")
        served.server.transport.accept = interrupted  # the serve loop ends abruptly, without a drain
        served.thread.join(30)
        assert not served.thread.is_alive(), "the serve loop returned"
        assert not thread.is_alive(), "RECOVERY_SERVE_LOOP_WAITS_FOR_RECOVERY"
        assert sum(subject.reconnect_calls.values()) == 1, "no further session was taken"
        left = {item.session_id for item in after.interrupted_sessions()}
        assert len(left & {first, second}) == 1, "the session not taken stays interrupted"
    finally:
        served.stop()


def test_recovery_running_belongs_to_the_live_runtime_not_to_durable_state(subject):
    """A runtime that is mid-recovery (or died there) leaves nothing that reads as running in another runtime."""
    import threading

    before = subject.runtime()
    subject.start(before, "hosted")
    abandoned = subject.runtime()  # recovers, and is abandoned mid-recovery as if it had died
    subject.slow_reconnect(1.0)
    worker = threading.Thread(target=abandoned.recover)
    worker.start()
    try:
        _wait_for(lambda: sum(subject.reconnect_calls.values()) == 1)
        assert abandoned.recovery_status()["running"] is True
        later = subject.runtime()
        later.recover([])  # its own recovery, which ends
        assert later.recovery_status()["running"] is False, "RECOVERY_RUNNING_BELONGS_TO_LIVE_RUNTIME"
    finally:
        worker.join(30)


MARKER_STORE = '''
import os
open(os.environ["XO_MARKER"] + ".imported", "w").close()


def factory(options):
    open(os.environ["XO_MARKER"] + ".constructed", "w").close()
    raise SystemExit("constructed")
'''


@pytest.mark.parametrize("approved", [False, True], ids=["unapproved", "approved"])
def test_doctor_never_imports_or_constructs_a_store_factory_for_its_recovery_check(tmp_path, approved):
    """Doctor reads recovery state only from a built-in store of approved configuration."""
    import json
    import os

    from tests.hexagonal.support.project_server import CliEnvironment, short_directory

    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "xo_marker_store.py").write_text(MARKER_STORE)
    marker = tmp_path / "marker"
    with short_directory() as directory:
        cli = CliEnvironment(directory)
        try:
            cli.project.write_config({"version": 1, "store": {"adapter": "xo_marker_store:factory"}},
                                     approved=approved)
            environment = {**cli.environment, "XO_MARKER": str(marker),
                           "PYTHONPATH": os.pathsep.join(filter(None, [str(modules),
                                                                       cli.environment.get("PYTHONPATH")]))}
            result = cli.run("doctor", environment=environment)
        finally:
            cli.cleanup()
    assert not (tmp_path / "marker.imported").exists(), "RECOVERY_DOCTOR_NEVER_IMPORTS_STORE_FACTORY"
    assert not (tmp_path / "marker.constructed").exists(), "the factory was called"
    checks = {check["check"]: check for check in json.loads(result.stdout)["checks"]}
    assert checks["recovery"].get("inspected") is False, checks["recovery"]


def test_doctor_does_not_open_a_built_in_store_of_unapproved_configuration():
    """Unapproved configuration is not inspected even when its store is built in: the store file is left as it is."""
    import hashlib
    import json

    from tests.hexagonal.support.project_server import CliEnvironment, short_directory

    with short_directory() as directory:
        cli = CliEnvironment(directory)
        try:
            cli.json("call", "list_sessions", "--json", "{}")  # an approved run created the project's store
            stores = sorted(Path(cli.home).rglob("*.sqlite3"))
            assert stores, "the approved run created a store"
            digests = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in stores}
            cli.project.write_config({"version": 1, "store": {"adapter": "sqlite", "options": {}}}, approved=False)
            result = cli.run("doctor")
            checks = {check["check"]: check for check in json.loads(result.stdout)["checks"]}
            assert checks["approval"]["status"] == "error", checks["approval"]
            assert checks["recovery"].get("inspected") is False, "RECOVERY_DOCTOR_SKIPS_UNAPPROVED_STORE"
            assert sorted(Path(cli.home).rglob("*.sqlite3")) == stores, "no store was created"
            assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in stores} == digests
        finally:
            cli.cleanup()
