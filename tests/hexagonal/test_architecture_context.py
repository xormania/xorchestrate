"""Exercise targeted architecture evidence with independent source contracts."""
from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.architecture.context import build_context
from scripts.architecture.diagnostics import diagnose, evaluation_tasks
from tests.hexagonal.contracts.architecture_context import (
    changed_unit_is_complete,
    related_units_are_supplied,
    tasks_are_grounded,
)
from tests.hexagonal.support.architecture_context import (
    RecordingEvaluator,
    commit_source_baseline,
    measured_sources,
    source_report,
)


@pytest.fixture
def measured(tmp_path):
    sources, line, changed = measured_sources(tmp_path)
    return source_report(sources), sources, line, changed


def test_changed_function_near_eof_is_complete_with_related_evidence(tmp_path, measured):
    report, _, line, changed = measured
    original = deepcopy(report)
    context = build_context(report, tmp_path, changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}],
                            max_source_bytes=1600, max_files=4)
    assert any(item["source"] == changed for item in context["sources"]), "CHANGED_FUNCTION_COMPLETE"
    assert {("pkg/ports.py", "Store"), ("pkg/work.py", "invoke"), ("tests/test_work.py", "test_changed")} <= {
        (item["path"], item["symbol"]) for item in context["sources"]}, "RELATED_EVIDENCE_INCLUDED"
    changed_unit_is_complete(context, source=changed, start_line=line)
    related_units_are_supplied(context)
    assert report == original, "selection must not rewrite deterministic measurement"


@pytest.mark.parametrize("budget,files", [(10, 4), (1600, 1)])
def test_limits_omit_whole_units_instead_of_sending_prefixes(tmp_path, measured, budget, files):
    report, _, line, changed = measured
    context = build_context(report, tmp_path, changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}],
                            max_source_bytes=budget, max_files=files)
    scope = context["source_scope"]
    assert scope["source_bytes"] == sum(len(item["source"].encode()) for item in context["sources"])
    assert scope["source_bytes"] <= budget, "UNIT_BUDGET_ENFORCED"
    assert len({item["path"] for item in context["sources"]}) <= files, "FILE_LIMIT_ENFORCED"
    assert scope["truncated"] == []
    assert all(item["truncated"] is False for item in context["sources"])
    assert scope["excluded"], "unprovided evidence needs an explicit exclusion reason"
    if budget == 10:
        assert not context["candidates"], "an oversized candidate cannot become a clipped classification"
        assert scope["oversize"]
    else:
        changed_unit_is_complete(context, source=changed, start_line=line)
        assert "pkg/ports.py" in scope["omitted"]


@pytest.mark.parametrize("path", ["pkg/work.py", "pkg/ports.py"])
def test_changed_selected_or_related_source_rejects_stale_measurement(tmp_path, measured, path):
    report, _, line, _ = measured
    (tmp_path / path).write_text("def replaced():\n    return 'changed after measurement'\n")
    reason = None
    try:
        build_context(report, tmp_path, changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}])
    except ValueError as error:
        reason = str(error)
    assert reason and any(word in reason for word in ("changed", "measurement", "hash")), "STALE_SOURCE_REJECTED"


def test_changed_path_outside_inventory_is_excluded_visibly(tmp_path, measured):
    report, _, _, _ = measured
    context = build_context(report, tmp_path, changed_ranges=[{"path": "other.py", "start_line": 1, "end_line": 1}])
    assert context["candidates"] == [], "UNMEASURED_SOURCE_NOT_CLASSIFIED"
    assert any(item["path"] == "other.py" and item["reason"] == "outside_measured_inventory"
               for item in context["source_scope"]["excluded"]), "UNMEASURED_SOURCE_VISIBLE"


def test_base_ref_selects_real_changed_function(tmp_path, measured):
    _, sources, line, changed = measured
    commit_source_baseline(tmp_path)
    sources["pkg/work.py"] = sources["pkg/work.py"].replace("port.store('work')", "port.store('updated')")
    (tmp_path / "pkg/work.py").write_text(sources["pkg/work.py"])
    context = build_context(source_report(sources), tmp_path, base_ref="HEAD", max_source_bytes=1600)
    assert any(item["source"] == changed.replace("'work'", "'updated'") for item in context["sources"]), "GIT_CHANGED_FUNCTION_SELECTED"
    changed_unit_is_complete(context, source=changed.replace("'work'", "'updated'"), start_line=line)
    assert context["source_scope"]["selection"]


