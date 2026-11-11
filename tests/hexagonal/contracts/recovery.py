"""Independent expectations for recovery after a hard restart (D9), from durable state alone.

Every check receives a ``subject`` (see ``tests.hexagonal.support.recovery``):

- ``subject.runtime(policy=None)`` builds an application on the subject's shared durable store, as a restarted
  server would (``policy`` names a recovery policy; ``None`` keeps the default);
- ``subject.start(app, driver)`` starts a session; ``subject.events(app, kind, session_id)`` reads its events; ``subject.status(app, session_id)`` reads its recorded status;
- ``subject.reconnects`` counts reconnect calls per source session; ``subject.sends`` counts sends;
  ``subject.fail_reconnect(session_id)`` makes one session's reconnect fail; ``subject.slow_reconnect(seconds)``;
- ``subject.server_recovery(app, unclean_stop)`` runs the server's startup recovery and waits for it;
- ``subject.one_shot_runtime()`` builds a one-shot runtime from configuration and says whether recovery ran.

Drivers: ``"hosted"`` reconnects (as hosted and bridge sessions do); ``"owned"`` cannot (an owned child process).
"""

LIVE = {"starting", "active", "unknown"}


def _crashed_with_sessions(subject, *drivers):
    """A runtime starts sessions and then dies without stopping them: only durable state is left."""
    before = subject.runtime()
    return [subject.start(before, driver) for driver in drivers]


def default_policy_recovers_from_durable_state_alone(subject):
    hosted, owned = _crashed_with_sessions(subject, "hosted", "owned")
    after = subject.runtime()
    report = subject.server_recovery(after, unclean_stop=True)
    assert subject.reconnects == {hosted: 1}, "only the session whose driver reconnects is reconnected"
    assert [item["session_id"] for item in report["recovered"]] == [hosted], report
    assert subject.status(after, owned) == "disconnected", "an owned child cannot survive a hard restart"
    assert subject.reason(after, owned) == "hard_restart"
    assert after.interrupted_sessions() == [], "nothing is left interrupted"


def manual_policy_reconnects_nothing_and_reconciles_what_cannot_reconnect(subject):
    """Manual: detect, reconcile what cannot be reconnected, report; reconnect nothing."""
    hosted, owned = _crashed_with_sessions(subject, "hosted", "owned")
    after = subject.runtime(policy="manual")
    status_before = subject.status(after, hosted)
    subject.server_recovery(after, unclean_stop=True)
    assert subject.reconnects == {}, "the manual policy reconnects nothing"
    assert subject.status(after, hosted) == status_before, "a reconnectable session is left exactly as it was"
    assert hosted in [item.session_id for item in after.interrupted_sessions()], "and is listed as needing recovery"
    assert subject.status(after, owned) == "disconnected", "a session that cannot reconnect is reconciled"
    assert subject.reason(after, owned) == "hard_restart"
    events = subject.events(after, "session.disconnected", owned)
    assert len(events) == 1 and events[0].get("reason") == "hard_restart", events


def recover_is_idempotent(subject):
    hosted, owned = _crashed_with_sessions(subject, "hosted", "owned")
    after = subject.runtime(policy="manual")
    first = after.recover()
    second = after.recover()
    assert subject.reconnects == {hosted: 1}, "a second recover reconnects nothing again"
    assert second["recovered"] == [] and second["disconnected"] == [], second
    assert subject.status(after, owned) == "disconnected"
    assert first["recovered"] and first["disconnected"], first


def pending_effects_are_never_replayed(subject):
    (hosted,) = _crashed_with_sessions(subject, "hosted")
    sends = subject.sends
    after = subject.runtime()
    subject.server_recovery(after, unclean_stop=True)
    assert subject.sends == sends, "recovery re-sends nothing"


def one_failure_stops_neither_the_others_nor_the_server(subject):
    broken, healthy = _crashed_with_sessions(subject, "hosted", "hosted")
    subject.fail_reconnect(broken)
    after = subject.runtime()
    report = subject.server_recovery(after, unclean_stop=True)
    assert [item["session_id"] for item in report["failed"]] == [broken], report
    assert [item["session_id"] for item in report["recovered"]] == [healthy], report
    assert subject.server_answers(after), "the server still serves after a failed recovery"


