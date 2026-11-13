"""One Serena MCP server per work tree over local HTTP: up only when it answers a real symbol query.

No real Serena runs. A fake launcher answers on the port the tool chose with a stdlib
streamable-HTTP MCP endpoint, or logs and dies, or stays silent. A fake clock carries
every timeout and the stop grace, and a fake process table receives every signal.
"""

import json
import os
import signal
import sys
import threading
import time

import pytest

from tests.hexagonal.contracts import worktrees as contract
from tests.hexagonal.support.git_repositories import commit, git
from tests.hexagonal.support.worktrees import (
    CRASH_TEXT,
    PROBE_FILE,
    FakeLauncher,
    RawEndpoint,
    Workspace,
    json_reply,
    server_argv,
    tool,
    truncated_reply,
)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path, monkeypatch)
    yield workspace
    workspace.close()


def codes(result):
    return contract.codes_in(result.text, contract.SERENA_CODES | contract.CHECK_CODES)


def port_of(argv):
    return int(argv[argv.index("--port") + 1])


def calls(ws, method):
    return [request for request in ws.mcp.requests if (request["body"] or {}).get("method") == method]


def up(ws, branch="served", **kwargs):
    path = ws.new(branch)
    result = ws.run("serena", "up", branch, **kwargs)
    return path, result


# up -----------------------------------------------------------------------------------------------

def test_up_launches_the_default_command_in_the_tree_and_records_a_ready_server(ws):
    path, result = up(ws)
    assert result.code == 0, result.text
    (spawn,) = ws.launcher.spawns
    port = port_of(spawn["argv"])
    assert spawn["argv"] == contract.serena_argv(contract.DEFAULT_SERENA_VERSION, contract.DEFAULT_SERENA_CONTEXT,
                                                  path, port)
    assert spawn["cwd"] == str(path)
    assert spawn["env"]["SERENA_HOME"] == str(ws.state / "serena-home")
    assert spawn["log"].startswith(str(ws.state / "logs") + "/") and spawn["log"].endswith(".log")
    state = ws.saved("served")
    assert (state.tree, state.port, state.head, state.version) == (str(path), port, git(path, "rev-parse", "HEAD"),
                                                                   contract.DEFAULT_SERENA_VERSION)
    assert state.pid in ws.processes.procs and state.last_call is None
    assert git(path, "status", "--porcelain", "--ignored") == "", "the tool wrote into the tree"
    assert not (ws.home / ".serena").exists() and not list(ws.home.rglob(".claude")), "the user's home was touched"


def test_ready_means_initialized_then_a_symbol_query_on_the_first_python_file_with_a_definition(ws):
    _, result = up(ws)
    assert result.code == 0, result.text
    (initialize,) = calls(ws, "initialize")
    assert initialize["body"]["params"]["protocolVersion"] == contract.PROTOCOL_VERSION
    assert calls(ws, "notifications/initialized")
    probes = calls(ws, "tools/call")
    assert probes and probes[-1]["body"]["params"] == {"name": contract.PROBE_TOOL,
                                                       "arguments": {"relative_path": PROBE_FILE}}
    assert len({request["session"] for request in ws.mcp.requests if request["session"]}) >= 1


def test_the_server_gets_the_given_environment_plus_its_own_home_and_never_file_values(ws):
    (ws.main / ".devtools.env").write_text("XORCHESTRATE_DEV_SERENA_CONTEXT=from-file\n")
    env = ws.env(UNRELATED_VARIABLE="kept")
    ws.new("served")
    result = ws.run("serena", "up", "served", env=env)
    assert result.code == 0, result.text
    (spawn,) = ws.launcher.spawns
    assert spawn["argv"][spawn["argv"].index("--context") + 1] == "from-file"
    assert spawn["env"] == {**env, "SERENA_HOME": str(ws.state / "serena-home")}


def test_a_configured_command_and_probe_replace_the_defaults(ws):
    env = ws.env(XORCHESTRATE_DEV_SERENA_COMMAND="my-serena --root {project} --port {port} --pin {version}",
                 XORCHESTRATE_DEV_SERENA_VERSION="7.7.7", XORCHESTRATE_DEV_PROBE_TOOL="find_symbol",
                 XORCHESTRATE_DEV_PROBE_ARGS='{"name_path": "Model"}')
    path = ws.new("custom")
    result = ws.run("serena", "up", "custom", env=env)
    assert result.code == 0, result.text
    (spawn,) = ws.launcher.spawns
    assert spawn["argv"] == ["my-serena", "--root", str(path), "--port", str(port_of(spawn["argv"])), "--pin", "7.7.7"]
    assert calls(ws, "tools/call")[-1]["body"]["params"] == {"name": "find_symbol", "arguments": {"name_path": "Model"}}
    assert ws.saved("custom").version == "7.7.7"


