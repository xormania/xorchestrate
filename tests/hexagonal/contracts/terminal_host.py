"""Independent expectations for the terminal host port, from its documented behaviour.

Every check receives a ``subject`` (see ``tests.hexagonal.support.terminal_host``):

- ``subject.host(name)`` returns a new controller of the host called ``name``;
  two calls with one name are two controllers of the same host.
- ``subject.programs`` builds commands for the deterministic peer programs.
- ``subject.wait(condition, timeout)`` polls until ``condition()`` is true.
- ``subject.log_path(name)`` returns a fresh private path for a terminal log and
  ``subject.read_log(path)`` its text.
- ``subject.traces(host_name)`` lists what of the host is still running or
  bound (processes, endpoints); empty once nothing is left.
- ``subject.unrelated()`` starts a live process the host never launched
  (``.alive()``, ``.stop()``) and ``subject.forge(receipt, process)`` rewrites a
  receipt's identity evidence to name it.
- ``subject.make_unsafe(host_name)`` loosens the host's private location.
- ``subject.altered(receipt)`` yields copies of a receipt, each with one piece
  of identity evidence changed.
- ``subject.run_attach(argv, text)`` runs an attach command as an operator's
  terminal would, until ``text`` is seen, then ends that client; true if seen.
- ``subject.race(host_name, names, command)`` opens one terminal per name, each
  from its own controller process, all at once; returns ``(receipts, errors)``.
- ``subject.kill(host_name, receipt)`` kills the terminal's process outright, as a crash would.
- ``subject.look(host_name, receipt)`` calls ``attach_instruction_for`` from a
  separate controller process and returns the argv, or the refusal's code.
- ``subject.additions`` names the variables the adapter declares it adds.
- ``subject.crash(host_name, terminal_name, command)`` opens a terminal from a
  separate controller process that is then killed outright, and returns the
  receipt it saved.

Expectations follow the host's own capability flags, ``survives_controller``
and ``interactive``, never an adapter name.

The contract never assumes a mechanism: no multiplexer, socket, process
identifier or receipt layout appears here.
"""

INVALID_NAMES = ("", ".hidden", "../escape", "a/b", "spaced name", "semi;colon")
PORT_CODES = frozenset({"invalid_arguments", "terminal_host_unavailable", "terminal_exists", "terminal_unknown",
                        "terminal_not_recoverable", "terminal_identity_mismatch", "terminal_operation_unsupported"})


class Guarded:
    """A host whose every refusal must carry one of the port's codes; anything else fails the check."""

    def __init__(self, host):
        self._host = host

    def __getattr__(self, attribute):
        value = getattr(self._host, attribute)
        if not callable(value):
            return value

        def call(*arguments, **options):
            try:
                return value(*arguments, **options)
            except Exception as error:
                code = getattr(error, "code", None)
                assert code in PORT_CODES, f"{attribute} raised {type(error).__name__} {code!r}: {error}"
                raise
        return call


def refuses(code, call):
    try:
        call()
    except Exception as error:  # noqa: BLE001 - any refusal carrying the code satisfies the contract
        codes = code if isinstance(code, tuple) else (code,)
        assert getattr(error, "code", None) in codes, (code, error)
        return error
    raise AssertionError(f"expected refusal {code}")


def consistent(terminal):
    """``running`` excludes any exit; an exit has at most one way."""
    if terminal.running:
        assert terminal.exit_status is None and terminal.exit_signal is None, terminal
    else:
        assert terminal.exit_status is None or terminal.exit_signal is None, terminal
    return terminal


def observe(host, name):
    found = [consistent(item) for item in host.terminals() if item.name == name]
    assert len(found) == 1, (name, host.terminals())
    return found[0]


def stopped_by_signal(terminal):
    """An adopted terminal may not be the controller's child, so how it ended can be unknowable."""
    assert terminal.exit_status is None, terminal
    return True


def exited(subject, host, name, timeout=10):
    subject.wait(lambda: not observe(host, name).running, timeout)
    return observe(host, name)


def shows(subject, host, name, text, timeout=10):
    subject.wait(lambda: text in host.recent_output(name), timeout)
    return host.recent_output(name)


def readiness_never_raises(subject):
    host = subject.host("ready")
    try:
        assert host.readiness() is None, "the subject is expected to run on this machine"
    finally:
        host.shutdown()


