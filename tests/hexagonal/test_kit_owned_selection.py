"""The test kit selects what a cleanup may kill by provenance, never by name alone.

``ownership.owned_processes(root, marker, *, spawned=(), within=None)`` selects this user's processes that
carry an exact environment marker, that the caller spawned, that listen on a socket bound inside ``root``, or
that descend from one of those; ``within`` only narrows. An ownerless ``close`` ends a process only when it
both names the run and is selected by the run's marker; anything else it finds is reported, never ended.

Every process here is started by the test itself and stopped in its own cleanup.
"""

import importlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.hexagonal.support.host_processes import track

ROOT = Path(__file__).resolve().parents[2]
MARKER_KEY = "XORCHESTRATE_PROOF_DIRECTORY"
SLEEP = "import time; time.sleep(120)"


def ownership():
    return importlib.import_module("scripts.testkit.adapters.ownership")


def composition():
    return importlib.import_module("scripts.testkit.operator_composition")


class Started:
    """Processes this test started; every one is stopped at the end."""

    def __init__(self):
        self.processes = []

    def __call__(self, *extra, env=None, cwd=None, program=SLEEP):
        environment = {name: value for name, value in os.environ.items() if name != MARKER_KEY}
        environment.update(env or {})
        process = subprocess.Popen([sys.executable, "-c", program, *map(str, extra)], env=environment,
                                   cwd=cwd or tempfile.gettempdir(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes.append(process)
        return process

    def identity(self, pid):
        return ownership().processes()[pid][0]

    def stop(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


@pytest.fixture
def started():
    tracker = Started()
    yield tracker
    tracker.stop()


def alive(identity):
    return ownership().alive(identity)


def pid_from(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not (path.exists() and path.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.05)
    return int(path.read_text())


# The adapter -------------------------------------------------------------------------------------

@pytest.fixture
def root():
    """A short private directory, so socket paths under it stay within the Unix limit whatever pytest's base."""
    import shutil
    directory = Path(track(tempfile.mkdtemp(prefix="xo-root-")))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def select(root, marker, **options):
    return ownership().owned_processes(root, marker, **options)


def test_a_process_carrying_the_exact_marker_is_selected_and_a_lookalike_is_not(root, started):
    marker = (MARKER_KEY, str(root))
    exact = started.identity(started(env={MARKER_KEY: str(root)}).pid)
    lookalikes = [started.identity(started(env={MARKER_KEY: value}).pid)
                  for value in (str(root) + "x", str(root) + "/nested", "/prefix" + str(root))]
    named = started.identity(started(str(root)).pid)  # names the root, carries no marker
    chosen = select(root, marker).selected
    assert chosen.get(exact) == "home", chosen
    assert not any(item in chosen for item in [*lookalikes, named]), chosen


def test_spawned_and_listening_processes_are_selected_by_their_own_signal(root, started, tmp_path):
    marker = (MARKER_KEY, str(root))
    spawned = started()
    ready = tmp_path / "ready"
    listener = started(root / "listen.sock", ready, program=(
        "import socket, sys, time; s = socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.listen();"
        "open(sys.argv[2], 'w').write('ready'); time.sleep(120)"))
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    chosen = select(root, marker, spawned=[spawned]).selected
    assert chosen.get(started.identity(spawned.pid)) == "spawned", chosen
    assert chosen.get(started.identity(listener.pid)) == "socket", chosen


@pytest.mark.parametrize("kind", ["missing", "file", "symlink"])
def test_a_root_that_is_not_a_real_directory_of_this_user_is_refused(kind, tmp_path, started):
    target = tmp_path / "real"
    target.mkdir()
    path = {"missing": tmp_path / "absent", "file": tmp_path / "plain", "symlink": tmp_path / "link"}[kind]
    if kind == "file":
        path.write_text("x")
    elif kind == "symlink":
        path.symlink_to(target)
    started(env={MARKER_KEY: str(path)})
    with pytest.raises(ValueError, match="refusing"):
        select(path, (MARKER_KEY, str(path)))


PARENT = ("import os, subprocess, sys, time;"
          f"env = {{k: v for k, v in os.environ.items() if k != {MARKER_KEY!r}}};"
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], env=env);"
          "open(sys.argv[1], 'w').write(str(child.pid)); time.sleep(120)")
"""A parent whose child carries no marker, so the child can only be selected as a descendant."""


def test_within_narrows_and_never_reaches_a_descendant_outside_it(root, started, tmp_path):
    marker = (MARKER_KEY, str(root))
    parent = started(tmp_path / "child.pid", env={MARKER_KEY: str(root)}, program=PARENT)
    child_pid = pid_from(tmp_path / "child.pid")
    try:
        parent_identity, child = started.identity(parent.pid), started.identity(child_pid)
        free = select(root, marker)
        assert free.selected.get(child) == "descendant" and free.ancestors.get(child) == parent_identity, free
        narrowed = select(root, marker, within=[parent_identity])
        assert parent_identity in narrowed.selected and child not in narrowed.selected, narrowed
        assert select(root, marker, within=[child]).selected == {}, "within selected something by itself"
    finally:
        os.kill(child_pid, signal.SIGKILL)


CALLER = ("import json, sys; sys.path.insert(0, sys.argv[1]);"
          "from scripts.testkit.adapters import ownership;"
          "chosen = ownership.owned_processes(sys.argv[2], (sys.argv[3], sys.argv[2])).selected;"
          "open(sys.argv[4], 'w').write(json.dumps(sorted(identity.pid for identity in chosen)))")
WRAPPER = ("import os, subprocess, sys;"
           "subprocess.run([sys.executable, '-c', sys.argv[1], *sys.argv[2:6]], check=False);"
           "open(sys.argv[6], 'w').write(str(os.getpid()))")


def test_the_caller_and_its_ancestors_are_never_selected(root, started, tmp_path):
    result, wrapper_pid = tmp_path / "selected.json", tmp_path / "wrapper.pid"
    wrapper = started(CALLER, ROOT, root, MARKER_KEY, result, wrapper_pid, env={MARKER_KEY: str(root)},
                      program=WRAPPER)
    wrapper.wait(timeout=60)
    assert result.exists(), "the selection inside the wrapped caller did not run"
    chosen = json.loads(result.read_text())
    assert int(wrapper_pid.read_text()) not in chosen, "a selection named the caller's own parent"


# Ownerless close ---------------------------------------------------------------------------------

@pytest.fixture
def run(tmp_path, monkeypatch):
    """A finished run directory with its derived endpoint; host building is stubbed: only processes matter."""
    terminals = importlib.import_module("scripts.testkit.adapters.terminals")
    selection = importlib.import_module("scripts.testkit.adapters.terminal_selection")
    directory = Path(track(tempfile.mkdtemp(prefix="xo-run-")))
    endpoint = terminals.create_endpoint(directory, composition().ENDPOINT_PREFIX)
    (directory / "plan.json").write_text(json.dumps({"terminal_endpoint": str(endpoint)}))
    monkeypatch.setattr(selection, "build", lambda *args, **options: selection.SelectedHost(None, None, "none"))
    yield directory
    import shutil
    shutil.rmtree(endpoint, ignore_errors=True)
    shutil.rmtree(directory, ignore_errors=True)


def close(run):
    return composition().close_without_supervisor(run, {})


def test_close_ends_the_runs_own_leftovers_and_their_descendants(run, started, tmp_path):
    own = started(run, env={MARKER_KEY: str(run)})
    parent = started(tmp_path / "child.pid", run, env={MARKER_KEY: str(run)}, program=PARENT.replace(
        "[sys.executable, '-c', 'import time; time.sleep(120)']",
        "[sys.executable, '-c', 'import time; time.sleep(120)', sys.argv[2]]"))
    child_marker = (Path("/proc") / pid_from(tmp_path / "child.pid").__str__() / "environ").read_bytes()
    assert MARKER_KEY.encode() + b"=" not in child_marker, "the leftover's child must carry no marker"
    child_pid = pid_from(tmp_path / "child.pid")
    identities = [started.identity(pid) for pid in (own.pid, parent.pid, child_pid)]
    try:
        result = close(run)
        assert not any(alive(item) for item in identities), "a leftover of the run survived its close"
        assert result["cleanup"]["remaining_processes"] == 0, result
    finally:
        if alive(identities[2]):
            os.kill(child_pid, signal.SIGKILL)


def test_close_leaves_an_unmarked_process_that_only_names_the_run_and_reports_it(run, started):
    named = started.identity(started(run).pid)
    result = close(run)
    assert alive(named), "close ended a process only because it named the run"
    assert result["cleanup"]["remaining_processes"] == 1 and not result["cleanup"]["verified"], result


def test_close_reports_a_marked_process_that_does_not_name_the_run_without_ending_it(run, started):
    marked = started.identity(started(env={MARKER_KEY: str(run)}).pid)
    result = close(run)
    assert alive(marked), "close ended a process the name rule did not reach"
    assert result["cleanup"]["remaining_processes"] == 1 and not result["cleanup"]["verified"], result


HELD_CHILD = ("import os, signal, sys\n"
              "if sys.argv[3] == 'ignores-term':\n"
              "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
              "hold, ready = int(sys.argv[1]), int(sys.argv[2])\n"
              "os.write(ready, b'%d\\n' % os.getpid()); os.close(ready)\n"
              "os.read(hold, 1)\n")
"""A child that names the run (its last argument) and lives until the test closes the hold pipe, never longer."""
HELD_PARENT = ("import os, subprocess, sys\n"
               "hold, ready = int(sys.argv[1]), int(sys.argv[2])\n"
               f"env = {{k: v for k, v in os.environ.items() if k != {MARKER_KEY!r}}}\n"
               f"env.update({{{MARKER_KEY!r}: ''}} if sys.argv[5] == 'blank' else {{}})\n"
               "subprocess.Popen([sys.executable, '-c', sys.argv[3], str(hold), str(ready), sys.argv[4], sys.argv[6]],\n"
               "                 env=env, pass_fds=(hold, ready), stdin=subprocess.DEVNULL)\n"
               "os.close(ready)\n"
               "os.read(hold, 1)\n")
"""A marked parent that names the run and starts one ``HELD_CHILD`` whose marker is removed (``none``) or blanked
as for a non-live check command (``blank``); both live until the test closes the hold pipe."""


class Family:
    """A marked parent naming the run and its one child, held on a pipe this test owns; ended by identity."""

    def __init__(self, run, child, child_marker):
        hold_read, self.hold = os.pipe()
        ready_read, ready_write = os.pipe()
        environment = {name: value for name, value in os.environ.items() if name != MARKER_KEY}
        environment[MARKER_KEY] = str(run)
        self.parent = subprocess.Popen(
            [sys.executable, "-c", HELD_PARENT, str(hold_read), str(ready_write), HELD_CHILD, child, child_marker,
             str(run)], env=environment, pass_fds=(hold_read, ready_write), cwd=tempfile.gettempdir(),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(hold_read)
        os.close(ready_write)
        try:
            table = ownership().processes()
            self.parent_identity = table[self.parent.pid][0]
            answer = os.read(ready_read, 64)
        finally:
            os.close(ready_read)
        assert answer, "the child did not start"
        self.child = ownership().processes()[int(answer)][0]

    def end(self):
        for identity in (self.child, self.parent_identity):
            ownership().send_signal(identity, signal.SIGKILL)
        os.close(self.hold)
        self.parent.wait(timeout=10)


@pytest.fixture
def families():
    made = []
    yield lambda *arguments: made.append(Family(*arguments)) or made[-1]
    for family in made:
        family.end()
    ours = [item for family in made for item in (family.child, family.parent_identity)]
    deadline = time.monotonic() + 10  # an allowance for SIGKILL to land, not a timing claim
    while any(alive(item) for item in ours) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(alive(item) for item in ours), "a process this test started outlived it"


class QuietHost:
    """A terminal host that is up and has nothing to shut down, so ``verified`` turns on processes alone."""

    survives_controller, interactive = True, False

    def adopt(self, receipt):
        raise AssertionError("nothing to adopt")

    def shutdown(self):
        pass

    def close(self):
        pass


def test_a_second_close_still_counts_a_marked_leftover_after_the_endpoint_is_gone(run, started, monkeypatch):
    """The first close reports a marked process it may not end and removes the endpoint; a second close must
    still count that process as remaining and not report the run as verified clean."""
    selection = importlib.import_module("scripts.testkit.adapters.terminal_selection")
    monkeypatch.setattr(selection, "build", lambda *args, **options: selection.SelectedHost(QuietHost(), "quiet", "quiet"))
    marked = started.identity(started(env={MARKER_KEY: str(run)}).pid)
    first = close(run)
    assert first["cleanup"]["remaining_processes"] == 1 and "errors" not in first["cleanup"], first
    second = close(run)
    assert alive(marked), "close ended a process the name rule did not reach"
    assert second["cleanup"]["remaining_processes"] == 1, f"a live marked leftover went uncounted: {second}"
    assert not second["cleanup"]["verified"], f"a close with a live marked leftover reported verified: {second}"


def test_close_ends_a_selected_child_that_ignores_sigterm_after_its_parent_has_gone(run, families):
    """Close selects a marked parent and its child (by descent) in its first round. Its own SIGTERM ends the
    parent, which cuts the child's parent link; the child ignores SIGTERM, and close must still end it."""
    family = families(run, "ignores-term", "none")
    result = close(run)
    assert not alive(family.parent_identity), "close did not end the marked parent"
    assert not alive(family.child), f"a child close had selected survived its escalation: {result}"


def test_close_reports_an_orphaned_check_command_whose_worker_died_without_ending_it(run, families):
    """A check command (blanked marker, names the run) whose worker died before close has no provenance left
    that close established: it is reported as remaining, never ended."""
    family = families(run, "plain", "blank")
    ownership().send_signal(family.parent_identity, signal.SIGKILL)
    family.parent.wait(timeout=10)
    result = close(run)
    assert alive(family.child), "close ended an orphaned check command it never selected"
    assert result["cleanup"]["remaining_processes"] == 1 and not result["cleanup"]["verified"], result


def test_close_leaves_another_runs_marked_process_whose_name_contains_this_run(run, started):
    other = str(run) + "-other"
    foreign = started.identity(started(other, env={MARKER_KEY: other}).pid)
    close(run)
    assert alive(foreign), "close ended another run's process"


CLOSER = ("import json, sys; sys.path.insert(0, sys.argv[1]);"
          "from scripts.testkit.adapters import terminal_selection as s;"
          "s.build = lambda *a, **o: s.SelectedHost(None, None, 'none');"
          "from scripts.testkit.operator_composition import close_without_supervisor;"
          "open(sys.argv[3], 'w').write(json.dumps(close_without_supervisor(sys.argv[2], {})))")
CLOSING_PARENT = ("import os, subprocess, sys;"
                  "subprocess.run([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]], check=False);"
                  "open(sys.argv[5], 'w').write('parent survived')")


def test_close_never_signals_its_own_ancestors(run, started, tmp_path):
    result, survived = tmp_path / "close.json", tmp_path / "survived"
    parent = started(CLOSER, ROOT, run, result, survived, env={MARKER_KEY: str(run)}, program=CLOSING_PARENT)
    parent.wait(timeout=90)
    assert survived.read_text() == "parent survived", "close signalled the process that started it"
    assert result.exists(), json.loads(result.read_text()) if result.exists() else "close did not finish"


# The world delegates its selection to the kit ----------------------------------------------------------

def test_the_end_to_end_world_selects_through_the_kit(monkeypatch):
    world_support = importlib.import_module("tests.hexagonal.support.e2e_world")
    contract = importlib.import_module("tests.hexagonal.contracts.e2e_world")
    real, calls = ownership().owned_processes, []

    def recording(root, marker, **options):
        calls.append((str(root), marker, options))
        return real(root, marker, **options)

    monkeypatch.setattr(ownership(), "owned_processes", recording)
    with world_support.world("detached-process") as world:
        world.save()
        world.up()
        home = str(world.cli.home)
        world.disturb(contract.TotalLoss())
    assert any(root == home and marker == ("XORCHESTRATE_HOME", home) for root, marker, _ in calls), calls


def test_a_root_owned_by_another_user_is_refused(started):
    foreign = Path(tempfile.gettempdir())
    if foreign.stat().st_uid == os.getuid():
        pytest.skip("the temporary directory belongs to this user here; no foreign directory to use")
    with pytest.raises(ValueError, match="refusing"):
        select(foreign, (MARKER_KEY, str(foreign)))


def test_close_without_its_endpoint_ends_nothing_and_reports_what_it_found(run, started):
    import shutil
    terminals = importlib.import_module("scripts.testkit.adapters.terminals")
    shutil.rmtree(terminals.run_endpoint(run, composition().ENDPOINT_PREFIX))
    leftover = started.identity(started(run, env={MARKER_KEY: str(run)}).pid)
    result = close(run)
    assert alive(leftover), "close ended a process with no endpoint left to anchor its provenance"
    assert result["cleanup"]["remaining_processes"] == 1 and not result["cleanup"]["verified"], result
