"""Independent expectations for hosted sessions: a native conversation in a bridge host the terminal host keeps.

Every check receives a ``subject`` (see ``tests.hexagonal.support.hosted_sessions``):

- ``subject.driver()`` returns a new hosted session driver bound to a new controller of the same terminal host,
  as composition binds it; ``subject.unbound()`` one with no host bound.
- ``subject.spec(key)`` builds a start specification; ``subject.native_id(session)`` reads the conversation's id.
- ``subject.terminal_running(session)`` and ``subject.state_kept(session)`` observe the host and the private state.
- ``subject.lose(session, everything=...)`` kills the session's bridge host outright: with the terminal host as
  a container failure would, or alone as a crashed bridge host would.
- ``subject.crash(key)`` starts a session from a controller process that is then killed; returns its receipt.
- ``subject.unverified()`` returns a driver whose host reports cleanup it could not verify.
- ``subject.failing()`` returns a driver whose wrapped session cannot start.
- ``subject.secret()`` makes a unique text; ``subject.argv_containing(text)`` lists processes whose arguments hold it.

The contract never assumes a terminal mechanism or a receipt layout.
"""


def refuses(code, call):
    try:
        call()
    except Exception as error:  # noqa: BLE001 - any refusal carrying the code satisfies the contract
        assert getattr(error, "code", None) == code, (code, error)
        return error
    raise AssertionError(f"expected refusal {code}")


def attempt(call, codes):
    """``(value, None)`` when ``call()`` returns, ``(None, error)`` when it is refused with one of ``codes``.

    Only the refusals the caller names are outcomes. Any other error, coded or not, propagates, so a runtime
    failure such as a session that cannot start is never read as the outcome a calibrated check asserts on.
    """
    try:
        return call(), None
    except Exception as error:  # noqa: BLE001 - only the named refusals are outcomes; others propagate below
        if getattr(error, "code", None) not in codes:
            raise
        return None, error


UNRECOVERABLE = ("terminal_not_recoverable", "terminal_identity_mismatch")
"""The host's refusals for a terminal it cannot give back; a vanished conversation is reopened instead."""


def converses(driver, session, text):
    reply = driver.send(session, {"text": text})
    assert isinstance(reply, dict) and reply.get("accepted") is True, reply
    return reply


def start_converses_and_detach_only_detaches(subject):
    driver = subject.driver()
    other = None
    try:
        session = driver.start(subject.spec("talk"))
        converses(driver, session, "hello")
        assert subject.terminal_running(session)
        driver.detach(session)
        assert subject.terminal_running(session), "HOSTED_DETACH_ONLY_DETACHES"
        driver.close()
        assert subject.terminal_running(session), "closing the runtime only detaches"
        other = subject.driver()
        again = other.reconnect(session, subject.spec("talk"))
        assert subject.native_id(again) == subject.native_id(session), "reconnect reaches the same conversation"
        converses(other, again, "still here")
        other.stop(again)
    finally:
        driver.close()
        if other is not None:
            other.close()


def stop_closes_the_terminal_keeps_the_state_and_resume_reopens_it(subject):
    driver = subject.driver()
    try:
        session = driver.start(subject.spec("pause"))
        converses(driver, session, "before")
        result = driver.stop(session)
        assert result["terminal"] == {"closed": True, "cleanup_verified": True}, result
        assert not subject.terminal_running(session), "stop ends the conversation's terminal"
        assert subject.state_kept(session), "HOSTED_STOP_KEEPS_PRIVATE_STATE"
        resumed = driver.resume(session, subject.spec("pause"))
        assert subject.terminal_running(resumed)
        assert subject.native_id(resumed) == subject.native_id(session), "resume continues the same conversation"
        converses(driver, resumed, "after")
        driver.stop(resumed)
    finally:
        driver.close()


