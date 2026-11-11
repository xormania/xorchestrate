"""Role-balanced, coherent Git changes and statically bound packet context."""
from __future__ import annotations

import hashlib

import pytest

from scripts.architecture.context import build_context
from tests.hexagonal.contracts.architecture_packets import coherent_change, declared_binding_chain
from tests.hexagonal.support.architecture_packets import baseline, binding_chain, measure, role_competition


@pytest.mark.parametrize("priorities", [("application", "adapters", "tools"), ("adapters", "application", "tools")])
def test_role_priority_rounds_preserve_changed_application_and_adapter_candidates(tmp_path, priorities):
    report, ranges = role_competition(tmp_path)
    context = build_context(report, tmp_path, changed_ranges=ranges, role_priorities=priorities, max_candidates=3)
    roles = [item["role"] for item in context["candidates"]]
    assert roles == list(priorities), "DECLARED_ROLE_ROUNDS_PREVENT_CROWDING"
    assert len({item["candidate_id"] for item in context["candidates"]}) == 3


@pytest.mark.parametrize("priorities", [("missing",), ("application", "application")])
def test_role_priorities_must_name_distinct_declared_roles(tmp_path, priorities):
    report, ranges = role_competition(tmp_path)
    reason = None
    try:
        build_context(report, tmp_path, changed_ranges=ranges, role_priorities=priorities, max_candidates=3)
    except ValueError as error:
        reason = str(error)
    assert reason is not None and "role" in reason.lower(), "ROLE_PRIORITY_CONFIGURATION_VALIDATED"


def test_modified_function_has_coherent_before_after_source_and_git_identity(tmp_path):
    before = "def execute(port):\n    return port.read('old')\n"
    after = "def execute(port):\n    return port.read('new')\n"
    commit = baseline(tmp_path, {"sample/work.py": before})
    report = measure(tmp_path, {"sample/work.py": after})
    context = build_context(report, tmp_path, base_ref="HEAD", max_source_bytes=2000)
    assert context.get("before_sources"), "BEFORE_CHANGE_EVIDENCE_RETAINED"
    previous = context["before_sources"][0]
    assert previous["git_commit"] == commit and previous["sha256"] == hashlib.sha256(before.encode()).hexdigest(), "BEFORE_SOURCE_PROVENANCE_BOUND"
    coherent_change(context, before=before, after=after, base_commit=commit, path="sample/work.py")
    assert context["source_scope"]["source_bytes"] == sum(len(item["source"].encode()) for item in context["sources"] + context["before_sources"])


def test_deleting_only_an_authority_guard_keeps_the_surviving_behavior_visible(tmp_path):
    before = "def execute(user):\n    if not user.authorized:\n        raise ValueError('denied')\n    return 'accepted'\n"
    after = "def execute(user):\n    return 'accepted'\n"
    commit = baseline(tmp_path, {"sample/work.py": before})
    report = measure(tmp_path, {"sample/work.py": after})
    context = build_context(report, tmp_path, base_ref="HEAD", max_source_bytes=2000)
    assert len(context["candidates"]) == 1 and context["candidates"][0]["symbol"] == "execute", "PURE_STATEMENT_DELETION_VISIBLE"
    coherent_change(context, before=before, after=after, base_commit=commit, path="sample/work.py")


@pytest.mark.parametrize("deleted_file", [False, True])
def test_deleted_declared_behavior_remains_visible_from_the_base_snapshot(tmp_path, deleted_file):
    removed = "def removed():\n    return 'essential-behavior'\n"
    stable = "def stable():\n    return 'healthy'\n"
    sources = {"sample/work.py": removed + "\n" + stable} if not deleted_file else {"sample/work.py": removed, "sample/keep.py": stable}
    commit = baseline(tmp_path, sources)
    if deleted_file:
        (tmp_path / "sample/work.py").unlink()
        sources.pop("sample/work.py")
    else:
        sources["sample/work.py"] = stable
    report = measure(tmp_path, sources)
    context = build_context(report, tmp_path, base_ref="HEAD", max_candidates=4)
    deleted = [item for item in context["candidates"] if item.get("change", {}).get("kind") == "deleted" and item["symbol"] == "removed"]
    assert len(deleted) == 1, "DELETED_BEHAVIOR_IS_A_DIAGNOSTIC_CANDIDATE"
    candidate = deleted[0]
    assert candidate["side"] == "before" and candidate["change"]["after_unit_id"] is None
    previous = next(item for item in context["before_sources"] if item["unit_id"] == candidate["change"]["before_unit_id"])
    assert previous["source"] == removed and previous["git_commit"] == commit
    assert not any(item["symbol"] == "removed" for item in context["sources"])


