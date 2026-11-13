"""Hosted sessions: one behavioural contract on both terminal hosts, with a real bridge host and the protocol peer."""

import pytest

from tests.hexagonal.contracts import hosted_sessions as contract
from tests.hexagonal.support.hosted_sessions import hosted_subject


@pytest.fixture(params=["tmux", "detached-process"], ids=["multiplexer", "detached-process"])
def subject(request):
    value = hosted_subject(request.param)
    probe = value.host()
    reason = probe.readiness()
    probe.close()
    if reason is not None:
        value.cleanup()
        pytest.skip(f"terminal host unavailable: {reason}")
    try:
        yield value
    finally:
        value.cleanup()


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
def test_hosted_sessions_meet_the_contract(subject, check):
    check(subject)


# -- the operator sees what a hosted session launches and which sessions need a reconnect ----------

HOSTED_CONFIG = {"version": 1, "sessions": {"agent": {"adapter": "hosted", "options": {
    "session": {"adapter": "codex", "options": {"command": ["${repo}/bin/inner-cli", "--serve"]}}}}}}


def test_a_wrapped_sessions_command_is_in_the_approval_summary_and_doctor():
    """The approval summary and doctor both list launched commands; a hosted session's inner command is one."""
    from xorchestrate.instance.render import check_repo_placeholders, launched_commands, repository_references

    commands = launched_commands(HOSTED_CONFIG)
    assert {"kind": "sessions", "name": "agent", "adapter": "codex", "option": "command",
            "command": ["${repo}/bin/inner-cli", "--serve"], "pass_env": []} in commands, commands
    assert all(item["adapter"] != "hosted" for item in commands), "the wrapper launches nothing itself"
    assert any(item["kind"] == "repository_command" and item["command"][0] == "${repo}/bin/inner-cli"
               for item in repository_references(HOSTED_CONFIG)), "a worktree command is flagged as outside approval"
    check_repo_placeholders(HOSTED_CONFIG)  # allowed where a launched process receives it
    misplaced = {"version": 1, "sessions": {"agent": {"adapter": "hosted", "options": {
        "session": {"adapter": "codex", "options": {"goal": "${repo}"}}}}}}
    with pytest.raises(Exception, match="repo"):
        check_repo_placeholders(misplaced)


class _Reconnecting:
    def start(self, specification):
        return {"handle": "h", "native_id": "n-reconnect"}

    def reconnect(self, source, specification):
        return dict(source)

    def send(self, session, message):
        return {"accepted": True}

    def poll(self, session):
        return []

    def stop(self, session):
        return {}

    def close(self):
        pass


class _Resuming(_Reconnecting):
    reconnect = None

    def resume(self, source, specification):
        return dict(source)


class _Neither(_Reconnecting):
    reconnect = None


def test_status_lists_the_sessions_a_restart_left_without_their_runtime():
    """After a restart the new runtime holds none of the live sessions: each is listed with the call to make."""
    from tests.hexagonal.support.application import application, make_store

    store = make_store("memory")
    drivers = {"hosted": _Reconnecting(), "resumable": _Resuming(), "plain": _Neither()}
    before = application(store, sessions=drivers)
    ids = {name: before.call("start_session", {"driver": name, "specification": {},
                                               "idempotency_key": f"start-{name}"})["session"]["id"]
           for name in drivers}
    stopped = before.call("start_session", {"driver": "hosted", "specification": {},
                                            "idempotency_key": "start-stopped"})["session"]["id"]
    before.call("stop_session", {"session_id": stopped, "idempotency_key": "stop-it"})
    assert before.sessions_needing_reconnect() == [], "the runtime that holds them needs nothing"

    after = application(store, sessions=drivers)  # the restarted runtime: same durable state, no live sessions
    listed = {item["session_id"]: item for item in after.sessions_needing_reconnect()}
    assert set(listed) == set(ids.values()), "every live session is listed and a stopped one is not"
    assert listed[ids["hosted"]]["call"]["action"] == "reconnect_session"
    assert listed[ids["resumable"]]["call"] == {"action": "start_session",
                                                "arguments": {"driver": "resumable",
                                                              "resume_session_id": ids["resumable"]}}
    assert listed[ids["plain"]]["call"] is None