def names_and_listing(subject):
    """Names are validated, unique per host, listed sorted; unknown names are refused."""
    host = subject.host("names")
    programs = subject.programs
    try:
        for name in INVALID_NAMES:
            refuses("invalid_arguments", lambda: host.open_terminal(name, programs.waits()))
        for name in ("beta", "alpha.1", "a_b-c"):
            opened = consistent(host.open_terminal(name, programs.waits()))
            assert opened.name == name and opened.running
        assert [item.name for item in host.terminals()] == ["a_b-c", "alpha.1", "beta"]
        refuses("terminal_exists", lambda: host.open_terminal("beta", programs.waits()))
        for call in (lambda: host.interrupt("ghost"), lambda: host.recent_output("ghost"),
                     lambda: host.close_terminal("ghost")):
            refuses("terminal_unknown", call)
        either = "terminal_unknown" if host.interactive else ("terminal_unknown", "terminal_operation_unsupported")
        refuses(either, lambda: host.send_input("ghost", "x\n"))
        refuses(either, lambda: host.attach_instruction("ghost"))
    finally:
        host.shutdown()


def exit_codes_and_output(subject):
    """Exit codes are reported; output stays readable after exit, bounded in lines, until closed."""
    host = subject.host("exits")
    programs = subject.programs
    try:
        host.open_terminal("fails", programs.exits(3))
        host.open_terminal("prints", programs.lines(40))
        failed = exited(subject, host, "fails")
        assert (failed.exit_status, failed.exit_signal) == (3, None), failed
        printed = exited(subject, host, "prints")
        assert (printed.exit_status, printed.exit_signal) == (0, None), printed
        tail = [line for line in host.recent_output("prints", 5).splitlines() if line.strip()]
        assert tail and len(tail) <= 5, tail
        assert tail[-1] == "line-40" and "line-35" not in tail, tail
        assert "line-1\n" in host.recent_output("prints", 200) + "\n"
        assert [item.name for item in host.terminals()] == ["fails", "prints"], "exited terminals stay listed"
        closure = host.close_terminal("prints")
        assert closure.cleanup_verified, closure
        assert consistent(closure.terminal).name == "prints" and not closure.terminal.running
        assert closure.terminal.exit_status == 0
        assert [item.name for item in host.terminals()] == ["fails"]
        refuses("terminal_unknown", lambda: host.recent_output("prints"))
    finally:
        host.shutdown()


def input_reaches_the_command(subject):
    host = subject.host("input")
    try:
        host.open_terminal("echo", subject.programs.echoes())
        if not host.interactive:
            shows(subject, host, "echo", "ready")
            refuses("terminal_operation_unsupported", lambda: host.send_input("echo", "x\n"))
            return
        shows(subject, host, "echo", "ready")
        host.send_input("echo", "hello there\n")
        shows(subject, host, "echo", "got:hello there")
        # Input is text, never key names or command syntax, even when a whole call spells one.
        for piece in ("Enter", "C-c", ";", "\\;", "Escape", "$HOME"):
            host.send_input("echo", piece)
            host.send_input("echo", "\n")
            shows(subject, host, "echo", f"got:{piece}")
        host.send_input("echo", "Enter C-c ; $HOME\n")
        shows(subject, host, "echo", "got:Enter C-c ; $HOME")
        assert observe(host, "echo").running
        host.send_input("echo", "quit\n")
        done = exited(subject, host, "echo")
        assert (done.exit_status, done.exit_signal) == (0, None), done
    finally:
        host.shutdown()


def environment_is_exact(subject, controller_variable):
    """The command sees exactly the caller's variables plus the adapter's declared additions.

    ``controller_variable`` names a variable the subject has set in the
    controller's own environment.
    """
    host = subject.host("environment")
    given = {"XO_GIVEN": "kept value", "XO_OTHER": "2", "LANG": "C.UTF-8"}  # LANG: no interpreter coercion
    try:
        host.open_terminal("env", subject.programs.environment(given))
        output = shows(subject, host, "env", "env-done")
        seen = dict(line[4:].split("=", 1) for line in output.splitlines() if line.startswith("env:"))
        assert f"env:{controller_variable}=" not in output, "the controller's environment leaked"
        assert {key: seen.get(key) for key in given} == given, seen
        assert set(seen) - set(given) <= set(subject.additions), sorted(set(seen) - set(given))
    finally:
        host.shutdown()


