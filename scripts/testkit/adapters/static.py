"""Resolve shared static checks and bind their actual source/configuration inputs."""

from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

from ..model import Check
from .evidence import candidate_inputs, digest, file_identity, write_json
from .identity import effective_inputs, repository_path

ACTIVE_ROOTS = ("xorchestrate", "scripts", "tests", ".github")
STATIC_SUITES = ("static", "security")
CHECK_SUITES = {
    "ruff": frozenset(STATIC_SUITES),
    "syntax": frozenset({"static"}),
    "architecture": frozenset({"static"}),
    "secret-scan": frozenset(STATIC_SUITES),
}
STATIC_IDS = tuple(CHECK_SUITES)
SYNTAX_SCRIPT = Path(__file__).with_name("syntax.py").resolve()
SECRET_SCAN_SCRIPT = Path(__file__).with_name("secret_scan.py").resolve()


def definitions(*, directory, timeout=600, suites=("static",)):
    """Declare each stable check once when any of its requested suite views applies."""
    directory = Path(directory).resolve()
    selected = {suites} if isinstance(suites, str) else set(suites)
    checks = (
        Check("ruff", (sys.executable, "-m", "ruff", "check", "--config", "ruff.toml",
                       "--no-cache", "--no-fix", "--no-respect-gitignore", "--output-format", "json",
                       "--output-file", str(directory / "ruff.json"), *ACTIVE_ROOTS), timeout,
              ("ruff.toml", ".github/requirements-ci.txt")),
        Check("syntax", (sys.executable, str(SYNTAX_SCRIPT), "--manifest",
                         str(directory / "inputs-syntax.json"), "--output", str(directory / "syntax.json")), timeout,
              (str(SYNTAX_SCRIPT),)),
        Check("architecture", (sys.executable, "-m", "scripts.architecture", "--output",
                               str(directory / "architecture.json")), timeout, ("contracts/hexagonal.json",)),
        Check("secret-scan", (sys.executable, str(SECRET_SCAN_SCRIPT), "--manifest",
                              str(directory / "inputs-secret-scan.json"), "--output",
                              str(directory / "secret-scan.json")), timeout,
              (str(SECRET_SCAN_SCRIPT), "contracts/security.json")),
    )
    return tuple(check for check in checks if CHECK_SUITES[check.name] & selected)


def _active_directories(root):
    paths = []
    for relative in ACTIVE_ROOTS:
        directory = root / relative
        if not directory.is_dir():
            if relative == ".github" and not directory.exists():
                continue
            raise ValueError("Active source root is missing or is not a directory: " + relative)
        if not directory.resolve().is_relative_to(root):
            raise ValueError("Active source root resolves outside the selected repository: " + relative)
        paths.append(relative)
    return paths


def _python_sources(root, roots, *, initializers=False):
    sources = set()
    for relative in roots:
        directory = root / relative
        if not directory.is_dir() or not directory.resolve().is_relative_to(root):
            raise ValueError("Source root is missing or outside the selected repository: " + relative)
        selected = set(directory.rglob("*.py"))
        if initializers and not selected:
            raise ValueError("Architecture source root contains no Python modules: " + relative)
        if initializers:
            for ancestor in directory.parents:
                if ancestor == root:
                    break
                initializer = ancestor / "__init__.py"
                if initializer.is_file():
                    selected.add(initializer)
        for path in selected:
            _, name, _ = repository_path(root, path)
            sources.add(name)
    if not sources:
        raise ValueError("Static source selection contains no Python files")
    return sorted(sources)


def _tool_sources(root, name):
    """Bind repository-owned checker code as well as its inspected subjects."""
    paths = []
    for relative in ("scripts/testkit", *( ("scripts/architecture",) if name == "architecture" else ())):
        directory = root / relative
        if directory.is_dir():
            paths.extend(path.relative_to(root).as_posix() for path in directory.rglob("*.py"))
    return paths


def _command(runner, argv, *, root, env, timeout):
    result = runner.run(tuple(argv), cwd=str(root), env=env, timeout=timeout)
    if result.exit != 0:
        raise ValueError("Static preparation command failed: " + (result.stderr or result.stdout).strip())
    return result.stdout


