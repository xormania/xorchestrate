"""Run pinned uv dependency auditing without republishing remote advisory prose."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

VERSION = "0.13.7"
TOKEN_NAMES = frozenset(("GH_TOKEN", "GITHUB_TOKEN"))
CONFIGURATION = ("pyproject.toml",)
SOURCES = ("uv.lock",)
LIMITATIONS = (
    "git-source-name-version-match",
    "mutable-osv-data",
    "short-response-completeness-unverified",
)
REPORT_FIELDS = frozenset((
    "schema_version", "status", "enforcement", "effective_input_sha256", "files",
    "scanner", "service", "selection", "packages", "findings", "limitations", "execution",
))
PACKAGE_FIELDS = frozenset(("name", "version", "source_kind", "source_identity", "match_basis"))
FINDING_FIELDS = frozenset((
    "kind", "id", "aliases", "package", "version", "fixed_versions", "published", "modified", "status",
))


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _child_environment():
    """Remove credentials and ambient uv policy that could alter the declared invocation."""
    return {
        name: value for name, value in os.environ.items()
        if name not in TOKEN_NAMES and not name.startswith("UV_")
    }


def _executable(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("uv executable must be absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("uv executable is unavailable")
    return resolved


def _repository_file(root, relative):
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise ValueError("Unsafe dependency-audit input path")
    path = root / name
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Dependency-audit input resolves outside the candidate or is not a file")
    return path, resolved


def _frozen_manifest(path, executable, service_url):
    manifest = json.loads(Path(path).read_bytes())
    expected_fields = {
        "identity_schema", "scope", "sources", "files", "root", "content_sha256", "tool",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise ValueError("Dependency audit requires an exact effective-input manifest")
    if manifest.get("identity_schema") != "testkit.effective-inputs.v1":
        raise ValueError("Dependency audit requires the supported effective-input schema")
    payload = {name: manifest[name] for name in ("identity_schema", "scope", "sources", "files")}
    if not _digest(manifest.get("content_sha256")) or _sha256(_canonical(payload)) != manifest["content_sha256"]:
        raise ValueError("Dependency-audit effective-input identity is malformed")

    root_name = manifest.get("root")
    if not isinstance(root_name, str) or not root_name:
        raise ValueError("Dependency-audit manifest has no candidate root")
    root = Path(root_name).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Dependency-audit candidate root is not a directory")

    scope = manifest.get("scope")
    if not isinstance(scope, dict) or scope.get("check") != "dependency-audit":
        raise ValueError("Dependency-audit manifest belongs to another check")
    if scope.get("configuration") != list(CONFIGURATION) or manifest.get("sources") != list(SOURCES):
        raise ValueError("Dependency-audit manifest has no exact project and lock selection")
    selection = scope.get("selection")
    expected_selection = {
        "groups": "all",
        "extras": "all",
        "target": "universal",
        "service_format": "osv",
        "service_url": service_url,
        "output_format": "json",
        "preview_features": "audit-command,json-output",
        "executable": str(executable),
        "version": "uv " + VERSION,
    }
    if selection != expected_selection:
        raise ValueError("Dependency-audit manifest has an unsupported execution policy")
    tool = manifest.get("tool")
    if (not isinstance(tool, dict) or tool.get("uv") != "uv " + VERSION
            or tool.get("uv_executable") != str(executable)):
        raise ValueError("Dependency-audit manifest has an unsupported tool identity")

    identities = manifest.get("files")
    if not isinstance(identities, dict):
        raise ValueError("Dependency-audit manifest has no input identities")
    current = {}
    for name in (*CONFIGURATION, *SOURCES):
        identity = identities.get(name)
        if not isinstance(identity, dict) or not _digest(identity.get("content_sha256")):
            raise ValueError("Dependency-audit input has no bound content identity")
        _, resolved = _repository_file(root, name)
        sha256 = _sha256(resolved.read_bytes())
        if sha256 != identity["content_sha256"]:
            raise ValueError("Dependency-audit input changed after effective-input capture")
        current[name] = sha256
    return manifest, root, current


def _locked_packages(raw):
    document = tomllib.loads(raw.decode("utf-8", errors="strict"))
    rows = document.get("package")
    if not isinstance(rows, list):
        raise ValueError("uv lock contains no package inventory")
    packages = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source"), dict):
            raise ValueError("uv lock contains a malformed package")
        source = row["source"]
        if "editable" in source:
            continue
        name, version = row.get("name"), row.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ValueError("uv lock contains a package without name and version")
        if set(source) == {"registry"} and isinstance(source["registry"], str) and source["registry"]:
            source_kind = "registry"
            source_identity = source["registry"]
        elif set(source) == {"git"} and isinstance(source["git"], str):
            source_kind = "git"
            source_identity = source["git"].rsplit("#", 1)[-1]
            if re.fullmatch(r"[0-9a-f]{40}", source_identity) is None:
                raise ValueError("uv lock Git package has no exact commit identity")
        else:
            raise ValueError("uv lock contains an unsupported non-editable package source")
        packages.append({
            "name": name,
            "version": version,
            "source_kind": source_kind,
            "source_identity": source_identity,
            "match_basis": "pypi-name-version",
        })
    packages.sort(key=lambda item: (item["name"], item["version"]))
    identities = [(item["name"], item["version"]) for item in packages]
    if not packages or len(identities) != len(set(identities)):
        raise ValueError("uv lock has an empty or duplicate auditable package inventory")
    return packages


def _optional_timestamp(value):
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError("uv audit finding has an invalid timestamp")
    return value


def _strings(value, label):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("uv audit finding has invalid " + label)
    if len(value) != len(set(value)):
        raise ValueError("uv audit finding repeats " + label)
    return sorted(value)


def _normalize(native, packages):
    expected_fields = {"schema", "summary", "vulnerabilities", "adverse_statuses"}
    if not isinstance(native, dict) or set(native) != expected_fields:
        raise ValueError("uv audit returned an unsupported preview report")
    if native.get("schema") != {"version": "preview"}:
        raise ValueError("uv audit returned an unsupported preview schema")
    summary = native.get("summary")
    if (not isinstance(summary, dict)
            or set(summary) != {"audited_packages", "vulnerabilities", "adverse_statuses"}
            or any(not isinstance(summary.get(name), int) or isinstance(summary[name], bool) or summary[name] < 0
                   for name in summary)):
        raise ValueError("uv audit returned a malformed summary")
    vulnerabilities, adverse = native.get("vulnerabilities"), native.get("adverse_statuses")
    if not isinstance(vulnerabilities, list) or not isinstance(adverse, list) or adverse:
        raise ValueError("uv audit returned unsupported adverse package statuses")
    if (summary["audited_packages"] != len(packages)
            or summary["vulnerabilities"] != len(vulnerabilities)
            or summary["adverse_statuses"] != 0):
        raise ValueError("uv audit summary contradicts the bound package selection")

    selected = {(item["name"], item["version"]) for item in packages}
    findings = []
    native_fields = {
        "dependency", "id", "display_id", "aliases", "summary", "description", "link",
        "fix_versions", "published", "modified",
    }
    for item in vulnerabilities:
        if not isinstance(item, dict) or set(item) != native_fields:
            raise ValueError("uv audit finding has an unsupported shape")
        dependency = item.get("dependency")
        if not isinstance(dependency, dict) or set(dependency) != {"name", "version"}:
            raise ValueError("uv audit finding has a malformed dependency")
        package, version = dependency.get("name"), dependency.get("version")
        if (package, version) not in selected:
            raise ValueError("uv audit finding names an unselected package")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("uv audit finding has no identifier")
        aliases = _strings(item.get("aliases"), "aliases")
        fixes = _strings(item.get("fix_versions"), "fixed versions")
        findings.append({
            "kind": "vulnerability",
            "id": identifier,
            "aliases": aliases,
            "package": package,
            "version": version,
            "fixed_versions": fixes,
            "published": _optional_timestamp(item.get("published")),
            "modified": _optional_timestamp(item.get("modified")),
            "status": "affected",
        })
    findings.sort(key=lambda item: (item["package"], item["version"], item["id"]))
    identities = [(item["package"], item["version"], item["id"]) for item in findings]
    if len(identities) != len(set(identities)):
        raise ValueError("uv audit report repeats a finding")
    return findings


def _stream_evidence(raw):
    return {"bytes": len(raw), "sha256": _sha256(raw)}


def _write_report(path, report):
    if set(report) != REPORT_FIELDS:
        raise ValueError("Dependency-audit normalized report has an unsupported shape")
    payload = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode() + b"\n"
    path = Path(path)
    created = False
    try:
        stream = path.open("xb")
        created = True
        with stream:
            os.chmod(stream.fileno(), 0o600)
            stream.write(payload)
    except BaseException:
        if created and path.is_file() and not path.is_symlink():
            path.unlink()
        raise


def _audit(args, executable):
    manifest, root, current = _frozen_manifest(args.manifest, executable, args.service_url)
    output = Path(args.output)
    if output.exists() or output.is_symlink():
        raise ValueError("Dependency-audit report path must be new")
    resolved_output = output.resolve()
    if resolved_output == root or resolved_output.is_relative_to(root):
        raise ValueError("Dependency-audit report must stay outside the candidate")
    packages = _locked_packages((root / SOURCES[0]).read_bytes())
    command = (
        str(executable), "--preview-features", "audit-command,json-output", "audit", "--locked",
        "--output-format", "json", "--no-progress", "--no-python-downloads",
        "--service-format", "osv", "--service-url", args.service_url,
    )
    completed = subprocess.run(
        command, cwd=root, capture_output=True, env=_child_environment(), check=False,
    )
    findings = []
    status = "error"
    if completed.returncode in {0, 1}:
        try:
            native = json.loads(completed.stdout.decode("utf-8", errors="strict"))
            findings = _normalize(native, packages)
            expected_exit = 1 if findings else 0
            if completed.returncode != expected_exit:
                raise ValueError("uv audit exit status contradicts its findings")
            status = "concern" if findings else "observed"
        except (TypeError, UnicodeError, ValueError, json.JSONDecodeError):
            findings = []
            status = "error"
    elif completed.returncode == 2 and b"503 Service Unavailable" in completed.stderr:
        status = "unavailable"

    report = {
        "schema_version": 1,
        "status": status,
        "enforcement": "advisory",
        "effective_input_sha256": manifest["content_sha256"],
        "files": [
            {"path": "pyproject.toml", "sha256": current["pyproject.toml"]},
            {"path": "uv.lock", "sha256": current["uv.lock"]},
        ],
        "scanner": {"name": "uv", "version": VERSION, "output_schema": "preview"},
        "service": {"format": "osv", "url": args.service_url},
        "selection": {
            "groups": "all", "extras": "all", "target": "universal", "audited_packages": len(packages),
        },
        "packages": packages,
        "findings": findings,
        "limitations": list(LIMITATIONS),
        "execution": {
            "native_exit": completed.returncode,
            "argv": list(command),
            "stdout": _stream_evidence(completed.stdout),
            "stderr": _stream_evidence(completed.stderr),
        },
    }
    _write_report(resolved_output, report)
    print("dependency audit: " + status)
    return 0 if status == "observed" else 1 if status == "concern" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service-url", required=True)
    args = parser.parse_args(argv)
    try:
        return _audit(args, _executable(args.executable))
    except (OSError, subprocess.SubprocessError, TypeError, UnicodeError, ValueError):
        print("dependency audit: invalid inputs or tool failure", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
