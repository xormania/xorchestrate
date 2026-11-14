"""Operation readiness requirements independent of a particular peer or transport."""


def completion_before_wait_is_retained(log, observer, source, incarnation):
    """Arming precedes the effect; waiting need not race its completion."""
    log.record("target", "complete", {"attempt": "old"})
    ticket = observer.arm(source, incarnation, "target", "complete")
    assert ticket.poll() is None, "OBSERVATION_MUST_NOT_REUSE_PREARM_COMPLETION"
    log.record("target", "complete", {"attempt": "current"})
    result = ticket.wait(timeout=1)
    assert result.operation == "target" and result.payload == {"attempt": "current"}, \
        "OBSERVATION_RETAINS_COMPLETION_BEFORE_WAIT"
    return ticket, result


def unrelated_completions_do_not_release_operation(log, observer, source, incarnation):
    """A different operation or boundary cannot supply the awaited acknowledgement."""
    ticket = observer.arm(source, incarnation, "target", "complete")
    log.record("competing", "complete", {"effect": "competing returned"})
    assert ticket.poll() is None, "OBSERVATION_REQUIRES_EXACT_OPERATION"
    log.record("target", "entered", {"effect": "target entered"})
    assert ticket.poll() is None, "OBSERVATION_REQUIRES_EXACT_BOUNDARY"
    log.record("target", "complete", {"effect": "target returned"})
    result = ticket.poll()
    assert result is not None and result.payload == {"effect": "target returned"}, \
        "OBSERVATION_ACCEPTS_CURRENT_BOUND_COMPLETION"
    return ticket, result


def witnessed_native_schedule(fixture):
    """Both overlapping tails must really activate before either is released."""
    fixture.submit("first", callback=True, tail=True)
    fixture.submit("second", callback=True, tail=True)
    first_callback = fixture.activation("first", "callback")
    second_callback = fixture.activation("second", "callback")
    assert first_callback["operation"] == "first" and second_callback["operation"] == "second", \
        "SCHEDULE_REQUIRES_CALLBACK_ACTIVATION"
    assert first_callback["incarnation"] == second_callback["incarnation"]
    fixture.release_callback("second")
    fixture.release_callback("first")
    first = fixture.activation("first", "tail")
    second = fixture.activation("second", "tail")
    assert first["operation"] == "first" and second["operation"] == "second"
    assert first["stage"] == second["stage"] == "tail"
    assert first["source"] == second["source"] and first["incarnation"] == second["incarnation"]
    assert first["native_operation"] and second["native_operation"]
    assert first["native_operation"] != second["native_operation"], "SCHEDULE_BINDS_DISTINCT_NATIVE_OPERATIONS"
    assert fixture.completion("first") is None and fixture.completion("second") is None, \
        "SCHEDULE_ACTIVATION_PRECEDES_TERMINAL_COMPLETION"
    fixture.release_tail("second")
    assert fixture.completion("first") is None, "COMPETING_COMPLETION_CANNOT_RELEASE_TARGET"
    completed = fixture.completion("second")
    assert completed is not None and completed.operation == "second"
    assert completed.payload["native_operation"] == second["native_operation"]
    fixture.release_tail("first")
    assert fixture.completion("first").payload["native_operation"] == first["native_operation"]
    fixture.await_idle()


def each_release_reaches_only_the_next_held_boundary(fixture):
    """One operation may hold before start, in its callback and in its native tail."""
    fixture.submit("chain", start=True, callback=True, tail=True)
    start = fixture.activation("chain", "start")
    assert start is not None and start["stage"] == "start", "CHAIN_START_IS_ACTIVATED"
    assert fixture.activation("chain", "callback") is None
    assert fixture.activation("chain", "tail") is None
    assert fixture.completion("chain") is None
    fixture.release_start("chain")
    callback = fixture.activation("chain", "callback")
    assert callback is not None and callback["operation"] == "chain", "CHAIN_RELEASE_REACHES_CALLBACK"
    assert fixture.activation("chain", "tail") is None and fixture.completion("chain") is None, \
        "CHAIN_CALLBACK_REMAINS_HELD_AFTER_START_RELEASE"
    fixture.release_callback("chain")
    tail = fixture.activation("chain", "tail")
    assert tail is not None and tail["stage"] == "tail", "CHAIN_RELEASE_REACHES_NATIVE_TAIL"
    assert tail["native_operation"] == start["native_operation"]
    assert fixture.completion("chain") is None, "CHAIN_NATIVE_TAIL_REMAINS_HELD_AFTER_CALLBACK_RELEASE"
    fixture.release_tail("chain")
    completed = fixture.completion("chain")
    assert completed is not None and completed.payload["native_operation"] == start["native_operation"], \
        "CHAIN_RELEASE_REACHES_OWN_COMPLETION"
    fixture.await_idle()
