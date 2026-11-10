"""Build distributions and exercise isolated installed consumers through public inputs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from scripts.testkit.adapters.evidence import (
    RecordingRunner,
    allocate_run,
    capture_candidate,
    digest,
    verify_candidate,
    write_json,
)

ROOT = Path(__file__).resolve().parents[2]

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
console = prefix / "bin" / "xorchestrate"
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
                  "installed": installed, "checks": ["no_project_refusal", "project_setup", "console_describe", "persisted_cli", "mcp_read",
                             "absolute_gateway"]}))
'''


def run(*, root=ROOT, output, artifacts=(), uv="uv", timeout=180):
    """Retain build/install/protocol receipts for fresh runtime-only consumers."""
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.is_relative_to(root):
        raise ValueError("Package evidence must be outside the candidate")
    directory = allocate_run(output)
    runner = RecordingRunner(directory / "commands")
    report = {"schema_version": 1, "status": "failed", "directory": str(directory),
              "runtime": {"python": sys.version}, "artifacts": []}
    stage = "snapshot"

    def command(argv, *, cwd=directory):
        result = runner.run(tuple(map(str, argv)), cwd=str(cwd), env={"PYTHONPATH": "", "PYTHONHOME": ""}, timeout=timeout)
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
            command([sys.executable, "-m", "build", "--no-isolation", "--outdir", destination, source])
            selected = sorted(destination.glob("*.whl")) + sorted(destination.glob("*.tar.gz"))
            if (len(selected) != 2 or sum(path.suffix == ".whl" for path in selected) != 1
                    or sum(path.name.endswith(".tar.gz") for path in selected) != 1):
                raise ValueError("Build must produce one wheel and one source distribution")
        if not selected:
            raise ValueError("Select at least one installable artifact")
        for number, artifact in enumerate(selected):
            if artifact.suffix != ".whl" and not artifact.name.endswith(".tar.gz"):
                raise ValueError("Select a wheel or source distribution")
            kind = "wheel" if artifact.suffix == ".whl" else "sdist"
            entry = {"kind": kind, "filename": artifact.name, "sha256": digest(artifact.read_bytes()),
                     "bytes": artifact.stat().st_size, "status": "failed"}
            report["artifacts"].append(entry)
            consumer = directory / f"consumer-{number}"
            stage = kind + " consumer environment"
            command([sys.executable, "-m", "venv", "--without-pip", consumer])
            python = consumer / "bin/python"
            stage = kind + " artifact install"
            command([uv, "pip", "install", "--python", python, "--constraints", runtime,
                     "--build-constraints", builder, artifact])
            stage = kind + " public consumer assertions"
            working = directory / f"work-{number}"
            working.mkdir(mode=0o700)
            observations = json.loads(command([python, "-I", "-c", CONSUMER], cwd=working))
            entry.update(status="passed", consumer=observations)
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
    args = parser.parse_args(argv)
    report = run(output=args.output, artifacts=args.artifact, uv=args.uv)
    print(json.dumps(report, allow_nan=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
