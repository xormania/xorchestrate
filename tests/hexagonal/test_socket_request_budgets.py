"""A request's configured timeout bounds all socket stages and line fragments."""

import json

import pytest

from xorchestrate.adapters.native_gateway import GatewayFailure, request
from xorchestrate.adapters.unix import UnixClient
from xorchestrate.model import OrchestrationError
from tests.hexagonal.support import socket_request_budgets as fixture


@pytest.mark.parametrize("stage", ["connect_send", "send_receive", "fragments"])
def test_unix_handshake_spends_one_total_request_budget(monkeypatch, stage):
    connect, sends, fragments = {
        "connect_send": (0.6, (0.6,), [(0, fixture.WELCOME)]),
        "send_receive": (0.2, (0.4,), [(0.6, fixture.WELCOME)]),
        "fragments": (0, (0,), fixture.split_response(fixture.WELCOME, 0.3)),
    }[stage]
    clock, connection = fixture.scripted_socket(monkeypatch, connect=connect, sends=sends, fragments=fragments)
    with pytest.raises(OrchestrationError) as caught:
        UnixClient("/unused/budget.sock", timeout=1)
    assert caught.value.code == "handshake_timeout"
    assert clock.elapsed == pytest.approx(1), "the handshake budget includes all earlier socket stages"
    assert connection.closed, "an unfinished handshake cannot leave an open connection"


@pytest.mark.parametrize("stage", ["send_receive", "fragments"])
def test_control_request_spends_one_total_request_budget(monkeypatch, stage):
    fragments = [(0, fixture.WELCOME)]
    fragments += ([(0.6, b'{"ok":true}\n')] if stage == "send_receive"
                  else fixture.split_response(b'{"ok":true}\n', 0.3))
    clock, connection = fixture.scripted_socket(monkeypatch, sends=(0, 0.6 if stage == "send_receive" else 0),
                                              fragments=fragments)
    client = UnixClient("/unused/budget.sock", purpose="control")
    try:
        with pytest.raises(TimeoutError):
            client.request({"command": "status"}, timeout=1)
        assert clock.elapsed == pytest.approx(1), "receiving must not restart the request budget"
        assert connection.closed, "a timed-out control stream cannot carry a later request"
    finally:
        client.close()


@pytest.mark.parametrize("transport", ["unix", "tcp"])
@pytest.mark.parametrize("stage", ["connect_send", "send_receive", "fragments"])
def test_gateway_request_spends_one_total_request_budget(tmp_path, monkeypatch, transport, stage):
    connect, sends, fragments = {
        "connect_send": (0.6, (0.6,), [(0, fixture.RESULT)]),
        "send_receive": (0.2, (0.4,), [(0.6, fixture.RESULT)]),
        "fragments": (0, (0,), fixture.split_response(fixture.RESULT, 0.3)),
    }[stage]
    config = fixture.descriptor(tmp_path / "gateway.json", transport=transport,
                                address="/unused/gateway.sock" if transport == "unix" else ["127.0.0.1", 99])
    clock, connection = fixture.scripted_socket(monkeypatch, connect=connect, sends=sends, fragments=fragments)
    with pytest.raises(TimeoutError):
        request(config, "tools/list", {"marker": "budget"})
    assert clock.elapsed == pytest.approx(1), "connect, send and fragmented receive share the descriptor budget"
    assert connection.closed


def test_unix_healthy_fragmented_handshake_and_control_keep_their_frames(monkeypatch):
    clock, connection = fixture.scripted_socket(
        monkeypatch, connect=0.1, sends=(0.1, 0.1),
        fragments=fixture.split_response(b'\r\n' + fixture.WELCOME, 0.1)
        + fixture.split_response(b'\n{"ok":true}\r\n', 0.1))
    client = UnixClient("/unused/budget.sock", actor="tester", purpose="control",
                        attributes={"worktree": "budget"}, timeout=1)
    try:
        assert client.welcome == {"xorchestrate": "welcome"}
        assert client.request({"command": "status"}, timeout=1) == {"ok": True}
        assert [json.loads(raw) for raw in connection.sent] == [
            {"xorchestrate": "hello", "version": 1, "purpose": "control", "actor": "tester",
             "attributes": {"worktree": "budget"}}, {"command": "status"}]
        assert clock.elapsed == pytest.approx(1.1), "separate completed requests receive separate budgets"
    finally:
        client.close()


