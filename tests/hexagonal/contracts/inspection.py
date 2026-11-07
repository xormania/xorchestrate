"""Independent boundary behavior for an inspection collaborator."""
from typing import Protocol


class InspectionFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class InspectionFixture(Protocol):
    def inspect(self) -> dict: ...
    def raw_records(self) -> dict: ...
    def change_during_authorization(self, change: str) -> None: ...


OPAQUE = {"sessions": [{"id": "caller-session", "nested": {"value": "opaque"}}],
    "work": {"id": "caller-work"}}


def isolated_projection_contract(fixture: InspectionFixture):
    before = fixture.raw_records()
    result = fixture.inspect()
    assert [row["id"] for row in result["sessions"]] == ["public-session"]
    assert result["work"]["context"] == OPAQUE
    assert result["evidence"][0]["result"] == OPAQUE
    assert result["inspection_redacted"] is True
    assert fixture.raw_records() == before, "INSPECTION_RAW_INPUTS_UNCHANGED"


def detached_result_contract(fixture: InspectionFixture):
    raw_before = fixture.raw_records()
    original = fixture.inspect()
    original["work"]["context"]["sessions"][0]["nested"]["value"] = "changed work"
    original["sessions"][0]["specification"]["nested"]["value"] = "changed session"
    original["evidence"][0]["result"]["sessions"][0]["nested"]["value"] = "changed evidence"
    assert fixture.raw_records() == raw_before, "INSPECTION_RETURNED_VALUES_ARE_DETACHED"
    current = fixture.inspect()
    assert current["work"]["context"] == OPAQUE
    assert current["sessions"][0]["specification"]["nested"]["value"] == "public"
    assert current["evidence"][0]["result"] == OPAQUE


def freshness_contract(fixture: InspectionFixture, change: str):
    fixture.change_during_authorization(change)
    try:
        fixture.inspect()
    except InspectionFailure as error:
        assert error.code == "conflict", "INSPECTION_CHANGED_RECORD_CONFLICT"
    else:
        raise AssertionError("INSPECTION_CHANGED_RECORD_MUST_REJECT")
