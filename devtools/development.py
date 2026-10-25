"""Installed Symfony capabilities and named checks, invoked behind the MCP surface."""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .artifacts import Artifacts
from .role_packages import adapter
from .workspaces import Workspaces, git, project_config

DEFAULT_CHECKS = {
    "container": ["php", "bin/console", "lint:container", "--no-interaction"],
    "configuration": ["php", "bin/console", "lint:yaml", "config", "--parse-tags", "--no-interaction"],
    "tests": ["php", "vendor/bin/phpunit"],
}


def configured(store):
    return isinstance(project_config(store).get("development"), dict)


def capabilities(spec):
    name = {"type": "string", "minLength": 1, "maxLength": 160, "pattern": "[A-Za-z][A-Za-z0-9_.:-]*"}
    values = {"type": "object", "maxProperties": 30}
    return [
        spec(
            "framework_inspect",
            "Inspect installed Makers, Mate diagnostics, packages or operator-approved Symfony Console introspection in your job workspace. Results are retained and delivered automatically.",
            {
                "kind": {"type": "string", "enum": ["makers", "mate", "mate_resource", "packages", "console"]},
                "name": name,
                "parameters": values,
                "resource": {"type": "string", "maxLength": 240,
                             "pattern": "^symfony-profiler://profile/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)?$"},
                "package": {"type": "string", "maxLength": 160,
                            "pattern": "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.*-]+$"},
            },
            ("kind",),
        ),
        spec(
            "framework_generate",
            "Run an installed Symfony Maker with named arguments/options; preserve its native attributes and conventions. Results are retained and delivered automatically.",
            {
                "maker": {**name, "pattern": "make:[A-Za-z0-9_:-]+"},
                "arguments": values,
                "options": values,
            },
            ("maker",),
        ),
        spec(
            "work_check",
            "Run the configured validation for your current work and retain measured results; completion is delivered automatically.",
            {
                "check": {**name, "description": "Optional configured check name; omit for the full configured suite."},
            },
        ),
    ]


