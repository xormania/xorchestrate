"""Temporal proof expectations independent of capture and verdict adapters."""

WALL_TIME_VARIANTS = ("forward", "rollback", "equal")
EXCHANGE_VARIANTS = ("exchange", "busy", "work_exchange")


def assert_qualified(report):
    assert report["status"] == "passed", ("TEMPORAL_WITNESS_SURVIVES_DIAGNOSTIC_TIME", report)


def assert_named_rejection(report, diagnostic):
    assert report["status"] == "incomplete", ("TEMPORAL_REQUIRED_WITNESS_CANNOT_BE_INVENTED", report)
    assert any(case.get("assertion") == diagnostic for case in report["cases"]), (
        "TEMPORAL_FAULT_HAS_NAMED_REJECTION", diagnostic, report)
