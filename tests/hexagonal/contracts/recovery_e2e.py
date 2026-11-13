"""Recovery after a container failure, end to end at the public boundary: scenarios against the e2e ``World``.

A hosted session runs through the real command line and project server with the resumable deterministic peer.
A total loss kills every process of the project at once and keeps the durable home; a server-only loss kills
just the server. Then ``up``, and, under the manual policy, ``recover``.
"""

from tests.hexagonal.contracts.e2e_world import ServerLoss, TotalLoss, awaited_or_none


def sessions(client):
    return {item["id"]: item for item in client.call("list_sessions", {})["sessions"]}


def unresolved(client, replacement_id):
    """The operations the replacement carries over as unresolved, with their preserved outcomes."""
    record = sessions(client)[replacement_id]
    return {operation_id: client.call("get_operation", {"operation_id": operation_id})["operation"]["status"]
            for operation_id in record.get("unresolved_effects") or []}


def assert_continues(client, original, replacement_id):
    listed = sessions(client)
    assert listed[replacement_id]["backend"]["native_id"] == original["backend"]["native_id"], "same conversation"
    reply = client.call("send_session", {"session_id": replacement_id, "message": {"text": "after"},
                                         "idempotency_key": "send-after"})
    assert reply["output"]["native_id"] == original["backend"]["native_id"], str(reply)[:300]


def lineage(listed, root):
    """``root`` and every session that replaced it, directly or through another replacement."""
    family, grown = {root}, True
    while grown:
        grown = False
        for key, item in listed.items():
            if key not in family and (item.get("resume_source") or {}).get("session_id") in family:
                family.add(key)
                grown = True
    return family


def _mentioned(value):
    """Every session id a status or report names anywhere."""
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"session_id", "replacement_session_id"} and isinstance(item, str):
                found.add(item)
            else:
                found |= _mentioned(item)
    elif isinstance(value, list):
        for item in value:
            found |= _mentioned(item)
    return found


def recovered_map(report):
    return {item["session_id"]: item["replacement_session_id"] for item in report["recovered"]}


def _finished(world):
    """The recovery status once a report exists and nothing runs."""
    recovery = world.status()["recovery"]
    return recovery if recovery.get("report") and not recovery.get("running") else None


def _lost(world, client):
    """Every process of the project dies at once; the client went with it."""
    client.kill()
    receipt = world.disturb(TotalLoss())
    assert receipt.outcome == "applied", receipt


def after_total_loss_the_default_policy_brings_the_session_back_without_a_call(world):
    world.save()
    client = world.client()
    original = world.start(client)
    _lost(world, client)
    assert world.up()["started"] is True
    recovery = awaited_or_none(world, "recovery finished after up", lambda: _finished(world))
    assert recovery is not None, "RECOVERY_STARTS_ON_UP"
    mapped = recovered_map(recovery["report"])
    assert list(mapped) == [original["id"]], mapped
    with world.client() as after:
        assert_continues(after, original, mapped[original["id"]])
    assert world.down()["stopped"] is True


def a_turn_in_flight_at_the_loss_is_left_unresolved_reported_and_never_replayed(world):
    world.save()
    client = world.client()
    original = world.start(client, in_flight=True)
    _lost(world, client)
    world.up()
    report = world.await_("recovery finished after up", lambda: _finished(world))["report"]
    replacement = recovered_map(report)[original["id"]]
    with world.client() as after:
        carried = unresolved(after, replacement)
        # Exactly as the effect journal has it: recovery adds no reconciliation of its own.
        assert list(carried.values()) == ["pending"], f"the turn in flight is left as the journal has it: {carried}"
        (operation_id,) = carried
        listed = [entry for entries in report.values() if isinstance(entries, list) for entry in entries
                  if isinstance(entry, dict) and entry.get("operation_id") == operation_id]
        assert listed and all(entry.get("call") for entry in listed), \
            f"the report names the interrupted operation and the call that reconciles it: {str(report)[:400]}"
        assert sessions(after)[replacement]["backend"]["native_id"] == original["backend"]["native_id"]
        after.call("poll_session", {"session_id": replacement, "idempotency_key": "poll-after"})  # observation works
        after.call("send_session", {"session_id": replacement, "message": {"text": "after"},
                                    "idempotency_key": "send-after"}, refused="conflict")
        assert unresolved(after, replacement) == carried, "nothing replayed or resolved it"
    delivered = [entry["text"] for entry in world.peer_received(original)]
    assert delivered.count("hang") <= 1, "RECOVERY_IN_FLIGHT_TURN_IS_NEVER_REPLAYED_TO_THE_PEER"
    assert delivered.count("hang") == 1, delivered
    world.down()