def sizes_follow_the_command(subject):
    host = subject.host("sizes")
    try:
        host.open_terminal("sized", subject.programs.size(rows=30, columns=100))
        output = shows(subject, host, "sized", "size:")
        expected = "size:30x100" if host.interactive else "size:none"
        assert expected in output, output
    finally:
        host.shutdown()


def interrupt_and_forced_close(subject):
    """An interrupt stops interruptible work by signal; close forces work that ignores it."""
    host = subject.host("interrupt")
    programs = subject.programs
    try:
        host.open_terminal("waits", programs.waits())
        host.open_terminal("stubborn", programs.stubborn())
        shows(subject, host, "waits", "ready")
        shows(subject, host, "stubborn", "ready")
        host.interrupt("waits")
        stopped = exited(subject, host, "waits")
        assert stopped.exit_signal is not None and stopped.exit_status is None, stopped
        host.interrupt("waits")  # interrupting finished work is harmless
        host.interrupt("stubborn")
        assert observe(host, "stubborn").running, "an ignored interrupt leaves the work running"
        closure = host.close_terminal("stubborn", grace=0.5)
        assert closure.cleanup_verified and not consistent(closure.terminal).running, closure
        assert [item.name for item in host.terminals()] == ["waits"]
    finally:
        host.shutdown()


def orderly_close_lets_the_command_stop_its_children(subject):
    """The grace goes to the terminal's own command first, so it can stop its children in order."""
    host = subject.host("orderly")
    log = subject.log_path("orderly")
    try:
        host.open_terminal("orderly", subject.programs.orderly(), log=log)
        shows(subject, host, "orderly", "ready")
        closure = host.close_terminal("orderly", grace=5)
        assert closure.cleanup_verified, closure
        lines = [line.strip() for line in subject.read_log(log).splitlines()]
        assert "term:child-signalled=False" in lines, f"the host signalled the command's children itself: {lines}"
    finally:
        host.shutdown()


def attach_by_receipt_takes_no_control(subject):
    """Another process gets a working attach from a verified receipt; the terminal stays with its controller."""
    host = subject.host("look")
    other = subject.host("look-elsewhere")
    victim = subject.unrelated()
    try:
        work = host.open_terminal("work", subject.programs.waits())
        shows(subject, host, "work", "ready")
        if not host.interactive:
            assert subject.look("look", work.receipt) == "terminal_operation_unsupported"
            return
        argv = subject.look("look", work.receipt)
        assert isinstance(argv, list) and argv, argv
        assert subject.run_attach(argv, "ready"), "the looked-up attach shows the terminal"
        assert observe(host, "work").running, "the terminal stays running under its controller"
        assert [item.name for item in host.terminals()] == ["work"]
        host.interrupt("work")  # still controlled by the controller that opened it
        assert exited(subject, host, "work").exit_signal is not None
        stale = work.receipt
        host.close_terminal("work")
        host.open_terminal("work", subject.programs.waits())
        assert subject.look("look", stale) in ("terminal_identity_mismatch", "terminal_not_recoverable")
        assert subject.look("look", subject.forge(stale, victim)) in ("terminal_identity_mismatch",
                                                                     "terminal_not_recoverable")
        foreign = other.open_terminal("work", subject.programs.waits()).receipt
        assert subject.look("look", foreign) == "terminal_identity_mismatch"
        assert victim.alive()
    finally:
        victim.stop()
        other.shutdown()
        host.shutdown()


def attach(subject):
    host = subject.host("attach")
    try:
        host.open_terminal("work", subject.programs.waits())
        if host.interactive:
            for target in ("work", None):
                instruction = host.attach_instruction(target)
                assert instruction.argv and all(isinstance(part, str) and part for part in instruction.argv)
                assert isinstance(instruction.text, str) and instruction.text
            assert host.attach_instruction("work") != host.attach_instruction(None)
            shows(subject, host, "work", "ready")
            assert subject.run_attach(host.attach_instruction("work").argv, "ready"), "attach showed the terminal"
            assert observe(host, "work").running, "ending the attached client leaves the terminal running"
        else:
            for target in ("work", None):
                refuses("terminal_operation_unsupported", lambda: host.attach_instruction(target))
    finally:
        host.shutdown()