def test_without_a_probe_file_or_configured_probe_up_refuses_before_launching(tmp_path, monkeypatch):
    bare = Workspace(tmp_path, monkeypatch, files={"README.md": "x\n", "consts.py": "LIMIT = 1\n"})
    try:
        bare.new("plain")
        result = bare.run("serena", "up", "plain")
        assert result.code == 1 and "NO_PROBE" in codes(result), result.text
        assert bare.launcher.spawns == []
    finally:
        bare.close()


@pytest.mark.parametrize("probe", ["error", "empty"])
def test_a_server_that_answers_without_a_real_symbol_result_is_never_ready(ws, probe):
    ws.mcp.probe = probe
    start, wall = ws.clock.now(), time.monotonic()
    _, result = up(ws)
    assert result.code == 1 and "SERVER_FAILED" in codes(result), result.text
    assert len(ws.launcher.spawns) == 1
    pid = 40_000
    assert (pid, signal.SIGTERM) in ws.processes.signals and not ws.processes.alive(pid)
    assert ws.saved("served") is None and ws.state_files() == []
    assert ws.clock.now() - start >= contract.DEFAULT_TIMEOUT
    assert time.monotonic() - wall < 30, "waiting must go through the clock"


def test_a_silent_server_fails_at_the_configured_timeout_and_is_stopped(ws):
    ws.launcher.script = ["silent"]
    start = ws.clock.now()
    path = ws.new("served")
    result = ws.run("serena", "up", "served", env=ws.env(XORCHESTRATE_DEV_SERENA_TIMEOUT="30"))
    assert result.code == 1 and "SERVER_FAILED" in codes(result), result.text
    assert 30 <= ws.clock.now() - start < contract.DEFAULT_TIMEOUT
    assert ws.processes.groups_signalled(signal.SIGTERM) == [40_000]
    assert ws.saved("served") is None and path.is_dir()


def test_a_server_that_exits_early_fails_with_its_log_tail_and_no_retry(ws):
    ws.launcher.script = ["exit"]
    _, result = up(ws)
    assert result.code == 1 and "SERVER_FAILED" in codes(result), result.text
    assert CRASH_TEXT.splitlines()[-1] in result.text
    assert len(ws.launcher.spawns) == 1 and ws.saved("served") is None


def test_an_address_in_use_is_retried_on_a_new_port_up_to_three_tries(ws):
    ws.launcher.script = ["address_in_use", "address_in_use"]
    _, result = up(ws)
    assert result.code == 0, result.text
    ports = [port_of(spawn["argv"]) for spawn in ws.launcher.spawns]
    assert len(ports) == contract.PORT_TRIES and len(set(ports)) == contract.PORT_TRIES, ports
    assert ws.saved("served").port == ports[-1]


def test_three_addresses_in_use_fail_without_a_fourth_try(ws):
    ws.launcher.script = ["address_in_use"] * 4
    _, result = up(ws)
    assert result.code == 1 and "SERVER_FAILED" in codes(result), result.text
    assert len(ws.launcher.spawns) == contract.PORT_TRIES and ws.saved("served") is None


def test_without_serena_up_says_so_and_the_tree_commands_still_work(ws):
    ws.launcher.script = ["missing"]
    _, result = up(ws)
    assert result.code == 1 and "SERENA_UNAVAILABLE" in codes(result), result.text
    assert "XORCHESTRATE_DEV_SERENA_COMMAND" in result.text
    assert ws.state_files() == []
    assert ws.run("new", "still-works").code == 0
    assert ws.run("remove", "still-works").code == 0


def test_up_on_a_live_answering_server_at_the_same_head_launches_nothing(ws):
    _, first = up(ws)
    assert first.code == 0, first.text
    second = ws.run("serena", "up", "served")
    assert second.code == 0, second.text
    assert "already up" in second.text.lower() and len(ws.launcher.spawns) == 1


# down ---------------------------------------------------------------------------------------------

