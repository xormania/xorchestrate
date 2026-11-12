"""Independent advisory execution preserves required verification and source identity."""

import hashlib
import json
import re
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.ci import advice as subject
from tests.hexagonal.contracts.ci_reuse import replace_path
from tests.hexagonal.support.ci_advice import AdviceAPI
from tests.hexagonal.support.ci_candidate import artifact_zip

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return json.loads((ROOT / ".github/workflows" / name).read_text())


def test_required_validation_graph_contains_no_provider_work():
    jobs = workflow("ci.yml")["jobs"]
    pending, seen = ["ci-result"], set()
    while pending:
        identifier = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        job = jobs[identifier]
        assert "TYPESAFE_API_KEY" not in json.dumps(job), "CI_REQUIRED_GRAPH_HAS_NO_PROVIDER_WORK"
        pending.extend(job.get("needs", []))
    assert "changes" in seen and "application-tests" in seen and "package" in seen


def test_provider_credentials_are_absent_from_the_candidate_validation_workflow():
    document = json.dumps(workflow("ci.yml"))
    assert "secrets.TYPESAFE_API_KEY" not in document and "secrets.JEV_API_KEY" not in document, \
        "CI_CANDIDATE_VALIDATION_HAS_NO_MODEL_CREDENTIAL"


def test_advice_runtime_paths_are_initialized_before_use_in_valid_context(tmp_path):
    job = workflow("jev.yml")["jobs"]["advice"]
    assert not re.search(r"\$\{\{\s*runner\.", json.dumps(job.get("env", {}))), \
        "ADVICE_JOB_ENV_CANNOT_USE_RUNNER_CONTEXT"
    steps = job["steps"]
    variables = ("PYTHONPYCACHEPREFIX", "UV_PROJECT_ENVIRONMENT")
    initializers = [(index, step) for index, step in enumerate(steps)
                    if "GITHUB_ENV" in step.get("run", "") and all(key in step["run"] for key in variables)]
    assert len(initializers) == 1, "ADVICE_RUNTIME_PATHS_REQUIRE_INITIALIZATION"
    index, initializer = initializers[0]
    consumers = [position for position, step in enumerate(steps) if position != index
                 and ("python" in step.get("run", "") or "uv sync" in step.get("run", "")
                      or "setup-uv@" in step.get("uses", ""))]
    assert consumers and index < min(consumers) and not initializer.get("if"), \
        "ADVICE_RUNTIME_PATHS_EXIST_BEFORE_ANY_PYTHON_OR_UV_USE"
    runner_temp, checkout = tmp_path / "runner temp", tmp_path / "checkout"
    runner_temp.mkdir()
    checkout.mkdir()
    env_file = runner_temp / "github-env"
    command = subprocess.run(["bash", "-euo", "pipefail", "-c", initializer["run"]], cwd=checkout,
        env={"RUNNER_TEMP": str(runner_temp), "GITHUB_ENV": str(env_file)}, capture_output=True, text=True)
    assert command.returncode == 0 and env_file.is_file(), "ADVICE_RUNTIME_INITIALIZER_EXECUTES"
    paths = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
    assert paths == {"PYTHONPYCACHEPREFIX": str(runner_temp / "jev-advice-pycache"),
                     "UV_PROJECT_ENVIRONMENT": str(runner_temp / "jev-advice-python")}, \
        "ADVICE_RUNTIME_PATHS_ARE_DISTINCT_AND_OUTSIDE_CHECKOUT"


@pytest.fixture
def peer(tmp_path, monkeypatch):
    value = AdviceAPI(tmp_path / "api")
    monkeypatch.setattr(subject.candidate, "urlopen", value.urlopen)
    monkeypatch.setattr(subject.reuse.candidate, "urlopen", value.urlopen)
    return value