def ownership_and_adoption(subject):
    """Controllers see only what they opened or adopted; adoption verifies identity."""
    first = subject.host("owner")
    programs = subject.programs
    second, released = None, False
    try:
        kept = first.open_terminal("kept", programs.waits())
        reopened = first.open_terminal("reused", programs.waits())
        old_receipt = reopened.receipt
        first.close_terminal("reused")
        first.open_terminal("reused", programs.waits())
        shows(subject, first, "kept", "ready")
        second = subject.host("owner")
        assert second.terminals() == [], "another controller's terminals are not controllable"
        refuses("terminal_unknown", lambda: second.interrupt("kept"))
        refuses("terminal_unknown", lambda: second.close_terminal("kept"))
        if not first.survives_controller:
            refuses("terminal_not_recoverable", lambda: second.adopt(kept.receipt))
            return
        stranger = subject.host("stranger")
        try:
            refuses("terminal_identity_mismatch", lambda: stranger.adopt(kept.receipt))
        finally:
            stranger.shutdown()
        refuses("terminal_identity_mismatch", lambda: second.adopt(old_receipt))
        first.close()
        released = True
        adopted = consistent(second.adopt(kept.receipt))
        assert adopted.name == "kept" and adopted.running, adopted
        assert [item.name for item in second.terminals()] == ["kept"]
        assert "ready" in second.recent_output("kept")
        refuses("terminal_exists", lambda: second.adopt(kept.receipt))
        second.interrupt("kept")
        assert stopped_by_signal(exited(subject, second, "kept"))
        assert second.close_terminal("kept").cleanup_verified
        refuses("terminal_not_recoverable", lambda: second.adopt(kept.receipt))
    finally:
        if not released:
            first.shutdown()
        if second is not None:
            second.shutdown()


def log_outlives_the_terminal(subject):
    host = subject.host("logs")
    log = subject.log_path("printed")
    try:
        host.open_terminal("printed", subject.programs.lines(12), log=log)
        exited(subject, host, "printed")
        assert host.close_terminal("printed").cleanup_verified
        lines = [line.strip() for line in subject.read_log(log).splitlines() if line.strip()]
        assert "line-1" in lines and "line-12" in lines, lines
    finally:
        host.shutdown()


def survives_a_killed_controller(subject):
    """The central claim: a surviving host keeps work running when its controller dies outright."""
    probe = subject.host("crash")
    try:
        if not probe.survives_controller:
            return
        receipt = subject.crash("crash", "work", subject.programs.waits())
        other = subject.crash("crash", "other", subject.programs.waits())
        foreign = subject.crash("foreign", "work", subject.programs.waits())
        assert probe.terminals() == []
        refuses("terminal_identity_mismatch", lambda: probe.adopt(foreign))
        adopted = consistent(probe.adopt(receipt))
        assert adopted.running and adopted.name == "work", adopted
        assert "ready" in shows(subject, probe, "work", "ready")
        assert consistent(probe.adopt(other)).running
        probe.interrupt("work")
        assert stopped_by_signal(exited(subject, probe, "work"))
        assert probe.close_terminal("work").cleanup_verified
    finally:
        probe.shutdown()
        subject.host("foreign").shutdown()


def an_empty_host_ends(subject):
    """A host does not outlive its last terminal: closing it leaves no process and no endpoint."""
    host = subject.host("empty")
    try:
        host.open_terminal("first", subject.programs.waits())
        host.open_terminal("second", subject.programs.lines(3))
        shows(subject, host, "first", "ready")
        assert host.close_terminal("first").cleanup_verified
        assert host.terminals(), "the host still has a terminal"
        assert host.close_terminal("second").cleanup_verified
        try:
            subject.wait(lambda: not subject.traces("empty"), 10)
        except AssertionError:
            raise AssertionError(f"the empty host left {subject.traces('empty')}") from None
        host.open_terminal("again", subject.programs.lines(1))  # an ended host starts again on demand
        exited(subject, host, "again")
        assert host.close_terminal("again").cleanup_verified
        subject.wait(lambda: not subject.traces("empty"), 10)
    finally:
        host.shutdown()


