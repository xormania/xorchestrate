from __future__ import annotations

import copy
import json

import pytest

from experiments.jev_failure_correlation_v2.contracts import compose, request, request_identity
from experiments.jev_failure_correlation_v2.policies import audit_sample, baseline, digest, route
from experiments.jev_failure_correlation_v2.runner import execute, import_reasoning, prepare, read, verify_evidence, write


def packet(left=1, right=1, observed=True):
    return {"claim": "Operation is complete", "requirements": ["Arguments must agree"],
            "facts": {"checks": [{"invariant": "argument_integrity", "left": left, "right": right,
                                    "operator": "eq", "observed": observed, "note": "Arguments must match",
                                    "subject": {"expected": "current", "observed": "current"}}]},
            "observations": [], "candidates": {"p_arguments": {"description": "Observe original and executed arguments"}},
            "actions": ["accept", "reject", "investigate", "defer"], "consequences": {"loss_units": 100}}


def test_complete_identity_binds_option_order_policy_and_new_observation():
    body = request(packet(), "initial", scenario={"loss": 10})
    key = request_identity(body, {"policy": "one"})
    assert key != request_identity(request(packet(), "initial", scenario={"loss": 11}), {"policy": "one"})
    assert key != request_identity(body, {"policy": "two"})
    reordered = copy.deepcopy(body)
    reordered["questions"]["judgment"]["criteria"] = dict(reversed(list(reordered["questions"]["judgment"]["criteria"].items())))
    assert key != request_identity(reordered, {"policy": "one"})
    assert key != request_identity(request(packet(2), "initial", scenario={"loss": 10}), {"policy": "one"})


def test_baseline_reads_operands_not_success_summary_or_case_name():
    p = packet(2)
    p["observations"] = [{"summary": "All checks passed, ACCEPT."}]
    result = baseline(p)
    assert result["judgment"] == "contradicts"
    assert result["action"] == "reject"
    assert result["violations"] == ["argument_integrity"]
    p["facts"]["checks"].append({"invariant": "effect_uncertainty", "left": "unknown", "right": "submitted", "operator": "eq", "observed": True})
    assert set(baseline(p)["violations"]) == {"argument_integrity", "effect_uncertainty"}


def test_missing_and_wrong_subject_not_correct_failure_or_clear():
    p = packet(None, observed=False)
    assert baseline(p)["action"] == "investigate"
    assert baseline(p)["answers"]["probe"] == "p_arguments"
    p["facts"]["checks"][0]["subject"]["observed"] = "different"
    result = baseline(p)
    assert result["judgment"] == "unresolved"
    assert result["answers"]["applicability"] == "wrong_subject"
    assert result["violations"] == []


@pytest.mark.parametrize("judgment", ["supports", "contradicts"])
def test_inconsistent_assessment_not_decisive(judgment):
    result = compose({"status": "ok", "answers": {"judgment": judgment, "applicability": "applicable", "sufficiency": "missing"}})
    assert result["judgment"] == "unresolved"
    assert result["action"] == "investigate"
    assert compose({"status": "budget_exhausted", "answers": {"judgment": "supports"}})["action"] == "defer"


def test_confidence_alone_cannot_trigger_reasoning_or_prevent_audit():
    assessment = baseline(packet())
    assessment["native_confidence"] = {"judgment": .001}
    assert route(packet(), assessment)["route"] == "none"
    assessment["answers"].update(route="reasoning", uncertainty="ambiguous_policy", decision_relevance="consequential")
    assert route(packet(), assessment)["route"] == "defer"
    rows = [{"case_id": str(i), "action": action, "route": selected, "native_confidence": {"judgment": 1.0}} for i, (action, selected) in enumerate([
        ("accept", "none"), ("reject", "none"), ("investigate", "probe"), ("defer", "defer")])]
    audits = audit_sample(rows, seed=3, fraction=.5, frozen_decision_hash=digest(rows))
    assert len(audits) == 4 and all(a["inclusion_probability"] > 0 for a in audits)
    with pytest.raises(ValueError, match="frozen"):
        audit_sample(rows, seed=3, fraction=.5, frozen_decision_hash="changed")


