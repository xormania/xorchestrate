"""Local fixture operator helper; the fixture owns preparation and verification."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import inspect
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
FIXTURE_REPOSITORY = "uscient/agentscient-test-repo"
RUNTIME_REPOSITORY = "uscient/agentscient"
RUN_COMMANDS = {"status", "doctor", "co", "verify", "stop", "collect"}
SHA = re.compile(r"[0-9a-f]{40}")


class SetupError(Exception):
    """An operator-actionable failure whose text never includes process output."""


def path(value):
    return Path(value).expanduser().resolve()


def manager_root():
    return path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "agentscient-acceptance"


def run_manifest(value):
    root = path(value)
    if root.name == "run.json":
        root = root.parent
    manifest = json.loads((root / "run.json").read_text())
    if (not isinstance(manifest, dict) or manifest.get("version") != 1 or manifest.get("repository") != FIXTURE_REPOSITORY
            or not re.fullmatch(r"[0-9a-f]{32}", str(manifest.get("run_id", "")))
            or manifest.get("run_root") != str(root)
            or manifest.get("target") != "acceptance/" + manifest["run_id"]):
        raise SetupError("Invalid run manifest; use the original isolated --run-dir.")
    return root, manifest


def run_identity(manifest):
    keys = ("run_root", "run_id", "agentscient_root", "agentscient_sha")
    if (not all(isinstance(manifest.get(key), str) and manifest[key] for key in keys)
            or not SHA.fullmatch(manifest["agentscient_sha"])):
        raise SetupError("Run manifest lacks its recorded runtime identity.")
    return {key: manifest[key] for key in keys}


def remembered_run():
    pointer = manager_root() / "last-run.json"
    if not pointer.exists():
        raise SetupError("No remembered acceptance run. Supply --run-dir or start with acceptance.sh run.")
    selected = json.loads(pointer.read_text())
    if not isinstance(selected, dict) or selected.get("version") != 1 or not isinstance(selected.get("run_root"), str):
        raise SetupError("Invalid last-run pointer; select the original --run-dir explicitly.")
    root, manifest = run_manifest(selected["run_root"])
    if selected != {"version": 1, **run_identity(manifest)}:
        raise SetupError("Remembered run identity changed; select the original --run-dir explicitly.")
    return root


def remember_run(root):
    _, manifest = run_manifest(root)
    pointer = {"version": 1, **run_identity(manifest)}
    directory = manager_root()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, prefix=".last-run-", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(pointer, handle)
        handle.write("\n")
    try:
        temporary.replace(directory / "last-run.json")
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def implicit_run_lock():
    directory = manager_root()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / "run.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError("Another acceptance run command is active. Use status, co, or collect for the remembered run.") from None
        yield


def require_previous_idle():
    if not (manager_root() / "last-run.json").exists():
        return
    root = remembered_run()
    database = root / "state/live.sqlite3"
    if not database.exists():
        _, manifest = run_manifest(root)
        if manifest.get("status") == "prepared":
            raise SetupError("The prepared remembered run has no state database. Inspect its files or select a new --run-dir explicitly.")
        return
    try:
        with contextlib.closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            active = db.execute("SELECT count(*) FROM members WHERE state NOT IN ('stopped','failed') "
                                "OR pid IS NOT NULL OR desired='running'").fetchone()[0]
            active += db.execute("SELECT count(*) FROM operations WHERE state IN ('queued','running')").fetchone()[0]
    except sqlite3.Error:
        raise SetupError("Cannot establish whether the remembered run is active. Inspect it with status/collect, or select a new --run-dir explicitly.") from None
    if active:
        raise SetupError("The remembered run still has active sessions or operations: " + str(root)
                         + ". Resume with co, inspect with collect, or stop it before starting another run."
                         + " An explicit new --run-dir allows a separate run.")


def environment():
    # Match the fixture's Git isolation, including subprocesses it starts later.
    result = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    result["GIT_TERMINAL_PROMPT"] = "0"
    return result


def capture(argv, *, cwd=None, timeout=45):
    try:
        return subprocess.run([str(item) for item in argv], cwd=cwd, env=environment(),
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        raise SetupError("Could not run " + str(argv[0]) + "; check installation/connectivity locally.") from None


def git(root, *args):
    result = capture(["git", "-C", str(root), "-c", "core.hooksPath=/dev/null",
                      "-c", "core.fsmonitor=false", "-c", "commit.gpgsign=false", *args], timeout=180)
    if result.returncode:
        raise SetupError("Git " + args[0] + " failed in " + str(root) + "; inspect that checkout locally.")
    return result.stdout.strip()


def repository(root, name, *, clean=True):
    if path(git(root, "rev-parse", "--show-toplevel")) != root:
        raise SetupError("Use the repository root: " + str(root))
    # Do not print an untrusted URL: it might contain a PAT.
    allowed = {"https://github.com/" + name, "git@github.com:" + name, "ssh://git@github.com/" + name}
    allowed |= {url + ".git" for url in allowed}
    for push in (False, True):
        urls = git(root, "remote", "get-url", *(["--push"] if push else []), "--all", "origin").splitlines()
        if len(urls) != 1 or urls[0] not in allowed:
            raise SetupError("Use one credential-free GitHub origin for " + name + " (fetch and push).")
    if clean and git(root, "status", "--porcelain"):
        raise SetupError("Checkout has local changes; commit or move them first: " + str(root))


def fixture_path(value):
    if value:
        return path(value)
    candidates = [SOURCE.parent / name for name in ("fixture-ag", "agentscient-test-repo")]
    found = [item for item in candidates if item.exists()]
    if len(found) > 1:
        raise SetupError("Both sibling fixture directories exist; select one with --fixture.")
    return found[0] if found else candidates[0]


def runtime_pin(fixture):
    workflow = fixture / ".github/workflows/acceptance.yml"
    try:
        data = json.loads(workflow.read_text())
        pins = {step["with"].get("ref", "") for job in data["jobs"].values()
                for step in job.get("steps", [])
                if step.get("with", {}).get("repository") == RUNTIME_REPOSITORY}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SetupError("Cannot read the fixture's JSON-formatted acceptance.yml; run update or select --fixture.") from None
    if len(pins) != 1 or not SHA.fullmatch(next(iter(pins))):
        raise SetupError("Fixture workflow must select one full, matching Agentscient commit in every checkout.")
    return next(iter(pins))


def exact_revision(value):
    if not SHA.fullmatch(value):
        raise argparse.ArgumentTypeError("use a full 40-character lowercase commit SHA")
    return value


def selected_revision(args, fixture):
    reference = runtime_pin(fixture)
    revision = args.runtime_revision or reference
    print("Fixture workflow runtime: " + reference)
    print("Selected runtime revision: " + revision + (" (explicit)" if args.runtime_revision else " (fixture default)"))
    if revision != reference:
        print("Fixture CI keeps its reference runtime; select an explicit revision whose Agentscient CI passed.")
    return revision


def runtime_path(args, pin):
    if args.runtime:
        return path(args.runtime)
    return manager_root() / "runtimes" / pin


def configuration_path(args):
    if args.configuration:
        return path(args.configuration)
    if args.runtime_from:
        return None
    default = Path.home() / ".config/agentscient/configuration.json"
    return default if default.is_file() else None


def development_commands(args, runtime):
    """Use dependencies.sh's owned release when neither executable was overridden."""
    if (args.php, args.composer) == ("php", "composer"):
        manifest = runtime / ".github/actions/php-runtime/toolchain.json"
        if manifest.is_file():
            tag = json.loads(manifest.read_text()).get("tag", "")
            if not re.fullmatch(r"[\w.-]+", tag) or tag in {".", ".."}:
                raise SetupError("Invalid owned toolchain tag in the selected runtime.")
            state = path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            directory = state / "agentscient/toolchains" / tag
            if all((directory / name).is_file() for name in ("php", "composer")):
                return str(directory / "php"), str(directory / "composer")
    return args.php, args.composer


