"""Readiness that costs nothing: the run location and an opt-in session handshake, through the public entrypoint."""


import pytest

from tests.hexagonal.contracts.live_diagnostics import (
    LOCATIONS,
    PROVIDER_REASON,
    handshake_only,
    location_verdict,
    probe_report,
    surfaced_reason,
)
from tests.hexagonal.support.live_diagnostics import (
    SESSION_START,
    TURN,
    home_accepted,
    native_configuration,
    observed_methods,
    peer_session,
    receipts,
    run_testkit,
    shared_directory,
)
from tests.hexagonal.support.terminal_host import requires_host

requires_tmux = requires_host()


# Both kinds of check that provision a private application home inside their run directory.
SELECTIONS = {"story": ("--suite", "stories", "--story", "coordination"), "acceptance": ("--suite", "acceptance")}


def location_cases():
    # An accepted location still needs tmux for a passed verdict; a refused one is refused either way.
    for case, (_, accepted) in LOCATIONS.items():
        for selection in SELECTIONS:
            yield pytest.param(case, selection, id=f"{selection}-{case}", marks=[requires_tmux] if accepted else [])


@pytest.mark.parametrize("case,selection", list(location_cases()))
def test_doctor_reports_whether_the_product_accepts_a_home_beneath_the_evidence_root(tmp_path, case, selection):
    mode, accepted = LOCATIONS[case]
    location = shared_directory(tmp_path, mode)
    # The table is the expectation; the product must agree with it before doctor is compared.
    assert home_accepted(location / "runs") is accepted
    result, report = run_testkit("doctor", *SELECTIONS[selection], "--output", location / "runs",
                                 cache=tmp_path / "cache")
    assert result.returncode == (0 if accepted else 1), result.stdout + result.stderr
    location_verdict(report, accepted=accepted, directory=location)
    assert not (location / "runs").exists(), "doctor allocates nothing"


def test_run_blocks_a_story_before_launch_when_its_location_would_be_refused(tmp_path):
    location = shared_directory(tmp_path, LOCATIONS["group-writable"][0])
    result, report = run_testkit("run", "--suite", "stories", "--story", "coordination",
                             "--output", location / "runs", cache=tmp_path / "cache")
    assert result.returncode == 1, result.stdout + result.stderr
    assert report["status"] == "failed"
    [row] = report["checks"]
    assert row["name"] == "story:coordination"
    assert row["status"] == "setup_error" and row["launched"] is False
    assert "Run location" in row["reason"] and str(location) in row["reason"]
    assert receipts(location / "runs") == [], "A blocked story never starts its scenario"


@requires_tmux
def test_doctor_probe_takes_each_selected_harness_through_its_handshake_and_reports_each_reason(tmp_path):
    traces = {name: tmp_path / (name + "-methods.jsonl") for name in ("healthy", "refused")}
    config = native_configuration(tmp_path / "operator" / "native.json", {
        "healthy": peer_session("--trace", traces["healthy"]),
        "refused": peer_session("--trace", traces["refused"], "--refuse-start")})
    result, report = run_testkit("doctor", "--probe", "--mode", "native", "--suite", "stories", "--config", config,
                             "--driver", "healthy", "--driver", "refused", "--output", tmp_path / "runs",
                             "--timeout", "30", "--shutdown-grace", "2", cache=tmp_path / "cache")
    assert result.returncode == 1, result.stdout + result.stderr
    assert report["status"] == "setup_error"
    probe = report["probe"]
    probe_report(probe, passed={"story:native-handshake:healthy"}, failed={"story:native-handshake:refused"})
    surfaced_reason(probe["failures"], check="story:native-handshake:refused",
                    words=("configured_native_session_handshake", "start_session", PROVIDER_REASON))
    assert not any(line.startswith("story:native-handshake:healthy:") for line in probe["failures"])
    # Independent of the test-kit's own accounting: what each harness process actually received.
    handshake_only(observed_methods(traces["healthy"]), start=SESSION_START, turn=TURN)
    handshake_only(observed_methods(traces["refused"]), start=SESSION_START, turn=TURN)
    retained = receipts(probe["directory"])
    assert len(retained) == 2
    assert all(row["story"]["id"] == "native-handshake" for row in retained)
    assert all(row["budget"]["limits"]["turns"] == 0 and row["budget"]["consumed"]["turns"] == 0 for row in retained)
    assert sorted(row["status"] for row in retained) == ["failed", "passed"]


