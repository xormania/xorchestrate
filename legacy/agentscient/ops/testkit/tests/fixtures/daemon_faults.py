"""Planted faults in world.py that test_world_daemon.py must catch; run
them with

    python3 ops/testkit/cases.py calibrate --subject ops/testkit/world.py \\
        --mutants ops/testkit/tests/fixtures/daemon_faults.py \\
        --command "python3 -m unittest discover -s ops/testkit/tests -p test_world_daemon.py"
"""

from ops.testkit import Mutant

WORLD = "ops/testkit/world.py"

MUTANTS = [
    Mutant("M-daemon-not-stopped", WORLD, "for daemon in reversed(self._daemons):",
           "for daemon in reversed(()):", "close never stops a recorded daemon"),
    Mutant("M-daemon-stop-trusted", WORLD,
           'if daemon.gone(daemon.timeout) and socket_state(daemon.socket) != "accepting":',
           "if True:", "close takes the stop's word and never checks the daemon went down"),
    Mutant("M-daemon-sigkill-first", WORLD, "for signum in (signal.SIGTERM, signal.SIGKILL):",
           "for signum in (signal.SIGKILL,):", "the escalation skips SIGTERM"),
    Mutant("M-daemon-no-sweep", WORLD, "refusals.append(sweep_daemons(self))",
           "refusals.append(None)", "close never looks for a daemon started around the kit"),
    Mutant("M-daemon-home-blind", WORLD,
           'return {pid for pid in pids if marker in _read(pid, "environ").split(b"\\0")}',
           "return set()", "no process is ever seen carrying the world's HOME"),
    Mutant("M-adopt-socket-blind", WORLD, "if len(fields) == 8 and fields[7] in names:",
           "if False:", "adopt never finds the holder of a socket"),
]