def under_manual_nothing_is_reconnected_until_recover(world):
    world.save(policy="manual")
    client = world.client()
    original = world.start(client)
    _lost(world, client)
    world.up()
    world.settle(2)  # long enough for a reconnect the manual policy must never make; slice 2 replaces it
    status = world.status()
    assert status["recovery"]["detected"]["selected"] == [], status["recovery"]
    assert any(item["session_id"] == original["id"] for item in status["sessions_needing_reconnect"]), status
    assert world.bridge_hosts() == [], "nothing was reconnected"
    recovered = world.recover()
    mapped = recovered_map(recovered.get("report") or recovered)
    assert original["id"] in mapped, str(recovered)[:300]
    with world.client() as after:
        assert_continues(after, original, mapped[original["id"]])
    world.down()


def a_server_only_crash_adopts_the_running_terminal_and_keeps_its_bridge_host(world):
    world.save()
    client = world.client()
    original = world.start(client)
    hosts = world.bridge_hosts()
    assert len(hosts) == 1, hosts
    receipt = world.disturb(ServerLoss())
    assert receipt.outcome == "applied", receipt
    client.kill()
    world.up()
    mapped = recovered_map(world.await_("recovery finished after up", lambda: _finished(world))["report"])
    assert original["id"] in mapped, mapped
    assert world.bridge_hosts() == hosts, "RECOVERY_ADOPTS_RUNNING_TERMINAL"
    with world.client() as after:
        assert_continues(after, original, mapped[original["id"]])
    world.down()


def stale_sockets_left_by_a_total_loss_never_block_recovery(world):
    """A total loss that leaves the dead processes' socket files behind: up still recovers the conversation."""
    world.save()
    client = world.client()
    original = world.start(client)
    client.kill()
    receipt = world.disturb(TotalLoss(keep_sockets=True))
    assert receipt.outcome == "applied", receipt
    assert world.up()["started"] is True, "a stale endpoint does not stop the server starting"
    recovery = world.await_("recovery finished after up", lambda: _finished(world))
    mapped = recovered_map(recovery["report"])
    assert list(mapped) == [original["id"]], mapped
    with world.client() as after:
        assert_continues(after, original, mapped[original["id"]])
    assert len(world.bridge_hosts()) == 1, "a live bridge host serves the conversation again"
    world.down()


def a_second_loss_while_a_session_is_being_reconnected_brings_it_back_exactly_once(world):
    """The project dies again while recovery has a session in hand; the next up finishes it, once."""
    world.save()
    client = world.client()
    original = world.start(client)
    gate = world.hold("resume")
    _lost(world, client)
    world.up()
    world.await_("the peer is asked to resume the conversation", gate.entered)
    with world.client() as watcher:
        taking = [item for item in sessions(watcher).values()
                  if (item.get("resume_source") or {}).get("session_id") == original["id"]]
        assert [item["status"] for item in taking] == ["starting"], taking
        operation = watcher.call("get_operation", {"operation_id": taking[0]["pending_operation"]})["operation"]
        assert (operation["action"], operation["status"]) == ("reconnect_session", "pending"), operation
    receipt = world.disturb(TotalLoss())
    assert receipt.outcome == "applied", receipt
    gate.release()
    world.up()
    recovery = world.await_("recovery finished after the second up", lambda: _finished(world))
    mapped = recovered_map(recovery["report"])
    with world.client() as after:
        listed = sessions(after)
        family = lineage(listed, original["id"])
        recovered = [replacement for source, replacement in mapped.items() if source in family]
        assert len(recovered) == 1, mapped
        live = sorted(key for key in family if listed[key]["status"] in {"starting", "active"})
        assert live == recovered, {key: listed[key]["status"] for key in family}
        # A replacement killed mid-reconnect keeps its outcome uncertain rather than guessed; the operator sees it.
        uncertain = sorted(key for key in family if listed[key]["status"] == "unknown")
        shown = _mentioned(recovery) | _mentioned(world.status())
        assert set(uncertain) <= shown, {"uncertain": uncertain, "shown to the operator": sorted(shown)}
        assert_continues(after, original, recovered[0])
    assert len(world.bridge_hosts()) == 1, "exactly one bridge host serves the conversation"
    world.down()


CHECKS = (after_total_loss_the_default_policy_brings_the_session_back_without_a_call,
          a_turn_in_flight_at_the_loss_is_left_unresolved_reported_and_never_replayed,
          under_manual_nothing_is_reconnected_until_recover,
          a_server_only_crash_adopts_the_running_terminal_and_keeps_its_bridge_host,
          stale_sockets_left_by_a_total_loss_never_block_recovery,
          a_second_loss_while_a_session_is_being_reconnected_brings_it_back_exactly_once)
