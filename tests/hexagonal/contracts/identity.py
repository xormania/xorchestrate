"""Independent identity/effect requirements against an injected evidence port.

These scenarios know application claims, not vendor packets, storage tables or
the verifier implementation. Expected outcomes are declared here from the
identity requirement; they are never calculated by the subject under test.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True)
class ClaimChange:
    record: str
    claim: str
    value: Any = None
    absent: bool = False


@dataclass(frozen=True)
class IdentityScenario:
    name: str
    scope: str
    changes: tuple[ClaimChange, ...] = ()
    accepted: bool = True
    diagnostic: str | None = None
    duplicate: bool = False
    alternate_work: bool = False
    omit_work: bool = False
    duplicate_callback: bool = False
    historical_scope: str | None = None
    historical_stale: bool = False


@dataclass(frozen=True)
class IdentityOutcome:
    accepted: bool
    diagnostics: frozenset[str]
    detail: Mapping[str, Any]


class IdentityEvidencePort(Protocol):
    def change_claim(self, change: ClaimChange) -> None: ...
    def duplicate_native(self) -> None: ...
    def duplicate_completed_callback(self) -> None: ...
    def add_historical_call(self, scope: str, stale: bool) -> None: ...
    def select_alternate_work(self) -> None: ...
    def omit_optional_work(self) -> None: ...
    def evaluate(self) -> IdentityOutcome: ...
    def native_facts(self) -> list[Mapping[str, Any]]: ...
    def source_native_records(self) -> list[Mapping[str, Any]]: ...


IdentityFactory = Callable[[str, str], IdentityEvidencePort]


EXCHANGE_SCENARIOS = (
    IdentityScenario("authorized-native", "exchange"),
    IdentityScenario("native-actor-absent", "exchange", (ClaimChange("native", "actor", absent=True),)),
    IdentityScenario("identical-native-duplicate", "exchange", duplicate=True),
    IdentityScenario("optional-actor-absent-on-duplicate", "exchange",
                     (ClaimChange("duplicate", "actor", absent=True),), duplicate=True),
    IdentityScenario("coherent-explicit-alternate-work", "exchange", alternate_work=True),
    *tuple(IdentityScenario("coherent-" + scope + "-history-" + str(stale), "exchange",
                            historical_scope=scope, historical_stale=stale)
           for scope in ("retired", "unrelated") for stale in (False, True)),
    IdentityScenario("optional-embedded-work-absent", "exchange", (ClaimChange("callback", "embedded_work", absent=True),)),
    *tuple(IdentityScenario("embedded-work-" + name, "exchange", (ClaimChange("callback", "embedded_work", value),),
                            False, "application_effect_arguments_mismatch")
           for name, value in (("wrong", "other-work"), ("null", None))),
    *tuple(IdentityScenario(side + "-" + claim + "-" + name, "exchange", (ClaimChange(side, claim, value),),
                            False, "application_effect_arguments_mismatch")
           for side, claim in (("callback", "embedded_actor"), ("callback", "embedded_sender"),
                               ("effect", "actor"), ("receipt-callback", "embedded_actor"),
                               ("receipt-callback", "embedded_source"), ("receipt-callback", "embedded_work"))
           for name, value in (("wrong", "another-identity"), ("null", None))),
    *tuple(IdentityScenario(side + "-" + claim + "-absent", "exchange", (ClaimChange(side, claim, absent=True),))
           for side, claim in (("callback", "embedded_actor"), ("callback", "embedded_sender"),
                               ("effect", "actor"), ("receipt-callback", "embedded_actor"),
                               ("receipt-callback", "embedded_source"), ("receipt-callback", "embedded_work"))),
    *tuple(IdentityScenario("saved-receipt-actor-" + name, "exchange", (ClaimChange("receipt-effect", "actor", value),),
                            False, "persisted_evidence_actor_mismatch")
           for name, value in (("wrong", "another-actor"), ("null", None))),
    IdentityScenario("saved-receipt-actor-absent", "exchange", (ClaimChange("receipt-effect", "actor", absent=True),)),
    IdentityScenario("exact-completed-callback-duplicate", "exchange", duplicate_callback=True),
    *tuple(IdentityScenario("duplicate-callback-work-" + name, "exchange",
                            (ClaimChange("duplicate-callback", "embedded_work", value),), False,
                            "application_effect_arguments_mismatch", duplicate_callback=True)
           for name, value in (("wrong", "other-work"), ("null", None))),
    *tuple(IdentityScenario("native-actor-" + name, "exchange", (ClaimChange("native", "actor", value),),
                            False, "native_call_missing_or_misbound")
           for name, value in (("wrong", "another-actor"), ("null", None), ("false", False))),
    *tuple(IdentityScenario("duplicate-actor-" + name, "exchange", (ClaimChange("duplicate", "actor", value),),
                            False, "native_call_missing_or_misbound", duplicate=True)
           for name, value in (("wrong", "another-actor"), ("null", None))),
    *tuple(IdentityScenario("duplicate-native-" + claim + "-" + name, "exchange", (ClaimChange("duplicate", claim, value),),
                            False, "native_call_missing_or_misbound", duplicate=True)
           for claim in ("native_session", "incarnation", "nested_session", "application_session")
           for name, value in (("wrong", "contradictory-identity"), ("null", None))),
    *tuple(IdentityScenario("duplicate-callback-" + claim + "-" + name, "exchange",
                            (ClaimChange("duplicate-callback", claim, value),), False,
                            "callback_identity_mismatch", duplicate_callback=True)
           for claim in ("native_session", "incarnation", "nested_session", "application_session")
           for name, value in (("wrong", "contradictory-identity"), ("null", None))),
    IdentityScenario("duplicate-work-contradiction", "exchange",
                     (ClaimChange("duplicate", "record_work", "other-work"),),
                     False, "native_call_missing_or_misbound", duplicate=True),
    *tuple(IdentityScenario(side + "-actor-" + name, "exchange", (ClaimChange(side, "actor", value),),
                            False, "callback_actor_binding")
           for side in ("requested", "callback") for name, value in (("wrong", "another-actor"), ("null", None))),
    *tuple(IdentityScenario("native-" + claim + "-" + name, "exchange", (ClaimChange("native", claim, value),),
                            False, "native_call_missing_or_misbound")
           for claim in ("native_session", "incarnation", "nested_session")
           for name, value in (("wrong", "contradictory-identity"), ("null", None))),
    IdentityScenario("native-nested-session-absent", "exchange", (ClaimChange("native", "nested_session", absent=True),)),
    IdentityScenario("durable-session-actor-contradiction", "exchange",
                     (ClaimChange("session", "actor", "another-actor"),), False, "callback_session_binding"),
)


LIFECYCLE_SCENARIOS = (
    IdentityScenario("retained-valid", "lifecycle"),
    *tuple(IdentityScenario("operation-session-" + claim + "-" + name, "lifecycle", (ClaimChange("operation", claim, value),),
                            False, "lifecycle_result_binding")
           for claim in ("result_actor", "result_work", "result_native_session", "result_incarnation")
           for name, value in (("wrong", "contradictory-identity"), ("null", None))),
    *tuple(IdentityScenario("operation-session-" + claim + "-absent", "lifecycle",
                            (ClaimChange("operation", claim, absent=True),))
           for claim in ("result_actor", "result_work", "result_native_session", "result_incarnation")),
    IdentityScenario("identical-native-duplicate", "lifecycle", duplicate=True),
    IdentityScenario("optional-actor-absent-on-duplicate", "lifecycle",
                     (ClaimChange("duplicate", "actor", absent=True),), duplicate=True),
    *tuple(IdentityScenario("duplicate-actor-" + name, "lifecycle", (ClaimChange("duplicate", "actor", value),),
                            False, "lifecycle_retained_observation_binding", duplicate=True)
           for name, value in (("wrong", "another-actor"), ("null", None))),
    *tuple(IdentityScenario("duplicate-native-" + claim + "-" + name, "lifecycle", (ClaimChange("duplicate", claim, value),),
                            False, "lifecycle_retained_observation_binding", duplicate=True)
           for claim in ("native_session", "incarnation", "nested_session", "application_session")
           for name, value in (("wrong", "contradictory-identity"), ("null", None))),
    IdentityScenario("identical-callback-duplicate", "lifecycle", duplicate_callback=True),
    *tuple(IdentityScenario("duplicate-callback-" + claim + "-" + name, "lifecycle", (ClaimChange("duplicate-callback", claim, value),),
                            False, "lifecycle_retained_observation_binding", duplicate_callback=True)
           for claim in ("actor", "record_work", "embedded_work", "native_session", "incarnation", "nested_session", "application_session")
           for name, value in (("wrong", "contradictory-identity"), ("null", None))),
    IdentityScenario("coherent-explicit-alternate-work", "lifecycle", alternate_work=True),
    IdentityScenario("all-optional-work-absent", "lifecycle", omit_work=True),
    IdentityScenario("native-work-argument-enriched", "lifecycle",
                     (ClaimChange("native", "argument_work", absent=True),)),
    IdentityScenario("optional-native-actor-absent", "lifecycle", (ClaimChange("native", "actor", absent=True),)),
    IdentityScenario("optional-nested-session-absent", "lifecycle", (ClaimChange("native", "nested_session", absent=True),)),
    *tuple(IdentityScenario(side + "-actor-" + name, "lifecycle", (ClaimChange(side, "actor", value),),
                            False, "lifecycle_actor_binding")
           for side in ("native", "callback", "session", "effect")
           for name, value in (("wrong", "another-actor"), ("null", None))),
    *tuple(IdentityScenario(side + "-" + claim + "-" + name, "lifecycle", (ClaimChange(side, claim, value),),
                            False, "lifecycle_work_binding")
           for side, claim in (("native", "argument_work"), ("callback", "argument_work"),
                               ("effect", "record_work"), ("callback", "embedded_work"),
                               ("native", "record_work"), ("callback", "record_work"))
           for name, value in (("wrong", "other-work"), ("null", None))),
    *tuple(IdentityScenario(side + "-" + claim + "-" + name, "lifecycle", (ClaimChange(side, claim, value),),
                            False, "lifecycle_retained_observation_binding")
           for side in ("native", "callback") for claim in ("native_session", "incarnation", "nested_session")
           for name, value in (("wrong", "contradictory-identity"), ("null", None))),
)


def assert_identity_scenario(factory: IdentityFactory, scenario: IdentityScenario, operation: str = "reconnect") -> None:
    subject = factory(scenario.scope, operation)
    control = subject.evaluate()
    assert control.accepted, ("IV03_VALID_CONTROL", control.detail)
    if scenario.alternate_work:
        subject.select_alternate_work()
    if scenario.omit_work:
        subject.omit_optional_work()
    if scenario.duplicate:
        subject.duplicate_native()
    if scenario.duplicate_callback:
        subject.duplicate_completed_callback()
    if scenario.historical_scope:
        subject.add_historical_call(scenario.historical_scope, scenario.historical_stale)
    for change in scenario.changes:
        subject.change_claim(change)
    observed = subject.evaluate()
    assert observed.accepted is scenario.accepted, ("IV03_INDEPENDENT_CLAIMS", scenario.name, observed.detail)
    if not scenario.accepted:
        # The interchangeable receipt policy and independent verification port
        # expose different public diagnostic vocabularies. Each accepted reason
        # is a literal identity predicate; malformed/unrelated failures never
        # satisfy this contract.
        receipt_reasons = {
            "native_call_missing_or_misbound": {"independent_native_arguments_mismatch", "independent_native_observation_missing",
                                                "native_session_drift"},
            "callback_actor_binding": {"native_call_id_conflict", "native_callback_missing_or_misbound"},
            "callback_identity_mismatch": {"native_call_id_conflict"},
            "callback_session_binding": {"native_callback_missing_or_misbound"},
            "application_effect_arguments_mismatch": {"native_callback_missing_or_misbound", "native_call_id_conflict"},
            "persisted_evidence_actor_mismatch": {"sender_receipt_missing_or_duplicate", "native_callback_missing_or_misbound",
                                                  "application_effect_arguments_mismatch"},
        }
        allowed = {scenario.diagnostic} | receipt_reasons.get(scenario.diagnostic, set())
        assert allowed & observed.diagnostics, ("IV03_NAMED_REJECTION", scenario.name, observed.detail)


def assert_native_claim_provenance(factory: IdentityFactory, value: Any, *, absent: bool = False) -> None:
    subject = factory("exchange", "reconnect")
    subject.duplicate_native()
    subject.change_claim(ClaimChange("duplicate", "actor", value, absent=absent))
    originals = subject.source_native_records()
    facts = subject.native_facts()
    for record in originals:
        matching = [fact for fact in facts if fact.get("raw_ref") == {"kind": "native_observation", "id": record["id"]}]
        assert len(matching) == 1, ("IV03_RAW_REFERENCE_PRESERVED", record["id"], facts)
        fact = matching[0]
        assert fact.get("raw_observation") == record, "IV03_RAW_PROVENANCE_PRESERVED"
        assert ("native_actor" in fact) == ("created_by" in record), "IV03_ABSENCE_IS_NOT_NULL"
        if "created_by" in record:
            assert fact["native_actor"] == record["created_by"], "IV03_CONFLICTING_ACTOR_PRESERVED"
    # Projection is a snapshot; later fixture mutations must not rewrite its raw
    # evidence. The expected snapshot comes from input records, not verdicts.
    subject.change_claim(ClaimChange("duplicate", "actor", "later-mutation"))
    expected = {row["id"]: row for row in originals}
    assert {fact["raw_ref"]["id"]: fact["raw_observation"] for fact in facts
            if fact.get("raw_ref", {}).get("id") in expected} == expected
