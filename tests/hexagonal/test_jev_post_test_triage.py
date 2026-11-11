"""Composition contracts for current source, executed failures and advisory triage."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.hexagonal.contracts.jev_triage import post_test_advice_is_bound, provider_did_not_run
from tests.hexagonal.support.ci_candidate import write_json
from tests.hexagonal.support.jev_triage import (
    AtomicProvider,
    many_complete_units_context,
    observe_triage,
    repeated_metadata_context,
    replace_inventory,
    revision_context,
    triage_evidence,
)


@pytest.fixture
def evidence(tmp_path):
    return triage_evidence(tmp_path)


def test_failed_execution_and_current_binding_reach_advisory_atomic_questions(evidence):
    original = deepcopy(evidence["result"])
    provider = AtomicProvider()
    report = observe_triage(evidence, provider)
    assert report["status"] == "complete" and provider.calls, "CURRENT_FAILURE_TRIAGE_EXECUTES"
    post_test_advice_is_bound(report, evidence["context"], original)
    assert evidence["result"] == original
    kinds = {row["kind"] for row in report["evidence"]}
    assert {"contract", "test_source", "binding_source", "test_observation"} <= kinds
    assert any("value mismatch" in json.dumps(row) for row in report["evidence"]), "BOUND_FAILURE_DETAIL_RETAINED"
    assert any("port.save" in row.get("content", "") for row in report["evidence"] if row["kind"] == "binding_source")
    for call in provider.calls:
        assert call["context"]["evidence"]
        assert all(isinstance(question, str) and question for question in call["questions"].values())


@pytest.mark.parametrize("fault", ("result_head", "dirty_source", "metadata_head", "captured_context"))
def test_foreign_candidate_observations_stop_before_provider(evidence, fault):
    if fault == "result_head":
        evidence["result"]["source_sha"] = "f" * 40
    elif fault == "dirty_source":
        (evidence["root"] / "pkg/work.py").write_text("def persist(port, value):\n    return 'unmeasured'\n")
    elif fault == "metadata_head":
        evidence["metadata"]["run"]["head_sha"] = "f" * 40
    else:
        captured = deepcopy(evidence["context"])
        captured["checkout"]["tree"] = "f" * 40
        write_json(evidence["artifacts"] / "candidate-context--attempt-1/context.json", captured)
    provider = AtomicProvider()
    provider_did_not_run(observe_triage(evidence, provider), provider)


@pytest.mark.parametrize("fault", ("missing_healthy_lane", "foreign_healthy_manifest"))
def test_one_unqualified_lane_does_not_erase_other_current_failures(evidence, fault):
    name = "framework-tests-python-3.12-shard-2-of-2--attempt-1"
    path = evidence["artifacts"] / name / "producer.json"
    if fault == "missing_healthy_lane":
        path.unlink()
    else:
        value = json.loads(path.read_text())
        value["context"]["source_sha"] = "f" * 40
        write_json(path, value)
    provider = AtomicProvider()
    report = observe_triage(evidence, provider)
    assert report["status"] == "complete" and provider.calls, "CURRENT_PARTIAL_LANES_REMAIN_USABLE"
    assert any(item.get("lane") == "framework-tests-python-3.12-shard-2-of-2" for item in report["missing_evidence"])
    assert any(row["check_id"] == "failure_regression" for row in report["assessments"])


@pytest.mark.parametrize("fault", ("missing_junit", "different_case"))
def test_failure_regression_requires_its_own_executed_assertion_detail(evidence, fault):
    path = evidence["artifacts"] / "framework-tests-python-3.12-shard-1-of-2--attempt-1/run/tests.xml"
    if fault == "missing_junit":
        path.unlink()
    else:
        path.write_text('<testsuite><testcase classname="elsewhere" name="test_unrelated">'
                        '<failure message="AssertionError: unrelated">Not this test.</failure></testcase></testsuite>')
    report = observe_triage(evidence, AtomicProvider())
    claims = [row for row in report["assessments"] if row["check_id"] == "failure_regression"]
    assert claims and all(row["outcome"] == "insufficient_evidence" for row in claims), "FAILURE_DETAIL_MUST_MATCH_CASE"
    assert any(item["reason"] for item in report["missing_evidence"])


def test_unexecuted_case_cannot_turn_a_lane_failure_into_a_behavioral_defect(evidence):
    replace_inventory(evidence, lambda inventory: inventory["cases"][0].update(outcome="not_run"))
    provider = AtomicProvider()
    report = observe_triage(evidence, provider)
    assert not any(row["check_id"] == "failure_regression" for row in report["assessments"]), "UNRUN_CASE_IS_NOT_A_REGRESSION"
    assert report["deterministic_status"] == "failed"
    assert not any(row["outcome"] == "concern" for row in report["assessments"])


def test_source_budget_exposes_missing_binding_without_forced_concern(evidence):
    provider = AtomicProvider()
    report = observe_triage(evidence, provider, max_source_bytes=1)
    assert report["assessments"] and report["missing_evidence"]
    assert all(row["outcome"] == "insufficient_evidence" for row in report["assessments"]), "MISSING_SOURCE_IS_NOT_DEFECT"
    assert provider.calls == []


@pytest.mark.parametrize("fault", ("changed_bytes", "swapped_shard", "duplicate_report", "wrong_count"))
def test_unbound_inventory_is_excluded_even_when_the_lane_and_candidate_match(evidence, fault):
    path = evidence["artifacts"] / "framework-tests-python-3.12-shard-1-of-2--attempt-1/run/inventory.json"
    if fault == "changed_bytes":
        value = json.loads(path.read_text())
        value["cases"][0]["outcome"] = "passed"
        write_json(path, value)
    elif fault == "swapped_shard":
        replace_inventory(evidence, lambda inventory: inventory["shard"].update(index=2))
    elif fault == "wrong_count":
        replace_inventory(evidence, lambda inventory: inventory.update(selected=2))
    else:
        directory = path.parent
        for name in ("report.json", "candidate.json", "inventory.json", "tests.xml"):
            target = directory / "duplicate" / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes((directory / name).read_bytes())
    provider = AtomicProvider()
    report = observe_triage(evidence, provider)
    assert not any(row["check_id"] == "failure_regression" for row in report["assessments"]), "INVENTORY_BYTES_MUST_BE_QUALIFIED"
    assert any(item.get("lane") == "framework-tests-python-3.12-shard-1-of-2" for item in report["missing_evidence"])


def test_source_stage_opt_in_asks_bound_atomic_questions_before_test_evidence(tmp_path, monkeypatch):
    from scripts.architecture.diagnostics import diagnose
    from tests.hexagonal.support.architecture_context import measured_sources, source_report

    sources, line, _ = measured_sources(tmp_path)
    report = source_report(sources)
    report["status"] = "violation"
    provider = AtomicProvider()
    monkeypatch.setattr("scripts.architecture.diagnostics.TypeSafeEvaluator", provider)
    profile = json.loads((Path(__file__).parents[2] / "contracts/hexagonal-jev.json").read_text())
    assert profile.get("source_atomic") is True, "SOURCE_CI_SELECTS_ATOMIC_PATH"
    if "role_priorities" in profile:
        profile["role_priorities"] = []  # This synthetic contract has its own core/adapters/tests roles.
    result = diagnose(report, tmp_path, {**profile, "changed_ranges": [
        {"path": "pkg/work.py", "start_line": line, "end_line": line + 3}]})
    assert result.get("stage") == "source" and result.get("advisory") is True, "SOURCE_STAGE_EXPLICIT"
    assert result["deterministic_status"] == "violation" and report["status"] == "violation"
    assert result["assessments"] and len(provider.calls) == 1
    assert all(row["judgments"] and row["supporting_evidence_ids"] for row in result["assessments"])
    assert not any(item["kind"] == "test_observation" for item in result["evidence"])


def test_stdlib_summary_exposes_stage_evidence_and_uncertainty_without_html_execution(evidence):
    from scripts.ci.result import jev_triage_markdown

    report = observe_triage(evidence, AtomicProvider())
    report["assessments"][0]["claim"] = "<script>untrusted</script> | claim"
    rendered = jev_triage_markdown(report)
    assert "post_test" in rendered and "failed" in rendered
    assert "Applicable" in rendered and "Sufficient evidence" in rendered
    assert "Supporting evidence IDs" in rendered and "tests/hexagonal/test_work.py" in rendered
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert "Model probabilities are uncalibrated" in rendered


def test_post_failure_ci_triage_remains_optional_and_retains_its_evidence():
    workflow = json.loads((Path(__file__).parents[2] / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["ci-result"]["steps"]
    qualification = next(index for index, step in enumerate(steps) if step.get("id") == "qualification")
    triage = next((step for step in steps if "-m scripts.architecture.triage" in step.get("run", "")), None)
    assert triage is not None, "POST_TEST_TRIAGE_IS_WIRED"
    assert steps.index(triage) > qualification
    assert triage.get("continue-on-error") is True and triage.get("timeout-minutes", 0) > 0
    assert "!cancelled()" in triage["if"] and "success()" not in triage["if"]
    assert "steps.qualification.outcome == 'success'" not in triage["if"]
    assert "reuse != 'true'" in triage["if"]
    assert all(token in triage["run"] for token in ("--result", "--metadata", "--artifacts", "--summary", "triage.json"))


@pytest.mark.parametrize("kind", ("deleted", "modified"))
def test_source_revision_subjects_retain_exact_before_after_focal_evidence(kind):
    from scripts.architecture.atomic import source_subjects

    context = revision_context(kind)
    subjects, evidence = source_subjects(context, ["semantic_boundaries"])
    expected = {"old-unit", "new-unit"} if kind == "modified" else {"old-unit"}
    assert expected <= set(evidence), "OLD_SIDE_SOURCE_IS_REQUIRED_EVIDENCE"
    assert evidence["old-unit"]["side"] == "before" and "return True" in evidence["old-unit"]["content"]
    assert set(subjects[0].get("focal_evidence_ids", [])) == expected, "SOURCE_CHANGE_FOCAL_IDS_RETAINED"
    assert subjects[0].get("change", {}).get("kind") == kind
    if kind == "deleted":
        assert "remov" in subjects[0]["claim"].lower(), "DELETED_CODE_IS_JUDGED_AS_A_REMOVAL"


@pytest.mark.parametrize("kind", ("deleted", "modified"))
def test_projected_source_revisions_retain_immutable_file_identity(tmp_path, kind):
    from scripts.architecture.atomic import source_subjects
    from scripts.architecture.requests import provider_context
    from tests.hexagonal.support.architecture_context import commit_source_baseline
    from tests.hexagonal.support.ci_candidate import git

    context = revision_context(kind)
    context["profile"] = {"roles": {"core": {"allow_dynamic": False}}}
    path = tmp_path / "sample/work.py"
    path.parent.mkdir()
    path.write_text(context["before_sources"][0]["source"])
    commit_source_baseline(tmp_path)
    before_commit = git(tmp_path, "rev-parse", "HEAD")
    if kind == "modified":
        path.write_text(context["sources"][0]["source"])
    else:
        path.unlink()
    git(tmp_path, "add", "--all")
    git(tmp_path, "commit", "-qm", "resulting source revision")
    after_commit = git(tmp_path, "rev-parse", "HEAD")
    context["candidates"][0]["change"]["base_commit"] = before_commit
    expected = {}
    for unit in [*context["sources"], *context["before_sources"]]:
        unit.update(module="sample.work", role="core", sha256=hashlib.sha256(unit["source"].encode()).hexdigest(),
                    git_commit=before_commit if unit["side"] == "before" else after_commit,
                    support_for=[context["candidates"][0]["candidate_id"]])
        expected[unit["unit_id"]] = {name: unit[name] for name in ("sha256", "module", "role", "side", "git_commit", "support_for")}
    projected = provider_context(context)
    assert projected["source_files"], "SOURCE_IDENTITY_IS_FACTORED_INTO_FILE_TABLE"
    subjects, evidence = source_subjects(projected, ["semantic_boundaries"])
    for identifier, identity in expected.items():
        actual = evidence.get(identifier, {})
        assert {name: actual.get(name) for name in identity} == identity, "PROJECTED_SOURCE_FILE_IDENTITY_RETAINED"
    assert set(subjects[0]["focal_evidence_ids"]) == set(expected), "PROJECTED_SOURCE_FOCAL_IDS_RETAINED"
    assert subjects[0]["change"]["base_commit"] == before_commit


def test_missing_old_side_focal_unit_stops_forced_source_classification():
    from scripts.architecture.atomic import evaluate_subjects, source_subjects

    context = revision_context("modified")
    context["before_sources"] = []
    subjects, _ = source_subjects(context, ["semantic_boundaries"])
    provider = AtomicProvider()
    rows, _ = evaluate_subjects(subjects, {"endpoint": "https://fixture.invalid", "model": "fixture"}, evaluator_factory=provider)
    assert provider.calls == [], "OMITTED_FOCAL_UNIT_IS_NOT_CLASSIFIED"
    assert rows[0]["outcome"] == "insufficient_evidence"


def test_atomic_transport_preserves_explicit_local_http_configuration():
    from scripts.architecture.atomic import evaluate_subjects
    from tests.hexagonal.support.jev_efficiency import http_choices

    subjects = [{"subject_id": "transport-control", "check_id": "control", "claim": "This source is observable.",
                 "evidence": {"source-1": {"kind": "source", "content": "VALUE = 1\n"}}, "required_evidence": ["source"]}]
    with http_choices() as server:
        server["choices"] = {"q" + str(index): "yes" for index in range(4)}
        rows, executions = evaluate_subjects(subjects, server["options"])
        assert len(server["requests"]) == 1, "CONFIGURED_ATOMIC_TRANSPORT_IS_USED"
        assert rows[0]["outcome"] == "concern" and rows[0]["judgments"]
        assert executions[0]["provider_calls"] == 1


def test_repeated_source_metadata_does_not_prevent_complete_bounded_atomic_work():
    from scripts.architecture.atomic import evaluate_subjects, source_subjects

    subjects, _ = source_subjects(repeated_metadata_context(), ["semantic_boundaries", "port_abstraction", "evidence_coverage"])
    provider = AtomicProvider()
    rows, _ = evaluate_subjects(subjects, {"endpoint": "https://fixture.invalid", "model": "fixture"}, evaluator_factory=provider)
    assert provider.calls and len(rows) == 9, "COHERENT_SOURCE_PACKETS_REACH_PROVIDER"
    assert all({"applicable", "sufficient", "claim"} <= set(row["judgments"]) for row in rows), "ALL_SOURCE_FACETS_COMPLETE"
    assert all(row["outcome"] == "concern" for row in rows)
    supplied = {identifier for call in provider.calls for identifier in call["context"]["evidence"]}
    assert {"unit-0", "unit-1", "unit-2"} <= supplied, "SOURCE_FACTORING_RETAINS_ALL_FOCAL_UNITS"


def test_many_complete_units_finish_all_facets_without_repeating_selected_ownership():
    from scripts.architecture.atomic import evaluate_subjects, source_subjects

    context = many_complete_units_context()
    assert context["source_scope"]["source_bytes"] <= 24000
    subjects, retained = source_subjects(context, ["semantic_boundaries", "port_abstraction", "evidence_coverage"])
    original = deepcopy(retained)
    expected = {identifier: item["content"] for identifier, item in retained.items() if item["kind"] == "source"}
    assert len(expected) == 114 and len(subjects) == 9
    provider = AtomicProvider()
    rows, _ = evaluate_subjects(subjects, {"endpoint": "https://fixture.invalid", "model": "fixture"}, evaluator_factory=provider)
    assert all({"applicable", "sufficient", "claim"} <= set(row["judgments"]) for row in rows), "ALL_MANY_UNIT_ATOMIC_FACETS_COMPLETE"
    supplied = {}
    for call in provider.calls:
        for identifier, item in call["context"]["evidence"].items():
            if item["kind"] == "source":
                supplied[identifier] = item["content"]
                role = item.get("role", call["context"]["source_files"][item["file_id"]].get("role"))
                assert role == original[identifier]["role"]
    assert supplied == expected, "BOUNDED_MANY_UNIT_PACKETS_RETAIN_COMPLETE_SOURCE"
    assert retained == original, "PROVIDER_FACTORING_MUST_NOT_MUTATE_RETAINED_OWNERSHIP"
    assert all(item["support_for"] == [format(index, "024x") for index in range(3)]
               for item in retained.values() if item["kind"] == "source")


def test_many_case_receipts_are_validated_then_explicitly_bounded_for_provider(evidence):
    def expand(inventory):
        template = inventory["cases"][0]
        inventory["cases"] = [{**template, "id": template["id"] + "[case" + str(index) + "]"} for index in range(10)]
        inventory["selected"] = 10

    replace_inventory(evidence, expand)
    junit = evidence["artifacts"] / "framework-tests-python-3.12-shard-1-of-2--attempt-1/run/tests.xml"
    junit.write_text('<testsuite>' + ''.join(
        '<testcase classname="tests.hexagonal.test_work" name="test_value[case' + str(index)
        + ']"><failure message="AssertionError: value mismatch">Wrong value.</failure></testcase>'
        for index in range(10)) + '</testsuite>')
    report = observe_triage(evidence, AtomicProvider(), max_cases_per_subject=2)
    assert report["status"] == "complete" and report["assessments"]
    assert all(len(row.get("selected_case_ids", [])) == 2 for row in report["assessments"]), "CASE_SUBJECT_LIMIT_IS_EXPLICIT"
    assert all(row.get("unselected_case_count") == 8 for row in report["assessments"])
    assert any(row.get("reason") == "case_subject_limit" for row in report["missing_evidence"])
