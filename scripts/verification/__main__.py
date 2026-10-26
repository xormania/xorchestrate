"""Command-line capture, raw-check execution, verification and calibration."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
from pathlib import Path

from .calibration import classify, junit_cases
from .capture import canonical, capture_candidate, digest, now, run_command, write_json
from .checker import VerificationFailure, default_criteria, verify


def read_json(path):
    return json.loads(Path(path).read_text())


def calibration_workspace(root, output, mutation):
    """Capture the complete candidate and plant one fault in an isolated copy.

    Every calibration family uses the same input capture, path checks, unique
    anchor validation and retained mutation patch. Families supply their own
    target selectors and independent verdict policy.
    """
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    identity = capture_candidate(root, output / "snapshot")
    control, mutant = output / "control", output / "mutant"
    shutil.copytree(output / "snapshot" / "content", control, symlinks=True)
    shutil.copytree(control, mutant, symlinks=True)
    relative = Path(mutation["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Mutation path must stay inside isolated candidate")
    source = mutant / relative
    if source.is_symlink():
        raise ValueError("Do not mutate through a symlink")
    original = source.read_text()
    if not mutation.get("old") or original.count(mutation["old"]) != 1:
        raise ValueError("Mutation must replace exactly one known source occurrence")
    changed = original.replace(mutation["old"], mutation["new"], 1)
    source.write_text(changed)
    write_json(output / "mutation.json", mutation)
    (output / "mutation.patch").write_text("".join(difflib.unified_diff(
        original.splitlines(keepends=True), changed.splitlines(keepends=True),
        fromfile="a/" + relative.as_posix(), tofile="b/" + relative.as_posix())))
    return identity, control, mutant


def observed_pytest(root, output, selectors, *, junit=None, timeout=120):
    """Execute selected cases through the common command/evidence adapter.

    The trusted assertion observer is retained outside the subject's package.
    This also supports small standalone faulty subjects without a test catalog.
    """
    root, output = Path(root).resolve(), Path(output).resolve()
    checker = output.parent / (output.name + "-observer")
    checker.mkdir(parents=True, exist_ok=False, mode=0o700)
    observer = checker / "_xorchestrate_calibration_observer.py"
    observer.write_bytes(Path(__file__).with_name("pytest_calibration.py").read_bytes())
    observer.chmod(0o600)
    junit = Path(junit).resolve() if junit is not None else output / "results.xml"
    command = [sys.executable, "-m", "pytest", "-p", "_xorchestrate_calibration_observer",
               "-q", "--tb=short", *selectors, "--junitxml=" + str(junit)]
    record = run_command(command, root, output, timeout=timeout, environment={
        "PYTHONPATH": str(checker) + os.pathsep + str(root), "PYTHONDONTWRITEBYTECODE": "1"})
    record.update(xml=str(junit), stdout=str(output / "stdout.log"), stderr=str(output / "stderr.log"),
                  assertion_observer_sha256=digest(observer.read_bytes()))
    write_json(output / "command.json", record)
    return record


def calibrate(root, output, mutation, selectors, target, diagnostic, unaffected):
    """Run a passing control and exactly one source mutation in isolated copies."""
    output = Path(output).resolve()
    identity, control, mutant = calibration_workspace(root, output, mutation)
    records, cases = {}, {}
    for name, directory in (("control", control), ("mutant", mutant)):
        junit = output / (name + ".xml")
        records[name] = observed_pytest(directory, output / (name + "-run"), selectors, junit=junit)
        cases[name] = junit_cases(junit) if junit.exists() else {}
    result = classify(cases["control"], cases["mutant"], target, diagnostic, unaffected,
                      control_exit=records["control"]["exit_status"], mutant_exit=records["mutant"]["exit_status"])
    result.update(candidate_sha256=identity["content_sha256"], mutation_sha256=digest(canonical(mutation)),
                  assertion_observer_sha256=records["control"]["assertion_observer_sha256"],
                  commands=records, finished_at=now())
    write_json(output / "calibration.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("--candidate", type=Path, required=True)
    capture.add_argument("--out", type=Path, required=True)
    criteria = commands.add_parser("criteria")
    criteria.add_argument("--brief", type=Path, required=True)
    criteria.add_argument("--out", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("--run", type=Path, required=True)
    check.add_argument("--criteria", type=Path, required=True)
    check.add_argument("--bindings", type=Path, required=True)
    check.add_argument("--fixture", type=Path, required=True)
    check.add_argument("--out", type=Path, required=True)
    measure = commands.add_parser("run")
    measure.add_argument("--cwd", type=Path, required=True)
    measure.add_argument("--out", type=Path, required=True)
    measure.add_argument("--timeout", type=float, default=300)
    measure.add_argument("argv", nargs=argparse.REMAINDER)
    fault = commands.add_parser("calibrate")
    fault.add_argument("--candidate", type=Path, required=True)
    fault.add_argument("--out", type=Path, required=True)
    fault.add_argument("--mutation", type=Path, required=True)
    fault.add_argument("--selector", action="append", required=True)
    fault.add_argument("--target", required=True)
    fault.add_argument("--diagnostic", required=True)
    fault.add_argument("--unaffected", action="append", default=[])
    args = parser.parse_args(argv)
    if args.command == "capture":
        capture_candidate(args.candidate, args.out)
        return 0
    if args.command == "criteria":
        write_json(args.out, default_criteria(digest(args.brief.read_bytes())))
        return 0
    if args.command == "run":
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not argv:
            parser.error("run requires a command after --")
        result = run_command(argv, args.cwd, args.out, timeout=args.timeout)
        return 0 if result["status"] == "passed" else 1
    if args.command == "calibrate":
        result = calibrate(args.candidate, args.out, read_json(args.mutation), args.selector,
                           args.target, args.diagnostic, args.unaffected)
        return 0 if result["classification"] == "detected" else 1
    try:
        result = verify(read_json(args.run), read_json(args.criteria), read_json(args.bindings), read_json(args.fixture))
    except VerificationFailure as exc:
        result = {"status": "failed", "assertion": exc.assertion}
    write_json(args.out, result)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