def _ruff_configuration(root):
    """Cover Ruff's explicit config and every repository-local inherited config."""
    paths, seen = [], set()
    current = root / "ruff.toml"
    while current is not None:
        path, relative, resolved = repository_path(root, current)
        if resolved in seen:
            raise ValueError("Ruff configuration inheritance contains a cycle")
        seen.add(resolved)
        paths.append(relative)
        configuration = tomllib.loads(path.read_text())
        if path.name == "pyproject.toml":
            configuration = configuration.get("tool", {}).get("ruff", {})
        inherited = configuration.get("extend")
        if inherited is not None and (not isinstance(inherited, str) or not inherited):
            raise ValueError("Ruff configuration extend must name another configuration file")
        current = path.parent / inherited if inherited else None
    return paths


def static_manifest(name, *, root, directory, runner, env, timeout=600):
    """Resolve current membership using the checker's own selection where needed.

    Ruff's bounded ``--show-files`` observes its configured/default exclusions.
    Git ignores are explicitly disabled within the active roots so global Git
    configuration cannot silently alter lint coverage. Syntax and architecture
    retain their declared Python traversal, including examined ignored files.
    Secret scanning follows all cached and nonignored untracked candidate files.
    """
    root, directory = Path(root).resolve(), Path(directory).resolve()
    configs = []
    tool = {"python": sys.version}
    if name == "ruff":
        roots = _active_directories(root)
        configs = [*_ruff_configuration(root), ".github/requirements-ci.txt"]
        version = _command(runner, (sys.executable, "-m", "ruff", "--version"),
                           root=root, env=env, timeout=timeout).strip()
        pins = [line.strip().split("==", 1)[1] for line in (root / ".github/requirements-ci.txt").read_text().splitlines()
                if line.strip().startswith("ruff==")]
        if len(pins) != 1 or version != "ruff " + pins[0]:
            raise ValueError("Installed Ruff does not match the single requirements-ci.txt pin: " + version)
        listing = _command(runner, (sys.executable, "-m", "ruff", "check", "--config", str(root / "ruff.toml"),
                                   "--no-cache", "--no-fix", "--no-respect-gitignore", "--show-files", *roots),
                           root=root, env=env, timeout=timeout)
        sources = []
        for line in listing.splitlines():
            if line:
                _, relative, _ = repository_path(root, line)
                sources.append(relative)
        if not sources:
            raise ValueError("Ruff selection contains no source files")
        selection = {"roots": list(ACTIVE_ROOTS), "method": "ruff --show-files",
                     "respect_gitignore": False, "exclusions": "Ruff configured/default exclusions"}
        tool["ruff"] = version
    elif name == "syntax":
        roots = _active_directories(root)
        sources = _python_sources(root, roots)
        selection = {"roots": list(ACTIVE_ROOTS), "method": "recursive *.py; no Git exclusions"}
        tool["syntax_checker"] = {"path": str(SYNTAX_SCRIPT), **file_identity(SYNTAX_SCRIPT)}
    elif name == "architecture":
        configs = ["contracts/hexagonal.json"]
        profile = json.loads((root / configs[0]).read_text())
        roots = profile["source_roots"]
        if not isinstance(roots, list) or any(not isinstance(item, str) or not item for item in roots):
            raise ValueError("Architecture requires declared source roots")
        for relative in roots:
            if Path(relative).is_absolute() or ".." in Path(relative).parts:
                raise ValueError("Unsafe architecture source root")
        sources = _python_sources(root, roots, initializers=True)
        selection = {"roots": roots, "method": "architecture recursive *.py plus package initializers"}
    elif name == "secret-scan":
        inputs = candidate_inputs(root)
        sources, non_file_entries = [], {}
        for relative, identity in inputs["files"].items():
            path = root / relative
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError("Secret scan input resolves outside the candidate: " + relative)
            if resolved.is_file():
                sources.append(relative)
            elif path.is_symlink() and resolved.is_dir():
                # Git records a directory link as one candidate entry. Its
                # in-repository target files have their own candidate paths;
                # bind the link itself without pretending it has file bytes.
                non_file_entries[relative] = identity
            else:
                raise ValueError("Secret scan input is not a regular file or directory link: " + relative)
        sources.sort()
        if not sources:
            raise ValueError("Secret scan selected no files from the candidate")
        configs = ["contracts/security.json"]
        from .secret_scan import load_policy

        policy, _ = load_policy((root / configs[0]).read_bytes())
        selection = {
            "method": "git ls-files --cached --others --exclude-standard",
            "tracked": True,
            "nonignored_untracked": True,
            "deleted": inputs["deleted"],
            "non_file_entries": non_file_entries,
        }
        tool["secret_scanner"] = {"path": str(SECRET_SCAN_SCRIPT), **file_identity(SECRET_SCAN_SCRIPT)}
        tool["security_policy"] = {"id": policy["id"], "version": policy["version"]}
    else:
        raise ValueError("Unknown static check: " + name)
    manifest = effective_inputs(root, [*sources, *configs, *_tool_sources(root, name)],
                                scope={"check": name, "selection": selection, "configuration": configs}, sources=sources)
    return {**manifest, "tool": tool}


