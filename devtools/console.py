"""Operator entry to a native, user-facing CO terminal with shared context."""

import codecs
import errno
import fcntl
import json
import os
import pty
import select
import shutil
import signal
import sys
import termios
import time
import tty
import uuid
from contextlib import ExitStack
from pathlib import Path

from .api import API, SERVER
from .launch import team_config
from .profiles import claude_command, operator_console, prepare, retain_login, toml
from .runtime import co_role, resolve
from .store import Store
from .telemetry import Trace, Transcript, context, requested

MAX_INITIAL_PROMPT_BYTES = 65536


def validate_initial_prompt(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("The initial CO brief must be nonempty UTF-8 text without NUL characters.")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError("The initial CO brief must be valid UTF-8 text.") from error
    if size > MAX_INITIAL_PROMPT_BYTES:
        raise ValueError("The initial CO brief exceeds 64 KiB.")
    return value


def read_brief(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("The initial CO brief must be a readable regular file.")
    with path.open("rb") as stream:
        payload = stream.read(MAX_INITIAL_PROMPT_BYTES + 1)
    if len(payload) > MAX_INITIAL_PROMPT_BYTES:
        raise ValueError("The initial CO brief exceeds 64 KiB.")
    try:
        return validate_initial_prompt(payload.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ValueError("The initial CO brief must be valid UTF-8 text.") from error


def _prompt_adapter(config, *, required=False):
    from .harnesses import get

    adapter = get(config["harness"])
    if required:
        if config.get("operator_console") is not True:
            raise ValueError("An initial CO brief requires an operator console profile.")
        if getattr(adapter, "supports_initial_prompt", False) is not True:
            raise ValueError("The selected harness does not support an initial CO brief: " + config["harness"])
    return adapter


def command(directory, member, config, private, server):
    initial_prompt = validate_initial_prompt(config.get("initial_prompt"))
    return _prompt_adapter(config, required=initial_prompt is not None).command(directory, member, config, private, server)


def _command_builtin(directory, member, config, private, server, *, protocol):
    """Build the appointed operator's TUI; workers use native transports."""
    operator_console(directory, member)
    if config.get("operator_console") is not True:
        raise ValueError("Native console commands require an operator console profile.")
    instructions = API(directory, member).instructions()
    prompt = (
        "Call onboard; it supplies the shared and role instructions. Read only relevant skills not marked loaded. "
        "Recover current work and reports before asking the user what is next."
    )
    initial_prompt = config.get("initial_prompt")
    harness = protocol
    argv, native_id = [config["binary"]], None
    if harness == "codex":
        options = {"mcp_servers." + SERVER: server}
        options.update({"hooks." + name: groups for name, groups in
                        json.loads((private / "observer-hooks.json").read_text()).items()})
        # Do not replace the operator's developer_instructions config.
        prompt = instructions + "\n\n" + prompt
        for source, target in (
            ("model", "model"),
            ("reasoning", "model_reasoning_effort"),
            ("provider", "model_provider"),
        ):
            if config.get(source):
                options[target] = config[source]
        for key, value in options.items():
            argv += ["-c", key + "=" + toml(value)]
        argv += ["--", prompt + ("\n\n" + initial_prompt if initial_prompt is not None else "")]
    elif harness == "claude":
        return claude_command(config, private, instructions, {},
                              prompt + ("\n\n" + initial_prompt if initial_prompt is not None else ""), managed=False)
    elif harness == "grok":
        native_id = str(uuid.uuid4())
        # A shared leader keeps its original environment and cannot inherit
        # this generation's MCP/hook identity from a later TUI client.
        argv += ["--no-leader", "--session-id", native_id, "--rules", instructions + "\n" + prompt]
        for source, flag in (("model", "--model"), ("reasoning", "--effort")):
            if config.get(source):
                argv += [flag, config[source]]
        if initial_prompt is not None:
            # Grok's positional prompt seeds the interactive TUI. --prompt-file
            # and --single instead select headless mode and would close it.
            argv += ["--", initial_prompt]
    else:
        raise ValueError("This builtin console does not implement the selected harness protocol.")
    return argv, native_id


def relay(argv, env, cwd, trace, poll):
    """Transparent PTY relay; capture output, never raw password/key input."""
    from . import processes

    input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
    original = termios.tcgetattr(input_fd) if os.isatty(input_fd) else None
    old_handlers = {}
    pid, master = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        except Exception as error:
            print(str(error), file=sys.stderr, flush=True)
            os._exit(127)

    def resize(*_):
        if original:
            size = fcntl.ioctl(input_fd, termios.TIOCGWINSZ, bytes(8))
            fcntl.ioctl(master, termios.TIOCSWINSZ, size)

    def terminate(*_):
        nonlocal stop_at
        if stop_at is None:
            stop_at = time.monotonic()
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def write_all(fd, data):
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]

    # Keep incomplete lines together so ordinary credentials split across reads
    # are still filtered by Trace.clean. Very large output is bounded.
    decoder, pending = codecs.getincrementaldecoder("utf-8")("replace"), ""
    stop_at, status, input_open = None, None, True
    owned = {}

    def forward(data):
        nonlocal pending
        write_all(output_fd, data)
        pending += decoder.decode(data)
        while "\n" in pending or len(pending) > 65536:
            end = pending.index("\n") + 1 if "\n" in pending else 65536
            trace.record("terminal_output", {"text": pending[:end]})
            pending = pending[end:]

    def observe():
        # The direct child cannot be reused before waitpid reaps it. Record its
        # identity even if it has not yet entered the PTY's new session.
        if status is None and (row := processes.identity(pid)):
            owned[pid] = row
        for row in processes.family(owned.values(), sessions=(pid,)):
            owned[row["pid"]] = row
        return processes.alive(owned.values())

    try:
        trace.record("process_started", {"argv": argv, "pid": pid, "transport": "interactive/pty"})
        for sig, handler in ((signal.SIGWINCH, resize), (signal.SIGTERM, terminate), (signal.SIGHUP, terminate)):
            old_handlers[sig] = signal.signal(sig, handler)
        resize()
        if original:
            tty.setraw(input_fd)
        while True:
            if not poll():
                terminate()
            if stop_at is not None and time.monotonic() - stop_at > 5:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            readable, _, _ = select.select([master, *([input_fd] if input_open else [])], [], [], 0.1)
            if input_fd in readable:
                data = os.read(input_fd, 65536)
                if data:
                    write_all(master, data)
                else:
                    input_open = False
                    terminate()
            if master in readable:
                try:
                    data = os.read(master, 65536)
                except OSError as error:
                    if error.errno != errno.EIO:
                        raise
                    data = b""
                if not data:
                    break
                forward(data)
            observe()
            waited, value = os.waitpid(pid, os.WNOHANG)
            if waited:
                status = value
                # Drain any final PTY output before returning.
                input_open = False
                while select.select([master], [], [], 0)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError as error:
                        if error.errno != errno.EIO:
                            raise
                        break
                    if not data:
                        break
                    forward(data)
                break
    finally:
        try:
            terminate()
            remaining = observe()
            processes.signal_owned(remaining, signal.SIGTERM)
            processes.signal_owned(remaining, signal.SIGCONT)
            while remaining or status is None:
                if status is None:
                    waited, value = os.waitpid(pid, os.WNOHANG)
                    if waited:
                        status = value
                if time.monotonic() >= stop_at + 5:
                    processes.signal_owned(remaining, signal.SIGKILL)
                    if status is None:
                        _, status = os.waitpid(pid, 0)
                    deadline = time.monotonic() + 1
                    while remaining and time.monotonic() < deadline:
                        processes.signal_owned(remaining, signal.SIGKILL)
                        time.sleep(0.02)
                        remaining = observe()
                    if remaining:
                        raise RuntimeError("Native CO processes are still stopping")
                    break
                remaining = observe()
                if remaining or status is None:
                    time.sleep(0.02)
            pending += decoder.decode(b"", final=True)
            if pending and sys.exc_info()[0] is None:
                trace.record("terminal_output", {"text": pending})
        finally:
            try:
                os.close(master)
            finally:
                try:
                    if original:
                        termios.tcsetattr(input_fd, termios.TCSADRAIN, original)
                finally:
                    for sig, handler in old_handlers.items():
                        signal.signal(sig, handler)
    return os.waitstatus_to_exitcode(status)


def _configuration(directory, profile=None, model=None, reasoning=None, *, require_initial_prompt=False):
    store = Store(directory)
    team = team_config(store.directory)
    member = team.get("co")
    if not member or not team.get("runtime"):
        raise ValueError("The CO launcher requires an appointed external CO and shared runtime policy (--runtime).")
    from .control import Control
    from .role_packages import runtime_profile

    scope = Control(store, member).snapshot()
    role = co_role(scope)
    config = resolve(store.directory, role, profile=profile)
    for key, value in (("model", model), ("reasoning", reasoning)):
        if value is not None:
            if not isinstance(value, str) or not value or "\0" in value:
                raise ValueError(key + " must be nonempty text")
            config[key] = value
    config = runtime_profile(role, config)
    # Internal launch mode, never an accepted profiles.json setting.
    config["operator_console"] = True
    _prompt_adapter(config, required=require_initial_prompt)
    config["binary"] = shutil.which(config["binary"])
    return store, member, scope, config


def initial_prompt_profile(directory, profile=None):
    """Resolve the actual CO brief adapter without allocating or preparing a session."""
    return _configuration(directory, profile, require_initial_prompt=True)[3]


def run(directory, profile=None, model=None, reasoning=None, *, initial_prompt=None):
    initial_prompt = validate_initial_prompt(initial_prompt)
    store, member, scope, config = _configuration(
        directory, profile, model, reasoning, require_initial_prompt=initial_prompt is not None)
    generation = store.begin_session(member, os.getpid(), expected_binding=scope, settings=config)
    trace = Trace(directory, member, component="co-console", context=lambda: context(store, member))
    error, exit_code = None, None
    try:
        private, env, server = prepare(directory, member, config["harness"], config=config)
        workspace = store.member(member)["workspace"]
        # The brief is user input for this launch, never a retained runtime
        # profile setting or a worker's instruction/configuration override.
        command_config = config if initial_prompt is None else {**config, "initial_prompt": initial_prompt}
        argv, native_id = command(directory, member, command_config, private, server)
        store.session(
            member,
            generation=generation,
            native_id=native_id,
            detail={"transport": "interactive/pty", "harness": config["harness"]},
        )
        store.event(
            member,
            "runtime_selected",
            **config["selection"],
            settings={key: config.get(key) for key in ("harness", "vendor", "model", "reasoning", "provider")},
        )
        requested(store, member, config, private)
        transcript = Transcript(store, member, trace)
        from .harnesses import get

        channel = get(config["harness"]).channel(private)
        ready = False

        def poll():
            nonlocal ready
            current = store.member(member)
            if current["generation"] != generation or current["desired"] != "running":
                return False
            if not ready and (not channel or (observation := channel.poll())):
                ready = True
                store.session(member, generation=generation, state="idle")
                if channel:
                    store.event(member, "channel_ready", native_id=native_id, **observation)
            transcript.poll()
            return True

        with ExitStack() as native_settings:
            if getattr(get(config["harness"]), "protocol", None) == "grok":
                from .console_settings import grok_settings

                native_settings.enter_context(grok_settings(config["binary"], private, server, workspace, env))
            exit_code = relay(argv, env, workspace, trace, poll)
        transcript.poll()
        if exit_code and store.member(member)["desired"] == "running":
            error = f"Native CO exited with status {exit_code}"
    except Exception as exc:
        error = str(exc)[:1000]
        raise
    finally:
        try:
            retain_login(directory, member, config["harness"], generation)
        except (OSError, ValueError):
            store.event(member, "login_cache_failed", harness=config["harness"])
        store.session(
            member,
            generation=generation,
            state="failed" if error else "stopped",
            desired="stopped",
            pid=None,
            detail={"error": error, "exit_code": exit_code, "harness": config["harness"]},
        )
        trace.record("console_closed", {"exit_code": exit_code, "error": error})
        trace.close()
    return (exit_code or 0) if error else 0
