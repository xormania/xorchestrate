"""Meaningful oracle, leakage, grouping, and executable-boundary checks."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from experiments.jev_failure_correlation_v2.corpus import load_corpus, key_for, transformations
from experiments.jev_failure_correlation_v2.probes import PROBES, assertion_rows, execute_probe


def test_initial_missing_evidence_is_not_hidden_outcome_truth():
    cases = load_corpus()
    unresolved = [case for case in cases if case["initial_key"]["judgment"] == "unresolved"]
    assert len(unresolved) == 8
    for case in unresolved:
        assert case["outcome_key"]["judgment"] == "contradicts"
        assert key_for(case["packet"])["judgment"] == "unresolved"
        assert all(not c["observed"] and c["left"] is None for c in case["packet"]["facts"]["checks"])


def test_packets_do_not_leak_splits_or_oracle_metadata():
    cases = load_corpus()
    for case in cases:
        text = json.dumps(case["packet"])
        for hidden in (case["id"], case["group"]):
            assert hidden not in text
        forbidden = {"initial_key", "outcome_key", "scenario", "raw_reference", "useful_probes", "split", "group"}
        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(v) for v in value))
            return set()
        assert not forbidden.intersection(keys(case["packet"]))
        assert set(case["packet"]["candidates"]) == set(PROBES)
    grouping = {}
    for case in cases:
        grouping.setdefault(case["group"], set()).add(case["split"])
    assert len(grouping) == 7 and all(len(splits) == 1 for splits in grouping.values())


def test_concurrent_violations_and_defensive_receipt_preserved():
    case = next(c for c in load_corpus() if c["probe_id"] == "p_arguments" and c["scenario"] == "fault")
    assert set(case["initial_key"]["violations"]) == {"argument_integrity", "effect_uncertainty"}
    details = case["packet"]["observations"][0]["details"]
    assert details["native_arguments"] != details["executed_arguments"]
    assert details["receipt"]["verified"] is False
    assert details["normalized_tool_arguments"][0] == details["native_arguments"]
    assert details["ordered_attempts"][-1]["status"] == "unknown"


def test_calibration_oracle_requires_reached_comparison_not_marker():
    cases = load_corpus()
    good = next(c for c in cases if c["probe_id"] == "p_assertion" and c["scenario"] == "healthy")
    bad = next(c for c in cases if c["probe_id"] == "p_assertion" and c["scenario"] == "fault")
    gd = good["packet"]["observations"][0]["details"]
    bd = bad["packet"]["observations"][0]["details"]
    assert gd["target_phase"] == {"phase": "comparison", "actual": 2, "expected": 1}
    assert bd["target_phase"] == {"phase": "entered"}
    assert gd["candidate_classifier"]["classification"] == bd["candidate_classifier"]["classification"] == "detected"
    assert good["initial_key"]["judgment"] == "supports"
    assert bad["initial_key"]["judgment"] == "contradicts"


def test_prespecified_transformations_preserve_or_change_evidence_as_declared():
    cases = load_corpus()
    parents = {c["id"]: c for c in cases}
    rows = transformations(cases)
    assert len(rows) == 30
    for row in rows:
        assert row["group"] == parents[row["parent_id"]]["group"]
        if row["kind"] == "invariance":
            assert key_for(row["packet"]) == parents[row["parent_id"]]["initial_key"]
        else:
            assert key_for(row["packet"])["judgment"] == "unresolved"
    wrong = next(r for r in rows if r["transform"] == "wrong_subject")
    assert key_for(wrong["packet"])["applicability"] == "wrong_subject"


@pytest.mark.parametrize("probe_id", sorted(PROBES))
def test_selected_boundary_executes_both_healthy_and_intended_fault(probe_id, tmp_path):
    cases = load_corpus()
    for variant in ("healthy", "fault"):
        case = next(c for c in cases if c["probe_id"] == probe_id and c["scenario"] == variant)
        result = execute_probe(case, probe_id, tmp_path / variant)
        assert result["status"] == "executed", result
        assert result["exercised"] and result["decisive"]
        satisfied = [a["satisfied"] for a in result["assertions"]]
        assert all(satisfied) if variant == "healthy" else not all(satisfied)
        assert Path(result["raw_reference"]).is_file()
        assert key_for({**case["packet"], **result["visible"]})["judgment"] == case["outcome_key"]["judgment"]


@pytest.mark.parametrize("probe_id", sorted(PROBES))
@pytest.mark.parametrize("control_mode", ["setup", "helper", "timeout"])
def test_unrelated_failure_never_earns_detection_credit(probe_id, control_mode, tmp_path):
    case = next(c for c in load_corpus() if c["probe_id"] == probe_id)
    result = execute_probe(case, probe_id, tmp_path / "probe", control_mode=control_mode)
    assert result["status"] == "not_exercised"
    assert not result["exercised"] and not result["decisive"] and not result["distinguishing"]
    assert result["assertions"] == []


def test_probe_mismatch_and_unsupported_keep_terminal_unknown(tmp_path):
    case = load_corpus()[0]
    for i, probe_id in enumerate(["none", next(p for p in PROBES if p != case["probe_id"]) ]):
        result = execute_probe(case, probe_id, tmp_path / str(i))
        assert result["status"] == "unsupported"
        assert not result["decisive"]


def test_end_to_end_action_changes_real_acceptance_and_has_healthy_control(tmp_path):
    from experiments.jev_failure_correlation_v2.probes import execute_action
    cases = load_corpus()
    fault = next(c for c in cases if c["probe_id"] == "p_context" and c["scenario"] == "fault")
    healthy = next(c for c in cases if c["probe_id"] == "p_context" and c["scenario"] == "healthy")
    a = execute_action(fault, "accept", tmp_path / "false_accept")
    b = execute_action(fault, "reject", tmp_path / "reject")
    c = execute_action(healthy, "accept", tmp_path / "healthy")
    d = execute_action(healthy, "investigate", tmp_path / "unnecessary")
    assert a["status"] == b["status"] == c["status"] == d["status"] == "executed"
    assert a["consequential_false_accept"] and not a["behavioral_outcome_correct"]
    assert not b["accepted_after"] and b["behavioral_outcome_correct"]
    assert c["accepted_after"] and c["behavioral_outcome_correct"]
    assert d["unnecessary_withholding"] and not d["behavioral_outcome_correct"]


def test_changed_subject_cannot_be_attributed_to_frozen_revision(monkeypatch):
    from experiments.jev_failure_correlation_v2 import probes
    import subprocess
    monkeypatch.setattr(probes.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a[0], 1))
    with pytest.raises(RuntimeError, match="Frozen product subject differs"):
        probes.assert_subject_identity()
