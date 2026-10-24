"""F07: keep deliberately faulty subjects, and calibrate their actual outcomes.

The target marker occurs in every assertion source line. Only executing that
assertion with a false condition earns detection; helper/collection failures do
not. These controls run real pytest processes and feed both public classifiers.
"""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from calibrate_work_acceptance import CONTROL, execute
from calibrate_work_acceptance import classify as classify_work

from scripts.verification.calibration import classify, junit_cases

MARKER = "EXPECTED_VIOLATION"
TARGET = "test_probe::test_target"
UNAFFECTED = "test_probe::" + CONTROL


def run_subject(tmp_path, source, *, collection="", target="test_target"):
    root = tmp_path
    root.mkdir(exist_ok=True)
    (root / "test_probe.py").write_text(
        collection + "\n" + source + "\n"
        f"def {target}():\n    assert compute() == 1, '{MARKER}'\n"
        f"def {CONTROL}():\n    assert True\n"
    )
    command = execute(root, root / "result", ["test_probe.py"])
    return command, junit_cases(Path(command["xml"]))


@pytest.fixture
def passing_control(tmp_path):
    return run_subject(tmp_path / "control", "def compute():\n    return 1")


@pytest.mark.parametrize("source,collection,expected", [
    ("def compute():\n    return 2", "", "detected"),
    ("def compute():\n    raise TimeoutError('unrelated timeout')", "", "unrelated_failure"),
    # Even deliberately quoting the target marker in a different exception
    # does not turn that exception into execution of the assertion.
    (f"def compute():\n    raise TimeoutError('{MARKER}')", "", "unrelated_failure"),
    (f"def compute():\n    raise AssertionError('{MARKER}')", "", "unrelated_failure"),
    (f"def compute():\n    assert False, '{MARKER}'", "", "unrelated_failure"),
    ("def compute():\n    import deliberately_missing_calibration_dependency", "", "setup_error"),
    ("def compute():\n    return 1", "import deliberately_missing_calibration_dependency", "setup_error"),
    ("def compute():\n    import pytest\n    pytest.skip('unavailable')", "", "skipped"),
    ("def compute():\n    return 1 + 0", "", "escaped_fault"),
])
def test_marker_in_source_is_not_executed_assertion(passing_control, tmp_path, source, collection, expected):
    control_command, control = passing_control
    mutant_command, mutant = run_subject(tmp_path / "mutant", source, collection=collection)
    result = classify(control, mutant, TARGET, MARKER, [UNAFFECTED],
                      control_exit=control_command["exit_code"], mutant_exit=mutant_command["exit_code"])
    assert result["classification"] == expected, "F07_ASSERTION_ATTRIBUTION"
    assert classify_work(mutant_command["exit_code"], Path(mutant_command["xml"]), "test_target", MARKER)["classification"] == expected
    if expected in {"detected", "unrelated_failure", "setup_error"} and TARGET in mutant:
        assert MARKER in mutant[TARGET]["diagnostic"]


def test_unapplied_fault_and_process_timeout_cannot_earn_detection(passing_control, tmp_path):
    _, control = passing_control
    command, mutant = run_subject(tmp_path / "mutant", "def compute():\n    return 2")
    assert classify(control, mutant, TARGET, MARKER, [UNAFFECTED], mutation_applied=False)["classification"] == "setup_error"
    assert classify(control, mutant, TARGET, MARKER, [UNAFFECTED], mutant_exit=124)["classification"] == "unrelated_failure"
    assert classify_work(124, Path(command["xml"]), "test_target", MARKER)["classification"] == "unrelated_failure"


def test_target_name_prefix_does_not_substitute_for_required_assertion(passing_control, tmp_path):
    _, control = passing_control
    command, mutant = run_subject(tmp_path / "mutant", "def compute():\n    return 2", target="test_target_unrelated")
    assert classify(control, mutant, TARGET, MARKER, [UNAFFECTED])["classification"] == "not_exercised"
    assert classify_work(command["exit_code"], Path(command["xml"]), "test_target", MARKER)["classification"] == "missing_coverage"


def test_traceback_marker_cannot_substitute_for_actual_assertion_message():
    control = {TARGET: {"status": "passed"}, UNAFFECTED: {"status": "passed"}}
    mutant = {TARGET: {"status": "failed", "diagnostic": f'assert compute(), "{MARKER}"',
                      "failure_message": "AssertionError: unrelated condition"}, UNAFFECTED: {"status": "passed"}}
    assert classify(control, mutant, TARGET, MARKER, [UNAFFECTED])["classification"] == "unrelated_failure"


@pytest.mark.parametrize("xml", ["<testsuite>", "not XML"])
def test_malformed_junit_cannot_earn_detection(tmp_path, xml):
    path = tmp_path / "invalid.xml"
    path.write_text(xml)
    with pytest.raises(ET.ParseError):
        junit_cases(path)
    assert classify_work(1, path, "test_target", MARKER)["classification"] == "setup_error"


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong_test", "malformed"])
def test_missing_or_ambiguous_observer_attribution_cannot_earn_detection(passing_control, tmp_path, change):
    _, control = passing_control
    command, _ = run_subject(tmp_path / "mutant", "def compute():\n    return 2")
    path = Path(command["xml"])
    tree = ET.parse(path)
    target = next(case for case in tree.getroot().iter("testcase") if case.get("name") == "test_target")
    props = target.find("properties")
    prop = props.find("property")
    if change == "missing":
        target.remove(props)
    elif change == "duplicate":
        ET.SubElement(props, "property", prop.attrib)
    elif change == "wrong_test":
        prop.set("value", prop.get("value").replace('"test_name": "test_target"', '"test_name": "other"'))
    else:
        prop.set("value", "not JSON")
    tree.write(path)
    mutant = junit_cases(path)
    assert classify(control, mutant, TARGET, MARKER, [UNAFFECTED])["classification"] == "unrelated_failure"
    assert classify_work(command["exit_code"], path, "test_target", MARKER)["classification"] == "unrelated_failure"
