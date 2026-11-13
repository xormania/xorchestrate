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
