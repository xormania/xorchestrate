"""Terminal host port: one behavioural contract for every adapter and the memory fake."""

import contextlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from tests.hexagonal.contracts import terminal_host as contract
from tests.hexagonal.support.host_processes import arguments_containing, track
from tests.hexagonal.support.terminal_host import adapter_subject, memory_subject
from xorchestrate.instance.doctor import terminal_host_check
from xorchestrate.instance.home import Home
from xorchestrate.instance.services import Services

CONTROLLER_VARIABLE = "XO_CONTROLLER_ONLY"

MEMORY_VARIANTS = {
    "surviving-interactive": {"survives_controller": True, "interactive": True},
    "surviving-detached": {"survives_controller": True, "interactive": False},
    "owned-interactive": {"survives_controller": False, "interactive": True},
}


@pytest.fixture(params=sorted(MEMORY_VARIANTS))
def memory(request, tmp_path, monkeypatch):
    monkeypatch.setenv(CONTROLLER_VARIABLE, "must not reach the command")
    return memory_subject(tmp_path, **MEMORY_VARIANTS[request.param])


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
def test_memory_host_meets_the_contract(memory, check):
    check(memory)


def test_memory_host_keeps_the_environment_exact(memory):
    contract.environment_is_exact(memory, CONTROLLER_VARIABLE)


# -- the real adapters ------------------------------------------------------------

ADAPTERS = {
    "multiplexer": ("xorchestrate.adapters.tmux:TmuxTerminalHost", "socket", lambda base, name: f"{base}/{name}.sock"),
    "detached-process": ("xorchestrate.adapters.process_terminal:DetachedProcessHost", "location",
                         lambda base, name: f"{base}/{name}"),
}
# What each adapter declares it adds to the command's exact environment.
ADDITIONS = {"multiplexer": {"TERM"}, "detached-process": set()}
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=[(adapter, owned) for adapter in sorted(ADAPTERS) for owned in (False, True)],
                ids=lambda param: f"{param[0]}-{'owned' if param[1] else 'surviving'}")
def adapter(request, tmp_path, monkeypatch):
    monkeypatch.setenv(CONTROLLER_VARIABLE, "must not reach the command")
    factory, key, location = ADAPTERS[request.param[0]]
    base = track(tempfile.mkdtemp(prefix="xth-"))  # short: socket paths are capped near 107 bytes
    used = set()

    def options_for(name):
        used.add(name)
        return {"name": name, key: location(base, name), "owned": request.param[1], "grace": 2}

    subject = adapter_subject(factory, options_for, track(tmp_path), root=ROOT,
                              placement_path=lambda name: location(base, name),
                              additions=ADDITIONS[request.param[0]])
    readiness = subject.factory("probe").readiness()
    if readiness is not None:
        pytest.skip(f"{request.param[0]} unavailable: {readiness}")
    yield subject
    for name in sorted(used):
        with contextlib.suppress(Exception):
            subject.factory(name).shutdown()
    shutil.rmtree(base, ignore_errors=True)


@pytest.mark.parametrize("check", contract.CHECKS, ids=lambda check: check.__name__)
def test_adapters_meet_the_contract(adapter, check):
    check(adapter)


def test_adapters_keep_the_environment_exact(adapter):
    contract.environment_is_exact(adapter, CONTROLLER_VARIABLE)


def test_environment_values_never_reach_process_arguments(adapter, monkeypatch):
    """Variables often carry secrets: they travel by environment, never by any argv the adapter starts."""
    import secrets
    import subprocess

    secret = "xo-secret-" + secrets.token_hex(12)
    started = []
    original = subprocess.Popen.__init__

    def spy(self, args, *rest, **options):
        started.append([str(part) for part in (args if isinstance(args, (list, tuple)) else [args])])
        return original(self, args, *rest, **options)

    monkeypatch.setattr(subprocess.Popen, "__init__", spy)
    host = adapter.host("secrets")
    try:
        host.open_terminal("env", adapter.programs.environment({"XO_TOKEN": secret}))
        output = contract.shows(adapter, host, "env", "env-done")
        assert f"env:XO_TOKEN={secret}" in output, "the variable must still reach the command"
        assert started, "the spy saw nothing the adapter started"
        assert not [argv for argv in started if any(secret in part for part in argv)], started
        assert arguments_containing(secret) == []
    finally:
        host.shutdown()


# -- selection: the configured host, its visible fallback, or a refusal ------------

UNREADY_MULTIPLEXER = {"adapter": "tmux", "options": {"binary": "/nonexistent/multiplexer"}}