def prepare_static(check, *, root, directory, runner, env):
    """Freeze selected command inputs and preserve the manifest consumed by syntax."""
    root, directory = Path(root).resolve(), Path(directory).resolve()
    manifest = static_manifest(check.name, root=root, directory=directory, runner=runner,
                               env=env, timeout=check.timeout)
    write_json(directory / ("inputs-" + check.name + ".json"), manifest)
    if check.name == "ruff":
        argv = [*check.argv[:check.argv.index("ruff.toml")], str(root / "ruff.toml")]
        argv.extend(check.argv[check.argv.index("ruff.toml") + 1:-len(ACTIVE_ROOTS)])
        # Explicit files freeze tool membership after the tool's own discovery.
        argv.extend(str(root / name) for name in manifest["sources"])
        check = replace(check, argv=tuple(argv))
    return check, manifest


def _reported_files(report, manifest, *, complete):
    entries = report.get("files")
    if not isinstance(entries, list):
        raise ValueError("Static report requires a source-file inventory")
    observed = {}
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("Malformed static source-file inventory")
        path, sha256 = item["path"], item.get("sha256")
        if path in observed or path not in manifest["sources"]:
            raise ValueError("Static report contains duplicate or unselected source: " + path)
        if sha256 != manifest["files"][path]["content_sha256"]:
            raise ValueError("Static report source bytes differ from bound input: " + path)
        observed[path] = sha256
    if complete and set(observed) != set(manifest["sources"]):
        raise ValueError("Successful static report does not account for every selected source")
    return observed