def test_a_failed_hosted_start_answers_the_caller_with_a_safe_summary_the_operation_and_the_log(tmp_path):
    """Through the application: the caller gets the operation id and the log location, never terminal output."""
    from tests.hexagonal.support.application import application, make_store
    from tests.hexagonal.support.hosted_sessions import hosted_subject
    from xorchestrate.model import OrchestrationError

    hosted = hosted_subject("detached-process")
    driver = hosted.failing()
    try:
        app = application(make_store("memory"), sessions={"agent": driver})
        with pytest.raises(OrchestrationError) as failed:
            app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "broken"})
        details = failed.value.details
        logs = [text for key, text in hosted._strings(details) if "log" in key]
        assert details.get("operation_id") and logs, details
        printed = [line.strip() for line in open(logs[0], errors="replace") if len(line.strip()) > 12]
        assert printed, "the session's output is in its private log"
        assert not any(line in repr(details) for line in printed), "no terminal output reaches the caller"
        outcome = app.call("get_operation", {"operation_id": details["operation_id"]})
        assert not any(line in repr(outcome) for line in printed), "nor the preserved outcome"
    finally:
        driver.close()
        hosted.cleanup()


# -- stop always closes the terminal; a hosted entry is bounded by max_sessions -----------------------

def _hosted_driver(hosted, **options):
    from xorchestrate.adapters.hosted import HostedSessionDriver

    driver = HostedSessionDriver(hosted.options(**options))
    driver.bind_terminal_host(hosted.host(), endpoints=str(hosted.base), entry="agent")
    return driver


def test_a_hosted_stop_closes_the_terminal_when_the_bridge_host_does_not_answer_and_can_be_retried():
    """A bridge host that does not answer still has its terminal closed; a runtime holding no connection can stop."""
    import os
    import signal

    from tests.hexagonal.support.hosted_sessions import hosted_subject

    hosted = hosted_subject("detached-process")
    first = _hosted_driver(hosted, timeout=1)
    try:
        session = first.start(hosted.spec("unanswered"))
        os.kill(session["hosted"]["terminal"]["process"]["pid"], signal.SIGSTOP)  # the bridge host stops answering
        report = first.stop(session)
        assert report["bridge"]["acknowledged"] is False, report
        assert report["terminal"]["closed"] is True, report
        assert not hosted.terminal_running(session), "the terminal of an unanswering bridge host is closed"

        held_elsewhere = first.start(hosted.spec("retry"))
        first.close()  # detaches: the terminal keeps running, and this runtime's connection is gone
        assert hosted.terminal_running(held_elsewhere)
        second = _hosted_driver(hosted)
        try:
            report = second.stop(held_elsewhere)  # a retry from a runtime that holds no connection
            assert report["terminal"]["closed"] is True, report
            assert not hosted.terminal_running(held_elsewhere)
        finally:
            second.close()
    finally:
        first.close()
        hosted.cleanup()


def test_hosted_sessions_are_bounded_by_max_sessions_counting_detached_terminals():
    """Past ``max_sessions`` a new start is refused before anything is created; detached terminals count."""
    from tests.hexagonal.support.hosted_sessions import hosted_subject
    from xorchestrate.model import OrchestrationError

    hosted = hosted_subject("detached-process")
    first = _hosted_driver(hosted, max_sessions=1)
    try:
        session = first.start(hosted.spec("one"))
        created = sorted(hosted.sessions.iterdir())
        with pytest.raises(OrchestrationError) as refused:
            first.start(hosted.spec("two"))
        assert refused.value.code == "session_capacity" and refused.value.details["outcome"] == "not_started"
        assert sorted(hosted.sessions.iterdir()) == created, "nothing was created for the refused start"

        first.close()  # the terminal keeps running, detached
        second = _hosted_driver(hosted, max_sessions=1)
        try:
            with pytest.raises(OrchestrationError) as refused:
                second.start(hosted.spec("three"))
            assert refused.value.code == "session_capacity", "a detached running terminal counts"
            again = second.reconnect(session, hosted.spec("one"))  # reconnecting to it is not a new session
            assert again["native_id"] == session["native_id"]
            second.stop(again)
            second.stop(second.start(hosted.spec("four")))  # its slot is free once it is stopped
        finally:
            second.close()
    finally:
        first.close()
        hosted.cleanup()


# -- kills reach only the subject's own processes ---------------------------------------------------

def _bystander(named):
    """A same-user process outside the subject whose arguments merely name ``named``."""
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from scripts.testkit.adapters.ownership import processes
    from tests.hexagonal.support.host_processes import track

    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", str(named)], cwd=outside,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return process, processes()[process.pid][0], outside


def _end(process, outside):
    process.kill()
    process.wait(timeout=10)
    outside.rmdir()


def _fenced(monkeypatch):
    from tests.hexagonal.support.host_processes import fenced_signals

    return fenced_signals(monkeypatch)