def family(ws, path, *, stray=False):
    """A server (7300), its child in its group, and a language server in a group of its own."""
    state = ws.seed_server(path, pid=7_300)
    ws.processes.add(7_301, ppid=7_300, pgid=7_300, cwd=path, argv=["python", "serena-worker"])
    ws.processes.add(7_302, ppid=7_301, pgid=9_100 if stray else 7_302, cwd=path, argv=["node", "pyright"])
    if stray:
        ws.processes.add(9_100, ppid=1, pgid=9_100, cwd=ws.base, argv=["bash"])
    ws.processes.add(9_000, ppid=1, pgid=9_000, cwd=path, argv=["unrelated"])
    return state


def test_down_terminates_the_server_group_and_its_descendants_groups_then_forgets_it(ws):
    path = ws.new("served")
    family(ws, path)
    result = ws.run("serena", "down", "served")
    assert result.code == 0, result.text
    assert ws.processes.groups_signalled(signal.SIGTERM)[:1] == [7_300]
    assert set(ws.processes.groups_signalled()) == {7_300, 7_302}, ws.processes.signals
    assert ws.processes.alive(9_000), "an unrelated process was signalled"
    assert ws.saved("served") is None


def test_down_kills_what_survives_the_grace_measured_on_the_clock(ws):
    path = ws.new("served")
    family(ws, path)
    ws.processes.ignore_term.add(7_300)
    start = ws.clock.now()
    result = ws.run("serena", "down", "served")
    assert result.code == 0, result.text
    assert ws.clock.now() - start >= contract.GRACE_SECONDS
    assert (7_300, signal.SIGKILL) in ws.processes.signals
    kill = ws.processes.signals.index((7_300, signal.SIGKILL))
    assert all(sig == signal.SIGTERM for _, sig in ws.processes.signals[:kill])
    assert not ws.processes.alive(7_300) and ws.saved("served") is None


def test_down_never_signals_a_group_shared_with_an_unrelated_process(ws):
    path = ws.new("served")
    family(ws, path, stray=True)
    result = ws.run("serena", "down", "served")
    assert result.code == 1, result.text
    assert 9_100 not in ws.processes.groups_signalled() and ws.processes.alive(9_100)
    assert "9100" in result.text
    assert 7_300 in ws.processes.groups_signalled(signal.SIGTERM)
    assert ws.saved("served") is None, "the server itself is gone, so its state is too"


def test_down_without_a_server_is_a_quiet_success(ws):
    ws.new("idle")
    result = ws.run("serena", "down", "idle")
    assert result.code == 0, result.text
    assert ws.processes.signals == []


# ls and reap --------------------------------------------------------------------------------------

def test_ls_shows_each_server_with_its_liveness_and_whether_its_tree_exists(ws):
    alive = ws.new("alive")
    ws.seed_server(alive, pid=7_400, port=5_001)
    ws.seed_server(ws.root / "gone", pid=7_401, port=5_002, head="0" * 40)
    ws.processes.kill(7_401)
    result = ws.run("serena", "ls")
    assert result.code == 0, result.text
    rows = {line.split()[0]: line.split() for line in result.out.splitlines()
            if line.split()[:1] in (["alive"], ["gone"])}
    assert {"5001", "7400", "alive", "yes"} <= set(rows["alive"]), rows
    assert {"5002", "7401", "dead", "no"} <= set(rows["gone"]), rows


def test_reap_forgets_dead_servers_stops_servers_of_missing_trees_and_keeps_healthy_ones(ws):
    healthy, dead, orphan = ws.new("healthy"), ws.new("dead"), ws.new("orphan")
    ws.seed_server(healthy, pid=7_500)
    ws.seed_server(dead, pid=7_510)
    ws.seed_server(orphan, pid=7_520)
    ws.processes.kill(7_510)
    git(ws.main, "worktree", "remove", orphan)
    result = ws.run("serena", "reap")
    assert result.code == 0, result.text
    assert ws.saved("dead") is None and ws.saved("orphan") is None
    assert (7_520, signal.SIGTERM) in ws.processes.signals and not ws.processes.alive(7_520)
    assert ws.saved("healthy") is not None and ws.processes.alive(7_500)
    assert 7_500 not in ws.processes.groups_signalled()
    assert "dead" in result.text and "orphan" in result.text