def adopt_refuses_a_process_the_host_did_not_launch(subject):
    """A receipt naming an unrelated live process is refused, and that process is left alone."""
    first = subject.host("impostor")
    second = None
    victim = subject.unrelated()
    try:
        receipt = first.open_terminal("work", subject.programs.waits()).receipt
        if not first.survives_controller:
            return
        second = subject.host("impostor")
        forged = subject.forge(receipt, victim)
        assert forged != receipt, "the subject could not forge this receipt"
        refuses("terminal_identity_mismatch", lambda: second.adopt(forged))
        variants = list(subject.altered(receipt))
        assert variants, "the subject offered no altered receipts"
        for variant in variants:
            refuses(("terminal_identity_mismatch", "terminal_not_recoverable"), lambda: second.adopt(variant))
        assert second.terminals() == []
        assert consistent(second.adopt(receipt)).running, "the genuine receipt is still adopted"
        second.close()
        second.shutdown()
        second = None
        assert victim.alive(), "the host signalled a process it never launched"
    finally:
        victim.stop()
        (second or first).shutdown()


def names_are_reusable_once_exited(subject):
    """A name is refused only while a terminal of that name runs; an exited one is replaced."""
    host = subject.host("reuse")
    try:
        host.open_terminal("job", subject.programs.exits(4))
        assert exited(subject, host, "job").exit_status == 4
        replaced = consistent(host.open_terminal("job", subject.programs.lines(2)))
        assert replaced.name == "job"
        assert exited(subject, host, "job").exit_status == 0, "the new command runs under the old name"
        assert "line-2" in host.recent_output("job") and [item.name for item in host.terminals()] == ["job"]
        host.open_terminal("busy", subject.programs.waits())
        refuses("terminal_exists", lambda: host.open_terminal("busy", subject.programs.waits()))
        if not host.survives_controller:
            return
        subject.crash("reuse", "left-exited", subject.programs.exits(5))
        running = subject.crash("reuse", "left-running", subject.programs.waits())
        fresh = subject.host("reuse")
        try:
            reopened = consistent(fresh.open_terminal("left-exited", subject.programs.lines(1)))
            assert reopened.name == "left-exited", "an exited terminal another controller left is replaced"
            error = refuses("terminal_exists", lambda: fresh.open_terminal("left-running", subject.programs.waits()))
            if fresh.interactive:
                attach = getattr(error, "details", {}).get("attach")
                assert attach, f"a running terminal that cannot be replaced says how to attach: {error.details}"
            assert consistent(fresh.adopt(running)).running, "the running terminal was left untouched"
        finally:
            fresh.shutdown()
    finally:
        host.shutdown()


def an_unsafe_location_is_reported(subject):
    host = subject.host("unsafe")
    try:
        host.open_terminal("work", subject.programs.waits())
        subject.make_unsafe("unsafe")
        expected = "terminal_host_unavailable" if host.interactive else "terminal_operation_unsupported"
        for target in ("work", None):
            error = refuses(expected, lambda: host.attach_instruction(target))
            if host.interactive:
                assert error.details.get("reason"), error.details
    finally:
        host.shutdown()


def inventory_sees_everything_and_takes_nothing(subject):
    """inventory() lists every terminal at the host, running or exited, held or not, without taking control."""
    host = subject.host("inventory")
    probe = None
    try:
        host.open_terminal("mine", subject.programs.waits())
        host.open_terminal("done", subject.programs.exits(0))
        shows(subject, host, "mine", "ready")
        exited(subject, host, "done")
        expected = {"mine": (True, True), "done": (False, True)}
        if host.survives_controller:
            subject.crash("inventory", "orphan", subject.programs.waits())
            expected["orphan"] = (True, False)
        seen = host.inventory()
        assert [item.name for item in seen] == sorted(expected), seen
        assert {item.name: item.running for item in seen} == {name: running for name, (running, _) in expected.items()}, seen
        assert {item.name: item.held for item in seen} == {name: held for name, (_, held) in expected.items()}, \
            "TERMINAL_HOST_INVENTORY_REPORTS_WHO_HOLDS"
        if host.survives_controller:
            probe = subject.host("inventory")
            assert {item.name: item.held for item in probe.inventory()} == dict.fromkeys(expected, False)
            assert probe.terminals() == [], "inventory takes no control"
            assert [item.name for item in host.terminals()] == ["done", "mine"]
    finally:
        if probe is not None:
            probe.close()
        host.shutdown()


