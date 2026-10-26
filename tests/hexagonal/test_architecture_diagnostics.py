"""Exercise architecture diagnostics through the real TypeSafe HTTP adapter."""
import hashlib
import json
import threading
import time
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.architecture.diagnostics import CHECKS, diagnose


@pytest.fixture
def report(tmp_path):
    sources = {"app/adapters/store.py": ("adapters", "def store(value):\n    return value\n"),
               "app/core.py": ("core", "def work(port):\n    return port.store('work')\n"),
               "app/ports.py": ("core", "class Port:\n    def store(self, value): ...\n")}
    files = []
    for path, (role, source) in sources.items():
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(source)
        files.append({"path": path, "module": path[:-3].replace("/", "."), "role": role,
                      "sha256": hashlib.sha256(source.encode()).hexdigest()})
    return {"status": "pass", "summary": {"violations": 0}, "findings": [],
            "subject": {"source_sha256": "subject-binding", "contract_sha256": "contract-binding"},
            "profile": {"id": "example", "roles": {
                "core": {"allow_dynamic": False, "allowed_roles": ["core"]},
                "adapters": {"allow_dynamic": True, "allowed_roles": ["core", "adapters"]}}},
            "files": files, "coverage": {"modules": 3, "classified": 3, "limitations": ["Source imports only."]}}