# call and tools -----------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["json", "sse"])
def test_call_initializes_notifies_calls_and_closes_one_session(ws, mode):
    path, _ = up(ws)
    ws.mcp.mode = mode
    ws.mcp.requests.clear()
    ws.clock.sleep(5)
    result = ws.run("serena", "call", "served", "find_symbol", '{"name_path": "Model"}')
    assert result.code == 0, result.text
    assert '"name_path": "Model"' in result.out or "Model" in result.out
    sequence = [(request["http"], (request["body"] or {}).get("method")) for request in ws.mcp.requests]
    assert sequence == [("POST", "initialize"), ("POST", "notifications/initialized"), ("POST", "tools/call"),
                        ("DELETE", None)], sequence
    assert ws.mcp.requests[0]["body"]["params"]["protocolVersion"] == contract.PROTOCOL_VERSION
    session = {request["session"] for request in ws.mcp.requests[1:]}
    assert len(session) == 1 and None not in session
    assert ws.mcp.requests[2]["body"]["params"] == {"name": "find_symbol", "arguments": {"name_path": "Model"}}
    assert ws.saved("served").last_call == ws.clock.now()


@pytest.mark.parametrize("failure", ["call_error", "rpc_error"])
def test_a_tool_error_or_protocol_error_fails_the_call(ws, failure):
    up(ws)
    setattr(ws.mcp, failure, True)
    result = ws.run("serena", "call", "served", "find_symbol", "{}")
    assert result.code == 1, result.text


@pytest.mark.parametrize("dead", [False, True], ids=["never-up", "dead"])
def test_call_needs_a_live_server(ws, dead):
    path = ws.new("served")
    if dead:
        ws.seed_server(path, pid=7_600)
        ws.processes.kill(7_600)
    result = ws.run("serena", "call", "served", "find_symbol", "{}")
    assert result.code == 1 and "NOT_UP" in codes(result), result.text


def test_tools_prints_one_tool_name_per_line_in_server_order(ws):
    up(ws)
    result = ws.run("serena", "tools", "served")
    assert result.code == 0, result.text
    assert result.out.strip().splitlines() == list(ws.mcp.tools)


# check --------------------------------------------------------------------------------------------

def test_check_passes_for_a_healthy_server(ws):
    up(ws)
    result = ws.run("serena", "check", "served")
    assert result.code == 0, result.text
    assert codes(result) == set()


def _not_up(ws, path):
    ws.store().delete(ws.key("served"))


def _not_answering(ws, path):
    for server in ws.launcher.servers.values():
        server.stop()


def _project_mismatch(ws, path):
    state = ws.saved("served")
    proc = ws.processes.procs[state.pid]
    argv = list(proc.argv)
    argv[argv.index("--project") + 1] = str(ws.main)
    ws.processes.add(proc.pid, ppid=proc.ppid, pgid=proc.pgid, cwd=proc.cwd, argv=argv)


def _multiple_servers(ws, path):
    ws.processes.add(7_700, cwd=path, argv=["serena", "start-mcp-server", "--project", str(path)])


def _project_yml_changed(ws, path):
    (path / ".serena").mkdir(exist_ok=True)
    (path / ".serena" / "project.yml").write_text("changed: true\n")


def _stale_head(ws, path):
    commit(path, "later.txt", "later\n")


CHECKS = {"NOT_UP": _not_up, "NOT_ANSWERING": _not_answering, "PROJECT_MISMATCH": _project_mismatch,
          "MULTIPLE_SERVERS": _multiple_servers, "PROJECT_YML_CHANGED": _project_yml_changed,
          "STALE_HEAD": _stale_head}


@pytest.fixture
def yml_ws(tmp_path, monkeypatch):
    from tests.hexagonal.support.git_repositories import FILES
    workspace = Workspace(tmp_path, monkeypatch, files={**FILES, ".serena/project.yml": "project_name: sample\n"})
    yield workspace
    workspace.close()


@pytest.mark.parametrize("code", sorted(CHECKS))
def test_check_reports_each_failure_by_its_code(yml_ws, code):
    path, result = up(yml_ws)
    assert result.code == 0, result.text
    CHECKS[code](yml_ws, path)
    result = yml_ws.run("serena", "check", "served")
    assert result.code == 1, result.text
    assert code in codes(result), result.text


def test_check_reports_a_version_other_than_the_configured_one(ws):
    up(ws)
    result = ws.run("serena", "check", "served", env=ws.env(XORCHESTRATE_DEV_SERENA_VERSION="9.9.9"))
    assert result.code == 1 and "VERSION_MISMATCH" in codes(result), result.text


def test_check_lists_every_failing_code_at_once(yml_ws):
    path, _ = up(yml_ws)
    _stale_head(yml_ws, path)
    _multiple_servers(yml_ws, path)
    _project_yml_changed(yml_ws, path)
    result = yml_ws.run("serena", "check", "served", env=yml_ws.env(XORCHESTRATE_DEV_SERENA_VERSION="9.9.9"))
    assert result.code == 1
    assert codes(result) >= {"STALE_HEAD", "MULTIPLE_SERVERS", "PROJECT_YML_CHANGED", "VERSION_MISMATCH"}, result.text