def test_unix_checks_listener_identity_before_sending_hello(monkeypatch):
    _, connection = fixture.scripted_socket(monkeypatch, fragments=[(0, fixture.WELCOME)])
    with pytest.raises(OrchestrationError) as caught:
        UnixClient("/unused/budget.sock", check=lambda _credentials: False)
    assert caught.value.code == "peer_untrusted"
    assert connection.sent == []
    assert connection.closed


def test_unix_coalesced_frames_survive_separate_request_budgets(monkeypatch):
    _, connection = fixture.scripted_socket(monkeypatch, sends=(0.1, 0.1, 0.1),
        fragments=[(0.1, fixture.WELCOME + b'{"reply":1}\n{"reply":2}\n')])
    client = UnixClient("/unused/budget.sock", purpose="control", timeout=1)
    try:
        assert client.request({"command": "first"}, timeout=1) == {"reply": 1}
        assert client.request({"command": "second"}, timeout=1) == {"reply": 2}
        assert len(connection.sent) == 3
    finally:
        client.close()


def test_unix_oversized_frame_is_drained_before_the_next_control_reply(monkeypatch):
    _, connection = fixture.scripted_socket(monkeypatch, sends=(0, 0, 0),
        fragments=[(0, fixture.WELCOME), (0.1, b"x" * 64 + b'\n{"ok":true}\n')])
    client = UnixClient("/unused/budget.sock", purpose="control", max_frame_bytes=32)
    try:
        with pytest.raises(OrchestrationError) as caught:
            client.request({"command": "large"}, timeout=1)
        assert caught.value.code == "frame_too_large"
        assert not connection.closed, "a fully drained oversize frame leaves the stream usable"
        assert client.request({"command": "small"}, timeout=1) == {"ok": True}
    finally:
        client.close()


def test_control_timeout_none_remains_unbounded(monkeypatch):
    clock, connection = fixture.scripted_socket(monkeypatch, sends=(0, 20),
        fragments=[(0, fixture.WELCOME)] + fixture.split_response(b'{"ok":true}\n', 20))
    client = UnixClient("/unused/budget.sock", purpose="control")
    try:
        assert client.request({"command": "wait"}, timeout=None) == {"ok": True}
        assert clock.elapsed == 100
        assert all(timeout is None for _, _, timeout, _ in connection.operations[3:])
    finally:
        client.close()


@pytest.mark.parametrize("response, expected", [
    ({"result": {"ok": True}}, {"ok": True}),
    ({"failure": {"code": "denied", "message": "No", "details": {"retry": False}}}, True),
    ({"error": {"code": "gateway_error", "message": "Failed"}}, False),
])
def test_gateway_healthy_fragmented_authentication_result_and_failure_envelopes(tmp_path, monkeypatch, response, expected):
    config = fixture.descriptor(tmp_path / "gateway.json")
    _, connection = fixture.scripted_socket(monkeypatch, connect=0.1, sends=(0.1,),
        fragments=fixture.split_response(json.dumps(response).encode() + b"\n", 0.1))
    if "result" in response:
        assert request(config, "tools/list", {"marker": "control"}) == expected
    else:
        with pytest.raises(GatewayFailure) as caught:
            request(config, "tools/list", {"marker": "control"})
        error = response.get("failure", response.get("error"))
        assert caught.value.application is expected
        assert caught.value.code == error["code"]
        assert caught.value.message == error["message"]
        assert caught.value.details == error.get("details", {})
    assert json.loads(connection.sent[0]) == {
        "token": "budget-test-capability", "action": "tools/list", "payload": {"marker": "control"}}
    assert connection.closed


@pytest.mark.parametrize("control", [False, True])
def test_real_unix_fragments_do_not_renew_request_timeout(monkeypatch, control):
    with fixture.fragmented_unix(monkeypatch, control=control) as peer:
        if control:
            client = UnixClient("/unused/socketpair.sock", purpose="control")
            try:
                with pytest.raises(TimeoutError):
                    client.request({"command": "status"}, timeout=peer.timeout)
            finally:
                client.close()
        else:
            with pytest.raises(OrchestrationError) as caught:
                UnixClient("/unused/socketpair.sock", timeout=peer.timeout)
            assert caught.value.code == "handshake_timeout"
        assert len(peer.sent) >= 2, "witness delivered repeated fragments before request exhaustion"


def test_real_tcp_gateway_fragments_do_not_renew_request_timeout(tmp_path):
    with fixture.fragmented_gateway(tmp_path / "gateway.json") as (config, peer):
        with pytest.raises(TimeoutError):
            request(config, "tools/list")
        assert len(peer.sent) >= 2, "witness delivered repeated fragments before request exhaustion"
        assert peer.requests == [{"token": "budget-test-capability", "action": "tools/list", "payload": {}}]
