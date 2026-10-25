"""What the protocol adapters (codex, grok) share: the supervisor's five
slice 1 methods over a bridge process, the channel, and the envelope.

Contract: ops/conductor/sessions-lever.md, "The adapter boundary" and
"The adapter boundary, extended". The adapters are loaded by path, never
as a package, so each loads this file by its own path; a staged copy of
the directory works the same way. `stop` and `diagnostics` keep slice 1's
claude shapes: a pid is signalled only while its start ticks match the
record, and evidence is named by path.

Stdlib only.
"""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The instrument module sits in the tools directory above this one.
sys.path.append(str(HERE.parent))
from instrument import Refusal, refuse  # noqa: E402

REPO = HERE.parents[3]
PROFILES = REPO / "ops" / "devlane" / "hooks" / "profiles.py"
PROBE_TIMEOUT = 10
STOP_WAIT = 5.0
SUBMIT_TIMEOUT = 30.0
EVIDENCE = ("pane.log", "stderr.log", "launch.json", "channel.log", "guard.log",
            "native.json", "bridge.json")
SERVER = "coordination"

__all__ = ("SERVER", "ProtocolAdapter", "Refusal", "envelope", "marker", "refuse")


def _sibling(name: str):
    path = HERE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"session_adapters_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


channel = _sibling("channel")


def marker(message: str) -> str:
    """The text that names a carried message in the native session's
    history; reconciliation looks for exactly this."""
    return f"[coordination {message}]"


def envelope(row: dict, *, mcp_only=False) -> str:
    """One queued message as the text a native session receives."""
    message = row.get("message")
    if mcp_only:
        return (f"{marker(message)} {row.get('purpose')} from {row.get('sender')}: {row.get('body')}\n"
                f"Use capability_describe for coord_ack and coord_send, then capability_invoke. "
                f"Acknowledge message {message}; answer a request with coord_send's reply_to={message}. "
                "Keep this native session; live replies do not require dispatch.")
    return (f"{marker(message)} {row.get('purpose')} from {row.get('sender')}: {row.get('body')}\n"
            f"Acknowledge it with coord_ack {message}; answer a request with coord_send "
            f"--reply-to {message}.")


def start_ticks(pid: int) -> str | None:
    """Field 22 of /proc/PID/stat, the start time in clock ticks; None when gone."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    return text.rsplit(")", 1)[1].split()[19]


def wait_gone(pid: int, start: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if start_ticks(pid) != start:
            return True
        time.sleep(0.05)
    return start_ticks(pid) != start


class ProtocolAdapter:
    """A native harness spoken to over a JSON-lines protocol by the bridge.

    Subclasses name `harness`, `jsonrpc`, `channel`, `handshake`, `submit`
    and `observe`. The instance holds the conversation it speaks for: the
    native id the handshake returned and the active turn, if any.
    """

    harness = ""
    jsonrpc = True

    def __init__(self):
        self.native: str | None = None
        self.turn: str | None = None

    def transport(self) -> str:
        return "protocol"

    def channel(self, member, binding) -> list[str]:
        raise NotImplementedError

    def argv(self, member, binding, launch_id):
        """The harness command the bridge spawns: the profile's run-interactive
        with the channel argv, never operator text. The launch id is the
        supervisor's; the native id comes from the handshake."""
        return [sys.executable, str(PROFILES), "run-interactive",
                "--binding", str(binding["binding"]), "--", *self.channel(member, binding)]

    def connect(self, argv, *, env=None, cwd=None, stderr=None):
        """The channel to a spawned harness, framed as this harness frames."""
        return channel.Channel(argv, jsonrpc=self.jsonrpc, env=env, cwd=cwd, stderr=stderr)

    def observe_identity(self, store, launch_id):
        """The native id the bridge recorded for this launch, when a
        participant is bound under it; None otherwise."""
        if store is None:
            return None
        try:
            row = store.execute(
                "SELECT m.native_id FROM session_member m JOIN participants p "
                "ON p.session_id = m.native_id WHERE m.launch_id = ?", (launch_id,)
            ).fetchone()
        except Exception:  # noqa: BLE001 - a store without the tables shows nothing
            return None
        return None if row is None else row[0]

    def probe(self, executable):
        """The installed version; the channel is measured by the handshake."""
        reading = {"executable": str(executable),
                   "capability": "protocol, measured at the bridge's handshake"}
        try:
            done = subprocess.run(  # noqa: S603 - argv is a list, never a shell string
                [str(executable), "--version"], capture_output=True, text=True,
                stdin=subprocess.DEVNULL, timeout=PROBE_TIMEOUT, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            reading["version"] = f"unresolved {type(error).__name__}: {error}"
            return reading
        lines = (done.stdout or done.stderr).strip().splitlines()
        reading["version"] = lines[0] if lines else f"unresolved exit {done.returncode}, no output"
        return reading

    def stop(self, handle, terminate):
        """Signal only a pid whose start ticks still match the record; the
        bridge closes its harness when it is signalled."""
        pid, start = int(handle["pid"]), str(handle["start"])
        current = start_ticks(pid)
        if current != start:
            return f"unresolved stale pid {pid}: start ticks {current or 'gone'}, recorded {start}"
        if not terminate:
            return f"unresolved running pid {pid}"
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as error:
            return f"unresolved pid {pid}: SIGTERM failed: {error}"
        if wait_gone(pid, start, STOP_WAIT * 2):
            return f"stopped terminated pid {pid}"
        if start_ticks(pid) == start:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError as error:
                return f"unresolved pid {pid}: SIGKILL failed: {error}"
        if wait_gone(pid, start, STOP_WAIT):
            return f"stopped killed pid {pid}"
        return f"unresolved pid {pid}: alive after SIGKILL"

    def diagnostics(self, member_dir):
        base = Path(member_dir)
        return [str(base / name) for name in EVIDENCE if (base / name).is_file()]

    # -- shared by the subclasses ---------------------------------------------

    def refuse_channel(self, method: str, error) -> None:
        reading = getattr(error, "reading", str(error))
        refuse("channel", f"the {self.harness} handshake answering {method}",
               f"{method}: {reading}",
               "read the member's channel.log; the harness did not speak its protocol")

    def other_native(self, named) -> str | None:
        """`refused native <id>` for a frame naming a conversation this
        adapter did not open; None for this one or for no id."""
        if isinstance(named, str) and named and self.native and named != self.native:
            return f"refused native {named}"
        return None

    def settled(self, method: str, error) -> str:
        """A submission's outcome from the channel's error."""
        if getattr(error, "code", None) == -32601:
            return f"unsupported {method}"
        reading = getattr(error, "reading", str(error))
        if reading.startswith("no answer within"):
            return f"uncertain {method}: {reading}"
        return f"failed {method}: {reading}"
