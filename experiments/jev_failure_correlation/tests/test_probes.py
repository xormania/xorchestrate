"""Tests distinguish experiment execution, known candidate defects and coverage gaps."""
import json
from experiments.jev_failure_correlation.probes import calibration, dependencies, run, intended_credit


def test_real_pytest_classification_boundaries(tmp_path):
    records = {r['variant']:r for r in calibration(tmp_path)}
    assert records['q01']['intended_detection_credit'] is True
    for key in ('q02','q03','q04'):
        assert records[key]['known_defect_reproduced'] is True
        assert records[key]['intended_detection_credit'] is False
    assert records['q05']['exit_code'] == 0
    for key in ('q06','q07','q08'):
        assert records[key]['intended_detection_credit'] is False
        assert records[key]['native_classification'] != 'detected'
        assert records[key]['work_classification'] != 'detected'
    assert records['q06']['target_observation'] is None
    assert records['q07']['target_observation']['status'] == 'skipped'
    assert records['q08']['target_observation'] is None


def test_material_and_cosmetic_changes_are_distinguished(tmp_path):
    records = dependencies(tmp_path)
    assert len(records) == 4
    assert sum(r['known_defect_reproduced'] for r in records) == 2
    assert sum(r['valid_control_passed'] for r in records) == 2


def test_unsupported_selected_probe_not_silently_replaced(tmp_path):
    report = run({'cases':{'c1':{'probe':'p1'},'c2':{'probe':'none'}}},'baseline',tmp_path/'out')
    assert all(r['status']=='unexecuted' for r in report['results'])
    assert all(not r['intended_detection_credit'] for r in report['results'])
    assert json.loads((tmp_path/'out/budget.json').read_text())['unique_probes_per_selector'] == 2


def test_unrelated_exception_never_earns_target_credit():
    control = {"test_probe::test_measurement_replay_is_bound_to_request_and_does_not_reexecute": {"status": "passed"}}
    for diagnostic in (
        'TimeoutError: unrelated\ntest_probe.py:3: in test_target\n    assert compute() == 1, "TARGET_REQUIREMENT"',
        'AssertionError: dependency invariant\ntest_probe.py:3: in test_target\n    assert compute() == 1, "TARGET_REQUIREMENT"',
        'AssertionError: TARGET_REQUIREMENT\nassert 2 == 1\ntest_probe.py:3: in test_target\nmodule.py:2: in compute',
    ):
        assert not intended_credit({"status":"failed","diagnostic":diagnostic},control,0,1)
    assert not intended_credit({"status":"skipped","diagnostic":""},control,0,0)
    assert not intended_credit(None,control,0,0)