def test_the_fence_lets_the_fenced_helper_fall_back_to_a_raw_signal_and_nothing_else(monkeypatch):
    """Where pidfd_open is missing or denied, ``send_signal`` falls back to ``os.kill`` after its own identity
    check: inside the fence that signal passes, while a raw ``os.kill`` from anywhere else still fails."""
    import os
    import signal
    import subprocess
    import sys

    from scripts.testkit.adapters import ownership

    def unavailable(pid, *flags):
        raise OSError("planted: pidfd_open is unavailable here")

    child = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    identity = ownership.processes()[child.pid][0]
    try:
        with monkeypatch.context() as during:
            during.setattr(os, "pidfd_open", unavailable)
            fence = _fenced(monkeypatch)
            try:
                with pytest.raises(pytest.fail.Exception, match="unfenced"):
                    os.kill(child.pid, 0)  # a raw signal outside the helper, harmless if it got through
                ownership.send_signal(identity, signal.SIGKILL)
            finally:
                fence.__exit__(None, None, None)
        assert child.wait(timeout=10) == -signal.SIGKILL, "the helper's fallback signal did not reach the child"
    finally:
        ownership.send_signal(identity, signal.SIGKILL)
        child.stdin.close()
        child.wait(timeout=10)
    assert not ownership.alive(identity), "the child outlived the test"


def test_a_total_loss_takes_the_terminal_host_and_its_terminals_and_nothing_outside(subject, monkeypatch):
    from scripts.testkit.adapters.ownership import alive, processes
    from tests.hexagonal.support.host_processes import processes_under

    driver = subject.driver()
    session = driver.start(subject.spec("loss"))
    driver.close()
    process, bystander, outside = _bystander(subject.sessions / "x")
    try:
        table = processes()
        own = [table[pid][0] for pid, _ in processes_under([subject.base]) if pid in table and pid != process.pid]
        assert own, "nothing of the subject was running"
        fence = _fenced(monkeypatch)
        try:
            subject.lose(session, everything=True)
        finally:
            fence.__exit__(None, None, None)
        assert alive(bystander), "the simulated loss killed a process outside the subject"
        assert not any(alive(item) for item in own), "the terminal host or a terminal survived the loss"
    finally:
        _end(process, outside)


LISTENS_HELD_ON_STDIN = ("import socket, sys\n"
                         "server = socket.socket(socket.AF_UNIX); server.bind(sys.argv[1]); server.listen()\n"
                         "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
                         "sys.stdin.buffer.read()\n")
"""A process of the subject by provenance (it listens on a socket inside the subject's base), held on a stdin pipe
the test owns, so it lives until the test closes it or dies itself, never longer."""