def test_state_is_json_the_tool_can_read_back_and_never_written_before_ready(ws):
    ws.launcher.script = ["silent"]
    up(ws)
    assert ws.state_files() == []
    ws.launcher.script = []
    assert ws.run("serena", "up", "served").code == 0
    files = ws.state_files()
    assert files and all(json.loads(path.read_text()) for path in files)


# Server identity: a recorded pid is ours only while the same process holds it ----------------------

IMPOSTOR = 2_345


def impostor(ws, path, *, cwd=None):
    """State for a server at pid 2345, but pid 2345 is now an unrelated tmux with children in its group."""
    state = ws.make_state(path, pid=IMPOSTOR, pgid=IMPOSTOR, port=1, head=git(path, "rev-parse", "HEAD"),
                          version=contract.DEFAULT_SERENA_VERSION, started=ws.clock.now(), last_call=None,
                          argv=tuple(server_argv(path, 1)), start_time=None)
    ws.store().save(ws.key(path), state)
    ws.processes.add(IMPOSTOR, pgid=IMPOSTOR, cwd=cwd or ws.base, argv=["tmux", "new-session", "-d"])
    ws.processes.add(IMPOSTOR + 1, ppid=IMPOSTOR, pgid=IMPOSTOR, cwd=cwd or ws.base, argv=["bash"])
    ws.processes.add(IMPOSTOR + 2, ppid=IMPOSTOR + 1, pgid=IMPOSTOR + 2, cwd=cwd or ws.base, argv=["vim"])
    return state


def unrelated_signalled(ws):
    return [(pgid, sig) for pgid, sig in ws.processes.signals if pgid in (IMPOSTOR, IMPOSTOR + 2)]


@pytest.mark.parametrize("command", ["down", "reap", "up"])
def test_a_recorded_pid_now_running_something_else_is_forgotten_and_never_signalled(ws, command):
    path = ws.new("served")
    impostor(ws, path)
    argv = ("serena", "reap") if command == "reap" else ("serena", command, "served")
    result = ws.run(*argv)
    assert unrelated_signalled(ws) == [], ws.processes.signals
    assert all(ws.processes.alive(pid) for pid in (IMPOSTOR, IMPOSTOR + 1, IMPOSTOR + 2))
    if command == "up":
        assert result.code == 0, result.text
        assert ws.saved("served").pid != IMPOSTOR and len(ws.launcher.spawns) == 1
    else:
        assert result.code == 0, result.text
        assert ws.saved("served") is None, "the stale state was kept"


def legacy_server(ws, path, pid=2_400):
    """A state written without the process identity (no argv, no start time) for a pid that IS a Serena server."""
    state = ws.make_state(path, pid=pid, pgid=pid, port=1, head=git(path, "rev-parse", "HEAD"),
                          version=contract.DEFAULT_SERENA_VERSION, started=ws.clock.now(), last_call=None,
                          argv=(), start_time=None)
    ws.store().save(ws.key(path), state)
    ws.processes.add(pid, pgid=pid, cwd=path, argv=server_argv(path, 1))
    return state


@pytest.mark.parametrize("command", ["down", "reap", "up"])
def test_a_state_without_a_recorded_command_is_never_trusted_even_for_a_serena_server(ws, command):
    path = ws.new("served")
    legacy_server(ws, path)
    argv = ("serena", "reap") if command == "reap" else ("serena", command, "served")
    result = ws.run(*argv)
    assert result.code == 0, result.text
    assert 2_400 not in ws.processes.groups_signalled(), ws.processes.signals
    assert ws.processes.alive(2_400)
    saved = ws.saved("served")
    if command == "up":
        assert saved is not None and saved.pid != 2_400 and len(ws.launcher.spawns) == 1
    else:
        assert saved is None, "the untrusted state was kept"


def test_a_recorded_pid_with_the_same_command_but_another_start_time_is_not_our_server(ws):
    path = ws.new("served")
    ws.seed_server(path, pid=7_300, start_time=111)
    ws.processes.add(7_300, cwd=path, argv=server_argv(path, 1), start_time=222)
    result = ws.run("serena", "down", "served")
    assert result.code == 0, result.text
    assert ws.processes.signals == [] and ws.processes.alive(7_300)
    assert ws.saved("served") is None