def sweep_removes_only_exited_terminals_nobody_here_holds(subject):
    host = subject.host("sweep")
    try:
        host.open_terminal("held-exited", subject.programs.exits(0))
        exited(subject, host, "held-exited")
        if not host.survives_controller:
            assert host.sweep() == [] and [item.name for item in host.terminals()] == ["held-exited"]
            return
        subject.crash("sweep", "orphan-exited", subject.programs.exits(0))
        subject.crash("sweep", "orphan-running", subject.programs.waits())
        subject.wait(lambda: any(item.name == "orphan-exited" and not item.running for item in host.inventory()), 10)
        swept = host.sweep()
        assert "orphan-running" not in swept, "TERMINAL_HOST_SWEEP_REMOVES_ONLY_EXITED_ORPHANS"
        assert swept == ["orphan-exited"], swept
        names = {item.name: item.running for item in host.inventory()}
        assert names == {"held-exited": False, "orphan-running": True}, names
        assert host.sweep() == [], "sweeping twice removes nothing more"
    finally:
        host.shutdown()


def a_crashed_terminal_stays_listed_until_swept(subject):
    """A terminal whose process is killed while no controller holds it is still at the host, exited, until swept."""
    probe = subject.host("crashed")
    try:
        if not probe.survives_controller:
            return
        alone = subject.crash("crashed", "alone", subject.programs.waits())
        subject.kill("crashed", alone)  # the host's only terminal
        subject.wait(lambda: {item.name: item.running for item in probe.inventory()}.get("alone") is False, 10)
        assert probe.sweep() == ["alone"], "a lone crashed terminal is still at the host, exited, until swept"
        receipt = subject.crash("crashed", "victim", subject.programs.waits())
        subject.crash("crashed", "bystander", subject.programs.waits())
        subject.kill("crashed", receipt)
        subject.wait(lambda: {item.name: item.running for item in probe.inventory()}.get("victim") is False, 10)
        assert {item.name: item.running for item in probe.inventory()} == {"victim": False, "bystander": True}
        assert probe.sweep() == ["victim"]
        assert [item.name for item in probe.inventory()] == ["bystander"]
    finally:
        probe.shutdown()


def the_first_open_is_atomic(subject):
    """Controllers racing to open a fresh surviving host's first terminals share one host; neither is lost."""
    probe = subject.host("race")
    try:
        if not probe.survives_controller:
            return
        receipts, errors = subject.race("race", ("left", "right"), subject.programs.waits())
        assert errors == [] and sorted(receipts) == ["left", "right"], errors
        assert {item.name: item.running for item in probe.inventory()} == {"left": True, "right": True}
        processes = [trace for trace in subject.traces("race") if trace.startswith("process")]
        assert len(processes) <= 1, f"one host serves both terminals: {processes}"
    finally:
        probe.shutdown()


def shutdown_stops_everything(subject):
    host = subject.host("shutdown")
    try:
        receipt = host.open_terminal("work", subject.programs.waits()).receipt
        host.open_terminal("more", subject.programs.stubborn())
        shows(subject, host, "more", "ready")
    finally:
        host.shutdown()
    subject.wait(lambda: not subject.traces("shutdown"), 10)
    later = subject.host("shutdown")
    try:
        assert later.terminals() == []
        if later.survives_controller:
            refuses("terminal_not_recoverable", lambda: later.adopt(receipt))
    finally:
        later.shutdown()


def shutdown_reaches_terminals_of_other_controllers(subject):
    """``shutdown`` stops every terminal of the host, including ones no live controller holds."""
    probe = subject.host("orphans")
    try:
        if not probe.survives_controller:
            return
        orphan = subject.crash("orphans", "orphan", subject.programs.waits())
        released = subject.host("orphans")
        kept = released.open_terminal("released", subject.programs.waits()).receipt
        released.close()
    finally:
        probe.shutdown()
    later = subject.host("orphans")
    try:
        for receipt in (orphan, kept):
            refuses("terminal_not_recoverable", lambda: later.adopt(receipt))
    finally:
        later.shutdown()


CHECKS =(readiness_never_raises, names_and_listing, exit_codes_and_output, input_reaches_the_command,
          sizes_follow_the_command, interrupt_and_forced_close, attach, ownership_and_adoption,
          log_outlives_the_terminal, survives_a_killed_controller, an_empty_host_ends, shutdown_stops_everything,
          shutdown_reaches_terminals_of_other_controllers, adopt_refuses_a_process_the_host_did_not_launch,
          names_are_reusable_once_exited, an_unsafe_location_is_reported,
          orderly_close_lets_the_command_stop_its_children, attach_by_receipt_takes_no_control,
          inventory_sees_everything_and_takes_nothing, sweep_removes_only_exited_terminals_nobody_here_holds,
          the_first_open_is_atomic, a_crashed_terminal_stays_listed_until_swept)
