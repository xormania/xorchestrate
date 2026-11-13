"""Run pinned Zizmor without republishing its source-bearing native output."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

TOKEN_NAMES = frozenset(("GH_TOKEN", "GITHUB_TOKEN", "ZIZMOR_GITHUB_TOKEN"))
CONFIGURATION = ("contracts/zizmor.yml", ".github/requirements-ci.txt")
FINDING_EXITS = frozenset((11, 12, 13, 14))
SEVERITIES = frozenset(("informational", "low", "medium", "high"))
CONFIDENCES = frozenset(("low", "medium", "high"))
REPORT_FIELDS = frozenset(("schema_version", "status", "effective_input_sha256", "files", "findings"))
FINDING_FIELDS = frozenset(("path", "line", "column", "rule", "severity", "confidence"))
VERSION = "1.32.0"


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _child_environment():
    """Keep material execution context while removing every recognized GitHub token."""
    return {name: value for name, value in os.environ.items() if name not in TOKEN_NAMES}


def _executable(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Zizmor executable must be absolute")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError("Zizmor executable is unavailable")
    return resolved


def _probe(executable):
    completed = subprocess.run(
        (str(executable), "--version"), capture_output=True, env=_child_environment(), check=False,
    )
    if completed.returncode != 0:
        raise ValueError("Zizmor version probe failed")
    version = completed.stdout.decode("utf-8", errors="strict").strip()
    if re.fullmatch(r"zizmor [0-9]+\.[0-9]+\.[0-9]+(?:[-+.][0-9A-Za-z.-]+)?", version) is None:
        raise ValueError("Zizmor version probe returned an unsupported value")
    return version


def _repository_file(root, relative):
    name = Path(relative)
    if name.is_absolute() or ".." in name.parts or not name.parts:
        raise ValueError("Unsafe Zizmor input path")
    path = root / name
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError("Zizmor input resolves outside the candidate or is not a file")
    return path, resolved


def _frozen_manifest(path, executable):
    manifest = json.loads(Path(path).read_bytes())
    expected_fields = {
        "identity_schema", "scope", "sources", "files", "root", "content_sha256", "tool",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise ValueError("Zizmor requires an exact effective-input manifest")
    if manifest.get("identity_schema") != "testkit.effective-inputs.v1":
        raise ValueError("Zizmor requires the supported effective-input schema")

    payload = {name: manifest[name] for name in ("identity_schema", "scope", "sources", "files")}
    if not _digest(manifest.get("content_sha256")) or _sha256(_canonical(payload)) != manifest["content_sha256"]:
        raise ValueError("Zizmor effective-input identity is malformed")
    root_name = manifest.get("root")
    if not isinstance(root_name, str) or not root_name:
        raise ValueError("Zizmor manifest has no candidate root")
    root = Path(root_name).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Zizmor candidate root is not a directory")

    scope = manifest.get("scope")
    if not isinstance(scope, dict) or scope.get("check") != "zizmor":
        raise ValueError("Zizmor manifest belongs to another check")
    if scope.get("configuration") != list(CONFIGURATION):
        raise ValueError("Zizmor manifest has no exact policy configuration")
    selection = scope.get("selection")
    if (not isinstance(selection, dict) or selection.get("offline") is not True
            or selection.get("strict_collection") is not True
            or selection.get("persona") != "regular"
            or selection.get("format") != "json-v1"
            or selection.get("executable") != str(executable)
            or selection.get("version") != "zizmor " + VERSION):
        raise ValueError("Zizmor manifest has an unsupported execution policy")

    tool = manifest.get("tool")
    if (not isinstance(tool, dict) or tool.get("zizmor") != "zizmor " + VERSION
            or tool.get("zizmor_executable") != str(executable)):
        raise ValueError("Zizmor manifest has an unsupported tool identity")
    sources, identities = manifest.get("sources"), manifest.get("files")
    if (not isinstance(sources, list) or not sources or sources != sorted(sources)
            or len(sources) != len(set(sources)) or not isinstance(identities, dict)):
        raise ValueError("Zizmor requires a nonempty ordered workflow selection")

    current_hashes = {}
    for name, identity in identities.items():
        if not isinstance(name, str) or not isinstance(identity, dict) or not _digest(identity.get("content_sha256")):
            raise ValueError("Zizmor input has no bound content identity")
        path, resolved = _repository_file(root, name)
        sha256 = _sha256(resolved.read_bytes())
        if sha256 != identity["content_sha256"]:
            raise ValueError("Zizmor input changed after effective-input capture")
        current_hashes[name] = sha256

    for name in CONFIGURATION:
        if name not in identities:
            raise ValueError("Zizmor configuration is not bound")
    inventory = []
    for name in sources:
        relative = Path(name)
        if (name not in identities or relative.parts[:2] != (".github", "workflows")
                or len(relative.parts) != 3 or relative.suffix not in {".yml", ".yaml"}):
            raise ValueError("Zizmor source is not a selected top-level workflow")
        inventory.append({"path": name, "sha256": current_hashes[name]})
    return manifest, root, tuple(sources), inventory


def _source_name(value, *, root, sources):
    if not isinstance(value, str) or not value:
        raise ValueError("Zizmor finding has no local source")
    path = Path(value)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            raise ValueError("Zizmor finding names a source outside the candidate") from None
    if ".." in path.parts:
        raise ValueError("Zizmor finding contains an unsafe source path")
    name = path.as_posix()
    if name not in sources:
        raise ValueError("Zizmor finding names an unselected workflow")
    return name


def _positive_coordinate(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Zizmor finding has an invalid source coordinate")
    return value + 1


def _normalize(native, *, root, sources):
    if not isinstance(native, list):
        raise ValueError("Zizmor native report is not a finding list")
    normalized = []
    for item in native:
        if not isinstance(item, dict) or item.get("ignored") is not False:
            raise ValueError("Zizmor native finding is malformed or ignored")
        rule = item.get("ident")
        if not isinstance(rule, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", rule) is None:
            raise ValueError("Zizmor native finding has an invalid rule")
        determinations = item.get("determinations")
        if not isinstance(determinations, dict):
            raise ValueError("Zizmor native finding has no determinations")
        severity, confidence = determinations.get("severity"), determinations.get("confidence")
        if not isinstance(severity, str) or not isinstance(confidence, str):
            raise ValueError("Zizmor native finding has invalid determinations")
        severity, confidence = severity.lower(), confidence.lower()
        if severity not in SEVERITIES or confidence not in CONFIDENCES:
            raise ValueError("Zizmor native finding has unsupported determinations")

        locations = item.get("locations")
        if not isinstance(locations, list):
            raise ValueError("Zizmor native finding has no locations")
        primary = [location for location in locations if isinstance(location, dict)
                   and isinstance(location.get("symbolic"), dict)
                   and location["symbolic"].get("kind") == "Primary"]
        if len(primary) != 1:
            raise ValueError("Zizmor native finding requires one primary location")
        symbolic = primary[0]["symbolic"]
        key = symbolic.get("key")
        local = key.get("Local") if isinstance(key, dict) else None
        concrete = primary[0].get("concrete")
        location = concrete.get("location") if isinstance(concrete, dict) else None
        start = location.get("start_point") if isinstance(location, dict) else None
        if not isinstance(local, dict) or not isinstance(start, dict):
            raise ValueError("Zizmor native finding has an invalid primary location")
        normalized.append({
            "path": _source_name(local.get("verbatim_path"), root=root, sources=sources),
            "line": _positive_coordinate(start.get("row")),
            "column": _positive_coordinate(start.get("column")),
            "rule": rule,
            "severity": severity,
            "confidence": confidence,
        })
    normalized.sort(key=lambda item: (
        item["path"], item["line"], item["column"], item["rule"], item["severity"], item["confidence"],
    ))
    identities = [tuple(item[field] for field in sorted(FINDING_FIELDS)) for item in normalized]
    if len(identities) != len(set(identities)):
        raise ValueError("Zizmor native report repeats a finding")
    return normalized


def _write_report(path, report):
    if set(report) != REPORT_FIELDS:
        raise ValueError("Zizmor normalized report has an unsupported shape")
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


def _scan(args, executable):
    manifest, root, sources, inventory = _frozen_manifest(args.manifest, executable)
    requested_output = Path(args.output)
    if requested_output.exists() or requested_output.is_symlink():
        raise ValueError("Zizmor report path must be new")
    output = requested_output.resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError("Zizmor report must stay outside the candidate")
    cache = Path(args.cache_dir).resolve()
    if cache == root or cache.is_relative_to(root):
        raise ValueError("Zizmor cache must stay outside the candidate")
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    command = (
        str(executable), "--format=json-v1", "--offline", "--strict-collection", "--no-progress",
        "--color=never", "--persona=regular", "--config", str(root / CONFIGURATION[0]),
        "--cache-dir", str(cache), *(str(root / name) for name in sources),
    )
    completed = subprocess.run(
        command, cwd=root, capture_output=True, env=_child_environment(), check=False,
    )
    if completed.returncode not in {0, *FINDING_EXITS}:
        raise ValueError("Zizmor collection or tool execution failed")
    native = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    findings = _normalize(native, root=root, sources=set(sources))
    if (completed.returncode == 0) != (not findings):
        raise ValueError("Zizmor exit status contradicts its findings")
    report = {
        "schema_version": 1,
        "status": "failed" if findings else "passed",
        "effective_input_sha256": manifest["content_sha256"],
        "files": inventory,
        "findings": findings,
    }
    _write_report(output, report)
    print("zizmor scan: " + report["status"])
    return completed.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--probe-version", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        executable = _executable(args.executable)
        if args.probe_version:
            if any(value is not None for value in (args.manifest, args.output, args.cache_dir)):
                raise ValueError("Zizmor version probe accepts no scan inputs")
            print(_probe(executable))
            return 0
        if any(value is None for value in (args.manifest, args.output, args.cache_dir)):
            raise ValueError("Zizmor scan requires manifest, output and cache")
        return _scan(args, executable)
    except (OSError, subprocess.SubprocessError, TypeError, UnicodeError, ValueError):
        print("zizmor scan: invalid inputs or tool failure", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