def test_a_matching_command_and_start_time_is_our_server(ws):
    path = ws.new("served")
    ws.seed_server(path, pid=7_300, start_time=111)
    result = ws.run("serena", "down", "served")
    assert result.code == 0, result.text
    assert (7_300, signal.SIGTERM) in ws.processes.signals and ws.saved("served") is None


def test_up_records_the_spawned_command_and_the_process_start_time(ws):
    path, result = up(ws)
    assert result.code == 0, result.text
    state = ws.saved("served")
    (spawn,) = ws.launcher.spawns
    assert tuple(getattr(state, "argv", ())) == tuple(spawn["argv"])
    assert getattr(state, "start_time", None) == FakeLauncher.START_TIME_BASE + state.pid


@pytest.mark.parametrize("pgid", [0, 1])
def test_down_never_signals_process_group_zero_or_one(ws, pgid):
    path = ws.new("served")
    ws.seed_server(path, pid=7_300, pgid=pgid)
    result = ws.run("serena", "down", "served")
    assert result.code in (0, 1), result.text
    assert [group for group in ws.processes.groups_signalled() if group <= 1] == [], ws.processes.signals


def test_procfs_reads_the_start_time_and_treats_zombies_as_dead(tmp_path):
    from tests.hexagonal.support.worktrees import fake_proc
    root = fake_proc(tmp_path / "proc", {
        201: (1, 201, "serena", ["serena"], str(tmp_path), []),
        202: (201, 201, "worker", ["worker"], str(tmp_path), []),
        203: (201, 201, "gone", ["gone"], str(tmp_path), []),
    }, states={202: "Z", 203: "X"}, start_times={201: 987654})
    table = tool("adapters.procfs").ProcFs(root)
    found = {proc.pid: proc for proc in table.processes()}
    assert getattr(found[201], "start_time", None) == 987654
    assert table.alive(201) and not table.alive(202) and not table.alive(203)


# Repositories sharing one state directory -----------------------------------------------------------

@pytest.fixture
def pair(tmp_path, monkeypatch):
    alpha = Workspace(tmp_path / "alpha", monkeypatch)
    beta = Workspace(tmp_path / "beta", monkeypatch, share=alpha)
    yield alpha, beta
    alpha.close()


def test_repositories_sharing_a_state_directory_never_touch_each_others_servers(pair):
    alpha, beta = pair
    alpha_tree, beta_tree = alpha.new("feat/x"), beta.new("feat/x")
    assert alpha.run("serena", "up", "feat/x").code == 0
    server = alpha.saved("feat/x")
    assert server is not None and server.tree == str(alpha_tree)
    assert beta.saved("feat/x") is None
    down = beta.run("serena", "down", "feat/x")
    assert down.code == 0, down.text
    removed = beta.run("remove", "feat/x")
    assert removed.code == 0, removed.text
    assert not beta_tree.exists() and alpha_tree.is_dir()
    assert alpha.processes.signals == [] and alpha.processes.alive(server.pid)
    assert alpha.saved("feat/x") == server


def test_repositories_sharing_a_state_directory_keep_separate_servers_and_logs(pair):
    alpha, beta = pair
    alpha.new("feat/x")
    beta.new("feat/x")
    assert alpha.run("serena", "up", "feat/x").code == 0
    second = beta.run("serena", "up", "feat/x")
    assert second.code == 0, second.text
    assert "already up" not in second.text.lower()
    first_spawn, second_spawn = alpha.launcher.spawns
    assert first_spawn["log"] != second_spawn["log"]
    assert alpha.saved("feat/x").pid != beta.saved("feat/x").pid
    listing = alpha.run("serena", "ls")
    assert "feat/x" in [line.split()[0] for line in listing.out.splitlines() if line.strip()], listing.out


def test_a_state_recorded_for_another_tree_is_not_ours(ws, tmp_path):
    path = ws.new("served")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    state = ws.make_state(elsewhere, pid=7_300, pgid=7_300, port=1, head="0" * 40,
                          version=contract.DEFAULT_SERENA_VERSION, started=ws.clock.now(), last_call=None,
                          argv=tuple(server_argv(elsewhere, 1)), start_time=None)
    ws.store().save(ws.key(path), state)
    ws.processes.add(7_300, cwd=elsewhere, argv=server_argv(elsewhere, 1))
    result = ws.run("serena", "down", "served")
    assert result.code == 0, result.text
    assert ws.processes.signals == [] and ws.processes.alive(7_300)


# Readiness: bounded over the real transport, and nothing escapes with a live server -------------------