@pytest.fixture
def home():
    base = Path(track(tempfile.mkdtemp(prefix="xth-")))  # short: the probe places a socket under the home
    try:
        yield Home(base / "home")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def select(home, setting):
    home.ensure()
    if setting is not None:
        home.update_settings(terminal_host=setting)
    servers = home.root / "servers"
    servers.mkdir(mode=0o700, exist_ok=True)
    placement = {"name": "selection", "location": str(servers / "selection.terminals"),
                 "socket": str(servers / "selection.sock")}
    return Services(home).terminal_host(placement)


def test_selection_uses_the_configured_host(home):
    active = select(home, {"adapter": "detached-process"})
    try:
        assert active.report() == {"adapter": "detached-process", "selected": "detached-process",
                                   "fallback_reason": None, "survives_controller": True, "interactive": False}
    finally:
        active.host.close()


def test_selection_defaults_to_the_multiplexer_with_a_fallback(home):
    active = select(home, None)
    try:
        report = active.report()
        assert report["selected"] == "tmux"
        if shutil.which("tmux"):
            assert report["adapter"] == "tmux" and report["fallback_reason"] is None
            assert report["interactive"] is True and report["survives_controller"] is True
        else:
            assert report["adapter"] == "detached-process" and report["fallback_reason"]
    finally:
        active.host.close()


def test_an_unready_host_uses_the_fallback_and_says_why(home):
    active = select(home, {**UNREADY_MULTIPLEXER, "fallback": {"adapter": "detached-process"}})
    try:
        report = active.report()
        assert (report["adapter"], report["selected"]) == ("detached-process", "tmux")
        assert isinstance(report["fallback_reason"], str) and report["fallback_reason"], report
    finally:
        active.host.close()


def test_an_unready_host_without_a_fallback_is_refused(home):
    contract.refuses("terminal_host_unavailable", lambda: select(home, UNREADY_MULTIPLEXER))


@pytest.mark.parametrize("setting", ["tmux", {"options": {}}, {"adapter": "no-such-host"},
                                     {"adapter": "tmux", "fallback": None},
                                     {"adapter": "tmux", "fallback": "detached-process"},
                                     {"adapter": "detached-process", "options": {"unknown": 1}},
                                     {"adapter": "tmux", "options": {"binary": "relative/path"}}],
                         ids=lambda value: json.dumps(value))
def test_malformed_selection_is_a_configuration_error(home, setting):
    # ``configuration_error`` is the shared adapter registry's code for an unknown adapter name.
    contract.refuses(("invalid_configuration", "configuration_error"), lambda: select(home, setting))


@pytest.mark.parametrize("setting, status", [({"adapter": "detached-process"}, "ok"),
                                             ({**UNREADY_MULTIPLEXER, "fallback": {"adapter": "detached-process"}},
                                              "warning"),
                                             (UNREADY_MULTIPLEXER, "error")],
                         ids=["ok", "fallback", "unavailable"])
def test_doctor_reports_the_active_host(home, setting, status):
    home.update_settings(terminal_host=setting)
    check = terminal_host_check({"XORCHESTRATE_HOME": str(home.root)})
    assert check["check"] == "terminal_host" and check["status"] == status, check
    if status == "error":
        assert check["code"] == "terminal_host_unavailable", check
    else:
        assert check["adapter"] == "detached-process" and check["interactive"] is False, check
        assert (check["fallback_reason"] is None) == (status == "ok"), check


# -- the multiplexer is not hardwired --------------------------------------------
# The architecture contract forbids the exact literal in roles that never reach
# composition and limits importers of the adapter role. This scan covers what it
# cannot: substrings (commands, f-strings, comments) in every product module.

MULTIPLEXER_ALLOWED = {"xorchestrate/adapters/tmux.py", "xorchestrate/composition.py"}


def multiplexer_mentions(root):
    found = []
    for path in sorted((root / "xorchestrate").rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        if relative in MULTIPLEXER_ALLOWED:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "tmux" in line.lower():
                found.append(f"{relative}:{number}")
    return found


def test_only_the_adapter_and_its_registry_entry_name_the_multiplexer():
    assert multiplexer_mentions(Path(__file__).resolve().parents[2]) == []


def test_the_multiplexer_scan_finds_a_planted_mention(tmp_path):
    for relative, text in (("xorchestrate/adapters/tmux.py", "TMUX = 'tmux'\n"),
                           ("xorchestrate/composition.py", "BUILTINS = {'tmux': ...}\n"),
                           ("xorchestrate/server.py", "argv = [f'{binary} -S x']\n"),
                           ("xorchestrate/instance/operations.py", "# start the Tmux server here\n")):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text, encoding="utf-8")
    assert multiplexer_mentions(tmp_path) == ["xorchestrate/instance/operations.py:1"]