def a_clean_stop_detects_nothing(subject):
    before = subject.runtime()
    for driver in ("hosted", "owned"):
        session = subject.start(before, driver)
        subject.stop(before, session)
    after = subject.runtime()
    assert after.interrupted_sessions() == []
    assert subject.server_recovery(after, unclean_stop=False) is None, "nothing runs after a clean stop"
    assert subject.reconnects == {}


def one_shot_runtimes_never_recover(subject):
    _crashed_with_sessions(subject, "hosted")
    assert subject.one_shot_runtime() is False, "a one-shot runtime never runs recovery"
    assert subject.reconnects == {}


def concurrent_recoveries_reconnect_each_session_exactly_once(subject):
    """The automatic run and explicit recovers race in one runtime (one server holds a project): one reconnect each."""
    first, second = _crashed_with_sessions(subject, "hosted", "hosted")
    subject.slow_reconnect(0.3)
    runtime = subject.runtime(policy="manual")
    subject.concurrently([lambda: subject.server_recovery_with_policy(runtime), runtime.recover, runtime.recover])
    assert subject.reconnects == {first: 1, second: 1}, subject.reconnects
    live = subject.live_replacements(runtime)
    assert sorted(live.values()) == [1, 1], f"one live replacement per session: {live}"


def explicit_recover_acts_only_where_its_caller_is_authorised(subject):
    """An explicit recover reconnects only what its caller may reconnect and reconciles only what it may stop."""
    before = subject.runtime()
    owners = {driver: subject.start(before, driver, "owner") for driver in ("hosted", "owned")}
    workers = {driver: subject.start(before, driver, "worker") for driver in ("hosted", "owned")}
    after = subject.runtime(policy="manual", rules=subject.WORKER_LIMITS)
    untouched = {key: subject.status(after, key) for key in owners.values()}

    def unauthorised(report):
        return {item["session_id"] for key, items in report.items() if "authori" in key and isinstance(items, list)
                for item in items}

    named = after.call("recover", {"session_ids": [owners["hosted"]]}, "worker")
    assert subject.reconnects == {}, "naming another actor's session does not let the worker reconnect it"
    assert owners["hosted"] in unauthorised(named), f"it is listed as not authorised: {named}"
    everything = after.call("recover", {}, "worker")
    assert subject.reconnects == {workers["hosted"]: 1}, "the worker recovers its own session only"
    assert subject.status(after, workers["owned"]) == "disconnected", "and reconciles only what it could stop"
    assert {key: subject.status(after, key) for key in owners.values()} == untouched, "others are left untouched"
    assert set(owners.values()) <= unauthorised(everything), everything
    owner = after.call("recover", {}, "owner")
    assert subject.reconnects == {workers["hosted"]: 1, owners["hosted"]: 1}, owner
    assert subject.status(after, owners["owned"]) == "disconnected"


def the_automatic_run_acts_as_the_runtime(subject):
    before = subject.runtime()
    owned_by_owner = subject.start(before, "hosted", "owner")
    after = subject.runtime(rules=subject.WORKER_LIMITS)
    subject.server_recovery(after, unclean_stop=True)
    assert subject.reconnects == {owned_by_owner: 1}, "the server's own recovery is not limited by a caller's rules"


def status_and_doctor_report_recovery(subject):
    hosted, _owned = _crashed_with_sessions(subject, "hosted", "owned")
    after = subject.runtime(policy="manual")
    status = subject.server_status(after, unclean_stop=True)
    recovery = status["recovery"]
    assert recovery["detected"]["unclean_stop"] is True and hosted in recovery["detected"]["interrupted"], recovery
    assert recovery["detected"]["selected"] == [], "the manual policy selects nothing"
    assert any(item["session_id"] == hosted for item in status["sessions_needing_reconnect"]), status
    doctor = subject.doctor_recovery()
    assert doctor["check"] == "recovery" and doctor["status"] in {"ok", "warning"}, doctor


CHECKS = (default_policy_recovers_from_durable_state_alone,
          manual_policy_reconnects_nothing_and_reconciles_what_cannot_reconnect, recover_is_idempotent,
          pending_effects_are_never_replayed, one_failure_stops_neither_the_others_nor_the_server,
          a_clean_stop_detects_nothing, one_shot_runtimes_never_recover,
          concurrent_recoveries_reconnect_each_session_exactly_once, status_and_doctor_report_recovery,
          explicit_recover_acts_only_where_its_caller_is_authorised, the_automatic_run_acts_as_the_runtime)