def run_bounded(ws, seconds, *argv, **kwargs):
    """``ws.run`` in a thread; a run still going after ``seconds`` of wall time is a failure (its endpoints are
    closed first so the thread can finish)."""
    box = {}

    def target():
        try:
            box["result"] = ws.run(*argv, **kwargs)
        except BaseException as error:  # noqa: BLE001 - reported to the test below
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    started = time.monotonic()
    thread.start()
    thread.join(seconds)
    elapsed = time.monotonic() - started
    if thread.is_alive():
        ws.launcher.stop_all()
        thread.join(30)
        pytest.fail(f"{' '.join(argv)} was still waiting after {seconds} s of wall time")
    if "error" in box:
        raise box["error"]
    return box["result"], elapsed


def test_the_readiness_timeout_holds_over_the_real_transport_when_the_endpoint_never_replies(ws):
    ws.launcher.script = ["hang"]
    ws.new("served")
    timeout, slack = 2, 8
    result, elapsed = run_bounded(ws, timeout + slack, "serena", "up", "served",
                                  env=ws.env(XORCHESTRATE_DEV_SERENA_TIMEOUT=str(timeout)))
    assert result.code == 1 and "SERVER_FAILED" in codes(result), result.text
    assert elapsed < timeout + slack
    assert (40_000, signal.SIGTERM) in ws.processes.signals and not ws.processes.alive(40_000)
    assert ws.saved("served") is None


def test_the_transport_post_honours_a_per_call_timeout():
    endpoint = RawEndpoint(0)
    port = endpoint.listener.getsockname()[1]
    try:
        started = time.monotonic()
        with pytest.raises(tool("ports").TransportError):
            tool("adapters.mcp").UrllibMcp().post(f"http://127.0.0.1:{port}/mcp", {"jsonrpc": "2.0"}, None,
                                                  timeout=0.5)
        assert time.monotonic() - started < 5
    finally:
        endpoint.stop()


@pytest.mark.parametrize("reply", [truncated_reply(), json_reply([1, 2])],
                         ids=["truncated-body", "json-list-of-numbers"])
def test_an_unreadable_reply_is_not_ready_and_the_server_is_stopped_at_the_timeout(ws, reply):
    ws.launcher.script = ["raw"]
    ws.launcher.raw_reply = reply
    ws.new("served")
    try:
        result = ws.run("serena", "up", "served", env=ws.env(XORCHESTRATE_DEV_SERENA_TIMEOUT="5"))
    except Exception as error:  # noqa: BLE001 - the defect being pinned
        pytest.fail(f"up raised {error!r}; server alive: {ws.processes.alive(40_000)}, "
                    f"recorded: {ws.saved('served') is not None}")
    assert result.code == 1 and "SERVER_FAILED" in codes(result), result.text
    assert (40_000, signal.SIGTERM) in ws.processes.signals and not ws.processes.alive(40_000)
    assert ws.saved("served") is None


class Exploding:
    """A transport that raises ``error`` on every request."""

    def __init__(self, error):
        self.error = error

    def post(self, url, body, session, timeout=None):
        raise self.error

    def delete(self, url, session):
        pass


@pytest.mark.parametrize("error", [KeyboardInterrupt, RuntimeError], ids=["interrupt", "unexpected"])
def test_any_other_exception_during_readiness_stops_the_spawned_server_and_propagates(ws, error):
    ws.launcher.script = ["silent"]
    ws.new("served")
    with pytest.raises(error):
        ws.run("serena", "up", "served", ports=ws.ports(transport=Exploding(error("planted"))))
    assert (40_000, signal.SIGTERM) in ws.processes.signals and not ws.processes.alive(40_000)
    assert ws.saved("served") is None


# Logs: each spawn attempt starts its own ---------------------------------------------------------------

def test_the_launcher_starts_each_log_afresh(tmp_path):
    log = tmp_path / "logs" / "tree.log"
    log.parent.mkdir()
    log.write_text("ERROR: address already in use\n", encoding="utf-8")
    pid = tool("adapters.launcher").PopenLauncher().spawn(
        [sys.executable, "-c", "print('second attempt')"], str(tmp_path), dict(os.environ), str(log))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            done, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            break
        if done:
            break
        time.sleep(0.05)
    text = log.read_text(encoding="utf-8")
    assert "second attempt" in text and "address already in use" not in text, text


class CountingLauncher:
    def __init__(self):
        self.inner = tool("adapters.launcher").PopenLauncher()
        self.spawns = []

    def spawn(self, argv, cwd, env, log):
        self.spawns.append(list(argv))
        return self.inner.spawn(argv, cwd, env, log)


