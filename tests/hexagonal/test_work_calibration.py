"""The calibrator must not credit broken setup, noise, skipped or absent tests."""
from pathlib import Path

import pytest

from tests.hexagonal.calibrate_work_acceptance import CONTROL, classify, execute


@pytest.mark.parametrize("body,control,expected", [
    ('assert False, "EXPECTED_VIOLATION"', "pass", "detected"),
    ('raise TimeoutError("unrelated timeout")', "pass", "unrelated_failure"),
    ('pytest.skip("unavailable")', "pass", "skipped"),
    ("pass", "pass", "escaped_fault"),
    ('assert False, "EXPECTED_VIOLATION"', 'assert False, "broken control"', "unrelated_failure"),
])
def test_calibrator_requires_exact_assertion_and_passing_controls(tmp_path, body, control, expected):
    source = tmp_path / "test_probe.py"
    source.write_text(f"import pytest\ndef test_behavior():\n    {body}\ndef {CONTROL}():\n    {control}\n")
    run = execute(tmp_path, tmp_path / "result", ["test_probe.py"])
    assert classify(run["exit_code"], Path(run["xml"]), "test_behavior", "EXPECTED_VIOLATION")["classification"] == expected


def test_missing_dependency_is_setup_error_and_never_fault_detection(tmp_path):
    (tmp_path / "test_probe.py").write_text("import deliberately_missing_calibration_dependency\n")
    run = execute(tmp_path, tmp_path / "result", ["test_probe.py"])
    assert classify(run["exit_code"], Path(run["xml"]), "test_behavior", "EXPECTED_VIOLATION")["classification"] == "setup_error"


def test_absent_requested_assertion_is_missing_coverage(tmp_path):
    (tmp_path / "test_probe.py").write_text(f"def {CONTROL}():\n    pass\n")
    run = execute(tmp_path, tmp_path / "result", ["test_probe.py"])
    assert classify(run["exit_code"], Path(run["xml"]), "test_behavior", "EXPECTED_VIOLATION")["classification"] == "missing_coverage"