class Development:
    def __init__(self, store, member):
        self.store, self.member = store, member
        self.workspaces = Workspaces(store, member)
        self.config = project_config(store).get("development", {})

    def execution_scope(self):
        scope = self.workspaces.scope()
        if adapter(scope["role"]) == "co":
            raise ValueError("CO hands work to XO; this role cannot execute application development processes.")
        return scope

    def require(self, capability):
        from .role_packages import require_capability

        scope = self.execution_scope()
        require_capability(scope["role"], capability)
        return scope

    def run(self, root, argv, *, timeout=120):
        """Only callers in this module construct commands; the MCP accepts no argv."""
        if self.execution_scope()["paused"]:
            raise ValueError("This work is paused; no new development process was started.")
        argv = [self.config.get("php", "php") if x == "php" and i == 0 else x for i, x in enumerate(argv)]
        env = {
            k: v
            for k, v in os.environ.items()
            if k in {"PATH", "HOME", "LANG", "TMPDIR", "COMPOSER_HOME", "COMPOSER_CACHE_DIR"}
        }
        # Let the project's bootstrap and test configuration select APP_ENV and
        # APP_DEBUG. Forcing dev here can override PHPUnit's server variables
        # through Symfony's preferred $_ENV values and disable test isolation.
        env["COMPOSER_NO_INTERACTION"] = "1"
        php = Path(self.config.get("php", "php"))
        if php.is_absolute():
            env["PATH"] = str(php.parent) + os.pathsep + env.get("PATH", "")
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                argv,
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                raise ValueError(
                    "Development operation timed out; changes were preserved. Inspect before retrying."
                ) from None
            size = output.tell()
            output.seek(0)
            text = output.read(2 * 1024 * 1024).decode(errors="replace")
        from .github_http import GitHubHTTP

        result = {
            "exit_code": process.returncode,
            "output": GitHubHTTP().clean(text),
            "truncated": size > 2 * 1024 * 1024,
        }
        if process.returncode < 0:
            try:
                result["signal"] = signal.Signals(-process.returncode).name
            except ValueError:
                result["signal"] = f"SIG{-process.returncode}"
        return result

    def prepare(self, root):
        self.execution_scope()
        root = Path(root)
        lock = root / "composer.lock"
        if not lock.is_file():
            raise ValueError("The project needs its committed Composer lock before using Symfony development tools.")
        fingerprint = hashlib.sha256(lock.read_bytes() + (root / "composer.json").read_bytes()).hexdigest()
        marker = root / "vendor/.agentscient-dependencies"
        if marker.is_file() and marker.read_text() == fingerprint and (root / "vendor/autoload.php").is_file():
            return
        # A worktree owns its vendor directory: a symlink to another checkout would load the wrong application.
        if (root / "vendor").is_symlink():
            raise ValueError("The job's vendor directory must not be a symlink to another checkout.")
        result = self.run(
            root,
            [self.config.get("composer", "composer"), "install", "--prefer-dist", "--no-interaction", "--no-progress"],
            timeout=600,
        )
        if result["exit_code"]:
            self.evidence("dependencies.txt", result)
            raise ValueError("Locked dependency installation failed: " + result["output"][-1800:])
        marker.write_text(fingerprint)

    def evidence(self, name, result):
        scope = self.workspaces.control.current()
        saved = Artifacts(self.store, self.member).ingest(
            name,
            io.BytesIO(json.dumps(result, ensure_ascii=False).encode()),
            "application/json",
            work_order=scope["work_order"],
            job=scope["job"],
        )
        return "artifact:" + str(saved["artifact"]["id"])

    def _json(self, root, argv):
        result = self.run(root, argv)
        if result["exit_code"]:
            raise ValueError(result["output"][-1800:])
        try:
            return json.loads(result["output"])
        except ValueError:
            raise ValueError(
                "Installed command did not return valid JSON metadata: " + result["output"][:600]
            ) from None

    @contextmanager
    def diagnostic_root(self, root):
        """Planner subprocesses own a disposable project and independent Git data."""
        scope = self.execution_scope()
        if scope["paused"]:
            raise ValueError("This work is paused; no new development process was started.")
        root = Path(root)
        if adapter(scope["role"]) != "planner":
            yield root
            return
        before = self._files(root)
        with tempfile.TemporaryDirectory(prefix="agentscient-diagnostics-") as temporary:
            staged = Path(temporary) / "project"
            # A copied worktree .git file would still address the producer's
            # index/refs. The Git transport creates independent objects too.
            git(root, "clone", "--no-local", "--no-checkout", "--", str(root), str(staged))
            git(staged, "remote", "remove", "origin")
            git(staged, "read-tree", "HEAD")
            shutil.copytree(root, staged, dirs_exist_ok=True, symlinks=True,
                            ignore=shutil.ignore_patterns(".git"))
            self._isolate_links(root, staged, before)
            if self._files(staged) != before or self._files(root) != before:
                raise ValueError("The project changed while preparing diagnostics; no diagnostic process was started.")
            # Retain profiler data for Mate resources, but never load a compiled
            # container that still embeds the producer's absolute project path.
            profiles = []
            cache = staged / "var/cache"
            if not (staged / "var").is_symlink() and not cache.is_symlink():
                for index, profile in enumerate(cache.glob("*/profiler")):
                    if profile.is_dir() and not profile.is_symlink():
                        saved = Path(temporary) / ("profiler-" + str(index))
                        profile.rename(saved)
                        profiles.append((profile, saved))
            self._fresh_cache(staged)
            for profile, saved in profiles:
                profile.parent.mkdir(parents=True, exist_ok=True)
                saved.rename(profile)
            yield staged

    def inspect(self, kind, name=None, parameters=None, resource=None, package=None):
        self.require("framework_inspect")
        if kind == "packages":
            if name or parameters is not None or resource or not package or not re.fullmatch(
                r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.*-]+", package
            ):
                raise ValueError("Supply only a package name or vendor/package* filter")
        elif package:
            raise ValueError("package is only available for kind packages")
        if kind == "mate_resource":
            if name or parameters is not None or not resource or not re.fullmatch(
                r"symfony-profiler://profile/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)?", resource
            ):
                raise ValueError("Supply only a profiler resource URI returned by Mate")
        elif resource:
            raise ValueError("resource is only available for kind mate_resource")
        with self.workspaces.use() as (_, workspace), self.diagnostic_root(workspace["root"]) as root:
            self.prepare(root)
            if kind == "makers":
                if parameters:
                    raise ValueError("Use framework_generate to run a Maker.")
                if name and not name.startswith("make:"):
                    raise ValueError("Choose an installed make: command.")
                value = self._json(
                    root,
                    [
                        "php",
                        "bin/console",
                        *(["help", name] if name else ["list", "make"]),
                        "--format=json",
                        "--no-interaction",
                    ],
                )
                if not name:
                    value = {
                        "makers": [
                            {"name": r["name"], "description": r["description"]}
                            for r in value["commands"]
                            if r["name"].startswith("make:")
                        ]
                    }
            elif kind == "console":
                from .assignments import policy

                patterns = policy(self.store, project_config(self.store))["inspection_commands"]
                installed = self._json(root, ["php", "bin/console", "list", "--format=json", "--no-interaction"])["commands"]
                commands = {row["name"]: row for row in installed
                            if any(fnmatch.fnmatchcase(row["name"], pattern) for pattern in patterns)}
                if name is None:
                    if parameters is not None:
                        raise ValueError("Name an installed inspection command before supplying parameters.")
                    value = {"commands": [{"name": key, "description": row["description"]} for key, row in commands.items()]}
                else:
                    if name not in commands:
                        raise ValueError("Choose an installed command allowed by inspection_commands.")
                    metadata = self._json(root, ["php", "bin/console", "help", name, "--format=json", "--no-interaction"])
                    if metadata["name"] != name:
                        raise ValueError("Installed command metadata changed; inspect again.")
                    if parameters is None:
                        value = metadata
                    else:
                        if set(parameters) - {"arguments", "options"} or any(not isinstance(v, dict) for v in parameters.values()):
                            raise ValueError("Console parameters accept named arguments and options objects.")
                        value = self.run(root, self.maker_argv(metadata, parameters.get("arguments", {}), parameters.get("options", {})))
            elif kind == "mate_resource":
                value = self._json(root, ["vendor/bin/mate", "resources:read", resource, "--format=json"])
            elif kind == "packages":
                value = self._json(root, [self.config.get("composer", "composer"), "show", "--format=json", package])
            else:
                tools = self._json(root, ["vendor/bin/mate", "tools:list", "--format=json"])["tools"]
                if name:
                    if name not in tools:
                        raise ValueError("Choose a diagnostic from the installed Mate tools.")
                    value = (
                        tools[name]
                        if parameters is None
                        else self._json(
                            root,
                            [
                                "vendor/bin/mate",
                                "tools:call",
                                name,
                                "--format=json",
                                "--json=" + json.dumps(parameters),
                            ],
                        )
                    )
                else:
                    if parameters:
                        raise ValueError("Name the installed Mate diagnostic to call.")
                    value = {
                        "tools": [
                            {"name": name, "description": row.get("description")}
                            for name, row in tools.items()
                        ]
                    }
            return self.result("framework-inspection.json", value)

    @staticmethod
    def maker_argv(metadata, arguments, options):
        definition = metadata["definition"]
        positional = {k: v for k, v in definition["arguments"].items() if k != "command"}
        if arguments.keys() - positional.keys() or options.keys() - definition["options"].keys():
            raise ValueError("Use argument and option names from this installed Maker's metadata.")
        forbidden = {
            "env",
            "no-debug",
            "help",
            "version",
            "quiet",
            "silent",
            "verbose",
            "ansi",
            "no-ansi",
            "no-interaction",
            "profile",
        }
        if options.keys() & forbidden:
            raise ValueError("Runtime and global console options are managed by the service.")
        argv = ["php", "bin/console", metadata["name"], "--no-interaction", "--no-ansi"]
        for key, value in options.items():
            option = definition["options"][key]
            if not option["accept_value"]:
                if type(value) is not bool:
                    raise ValueError("Flag options must be boolean.")
                if value:
                    argv.append("--" + key)
            else:
                values = value if isinstance(value, list) and option["is_multiple"] else [value]
                for item in values:
                    if not isinstance(item, (str, int, float)) or isinstance(item, bool):
                        raise ValueError("Maker option values must be scalar, or arrays for multiple options.")
                    argv.append("--" + key + "=" + str(item))
        argv.append("--")
        for key, argument in positional.items():
            value = arguments.get(key, argument.get("default"))
            if value is None:
                if argument["is_required"]:
                    raise ValueError("Missing Maker argument: " + key)
                value = ""
            values = value if isinstance(value, list) and argument["is_array"] else [value]
            if any(not isinstance(item, (str, int, float)) or isinstance(item, bool) for item in values):
                raise ValueError("Maker arguments must be scalar, or arrays for variadic arguments.")
            argv.extend(str(item) for item in values)
        return argv

    def generate(self, maker, arguments=None, options=None):
        from .assignments import policy

        self.require("framework_generate")
        if maker in policy(self.store, project_config(self.store))["denied_makers"]:
            raise ValueError("Managed agents use maintained Makers; they cannot generate or modify Makers.")
        with self.workspaces.use(write=True) as (scope, workspace):
            if adapter(scope["role"]) not in {"coder", "test_writer"}:
                raise ValueError("This role cannot generate Symfony code.")
            if adapter(scope["role"]) == "test_writer" and maker != "make:test":
                raise ValueError("Test writers use the installed make:test Maker.")
            root = Path(workspace["root"])
            before = self._files(root)
            with tempfile.TemporaryDirectory(prefix="agentscient-maker-") as temporary:
                staged = Path(temporary) / "project"
                shutil.copytree(root, staged, symlinks=True, ignore=shutil.ignore_patterns(".git"))
                self._isolate_links(root, staged, before)
                if self._files(staged) != before:
                    raise ValueError("The project changed while preparing generation; no generated changes were applied.")
                self._fresh_cache(staged)
                self.prepare(staged)
                metadata = self._json(staged, ["php", "bin/console", "help", maker, "--format=json", "--no-interaction"])
                if metadata["name"] != maker or not maker.startswith("make:"):
                    raise ValueError("Choose an installed Symfony Maker.")
                self.workspaces.scope(write=True)
                result = self.run(staged, self.maker_argv(metadata, arguments or {}, options or {}))
                after = self._files(staged)
                changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
                # Composer and Console may write ignored dependencies/cache in
                # staging. They are discarded, never copied into the workspace.
                ignored = set()
                if changed:
                    checked = git(root, "check-ignore", "--stdin", "-z", check=False,
                                  input=("\0".join(changed) + "\0").encode(), env={"GIT_LITERAL_PATHSPECS": "0"})
                    if checked.returncode not in {0, 1}:
                        raise ValueError("Cannot establish ignored runtime paths; no generated changes were applied.")
                    ignored = set(checked.stdout.decode().split("\0"))
                changed = [path for path in changed if path not in ignored]
                if self._files(root) != before:
                    raise ValueError("The workspace changed during generation; no generated changes were applied.")
                # Job locks serialize source writers. This transaction also
                # orders binding revocation against the final source effects.
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self.workspaces.scope(write=True)
                    denied = [path for path in changed if not self.workspaces.permitted_path(path, scope["role"])]
                    if denied:
                        raise ValueError("Maker would change paths outside this role's configured paths: "
                                         + ", ".join(denied[:10]) + ". No generated changes were applied; request guidance.")
                    if any((before.get(path) or after.get(path))[0] != "file"
                           or after.get(path, ("file",))[0] != "file" for path in changed):
                        raise ValueError("Makers can generate regular project files only; no generated changes were applied.")
                    self._promote(root, staged, changed, before, Path(temporary) / "backup")
            result["changes"] = self.workspaces.changes(root)
            result["outside_role_paths"] = []
            return self.result("maker-result.json", result)

    @staticmethod
    def _files(root):
        """Snapshot file contents/modes without following project symlinks."""
        files = {}
        for directory, folders, names in os.walk(root, followlinks=False):
            folders[:] = [name for name in folders if name != ".git"]
            for name in names + [name for name in folders if (Path(directory) / name).is_symlink()]:
                if name == ".git":
                    continue
                path = Path(directory) / name
                mode = path.lstat().st_mode
                key = path.relative_to(root).as_posix()
                if stat.S_ISLNK(mode):
                    target = path.resolve()
                    value = ("link", stat.S_IMODE(mode),
                             target.relative_to(root).as_posix() if target.is_relative_to(root) else str(target))
                elif stat.S_ISREG(mode):
                    digest = hashlib.sha256()
                    with path.open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                    value = ("file", stat.S_IMODE(mode), digest.hexdigest())
                else:
                    raise ValueError("Generation needs regular project files; no generated changes were applied.")
                files[key] = value
        return files

    @staticmethod
    def _fresh_cache(staged):
        if (staged / "var").is_symlink():
            raise ValueError("Development cannot use a Symfony cache beneath a project symlink.")
        cache = staged / "var/cache"
        if cache.is_symlink() or cache.is_file():
            cache.unlink()
        elif cache.is_dir():
            shutil.rmtree(cache)

    @staticmethod
    def _isolate_links(root, staged, before):
        for path, value in before.items():
            if value[0] != "link":
                continue
            target = (root / path).resolve()
            if not target.is_relative_to(root) or ".git" in target.relative_to(root).parts:
                raise ValueError("Generation cannot use symlinks outside the project or into Git metadata.")
            link = staged / path
            link.unlink()
            link.symlink_to(os.path.relpath(staged / target.relative_to(root), link.parent))

    @staticmethod
    def _promote(root, staged, paths, before, backup):
        """Preflight every destination and roll back a failed file promotion."""
        for path in paths:
            target = root / path
            if target.is_dir():
                raise ValueError("Generation cannot replace project directories; no generated changes were applied.")
            for parent in target.parents:
                if parent == root:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise ValueError("Generation cannot write through symlinks or replace parent files.")
            if path in before:
                saved = backup / path
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
        touched, created = [], []

        def replace(source, target):
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copy2(source, temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

        try:
            for path in paths:
                target, source = root / path, staged / path
                if not source.exists():
                    target.unlink()
                    touched.append(path)
                    continue
                parents = []
                parent = target.parent
                while not parent.exists():
                    parents.append(parent)
                    parent = parent.parent
                for parent in reversed(parents):
                    parent.mkdir()
                    created.append(parent)
                replace(source, target)
                touched.append(path)
        except BaseException as failure:
            unrestored = []
            for path in reversed(touched):
                try:
                    target = root / path
                    if path in before:
                        replace(backup / path, target)
                    else:
                        target.unlink(missing_ok=True)
                except Exception:
                    unrestored.append(path)
            for parent in reversed(created):
                try:
                    parent.rmdir()
                except OSError:
                    unrestored.append(parent.relative_to(root).as_posix())
            if unrestored:
                raise ValueError("Maker promotion failed and some generated changes could not be rolled back: "
                                 + ", ".join(unrestored[:10]) + ". Inspect the preserved workspace before retrying.") from failure
            raise

    def result(self, name, value):
        evidence = self.evidence(name, value)
        text = json.dumps(value, ensure_ascii=False)
        return {
            "result": value if len(text) <= 10000 else None,
            "preview": text[:10000] if len(text) > 10000 else None,
            "evidence": evidence,
        }

    def checks(self, root, check=None):
        self.execution_scope()
        source = root
        results = []
        with self.diagnostic_root(root) as root:
            self.prepare(root)
            checks = self.config.get("checks", DEFAULT_CHECKS)
            if not checks or check is not None and check not in checks:
                raise ValueError("Choose a configured check: " + ", ".join(checks))
            for name, argv in checks.items():
                if check is not None and check != name:
                    continue
                self.workspaces.scope()
                result = self.run(root, argv, timeout=self.config.get("check_timeout", 600))
                # Retain what was measured with its output, so a passing exit code
                # cannot be mistaken for proof of an unrelated goal.
                from .telemetry import clean

                measurement = {**result, "check": name, "argv": clean(argv),
                               "source_revision": git(source, "rev-parse", "HEAD").stdout.decode().strip()}
                results.append(
                    {
                        "check": name,
                        "exit_code": result["exit_code"],
                        **({"signal": result["signal"]} if "signal" in result else {}),
                        "output": result["output"][-2500:],
                        "evidence": self.evidence("check-" + name + ".json", measurement),
                    }
                )
        return {
            "passed": all(row["exit_code"] == 0 for row in results),
            "checks": results,
            "revision": git(source, "rev-parse", "HEAD").stdout.decode().strip(),
            "uncommitted": self.workspaces.changes(source),
        }

    def check(self, check=None):
        self.require("work_check")
        with self.workspaces.use() as (_, workspace):
            return self.checks(workspace["root"], check)