def test_a_stale_address_in_use_line_in_an_old_log_does_not_cause_a_retry(ws):
    ws.new("served")
    command = f"{sys.executable} -c \"print('planted: another failure')\" {{port}}"
    env = ws.env(XORCHESTRATE_DEV_SERENA_COMMAND=command, XORCHESTRATE_DEV_SERENA_TIMEOUT="30")
    launcher = CountingLauncher()
    real = ws.ports(launcher=launcher, processes=tool("adapters.procfs").ProcFs(), clock=None)
    first = ws.run("serena", "up", "served", env=env, ports=real)
    assert first.code == 1 and "SERVER_FAILED" in codes(first), first.text
    log = first.text.split("; log ", 1)[1].split()[0]
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("ERROR: [Errno 98] address already in use\n")
    launcher.spawns.clear()
    second = ws.run("serena", "up", "served", env=env, ports=real)
    assert second.code == 1 and "SERVER_FAILED" in codes(second), second.text
    assert len(launcher.spawns) == 1, launcher.spawns
    assert "exited before answering" in second.text and "address already in use" not in second.text.lower()


# Locking, stopping and zombies ------------------------------------------------------------------------

def test_two_concurrent_ups_on_one_tree_start_one_server(ws):
    ws.new("served")
    main = tool("__main__").main
    argv = ["--repo", str(ws.main), "--root", str(ws.root), "--state-dir", str(ws.state), "serena", "up", "served"]
    second_reached_spawn = threading.Event()
    first = threading.Event()

    def on_spawn(_argv):
        if not first.is_set():
            first.set()
            second_reached_spawn.wait(2)
        else:
            second_reached_spawn.set()

    ws.launcher.on_spawn = on_spawn
    codes_seen = []

    def target():
        codes_seen.append(main(argv, env=ws.env(), ports=ws.ports()))

    threads = [threading.Thread(target=target, daemon=True) for _ in range(2)]
    threads[0].start()
    assert first.wait(30)
    threads[1].start()
    for thread in threads:
        thread.join(60)
    assert codes_seen == [0, 0]
    assert len(ws.launcher.spawns) == 1, ws.launcher.spawns


def test_down_waits_briefly_on_the_clock_for_a_killed_server_to_die(ws):
    path = ws.new("served")
    ws.seed_server(path, pid=7_300)
    ws.processes.ignore_term.add(7_300)
    ws.processes.linger[7_300] = 0.2
    result = ws.run("serena", "down", "served")
    assert result.code == 0, result.text
    assert (7_300, signal.SIGKILL) in ws.processes.signals
    assert not ws.processes.alive(7_300) and ws.saved("served") is None


def test_a_server_that_survives_sigkill_fails_after_a_bounded_wait(ws):
    path = ws.new("served")
    ws.seed_server(path, pid=7_300)
    ws.processes.ignore_term.add(7_300)
    ws.processes.ignore_kill.add(7_300)
    start = ws.clock.now()
    result = ws.run("serena", "down", "served")
    assert result.code == 1 and "SERVER_FAILED" in codes(result), result.text
    assert ws.clock.now() - start <= 2 * contract.GRACE_SECONDS
    assert ws.saved("served") is not None


def test_zombie_members_do_not_hold_the_grace(ws):
    path = ws.new("served")
    family(ws, path)
    ws.processes.zombie_on_term.add(7_301)
    start = ws.clock.now()
    result = ws.run("serena", "down", "served")
    assert result.code == 0, result.text
    assert ws.clock.now() - start < contract.GRACE_SECONDS
    assert ws.processes.groups_signalled(signal.SIGKILL) == [], ws.processes.signals
    assert ws.saved("served") is None


# A path inside a tree means the tree ---------------------------------------------------------------------

def test_up_on_a_path_inside_a_tree_or_through_a_symlink_serves_the_trees_toplevel(ws):
    path = ws.new("served")
    result = ws.run("serena", "up", str(path / "pkg"))
    assert result.code == 0, result.text
    (spawn,) = ws.launcher.spawns
    assert spawn["cwd"] == os.path.realpath(path)
    assert spawn["argv"][spawn["argv"].index("--project") + 1] == os.path.realpath(path)
    assert ws.saved("served") is not None
    link = ws.base / "link"
    link.symlink_to(path)
    again = ws.run("serena", "up", str(link / "pkg"))
    assert again.code == 0, again.text
    assert "already up" in again.text.lower() and len(ws.launcher.spawns) == 1
