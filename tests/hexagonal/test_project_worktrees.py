"""One project server serves every worktree of a project, and knows which worktree each call came from."""

import json
import sys

import pytest

from tests.hexagonal.contracts.project_server import INITIALIZED, initialize, rpc
from tests.hexagonal.support.project_server import SESSION_FIXTURE, CliEnvironment, short_directory


@pytest.fixture
def cli():
    with short_directory() as directory:
        environment = CliEnvironment(directory)
        try:
            yield environment
        finally:
            environment.cleanup()


def open_session(channel):
    assert channel.request(initialize())["result"]["serverInfo"]["name"] == "xorchestrate"
    channel.send(INITIALIZED)


def tool(channel, identifier, name, arguments):
    reply = channel.request(rpc(identifier, "tools/call", {"name": name, "arguments": arguments}))
    return reply["result"]["structuredContent"]


def test_worktrees_share_one_server_and_calls_carry_their_worktree(cli):
    feature = cli.project.add_worktree("feature")
    main, main_channel = cli.spawn("connect", "--actor", "main-agent")
    open_session(main_channel)
    other, feature_channel = cli.spawn("connect", "--actor", "feature-agent", cwd=feature)
    open_session(feature_channel)

    main_work = tool(main_channel, 2, "create_work", {"goal": "from main"})["work"]
    feature_work = tool(feature_channel, 2, "create_work", {"goal": "from feature"})["work"]
    assert main_work["created_worktree"]["root"] == str(cli.repo)
    assert feature_work["created_worktree"] == {
        "root": str(feature), "name": "feature", "branch": "feature",
        "config_hash": main_work["created_worktree"]["config_hash"]}
    # The same server and state: each connection sees the other's records.
    listed = {item["id"] for item in tool(main_channel, 3, "list_work", {})["work"]}
    assert {main_work["id"], feature_work["id"]} <= listed

    events = tool(feature_channel, 3, "events", {"after": 0, "limit": 100})["events"]
    created = {event["record_id"]: event for event in events if event["type"] == "work.created"}
    assert created[main_work["id"]]["worktree"]["root"] == str(cli.repo)
    assert created[feature_work["id"]]["worktree"]["root"] == str(feature)

    status = cli.json("status")
    assert status["worktrees"] == sorted([str(cli.repo), str(feature)])
    assert status["started_worktree"] in (str(cli.repo), str(feature))
    for process, channel in ((main, main_channel), (other, feature_channel)):
        assert cli.finish(process, channel)[0] == 0
    assert cli.json("down")["stopped"] is True


def test_sessions_resolve_repo_to_the_connections_worktree(cli):
    feature = cli.project.add_worktree("feature")
    # Only the feature worktree has this directory, so the session starts only
    # if ${repo} resolves to the connection's worktree, not the server's.
    (feature / "only-here").mkdir()
    cli.save({**cli.options, "sessions": {"fixture": {"adapter": "jsonrpc", "options": {
        "command": [sys.executable, str(SESSION_FIXTURE), "jsonrpc"], "cwd": "${repo}/only-here", "timeout": 10}}}})
    # The worktree carries the same tracked configuration as the running server.
    cli.project.write_config(cli.options, worktree=feature)
    process, channel = cli.spawn("connect", cwd=feature)
    open_session(channel)
    started = tool(channel, 2, "start_session", {"driver": "fixture", "specification": {"goal": "where am I"},
                                                 "idempotency_key": "start-1"})
    assert "session" in started, started
    assert started["session"]["created_worktree"]["root"] == str(feature)
    main, main_channel = cli.spawn("connect")
    open_session(main_channel)
    elsewhere = tool(main_channel, 2, "start_session", {"driver": "fixture", "specification": {"goal": "main"},
                                                        "idempotency_key": "start-main"})
    assert elsewhere["error"]["details"]["cause"] == "provider_unavailable", elsewhere
    assert cli.finish(main, main_channel)[0] == 0
    stopped = tool(channel, 3, "stop_session", {"session_id": started["session"]["id"], "idempotency_key": "stop-1"})
    assert "error" not in stopped, stopped
    assert cli.finish(process, channel)[0] == 0
    assert cli.json("down")["stopped"] is True


def test_a_worktree_with_a_different_tracked_configuration_is_refused(cli):
    feature = cli.project.add_worktree("feature")
    assert cli.json("up")["started"] is True
    changed = {**cli.options, "guardrail": {"adapter": "rules", "options": {"default": "deny"}}}
    # Even an approved configuration is refused until the server runs it.
    cli.project.write_config(changed, worktree=feature)
    refused = cli.run("connect", cwd=feature, timeout=30)
    assert refused.returncode == 2, refused
    error = json.loads(refused.stderr)["error"]
    assert error["code"] == "connection_refused"
    assert error["details"]["reason"] == "configuration_mismatch"
    assert error["details"]["files"] == ["config.json"]
    assert "config.json" in error["message"]
    # The main worktree still connects.
    process, channel = cli.spawn("connect")
    open_session(channel)
    assert cli.finish(process, channel)[0] == 0
    assert cli.json("down")["stopped"] is True


def test_guardrails_receive_the_worktree_of_each_call(cli):
    feature = cli.project.add_worktree("feature")
    policy = {**cli.options, "guardrail": {"adapter": "rules", "options": {"default": "allow", "rules": [
        {"id": "no-feature-work", "effect": "deny", "actions": ["create_work"],
         "where": {"worktree.name": "feature"}, "reason": "Feature worktrees only read"}]}}}
    cli.save(policy)
    cli.project.write_config(policy, worktree=feature)
    main, main_channel = cli.spawn("connect")
    open_session(main_channel)
    other, feature_channel = cli.spawn("connect", cwd=feature)
    open_session(feature_channel)
    assert "work" in tool(main_channel, 2, "create_work", {"goal": "allowed"})
    denied = tool(feature_channel, 2, "create_work", {"goal": "denied"})
    assert denied["error"]["code"] == "denied" and denied["error"]["message"] == "Feature worktrees only read"
    for process, channel in ((main, main_channel), (other, feature_channel)):
        assert cli.finish(process, channel)[0] == 0
    assert cli.json("down")["stopped"] is True


def test_a_server_starts_only_on_an_approved_configuration(cli):
    cli.project.write_config({**cli.options, "routing": {"adapter": "configurable"}}, approved=False)
    failed = cli.run("up", "--start-timeout", "20")
    assert failed.returncode == 2, failed
    assert json.loads(failed.stderr)["error"]["code"] == "server_unavailable"
    assert "approval_required" in cli.server_log()
    cli.project.approve()
    assert cli.json("up")["started"] is True
    assert cli.json("down")["stopped"] is True