@pytest.mark.parametrize("path,value", (
    (("id",), 102), (("run_attempt",), 2), (("head_sha",), "d" * 40),
    (("repository", "id"), 999), (("head_repository", "full_name"), "other/fork"),
    (("status",), "in_progress"), (("path",), ".github/workflows/unrelated.yml"),
    (("event",), "pull_request_target"),
))
def test_advice_source_must_match_the_completed_ci_event(peer, path, value):
    run = peer.metadata[101]["run"]
    control = subject.validate_source(peer.event, run)
    assert control["run_id"] == 101 and control["source_sha"] == peer.original["source_sha"]
    rejected = False
    try:
        subject.validate_source(peer.event, replace_path(run, path, value))
    except ValueError:
        rejected = True
    assert rejected, "ADVICE_COMPLETED_SOURCE_MUST_MATCH_EVENT_IDENTITY"


def test_preparation_authenticates_real_receipts_before_reserving_provider_access(peer, tmp_path):
    plan = subject.prepare(peer.event, tmp_path / "work", "fixture-token", peer.env, get=peer.get)
    assert plan["status"] == "reservation_required" and plan["provider_needed"] is True
    assert plan["source"]["source_sha"] == "a" * 40 and plan["tested_sha"] == "c" * 40
    assert plan["trusted_sha"] == "f" * 40, "ADVICE_SOURCE_AND_TRUSTED_EXECUTABLE_ARE_DISTINCT"
    reserved = json.loads((tmp_path / "work/reservation/reservation.json").read_text())
    assert reserved == plan
    assert any(path.startswith("/check-suites/") for path in peer.requests)
    assert any(path.startswith("/git/commits/") for path in peer.requests)
    assert plan["artifact_budget"]["count"] == 4


