"""F07: keep deliberately faulty subjects, and calibrate their actual outcomes.

The target marker occurs in every assertion source line. Only executing that
assertion with a false condition earns detection; helper/collection failures do
not. These controls run real pytest processes and feed both public classifiers.
"""
import difflib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts.verification.calibration import classify, junit_cases
from tests.hexagonal.calibrate_work_acceptance import CONTROL, execute
from tests.hexagonal.calibrate_work_acceptance import classify as classify_work

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


def run_statement_subject(root, statement, *, fault=False, unrelated=False, mode="rewrite"):
    root.mkdir()
    source = ("def compute():\n    return " + ("2" if fault and not unrelated else "1") + "\n"
              "def unrelated_environment_error():\n    return "
              + (f"AssertionError('{MARKER}')" if fault and unrelated else "None") + "\n"
              "def test_target():\n    observed = compute()\n    trouble = unrelated_environment_error()\n"
              + statement + f"\n    assert observed == 1, '{MARKER}'\n"
              + f"def {CONTROL}():\n    assert True\n")
    (root / "test_probe.py").write_text(source)
    command = execute(root, root / "result", ["test_probe.py", "--assert=" + mode])
    return command, junit_cases(Path(command["xml"]))


@pytest.mark.parametrize("mode", ["rewrite", "plain"])
@pytest.mark.parametrize("statement,unrelated,expected", [
    (f"    if trouble is not None:\n        assert observed == 1, '{MARKER}'; raise trouble", True, "unrelated_failure"),
    (f"    if trouble is not None:\n        raise trouble; assert observed == 1, '{MARKER}'", True, "unrelated_failure"),
    (f"    if trouble is not None:\n        assert (\n            observed == 1\n        ), '{MARKER}'; raise trouble", True, "unrelated_failure"),
    (f"    if trouble is not None:\n        assert observed == 1, '{MARKER}'\n        raise trouble", True, "unrelated_failure"),
    (f"    assert (\n        observed == 1\n    ), '{MARKER}'", False, "detected"),
    (f"    assert observed == 1, '{MARKER}'; assert True, '{MARKER}'", False, "detected"),
    (f"    assert True, '{MARKER}'; assert observed == 1, '{MARKER}'", False, "detected"),
    (f"    label = 'é'; assert observed == 1, '{MARKER}'", False, "detected"),
    (f"    if True: assert observed == 1, '{MARKER}'", False, "detected"),
])
def test_raising_instruction_identifies_assertion_not_an_adjacent_statement(tmp_path, mode, statement, unrelated, expected):
    control_command, control = run_statement_subject(tmp_path / "control", statement, mode=mode)
    mutant_command, mutant = run_statement_subject(tmp_path / "mutant", statement, fault=True, unrelated=unrelated, mode=mode)
    before = (tmp_path / "control/test_probe.py").read_text()
    after = (tmp_path / "mutant/test_probe.py").read_text()
    (tmp_path / "mutation.patch").write_text("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
        fromfile="control/test_probe.py", tofile="mutant/test_probe.py")))
    assert before != after, "IV04_SOURCE_FAULT_MUST_ACTIVATE"
    if unrelated:
        assert "def compute():\n    return 1\n" in after, "IV04_INTENDED_ASSERTIONS_REMAIN_TRUE"
    result = classify(control, mutant, TARGET, MARKER, [UNAFFECTED],
        control_exit=control_command["exit_code"], mutant_exit=mutant_command["exit_code"])
    assert result["classification"] == expected, "IV04_ACTUAL_ASSERTION_EXECUTION_REQUIRED"
    assert classify_work(mutant_command["exit_code"], Path(mutant_command["xml"]), "test_target", MARKER)["classification"] == expected
    attribution = mutant[TARGET]["attribution"]
    assert attribution["target_assertion"] is (expected == "detected")
    assert attribution["instruction"] == "RAISE_VARARGS"
    assert attribution["instruction_span"] is not None


@pytest.mark.parametrize("mode", ["rewrite", "plain"])
def test_missing_instruction_positions_cannot_earn_detection(tmp_path, monkeypatch, mode):
    monkeypatch.setenv("PYTHONNODEBUGRANGES", "1")
    statement = f"    assert observed == 1, '{MARKER}'"
    control_command, control = run_statement_subject(tmp_path / "control", statement, mode=mode)
    mutant_command, mutant = run_statement_subject(tmp_path / "mutant", statement, fault=True, mode=mode)
    assert classify(control, mutant, TARGET, MARKER, [UNAFFECTED],
        control_exit=control_command["exit_code"], mutant_exit=mutant_command["exit_code"])["classification"] == "unrelated_failure"
    assert classify_work(mutant_command["exit_code"], Path(mutant_command["xml"]), "test_target", MARKER)["classification"] == "unrelated_failure"
    assert mutant[TARGET]["attribution"]["instruction_span"] is None
    assert mutant[TARGET]["attribution"]["target_assertion"] is False
