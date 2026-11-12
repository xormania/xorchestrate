"""Provider-independent expectations for source-bound operational trials."""

from __future__ import annotations


def labels_are_withheld(packet):
    forbidden = {"oracle", "fixture_outcome", "expected_fault", "split", "group", "provenance", "dispositions", "case_id"}
    if isinstance(packet, dict):
        assert not forbidden.intersection(packet), "OPERATIONAL_LABELS_WITHHELD"
        for value in packet.values():
            labels_are_withheld(value)
    elif isinstance(packet, list):
        for value in packet:
            labels_are_withheld(value)


def comparison_stays_advisory(report):
    assert report.get("mode") in {"fixture", "live"}, "OPERATIONAL_MODE_VISIBLE"
    assert report.get("measurement_basis") != "production_acceptance", "NO_INVENTED_OPERATIONAL_ACCEPTANCE"
