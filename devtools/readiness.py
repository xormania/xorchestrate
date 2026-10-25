"""Operator setup and read-only preflight. Never launch models or infer acceptance."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import uuid
from pathlib import Path

from .configuration import decision_reservations, selections
from .configuration import resolve as resolve_settings
from .credentials import read_credential
from .github_http import GitHubHTTP
from .harnesses import get as harness_adapter
from .launch import team_config
from .store import State
from .telemetry import clean
from .workspaces import git

ENTRY = Path(__file__).with_name("run.py")


def config(directory, role=None, job=None):
    """Inspect effective settings without opening the configured credential source."""
    state = State(directory)
    team_config(state.directory)
    settings = resolve_settings(state, role=role, job=job)
    result = clean(settings)
    # The validated selector contains a location, never credential contents.
    result["connection"]["credential"] = clean(settings["connection"]["credential"])
    return result


def project(workspace, repository, target="dev", remote="origin", serena_image=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository) or repository.split("/")[1] in {
        ".",
        "..",
    }:
        raise ValueError("repository must be a GitHub owner/repository.")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", remote):
        raise ValueError("Invalid Git remote name.")
    root = Path(workspace).expanduser().resolve(strict=True)
    if Path(git(root, "rev-parse", "--show-toplevel").stdout.decode().strip()).resolve() != root:
        raise ValueError("workspace must be the Git repository root.")
    git(root, "check-ref-format", "--branch", target)
    if target.startswith("-"):
        raise ValueError("Invalid integration branch.")
    value = {
        "checkout": str(root),
        "repository": repository,
        "target": target,
        "remote": remote,
        "workspaces": str(root.parent / "wt-ag"),
        "development": {},
    }
    if serena_image:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]+", serena_image):
            raise ValueError("Invalid Serena container image name.")
        value["serena"] = {"image": serena_image}
    return value


def connection(directory):
    directory = Path(directory).expanduser().resolve()
    config = team_config(directory)
    if not config.get("co"):
        raise ValueError("Appoint the external CO in team.json first.")
    return {
        "mcpServers": {
            "agentscient_dev": {
                "command": sys.executable,
                "args": [str(ENTRY), "mcp", "--state", str(directory), "--member", config["co"]],
            }
        }
    }


def probe(argv, root=None, env=None):
    result = subprocess.run(
        argv, cwd=root, env=env, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL
    )
    if result.returncode:
        # Do not echo arbitrary binary output, credential URLs or environment data.
        raise ValueError(f"Installed command failed with exit status {result.returncode}.")
    return result.stdout.strip()


def startup_preflight(directory, timeout):
    """Run existing read-only checks with one deadline, reaping timed-out probes.

    The child isolates subprocess/provider timeouts from the interactive MCP
    request. This is configuration evidence; a duty receipt proves the separate
    live agent round trip. Online provider access remains explicitly untested.
    """
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Startup preflight needs a positive finite time budget")

    def blocked(detail):
        return {"status": "blocked", "checks": [{"name": "startup preflight", "status": "failed", "detail": detail}],
                "authenticated_acceptance": "not established by preflight"}

    try:
        process = subprocess.Popen(
            [sys.executable, str(ENTRY), "doctor", "--state", str(directory), "--startup"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True,
        )
    except OSError:
        return blocked("Could not start the read-only preflight process")
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # A probe may have detached from this process group while retaining
        # stdout. Do not wait for that detached pipe writer to close it.
        process.stdout.close()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            return blocked("Startup preflight exceeded its time budget; its worker did not exit within the cleanup budget")
        return blocked("Startup preflight exceeded its time budget; its worker was stopped")
    if process.returncode not in {0, 2} or len(output) > 1024 * 1024:
        return blocked("Startup preflight did not return a valid bounded result")
    try:
        result = json.loads(output)
        if (not isinstance(result, dict) or result.get("status") not in {"blocked", "preflight_passed"}
                or not isinstance(result.get("checks"), list)
                or (process.returncode == 0) != (result["status"] == "preflight_passed")):
            raise ValueError("Invalid result")
    except (TypeError, ValueError):
        return blocked("Startup preflight did not return a valid bounded result")
    return result


def platform(checkout, development):
    env = os.environ.copy()
    php = Path(development.get("php", "php"))
    if php.is_absolute():
        env["PATH"] = str(php.parent) + os.pathsep + env.get("PATH", "")
    # Ignore operator overrides that would hide real platform failures.
    for key in ("COMPOSER", "COMPOSER_IGNORE_PLATFORM_REQ", "COMPOSER_IGNORE_PLATFORM_REQS", "COMPOSER_NO_DEV"):
        env.pop(key, None)
    result = subprocess.run(
        [
            development.get("composer", "composer"),
            "--no-plugins",
            "--no-scripts",
            "--no-interaction",
            "check-platform-reqs",
            "--lock",
            "--format=json",
        ],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
    )
    try:
        requirements = json.loads(result.stdout)
        failed = [row["name"] for row in requirements if row["status"] != "success"]
    except (ValueError, TypeError, KeyError):
        raise ValueError(
            "Composer could not inspect the locked platform; run check-platform-reqs --lock locally."
        ) from None
    if failed or result.returncode:
        raise ValueError("Unsatisfied locked platform requirements: " + ", ".join(failed))
    return "Locked PHP and extension requirements, including development packages, are satisfied."


def managed_platform():
    """Inspect the running host without launching or signalling any process."""
    from .processes import identity

    if sys.platform != "linux":
        raise ValueError("Managed sessions require Linux or WSL2; run this control plane on a Linux host.")
    if not callable(getattr(os, "pidfd_open", None)) or not callable(getattr(signal, "pidfd_send_signal", None)):
        raise ValueError("Use a Linux Python build with os.pidfd_open and signal.pidfd_send_signal for managed process recovery.")
    if not hasattr(socket, "SO_PEERCRED"):
        raise ValueError("Managed launch requires Linux Unix-socket peer credentials (SO_PEERCRED).")
    try:
        current = identity(os.getpid())
        if not current or current["pid"] != os.getpid() or not current["boot"]:
            raise ValueError("Current process identity is unavailable")
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        raise ValueError("Mount readable /proc for this PID namespace, including its boot identity, before managed launch.") from error
    try:
        descriptor = os.pidfd_open(os.getpid())
        os.close(descriptor)
    except OSError as error:
        raise ValueError("The Linux kernel or sandbox blocks pidfd_open; enable pidfd access for managed process recovery.") from error
    try:
        left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        with left, right:
            pid, uid, _ = struct.unpack("3i", left.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid != os.getpid() or uid != os.getuid():
                raise ValueError("Peer identity does not match this process")
    except (OSError, ValueError) as error:
        raise ValueError("Linux Unix-socket peer credentials must be available in this namespace for managed launch.") from error
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind("\0agentscient-doctor-" + uuid.uuid4().hex)
    except OSError as error:
        raise ValueError("Managed launch requires abstract Unix sockets; allow AF_UNIX abstract bind in this Linux sandbox.") from error
    return "Linux process identity, pidfd access, abstract Unix sockets and peer credentials are available."


def doctor(directory, online=False, startup=False):
    directory = Path(directory).expanduser().resolve()
    checks = []

    def check(name, action):
        try:
            detail = action()
        except Exception as error:
            checks.append({"name": name, "status": "failed", "detail": GitHubHTTP().clean(str(error))[:600]})
            return None
        checks.append({"name": name, "status": "passed", "detail": detail})
        return detail

    def require(condition, detail):
        if not condition:
            raise ValueError(detail)
        return detail

    config = check("team", lambda: team_config(directory))
    if config is None:
        return {"status": "blocked", "checks": checks, "authenticated_acceptance": "not established by preflight"}
    checks[-1]["detail"] = "Team configuration is readable."
    check("CO connection", lambda: require(bool(config.get("co")), "An external user-facing CO must be appointed."))
    check(
        "private state",
        lambda: require(
            directory.stat().st_mode & 0o077 == 0
            and all(
                not directory.is_relative_to(Path(m["workspace"]).expanduser().resolve()) for m in config["members"]
            ),
            "State must be private (mode 700) and outside every source workspace.",
        ),
    )
    settings_state = State(directory)
    settings = check("assessment configuration", lambda: [
        resolve_settings(settings_state, **selection) for selection in selections(settings_state)
    ])
    reserved = max(decision_reservations(settings or []).values(), default=0)
    if not config.get("runtime"):
        check("collaborator capacity", lambda: require(
            len([m for m in config["members"] if m["name"] != config.get("co")]) >= 2 + reserved,
            f"At least {2 + reserved} collaborator slots are needed for XO, a producer and {reserved} reserved Arbitrator sessions.",
        ))
    if settings is not None:
        next(item for item in checks if item["name"] == "assessment configuration")["detail"] = [
            {"revision": item["revision"], "selection": item["selection"], "features": item["features"]} for item in settings]
        references = {json.dumps(item["connection"]["credential"], sort_keys=True)
                      for item in settings
                      if any(item["features"][name] for name in ("selection", "advice", "filtering", "completion"))}
        if references:
            def credential_ready():
                for reference in sorted(references):
                    read_credential(json.loads(reference))
                return {"sources_checked": len(references),
                        "authentication": "Provider authentication was not contacted."}

            check("Jev credential", credential_ready)
        else:
            checks.append({"name": "Jev credential", "status": "not_checked",
                           "detail": "Jev features are disabled; no credential is required."})
    configured = config.get("project", {})
    has_project = bool(set(configured) & {"checkout", "repository", "development", "serena"})
    if startup and not has_project:
        checks.append({"name": "project", "status": "not_checked",
                       "detail": "No project capabilities are configured for this team."})
    validated = None if startup and not has_project else check(
        "project",
        lambda: project(
            configured.get("checkout", ""),
            configured.get("repository", ""),
            configured.get("target", "dev"),
            configured.get("remote", "origin"),
            configured.get("serena", {}).get("image"),
        ),
    )
    if validated:
        checks[-1]["detail"] = "Repository root, repository identity and integration branch syntax are valid."
        root, remote, target = validated["checkout"], validated["remote"], validated["target"]

        def remote_identity():
            url = git(root, "remote", "get-url", remote).stdout.decode().strip()
            match = re.search(r"(?:github\.com[:/])([^/]+/[^/]+?)(?:\.git)?$", url)
            require(
                bool(match) and match[1].casefold() == configured["repository"].casefold(),
                "The configured Git remote must identify the same GitHub repository as project.repository.",
            )
            return "Git push and GitHub outcomes address the same repository."

        check("Git remote", remote_identity)
        check(
            "Git identity",
            lambda: require(
                bool(configured.get("identity", {}).get("name") and configured.get("identity", {}).get("email"))
                or git(root, "var", "GIT_AUTHOR_IDENT", check=False).returncode == 0,
                "A commit identity must be configured in Git or project.identity.",
            ),
        )
        if not startup or "development" in configured:
            check(
                "Symfony tools enabled",
                lambda: require(
                    isinstance(configured.get("development"), dict),
                    "project.development must be an object ({} enables defaults).",
                ),
            )
            check("locked PHP platform", lambda: platform(root, configured.get("development", {})))
        image = configured.get("serena", {}).get("image")

        def serena():
            require(bool(image), "Configure project.serena.image for product source work.")
            docker = configured["serena"].get("docker", "docker")
            probe([docker, "info", "--format", "{{.ServerVersion}}"])
            labels = json.loads(probe([docker, "image", "inspect", "--format", "{{json .Config.Labels}}", image])) or {}
            source = Path(__file__).with_name("serena").joinpath("Dockerfile").read_text()
            revision = re.search(r"ARG SERENA_REVISION=([0-9a-f]{40})", source)[1]
            require(
                labels.get("org.opencontainers.image.revision") == revision,
                "Build the current devtools/serena image; its source revision label must match the pinned beta.",
            )
            return {
                "image": image,
                "source_revision": revision,
                "runtime_mounts": "verified by the separate integration test",
            }

        if not startup or "serena" in configured:
            check("Serena image", serena)
        if online:
            check(
                "Git remote target",
                lambda: require(
                    bool(git(root, "ls-remote", "--exit-code", remote, "refs/heads/" + target).stdout.strip()),
                    "The integration branch must be readable on the configured remote.",
                ),
            )

            def github():
                result = GitHubHTTP(config.get("github", {})).request("GET", "/repos/" + configured["repository"])
                require(not result.get("archived") and not result.get("disabled"), "Repository must be active.")
                require(
                    result.get("permissions", {}).get("push") is True,
                    "GitHub credential must have repository push permission.",
                )
                return "Repository is readable and reports push permission; merge and review rules still apply."

            check("GitHub access", github)
        else:
            checks.append(
                {
                    "name": "Git/GitHub online access",
                    "status": "not_checked",
                    "detail": "Use doctor --online for read-only provider probes.",
                }
            )
    seen = set()
    # Runtime profiles replace fixed managed-member settings at launch. Probe
    # resolved profiles, so a missing executable that has a valid fallback does
    # not incorrectly fail readiness.
    members = [] if config.get("runtime") else list(config["members"])
    if config.get("runtime"):
        from .role_packages import adapter, default_role
        from .runtime import co_role, load, resolve

        loaded = check("Runtime policy", lambda: load(directory))
        if loaded is not None:
            policy, _, revision = loaded
            checks[-1]["detail"] = {"revision": revision, "roles": list(policy["roles"])}
            check("collaborator capacity", lambda: require(
                policy["max_sessions"] >= 2 + reserved,
                f"Set roles.json max_sessions to at least {2 + reserved} for XO, one planner or specialist and {reserved} reserved Arbitrator sessions.",
            ))

            def required_roles():
                with settings_state.connect() as db:
                    row = db.execute("SELECT role FROM member_bindings WHERE member=?", (config.get("co"),)).fetchone()
                required = {co_role({"role": row[0]} if row else None), default_role("xo"), default_role("planner")}
                for item in settings or []:
                    if any(item["features"][name] for name in ("selection", "advice", "filtering", "completion")):
                        required.add(item["decisions"]["arbitrator_role"])
                return required

            required = check("Workflow role identities", required_roles)
            if required is not None:
                checks[-1]["detail"] = sorted(required)
                for role in sorted(required | set(policy["roles"])):
                    selected = check("Runtime role " + role, lambda role=role: resolve(directory, role))
                    if selected is not None:
                        if adapter(role) == "co":
                            selected["operator_console"] = True
                        members.append(selected)
                        checks[-1]["detail"] = {"profile": selected["selection"]["profile"],
                                                 "harness": selected["harness"],
                                                 "skipped": selected["selection"]["skipped"]}
                        # Selection may choose any available permitted profile,
                        # including one with distinct native credential settings.
                        if any(item["features"]["selection"] for item in settings or []):
                            for profile in policy["roles"][role]:
                                if profile == selected["selection"]["profile"]:
                                    continue
                                try:
                                    alternative = resolve(directory, role, profile=profile)
                                except ValueError:
                                    continue  # Disabled or absent candidates cannot be selected.
                                if adapter(role) == "co":
                                    alternative["operator_console"] = True
                                members.append(alternative)
    if any(row["harness"] != "external" for row in members) or config.get("runtime"):
        check("managed process platform", managed_platform)
        check("tmux", lambda: require(bool(shutil.which("tmux")), "Managed harness sessions require tmux."))
    for member in members:
        harness = member["harness"]
        binary = member.get("binary") or harness
        profile = (harness, binary, member.get("provider"), bool(member.get("operator_console")))
        if harness == "external" or profile in seen:
            continue
        seen.add(profile)

        def native(harness=harness, binary=binary, member=member):
            executable = shutil.which(binary)
            require(bool(executable), f"Install the configured {harness} binary and authenticate it locally.")
            version = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", probe([executable, "--version"]))
            detail = {"version": version[0] if version else "unreported", "authentication": "requires live acceptance"}
            detail.update(harness_adapter(harness).readiness(executable, probe, config=member))
            return detail

        label = harness + " binary" + (" (" + member["provider"] + ")" if member.get("provider") else "")
        if member.get("operator_console"):
            label += " (CO operator)"
        check(label, native)
    return {
        "status": "blocked" if any(c["status"] == "failed" for c in checks) else "preflight_passed",
        "checks": checks,
        "authenticated_acceptance": "not established by preflight",
        "next": "Follow docs/local-agent-readiness.md and retain the actual local run's evidence.",
    }


def evidence(directory, work_order):
    """Summarize existing records without upgrading the DB or certifying a model run."""
    state = State(directory)
    with state.connect() as db:
        db.execute("BEGIN")
        order = db.execute("SELECT id,title,status FROM work_orders WHERE id=?", (work_order,)).fetchone()
        if not order:
            raise ValueError("No recorded work order has that number.")
        current_schema = db.execute("PRAGMA user_version").fetchone()[0] >= 8
        delivery = (
            db.execute(
                "SELECT w.pull_request,d.revision,d.merge_commit,d.assessment FROM work_orders w "
                "LEFT JOIN order_deliveries d ON d.work_order=w.id WHERE w.id=?",
                (work_order,),
            ).fetchone()
            if current_schema
            else None
        )
        rows = db.execute("SELECT actor,kind,data FROM events WHERE work_order=? ORDER BY id", (work_order,))
        sessions, tools, denied, serena = {}, {}, 0, 0
        runtime, actors = [], set()
        suppressed = {}
        for row in rows:
            actors.add(row["actor"])
            data = json.loads(row["data"])
            if data.get("capture") == "suppressed":
                reason = data.get("reason", "unavailable")
                suppressed[reason] = suppressed.get(reason, 0) + 1
                continue
            if row["kind"] in {"runtime_requested", "runtime_observed", "mcp_client", "runtime_selected"}:
                runtime.append({"actor": row["actor"], "kind": row["kind"], **data})
            if row["kind"] in {"native_session", "channel_ready"} and data.get("native_id"):
                sessions.setdefault(row["actor"], set()).add(data["native_id"])
            if row["kind"] == "operation_finished" and isinstance(data.get("capability"), str):
                tool = data["capability"]
                tools[tool] = tools.get(tool, 0) + 1
            denied += row["kind"] in {"tool_denied", "native_tool_denied", "native_tool_violation"}
            serena += row["kind"] == "serena_call" and data.get("success") is True
        return {
            "work_order": dict(order),
            "delivery": dict(delivery) if delivery else None,
            "cleanup": [
                dict(r)
                for r in db.execute(
                    "SELECT job,state,detail FROM managed_worktrees WHERE work_order=? ORDER BY root",
                    (work_order,),
                )
            ]
            if current_schema
            else [],
            "native_sessions": {actor: sorted(ids) for actor, ids in sessions.items()},
            "runtime_records": runtime,
            "suppressed_events": {"total": sum(suppressed.values()), "reasons": suppressed},
            "telemetry_files": [str(state.directory / "telemetry" / (actor + ".jsonl")) for actor in sorted(actors)
                                if (state.directory / "telemetry" / (actor + ".jsonl")).is_file()],
            "capture_notes": "Requested/inherited settings are not confirmed settings. Missing native fields are unknown. "
                             "Wire captures contain exposed protocol traffic; Claude transcript capture begins when "
                             "its hook registers a session transcript. External clients expose only their MCP traffic.",
            "completed_tool_calls": tools,
            "successful_serena_calls": serena,
            "denials_or_violations_to_inspect": denied,
            "messages": [
                dict(r)
                for r in db.execute(
                    "SELECT delivery,count(*) AS count,sum(acknowledged IS NOT NULL) AS acknowledged FROM messages WHERE work_order=? GROUP BY delivery",
                    (work_order,),
                )
            ],
            "jobs": [
                dict(r)
                for r in db.execute(
                    "SELECT j.title,j.status,j.pull_request,d.revision,d.merge_commit,d.assessment FROM jobs j LEFT JOIN deliveries d ON d.job=j.id WHERE j.work_order=? ORDER BY j.id",
                    (work_order,),
                )
            ],
            "operations": [
                dict(r)
                for r in db.execute(
                    "SELECT action,state,count(*) AS count FROM operations WHERE work_order=? GROUP BY action,state",
                    (work_order,),
                )
            ],
            "artifact_count": db.execute(
                "SELECT count(DISTINCT artifact) FROM work_links WHERE work_order=?", (work_order,)
            ).fetchone()[0],
            "interpretation": "Recorded observations only. Completed calls can return failures or receipts. Inspect operation results, traces and artifacts; this does not certify authenticated model acceptance.",
        }
