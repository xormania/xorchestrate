"""Start and stop configured persistent sessions. There is no job dispatcher."""

import fcntl
import json
import os
import shutil
import signal
import socket
import struct
import sys
import time
import uuid
from pathlib import Path

from .adapters import names as adapter_names
from .store import Store, identifier

STARTUP_TIMEOUT = 10
STOP_GRACE = 5


def initialize(directory, workspace, members, co=None, project=None, runtime=False):
    from .profiles import write_json

    directory = Path(directory).expanduser().resolve()
    config = directory / "team.json"
    if config.exists():
        raise ValueError("Team already exists; edit its team.json to change configuration.")
    rows = []
    for item in members:
        name, harness = item.split(":", 1)
        identifier(name)
        if harness not in {*adapter_names("harness"), "external"} or name in {row["name"] for row in rows}:
            raise ValueError("Use unique names and a registered harness or an external connection.")
        rows.append(
            {
                "name": name,
                "harness": harness,
                "binary": harness,
                "workspace": str(Path(workspace).expanduser().resolve(strict=True)),
            }
        )
    if not rows:
        raise ValueError("Configure at least one member.")
    if co is not None and not any(row["name"] == co and row["harness"] == "external" for row in rows):
        raise ValueError("CO must name the external user-facing session.")
    if runtime:
        from .runtime import defaults

        defaults(directory)
    Store(directory).initialize(rows)
    write_json(config, {"members": rows, **({"co": co} if co else {}), **({"project": project} if project else {}),
                        **({"runtime": True} if runtime else {})})
    return {"state": str(directory), "members": rows}


def team_config(directory):
    config = json.loads((Path(directory) / "team.json").read_text())
    if type(config.get("runtime", False)) is not bool:
        raise ValueError("runtime must be a boolean")
    rows = config.get("members")
    if not isinstance(rows, list) or not rows:
        raise ValueError("team.json needs a nonempty members array")
    names = set()
    for row in rows:
        identifier(row["name"])
        if row["name"] in names or row["harness"] not in {*adapter_names("harness"), "external"}:
            raise ValueError("Invalid or duplicate team member")
        names.add(row["name"])
        if set(row) - {"name", "harness", "workspace", "binary", "model", "reasoning"}:
            raise ValueError("Unknown team member option")
        for key in ("binary", "model", "reasoning"):
            if key in row and (not isinstance(row[key], str) or not row[key] or "\0" in row[key]):
                raise ValueError(f"{key} must be a nonempty string")
    if config.get("co") and not any(row["name"] == config["co"] and row["harness"] == "external" for row in rows):
        raise ValueError("CO must name the external user-facing session.")
    return config


def configuration(directory, member):
    store = Store(directory)
    team = team_config(store.directory)
    rows = team["members"]
    store.initialize(rows, runtime=team.get("runtime", False))
    selected = next((row for row in rows if row["name"] == member), None)
    if selected is None and not team.get("runtime"):
        raise ValueError("Member is not configured in team.json")
    if selected and selected["harness"] == "external":
        raise ValueError("External members connect their own MCP client; there is no managed process to start.")
    from .control import Control

    scope = Control(store, member).snapshot()
    standby_scope = None
    if not scope:
        from .standby import authorized
        from .standby import binding as standby_binding

        with store.connect() as db:
            standby_scope = standby_binding(db, member)
            if standby_scope and not authorized(store, standby_scope, db=db):
                raise ValueError("The standby CO appointment is no longer current.")
        if not standby_scope:
            raise ValueError(
                "Bind this member's role and work through the operator member_bind capability before starting it."
            )
        scope = standby_scope
    if team.get("runtime"):
        from .role_packages import adapter as role_adapter
        from .runtime import selected as selected_runtime

        if standby_scope:
            from .runtime import resolve

            choice = resolve(store.directory, scope["role"])
        elif role_adapter(scope["role"]) == "xo":
            from .assignment_decisions import select_bound_runtime

            choice = select_bound_runtime(store, scope)
        else:
            choice = selected_runtime(store, scope)
        row = store.member(member)
        selected = {"name": member, "workspace": row["workspace"], **choice}
    from .role_packages import runtime_profile

    selected = runtime_profile(scope["role"], selected)
    from .workspaces import Workspaces, project_config

    if project_config(store) and not standby_scope:
        selected["workspace"] = Workspaces(store, member).ensure()["root"]
    binary = shutil.which(selected.get("binary", selected["harness"]))
    if not binary:
        raise ValueError(f"Install/login to {selected['harness']} and set its binary in team.json")
    selected["binary"] = str(Path(binary).resolve())
    return selected


