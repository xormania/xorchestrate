package contracts

import "list"

// CLOSED DEFINITION: each registered check has exactly name, class, and argv.
// PLAN.md SS6 lines 328-347 (stated check semantics; illustrative JSON field/container form).
#GateCheck: close({
	// PLAN.md SS6 lines 330-347 (stated name concept; illustrative field spelling).
	name!: string
	// PLAN.md SS3.4 lines 171-177 (stated class concept; illustrative field spelling at SS6 lines 345-347).
	class!: string
	// PLAN.md SS6 lines 330-347 (stated argv-template concept; illustrative list form; values remain open).
	argv!: [...string]
})

// CLOSED DEFINITION WITH OPEN LABEL SET: outcome names vary by gate kind;
// every admitted label is constrained to a string effect.
// PLAN.md SS3.5 lines 181-198 (stated outcome/effect semantics; illustrative map form at SS6 lines 341-342).
#GateOutcomeMap: close({
	// PLAN.md SS3.5 lines 181-198 (stated: labels are the spec's declared outcome values).
	[string]: string
})

// CLOSED DEFINITION: stages admit only the spec fields named by PLAN.md.
// PLAN.md SS6 lines 328-347 (stated stage semantics; illustrative JSON field/container form).
#GateStage: close({
	// PLAN.md SS6 lines 330-347 (stated stage-name concept; illustrative field spelling).
	Name=name!: string
	// PLAN.md SS3.3 lines 165-167 (stated leave preconditions; illustrative leave_requires list form at SS6 lines 337-340).
	leave_requires?: [...string]
	// PLAN.md SS3.5 lines 181-198 (stated outcome semantics; illustrative outcomes map form at SS6 lines 341-342).
	outcomes?: #GateOutcomeMap
	// PLAN.md SS3.5 lines 188-193 (stated owner value; illustrative stage-level placement at SS6 lines 341-342).
	actor?: "owner"
	// PLAN.md SS6 lines 330-332 (inferred helper over the neutral carrier).
	_nameValue: Name
})

// OPEN DEFINITION: PLAN.md fixes gate semantics but leaves room for later data fields.
// PLAN.md SS6 lines 328-348 (stated spec semantics; illustrative JSON field/container form).
// PLAN.md SS6 lines 366-367 (stated: later kinds/check classes/event types extend as data).
#GateKindSpec: {
	// PLAN.md SS6 lines 328-348 (stated kind concept; illustrative field spelling).
	Kind=kind!: string
	// PLAN.md SS6 lines 328-348 (every spec names a version; the general value form is not fixed).
	version!: _
	// PLAN.md SS6 lines 328-348 (stated ordering; illustrative stages list form; cardinality remains open).
	Stages=stages!: [...#GateStage]
	// PLAN.md SS6 lines 330-347 (stated test-scope concept; illustrative field/list form; globs remain open).
	test_scope!: [...string]
	// PLAN.md SS3.3 lines 130-137 (stated harness-scope concept; illustrative field/list form at SS6 lines 343-344).
	harness_scope!: [...string]
	// PLAN.md SS6 lines 330-347 (stated checks concept; illustrative field/list form; values remain open).
	checks!: [...#GateCheck]

	// PLAN.md SS6 lines 330-332 (stated: stages are ordered and named).
	_stageNames: [for stage in Stages {stage._nameValue}]
	// PLAN.md SS3.5 lines 181-198 (stated: collect the declared values without inventing field names).
	_outcomeNames: [
		for _, stage in Stages
		if stage.outcomes != _|_
		for outcome, _ in stage.outcomes {
			outcome
		}
	]
	// PLAN.md SS3.5 lines 185-187 (stated: effects are advance, stay, terminal, or a named re-entry stage).
	_effectChecks: [
		for _, stage in Stages
		if stage.outcomes != _|_
		for _, effect in stage.outcomes {
			effect == "advance" || effect == "stay" || effect == "terminal" || list.Contains(_stageNames, effect)
		}
	] & [...true]

	if Kind == "artifact" {
		// PLAN.md SS3.5 lines 181-181 (stated).
		_artifactStages: _stageNames & ["draft", "review", "fix", "ratify"]
		// PLAN.md SS3.5 lines 181-186 (stated: all six artifact outcome values are declared).
		_artifactOutcomeSet: [
			list.Contains(_outcomeNames, "accepted"),
			list.Contains(_outcomeNames, "rejected"),
			list.Contains(_outcomeNames, "denied"),
			list.Contains(_outcomeNames, "aborted"),
			list.Contains(_outcomeNames, "finding"),
			list.Contains(_outcomeNames, "ratified"),
		] & [...true]
		// PLAN.md SS3.5 lines 181-198 (stated values, but no value-to-stage/effect map is supplied).
		_artifactOutcomeOnly: [for outcome in _outcomeNames {
			outcome == "accepted" || outcome == "rejected" || outcome == "denied" || outcome == "aborted" || outcome == "finding" || outcome == "ratified"
		}] & [...true]
	}

	// PLAN.md SS6 lines 328-332 (inferred helper over the neutral carrier).
	_kindValue: Kind
	...
}

// CLOSED DEFINITION: a gate-kind file observation has exactly file, spec, and two digests.
// PLAN.md SS6 lines 328-360 (inferred; APPROACH.md SS5.3 fixes this carrier).
#GateKindFile: close({
	// PLAN.md SS6 lines 328-330 (stated; exact relative-path representation is not constrained).
	file!: string
	// PLAN.md SS6 lines 328-348 (stated).
	Spec=spec!: #GateKindSpec
	// PLAN.md SS6 lines 354-360 (inferred carrier; the raw reading declared in notes cannot be encoded here).
	sha256_raw?: string
	// PLAN.md SS6 lines 354-360 (inferred carrier: canonical-vs-raw is unspecified, so this alternative is unpinned).
	sha256_canonical?: string
	// PLAN.md SS6 lines 328-332 (inferred helper over the neutral carrier).
	_kindValue: Spec._kindValue
})

// CLOSED DEFINITION: facts has only kinds; the list stays extensible for new kinds/versions.
// PLAN.md SS6 lines 328-367 (stated).
#GateKinds: close({
	// PLAN.md SS6 lines 328-367 (inferred; this is the neutral facts envelope).
	facts!: close({
		// PLAN.md SS6 lines 328-367 (stated: seeded kinds are required and later entries are allowed).
		Kinds=kinds!: [...#GateKindFile] & list.MinItems(2)
		// PLAN.md SS11 lines 513-519 (inferred helper over the seeded-kind carrier).
		_kindNames: [for entry in Kinds {entry._kindValue}]
		// PLAN.md SS11 lines 513-519 (stated).
		_seedKinds: [
			list.Contains(_kindNames, "artifact"),
			list.Contains(_kindNames, "tdd"),
		] & [...true]
	})
	// PLAN.md SS1 lines 21-24 (inferred; evidence provenance is deliberately unconstrained).
	provenance!: _
})
