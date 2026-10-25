# testkit: exempt fixture program planted as the native harness
"""Fake native harness for managed-sessions slice 1.

The kit plants this file through Tools.plant(..., script=...). The spec
beside the registry (fakes/<name>.spec.json) says which session id to
bind: null or absent uses the launch id from --session-id; a string is
that native id (the native-mismatch variant); empty skips the bind (the
timeout-with-no-bind variant). It runs the profile's guard.sh
SessionStart and then holds until the supervisor stops it. A
``bind_after_exit`` path in the spec makes it exit at once instead and
leave a binder that runs SessionStart only after this process is gone,
then writes that path (the exited-pane variant). ``ack_probe`` true makes
it, once bound, read its own inbox as the id it bound, with the
capability in the profile's binding.json, and ack the readiness probe
``start`` sends (sessions-lever.md slice 2, 11), as a member's own hooks
would; it then holds.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

VERSION = "session-harness 0.0.0-fake"
REPO = "@@REPO@@"
# How long the binder waits for the harness's pid to leave /proc.
GONE_WAIT = 20.0
# How long a bound harness looks for the readiness probe to ack.
PROBE_WAIT = 30.0


def spec_path() -> Path | None:
    named = os.environ.get("TESTKIT_FAKE_SPEC", "")
    if named:
        path = Path(named)
        if path.is_file():
            return path
    exe = Path(sys.argv[0]).resolve()
    candidate = exe.parent.parent / "fakes" / f"{exe.name}.spec.json"
    return candidate if candidate.is_file() else None


def load_spec() -> dict:
    path = spec_path()
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def log_call(spec: Path | None, argv: list[str]) -> None:
    if spec is None:
        return
    log = spec.with_name(spec.name.replace(".spec.json", ".calls.jsonl"))
    line = json.dumps({"pid": os.getpid(), "argv": argv, "cwd": os.getcwd()}) + "\n"
    fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def flag(argv: list[str], name: str) -> str | None:
    if name in argv:
        index = argv.index(name)
        if index + 1 < len(argv):
            return argv[index + 1]
    prefix = name + "="
    for item in argv:
        if item.startswith(prefix):
            return item[len(prefix):]
    return None


def profile_dir(argv: list[str]) -> Path | None:
    settings = flag(argv, "--settings")
    if settings:
        return Path(settings).resolve().parent
    binding = os.environ.get("AGENTSCIENT_HOOK_PROFILE", "")
    if binding:
        return Path(binding).resolve().parent
    return None


def native_id(argv: list[str], spec: dict) -> str | None:
    if "session_id" in spec:
        value = spec["session_id"]
        if value is None:
            return flag(argv, "--session-id")
        return str(value)
    return flag(argv, "--session-id")


def kit_run():
    """ops.testkit.run, after the plant substitutes the repository root.

    origin: 20260922T215604Z-review-codex-f6777b#20
    """
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    from ops.testkit import run
    return run


def run_session_start(profile: Path, bind_id: str) -> int:
    guard = profile / "guard.sh"
    if not guard.is_file() or not bind_id:
        return 0
    payload = json.dumps({
        "session_id": bind_id,
        "hook_event_name": "SessionStart",
        "source": "startup",
        "cwd": os.getcwd(),
    })
    env = os.environ.copy()
    env.pop("COORDINATION_CAPABILITY", None)
    result = kit_run()(
        [str(guard), "SessionStart"],
        cwd=os.getcwd(),
        env=env,
        stdin=payload,
        timeout=30,
    )
    if result.exit != 0:
        sys.stderr.write(result.stderr or result.stdout)
        return result.exit
    # The guard reports a native refusal and still exits 0; the pane is the
    # terminal, so its output is echoed there for pane.log to carry
    # (A-495 item c).
    sys.stdout.write(result.stdout)
    sys.stdout.write(result.stderr)
    sys.stdout.flush()
    return 0


def ack_probe(profile: Path, bind_id: str) -> None:
    """Ack the readiness probe from this member's inbox, or give up quietly
    at PROBE_WAIT: the row reads ``waiting ack`` then."""
    try:
        binding = json.loads((profile / "binding.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    capability = binding.get("coordination_capability") if isinstance(binding, dict) else None
    if not isinstance(capability, str) or not capability:
        return
    env = os.environ.copy()
    env["CLAUDE_CODE_SESSION_ID"] = bind_id
    env["COORDINATION_CAPABILITY"] = capability
    tool = str(Path(REPO) / "ops" / "conductor" / "tools" / "coordination.py")
    run = kit_run()
    deadline = time.monotonic() + PROBE_WAIT
    while time.monotonic() < deadline:
        shown = run([sys.executable, tool, "inbox"], cwd=os.getcwd(), env=env, timeout=30)
        probes = [line for line in shown.stdout.splitlines() if "readiness probe" in line]
        for line in probes:
            for token in re.findall(r"\bM-\d+\b", line):
                run([sys.executable, tool, "ack", token], cwd=os.getcwd(), env=env, timeout=30)
        if probes:
            return
        time.sleep(0.2)


def pane_chain() -> list[int]:
    """This process and its ancestors below the tmux server, or this
    process alone when no tmux server is above it.

    The pid the supervisor records is the pane's own process, profiles.py
    run-interactive, which runs this harness as a child and outlives it by
    its custody step. A binder that waited for this process alone could
    bind while the pane's process still read ok (LD-149).
    """
    chain = [os.getpid()]
    pid = os.getppid()
    while pid > 1:
        # stat, never exe: under the harness sandbox an ancestor's exe link
        # is denied (measured: PermissionError 13), its stat is not.
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            break
        comm, rest = stat.split("(", 1)[1].rsplit(")", 1)
        if comm.startswith("tmux"):
            return chain
        chain.append(pid)
        pid = int(rest.split()[1])
    return chain[:1]


def running(pid: int) -> bool:
    """In /proc and not a zombie. A zombie has exited; only its parent's
    reap is pending, and a tmux server has left a pane's process unreaped
    for 15 s (LD-150)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def bind_after_exit(profile: Path, bind_id: str, marker: Path) -> None:
    """Fork a binder and return, so the caller exits at once.

    The binder waits until this process and every ancestor up to the
    pane's own process have exited, runs SessionStart, and writes
    ``marker`` with the guard's exit. No poll of the supervisor can then
    find the process ok and the member bound at the same time (origin:
    20260922T215604Z-review-codex-f6777b#9; LD-149).
    """
    chain = pane_chain()
    if os.fork() != 0:
        return
    code = 1
    try:
        os.setsid()
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        deadline = time.monotonic() + GONE_WAIT
        while any(running(pid) for pid in chain):
            if time.monotonic() >= deadline:
                note = f"pane pids {chain} still running after {GONE_WAIT}s, never bound\n"
                break
            time.sleep(0.02)
        else:
            note = f"bound after exit {run_session_start(profile, bind_id)}\n"
            code = 0
    except BaseException as error:  # the row reads why the binder failed
        note = f"binder failed {error!r}\n"
    try:
        pending = marker.with_name(marker.name + ".new")
        pending.write_text(note, encoding="utf-8")
        os.replace(pending, marker)
    finally:
        os._exit(code)


def hold() -> None:
    try:
        stream = sys.stdin
        if stream is not None and not stream.closed and not stream.isatty():
            stream.read()
    except (OSError, ValueError):
        pass
    while True:
        time.sleep(3600)


def main() -> int:
    argv = sys.argv[1:]
    spec_file = spec_path()
    log_call(spec_file, argv)
    if argv[:1] == ["--version"]:
        # The adapter's probe asks the executable its version
        # (origin: 20260923T003033Z-review-codex-c99cc9#6).
        sys.stdout.write(VERSION + "\n")
        return 0
    spec = load_spec()
    bind_id = native_id(argv, spec)
    profile = profile_dir(argv)
    after_exit = spec.get("bind_after_exit")
    if after_exit and profile is not None and bind_id:
        bind_after_exit(profile, bind_id, Path(after_exit))
        return 0
    if profile is not None and bind_id:
        code = run_session_start(profile, bind_id)
        if code:
            return code
        if spec.get("ack_probe"):
            ack_probe(profile, bind_id)
    hold()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