def start(directory, member, *, binding_revision=None, standby_revision=None):
    store = Store(directory)
    locks = store.directory / "launch-locks"
    locks.mkdir(exist_ok=True, mode=0o700)
    with (locks / (identifier(member) + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if binding_revision is not None and standby_revision is not None:
            raise ValueError("Choose a work launch or a standby launch.")
        if standby_revision is not None:
            from .standby import authorized
            from .standby import binding as standby_binding

            with store.connect() as db:
                scope = standby_binding(db, member)
                work_bound = db.execute("SELECT 1 FROM member_bindings WHERE member=?", (member,)).fetchone()
                current_appointment = authorized(store, scope, db=db)
            if (not scope or work_bound or not current_appointment or scope["revision"] != standby_revision or not scope["launch_pending"]
                    or time.time() >= scope["deadline"]):
                return {"superseded": True}
        if binding_revision is not None:
            from .control import Control

            scope = Control(store, member).snapshot()
            if not scope or scope["revision"] != binding_revision or scope["paused"] or not scope["launch_pending"]:
                return {"superseded": True}
        current = store.member(member)
        if current["generation"] and current["desired"] == "stopped":
            _stop(store, member)
        result = _start(directory, member, binding_revision, standby_revision)
        if binding_revision is not None and result.get("already_running"):
            with store.connect() as db:
                db.execute(
                    "UPDATE member_bindings SET launch_pending=0 WHERE member=? AND revision=?",
                    (member, binding_revision),
                )
        if standby_revision is not None and result.get("already_running"):
            with store.connect() as db:
                db.execute("UPDATE standby_bindings SET launch_pending=0 WHERE member=? AND revision=?",
                           (member, standby_revision))
        return result


def _start(directory, member, binding_revision, standby_revision=None):
    from .bootstrap import receive, send
    from .profiles import ENTRY
    from .terminals import start as start_terminal

    store = Store(directory)
    current = store.member(member)
    if current["state"] in {"starting", "idle", "active"} and current["pid"]:
        from . import processes, terminals

        owned = processes.owned(store, member, current["generation"])
        running = any(row["pid"] == current["pid"] for row in owned)
        if not running:
            running = any(row["pid"] == current["pid"] and not row["dead"]
                          for row in terminals.status(store, member)["panes"])
        if running:
            return {"member": member, "already_running": True, "session": current}
    config = configuration(directory, member)
    launch_id = "agentscient-launch-" + uuid.uuid4().hex
    argv = [sys.executable, str(ENTRY), "agent", "--state", str(store.directory), "--member", member]
    if binding_revision is not None:
        argv += ["--binding-revision", str(binding_revision)]
    if standby_revision is not None:
        argv += ["--standby-revision", str(standby_revision)]
    argv = [sys.executable, "-I", "-S", str(ENTRY.with_name("bootstrap.py")), launch_id, *argv]
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind("\0" + launch_id)
        listener.listen(1)
        listener.settimeout(STARTUP_TIMEOUT)
        detail = start_terminal(store, member, argv, config["workspace"])
        store.event(member, "launch_requested", **detail)
        deadline = time.monotonic() + STARTUP_TIMEOUT
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Launch registration deadline expired")
                listener.settimeout(max(0.001, deadline - time.monotonic()))
                stream, _ = listener.accept()
                with stream:
                    _, uid, _ = struct.unpack("3i", stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != os.getuid():
                        continue
                    stream.settimeout(max(0.001, deadline - time.monotonic()))
                    send(stream, dict(os.environ))
                    send(stream, {"member": member, "config": config})
                    registered = receive(stream)
                    break
        except Exception as error:
            # Closing the handoff fences late bootstrap processes. Reap the
            # terminal too, so timeout cannot leave an unreported running agent.
            _stop(store, member, grace=0)
            store.session(member, state="failed", detail={"error": "Launch registration failed; launch was stopped"})
            raise RuntimeError("Managed process registration failed; launch was stopped. Inspect monitor and retry.") from error
    current = store.member(member)
    if registered.get("generation") != current["generation"]:
        raise RuntimeError("Launch registration was superseded")
    if current["state"] == "failed":
        raise RuntimeError(current["detail"].get("error", "Native startup failed"))
    return {"member": member, "requested": "running", **detail, "session": current}


def stop(directory, member, *, grace=STOP_GRACE, expected_generation=None):
    store = Store(directory)
    locks = store.directory / "launch-locks"
    locks.mkdir(exist_ok=True, mode=0o700)
    with (locks / (identifier(member) + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = store.member(member)
        if expected_generation is not None and (
            current["generation"] != expected_generation or current["desired"] != "stopped"
        ):
            return {"superseded": True}
        return _stop(store, member, grace=grace)


def _stop(store, member, *, grace=STOP_GRACE):
    from . import processes, terminals

    current = store.member(member)
    generation = current["generation"]
    store.session(member, generation=generation, desired="stopped")
    if current["harness"] == "external":
        return {"member": member, "requested": "stopped"}
    records = {}

    def observe():
        for row in processes.owned(store, member, generation):
            records[row["pid"]] = row
        terminal = terminals.status(store, member)
        for pane in terminal["panes"]:
            if not pane["dead"] and (row := processes.identity(pane["pid"])):
                records[row["pid"]] = row
        for row in processes.family(records.values()) if records else []:
            records[row["pid"]] = row
        return processes.alive(records.values()), terminal["exists"]

    deadline = time.monotonic() + grace
    remaining, terminal_exists = observe()
    while (remaining or terminal_exists) and time.monotonic() < deadline:
        time.sleep(0.05)
        remaining, terminal_exists = observe()
    forced = bool(remaining or terminal_exists)
    if forced:
        processes.signal_owned(remaining, signal.SIGTERM)
        processes.signal_owned(remaining, signal.SIGCONT)
        deadline = time.monotonic() + 1
        while remaining and time.monotonic() < deadline:
            time.sleep(0.05)
            remaining, _ = observe()
        processes.signal_owned(remaining, signal.SIGKILL)
        terminals.stop(store, member)
        deadline = time.monotonic() + 1
        while processes.alive(records.values()) and time.monotonic() < deadline:
            time.sleep(0.05)
    if processes.alive(records.values()):
        raise RuntimeError("Managed processes are still stopping; inspect monitor before restarting")
    store.session(member, generation=generation, state="stopped", desired="stopped", pid=None)
    store.event(member, "session_stop_completed", generation=generation, forced=forced)
    return {"member": member, "requested": "stopped", "stopped": True, "forced": forced}


def agent(directory, member, binding_revision=None, launch_fd=None, standby_revision=None):
    from .bootstrap import receive, send
    from .control import REQUEST_BINDING, Control
    from .processes import register

    store = Store(directory)
    handoff = socket.socket(fileno=launch_fd) if launch_fd is not None else None
    try:
        if handoff:
            handoff.settimeout(STARTUP_TIMEOUT)
            saved = receive(handoff)
            if saved["member"] != member:
                raise ValueError("Launch handoff belongs to another member")
            config = saved["config"]
        else:
            config = configuration(directory, member)
        generation = store.begin_session(
            member, os.getpid(), binding_revision=binding_revision, standby_revision=standby_revision,
            settings={key: config.get(key) for key in ("harness", "vendor", "model", "reasoning", "provider", "role_package")},
        )
        register(store, member, generation, os.getpid())
        if handoff:
            send(handoff, {"generation": generation})
    finally:
        if handoff:
            handoff.close()
    control = Control(store, member)
    origin = control.snapshot()
    with store.connect() as db:
        db.execute("UPDATE members SET harness=? WHERE name=?", (config["harness"], member))
    if config.get("selection"):
        store.event(member, "runtime_selected", **config["selection"],
                    settings={k: config.get(k) for k in ("harness", "vendor", "model", "reasoning", "provider", "role_package")})

    def stop(_signal, _frame):
        store.session(member, generation=generation, desired="stopped")

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    error = None
    try:
        from .harnesses import get
        from .retirement import lease

        with lease(store, config["workspace"]):
            get(config["harness"]).run(directory, member, config, generation)
    except Exception as exc:
        error = str(exc)[:1000]
        store.event(member, "session_failed", error=error)
        raise
    finally:
        from .profiles import retain_login

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
            detail={"error": error},
        )
        if error and origin:
            token = REQUEST_BINDING.set((member, origin))
            try:
                with store.work_context(member, origin["work_order"], origin["job"]):
                    control.report("Native session failed: " + error[:600], kind="blocker")
            except ValueError:
                pass  # A revoked or changed reporting relationship cannot receive this report.
            finally:
                REQUEST_BINDING.reset(token)
