"""Telemetry boundaries retain live collaborators without weakening disclosure."""


def live_capture_contract(subject):
    subject.seed_captures()
    assert subject.event_ids() == ["original-source"]
    subject.select_capture("replacement")
    assert subject.event_ids() == ["replacement-source"], "TELEMETRY_READ_USES_CURRENT_CAPTURE"
    subject.select_capture("disabled")
    assert subject.status()["status"] == "disabled"
    subject.select_capture("original")
    assert subject.event_ids() == ["original-source"]


def live_source_health_contract(subject, kind):
    assert subject.status()["source_dispatch"] == {}
    subject.install_health(kind, {"pending": 2, "coverage": "pending", "private": "must stay private"})
    assert subject.status()["source_dispatch"] == {
        kind: {"controlled": {"pending": 2, "coverage": "pending"}}}, "TELEMETRY_STATUS_USES_CURRENT_SOURCES"
    subject.install_health(kind, failure=True)
    assert subject.status()["source_dispatch"] == {
        kind: {"controlled": {"status": "unavailable", "coverage": "unknown"}}}
    subject.remove_health(kind)
    assert subject.status()["source_dispatch"] == {}


def health_authority_change_contract(subject):
    subject.install_authority_change()
    assert subject.status_failure() == "conflict", "TELEMETRY_STATUS_RECHECKS_AUTHORITY_AFTER_HEALTH"
    assert subject.authority_changed(), "The source report must actually replace the policy"