@pytest.fixture
def provider(monkeypatch):
    state = {"requests": [], "status": 200, "delay": 0, "choice": "concern", "response": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["requests"].append({"headers": dict(self.headers), "payload": payload})
            if state["delay"]:
                time.sleep(state["delay"])
            criteria = payload["questions"]["assessment"]["criteria"]
            response = state["response"]
            if response is None:
                response = {"model": payload["model"], "answers": {"assessment": {
                    "type": "choice", "choice": state["choice"], "confidence": 1,
                    "probabilities": {key: int(key == state["choice"]) for key in criteria}}},
                    "usage": {"input_tokens": 20, "output_tokens": 3}}
            self.send_response(state["status"])
            self.end_headers()
            try:
                self.wfile.write(response if isinstance(response, bytes) else json.dumps(response).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    monkeypatch.setenv("ARCHITECTURE_TEST_JEV_KEY", "private-fixture-credential")
    state["options"] = {"endpoint": f"http://127.0.0.1:{server.server_port}/assess", "allow_http": True,
                        "model": "jev-fixture-model", "credential_env": "ARCHITECTURE_TEST_JEV_KEY"}
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_http_diagnostics_run_on_pass_and_preserve_exact_evidence(tmp_path, report, provider):
    original = deepcopy(report)
    result = diagnose(report, tmp_path, provider["options"])
    assert result["status"] == "complete"
    assert result["concerns"] == 3
    assert [item["check_id"] for item in result["assessments"]] == list(CHECKS)
    assert report == original
    assert result["deterministic_status"] == "pass"
    assert len(provider["requests"]) == 3
    wire = provider["requests"][0]
    assert wire["headers"]["Authorization"] == "Bearer private-fixture-credential"
    context = wire["payload"]["state"]
    assert context["profile"] == report["profile"]
    assert context["subject"] == report["subject"]
    assert context["deterministic"]["coverage"] == report["coverage"]
    assert context["sources"][0]["path"] == "app/core.py"
    assert context["sources"][0]["source"] == (tmp_path / "app/core.py").read_text()
    assert "private-fixture-credential" not in json.dumps(result)
    assert "private-fixture-credential" not in json.dumps(wire["payload"])


def test_no_concern_never_clears_deterministic_violation(tmp_path, report, provider):
    report["status"] = "violation"
    report["findings"] = [{"rule_id": "core_import", "path": "app/core.py", "message": "Forbidden import"}]
    provider["choice"] = "no_concern"
    result = diagnose(report, tmp_path, {**provider["options"], "checks": ["semantic_boundaries"]})
    assert result["status"] == "complete" and result["concerns"] == 0
    assert result["deterministic_status"] == report["status"] == "violation"
    assert result["assessments"][0]["outcome"] == "no_concern"
    assert len(provider["requests"]) == 1


def test_source_selection_priorities_and_truncation_are_explicit(tmp_path, report, provider):
    report["findings"] = [{"path": "app/adapters/store.py", "message": "Inspect this source"}]
    result = diagnose(report, tmp_path, {**provider["options"], "max_source_bytes": 20,
                                       "max_files": 2, "source_paths": ["app/ports.py"]})
    assert result["status"] == "complete"
    scope = result["source_scope"]
    assert scope["selected"] == ["app/ports.py", "app/adapters/store.py"]
    assert scope["omitted"] == ["app/core.py"]
    assert scope["truncated"] == scope["selected"]
    assert scope["source_bytes"] <= 20
    assert all(item["evidence_scope"] == scope["selected"] for item in result["assessments"])


def test_missing_configuration_is_visible_without_provider_execution(tmp_path, report):
    result = diagnose(report, tmp_path)
    assert result["status"] == "not_configured"
    assert result["assessments"] == []


@pytest.mark.parametrize("change", ["changed", "missing", "escaped"])
def test_source_binding_failures_do_not_call_provider(tmp_path, report, provider, change):
    if change == "changed":
        (tmp_path / "app/core.py").write_text("print('changed')")
    elif change == "missing":
        (tmp_path / "app/core.py").unlink()
    else:
        report["files"][1]["path"] = "../outside.py"
    result = diagnose(report, tmp_path, provider["options"])
    assert result["status"] == "inconclusive"
    assert result["reason"] == "source_evidence_unavailable_or_changed"
    assert provider["requests"] == []


@pytest.mark.parametrize("options", [
    {"max_source_bytes": 0}, {"max_files": True}, {"checks": []},
    {"checks": ["invented"]}, {"checks": ["semantic_boundaries", "semantic_boundaries"]},
    {"criteria": {"pass": "Whatever"}}, {"unrecognized": "value"},
])
def test_invalid_configuration_is_distinct_from_architecture_failure(tmp_path, report, provider, options):
    result = diagnose(report, tmp_path, {**provider["options"], **options})
    assert result["status"] == "error"
    assert result["reason"] == "invalid_configuration"
    assert result["deterministic_status"] == "pass"
    assert provider["requests"] == []


@pytest.mark.parametrize("field,value", [
    ("endpoint", None), ("model", None), ("model", ""),
    ("endpoint", "https://[broken"), ("endpoint", "not-a-url"),
])
def test_invalid_typesafe_configuration_is_caught(tmp_path, report, provider, field, value):
    options = dict(provider["options"])
    if value is None:
        options.pop(field)
    else:
        options[field] = value
    result = diagnose(report, tmp_path, options)
    assert result["status"] == "error"
    assert result["reason"] == "invalid_configuration"
    assert provider["requests"] == []


def test_missing_credential_is_unavailable_not_success(tmp_path, report, provider, monkeypatch):
    monkeypatch.delenv("ARCHITECTURE_TEST_JEV_KEY")
    result = diagnose(report, tmp_path, provider["options"])
    assert result["status"] == "unavailable"
    assert result["assessments"] == []
    assert provider["requests"] == []


@pytest.mark.parametrize("failure", ["http", "timeout", "malformed", "wrong_model", "credential_echo"])
def test_provider_failure_is_retained_without_secret_or_false_diagnostic(tmp_path, report, provider, failure):
    options = provider["options"]
    if failure == "http":
        provider["status"] = 503
    elif failure == "timeout":
        provider["delay"] = 0.05
        options = {**options, "timeout": 0.005}
    elif failure == "malformed":
        provider["response"] = b"not json"
    elif failure == "wrong_model":
        provider["response"] = {"model": "wrong-model"}
    else:
        provider["response"] = {"error": "private-fixture-credential"}
    result = diagnose(report, tmp_path, options)
    assert result["status"] == ("unavailable" if failure in {"http", "timeout"} else "error")
    assert result["assessments"] == []
    assert result["deterministic_status"] == "pass"
    assert "private-fixture-credential" not in json.dumps(result)


def test_diagnostic_request_budget_failure_is_explicit(tmp_path, report, provider):
    result = diagnose(report, tmp_path, {**provider["options"], "max_request_bytes": 10})
    assert result["status"] == "error"
    assert result["reason"] == "invalid_request"
    assert provider["requests"] == []


@pytest.mark.parametrize("violation", [False, True])
def test_local_command_runs_real_http_diagnostics_and_preserves_gate(
        tmp_path, report, provider, monkeypatch, violation):
    from scripts.architecture.__main__ import main

    monkeypatch.delenv("JEV_ENDPOINT", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    profile = {
        "schema_version": 1, "id": "fixture.hexagonal", "version": 1,
        "source_roots": ["app"], "roles": {
            "core": {"modules": ["app.core", "app.ports"], "allowed_roles": ["core"],
                     "allowed_external": [], "allow_dynamic": False},
            "adapters": {"modules": ["app.adapters.*"], "allowed_roles": ["core", "adapters"],
                         "allowed_external": ["stdlib:*"], "allow_dynamic": True},
        },
    }
    (tmp_path / "profile.json").write_text(json.dumps(profile))
    (tmp_path / "jev.json").write_text(json.dumps(provider["options"]))
    output = tmp_path / "result.json"
    provider["choice"] = "no_concern"
    if violation:
        (tmp_path / "app/core.py").write_text("import sqlite3\n")
    exit_code = main(["--root", str(tmp_path), "--profile", "profile.json", "--jev-config", "jev.json",
                      "--output", str(output)])
    result = json.loads(output.read_text())
    assert exit_code == int(violation)
    assert result["status"] == ("violation" if violation else "pass")
    assert result["diagnostics"]["status"] == "complete"
    assert result["diagnostics"]["concerns"] == 0
    assert len(provider["requests"]) == 3