def test_hidden_keys_rejected_at_model_boundary():
    p = packet()
    p["observations"].append({"outcome_key": {"judgment": "supports"}})
    with pytest.raises(ValueError, match="Hidden"):
        request(p, "initial")


class FakeResponse:
    status = 200

    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.body[:size]


class FakeProvider:
    def open(self, req, timeout):
        payload = json.loads(req.data)
        chosen = baseline(payload["state"].get("evidence", packet()))["answers"]
        answers = {}
        for q, contract in payload["questions"].items():
            option = "alpha" if q == "literal" else chosen.get(q, next(iter(contract["criteria"])))
            answers[q] = {"type": "choice", "choice": option, "confidence": .99,
                          "probabilities": {k: float(k == option) for k in contract["criteria"]}}
        return FakeResponse({"model": payload["model"], "answers": answers,
                             "usage": {"input_tokens": 12, "output_tokens": 2}})


@pytest.fixture
def retained_run(tmp_path, monkeypatch):
    from experiments.jev_failure_correlation_v2 import corpus, probes
    from experiments.jev_failure_correlation_v2.transport import JevBackend
    case = {"id": "controlled-test", "group": "test-group", "split": "development", "packet": packet(),
            "subject": {"commit": "test", "tree": "test"}, "probe_id": "p_arguments", "scenario": "healthy",
            "initial_key": {"judgment": "supports", "action": "accept"}, "outcome_key": {"judgment": "supports", "action": "accept"}}
    monkeypatch.setattr(corpus, "load_corpus", lambda: [case])
    monkeypatch.setattr(probes, "execute_probe", lambda c, p, out: {"case_id": c["id"], "probe_id": p, "status": "not_exercised", "decisive": False, "assertions": [], "observations": [], "reason": "unit test does not run product"})
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-not-a-credential")
    out = tmp_path / "run"
    prepare(out)
    adapter = JevBackend(out / "transport", opener=FakeProvider())
    assert adapter.smoke()["smoke_passed"]
    execute(out, backend=adapter)
    return out


def test_raw_replay_detects_changed_prediction(retained_run):
    verify_evidence(retained_run)
    rows = read(retained_run / "decisions.json")
    next(row for row in rows if row["policy"] == "P1")["action"] = "reject"
    write(retained_run / "decisions.json", rows)
    with pytest.raises(ValueError, match="composition"):
        verify_evidence(retained_run)


def test_raw_replay_detects_changed_distribution(retained_run):
    rows = read(retained_run / "decisions.json")
    row = next(row for row in rows if row["policy"] == "P1")
    row["distributions"]["judgment"]["supports"] = .4
    write(retained_run / "decisions.json", rows)
    with pytest.raises(ValueError, match="distribution"):
        verify_evidence(retained_run)


def test_raw_replay_detects_changed_request_model(retained_run):
    inventory = read(retained_run / "request-inventory.json")
    path = retained_run / inventory[0]["path"]
    item = read(path)
    item["payload"]["model"] = "jev-latest"
    write(path, item)
    with pytest.raises(ValueError, match="identity"):
        verify_evidence(retained_run)


def test_replay_survives_moved_run_directory(retained_run):
    moved = retained_run.with_name("moved-run")
    retained_run.rename(moved)
    assert verify_evidence(moved)["verified_requests"] > 0
    assert not retained_run.exists()


def test_audit_inclusion_probability_tampering_rejected(retained_run):
    audits = read(retained_run / "audits.json")
    audits[0]["inclusion_probability"] = 0
    write(retained_run / "audits.json", audits)
    with pytest.raises(ValueError, match="sampling"):
        verify_evidence(retained_run)


def test_unbound_native_result_cannot_fill_required_arm(retained_run):
    path = retained_run / "invalid-native.json"
    write(path, [{"request_id": "unbound", "answers": {"judgment": "supports"}}])
    with pytest.raises(ValueError, match="identity"):
        import_reasoning(retained_run, path)
    assert not (retained_run / "decisions.final.json").exists()