@pytest.mark.parametrize("path", ["pkg/space dir/work name.py", "pkg/tab\tname.py"], ids=["spaces", "tab"])
def test_base_ref_preserves_git_path_identity_for_special_filenames(tmp_path, path):
    before = "def changed():\n    return 'before'\n"
    after = "def changed():\n    return 'after'\n"
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(before)
    commit_source_baseline(tmp_path)
    file.write_text(after)
    context = build_context(source_report({path: after}), tmp_path, base_ref="HEAD")
    assert len(context["candidates"]) == 1 and context["candidates"][0]["path"] == path, "SPECIAL_GIT_PATH_TARGETED"
    assert context["sources"][0]["source"] == after
    assert context["sources"][0]["start_line"] == 1 and context["sources"][0]["end_line"] == 2


def test_task_claims_bind_to_candidate_source_span_and_contract(tmp_path, measured):
    report, _, line, _ = measured
    context = build_context(report, tmp_path, changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}])
    tasks = evaluation_tasks(context)
    assert all(task["path"] == "pkg/work.py" and task["start_line"] == line for task in tasks), "TASK_SOURCE_BOUND"
    tasks_are_grounded(tasks, context)


@pytest.mark.parametrize("choice", ["insufficient_evidence", "no_concern"])
def test_classification_retains_abstention_and_exact_candidate_without_clearing_gate(tmp_path, measured, monkeypatch, choice):
    report, _, line, _ = measured
    report["status"] = "violation"
    RecordingEvaluator.calls = []
    monkeypatch.setattr(RecordingEvaluator, "choice", choice)
    monkeypatch.setattr("scripts.architecture.diagnostics.TypeSafeEvaluator", RecordingEvaluator)
    result = diagnose(report, tmp_path, {"endpoint": "https://fixture.invalid", "model": "fixture",
                                        "changed_ranges": [{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}]})
    assert result["status"] == "complete"
    assert result["deterministic_status"] == "violation", "DETERMINISTIC_FAILURE_PRESERVED"
    assert {item["outcome"] for item in result["assessments"]} == {choice}, "PROVIDER_ABSTENTION_RETAINED"
    assert len(result["assessments"]) == len(RecordingEvaluator.calls) == 3
    for item in result["assessments"]:
        assert item["candidate_id"]
        assert item["path"] == "pkg/work.py"
        assert item["start_line"] == line and item["end_line"] == line + 3
        assert item["claim"] and item["contract"]


def test_candidate_that_cannot_fit_is_inconclusive_without_provider_call(tmp_path, measured, monkeypatch):
    report, _, line, _ = measured
    RecordingEvaluator.calls = []
    monkeypatch.setattr("scripts.architecture.diagnostics.TypeSafeEvaluator", RecordingEvaluator)
    result = diagnose(report, tmp_path, {"endpoint": "https://fixture.invalid", "model": "fixture", "max_source_bytes": 10,
                                        "changed_ranges": [{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}]})
    assert result["status"] == "inconclusive", "MISSING_CANDIDATE_INCONCLUSIVE"
    assert result["assessments"] == []
    assert RecordingEvaluator.calls == []


def test_whole_new_module_prioritizes_behavior_over_module_metadata(tmp_path):
    source = (
        '\"\"\"New module with useful behavior below its metadata.\"\"\"\n'
        "from __future__ import annotations\n"
        "from pathlib import Path\n\n"
        "def compute(value: int) -> int:\n"
        "    return value + 1\n\n"
        "def persist(path: Path, value: int) -> None:\n"
        "    path.write_text(str(compute(value)))\n"
    )
    path = "pkg/new_module.py"
    file = tmp_path / path
    file.parent.mkdir(parents=True)
    file.write_text(source)
    context = build_context(source_report({path: source}), tmp_path, max_candidates=2,
                            changed_ranges=[{"path": path, "start_line": 1, "end_line": source.count("\n")}])
    assert [item["symbol"] for item in context["candidates"]] == ["compute", "persist"], "NEW_MODULE_BEHAVIOR_SELECTED_BEFORE_METADATA"
    assert [item["kind"] for item in context["sources"] if item["relation"] == "candidate"] == ["FunctionDef", "FunctionDef"]
    assert all(item["path"] == path for item in context["candidates"])