def github_access():
    if capture(["gh", "auth", "status", "--hostname", "github.com"]).returncode:
        raise SetupError("GitHub login is unavailable. Run gh auth login --hostname github.com locally.")
    for name in (FIXTURE_REPOSITORY, RUNTIME_REPOSITORY):
        result = capture(["gh", "api", "repos/" + name])
        try:
            data = json.loads(result.stdout) if result.returncode == 0 else {}
        except ValueError:
            data = {}
        if data.get("full_name") != name:
            raise SetupError("GitHub credential cannot read " + name + ".")
        if name == FIXTURE_REPOSITORY and data.get("permissions", {}).get("push") is not True:
            raise SetupError("GitHub credential needs push access to " + name + ".")
    print("PASS GitHub login and repository access (Git transport checked when fetching)")


def update(args, fixture):
    # Validate every existing checkout before the first fetch or local change.
    if fixture.exists():
        repository(fixture, FIXTURE_REPOSITORY)
        if git(fixture, "branch", "--show-current") != "main":
            raise SetupError("Fixture update requires main; switch branches explicitly first.")
    if args.runtime and path(args.runtime).exists():
        repository(path(args.runtime), RUNTIME_REPOSITORY)
    github_access()
    if not fixture.exists():
        fixture.parent.mkdir(parents=True, exist_ok=True)
        git(fixture.parent, "clone", "--branch", "main", "--single-branch", "--",
            "https://github.com/" + FIXTURE_REPOSITORY + ".git", str(fixture))
    else:
        git(fixture, "fetch", "--no-tags", "origin", "refs/heads/main:refs/remotes/origin/main")
        git(fixture, "merge-base", "--is-ancestor", "HEAD", "refs/remotes/origin/main")
        git(fixture, "merge", "--ff-only", "refs/remotes/origin/main")
    pin = selected_revision(args, fixture)
    runtime = runtime_path(args, pin)
    if runtime.exists():
        repository(runtime, RUNTIME_REPOSITORY)
        if git(runtime, "rev-parse", "HEAD") != pin:
            raise SetupError("Runtime is at another revision; choose a new --runtime directory or omit --runtime.")
    else:
        runtime.parent.mkdir(parents=True, exist_ok=True)
        # Rename only a complete checkout. Keep a failed download separate from the destination.
        with tempfile.TemporaryDirectory(prefix=".acceptance-download-", dir=runtime.parent) as temporary:
            staged = Path(temporary) / "runtime"
            staged.mkdir()
            git(staged, "init")
            git(staged, "remote", "add", "origin", "https://github.com/" + RUNTIME_REPOSITORY + ".git")
            git(staged, "fetch", "--no-tags", "origin", pin)
            git(staged, "checkout", "--detach", pin)
            if git(staged, "rev-parse", "HEAD") != pin:
                raise SetupError("Downloaded runtime does not match the selected revision.")
            staged.rename(runtime)
    print("Updated fixture main: " + str(fixture))
    print("Selected runtime: " + str(runtime))
    if args.command != "run":
        print("Next: check --online. No active run or source-runtime branch was changed.")
    return 0


