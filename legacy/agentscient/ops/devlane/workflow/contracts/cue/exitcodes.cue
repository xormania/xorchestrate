package contracts

// CLOSED DEFINITION: an observed named constant has exactly the neutral carrier fields.
// PLAN.md SS4 lines 271-274 (inferred; APPROACH.md SS5.7 fixes the carrier).
#ExitCodeConstant: close({
	// PLAN.md SS4 lines 271-274 (inferred; PLAN.md does not name implementation constants).
	name!: string
	// PLAN.md SS4 lines 271-274 (stated).
	value!: 0 | 1 | 2 | 3 | 64
	// PLAN.md SS4 lines 271-274 (inferred; source file is observation metadata).
	file!: string
})

// CLOSED DEFINITION: only the neutral exit-code observation fields are admitted.
// PLAN.md SS4 lines 271-274 (inferred; APPROACH.md SS5.7 fixes the carrier).
#ExitCodes: close({
	// PLAN.md SS4 lines 271-274 (inferred; this is the neutral facts envelope).
	facts!: close({
		// PLAN.md SS4 lines 271-274 (stated: any surfaced code must be in the documented map).
		constants!: [...#ExitCodeConstant]
		// PLAN.md SS4 lines 271-274 (stated: any surfaced literal must be in the documented map).
		exit_literals!: [...(0 | 1 | 2 | 3 | 64)]
		// PLAN.md SS4 lines 271-274 (stated: the map is exact).
		all_codes!: [0, 1, 2, 3, 64]
	})
	// PLAN.md SS1 lines 21-24 (inferred; evidence provenance is deliberately unconstrained).
	provenance!: _
})