@requires_tmux
def test_doctor_probe_passes_when_every_selected_handshake_completes(tmp_path):
    config = native_configuration(tmp_path / "operator" / "native.json", {"healthy": peer_session()})
    result, report = run_testkit("doctor", "--probe", "--mode", "native", "--suite", "stories", "--config", config,
                             "--driver", "healthy", "--output", tmp_path / "runs",
                             "--timeout", "30", "--shutdown-grace", "2", cache=tmp_path / "cache")
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["status"] == "passed"
    probe_report(report["probe"], passed={"story:native-handshake:healthy"}, failed=set())
    assert report["probe"]["failures"] == []


@requires_tmux
def test_plain_doctor_never_launches_a_harness_or_allocates_a_run(tmp_path):
    trace = tmp_path / "methods.jsonl"
    config = native_configuration(tmp_path / "operator" / "native.json", {"healthy": peer_session("--trace", trace)})
    result, report = run_testkit("doctor", "--mode", "native", "--suite", "stories", "--config", config,
                             "--driver", "healthy", "--output", tmp_path / "runs", cache=tmp_path / "cache")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe" not in report
    assert observed_methods(trace) == []
    assert not (tmp_path / "runs").exists()


def test_probe_is_not_attempted_over_failed_readiness(tmp_path):
    trace = tmp_path / "methods.jsonl"
    location = shared_directory(tmp_path, LOCATIONS["group-writable"][0])
    config = native_configuration(tmp_path / "operator" / "native.json", {"healthy": peer_session("--trace", trace)})
    result, report = run_testkit("doctor", "--probe", "--mode", "native", "--suite", "stories", "--config", config,
                             "--driver", "healthy", "--output", location / "runs", cache=tmp_path / "cache")
    assert result.returncode == 1
    assert report["status"] == "setup_error"
    assert report["probe"]["status"] == "not_run"
    assert observed_methods(trace) == []
    assert not (location / "runs").exists()


@pytest.mark.parametrize("arguments", [
    ("doctor", "--probe", "--suite", "stories", "--story", "coordination"),
    ("plan", "--probe", "--suite", "stories", "--story", "coordination"),
    ("run", "--probe", "--suite", "stories", "--story", "coordination"),
], ids=["protocol-mode", "plan", "run"])
def test_probe_is_refused_outside_a_native_doctor_without_launching(tmp_path, arguments):
    result, report = run_testkit(*arguments, "--output", tmp_path / "runs", cache=tmp_path / "cache")
    assert result.returncode == 1
    assert report["status"] == "setup_error"
    assert "--probe" in report["diagnostic"]
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("native", [False, True], ids=["protocol-mode", "native-without-driver"])
def test_probe_selection_is_refused_before_readiness_is_assessed(tmp_path, native):
    # The evidence root would fail readiness, so a selection refusal can only come first.
    location = shared_directory(tmp_path, LOCATIONS["group-writable"][0])
    trace = tmp_path / "methods.jsonl"
    config = native_configuration(tmp_path / "operator" / "native.json", {"healthy": peer_session("--trace", trace)})
    selection = ("--mode", "native", "--config", config) if native else ("--story", "coordination")
    result, report = run_testkit("doctor", "--probe", "--suite", "stories", *selection,
                                 "--output", location / "runs", cache=tmp_path / "cache")
    assert result.returncode == 1, result.stdout + result.stderr
    assert report.get("probe", {}).get("status") != "not_run", "The probe selection must be refused, not deferred"
    assert report["status"] == "setup_error"
    assert report["diagnostic"] == "--probe launches configured native harnesses; select --mode native and a driver"
    assert "checks" not in report and "probe" not in report, "Readiness was assessed before the selection was refused"
    assert observed_methods(trace) == []
    assert not (location / "runs").exists()


@requires_tmux
def test_run_agrees_with_doctor_when_the_evidence_root_and_its_parents_do_not_exist_yet(tmp_path):
    # A permissive umask would otherwise leave the created parents group-writable, so a root that
    # doctor accepted would be refused once the run had created it.
    output = tmp_path / "missing" / "parent" / "runs"
    result, report = run_testkit("doctor", "--suite", "stories", "--story", "coordination", "--output", output,
                                 cache=tmp_path / "cache", umask=0o002)
    assert result.returncode == 0, result.stdout + result.stderr
    location_verdict(report, accepted=True, directory=tmp_path)
    result, report = run_testkit("run", "--suite", "stories", "--story", "coordination", "--output", output,
                                 cache=tmp_path / "cache", umask=0o002)
    [row] = report["checks"]
    assert row["launched"] is True, row.get("reason")
    assert result.returncode == 0 and row["status"] == "passed", result.stdout + result.stderr
    for created in (tmp_path / "missing", tmp_path / "missing" / "parent", output):
        assert created.stat().st_mode & 0o777 == 0o700, created