def test_deleted_file_outside_declared_source_roots_is_not_promoted_to_candidate(tmp_path):
    baseline(tmp_path, {"sample/work.py": "def keep():\n    return 1\n", "outside/old.py": "def removed():\n    return 2\n"})
    (tmp_path / "outside/old.py").unlink()
    report = measure(tmp_path, {"sample/work.py": "def keep():\n    return 1\n"})
    context = build_context(report, tmp_path, base_ref="HEAD", max_candidates=4)
    assert not any(item["path"] == "outside/old.py" for item in context["candidates"]), "UNDECLARED_DELETED_SOURCE_EXCLUDED"
    assert any(item["path"] == "outside/old.py" for item in context["source_scope"]["excluded"]), "UNDECLARED_DELETION_VISIBLE"


def test_pair_budget_omits_a_whole_change_instead_of_a_one_sided_packet(tmp_path):
    before = "def execute():\n    return 'old'\n"
    after = "def execute():\n    return 'new'\n"
    baseline(tmp_path, {"sample/work.py": before})
    report = measure(tmp_path, {"sample/work.py": after})
    budget = len(after.encode()) + 1
    context = build_context(report, tmp_path, base_ref="HEAD", max_source_bytes=budget)
    assert context["candidates"] == [], "CHANGE_PAIR_BUDGET_IS_ATOMIC"
    assert context["sources"] == [] and context["before_sources"] == []
    assert any(item["reason"] == "candidate_pair_omitted" for item in context["missing_inputs"])
    assert context["source_scope"]["source_bytes"] <= budget


def test_parameterized_contract_chain_keeps_wrapper_fixture_and_actual_subject(tmp_path):
    report, _ = binding_chain(tmp_path)
    context = build_context(report, tmp_path,
                            changed_ranges=[{"path": "checks/contracts/consumer.py", "start_line": 1, "end_line": 2}],
                            max_files=8, max_source_bytes=4000)
    required = {("checks/test_consumer.py", "test_consumer"), ("checks/test_consumer.py", "subject"),
                ("checks/support/consumer.py", "make_subject"), ("sample/subject.py", "Subject")}
    assert required <= {(item["path"], item["symbol"]) for item in context["sources"]}, "CONTRACT_BINDING_CHAIN_RETAINED"
    declared_binding_chain(context)


@pytest.mark.parametrize("binding,constructor", [
    ("import sample.subject", "sample.subject.Subject"),
    ("import sample.subject as subject", "subject.Subject"),
    ("from sample.subject import Subject", "Subject"),
], ids=["dotted", "alias", "from"])
def test_known_subject_binding_survives_each_supported_python_import_form(tmp_path, binding, constructor):
    sources = {
        "sample/subject.py": "class Subject:\n    def execute(self):\n        return 1\n",
        "sample/use.py": binding + "\n\ndef make():\n    return " + constructor + "()\n",
    }
    report = measure(tmp_path, sources)
    context = build_context(report, tmp_path,
                            changed_ranges=[{"path": "sample/use.py", "start_line": 3, "end_line": 4}], max_source_bytes=2000)
    assert any(item["path"] == "sample/subject.py" and item["symbol"] == "Subject" for item in context["sources"]), "DECLARED_IMPORT_SUBJECT_BOUND"
    assert not any(item["reason"] == "dependency_not_measured" for item in context["missing_inputs"])
