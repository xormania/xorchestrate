"""Build distributions and exercise isolated installed consumers through public inputs."""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
import zipfile
from pathlib import Path

from scripts.ci.release_identity import package_version
from scripts.testkit.adapters.evidence import (
    RecordingRunner,
    allocate_run,
    capture_candidate,
    digest,
    verify_candidate,
    write_json,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "xorchestrate"
ALIAS, ALIAS_PROJECT = "xork", "packaging/xork"
COMMANDS = ("xorchestrate", "xo")

# Runs under the consumer's isolated interpreter, from outside the checkout.
# Expected behavior is stated at the CLI/MCP boundary, not read from source.
CONSUMER = r'''
import importlib.metadata
import json
import os
import pathlib
import subprocess
import sys

import xorchestrate
from xorchestrate.adapters.native_gateway import LocalGateway
from xorchestrate.adapters.telemetry_collector import sqlite as telemetry_corpus  # the collector ships in the package

prefix = pathlib.Path(sys.prefix).resolve()
package = pathlib.Path(xorchestrate.__file__).resolve()
assert package.is_relative_to(prefix), ("not an installed consumer", str(package))
assert pathlib.Path(telemetry_corpus.__file__).resolve().is_relative_to(prefix), "collector missing from package"
installed = {item.metadata["Name"].lower().replace("_", "-"): item.version
             for item in importlib.metadata.distributions()}
assert not {"pytest", "ruff", "build"}.intersection(installed), installed
# The xork alias installs exactly its own version of xorchestrate.
assert "xork" not in installed or installed["xork"] == installed["xorchestrate"], installed
console = prefix / "bin" / "xorchestrate"
short = prefix / "bin" / "xo"
assert short.is_file(), "the xo command is missing"
# A private home: the consumer never reads or writes the account's ~/.xorchestrate.
work = pathlib.Path.cwd()
environment = {**os.environ, "XORCHESTRATE_HOME": str(work / "home")}

def invoke(arguments, requests=None):
    try:
        result = subprocess.run(arguments, input=requests, capture_output=True, text=True,
                                timeout=30, check=True, env=environment)
    except subprocess.CalledProcessError as error:
        for stream, value in ((sys.stdout, error.stdout or ""), (sys.stderr, error.stderr or "")):
            if len(value) > 16384:
                stream.write("[child output truncated to its final 16384 characters]\n")
            stream.write(value[-16384:])
        raise
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]

# `xo` is the same launcher as `xorchestrate`.
assert invoke([str(short), "--version"]) == invoke([str(console), "--version"])

# No project, no run: the installed launcher refuses and names `xorchestrate init`.
refused = subprocess.run([str(console), "describe"], capture_output=True, text=True, timeout=30, env=environment)
assert refused.returncode == 2 and "xorchestrate init" in json.loads(refused.stderr)["error"]["message"], refused
invoke([str(console), "setup", str(work / "companion")])
subprocess.run(["git", "init", "-q", str(work / "app")], check=True, timeout=30)
initialized, = invoke([str(console), "init", str(work / "app")])
assert initialized["approval"]["state"] == "required", initialized
invoke([str(console), "approve", str(work / "app")])
os.chdir(work / "app")
description, = invoke([str(console), "describe"])
assert {"create_work", "list_work"} <= {item["name"] for item in description["operations"]}
assert invoke([str(short), "describe"]) == [description]
initial, = invoke([str(console), "call", "list_work", "--json", "{}"])
assert initial["work"] == [], "Consumer inherited another artifact's application state"
created, = invoke([str(console), "call", "create_work", "--json", '{"goal":"Installed artifact witness"}'])
work = created["work"]
listed, = invoke([str(console), "call", "list_work", "--json", "{}"])
assert work["id"] in {item["id"] for item in listed["work"]}
packets = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_work", "arguments": {}}},
]
responses = invoke([str(console), "serve"], "".join(json.dumps(item) + "\n" for item in packets))
by_id = {item["id"]: item for item in responses}
assert len(responses) == len(by_id) == 3 and set(by_id) == {1, 2, 3}
assert by_id[1]["result"]["serverInfo"]["name"] == "xorchestrate"
assert "list_work" in {item["name"] for item in by_id[2]["result"]["tools"]}
assert by_id[3]["result"]["isError"] is False
assert work["id"] in {item["id"] for item in by_id[3]["result"]["structuredContent"]["work"]}

# The native launcher executes its installed file by absolute path using only
# stdlib imports. This checks gateway transport, not authenticated agent behavior.
gateway = LocalGateway(lambda action, payload: {"tools": [{"name": "installed-witness", "inputSchema": {"type": "object"}}]})
try:
    spec = gateway.mcp_spec()
    assert pathlib.Path(spec["args"][0]).resolve().is_relative_to(prefix)
    replies = invoke([sys.executable, "-I", *spec["args"]],
                     "".join(json.dumps(item) + "\n" for item in packets[:2]))
    indexed = {item["id"]: item for item in replies}
    assert indexed[2]["result"]["tools"][0]["name"] == "installed-witness"
finally:
    gateway.close()
print(json.dumps({"status": "passed", "package_path": str(package), "executable": str(console), "cwd": str(pathlib.Path.cwd()),
                  "installed": installed, "checks": ["short_command", "no_project_refusal", "project_setup", "console_describe", "persisted_cli", "mcp_read",
                             "absolute_gateway"]}))
'''


def distribution_name(artifact):
    """The normalized distribution name in a wheel or sdist filename."""
    name = Path(artifact).name
    stem = name.split("-")[0] if name.endswith(".whl") else name.removesuffix(".tar.gz").rsplit("-", 1)[0]
    return stem.lower().replace("_", "-").replace(".", "-")


def install_paths(*, wheel, version, workspace, uv, alias_wheel=None):
    """Supported launcher install paths that run offline from the candidate.

    Each entry is ``(name, install argv, environment, tool, expected install_source,
    expected upgrade_command, installs the candidate wheel)``; ``tool`` names the
    tool environment. The expected values are stated here, independently of the
    launcher's own mapping. The index paths install by a pinned name from a private
    flat index holding only candidate wheels, so the launcher must offer the
    unpinned reinstall; the public index is exercised only after a release is
    published. With the alias wheel, ``uv tool install xork`` resolves xorchestrate
    from that index too.
    """
    index = workspace / "index"
    index.mkdir(mode=0o700)
    shutil.copy2(wheel, index / wheel.name)
    distribution_version = wheel.name.split("-")[1]
    repository, local = workspace / "git-source", workspace / "local-source"
    paths = [
        ("index", ["--find-links", index, f"{PACKAGE}=={distribution_version}"], PACKAGE, "index",
         ["uv", "tool", "install", f"{PACKAGE}@latest"], True),
        ("archive", [wheel], PACKAGE, "archive", ["uv", "tool", "install", "--force", "<newer wheel or sdist>"], True),
        ("git", [f"git+{repository.as_uri()}@v{version}"], PACKAGE, "git",
         ["uv", "tool", "install", "--force", f"git+{repository.as_uri()}@<tag>"], False),
        ("local", [local], PACKAGE, "local", ["uv", "tool", "install", "--force", str(local)], False),
    ]
    if alias_wheel is not None:
        shutil.copy2(alias_wheel, index / alias_wheel.name)
        paths.append(("alias-index", ["--find-links", index, f"{ALIAS}=={distribution_version}"], ALIAS, "index",
                      ["uv", "tool", "install", f"{ALIAS}@latest"], True))
    for name, target, tool, source, upgrade, from_wheel in paths:
        environment = {"UV_TOOL_DIR": str(workspace / f"tools-{name}"),
                       "UV_TOOL_BIN_DIR": str(workspace / f"bin-{name}")}
        yield name, [uv, "tool", "install", *target], environment, tool, source, shlex.join(upgrade), from_wheel


def wheel_mismatches(wheel, prefix):
    """Files of ``wheel`` (outside its metadata) that the environment at ``prefix`` lacks or holds differently."""
    sites = list(Path(prefix).glob("lib/python*/site-packages"))
    if len(sites) != 1:
        return ["site-packages not found"]
    with zipfile.ZipFile(wheel) as archive:
        members = [item for item in archive.infolist()
                   if not item.is_dir() and ".dist-info/" not in item.filename and ".data/" not in item.filename]
        mismatched = [item.filename for item in members if not (sites[0] / item.filename).is_file()
                      or (sites[0] / item.filename).read_bytes() != archive.read(item)]
    return mismatched if members else ["wheel has no package files"]


def run(*, root=ROOT, output, artifacts=(), uv="uv", timeout=180, paths=True):
    """Retain build/install/protocol receipts for fresh runtime-only consumers."""
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.is_relative_to(root):
        raise ValueError("Package evidence must be outside the candidate")
    directory = allocate_run(output)
    runner = RecordingRunner(directory / "commands")
    report = {"schema_version": 1, "status": "failed", "directory": str(directory),
              "runtime": {"python": sys.version}, "artifacts": []}
    stage = "snapshot"

    def command(argv, *, cwd=directory, env=None):
        result = runner.run(tuple(map(str, argv)), cwd=str(cwd), env={"PYTHONPATH": "", "PYTHONHOME": "", **(env or {})},
                            timeout=timeout)
        if result.exit != 0:
            raise RuntimeError(f"{stage} command failed (exit {result.exit}); inspect retained command receipts")
        return result.stdout

    try:
        report["candidate"] = capture_candidate(root, directory / "snapshot")
        source = directory / "snapshot/content"
        stage = "dependency export"
        runtime = directory / "runtime-constraints.txt"
        builder = directory / "build-constraints.txt"
        export = [uv, "export", "--locked", "--no-default-groups", "--no-emit-project", "--no-hashes",
                  "--no-header", "--no-annotate", "--format", "requirements.txt"]
        command([*export, "--output-file", runtime], cwd=source)
        command([*export, "--group", "validation", "--output-file", builder], cwd=source)
        report["constraints"] = {"runtime_sha256": digest(runtime.read_bytes()), "build_sha256": digest(builder.read_bytes())}
        if artifacts:
            selected = [Path(path).resolve() for path in artifacts]
        else:
            stage = "distribution build"
            destination = directory / "distributions"
            for project in (source, source / ALIAS_PROJECT):
                command([sys.executable, "-m", "build", "--no-isolation", "--outdir", destination, project])
            selected = sorted(destination.glob("*.whl")) + sorted(destination.glob("*.tar.gz"))
            for name in (PACKAGE, ALIAS):
                built = [path for path in selected if distribution_name(path) == name]
                if (len(built) != 2 or sum(path.suffix == ".whl" for path in built) != 1
                        or sum(path.name.endswith(".tar.gz") for path in built) != 1):
                    raise ValueError(f"Build must produce one wheel and one source distribution of {name}")
            if len(selected) != 4:
                raise ValueError(f"Build produced unexpected files: {[path.name for path in selected]}")
        stage = "artifact selection"
        if not selected:
            raise ValueError("Select at least one installable artifact")
        if any(path.suffix != ".whl" and not path.name.endswith(".tar.gz") for path in selected):
            raise ValueError("Select a wheel or source distribution")
        if any(distribution_name(path) not in {PACKAGE, ALIAS} for path in selected):
            raise ValueError(f"Select distributions of {PACKAGE} or {ALIAS}")
        wheels = [path for path in selected if path.suffix == ".whl" and distribution_name(path) == PACKAGE]
        alias_wheels = [path for path in selected if path.suffix == ".whl" and distribution_name(path) == ALIAS]
        aliased = [path for path in selected if distribution_name(path) == ALIAS]
        if aliased and len(wheels) != 1:
            raise ValueError(f"{ALIAS} artifacts need exactly one candidate {PACKAGE} wheel to install with")
        # The alias resolves xorchestrate from a private flat index holding only the candidate wheel.
        candidates = directory / "candidate-index"
        if aliased:
            candidates.mkdir(mode=0o700)
            shutil.copy2(wheels[0], candidates / wheels[0].name)
        # The package's own artifacts first, then the alias's.
        ordered = sorted(selected, key=lambda path: distribution_name(path) == ALIAS)
        for number, artifact in enumerate(ordered):
            kind = "wheel" if artifact.suffix == ".whl" else "sdist"
            alias = distribution_name(artifact) == ALIAS
            label = f"{ALIAS} {kind}" if alias else kind
            entry = {"kind": kind, "distribution": distribution_name(artifact), "filename": artifact.name,
                     "sha256": digest(artifact.read_bytes()), "bytes": artifact.stat().st_size, "status": "failed"}
            report["artifacts"].append(entry)
            consumer = directory / f"consumer-{number}"
            stage = label + " consumer environment"
            command([sys.executable, "-m", "venv", "--without-pip", consumer])
            python = consumer / "bin/python"
            stage = label + " artifact install"
            links = ["--find-links", candidates] if alias else []
            command([uv, "pip", "install", "--python", python, "--constraints", runtime,
                     "--build-constraints", builder, *links, artifact])
            if alias:
                stage = label + " candidate files"
                mismatched = wheel_mismatches(wheels[0], consumer)
                if mismatched:
                    raise ValueError(f"{ALIAS} did not install the candidate {PACKAGE} wheel: {mismatched[:5]}")
            stage = label + " public consumer assertions"
            working = directory / f"work-{number}"
            working.mkdir(mode=0o700)
            observations = json.loads(command([python, "-I", "-c", CONSUMER], cwd=working))
            if alias and observations["installed"].get(ALIAS) is None:
                raise ValueError(f"{ALIAS} consumer does not have {ALIAS} installed")
            entry.update(status="passed", consumer=observations)
        if paths and len(wheels) == 1:
            report["install_paths"] = []
            workspace = directory / "install-paths"
            workspace.mkdir(mode=0o700)
            version = package_version(source, PACKAGE)
            stage = "git install source"
            repository = workspace / "git-source"
            shutil.copytree(source, repository, symlinks=True)
            shutil.copytree(source, workspace / "local-source", symlinks=True)
            identity = ["git", "-c", "user.name=package-acceptance", "-c", "user.email=package-acceptance@invalid",
                        "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
            command(["git", "init", "-q", repository])
            command(["git", "-C", repository, "add", "-A"])
            command([*identity, "-C", repository, "commit", "-q", "-m", "candidate"])
            command([*identity, "-C", repository, "tag", "-a", f"v{version}", "-m", f"v{version}"])
            commit = command(["git", "-C", repository, "rev-parse", "HEAD"]).strip()
            shared = {"UV_CACHE_DIR": str(workspace / "uv-cache"), "UV_PYTHON_DOWNLOADS": "never"}
            pinned = ["--python", sys.executable, "--constraints", runtime, "--build-constraints", builder]
            for number, (name, install, environment, tool, expected, upgrade, from_wheel) in enumerate(
                    install_paths(wheel=wheels[0], version=version, workspace=workspace, uv=uv,
                                  alias_wheel=alias_wheels[0] if len(alias_wheels) == 1 else None)):
                entry = {"path": name, "installer": "uv-tool", "status": "failed"}
                report["install_paths"].append(entry)
                environment = {**shared, **environment}
                stage = f"{name} tool install"
                command([*install, *pinned], env=environment)
                stage = f"{name} launcher identity"
                # Each command the tool exposes reports the same identity.
                reports = [json.loads(command([Path(environment["UV_TOOL_BIN_DIR"]) / executable, "--version"],
                                              env=environment)) for executable in COMMANDS]
                identity_report = reports[0]
                if any(item != identity_report for item in reports):
                    raise ValueError(f"{name} install commands {COMMANDS} report different identities: {reports}")
                expected_commit = commit if name == "git" else None
                if (identity_report.get("install_source") != expected
                        or identity_report.get("commit") != expected_commit
                        or identity_report.get("upgrade_command") != upgrade):
                    raise ValueError(f"{name} install reported {identity_report}")
                if from_wheel:
                    stage = f"{name} candidate files"
                    mismatched = wheel_mismatches(wheels[0], Path(environment["UV_TOOL_DIR"]) / tool)
                    if mismatched:
                        raise ValueError(f"{name} install does not hold the candidate wheel: {mismatched[:5]}")
                stage = f"{name} public consumer assertions"
                working = directory / f"path-work-{number}"
                working.mkdir(mode=0o700)
                python = Path(environment["UV_TOOL_DIR"]) / tool / "bin/python"
                observations = json.loads(command([python, "-I", "-c", CONSUMER], cwd=working))
                entry.update(status="passed", version=identity_report, consumer=observations)
            entry = {"path": "uvx", "installer": "uvx", "status": "failed"}
            report["install_paths"].append(entry)
            stage = "uvx launcher identity"
            ephemeral = [uv, "tool", "run", *pinned, "--from", wheels[0], PACKAGE]
            identity_report = json.loads(command([*ephemeral, "--version"], env=shared))
            short = json.loads(command([*ephemeral[:-1], "xo", "--version"], env=shared))
            if short != identity_report:
                raise ValueError(f"uvx xo reported {short}, xorchestrate reported {identity_report}")
            if (identity_report.get("install_source") != "uvx" or identity_report.get("commit") is not None
                    or identity_report.get("upgrade_command") != shlex.join(
                        ["uvx", "--refresh", "--from", "<newer wheel or sdist>", PACKAGE])):
                raise ValueError(f"uvx run reported {identity_report}")
            stage = "uvx public consumer assertions"
            working = directory / "path-work-uvx"
            working.mkdir(mode=0o700)
            # The application runs only inside a set-up project, with a private home.
            scoped = {**shared, "XORCHESTRATE_HOME": str(working / "home")}
            command([*ephemeral, "setup", working / "companion"], cwd=working, env=scoped)
            command(["git", "init", "-q", working / "app"])
            command([*ephemeral, "init", working / "app"], cwd=working, env=scoped)
            command([*ephemeral, "approve", working / "app"], cwd=working, env=scoped)
            described = json.loads(command([*ephemeral, "--project", working / "app", "describe"], cwd=working,
                                           env=scoped))
            if "list_work" not in {item["name"] for item in described["operations"]}:
                raise ValueError("uvx describe did not list application operations")
            entry.update(status="passed", version=identity_report)
        report["candidate_integrity"] = verify_candidate(root, report["candidate"])
        if not report["candidate_integrity"]["passed"]:
            raise ValueError("Package candidate changed during verification")
        report["status"] = "passed"
    except Exception as error:
        report["error"] = {"stage": stage, "type": type(error).__name__, "message": str(error)}
    finally:
        report["commands"] = [str(Path(item["directory"]) / "command.json") for item in runner.records]
        write_json(directory / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", default=[], help="Verify a prebuilt artifact instead of building")
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    parser.add_argument("--no-install-paths", dest="paths", action="store_false",
                        help="Skip the uv tool, git, local and uvx launcher install paths")
    args = parser.parse_args(argv)
    report = run(output=args.output, artifacts=args.artifact, uv=args.uv, paths=args.paths)
    print(json.dumps(report, allow_nan=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