class _Newcomers:
    """Starts a process that becomes the subject's own right after each selection of the subject's processes, up to
    ``arrivals`` of them; each is held on a stdin pipe the test owns and is ended by identity in ``end``."""

    def __init__(self, subject, arrivals):
        from scripts.testkit.adapters import ownership

        self.subject, self.arrivals, self.real, self.started = subject, arrivals, ownership.owned_processes, []

    def after_a_selection(self, root, marker, **options):
        import subprocess
        import sys
        from pathlib import Path

        from scripts.testkit.adapters import ownership

        selection = self.real(root, marker, **options)
        if len(self.started) < self.arrivals and Path(root) == self.subject.base:
            address = self.subject.base / f"late-{len(self.started)}.sock"
            child = subprocess.Popen([sys.executable, "-c", LISTENS_HELD_ON_STDIN, str(address)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.started.append((child, ownership.processes()[child.pid][0], address))
            assert child.stdout.readline() == b"ready\n", "the newcomer did not start listening"
        return selection

    @property
    def identities(self):
        return [identity for _, identity, _ in self.started]

    def end(self):
        from scripts.testkit.adapters import ownership

        for child, identity, address in self.started:
            ownership.send_signal(identity, 9)
            child.stdin.close()
            child.stdout.close()
            child.wait(timeout=10)
            address.unlink(missing_ok=True)


@pytest.mark.parametrize("arrivals", [1, 4], ids=["after-the-first-selection", "after-every-selection"])
def test_a_total_loss_ends_subject_processes_born_after_it_selects_or_fails(subject, monkeypatch, arrivals):
    """A container failure leaves nothing of the subject. The test itself starts a process that becomes the
    subject's own right after a selection the loss makes, up to ``arrivals`` of them, and ends each by identity
    whatever happens.

    After the first selection only: that newcomer is ended too, or the loss fails. After every selection: some
    newcomer always arrives after the loss's last look, so the loss must fail rather than return as if total.
    """
    from scripts.testkit.adapters import ownership

    driver = subject.driver()
    session = driver.start(subject.spec("late"))
    driver.close()
    newcomers = _Newcomers(subject, arrivals)
    process, bystander, outside = _bystander(subject.sessions / "z")
    try:
        with monkeypatch.context() as during:
            during.setattr(ownership, "owned_processes", newcomers.after_a_selection)
            fence = _fenced(monkeypatch)
            try:
                subject.lose(session, everything=True)
                failed = False
            except AssertionError:
                failed = True
            finally:
                fence.__exit__(None, None, None)
        assert newcomers.started, "the loss never selected the subject's processes through the hook"
        assert len(newcomers.started) <= arrivals, newcomers.started
        if arrivals == 1:
            assert failed or not ownership.alive(newcomers.identities[0]), \
                "a subject process born after the first selection survived the loss"
        else:
            assert failed, "a loss that left a subject process alive returned as if it were total"
        assert ownership.alive(bystander), "the simulated loss killed a process outside the subject"
    finally:
        newcomers.end()
        _end(process, outside)
    assert not any(ownership.alive(identity) for identity in newcomers.identities), "a newcomer outlived the test"


def test_cleanup_reaches_only_the_subjects_own_processes(subject, monkeypatch):
    from scripts.testkit.adapters.ownership import alive

    process, bystander, outside = _bystander(subject.base / "y")
    try:
        fence = _fenced(monkeypatch)
        try:
            subject.cleanup()
        finally:
            fence.__exit__(None, None, None)
        assert alive(bystander), "cleanup killed a process outside the subject"
    finally:
        _end(process, outside)


class _FailingShutdown:
    """The subject's real host, except that shutdown fails, so cleanup has to stop what is left itself."""

    def __init__(self, host):
        self.host = host

    def __getattr__(self, name):
        return getattr(self.host, name)

    def shutdown(self):
        raise RuntimeError("planted: the terminal host did not shut down")


def test_cleanup_kills_what_a_failed_shutdown_left_and_nothing_outside(subject, monkeypatch):
    from scripts.testkit.adapters.ownership import alive, processes
    from tests.hexagonal.support.host_processes import processes_under

    driver = subject.driver()
    driver.start(subject.spec("cleanup"))
    driver.close()
    process, bystander, outside = _bystander(subject.base / "z")
    try:
        table = processes()
        own = [table[pid][0] for pid, _ in processes_under([subject.base]) if pid in table and pid != process.pid]
        assert own, "nothing of the subject was running"
        real_host = subject.host
        monkeypatch.setattr(subject, "host", lambda: _FailingShutdown(real_host()))
        fence = _fenced(monkeypatch)
        try:
            subject.cleanup()
        finally:
            fence.__exit__(None, None, None)
        assert alive(bystander), "cleanup killed a process outside the subject"
        assert not any(alive(item) for item in own), "cleanup left the subject's processes running"
    finally:
        _end(process, outside)


def test_cleanup_also_ends_a_subject_process_born_after_its_first_selection(subject, monkeypatch):
    """When the terminal host fails to shut down, cleanup stops what is left itself: a process that becomes the
    subject's own after cleanup first selected its victims is ended too. Cleanup is best effort (the leak guard
    reports what it cannot stop), so it is not required to fail; one newcomer, after the first selection only."""
    from scripts.testkit.adapters import ownership

    driver = subject.driver()
    driver.start(subject.spec("late-cleanup"))
    driver.close()
    newcomers = _Newcomers(subject, 1)
    process, bystander, outside = _bystander(subject.sessions / "z")
    try:
        real_host = subject.host
        with monkeypatch.context() as during:
            during.setattr(subject, "host", lambda: _FailingShutdown(real_host()))
            during.setattr(ownership, "owned_processes", newcomers.after_a_selection)
            fence = _fenced(monkeypatch)
            try:
                subject.cleanup()
            finally:
                fence.__exit__(None, None, None)
        assert newcomers.started, "cleanup never selected the subject's processes through the hook"
        assert not ownership.alive(newcomers.identities[0]), \
            "a subject process born after cleanup's first selection survived it"
        assert ownership.alive(bystander), "cleanup killed a process outside the subject"
    finally:
        newcomers.end()
        _end(process, outside)
    assert not any(ownership.alive(identity) for identity in newcomers.identities), "a newcomer outlived the test"
