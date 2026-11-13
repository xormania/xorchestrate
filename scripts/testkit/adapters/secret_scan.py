"""Scan frozen candidate bytes for high-signal credential shapes without republishing matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

POLICY_FIELDS = {"schema_version", "id", "version", "allow_marker", "rules"}
RULE_FIELDS = {"id", "pattern"}


def _sha256(value):
    return hashlib.sha256(value).hexdigest()


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def load_policy(raw):
    """Validate the repository policy and compile its byte-oriented signatures."""
    policy = json.loads(raw)
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS:
        raise ValueError("Secret-scan policy has an unsupported shape")
    if type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        raise ValueError("Secret-scan policy requires schema_version 1")
    if any(not isinstance(policy[field], str) or not policy[field] for field in ("id", "version", "allow_marker")):
        raise ValueError("Secret-scan policy identity and allow marker must be nonempty strings")
    rules = policy["rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("Secret-scan policy requires at least one rule")
    compiled, identifiers = [], set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != RULE_FIELDS:
            raise ValueError("Secret-scan rules require exactly id and pattern")
        identifier, pattern = rule["id"], rule["pattern"]
        if (not isinstance(identifier, str) or not identifier or identifier in identifiers
                or not isinstance(pattern, str) or not pattern):
            raise ValueError("Secret-scan rules require unique nonempty ids and patterns")
        try:
            expression = re.compile(pattern.encode("ascii"))
        except (UnicodeEncodeError, re.error):
            raise ValueError("Secret-scan rule has an invalid pattern: " + identifier) from None
        identifiers.add(identifier)
        compiled.append((identifier, expression))
    return policy, tuple(compiled)


def _frozen_sources(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("Secret scan requires an effective-input manifest")
    root_name, sources, identities = manifest.get("root"), manifest.get("sources"), manifest.get("files")
    content_sha256 = manifest.get("content_sha256")
    if not isinstance(root_name, str) or not root_name or not _digest(content_sha256):
        raise ValueError("Secret scan requires a bound manifest identity")
    if (not isinstance(sources, list) or not sources or len(sources) != len(set(sources))
            or any(not isinstance(name, str) or not name for name in sources)):
        raise ValueError("Secret scan cannot accept an empty or malformed file selection")
    if not isinstance(identities, dict):
        raise ValueError("Secret scan requires bound source identities")

    root = Path(root_name).resolve()
    frozen = []
    for name in sources:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Secret scan contains an unsafe source path: " + name)
        identity = identities.get(name)
        if not isinstance(identity, dict) or not _digest(identity.get("content_sha256")):
            raise ValueError("Secret scan source has no bound content identity: " + name)
        path = root / relative
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ValueError("Secret scan source resolves outside the candidate or is not a file: " + name)
        raw = path.read_bytes()
        sha256 = _sha256(raw)
        if sha256 != identity["content_sha256"]:
            raise ValueError("Secret scan source changed after effective-input capture: " + name)
        frozen.append((name, raw, sha256))
    return tuple(frozen)


def check_manifest(manifest):
    """Return complete, match-free evidence for the exact frozen candidate selection."""
    frozen = _frozen_sources(manifest)
    policy_name = "contracts/security.json"
    policy_raw = next((raw for name, raw, _ in frozen if name == policy_name), None)
    if policy_raw is None:
        raise ValueError("Secret scan selection does not contain its policy")
    policy, rules = load_policy(policy_raw)
    marker = policy["allow_marker"].encode("utf-8")
    files = [{"path": name, "sha256": sha256} for name, _, sha256 in frozen]
    findings = []
    for name, raw, _ in frozen:
        for line_number, line in enumerate(raw.splitlines(), 1):
            if marker in line:
                continue
            for identifier, pattern in rules:
                if pattern.search(line):
                    findings.append({"path": name, "line": line_number, "rule": identifier})
    return {
        "schema_version": 1,
        "status": "failed" if findings else "passed",
        "effective_input_sha256": manifest["content_sha256"],
        "files": files,
        "findings": findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = check_manifest(json.loads(args.manifest.read_bytes()))
        args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    except (OSError, TypeError, UnicodeError, ValueError):
        print("secret scan: invalid inputs", file=sys.stderr)
        return 2
    print("secret scan: " + report["status"])
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
