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