class _Loss:
    """A conversation started and then lost: with its terminal host as a container failure would, or alone."""

    def __init__(self, subject, everything):
        self.subject, self.other = subject, None
        self.driver = subject.driver()
        self.session = self.driver.start(subject.spec("loss"))
        converses(self.driver, self.session, "before the loss")
        self.driver.close()  # the application went with the failure as well
        subject.lose(self.session, everything=everything)
        assert not subject.terminal_running(self.session)

    def reconnect(self):
        self.other = self.subject.driver()
        return self.other.reconnect(self.session, self.subject.spec("loss"))

    def continues(self, reopened):
        assert self.subject.native_id(reopened) == self.subject.native_id(self.session), "with the same state"
        converses(self.other, reopened, "after the loss")
        self.other.stop(reopened)

    def close(self):
        self.driver.close()
        if self.other is not None:
            self.other.close()


def reconnect_reopens_a_vanished_conversation_without_manual_cleanup(subject):
    loss = _Loss(subject, everything=True)
    try:
        reopened, refused = attempt(loss.reconnect, UNRECOVERABLE)
        assert refused is None and subject.terminal_running(reopened), "HOSTED_RECONNECT_REOPENS_VANISHED"
        loss.continues(reopened)
    finally:
        loss.close()


def reconnect_replaces_a_crashed_bridge_host_in_a_living_terminal_host(subject):
    loss = _Loss(subject, everything=False)
    try:
        reopened = loss.reconnect()
        assert subject.terminal_running(reopened), "a crashed bridge host is replaced in its living terminal host"
        loss.continues(reopened)
    finally:
        loss.close()


def a_session_survives_its_killed_controller(subject):
    receipt = subject.crash("survivor")
    driver = subject.driver()
    try:
        assert subject.terminal_running(receipt), "the conversation outlives the controller that started it"
        again = driver.reconnect(receipt, subject.spec("survivor"))
        assert subject.native_id(again) == subject.native_id(receipt)
        converses(driver, again, "after the controller died")
        driver.stop(again)
    finally:
        driver.close()


def unverified_cleanup_is_reported_and_nothing_is_removed(subject):
    driver = subject.unverified()
    try:
        session = driver.start(subject.spec("unclean"))
        result = driver.stop(session)
        assert result["terminal"]["cleanup_verified"] is False, "HOSTED_UNVERIFIED_CLEANUP_REPORTED"
        assert subject.state_kept(session), "nothing is removed when cleanup could not be verified"
    finally:
        driver.close()


def a_failed_start_keeps_its_output_private_and_says_where_the_log_is(subject):
    """#63: the caller learns that it failed and where the private log is; terminal output stays in that log."""
    driver = subject.failing()
    try:
        error = refuses("session_start_failed", lambda: driver.start(subject.spec("broken")))
        details = getattr(error, "details", {})
        assert not subject.failure_output(details), "HOSTED_FAILED_START_KEEPS_OUTPUT_PRIVATE"
        assert subject.log_exists(details), f"the log location is returned and exists: {details}"
        assert subject.log_has_output(details), "what the session printed is kept in the private log"
    finally:
        driver.close()


def the_specification_never_reaches_process_arguments(subject):
    """A goal is the user's words: it travels privately, never on any command line another user can read."""
    driver = subject.driver()
    secret = subject.secret()
    try:
        session = driver.start({**subject.spec("private"), "goal": secret})
        converses(driver, session, "hello")
        assert subject.argv_containing(secret) == [], "HOSTED_SPECIFICATION_NEVER_IN_ARGUMENTS"
        driver.stop(session)
        resumed = driver.resume(session, subject.spec("private"))
        assert subject.argv_containing(secret) == [], "nor after a resume"
        driver.stop(resumed)
    finally:
        driver.close()


def without_a_bound_host_start_is_refused(subject):
    driver = subject.unbound()
    try:
        refuses("terminal_host_unavailable", lambda: driver.start(subject.spec("nowhere")))
    finally:
        driver.close()


CHECKS = (start_converses_and_detach_only_detaches, stop_closes_the_terminal_keeps_the_state_and_resume_reopens_it,
          reconnect_reopens_a_vanished_conversation_without_manual_cleanup,
          reconnect_replaces_a_crashed_bridge_host_in_a_living_terminal_host, a_session_survives_its_killed_controller,
          unverified_cleanup_is_reported_and_nothing_is_removed,
          a_failed_start_keeps_its_output_private_and_says_where_the_log_is, without_a_bound_host_start_is_refused,
          the_specification_never_reaches_process_arguments)