def bind_static_report(name, *, directory, manifest):
    """Validate actual structured evidence before a launched check can pass.

    Failed extraction can report only the files it read. A successful checker
    must account for its complete frozen selection with the captured byte hashes.
    Tool-native report identities remain explicit alongside the shared identity.
    """
    if name not in STATIC_IDS:
        raise ValueError("Unknown static report: " + name)
    path = Path(directory) / (name + ".json")
    raw = path.read_bytes()
    report = json.loads(raw)
    binding = {"path": str(path), "sha256": digest(raw),
               "effective_input_sha256": manifest["content_sha256"]}
    if name == "ruff":
        if not isinstance(report, list):
            raise ValueError("Ruff report must be a structured diagnostic list")
        root = Path(manifest["root"])
        allowed = set(manifest["sources"])
        allowed.update(manifest["files"][name]["resolved_path"] for name in manifest["sources"])
        for finding in report:
            if not isinstance(finding, dict):
                raise ValueError("Malformed Ruff diagnostic")
            if any(not isinstance(finding.get(key), str) or not finding[key]
                   for key in ("code", "filename", "message")):
                raise ValueError("Ruff diagnostic requires rule, filename and message")
            location = finding.get("location")
            if not isinstance(location, dict) or any(
                    not isinstance(location.get(key), int) or isinstance(location[key], bool) or location[key] <= 0
                    for key in ("row", "column")):
                raise ValueError("Ruff diagnostic requires a valid source location")
            filename = Path(finding["filename"])
            filename = filename.relative_to(root).as_posix() if filename.is_absolute() else filename.as_posix()
            if filename not in allowed:
                raise ValueError("Ruff reported an unselected source: " + filename)
        binding.update(status="passed" if not report else "failed", findings=len(report))
    elif name == "secret-scan":
        expected_fields = {"schema_version", "status", "effective_input_sha256", "files", "findings"}
        if not isinstance(report, dict) or set(report) != expected_fields or report.get("schema_version") != 1:
            raise ValueError("Unsupported structured secret-scan report")
        if report.get("status") not in {"passed", "failed"} or not isinstance(report.get("findings"), list):
            raise ValueError("Malformed secret-scan report status/findings")
        if report.get("effective_input_sha256") != manifest["content_sha256"]:
            raise ValueError("Secret-scan report belongs to another effective-input manifest")
        entries = report.get("files")
        if not isinstance(entries, list) or any(not isinstance(item, dict) or set(item) != {"path", "sha256"}
                                                for item in entries):
            raise ValueError("Secret-scan file evidence requires exactly path and sha256")
        observed = _reported_files(report, manifest, complete=True)

        policy_name = "contracts/security.json"
        configuration = manifest.get("scope", {}).get("configuration")
        policy_identity = manifest.get("files", {}).get(policy_name)
        if configuration != [policy_name] or not isinstance(policy_identity, dict):
            raise ValueError("Secret-scan manifest has no bound security policy")
        policy_raw = (Path(manifest["root"]) / policy_name).read_bytes()
        if digest(policy_raw) != policy_identity.get("content_sha256"):
            raise ValueError("Secret-scan policy changed after effective-input capture")
        from .secret_scan import load_policy

        policy, _ = load_policy(policy_raw)
        rule_ids = {rule["id"] for rule in policy["rules"]}
        for finding in report["findings"]:
            if not isinstance(finding, dict) or set(finding) != {"path", "line", "rule"}:
                raise ValueError("Secret-scan findings require exactly path, line and rule")
            if finding["path"] not in observed:
                raise ValueError("Secret-scan finding names an unselected source")
            if (not isinstance(finding["line"], int) or isinstance(finding["line"], bool)
                    or finding["line"] <= 0):
                raise ValueError("Secret-scan finding requires a positive line number")
            if not isinstance(finding["rule"], str) or finding["rule"] not in rule_ids:
                raise ValueError("Secret-scan finding names an unknown rule")
        passed = report["status"] == "passed"
        if passed == bool(report["findings"]):
            raise ValueError("Secret-scan status contradicts its findings")
        binding.update(status=report["status"], findings=len(report["findings"]))
    else:
        if not isinstance(report, dict) or report.get("schema_version") != 1:
            raise ValueError("Unsupported structured static report")
        statuses = ("passed", "failed") if name == "syntax" else ("pass", "violation", "error")
        if report.get("status") not in statuses or not isinstance(report.get("findings"), list):
            raise ValueError("Malformed static report status/findings")
        passed = report["status"] == statuses[0]
        _reported_files(report, manifest, complete=passed)
        if passed and report["findings"]:
            raise ValueError("Successful static report contains findings")
        if name == "syntax":
            if report.get("effective_input_sha256") != manifest["content_sha256"]:
                raise ValueError("Syntax report belongs to another effective-input manifest")
        else:
            subject = report.get("subject")
            if not isinstance(subject, dict):
                raise ValueError("Architecture report has no source/policy identity")
            for field in ("source_sha256", "contract_sha256"):
                value = subject.get(field)
                if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                    raise ValueError("Architecture report has an invalid " + field)
            profile = Path(manifest["root"]) / "contracts/hexagonal.json"
            profile_bytes = profile.read_bytes()
            if digest(profile_bytes) != manifest["files"]["contracts/hexagonal.json"]["content_sha256"]:
                raise ValueError("Architecture configuration changed after effective-input capture")
            if report.get("profile") != json.loads(profile_bytes):
                raise ValueError("Architecture report used a different configuration")
            binding.update(architecture_source_sha256=subject["source_sha256"],
                           architecture_contract_sha256=subject["contract_sha256"],
                           checker_sha256=report.get("execution", {}).get("checker_sha256"))
        binding.update(status="passed" if passed else "failed", findings=len(report["findings"]))
    return binding
