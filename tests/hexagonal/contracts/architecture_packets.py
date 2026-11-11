"""Observable packet guarantees independent of packet selection machinery."""
from __future__ import annotations

import hashlib


def coherent_change(context, *, before, after, base_commit, path):
    candidates = [item for item in context["candidates"] if item["path"] == path]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["change"]["kind"] == "modified"
    assert candidate["change"]["base_commit"] == base_commit
    previous = next(item for item in context["before_sources"] if item["unit_id"] == candidate["change"]["before_unit_id"])
    current = next(item for item in context["sources"] if item["unit_id"] == candidate["change"]["after_unit_id"])
    assert previous["source"] == before and current["source"] == after
    assert previous["side"] == "before" and current["side"] == "after"
    assert previous["git_commit"] == base_commit
    assert previous["contract_basis"] == "current_profile"
    assert previous["unit_id"] != current["unit_id"]
    assert previous["sha256"] == hashlib.sha256(before.encode()).hexdigest()
    assert candidate["candidate_id"] in previous["support_for"]
    assert candidate["candidate_id"] in current["support_for"]


def declared_binding_chain(context):
    supplied = {(item["path"], item["symbol"]) for item in context["sources"]}
    required = {("checks/contracts/consumer.py", "consumer_contract"),
                ("checks/test_consumer.py", "test_consumer"), ("checks/test_consumer.py", "subject"),
                ("checks/support/consumer.py", "make_subject"), ("sample/subject.py", "Subject")}
    assert required <= supplied
    assert ("checks/support/unrelated.py", "make_subject") not in supplied
    assert all(item["relation"] for item in context["sources"])
    assert not any(item.get("coverage_proven") is True for item in context["sources"])