@pytest.mark.parametrize("conclusion", ("success", "failure"))
def test_disabled_automatic_advice_stops_before_evidence_intake(peer, tmp_path, monkeypatch, conclusion):
    peer.event["workflow_run"]["conclusion"] = conclusion
    peer.metadata[101]["run"]["conclusion"] = conclusion
    event_path, github_output, work = tmp_path / "event.json", tmp_path / "github-output", tmp_path / "work"
    event_path.write_text(json.dumps(peer.event))
    for key, value in {**peer.env, "GITHUB_EVENT_PATH": str(event_path), "GITHUB_OUTPUT": str(github_output),
                       "GITHUB_TOKEN": "fixture-token"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(subject, "api_json", lambda url, token: peer.get(url))

    exit_code = subject.main(["prepare", "--work", str(work), "--trusted-root", str(ROOT)])
    plan = json.loads((work / "plan.json").read_text())
    assert exit_code == 0 and plan["status"] == "automatic_disabled" and not peer.requests, \
        "DISABLED_AUTOMATIC_ADVICE_PERFORMS_NO_EVIDENCE_INTAKE"
    assert plan["provider_needed"] is False and "provider_needed=false\n" in github_output.read_text(), \
        "DISABLED_AUTOMATIC_ADVICE_NEVER_REQUESTS_PROVIDER_ACCESS"
    assert not (work / "artifacts").exists() and not (work / "reservation").exists()


@pytest.mark.parametrize("mode", ("automatic", "manual_source", "manual_post_test", "calibration"))
def test_broad_advice_remains_explicitly_available(peer, tmp_path, mode):
    policy = json.loads((ROOT / "contracts/jev-advice.json").read_text())
    event, env = deepcopy(peer.event), dict(peer.env)
    if mode == "automatic":
        policy["automatic"] = {"source": True, "post_test": True}
    else:
        env.update(GITHUB_EVENT_NAME="workflow_dispatch", GITHUB_REF="refs/heads/master")
        event.pop("workflow_run")
        event["inputs"] = {"calibration": "true"} if mode == "calibration" else {
            "calibration": "false", "source_run_id": "101", "source": str(mode == "manual_source").lower(),
            "post_test": str(mode == "manual_post_test").lower()}
    plan = subject.prepare(event, tmp_path / "work", "fixture-token", env, get=peer.get, policy=policy)
    assert plan["status"] == "reservation_required" and plan["provider_needed"] is True, \
        "EXPLICIT_BROAD_ADVICE_REMAINS_AVAILABLE"
    if mode == "calibration":
        assert plan["kind"] == "calibration" and not any("/actions/runs/101" in path for path in peer.requests)
    else:
        expected = {"source": mode != "manual_post_test", "post_test": mode != "manual_source"}
        assert plan["stages"] == expected and plan["source"]["run_id"] == 101, \
            "EXPLICIT_BROAD_ADVICE_BINDS_SELECTED_STAGES_AND_SOURCE"
        assert plan["artifact_budget"]["count"] == (4 if expected["post_test"] else 2), \
            "SOURCE_ONLY_ADVICE_DOES_NOT_DOWNLOAD_TEST_ARCHIVES"


@pytest.mark.parametrize("expired", (False, True))
def test_a_trusted_reservation_suppresses_duplicate_provider_work_even_after_expiry(peer, tmp_path, expired):
    peer.reserve(expired=expired)
    plan = subject.prepare(peer.event, tmp_path / "work", "fixture-token", peer.env, get=peer.get)
    assert plan["status"] == "duplicate_suppressed" and plan["provider_needed"] is False, \
        "ADVICE_DURABLE_RESERVATION_PREVENTS_DUPLICATE_PROVIDER_CALL"
    assert plan["prior_reservation"]["consumer_run_id"] == 701
    assert not (tmp_path / "work/reservation/reservation.json").exists()


@pytest.mark.parametrize("field,value", (("path", ".github/workflows/ci.yml"),
    ("head_branch", "feature"), ("event", "pull_request"), ("repository", {"full_name": "other/repo"})))
def test_foreign_or_candidate_reservations_cannot_suppress_trusted_advice(peer, tmp_path, field, value):
    peer.reserve()
    peer.consumers[701][field] = value
    plan = subject.prepare(peer.event, tmp_path / "work", "fixture-token", peer.env, get=peer.get)
    assert plan["provider_needed"] is True, "ADVICE_UNTRUSTED_RESERVATION_CANNOT_HIDE_WORK"


@pytest.mark.parametrize("reason", ("cancelled", "skipped", "superseded"))
def test_irrelevant_source_attempts_never_request_provider_work(peer, tmp_path, reason):
    if reason != "superseded":
        peer.metadata[101]["run"]["conclusion"] = reason
    plan = subject.prepare(peer.event, tmp_path / "work", "fixture-token", peer.env,
        get=peer.get, observe=lambda *_: {"status": "superseded" if reason == "superseded" else "current"})
    assert plan["provider_needed"] is False
    assert not (tmp_path / "work/reservation/reservation.json").exists()


def test_consumer_rerun_does_not_repeat_an_interrupted_provider_attempt(peer, tmp_path):
    env = {**peer.env, "GITHUB_RUN_ATTEMPT": "2"}

    def no_lookup(url):
        raise AssertionError("A consumer retry must not reinterpret absence of its previous upload as permission")

    plan = subject.prepare(peer.event, tmp_path / "work", "fixture-token", env, get=no_lookup)
    assert plan["status"] == "consumer_rerun_suppressed" and plan["provider_needed"] is False


@pytest.mark.parametrize("fault", ("foreign_source", "digest", "traversal", "symlink", "total_count", "total_bytes", "artifact_name"))
def test_advisory_artifact_intake_reuses_bounded_identity_and_archive_validation(peer, tmp_path, monkeypatch, fault):
    source = subject.validate_source(peer.event, peer.metadata[101]["run"])
    listing = peer.get("https://api.github.com/repos/xormania/xorchestrate/actions/runs/101/artifacts")["artifacts"]
    selected = deepcopy(next(item for item in listing if item["name"] == "ci-result-evidence--attempt-1"))
    budget = {"count": 0, "bytes": 0}
    if fault == "artifact_name":
        selected["name"] = "../../outside--attempt-1"
    elif fault == "foreign_source":
        selected["workflow_run"]["id"] = 102
    elif fault == "digest":
        selected["digest"] = "sha256:" + "0" * 64
    elif fault in {"traversal", "symlink"}:
        payload = artifact_zip([("../escape.json" if fault == "traversal" else "link", b"outside", 
                                 "file" if fault == "traversal" else "symlink")])
        selected.update(digest="sha256:" + hashlib.sha256(payload).hexdigest(), size_in_bytes=len(payload))
        peer.payloads[selected["id"]] = payload
    elif fault == "total_count":
        budget["count"] = subject.MAX_SELECTED_ARCHIVES
    else:
        budget["bytes"] = subject.MAX_TOTAL_ARCHIVE
    rejected = False
    try:
        subject.download_artifact(source, [selected], selected["name"], "fixture-token", tmp_path / "received", budget)
    except ValueError:
        rejected = True
    assert rejected, "ADVICE_UNTRUSTED_ARTIFACT_INTAKE_IS_REFUSED"
    assert not (tmp_path / "escape.json").exists()


def test_independent_consumer_reserves_before_any_secret_bearing_step():
    document = workflow("jev.yml")
    assert document["on"]["workflow_run"] == {"workflows": ["CI"], "types": ["completed"]}
    assert document["permissions"]["contents"] == "read"
    job = document["jobs"]["advice"]
    assert "conclusion == 'success'" not in job.get("if", ""), "ADVICE_CAN_DIAGNOSE_FAILED_VALIDATION"
    steps = job["steps"]
    reserve = next(step for step in steps if step.get("id") == "reserve")
    assert reserve["with"]["if-no-files-found"] == "error"
    secret_steps = [step for step in steps if "secrets.TYPESAFE_API_KEY" in json.dumps(step)]
    assert secret_steps
    for step in secret_steps:
        assert steps.index(step) > steps.index(reserve)
        assert "steps.gate.outputs.provider_needed == 'true'" in step["if"], \
            "DISABLED_ADVICE_GATE_WITHHOLDS_PROVIDER_CREDENTIALS"
        assert "steps.reserve.outcome == 'success'" in step["if"], "ADVICE_RESERVATION_PRECEDES_PROVIDER_ACCESS"
        assert step["run"].startswith("cd trusted\n"), "ADVICE_EXECUTES_ONLY_TRUSTED_CODE"
        assert step.get("timeout-minutes", 0) > 0
    source_checkout = next(step for step in steps if step.get("with", {}).get("path") == "source")
    assert source_checkout["with"]["ref"] == "${{ steps.gate.outputs.tested_sha }}"
    assert source_checkout["with"]["persist-credentials"] is False
    assert not any("cd source" in step.get("run", "") for step in steps)


@pytest.mark.parametrize("timing", ("current", "before", "after"))
def test_assessment_uses_trusted_policy_and_collectors_on_source_data_with_exact_base(tmp_path, monkeypatch, timing):
    from scripts.architecture import diagnostics, python, triage
    from tests.hexagonal.support.ci_advice import assessment_checkouts

    fixture = assessment_checkouts(tmp_path)
    calls = []
    original_check = python.check

    def check(root, profile):
        calls.append(("check", root, profile))
        return original_check(root, profile)

    def diagnose(report, root, options):
        calls.append(("diagnose", root, options))
        return {"status": "complete"}

    def collect_triage(root, artifacts, result, metadata, workflow, catalog, options):
        calls.append(("triage", root, options, artifacts, result, metadata))
        return {"status": "complete"}

    states = iter([{"status": "superseded" if timing == "before" else "current"},
                   {"status": "superseded" if timing == "after" else "current"}])
    monkeypatch.setattr(subject.reuse, "observe_current", lambda *_: next(states))
    monkeypatch.setattr(python, "check", check)
    monkeypatch.setattr(diagnostics, "diagnose", diagnose)
    monkeypatch.setattr(triage, "triage", collect_triage)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.delenv("JEV_ENDPOINT", raising=False)
    output = fixture.work / "output"
    result = subject.assess(fixture.plan, fixture.source, fixture.trusted, output, token="fixture-token")
    if timing == "before":
        assert result["status"] == "superseded_candidate" and not calls, "ADVICE_STALE_BEFORE_PROVIDER_IS_SUPPRESSED"
        assert not output.exists()
        return
    assert calls[0] == ("check", fixture.source, fixture.profile), "ADVICE_POLICY_COMES_FROM_TRUSTED_CHECKOUT"
    assert calls[1][1] == calls[2][1] == fixture.source, "ADVICE_ANALYZES_CANDIDATE_FILES_AS_DATA"
    for call, model in zip(calls[1:], ("trusted-model", "trusted-triage-model"), strict=True):
        assert call[2]["model"] == model, "ADVICE_OPTIONS_COME_FROM_TRUSTED_CHECKOUT"
        assert call[2]["base_ref"] == fixture.plan["candidate_context"]["tested_base_sha"], \
            "ADVICE_CONTEXT_USES_ACTUALLY_TESTED_BASE"
    assert calls[2][3] == fixture.work / "artifacts" and calls[2][4]["status"] == "failed"
    assert calls[2][5] == {"id": "authenticated-metadata"}
    assert result["status"] == ("superseded_after_assessment" if timing == "after" else "complete"), \
        "ADVICE_CANNOT_PUBLISH_HISTORICAL_ASSESSMENT_AS_CURRENT"
    for name in ("diagnostics", "triage"):
        report = json.loads((output / (name + ".json")).read_text())
        provenance = report["advice_provenance"]
        assert provenance["trusted_sha"] == fixture.plan["trusted_sha"]
        assert provenance["tested_sha"] == fixture.plan["candidate_context"]["checkout"]["commit"]
        assert provenance["trusted_sha"] != provenance["tested_sha"]


def test_changed_candidate_checkout_is_refused_before_assessment(tmp_path, monkeypatch):
    from tests.hexagonal.support.ci_advice import assessment_checkouts

    fixture = assessment_checkouts(tmp_path)
    (fixture.source / "pkg/work.py").write_text("CHANGED = True\n")

    def no_observe(*args):
        raise AssertionError("A changed checkout must fail before currentness or model work")

    monkeypatch.setattr(subject.reuse, "observe_current", no_observe)
    with pytest.raises(ValueError, match="checkout identity changed"):
        subject.assess(fixture.plan, fixture.source, fixture.trusted, fixture.work / "output", token="fixture-token")


@pytest.mark.parametrize("selected", ("source", "post_test"))
def test_only_selected_broad_advice_stage_reaches_its_collector(tmp_path, monkeypatch, selected):
    from scripts.architecture import diagnostics, triage
    from tests.hexagonal.support.ci_advice import assessment_checkouts

    fixture = assessment_checkouts(tmp_path)
    fixture.plan["stages"] = {"source": selected == "source", "post_test": selected == "post_test"}
    calls = []
    def diagnose(*args):
        calls.append("source")
        return {"status": "complete"}
    def collect_triage(*args):
        calls.append("post_test")
        return {"status": "complete"}
    monkeypatch.setattr(subject.reuse, "observe_current", lambda *_: {"status": "current"})
    monkeypatch.setattr(diagnostics, "diagnose", diagnose)
    monkeypatch.setattr(triage, "triage", collect_triage)
    result = subject.assess(fixture.plan, fixture.source, fixture.trusted, fixture.work / "output", token="fixture-token")
    assert calls == [selected], "DISABLED_BROAD_STAGE_MAKES_NO_PROVIDER_REQUEST"
    assert result["source_status"] == ("complete" if selected == "source" else "disabled")
    assert result["triage_status"] == ("complete" if selected == "post_test" else "disabled")