def load_fixture(fixture):
    spec = importlib.util.spec_from_file_location("agentscient_fixture_operator", fixture / "acceptance/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selected_harnesses(args):
    return [] if args.runtime_from else args.harness or ["codex", "claude", "grok"]


def native_credentials(selected):
    failed = False
    for name in selected:
        if name in {"codex", "claude"}:
            command = [name, "login", "status"] if name == "codex" else [name, "auth", "status"]
            try:
                ready = capture(command, timeout=30).returncode == 0
            except SetupError:
                ready = False
            detail = "stored login; model entitlement still requires a live run"
        elif name == "grok":
            ready = bool(os.environ.get("XAI_API_KEY", "").strip())
            if not ready:
                auth = path(os.environ.get("GROK_HOME", str(Path.home() / ".grok"))) / "auth.json"
                try:
                    # Presence only: Grok has no supported equivalent of login status.
                    ready = auth.stat().st_size <= 1048576 and bool(json.loads(auth.read_text()))
                except (OSError, ValueError):
                    ready = False
            detail = "credential source present; authentication still requires a live run"
        else:
            print("NOT_CHECKED " + name + " credentials: rely on adapter readiness and live authentication")
            continue
        print(("PASS " if ready else "FAIL ") + name + " credentials: " + detail)
        failed |= not ready
    return not failed


def print_checks(result):
    for row in result["checks"]:
        # Successful detail may contain configuration objects. Only show failed diagnostics.
        print(row["status"].upper() + " " + row["name"])
        if row["status"] == "failed" and isinstance(row.get("detail"), str):
            print("  " + row["detail"])


def probe_jev(effective):
    from devtools.jev import Jev

    seen = set()
    passed = True
    for row in effective:
        settings = row["settings"]
        if not any(settings["features"].get(name) for name in ("selection", "advice", "filtering", "completion")):
            continue
        identity = json.dumps([settings[key] for key in ("transport", "connection", "model")], sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            Jev(settings["transport"], settings["connection"]).evaluate(
                {"value": 2}, {"check": {"type": "noul", "instructions": "Is value equal to 2?"}},
                model=settings["model"])
            print("PASS Jev request/response validation: " + settings["model"])
        except (ValueError, OSError):
            passed = False
            print("FAIL Jev request: check the configured credential, endpoint/model and provider access locally.")
    if not seen:
        print("NOT_CHECKED Jev: provider-backed features are disabled")
    return passed


def preflight(args, fixture, runtime, runner, *, online):
    configuration = configuration_path(args)
    if configuration and not configuration.is_file():
        raise SetupError("Missing configuration: " + str(configuration)
                         + ". See devtools/OPERATIONS.md for the local Jev overlay/key-file setup.")
    commands = development_commands(args, runtime)
    php, composer = (shutil.which(command) for command in commands)
    if not php or not composer:
        raise SetupError("PHP/Composer unavailable. Use --php and --composer with the owned release's executable paths.")
    scenario_path = Path(args.scenario)
    if scenario_path.is_absolute() or ".." in scenario_path.parts or "\\" in str(scenario_path):
        raise SetupError("--scenario must stay inside the fixture's acceptance directory.")
    scenario = runner.read_scenario(fixture / "acceptance" / scenario_path)
    api = runner.import_runtime(runtime)
    try:
        overlay = runner.semantic_overlay(configuration, args.runtime_from)
        copied = runner.copied_runtime(api, path(args.runtime_from)) if args.runtime_from else None
        selected = runner.select_harnesses(api, selected_harnesses(args)) if not copied else []
        with tempfile.TemporaryDirectory(prefix="agentscient-preflight-") as temporary:
            root = Path(temporary)
            state = root / "state"
            project = api.readiness.project(fixture, FIXTURE_REPOSITORY, target="main", serena_image=args.serena_image)
            project["development"] = {"php": php, "composer": composer}
            api.launch.initialize(state, fixture, ["owner:external"], co="owner", project=project, runtime=True)
            selected = runner.configure_runtime(api, state, selected=selected, copied=copied)
            semantics = runner.configure_semantics(api, state, root, overlay)
            if args.command == "run":
                try:
                    api.console.initial_prompt_profile(state, profile=args.profile)
                except (ValueError, RuntimeError):
                    raise SetupError("The selected CO profile cannot accept an automatic brief. Check --profile and its harness adapter before starting a new run.") from None
            try:
                runner.require_selection_candidates(api, state, scenario, semantics)
            except ValueError:
                raise SetupError("This scenario requires eligible role alternatives. Use --runtime-from with an approved role policy that satisfies selection coverage.") from None
            credentials_ok = native_credentials(selected)
            if online:
                github_access()
            result = api.readiness.doctor(state, online=online)
            print_checks(result)
            jev_ok = probe_jev(semantics["effective"]) if online else True
    except (ValueError, OSError, RuntimeError) as error:
        # These services can reject arbitrary operator-supplied JSON. Never echo its values.
        raise SetupError("Configuration/readiness could not complete (" + type(error).__name__
                         + "). Check your overlay, enabled role profiles, scenario and executable paths.") from None
    passed = credentials_ok and jev_ok and result["status"] == "preflight_passed"
    print("PREFLIGHT " + ("PASSED" if passed else "BLOCKED") + "; authenticated product acceptance is not yet established.")
    if not online:
        print("Offline mode: Jev provider and GitHub access were not contacted. Use check --online before prepare.")
    return passed


def prepare(args, fixture, runtime, runner):
    root = path(args.run_dir)
    if root.exists() or Path(args.run_dir).expanduser().is_symlink() or not root.parent.is_dir():
        raise SetupError("--run-dir must be new, with an existing parent directory.")
    sources = [fixture, runtime] + ([path(args.runtime_from)] if args.runtime_from else [])
    if any(root == source or root in source.parents or source in root.parents for source in sources):
        raise SetupError("--run-dir must be outside the repositories and any --runtime-from state.")
    if not preflight(args, fixture, runtime, runner, online=True):
        return 1
    print("Preparing isolated run and publishing its unique fixture acceptance branch...", flush=True)
    configuration = configuration_path(args)
    php, composer = development_commands(args, runtime)
    forwarded = ["prepare", "--agentscient", str(runtime), "--run-dir", str(root),
                 "--scenario", args.scenario, "--php", php, "--composer", composer,
                 "--serena-image", args.serena_image]
    if configuration:
        forwarded += ["--configuration", str(configuration)]
    if args.runtime_from:
        forwarded += ["--runtime-from", str(path(args.runtime_from))]
    for name in selected_harnesses(args):
        forwarded += ["--harness", name]
    # Fresh interpreter: keep preparation identical to running the fixture CLI directly.
    try:
        result = subprocess.call([sys.executable, "-I", "-B", str(fixture / "acceptance/run.py"), *forwarded], env=environment())
    finally:
        if args.command == "run" and (root / "run.json").is_file():
            try:
                remember_run(root)
            except (SetupError, OSError, ValueError):
                print("Could not remember this run; use --run-dir " + str(root) + " for subsequent commands.", file=sys.stderr)
    if result == 0 and args.command != "run":
        print("Prepared. Next: doctor --online --run-dir PATH, then co --brief --run-dir PATH. Brief: " + str(root / "brief.txt"))
    return result


def existing_run(args):
    root, manifest = run_manifest(args.run_dir or remembered_run())
    # Status must not execute fixture code. Action commands let the frozen CLI
    # apply its complete validation after this inexpensive directory check.
    if args.command == "status":
        print(json.dumps({key: manifest.get(key) for key in
                          ("status", "run_root", "target", "agentscient_root", "agentscient_sha", "gates")}, indent=2))
        print("Preparation status is not final product acceptance; use verify --online.")
        return 0
    if args.command == "collect":
        forwarded = ["--run-dir", str(root)]
        if getattr(args, "output", None):
            forwarded += ["--output", str(path(args.output))]
        return subprocess.call([sys.executable, "-I", "-B", str(Path(__file__).with_name("acceptance_collect.py")),
                                *forwarded], env=environment())
    forwarded = [args.command, "--run-dir", str(root)]
    if args.command == "co" and getattr(args, "brief", False):
        forwarded.append("--brief")
    if getattr(args, "online", False):
        forwarded.append("--online")
    for name in ("profile", "work_order", "php"):
        value = getattr(args, name, None)
        if value is not None:
            forwarded += ["--" + name.replace("_", "-"), str(value)]
    return subprocess.call([sys.executable, "-I", "-B", str(root / "verifier/acceptance/run.py"), *forwarded], env=environment())


def checked_runtime(args, fixture):
    repository(fixture, FIXTURE_REPOSITORY)
    git(fixture, "merge-base", "--is-ancestor", "HEAD", "refs/remotes/origin/main")
    pin = selected_revision(args, fixture)
    runtime = runtime_path(args, pin)
    repository(runtime, RUNTIME_REPOSITORY)
    if git(runtime, "rev-parse", "HEAD") != pin:
        raise SetupError("Runtime does not match selected revision. Run update with a new runtime directory.")
    return runtime, load_fixture(fixture)


def require_initial_brief(fixture, runtime, runner):
    """Negotiate both sides before preparation can publish or use model accounts."""
    help_result = capture([sys.executable, "-I", "-B", fixture / "acceptance/run.py", "co", "--help"])
    if help_result.returncode or not re.search(r"(?<!\S)--brief(?:[\s,]|$)", help_result.stdout):
        raise SetupError("The fixture does not support automatic CO briefing. Update fixture main before using run.")
    api = runner.import_runtime(runtime)
    if ("initial_prompt" not in inspect.signature(api.console.run).parameters
            or not callable(getattr(api.console, "initial_prompt_profile", None))):
        raise SetupError("The selected runtime does not support automatic CO briefing. Select a newer reviewed revision.")


def run(args, fixture):
    implicit = args.run_dir is None
    with implicit_run_lock() if implicit else contextlib.nullcontext():
        if implicit:
            require_previous_idle()
        if args.runtime_revision is None:
            repository(SOURCE, RUNTIME_REPOSITORY)
            args.runtime_revision = git(SOURCE, "rev-parse", "HEAD")
            print("Testing clean source HEAD: " + args.runtime_revision, flush=True)
        if implicit:
            parent = manager_root() / "runs"
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            args.run_dir = str(parent / (datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12]))
        root = path(args.run_dir)
        print("Acceptance run: " + str(root), flush=True)
        result = update(args, fixture)
        if result:
            return result
        runtime, runner = checked_runtime(args, fixture)
        require_initial_brief(fixture, runtime, runner)
        result = prepare(args, fixture, runtime, runner)

        def action(command, **options):
            return existing_run(argparse.Namespace(command=command, run_dir=str(root), **options))

        def collect():
            if not (root / "run.json").is_file():
                return
            try:
                collected = action("collect") == 0
            except (SetupError, OSError, ValueError):
                collected = False
            if not collected:
                print("Diagnostics collection did not complete; retry acceptance.sh collect --run-dir " + str(root), file=sys.stderr)

        def failed(code):
            if code < 0 or code in {130, 143}:
                print("Acceptance interrupted. Run/evidence retained at " + str(root), flush=True)
                return 128 - code if code < 0 else code
            print("Acceptance is not established. Evidence retained at " + str(root), flush=True)
            collect()
            print("Inspect with acceptance.sh status or collect --run-dir " + str(root) + ". No agents were stopped.")
            return code

        if result:
            return failed(result)
        result = action("doctor", online=True)
        if result:
            return failed(result)
        print("Starting CO with the run's brief...", flush=True)
        result = action("co", profile=args.profile, brief=True)
        if result:
            return failed(result)
        print("CO exited normally. Running the frozen independent verifier...", flush=True)
        result = action("verify", online=True)
        if result:
            return failed(result)
        print("Acceptance passed. Report: " + str(root / "report.json"), flush=True)
        collect()
        return 0


def preview(args, fixture):
    print("Preview only: no credentials read, processes launched, network calls or files changed.")
    print("Routine acceptance: acceptance.sh run tests clean source HEAD, prepares and remembers a new run, submits its brief to CO, then verifies on normal exit.")
    print("Inspect its retained evidence with acceptance.sh collect. The separate setup commands below retain their fixture-pin default.")
    print("Fixture: " + str(fixture))
    if fixture.is_dir():
        pin = selected_revision(args, fixture)
        runtime = runtime_path(args, pin)
        print("Runtime: " + str(runtime))
        php, composer = development_commands(args, runtime)
        print("PHP: " + php + "; Composer: " + composer)
    elif args.runtime_revision:
        print("Fixture workflow runtime: checked after update clones main")
        print("Selected runtime revision: " + args.runtime_revision + " (explicit)")
        print("Runtime: " + str(runtime_path(args, args.runtime_revision)))
    else:
        print("Runtime: selected from fixture workflow after update clones main")
    if args.runtime_revision:
        print("Repeat --runtime-revision " + args.runtime_revision + " on update, check, and prepare; it is not saved as a new default.")
    configuration = configuration_path(args)
    print("Configuration: " + (str(configuration) if configuration else
          "imported from --runtime-from" if args.runtime_from else "runtime defaults (TYPESAFE_API_KEY environment reference)"))
    print("Harnesses: " + (", ".join(selected_harnesses(args)) or "imported approved role policy"))
    print("Scenario: " + args.scenario)
    print("Run directory: " + (str(path(args.run_dir)) if args.run_dir else "supply a new --run-dir for prepare"))
    print("Sequence: update -> check --online -> prepare --run-dir PATH -> doctor --online --run-dir PATH")
    print("Then: co --brief --run-dir PATH; verify --online --run-dir PATH; stop --run-dir PATH")
    print("update: fast-forward clean fixture main, provision a separate pinned runtime; requires GitHub access.")
    print("check: disposable private state, readiness/native credential checks; --online adds a small paid Jev request.")
    print("prepare: repeats online checks, installs dependencies in the new run, pushes its unique fixture branch.")
    print("co: launches authenticated harnesses; work can create PRs/branches and incur provider usage.")
    print("doctor/verify: retain evidence; stop: stops this run's agents and preserves its files/remote branches.")
    return 0


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("preview", "run", "update", "check", "prepare", "status", "doctor", "co", "verify", "stop", "collect"):
        item = commands.add_parser(name, help={
            "preview": "show paths and effects only (default)", "update": "fetch/fast-forward fixture main and provision pinned runtime",
            "run": "test clean source HEAD: prepare, launch CO, then verify its retained outcome",
            "check": "check credentials/config/platform in disposable state", "prepare": "check online, create run and push unique fixture branch",
            "status": "read preparation status", "doctor": "check run readiness and write doctor.json",
            "co": "start authenticated CO (provider usage)", "verify": "run frozen verifier and write evidence",
            "stop": "stop run agents, retaining files and branches", "collect": "write filtered diagnostic bundle"}[name])
        if name in RUN_COMMANDS:
            item.add_argument("--run-dir", help="original run directory (default: validated remembered run)")
        else:
            item.add_argument("--fixture", help="fixture checkout (default: sibling fixture-ag or agentscient-test-repo)")
            item.add_argument("--runtime", help="clean checkout at selected revision (default: separate revision-specific state directory)")
            item.add_argument("--runtime-revision", type=exact_revision,
                              help="explicit full commit SHA (default: clean source HEAD for run; fixture workflow pin otherwise)")
        if name in {"preview", "run", "check", "prepare"}:
            item.add_argument("--configuration", help="private semantic overlay (default: ~/.config/agentscient/configuration.json)")
            group = item.add_mutually_exclusive_group()
            group.add_argument("--runtime-from", help="existing team whose validated role/vendor policy is copied")
            group.add_argument("--harness", action="append", help="repeat to select harnesses; default: codex, claude, grok")
            item.add_argument("--scenario", default="scenario.json", help="file relative to fixture acceptance/")
            item.add_argument("--php", default="php", help="owned PHP executable or command on PATH")
            item.add_argument("--composer", default="composer", help="Composer executable or command on PATH")
            item.add_argument("--serena-image", default="agentscient-serena:v2-beta")
        if name in {"preview", "run", "prepare"}:
            item.add_argument("--run-dir", required=name == "prepare")
        if name in {"check", "doctor", "verify"}:
            item.add_argument("--online", action="store_true", help="read-only remote checks; check also sends a small Jev request")
        if name in {"co", "run"}:
            item.add_argument("--profile", help="configured CO profile")
        if name == "co":
            item.add_argument("--brief", action="store_true", help="submit this run's brief as the initial user message (first launch only)")
        if name == "collect":
            item.add_argument("--output", help="diagnostic report destination")
        if name == "verify":
            item.add_argument("--work-order", type=int)
            item.add_argument("--php")
    return result


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in {"-h", "--help"}):
        argv.insert(0, "preview")
    args = parser().parse_args(argv)
    if sys.version_info < (3, 11):
        print("Acceptance: Python 3.11 or newer is required.", file=sys.stderr)
        return 1
    try:
        if args.command in RUN_COMMANDS:
            return existing_run(args)
        fixture = fixture_path(args.fixture)
        if args.command == "preview":
            return preview(args, fixture)
        if args.command == "update":
            return update(args, fixture)
        if args.command == "run":
            return run(args, fixture)
        runtime, runner = checked_runtime(args, fixture)
        if args.command == "prepare":
            return prepare(args, fixture, runtime, runner)
        return 0 if preflight(args, fixture, runtime, runner, online=args.online) else 1
    except SetupError as error:
        print("Acceptance: " + str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError, AttributeError) as error:
        print("Acceptance: operation failed (" + type(error).__name__ + "); check paths and compatible fixture/runtime revisions.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Acceptance: interrupted; any created run/evidence is retained.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    # -I/-B also protect the checkouts from accidental import shadowing/bytecode writes.
    safe_environment = environment()
    os.environ.clear()
    os.environ.update(safe_environment)
    raise SystemExit(main())
